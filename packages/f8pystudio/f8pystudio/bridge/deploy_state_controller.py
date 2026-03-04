from __future__ import annotations

import asyncio
import logging
from typing import Any

from qtpy import QtCore

from f8pysdk import F8RuntimeGraph
from f8pysdk.nats_naming import ensure_token
from f8pysdk.service_bus.state_write import StateWriteError

from .json_codec import coerce_json_value
from .managed_service_inventory import collect_managed_service_inventory
from .rungraph_deploy_flow import pick_compiled
from .runtime_graph_projection import build_local_state_field_index, build_remote_watch_targets, build_studio_runtime_graph
from .service_endpoint_client import request_set_remote_state
from .studio_runtime_flow import apply_remote_state_watches_if_changed, install_studio_runtime_graph
from ..nodegraph.runtime_compiler import CompiledRuntimeGraphs
from ..pystudio_node_registry import SERVICE_CLASS

logger = logging.getLogger(__name__)


class DeployStateControllerMixin:
    def deploy(self, compiled: CompiledRuntimeGraphs) -> None:
        """
        Starts service processes (if not running), deploys per-service graphs,
        installs the studio runtime graph, and enables remote state monitoring.
        """
        # 1) start processes (sync)
        self._last_compiled = compiled
        self._local_state_fields_by_node = self._build_local_state_field_index(compiled)
        inventory = collect_managed_service_inventory(
            services=list(compiled.global_graph.services or []),
            studio_service_id=self.studio_service_id,
            studio_service_class=SERVICE_CLASS,
            on_collect_error=lambda exc: self._emit_log_line(f"start service failed: {exc}"),
        )
        for service_id, service_class in list(inventory.start_order):
            try:
                # Use the public helper so we dedup against already-running services
                # (including ones started outside this Studio process).
                self.start_service(service_id, service_class=service_class)
            except Exception as exc:
                self._emit_log_line(f"start service failed: {exc}")
        self._managed_service_ids = set(inventory.service_ids)
        self._managed_service_classes = dict(inventory.service_classes)

        # 2) deploy + install monitoring (async)
        self._submit_async(self._deploy_and_monitor_async(compiled), context="submit deploy_and_monitor failed")
        # Preserve the current global lifecycle preference across repeated deploys.
        # Only enforce deactivate here when globally paused; avoid forcing activate
        # on every F5, which can override rungraph/state-edge driven inactive states.
        if not bool(self._managed_active):
            self.set_managed_active(False)

    def deploy_service_rungraph(self, service_id: str, *, compiled: CompiledRuntimeGraphs | None = None) -> None:
        """
        Deploy the current per-service rungraph to a running service instance (best-effort).
        """
        try:
            sid = ensure_token(str(service_id), label="service_id")
        except ValueError:
            return
        if sid == self.studio_service_id:
            return
        if not self.is_service_running(sid):
            self._emit_log_line(f"deploy skipped (service not running) serviceId={sid}")
            return

        async def _do() -> None:
            await self._install_studio_graph_async(compiled=compiled)
            await self._deploy_service_rungraph_async(sid, compiled=compiled)

        self._submit_async(_do(), context=f"submit deploy_service_rungraph failed serviceId={sid}")

    def _build_studio_runtime_graph(self, compiled: CompiledRuntimeGraphs) -> F8RuntimeGraph:
        return build_studio_runtime_graph(compiled, studio_service_id=self.studio_service_id)

    def _build_remote_watch_targets(self, compiled: CompiledRuntimeGraphs) -> tuple[WatchTarget, ...]:
        return build_remote_watch_targets(
            compiled,
            on_invalid_target=lambda line: self._emit_log_line(line),
        )

    def _build_local_state_field_index(self, compiled: CompiledRuntimeGraphs) -> dict[str, tuple[str, ...]]:
        return build_local_state_field_index(compiled, studio_service_id=self.studio_service_id)

    async def _apply_remote_state_watches_async(self, compiled: CompiledRuntimeGraphs) -> None:
        next_cache, _applied = await apply_remote_state_watches_if_changed(
            compiled=compiled,
            remote_state_gateway=self._remote_state_gateway,
            watch_targets_cache=self._watch_targets_cache,
            build_remote_watch_targets=self._build_remote_watch_targets,
        )
        self._watch_targets_cache = next_cache

    async def _deploy_service_rungraph_async(self, service_id: str, *, compiled: CompiledRuntimeGraphs | None = None) -> None:
        """
        Deploy the last compiled per-service rungraph for a single service (best-effort).
        """
        compiled = pick_compiled(compiled, self._last_compiled)
        if compiled is None:
            return
        await self._rungraph_deploy_flow.deploy_service_rungraph(service_id=str(service_id), compiled=compiled)

    async def _install_studio_graph_async(self, *, compiled: CompiledRuntimeGraphs | None = None) -> None:
        """
        Reinstall the studio runtime graph from the last compiled graphs (best-effort).
        """
        compiled = pick_compiled(compiled, self._last_compiled)
        if compiled is None:
            return
        self._local_state_fields_by_node = self._build_local_state_field_index(compiled)
        if not await self._ensure_studio_runtime_async():
            return
        await install_studio_runtime_graph(
            compiled=compiled,
            get_service_bus=self._studio_service_bus,
            build_studio_runtime_graph=self._build_studio_runtime_graph,
            emit_log=self._emit_log_line,
        )
        try:
            await self._apply_remote_state_watches_async(compiled)
        except Exception as exc:
            self._report_exception("apply remote state watches failed", exc)

    async def _deploy_and_monitor_async(self, compiled: CompiledRuntimeGraphs) -> None:
        await self._rungraph_deploy_flow.deploy_all_service_rungraphs(compiled=compiled)

        # Install studio runtime graph (studio operators + edges).
        if self._studio_service_bus() is None:
            return
        await install_studio_runtime_graph(
            compiled=compiled,
            get_service_bus=self._studio_service_bus,
            build_studio_runtime_graph=self._build_studio_runtime_graph,
            emit_log=self._emit_log_line,
        )

        try:
            await self._apply_remote_state_watches_async(compiled)
        except Exception as exc:
            self._report_exception("apply remote state watches failed", exc)

    def set_local_state(self, node_id: str, field: str, value: Any) -> None:
        """
        Set state in the local studio service KV (best-effort).
        """
        node_id = ensure_token(node_id, label="node_id")
        field = str(field or "").strip()
        if not field:
            return
        allowed_fields = self._local_state_fields_by_node.get(node_id)
        if allowed_fields is None or field not in allowed_fields:
            return

        async def _do() -> None:
            if self._svc is None or self._svc.bus is None:
                return
            try:
                await self._svc.bus.publish_state_external(node_id, field, value, source="pystudio")
            except StateWriteError as exc:
                if "unknown state field" in str(exc):
                    logger.warning("Skip local state publish for unknown field: %s.%s", node_id, field)
                    return
                self._report_exception("publish local state failed", exc)
            except Exception as exc:
                self._report_exception("publish local state failed", exc)

        self._submit_async(_do(), context=f"submit set_local_state failed nodeId={node_id}")

    def set_remote_state(self, service_id: str, node_id: str, field: str, value: Any) -> None:
        """
        Set state in a managed remote service via its `set_state` endpoint (best-effort).

        This is used to propagate UI property edits into the runtime so the
        running node behavior matches the values shown in Node Properties.
        """
        try:
            service_id = ensure_token(str(service_id), label="service_id")
            node_id = ensure_token(str(node_id), label="node_id")
        except ValueError:
            return
        field = str(field or "").strip()
        if not field:
            return

        value_json = coerce_json_value(value)

        async def _do() -> None:
            nc = await self._ensure_nc()
            if nc is None:
                return
            result = await request_set_remote_state(
                nc,
                service_id=service_id,
                node_id=node_id,
                field=field,
                value=value_json,
                attempts=3,
                timeout_s=0.5,
                retry_sleep_s=0.1,
            )
            if result.accepted:
                return
            if result.rejected and (result.reject_code or result.reject_message):
                self._emit_log_line(
                    f"set_state rejected serviceId={service_id} nodeId={node_id} field={field} "
                    f"code={result.reject_code} msg={result.reject_message}"
                )
                return

        self._submit_async(
            _do(),
            context=f"submit set_remote_state failed serviceId={service_id} nodeId={node_id} field={field}",
        )


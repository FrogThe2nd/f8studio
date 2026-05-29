from __future__ import annotations

import asyncio
import logging
from typing import Any

from qtpy import QtCore

from f8pysdk.f8_naming import data_key
from f8pysdk.specs import F8RuntimeGraph
from f8pysdk.f8_naming import ensure_token
from f8pysdk.state import StateWriteError

from .data_port_sampler import summarize_data_port_payload
from .json_codec import coerce_json_value
from .managed_service_inventory import collect_managed_service_inventory
from .rungraph_deploy_flow import pick_compiled
from .runtime_graph_projection import (
    build_local_state_field_index,
    build_remote_watch_targets,
    build_studio_runtime_graph,
)
from .service_endpoint_client import request_set_remote_state
from .studio_runtime_flow import apply_remote_state_watches_if_changed, install_studio_runtime_graph
from .remote_state_watcher import WatchTarget
from ..nodegraph.runtime_compiler import CompiledRuntimeGraphs
from f8pystudio.studio_specs.registry import SERVICE_CLASS

logger = logging.getLogger(__name__)
REMOTE_SERVICE_ENSURE_CONCURRENCY = 4
_REMOTE_STATE_WATCH_APPLY_ERRORS = (Exception,)
_REMOTE_SERVICE_ENSURE_ERRORS = (Exception,)
_LOCAL_STATE_PUBLISH_ERRORS = (Exception,)
_DATA_PORT_SAMPLE_ERRORS = (AttributeError, OSError, RuntimeError, TimeoutError, TypeError, ValueError)


class DeployStateControllerMixin:
    def _remember_compiled_graphs(self, compiled: CompiledRuntimeGraphs) -> None:
        self._last_compiled = compiled
        self._local_state_fields_by_node = self._build_local_state_field_index(compiled)

    def sync_studio_runtime(self, compiled: CompiledRuntimeGraphs) -> None:
        """
        Refresh only the in-process studio runtime graph from a compiled snapshot.

        Unlike full `deploy()`, this does not start external services or deploy
        per-service rungraphs. It exists so built-in studio operators and local
        state edges can respond immediately after graph edits.
        """
        self._remember_compiled_graphs(compiled)
        self._submit_async(
            self._refresh_studio_runtime_async(compiled=compiled),
            context="submit sync_studio_runtime failed",
        )

    def deploy(self, compiled: CompiledRuntimeGraphs) -> None:
        """
        Starts service processes (if not running), deploys per-service graphs,
        installs the studio runtime graph, and enables remote state monitoring.
        """
        self._remember_compiled_graphs(compiled)
        inventory = collect_managed_service_inventory(
            services=list(compiled.global_graph.services or []),
            studio_service_id=self.studio_service_id,
            studio_service_class=SERVICE_CLASS,
            on_collect_error=lambda exc: self._emit_log_line(f"start service failed: {exc}"),
        )
        previous_service_classes = dict(self._managed_service_classes)
        self._managed_service_ids = set(inventory.service_ids)
        self._managed_service_classes = dict(inventory.service_classes)

        self._submit_async(
            self._ensure_remote_services_and_deploy_async(
                compiled,
                start_order=tuple(inventory.start_order),
                previous_service_classes=previous_service_classes,
            ),
            context="submit deploy_remote_services_and_refresh_studio failed",
        )
        # Preserve the current global lifecycle preference across repeated deploys.
        # Only enforce deactivate here when globally paused; avoid forcing activate
        # on every F5, which can override rungraph/state-edge driven inactive states.
        if not bool(self._managed_active):
            self.set_managed_active(False)

    def deploy_and_wait(self, compiled: CompiledRuntimeGraphs, *, timeout_s: float = 20.0) -> dict[str, Any]:
        """
        Synchronous automation boundary for full deploy.

        GUI callers keep using `deploy()`; CLI/MCP callers use this so the
        response can state whether the async deploy completed before timeout.
        """
        self._remember_compiled_graphs(compiled)
        inventory = collect_managed_service_inventory(
            services=list(compiled.global_graph.services or []),
            studio_service_id=self.studio_service_id,
            studio_service_class=SERVICE_CLASS,
            on_collect_error=lambda exc: self._emit_log_line(f"start service failed: {exc}"),
        )
        previous_service_classes = dict(self._managed_service_classes)
        self._managed_service_ids = set(inventory.service_ids)
        self._managed_service_classes = dict(inventory.service_classes)
        future = self._submit_async_future(
            self._ensure_remote_services_and_deploy_async(
                compiled,
                start_order=tuple(inventory.start_order),
                previous_service_classes=previous_service_classes,
            ),
            context="submit deploy_and_wait failed",
        )
        if future is None:
            return {"submitted": False, "completed": False, "error": "submit failed"}
        completed = self._wait_for_submitted_future(
            future,
            timeout_s=float(timeout_s),
            context="deploy_and_wait failed",
            timeout_message="deploy_and_wait timed out",
        )
        if not bool(self._managed_active):
            self.set_managed_active(False)
        return {
            "submitted": True,
            "completed": bool(completed["completed"]),
            "error": str(completed["error"] or ""),
        }

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
            await self._refresh_studio_runtime_async(compiled=compiled)
            desired_class = ""
            resolved_compiled = pick_compiled(compiled, self._last_compiled)
            if resolved_compiled is not None:
                for service in list(resolved_compiled.global_graph.services or []):
                    if str(service.serviceId or "") == sid:
                        desired_class = str(service.serviceClass or "").strip()
                        break
            if not await self.ensure_service_available(sid, desired_class):
                return
            await self._deploy_service_rungraph_async(sid, compiled=compiled)

        self._submit_async(_do(), context=f"submit deploy_service_rungraph failed serviceId={sid}")

    def deploy_service_and_wait(
        self,
        service_id: str,
        *,
        compiled: CompiledRuntimeGraphs | None = None,
        timeout_s: float = 10.0,
    ) -> dict[str, Any]:
        try:
            sid = ensure_token(str(service_id), label="service_id")
        except ValueError as exc:
            return {"submitted": False, "completed": False, "error": str(exc)}
        if sid == self.studio_service_id:
            return {"submitted": False, "completed": False, "error": "cannot deploy studio service rungraph directly"}

        async def _do() -> dict[str, Any]:
            await self._refresh_studio_runtime_async(compiled=compiled)
            resolved_compiled = pick_compiled(compiled, self._last_compiled)
            if resolved_compiled is None:
                return {"deployed": False, "error": "no compiled rungraph available"}
            desired_class = ""
            for service in list(resolved_compiled.global_graph.services or []):
                if str(service.serviceId or "") == sid:
                    desired_class = str(service.serviceClass or "").strip()
                    break
            if not await self.ensure_service_available(sid, desired_class):
                return {"deployed": False, "error": "service unavailable"}
            await self._deploy_service_rungraph_async(sid, compiled=resolved_compiled)
            return {"deployed": True, "error": ""}

        future = self._submit_async_future(_do(), context=f"submit deploy_service_and_wait failed serviceId={sid}")
        if future is None:
            return {"submitted": False, "completed": False, "error": "submit failed"}
        completed = self._wait_for_submitted_future(
            future,
            timeout_s=float(timeout_s),
            context=f"deploy_service_and_wait failed serviceId={sid}",
            timeout_message=f"deploy_service_and_wait timed out serviceId={sid}",
        )
        result = completed["result"]
        result_dict = result if isinstance(result, dict) else {}
        return {
            "submitted": True,
            "completed": bool(completed["completed"]),
            "deployed": bool(result_dict.get("deployed", False)),
            "error": str(completed["error"] or result_dict.get("error") or ""),
        }

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

    async def _deploy_service_rungraph_async(
        self, service_id: str, *, compiled: CompiledRuntimeGraphs | None = None
    ) -> None:
        """
        Deploy the last compiled per-service rungraph for a single service (best-effort).
        """
        compiled = pick_compiled(compiled, self._last_compiled)
        if compiled is None:
            return
        await self._rungraph_deploy_flow.deploy_service_rungraph(service_id=str(service_id), compiled=compiled)

    async def _refresh_studio_runtime_async(self, *, compiled: CompiledRuntimeGraphs | None = None) -> None:
        """
        Refresh the in-process studio runtime from the last compiled graphs (best-effort).

        This shared path keeps the local state-field index, installs the studio
        subgraph into the in-process runtime, and reapplies remote watches.
        """
        compiled = pick_compiled(compiled, self._last_compiled)
        if compiled is None:
            return
        self._remember_compiled_graphs(compiled)
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
        except _REMOTE_STATE_WATCH_APPLY_ERRORS as exc:
            self._report_exception("apply remote state watches failed", exc)

    async def _deploy_remote_services_and_refresh_studio_async(self, compiled: CompiledRuntimeGraphs) -> None:
        await self._rungraph_deploy_flow.deploy_all_service_rungraphs(compiled=compiled)
        await self._refresh_studio_runtime_async(compiled=compiled)

    async def _ensure_remote_services_and_deploy_async(
        self,
        compiled: CompiledRuntimeGraphs,
        *,
        start_order: tuple[tuple[str, str], ...],
        previous_service_classes: dict[str, str],
    ) -> None:
        semaphore = asyncio.Semaphore(REMOTE_SERVICE_ENSURE_CONCURRENCY)

        async def _ensure_one(service_id: str, service_class: str) -> str | None:
            try:
                sid = ensure_token(str(service_id), label="service_id")
            except ValueError as exc:
                self._emit_log_line(f"deploy blocked: invalid serviceId {service_id!r}: {exc}")
                return None
            async with semaphore:
                try:
                    ok = await self.ensure_service_available(
                        sid,
                        str(service_class),
                        local_known_service_class=previous_service_classes.get(sid),
                    )
                except _REMOTE_SERVICE_ENSURE_ERRORS as exc:
                    self._report_exception(f"ensure service available failed serviceId={sid}", exc)
                    return None
            return sid if ok else None

        tasks = [
            asyncio.create_task(_ensure_one(service_id, service_class), name=f"ensure_service:{service_id}")
            for service_id, service_class in list(start_order)
        ]
        ensured_service_ids = {sid for sid in await asyncio.gather(*tasks) if sid is not None}
        await self._rungraph_deploy_flow.deploy_selected_service_rungraphs(
            compiled=compiled,
            allowed_service_ids=ensured_service_ids,
        )
        await self._refresh_studio_runtime_async(compiled=compiled)

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
            except _LOCAL_STATE_PUBLISH_ERRORS as exc:
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
            requester = await self._ensure_requester()
            if requester is None:
                return
            result = await request_set_remote_state(
                requester,
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

    def set_remote_state_and_wait(
        self,
        service_id: str,
        node_id: str,
        field: str,
        value: Any,
        *,
        timeout_s: float = 2.0,
    ) -> dict[str, Any]:
        try:
            sid = ensure_token(str(service_id), label="service_id")
            nid = ensure_token(str(node_id), label="node_id")
        except ValueError as exc:
            return {"submitted": False, "completed": False, "accepted": False, "error": str(exc)}
        state_field = str(field or "").strip()
        if not state_field:
            return {"submitted": False, "completed": False, "accepted": False, "error": "field is required"}
        value_json = coerce_json_value(value)

        async def _do() -> dict[str, Any]:
            requester = await self._ensure_requester()
            if requester is None:
                return {"accepted": False, "rejected": False, "error": "requester unavailable"}
            result = await request_set_remote_state(
                requester,
                service_id=sid,
                node_id=nid,
                field=state_field,
                value=value_json,
                attempts=3,
                timeout_s=0.5,
                retry_sleep_s=0.1,
            )
            return {
                "accepted": bool(result.accepted),
                "rejected": bool(result.rejected),
                "rejectCode": result.reject_code,
                "rejectMessage": result.reject_message,
                "error": "",
            }

        future = self._submit_async_future(
            _do(),
            context=f"submit set_remote_state_and_wait failed serviceId={sid} nodeId={nid} field={state_field}",
        )
        if future is None:
            return {"submitted": False, "completed": False, "accepted": False, "error": "submit failed"}
        completed = self._wait_for_submitted_future(
            future,
            timeout_s=float(timeout_s),
            context=f"set_remote_state_and_wait failed serviceId={sid} nodeId={nid} field={state_field}",
            timeout_message=f"set_remote_state_and_wait timed out serviceId={sid} nodeId={nid} field={state_field}",
        )
        result = completed["result"]
        result_dict = result if isinstance(result, dict) else {}
        return {
            "submitted": True,
            "completed": bool(completed["completed"]),
            "accepted": bool(result_dict.get("accepted", False)),
            "rejected": bool(result_dict.get("rejected", False)),
            "rejectCode": str(result_dict.get("rejectCode") or ""),
            "rejectMessage": str(result_dict.get("rejectMessage") or ""),
            "error": str(completed["error"] or result_dict.get("error") or ""),
        }

    def sample_data_port_and_wait(
        self,
        service_id: str,
        node_id: str,
        port: str,
        *,
        limit: int = 1,
        timeout_s: float = 2.0,
        include_value: bool = True,
        max_value_bytes: int = 65536,
    ) -> dict[str, Any]:
        try:
            sid = ensure_token(str(service_id), label="service_id")
            nid = ensure_token(str(node_id), label="node_id")
            port_id = ensure_token(str(port), label="port_id")
        except ValueError as exc:
            return {"submitted": False, "completed": False, "samples": [], "error": str(exc)}
        sample_limit = max(1, min(int(limit), 100))
        timeout = max(0.0, float(timeout_s))

        async def _do() -> dict[str, Any]:
            transport = await self._ensure_runtime_transport()
            key = data_key(sid, from_node_id=nid, port_id=port_id)
            samples: list[dict[str, Any]] = []
            done = asyncio.Event()

            async def _on_sample(sample_key: str, payload: bytes) -> None:
                samples.append(
                    summarize_data_port_payload(
                        service_id=sid,
                        node_id=nid,
                        port=port_id,
                        key=str(sample_key),
                        payload=bytes(payload),
                        include_value=bool(include_value),
                        max_value_bytes=int(max_value_bytes),
                    )
                )
                if len(samples) >= sample_limit:
                    done.set()

            sub = await transport.subscribe(key, cb=_on_sample)
            try:
                try:
                    await asyncio.wait_for(done.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    return {"samples": list(samples[-sample_limit:]), "timedOut": True, "error": ""}
                return {"samples": list(samples[-sample_limit:]), "timedOut": False, "error": ""}
            finally:
                await sub.unsubscribe()

        future = self._submit_async_future(
            _do(),
            context=f"submit sample_data_port_and_wait failed serviceId={sid} nodeId={nid} port={port_id}",
        )
        if future is None:
            return {"submitted": False, "completed": False, "samples": [], "error": "submit failed"}
        completed = self._wait_for_submitted_future(
            future,
            timeout_s=timeout + 1.0,
            context=f"sample_data_port_and_wait failed serviceId={sid} nodeId={nid} port={port_id}",
            timeout_message=f"sample_data_port_and_wait timed out serviceId={sid} nodeId={nid} port={port_id}",
        )
        result = completed["result"]
        result_dict = result if isinstance(result, dict) else {}
        return {
            "submitted": True,
            "completed": bool(completed["completed"]),
            "samples": list(result_dict.get("samples") if isinstance(result_dict.get("samples"), list) else []),
            "timedOut": bool(result_dict.get("timedOut", False)),
            "error": str(completed["error"] or result_dict.get("error") or ""),
        }

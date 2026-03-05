from __future__ import annotations

from f8pysdk.msgspec_codec import dump_json
from f8pysdk.service_bus.codec import decode_obj
from typing import Any

from f8pysdk.runtime_node_registry import RuntimeNodeRegistry

from .nats_lifecycle import ensure_nats_server_owned_pid, stop_owned_nats_server
from .remote_state_sync import RemoteStateGatewayAdapter
from .studio_runtime_flow import wait_for_studio_runtime_ready
from ..pystudio_service import PyStudioService, PyStudioServiceConfig
from ..remote_state_watcher import RemoteStateWatcher
from ..ui_bus import UiCommand


class RuntimeSessionControllerMixin:
    async def _ensure_studio_runtime_async(self, *, timeout_s: float = 6.0) -> bool:
        """
        Best-effort wait for the in-process studio runtime (ServiceRuntime) to be ready.
        """
        return await wait_for_studio_runtime_ready(
            get_service_bus=self._studio_service_bus,
            emit_log=self._emit_log_line,
            timeout_s=float(timeout_s),
            poll_interval_s=0.08,
        )

    def _studio_service_bus(self) -> Any | None:
        svc = self._svc
        if svc is None:
            return None
        return svc.bus

    async def _start_async(self) -> None:
        nats_url = str(self._nats_connection_manager.nats_url).strip() or "nats://127.0.0.1:4222"
        owned_pid = await ensure_nats_server_owned_pid(
            nats_url,
            emit_log=self._emit_log_line,
            report_exception=self._report_exception,
        )
        if owned_pid is not None:
            self._owned_nats_server_pid = int(owned_pid)

        # Singleton guard (best-effort): if any existing studio ServiceBus micro responds, do not start.
        self._nc = await self._nats_connection_manager.connect(context="connect nats for singleton guard failed")
        guard = await self._nats_connection_manager.singleton_guard(
            self._nc,
            studio_service_id=self.studio_service_id,
            ping_timeout_s=0.2,
        )
        self._nc = guard.connection
        if not bool(guard.should_start):
            return

        try:
            cfg = PyStudioServiceConfig(nats_url=nats_url, studio_service_id=self.studio_service_id)
            self._svc = PyStudioService(cfg, registry=RuntimeNodeRegistry.instance())
            await self._svc.start(
                on_ui_command=lambda cmd: self.ui_command.emit(cmd),
            )
        except Exception as exc:
            self._emit_log_line(f"studio runtime start failed: {exc}")
            self._svc = None

        # Studio-side remote KV watcher (monitors all remote node state and mirrors into UI).
        if self._remote_state_watcher is None:
            async def _on_state(
                service_id: str,
                node_id: str,
                field: str,
                value: Any,
                ts_ms: int,
                meta: dict[str, Any],
            ) -> None:
                _ = meta
                try:
                    self.ui_command.emit(
                        UiCommand(
                            node_id=str(node_id),
                            command="state.update",
                            payload={"serviceId": str(service_id), "field": str(field), "value": value},
                            ts_ms=int(ts_ms),
                        )
                    )
                except RuntimeError as exc:
                    self._report_exception("emit ui state.update failed", exc)

            try:
                self._remote_state_watcher = RemoteStateWatcher(
                    nats_url=nats_url,
                    studio_service_id=self.studio_service_id,
                    on_state=_on_state,
                )
                self._remote_state_gateway = RemoteStateGatewayAdapter(self._remote_state_watcher)
                await self._remote_state_gateway.start()
            except Exception as exc:
                self._report_exception("start remote state watcher failed", exc)
                self._remote_state_watcher = None
                self._remote_state_gateway = None

        if self._nc is not None and self._monitor_sub is None:
            async def _on_monitor_msg(msg: Any) -> None:
                try:
                    raw = bytes(msg.data or b"")
                except (AttributeError, TypeError, ValueError):
                    return
                if not raw:
                    return
                try:
                    envelope = decode_obj(raw)
                except ValueError:
                    return
                value = envelope.get("value")
                if not isinstance(value, dict):
                    return
                try:
                    snapshot = self._monitor_center.ingest_snapshot(value)
                except Exception as exc:
                    self._report_exception("ingest monitor snapshot failed", exc)
                    return
                self._monitor_center.update_service_status(
                    service_id=str(snapshot.serviceId),
                    alive=True,
                    ready=bool(snapshot.ready),
                    active=bool(snapshot.active),
                )
                try:
                    self.ui_command.emit(
                        UiCommand(
                            node_id=str(snapshot.serviceId),
                            command="monitor.update",
                            payload=dump_json(snapshot, mode="json", by_alias=True),
                            ts_ms=int(snapshot.tsMs),
                        )
                    )
                except RuntimeError as exc:
                    self._report_exception("emit ui monitor.update failed", exc)

            try:
                self._monitor_sub = await self._nc.subscribe("svc.*.nodes.*.data.monitor", cb=_on_monitor_msg)
            except Exception as exc:
                self._report_exception("subscribe monitor stream failed", exc)

        # Re-apply current desired lifecycle to any already-known managed services.
        try:
            await self._set_managed_active_async(bool(self._managed_active))
        except Exception as exc:
            self._report_exception("re-apply managed active failed", exc)

    async def _stop_async(self) -> None:
        if self._monitor_sub is not None:
            try:
                await self._monitor_sub.unsubscribe()
            except Exception as exc:
                self._report_exception("unsubscribe monitor stream failed", exc)
        self._monitor_sub = None
        try:
            if self._remote_state_gateway is not None:
                await self._remote_state_gateway.stop()
            elif self._remote_state_watcher is not None:
                await self._remote_state_watcher.stop()
        except Exception as exc:
            self._report_exception("stop remote state watcher failed", exc)
        self._remote_state_gateway = None
        self._remote_state_watcher = None
        self._watch_targets_cache = None
        try:
            if self._svc is not None:
                await self._svc.stop()
        except Exception as exc:
            self._report_exception("stop studio service failed", exc)
        self._svc = None

        try:
            await self._command_gateway.close()
        except Exception as exc:
            self._report_exception("close command gateway failed", exc)

        await self._nats_connection_manager.close(self._nc, context="close nats connection failed")
        self._nc = None

        owned_nats_pid = self._owned_nats_server_pid
        self._owned_nats_server_pid = None
        if owned_nats_pid is not None:
            stopped = await stop_owned_nats_server(
                int(owned_nats_pid),
                emit_log=self._emit_log_line,
                report_exception=self._report_exception,
            )
            if not stopped:
                self._emit_log_line(f"failed to stop studio-owned nats-server pid={owned_nats_pid}")

    async def _ensure_nc(self) -> Any | None:
        """
        Ensure a NATS connection exists for command channel requests.
        """
        if self._nc is not None:
            return self._nc
        self._nc = await self._nats_connection_manager.connect(context="ensure nats connection failed")
        return self._nc

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from f8pysdk.codec import decode_obj, dump_json

from .nats_lifecycle import (
    SINGLETON_GUARD_DIALOG_MESSAGE,
    ensure_nats_server_owned_pid,
    stop_owned_nats_server,
)
from .remote_state_sync import RemoteStateGatewayAdapter
from .studio_runtime_flow import wait_for_studio_runtime_ready
from f8pystudio.bridge.studio_service import PyStudioService, PyStudioServiceConfig
from f8pystudio.bridge.remote_state_watcher import RemoteStateWatcher
from f8pystudio.contracts.ui_commands import UiCommand
from f8pystudio.studio_specs.registry import shared_pystudio_registry

_MONITOR_UI_EMIT_INTERVAL_S = 1.0


@dataclass(frozen=True)
class PendingMonitorUpdate:
    payload: dict[str, Any]
    ts_ms: int


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

    def _emit_monitor_ui_update_now(self, *, service_id: str, update: PendingMonitorUpdate) -> None:
        try:
            self.ui_command.emit(
                UiCommand(
                    node_id=str(service_id),
                    command="monitor.update",
                    payload=update.payload,
                    ts_ms=int(update.ts_ms),
                )
            )
        except RuntimeError as exc:
            self._report_exception("emit ui monitor.update failed", exc)

    def _queue_monitor_ui_update(self, *, service_id: str, payload: dict[str, Any], ts_ms: int) -> None:
        sid = str(service_id or "").strip()
        if not sid:
            return
        update = PendingMonitorUpdate(payload=dict(payload), ts_ms=int(ts_ms))
        now_s = time.monotonic()
        last_emit_s = float(self._monitor_ui_last_emit_s_by_service.get(sid, 0.0))
        if last_emit_s <= 0.0 or (now_s - last_emit_s) >= _MONITOR_UI_EMIT_INTERVAL_S:
            self._monitor_ui_last_emit_s_by_service[sid] = now_s
            self._emit_monitor_ui_update_now(service_id=sid, update=update)
            return

        self._monitor_ui_pending_by_service[sid] = update
        task = self._monitor_ui_flush_task
        if task is not None and not task.done():
            return
        self._monitor_ui_flush_task = asyncio.create_task(
            self._flush_monitor_ui_updates(),
            name="pystudio:monitor_ui_flush",
        )

    async def _flush_monitor_ui_updates(self) -> None:
        try:
            while self._monitor_ui_pending_by_service:
                now_s = time.monotonic()
                ready_service_ids: list[str] = []
                next_delay_s: float | None = None
                for service_id in list(self._monitor_ui_pending_by_service.keys()):
                    last_emit_s = float(self._monitor_ui_last_emit_s_by_service.get(service_id, 0.0))
                    if last_emit_s <= 0.0:
                        ready_service_ids.append(service_id)
                        continue
                    remaining_s = _MONITOR_UI_EMIT_INTERVAL_S - (now_s - last_emit_s)
                    if remaining_s <= 0.0:
                        ready_service_ids.append(service_id)
                        continue
                    if next_delay_s is None or remaining_s < next_delay_s:
                        next_delay_s = remaining_s

                if ready_service_ids:
                    emit_s = time.monotonic()
                    for service_id in ready_service_ids:
                        update = self._monitor_ui_pending_by_service.pop(service_id, None)
                        if update is None:
                            continue
                        self._monitor_ui_last_emit_s_by_service[service_id] = emit_s
                        self._emit_monitor_ui_update_now(service_id=service_id, update=update)
                    continue

                await asyncio.sleep(max(0.001, float(next_delay_s or _MONITOR_UI_EMIT_INTERVAL_S)))
        finally:
            self._monitor_ui_flush_task = None

    async def _run_startup_preflight_async(self) -> str | None:
        nats_url = str(self._nats_connection_manager.nats_url).strip() or "nats://127.0.0.1:4222"
        if self._owned_nats_server_pid is None:
            owned_pid = await ensure_nats_server_owned_pid(
                nats_url,
                emit_log=self._emit_log_line,
                report_exception=self._report_exception,
            )
            if owned_pid is not None:
                self._owned_nats_server_pid = int(owned_pid)

        # The singleton probe should fail fast. If NATS is still unavailable here,
        # let startup continue rather than blocking on the client's reconnect loop.
        self._nc = await self._nats_connection_manager.connect(
            context="connect nats for singleton guard failed",
            allow_reconnect=False,
        )
        guard = await self._nats_connection_manager.singleton_guard(
            self._nc,
            studio_service_id=self.studio_service_id,
            ping_timeout_s=0.2,
        )
        self._nc = guard.connection
        if not bool(guard.should_start):
            return SINGLETON_GUARD_DIALOG_MESSAGE
        return None

    async def _start_after_preflight_async(self) -> str | None:
        nats_url = str(self._nats_connection_manager.nats_url).strip() or "nats://127.0.0.1:4222"
        if self._owned_nats_server_pid is None:
            owned_pid = await ensure_nats_server_owned_pid(
                nats_url,
                emit_log=self._emit_log_line,
                report_exception=self._report_exception,
            )
            if owned_pid is not None:
                self._owned_nats_server_pid = int(owned_pid)

        try:
            cfg = PyStudioServiceConfig(nats_url=nats_url, studio_service_id=self.studio_service_id)
            self._svc = PyStudioService(cfg, registry=shared_pystudio_registry())
            await self._svc.start(
                on_ui_command=lambda cmd: self.ui_command.emit(cmd),
            )
            self._cache_service_alive(self.studio_service_id, True)
            self._cache_service_active(self.studio_service_id, True)
            self._monitor_center.update_service_status(service_id=self.studio_service_id, ready=True)
        except Exception as exc:
            self._emit_log_line(f"studio runtime start failed: {exc}")
            self._svc = None
            self._cache_service_alive(self.studio_service_id, False)
            self._cache_service_active(self.studio_service_id, None)
            self._monitor_center.update_service_status(service_id=self.studio_service_id, ready=False)

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
                payload = dump_json(snapshot, mode="json", by_alias=True)
                if isinstance(payload, dict):
                    self._queue_monitor_ui_update(
                        service_id=str(snapshot.serviceId),
                        payload=payload,
                        ts_ms=int(snapshot.tsMs),
                    )

            try:
                self._monitor_sub = await self._nc.subscribe("svc.*.nodes.*.data.monitor", cb=_on_monitor_msg)
            except Exception as exc:
                self._report_exception("subscribe monitor stream failed", exc)

        # Re-apply current desired lifecycle to any already-known managed services.
        try:
            await self._set_managed_active_async(bool(self._managed_active))
        except Exception as exc:
            self._report_exception("re-apply managed active failed", exc)
        return None

    async def _start_async(self) -> str | None:
        startup_blocked_message = await self._run_startup_preflight_async()
        if startup_blocked_message is not None:
            return startup_blocked_message
        return await self._start_after_preflight_async()

    async def _stop_async(self) -> None:
        monitor_ui_flush_task = self._monitor_ui_flush_task
        self._monitor_ui_flush_task = None
        self._monitor_ui_pending_by_service.clear()
        if monitor_ui_flush_task is not None:
            monitor_ui_flush_task.cancel()
            await asyncio.gather(monitor_ui_flush_task, return_exceptions=True)

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
        self._cache_service_alive(self.studio_service_id, False)
        self._cache_service_active(self.studio_service_id, None)
        self._monitor_center.update_service_status(service_id=self.studio_service_id, ready=False)

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

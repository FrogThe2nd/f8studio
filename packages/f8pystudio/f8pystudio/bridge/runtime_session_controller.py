from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import msgspec
from f8pysdk.bus import BusBackend
from f8pysdk.codec import decode_obj, dump_json
from f8pysdk.zenoh_naming import zenoh_studio_liveliness_key
from f8pysdk.zenoh_shutdown import close_zenoh_session_best_effort

from .service_liveliness import (
    ServiceLivelinessQueryResult,
    ZENOH_SERVICE_LIVELINESS_PREFIX,
    is_zenoh_liveliness_reply_channel_drained,
    query_service_liveliness_instances_sync,
    service_liveliness_identity_from_zenoh_key,
)
from .runtime_lifecycle import (
    SINGLETON_GUARD_DIALOG_MESSAGE,
)
from .remote_state_sync import RemoteStateGatewayAdapter
from .studio_runtime_flow import wait_for_studio_runtime_ready
from f8pystudio.bridge.studio_service import PyStudioService, PyStudioServiceConfig
from f8pystudio.bridge.remote_state_watcher import RemoteStateWatcher
from f8pystudio.bridge.runtime_request import RuntimeTransportRequester
from f8pystudio.contracts.ui_commands import UiCommand
from f8pystudio.studio_specs.registry import shared_pystudio_registry

_MONITOR_UI_EMIT_INTERVAL_S = 1.0
_RUNTIME_BOUNDARY_ERRORS = (
    AttributeError,
    OSError,
    RuntimeError,
    TimeoutError,
    TypeError,
    ValueError,
    msgspec.DecodeError,
)


@dataclass(frozen=True)
class PendingMonitorUpdate:
    payload: dict[str, Any]
    ts_ms: int


class RuntimeSessionControllerMixin:
    _cfg: Any = None
    _runtime_transport_lock: asyncio.Lock | None = None
    _runtime_transport_lock_loop: asyncio.AbstractEventLoop | None = None

    def _runtime_bus_backend(self) -> BusBackend:
        cfg = self._cfg
        if cfg is None:
            return "zenoh"
        text = str(cfg.bus_backend).strip().lower()
        if text == "mem":
            return "mem"
        return "zenoh"

    def _runtime_zenoh_config_path(self) -> str | None:
        cfg = self._cfg
        if cfg is None:
            return None
        return str(cfg.zenoh_config_path).strip() if cfg.zenoh_config_path else None

    def _runtime_zenoh_connect(self) -> tuple[str, ...]:
        cfg = self._cfg
        if cfg is None:
            return ()
        return tuple(str(item).strip() for item in cfg.zenoh_connect if str(item).strip())

    def _runtime_zenoh_listen(self) -> tuple[str, ...]:
        cfg = self._cfg
        if cfg is None:
            return ()
        return tuple(str(item).strip() for item in cfg.zenoh_listen if str(item).strip())

    def _runtime_zenoh_shm_pool_bytes(self) -> int:
        cfg = self._cfg
        if cfg is None:
            return 256 * 1024 * 1024
        return max(0, int(cfg.zenoh_shm_pool_bytes))

    def _build_zenoh_session_config(self, zenoh_module: Any) -> Any:
        cfg = self._cfg
        if cfg is not None and cfg.zenoh_config_path:
            config = zenoh_module.Config.from_file(str(cfg.zenoh_config_path))
        else:
            config = zenoh_module.Config()
        if cfg is not None and cfg.zenoh_connect:
            import json

            config.insert_json5("connect/endpoints", json.dumps(list(cfg.zenoh_connect)))
        if cfg is not None and cfg.zenoh_listen:
            import json

            config.insert_json5("listen/endpoints", json.dumps(list(cfg.zenoh_listen)))
        return config

    @staticmethod
    def _zenoh_boundary_errors(zenoh_module: Any) -> tuple[type[BaseException], ...]:
        try:
            zerror: type[BaseException] = zenoh_module.ZError
        except AttributeError:
            return _RUNTIME_BOUNDARY_ERRORS
        return (*_RUNTIME_BOUNDARY_ERRORS, zerror)

    async def _ensure_studio_runtime_async(self, *, timeout_s: float = 6.0) -> bool:
        """
        Best-effort wait for the in-process studio runtime (ServiceRuntime) to be ready.
        """
        if self._studio_service_bus() is None:
            await self._ensure_studio_service_started_async()
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

    def _studio_service_lock_for_loop(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        lock = self._studio_service_lock
        if lock is None or self._studio_service_lock_loop is not loop:
            lock = asyncio.Lock()
            self._studio_service_lock = lock
            self._studio_service_lock_loop = loop
        return lock

    async def _run_async_boundary_step(self, context: str, step: Callable[[], Awaitable[object]]) -> None:
        try:
            await step()
        except Exception as exc:
            self._report_exception(context, exc)

    def _run_sync_boundary_step(self, context: str, step: Callable[[], object]) -> None:
        try:
            step()
        except Exception as exc:
            self._report_exception(context, exc)

    async def _ensure_studio_service_started_async(self) -> bool:
        if self._studio_service_bus() is not None:
            return True
        async with self._studio_service_lock_for_loop():
            if self._studio_service_bus() is not None:
                return True
            svc: PyStudioService | None = None
            try:
                cfg = PyStudioServiceConfig(
                    bus_backend=self._runtime_bus_backend(),
                    zenoh_config_path=self._runtime_zenoh_config_path(),
                    zenoh_connect=self._runtime_zenoh_connect(),
                    zenoh_listen=self._runtime_zenoh_listen(),
                    zenoh_shm_pool_bytes=self._runtime_zenoh_shm_pool_bytes(),
                    studio_service_id=self.studio_service_id,
                )
                svc = PyStudioService(cfg, registry=shared_pystudio_registry())
                await svc.start(
                    on_ui_command=lambda cmd: self.ui_command.emit(cmd),
                )
                self._svc = svc
                self._cache_service_alive(self.studio_service_id, True)
                self._cache_service_active(self.studio_service_id, True)
                self._monitor_center.update_service_status(service_id=self.studio_service_id, ready=True)
                return True
            except _RUNTIME_BOUNDARY_ERRORS as exc:
                self._emit_log_line(f"studio runtime start failed: {exc}")
                if svc is not None:
                    await self._run_async_boundary_step(
                        "cleanup failed studio runtime start failed",
                        lambda: svc.stop(),
                    )
                self._svc = None
                self._cache_service_alive(self.studio_service_id, False)
                self._cache_service_active(self.studio_service_id, None)
                self._monitor_center.update_service_status(service_id=self.studio_service_id, ready=False)
                return False

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
        backend = self._runtime_bus_backend()
        if backend == "zenoh":
            return await self._run_zenoh_startup_preflight_async()
        return None

    async def _run_zenoh_startup_preflight_async(self) -> str | None:
        try:
            import zenoh  # type: ignore[import-not-found]
        except ImportError as exc:
            self._report_exception("import zenoh for singleton guard failed", exc)
            return None

        try:
            config = self._build_zenoh_session_config(zenoh)
            session = await asyncio.to_thread(zenoh.open, config)
        except self._zenoh_boundary_errors(zenoh) as exc:
            self._report_exception("open zenoh for singleton guard failed", exc)
            return None

        key = zenoh_studio_liveliness_key(self.studio_service_id)
        try:
            replies = session.liveliness().get(key, timeout=0.2)
            deadline = time.monotonic() + 0.25
            while time.monotonic() < deadline:
                try:
                    reply = replies.try_recv()
                except zenoh.ZError as exc:  # type: ignore[attr-defined]
                    if is_zenoh_liveliness_reply_channel_drained(exc):
                        break
                    raise
                if reply is None:
                    await asyncio.sleep(0.01)
                    continue
                if reply.ok is not None:
                    close_zenoh_session_best_effort(
                        session,
                        context="pystudio-singleton-conflict",
                        native_close=False,
                    )
                    return SINGLETON_GUARD_DIALOG_MESSAGE
            self._zenoh_singleton_session = session
            self._zenoh_singleton_token = session.liveliness().declare_token(key)
        except self._zenoh_boundary_errors(zenoh) as exc:
            self._report_exception("zenoh singleton guard failed", exc)
            close_zenoh_session_best_effort(
                session,
                context="pystudio-singleton-guard-error",
                native_close=False,
            )
        return None

    async def _start_zenoh_service_liveliness_watch_async(self) -> None:
        if self._runtime_bus_backend() != "zenoh":
            return
        if self._zenoh_service_liveliness_sub is not None:
            return
        try:
            import zenoh  # type: ignore[import-not-found]
        except ImportError as exc:
            self._report_exception("import zenoh for service liveliness watch failed", exc)
            return

        owns_session = False
        session = self._zenoh_singleton_session
        if session is None:
            try:
                config = self._build_zenoh_session_config(zenoh)
                session = await asyncio.to_thread(zenoh.open, config)
                owns_session = True
            except self._zenoh_boundary_errors(zenoh) as exc:
                self._report_exception("open zenoh for service liveliness watch failed", exc)
                return

        loop = asyncio.get_running_loop()

        def _on_sample(sample: Any) -> None:
            try:
                identity = service_liveliness_identity_from_zenoh_key(str(sample.key_expr))
                if identity is None:
                    return
                if sample.kind == zenoh.SampleKind.PUT:
                    loop.call_soon_threadsafe(
                        self._on_zenoh_service_liveliness,
                        identity.service_id,
                        identity.runtime_instance_id,
                        True,
                    )
                    return
                if sample.kind == zenoh.SampleKind.DELETE:
                    loop.call_soon_threadsafe(
                        self._on_zenoh_service_liveliness,
                        identity.service_id,
                        identity.runtime_instance_id,
                        False,
                    )
            except _RUNTIME_BOUNDARY_ERRORS as exc:
                loop.call_soon_threadsafe(self._report_exception, "zenoh service liveliness sample failed", exc)

        try:
            self._zenoh_service_liveliness_sub = session.liveliness().declare_subscriber(
                f"{ZENOH_SERVICE_LIVELINESS_PREFIX}**",
                _on_sample,
                history=True,
            )
            if owns_session:
                self._zenoh_service_liveliness_session = session
        except self._zenoh_boundary_errors(zenoh) as exc:
            self._report_exception("declare zenoh service liveliness watch failed", exc)
            if owns_session:
                close_zenoh_session_best_effort(
                    session,
                    context="pystudio-liveliness-watch-declare-failed",
                    native_close=False,
                )

    def _on_zenoh_service_liveliness(self, service_id: str, runtime_instance_id: str, alive: bool) -> None:
        sid = str(service_id or "").strip()
        rid = str(runtime_instance_id or "").strip()
        if not sid or not rid:
            return
        instances = self._service_liveliness_instances_by_service.setdefault(sid, set())
        if alive:
            instances.add(rid)
            self._cache_service_alive(sid, True)
            self.request_service_status(sid)
            return
        instances.discard(rid)
        if instances:
            self._cache_service_alive(sid, True)
            return
        self._service_liveliness_instances_by_service.pop(sid, None)
        self._cache_service_alive(sid, False)
        self._cache_service_active(sid, None)
        self._monitor_center.update_service_status(service_id=sid, ready=False)

    async def _query_zenoh_service_liveliness_instances_async(
        self,
        service_id: str,
        *,
        timeout_s: float = 0.25,
    ) -> set[str] | None:
        sid = str(service_id or "").strip()
        if not sid or self._runtime_bus_backend() != "zenoh":
            return set()
        try:
            import zenoh  # type: ignore[import-not-found]
        except ImportError as exc:
            self._report_exception("import zenoh for service liveliness query failed", exc)
            return None

        owns_session = False
        session = self._zenoh_singleton_session
        if session is None:
            try:
                config = self._build_zenoh_session_config(zenoh)
                session = await asyncio.to_thread(zenoh.open, config)
                owns_session = True
            except self._zenoh_boundary_errors(zenoh) as exc:
                self._report_exception("open zenoh for service liveliness query failed", exc)
                return None

        result: ServiceLivelinessQueryResult | None = None
        try:
            result = await asyncio.to_thread(
                query_service_liveliness_instances_sync,
                zenoh_module=zenoh,
                session=session,
                service_id=sid,
                timeout_s=float(timeout_s),
            )
            if result.error is not None:
                self._report_exception(f"query zenoh service liveliness failed serviceId={sid}", result.error)
        finally:
            if owns_session:
                close_zenoh_session_best_effort(
                    session,
                    context="pystudio-service-liveliness-query",
                    native_close=False,
                )
        if result is None:
            return None
        instances = set(result.instances)
        if instances:
            self._service_liveliness_instances_by_service[sid] = set(instances)
        elif result.query_ok:
            self._service_liveliness_instances_by_service.pop(sid, None)
        else:
            cached = self._service_liveliness_instances_by_service.get(sid)
            return set(cached) if cached is not None else None
        return instances

    async def _start_after_preflight_async(self) -> str | None:
        await self._ensure_studio_service_started_async()

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
                    studio_service_id=self.studio_service_id,
                    on_state=_on_state,
                    bus_backend=self._runtime_bus_backend(),
                    zenoh_config_path=self._runtime_zenoh_config_path(),
                    zenoh_connect=self._runtime_zenoh_connect(),
                    zenoh_listen=self._runtime_zenoh_listen(),
                    zenoh_shm_pool_bytes=self._runtime_zenoh_shm_pool_bytes(),
                )
                self._remote_state_gateway = RemoteStateGatewayAdapter(self._remote_state_watcher)
                await self._remote_state_gateway.start()
            except _RUNTIME_BOUNDARY_ERRORS as exc:
                self._report_exception("start remote state watcher failed", exc)
                self._remote_state_watcher = None
                self._remote_state_gateway = None

        await self._run_async_boundary_step(
            "start zenoh service liveliness watch failed",
            self._start_zenoh_service_liveliness_watch_async,
        )

        if self._monitor_sub is None:

            async def _on_monitor_payload(raw: bytes) -> None:
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
                except (TypeError, ValueError, msgspec.ValidationError) as exc:
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
                transport = await self._ensure_runtime_transport()

                async def _on_monitor_sample(_key: str, payload: bytes) -> None:
                    await _on_monitor_payload(bytes(payload))

                self._monitor_sub = await transport.subscribe("f8/svc/*/nodes/*/data/monitor", cb=_on_monitor_sample)
            except _RUNTIME_BOUNDARY_ERRORS as exc:
                self._report_exception("subscribe monitor stream failed", exc)

        # Re-apply current desired lifecycle to any already-known managed services.
        await self._run_async_boundary_step(
            "re-apply managed active failed",
            lambda: self._set_managed_active_async(bool(self._managed_active)),
        )

        compiled = self._last_compiled
        if compiled is not None:
            await self._run_async_boundary_step(
                "refresh studio runtime after bridge start failed",
                lambda: self._refresh_studio_runtime_async(compiled=compiled),
            )
        return None

    async def _start_async(self) -> str | None:
        startup_blocked_message = await self._run_startup_preflight_async()
        if startup_blocked_message is not None:
            return startup_blocked_message
        return await self._start_after_preflight_async()

    def _stop_known_service_process_for_shutdown(self, service_id: str) -> None:
        self._stop_process_once_local(service_id)
        if not self.is_service_running(service_id):
            self._cache_stopped_service(service_id)
        else:
            self._emit_log_line(f"[service] shutdown stop incomplete serviceId={service_id}")

    async def _stop_async(self) -> None:
        monitor_ui_flush_task = self._monitor_ui_flush_task
        self._monitor_ui_flush_task = None
        self._monitor_ui_pending_by_service.clear()
        if monitor_ui_flush_task is not None:
            monitor_ui_flush_task.cancel()
            await asyncio.gather(monitor_ui_flush_task, return_exceptions=True)

        if self.kill_managed_services_on_exit():
            service_ids = self._known_non_studio_service_ids()
            if service_ids:
                self._emit_log_line(f"[service] shutdown stopping {len(service_ids)} known service(s)")
            terminate_tasks: list[asyncio.Task[object]] = []
            for sid in service_ids:
                terminate_tasks.append(
                    asyncio.create_task(
                        self._request_service_terminate_async(sid),
                        name=f"pystudio:shutdown_terminate:{sid}",
                    )
                )
            if terminate_tasks:
                terminate_results = await asyncio.gather(*terminate_tasks, return_exceptions=True)
                for sid, result in zip(service_ids, terminate_results, strict=False):
                    if isinstance(result, BaseException):
                        self._report_exception(
                            f"request service terminate failed during shutdown serviceId={sid}",
                            result,
                        )
            for sid in service_ids:
                self._run_sync_boundary_step(
                    f"stop service process failed serviceId={sid}",
                    lambda service_id=sid: self._stop_known_service_process_for_shutdown(service_id),
                )

        monitor_sub = self._monitor_sub
        self._monitor_sub = None
        if monitor_sub is not None:
            await self._run_async_boundary_step(
                "unsubscribe monitor stream failed",
                lambda: monitor_sub.unsubscribe(),
            )
        liveliness_sub = self._zenoh_service_liveliness_sub
        self._zenoh_service_liveliness_sub = None
        if liveliness_sub is not None:
            self._run_sync_boundary_step(
                "undeclare zenoh service liveliness watch failed",
                lambda: liveliness_sub.undeclare(),
            )
        liveliness_session = self._zenoh_service_liveliness_session
        self._zenoh_service_liveliness_session = None
        if liveliness_session is not None:
            close_zenoh_session_best_effort(
                liveliness_session,
                context="pystudio-service-liveliness",
                native_close=False,
            )
        if self._remote_state_gateway is not None:
            await self._run_async_boundary_step(
                "stop remote state watcher failed",
                lambda: self._remote_state_gateway.stop(),
            )
        elif self._remote_state_watcher is not None:
            await self._run_async_boundary_step(
                "stop remote state watcher failed",
                lambda: self._remote_state_watcher.stop(),
            )
        self._remote_state_gateway = None
        self._remote_state_watcher = None
        self._watch_targets_cache = None
        async with self._studio_service_lock_for_loop():
            svc = self._svc
            self._svc = None
            if svc is not None:
                await self._run_async_boundary_step("stop studio service failed", lambda: svc.stop())
        self._cache_service_alive(self.studio_service_id, False)
        self._cache_service_active(self.studio_service_id, None)
        self._monitor_center.update_service_status(service_id=self.studio_service_id, ready=False)
        self._studio_service_lock = None
        self._studio_service_lock_loop = None

        await self._run_async_boundary_step("close command gateway failed", lambda: self._command_gateway.close())

        await self._run_async_boundary_step("close rungraph gateway failed", lambda: self._rungraph_gateway.close())

        runtime_transport = self._runtime_transport
        self._runtime_transport = None
        if runtime_transport is not None:
            await self._run_async_boundary_step("close runtime transport failed", lambda: runtime_transport.close())
        self._runtime_transport_lock = None
        self._runtime_transport_lock_loop = None

        token = self._zenoh_singleton_token
        self._zenoh_singleton_token = None
        if token is not None:
            self._run_sync_boundary_step("undeclare zenoh singleton token failed", lambda: token.undeclare())
        session = self._zenoh_singleton_session
        self._zenoh_singleton_session = None
        if session is not None:
            close_zenoh_session_best_effort(
                session,
                context="pystudio-singleton",
                native_close=False,
            )

    def _runtime_transport_lock_for_loop(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        lock = self._runtime_transport_lock
        if lock is None or self._runtime_transport_lock_loop is not loop:
            lock = asyncio.Lock()
            self._runtime_transport_lock = lock
            self._runtime_transport_lock_loop = loop
        return lock

    async def _ensure_runtime_transport(self) -> Any:
        transport = self._runtime_transport
        if transport is not None:
            return transport
        async with self._runtime_transport_lock_for_loop():
            transport = self._runtime_transport
            if transport is not None:
                return transport
            transport = self._build_runtime_transport()
            await transport.connect()
            self._runtime_transport = transport
            return transport

    async def _ensure_requester(self) -> Any | None:
        transport = await self._ensure_runtime_transport()
        return RuntimeTransportRequester(transport=transport)

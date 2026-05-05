from __future__ import annotations

from f8pysdk.codec import dump_json
import asyncio
import concurrent.futures
import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

from qtpy import QtCore

from f8pysdk.bus import BusBackend
from f8pysdk.runtime_transport import RuntimeTransport
from f8pysdk.transport import NatsTransport, NatsTransportConfig
from f8pysdk.specs import F8RuntimeGraph
from f8pysdk.nats_naming import ensure_token, kv_bucket_for_service, new_id
from f8pysdk.zenoh_transport import ZenohTransport, ZenohTransportConfig
from f8pysdk.registry import RuntimeNodeRegistry
from f8pysdk.state import StateWriteError
from f8pystudio.contracts.ui_commands import UiCommand
from f8pystudio.diagnostics.error_reporting import ExceptionLogOnce, report_exception
from f8pystudio.monitoring import MonitorCenter
from f8pystudio.monitoring.service_rows import ServiceMonitorRow, build_service_monitor_rows, collect_known_service_ids
from f8pystudio.nodegraph.runtime_compiler import CompiledRuntimeGraphs
from f8pystudio.studio_specs.registry import SERVICE_CLASS, STUDIO_SERVICE_ID

from .async_runtime import AsyncRuntimeThread
from .command_client import CommandRequest, NatsCommandGateway, RuntimeCommandGateway, RuntimeCommandGatewayConfig
from .json_codec import coerce_json_value
from .managed_service_inventory import collect_managed_service_inventory
from .nats_lifecycle import (
    NatsConnectionManager,
)
from .process_action_scheduler import ServiceProcessActionScheduler
from .process_lifecycle import (
    LocalServiceProcessGateway,
    StartServiceRequest,
    StopServiceRequest,
)
from .process_manager import ServiceProcessManager
from .remote_command_controller import RemoteCommandControllerMixin
from .remote_state_sync import RemoteStateGatewayAdapter
from .remote_state_watcher import RemoteStateWatcher, WatchTarget
from .rungraph_deployer import (
    NatsRungraphGateway,
    RungraphDeployConfig,
    RuntimeRungraphGateway,
)
from .rungraph_deploy_flow import RungraphDeployFlow, pick_compiled
from .runtime_graph_projection import (
    build_local_state_field_index,
    build_remote_watch_targets,
    build_studio_runtime_graph,
)
from .runtime_session_controller import PendingMonitorUpdate, RuntimeSessionControllerMixin
from .service_endpoint_client import (
    request_service_status,
    request_service_terminate,
    request_set_remote_state,
    request_set_service_active,
)
from .service_lifecycle_controller import ServiceLifecycleControllerMixin
from .service_status_store import ServiceStatusStore
from .studio_runtime_flow import (
    apply_remote_state_watches_if_changed,
    install_studio_runtime_graph,
    wait_for_studio_runtime_ready,
)
from .studio_service import PyStudioService
from .deploy_state_controller import DeployStateControllerMixin

logger = logging.getLogger(__name__)
STARTUP_GATE_TIMEOUT_S = 6.0


@dataclass(frozen=True)
class PyStudioServiceBridgeConfig:
    bus_backend: BusBackend = "zenoh"
    nats_url: str = "nats://127.0.0.1:4222"
    zenoh_config_path: str | None = None
    zenoh_connect: tuple[str, ...] = ()
    zenoh_listen: tuple[str, ...] = ()
    zenoh_shm_pool_bytes: int = 256 * 1024 * 1024
    studio_service_id: str = STUDIO_SERVICE_ID


class PyStudioServiceBridge(RuntimeSessionControllerMixin, ServiceLifecycleControllerMixin, DeployStateControllerMixin, RemoteCommandControllerMixin, QtCore.QObject):
    """
    Orchestrate:
    - singleton studio presence (Zenoh liveliness by default; NATS micro for explicit NATS fallback)
    - start service processes
    - deploy per-service rungraphs
    - monitor remote state via Studio-side KV watches (UI reflection)
    """

    # Note: Qt `int` is typically 32-bit; use `object` for ts_ms (ms timestamps exceed 2^31).
    ui_command = QtCore.Signal(object)  # UiCommand
    service_output = QtCore.Signal(str, str)  # serviceId, line
    log = QtCore.Signal(str)
    service_process_state = QtCore.Signal(str, bool)  # serviceId, running
    _remote_command_response = QtCore.Signal(str, object, object)  # reqId, result, err

    def __init__(self, config: PyStudioServiceBridgeConfig, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._cfg = config
        self._async = AsyncRuntimeThread()
        self._proc_mgr = ServiceProcessManager()
        self._process_gateway = LocalServiceProcessGateway(self._proc_mgr)
        self._rungraph_gateway = self._build_rungraph_gateway()
        self._rungraph_deploy_flow = RungraphDeployFlow(
            studio_service_id=self.studio_service_id,
            rungraph_gateway=self._rungraph_gateway,
            emit_log=self._emit_log_line,
        )
        self._command_gateway = self._build_command_gateway()
        self._exception_log_once = ExceptionLogOnce()
        self._nats_connection_manager = NatsConnectionManager(
            nats_url=str(self._cfg.nats_url).strip() or "nats://127.0.0.1:4222",
            emit_log=self._emit_log_line,
            report_exception=self._report_exception,
        )
        self._managed_service_ids: set[str] = set()
        self._managed_service_classes: dict[str, str] = {}  # serviceId -> serviceClass
        self._managed_active: bool = True
        self._service_status_cache: dict[str, tuple[bool | None, float]] = {}  # serviceId -> (active, monotonic_ts)
        self._service_alive_cache: dict[str, tuple[bool, float]] = {}  # serviceId -> (alive, monotonic_ts)
        self._service_status_inflight: set[str] = set()
        self._service_status_req_s: dict[str, float] = {}
        self._status_store = ServiceStatusStore(
            service_status_cache=self._service_status_cache,
            service_alive_cache=self._service_alive_cache,
            service_status_inflight=self._service_status_inflight,
            service_status_req_s=self._service_status_req_s,
        )
        self._last_compiled: CompiledRuntimeGraphs | None = None
        self._local_state_fields_by_node: dict[str, tuple[str, ...]] = {}
        self._shutting_down: bool = False

        self._svc: PyStudioService | None = None
        self._remote_state_watcher: RemoteStateWatcher | None = None
        self._remote_state_gateway: RemoteStateGatewayAdapter | None = None
        self._watch_targets_cache: tuple[WatchTarget, ...] | None = None
        self._monitor_center = MonitorCenter(window_ms=30 * 60 * 1000)
        self._monitor_sub: Any = None
        self._monitor_ui_last_emit_s_by_service: dict[str, float] = {}
        self._monitor_ui_pending_by_service: dict[str, PendingMonitorUpdate] = {}
        self._monitor_ui_flush_task: asyncio.Task[object] | None = None
        self._nc: Any = None
        self._runtime_transport: RuntimeTransport | None = None
        self._zenoh_singleton_session: Any = None
        self._zenoh_singleton_token: Any = None
        self._owned_nats_server_pid: int | None = None
        self._pending_remote_command_cbs: dict[str, Callable[[dict[str, Any] | None, str | None], None]] = {}
        self._async_started: bool = False
        self._startup_future: concurrent.futures.Future[Any] | None = None
        self._startup_preflight_ready = threading.Event()
        self._startup_continue_requested = threading.Event()
        self._startup_preflight_message: str | None = None
        self._process_actions = ServiceProcessActionScheduler(
            owner=self,
            is_service_running=self.is_service_running,
            stop_process_once=self._stop_process_once_local,
            emit_service_process_state=self._emit_service_process_state_safe,
            start_service=lambda sid, svc_class: self.start_service(sid, service_class=svc_class),
            emit_log=self._emit_log_line,
            report_exception=self._report_exception,
        )

        try:
            self._remote_command_response.connect(self._on_remote_command_response)  # type: ignore[attr-defined]
        except Exception as exc:
            self._report_exception("connect remote_command_response failed", exc)

    def _build_rungraph_config(self) -> RungraphDeployConfig:
        return RungraphDeployConfig(
            nats_url=str(self._cfg.nats_url),
            bus_backend=self._cfg.bus_backend,
            client_service_id=self.studio_service_id,
            zenoh_config_path=self._cfg.zenoh_config_path,
            zenoh_connect=self._cfg.zenoh_connect,
            zenoh_listen=self._cfg.zenoh_listen,
            zenoh_shm_pool_bytes=self._cfg.zenoh_shm_pool_bytes,
        )

    def _build_rungraph_gateway(self) -> Any:
        config = self._build_rungraph_config()
        if self._cfg.bus_backend == "nats":
            return NatsRungraphGateway(config)
        return RuntimeRungraphGateway(config)

    def _build_command_gateway(self) -> Any:
        if self._cfg.bus_backend == "nats":
            return NatsCommandGateway(nats_url=str(self._cfg.nats_url))
        return RuntimeCommandGateway(
            RuntimeCommandGatewayConfig(
                bus_backend=self._cfg.bus_backend,
                nats_url=str(self._cfg.nats_url),
                client_service_id=self.studio_service_id,
                zenoh_config_path=self._cfg.zenoh_config_path,
                zenoh_connect=self._cfg.zenoh_connect,
                zenoh_listen=self._cfg.zenoh_listen,
                zenoh_shm_pool_bytes=self._cfg.zenoh_shm_pool_bytes,
            )
        )

    def _build_runtime_transport(self) -> RuntimeTransport:
        if self._cfg.bus_backend == "nats":
            return NatsTransport(
                NatsTransportConfig(
                    url=str(self._cfg.nats_url),
                    kv_bucket=kv_bucket_for_service(self.studio_service_id),
                )
            )
        return ZenohTransport(
            ZenohTransportConfig(
                service_id=self.studio_service_id,
                config_path=self._cfg.zenoh_config_path,
                connect=self._cfg.zenoh_connect,
                listen=self._cfg.zenoh_listen,
                shm_pool_bytes=self._cfg.zenoh_shm_pool_bytes,
            )
        )

    def _emit_log_line(self, line: str) -> None:
        try:
            self.log.emit(str(line))
        except Exception:
            logger.exception("bridge.log.emit failed")

    def _report_exception(self, context: str, exc: BaseException) -> None:
        report_exception(
            self._emit_log_line,
            context=str(context or "").strip(),
            exc=exc,
            log_once=self._exception_log_once,
        )
        try:
            logger.error("%s", str(context or "").strip(), exc_info=exc)
        except Exception:
            # Logging must never crash the bridge.
            pass

    def _emit_remote_command_response_safe(self, req_id: str, result: object, err: object) -> None:
        try:
            self._remote_command_response.emit(str(req_id), result, err)
        except RuntimeError as exc:
            self._report_exception("emit remote command response failed", exc)

    def _emit_service_process_state_safe(self, service_id: str, running: bool) -> None:
        try:
            self.service_process_state.emit(str(service_id), bool(running))
        except RuntimeError as exc:
            self._report_exception(f"emit service process state failed serviceId={service_id}", exc)

    def _submit_async(self, coro: Any, *, context: str) -> bool:
        if self._shutting_down or (not self._async.is_accepting_submissions()):
            if asyncio.iscoroutine(coro):
                coro.close()
            return False
        try:
            self._async.submit(coro)
            return True
        except RuntimeError as exc:
            if asyncio.iscoroutine(coro):
                coro.close()
            if self._shutting_down or (not self._async.is_accepting_submissions()):
                return False
            self._report_exception(context, exc)
            return False
        except Exception as exc:
            self._report_exception(context, exc)
            return False

    def _ensure_async_runtime_started(self) -> None:
        if self._async_started:
            return
        self._async.start()
        self._async_started = True

    def _submit_startup(self) -> concurrent.futures.Future[Any] | None:
        future = self._startup_future
        if future is not None:
            return future
        try:
            future = self._async.submit(self._run_startup_sequence_async())
        except Exception as exc:
            self._report_exception("submit startup wait failed", exc)
            return None
        self._startup_future = future
        return future

    async def _run_startup_sequence_async(self) -> str | None:
        try:
            startup_blocked_message = await self._run_startup_preflight_async()
        except Exception:
            self._startup_preflight_message = None
            self._startup_preflight_ready.set()
            raise
        self._startup_preflight_message = None
        if isinstance(startup_blocked_message, str):
            message = startup_blocked_message.strip()
            if message:
                self._startup_preflight_message = message
        self._startup_preflight_ready.set()
        if self._startup_preflight_message is not None:
            return self._startup_preflight_message
        await asyncio.to_thread(self._startup_continue_requested.wait)
        if self._shutting_down:
            return None
        return await self._start_after_preflight_async()

    def _wait_for_future_message(
        self,
        future: concurrent.futures.Future[Any] | None,
        *,
        timeout_s: float,
        timeout_log_line: str,
        error_context: str,
    ) -> tuple[bool, str | None]:
        if future is None:
            return False, None
        try:
            result = future.result(timeout=float(timeout_s))
        except concurrent.futures.TimeoutError:
            self._emit_log_line(timeout_log_line)
            return False, None
        except Exception as exc:
            self._report_exception(error_context, exc)
            return True, None

        if not isinstance(result, str):
            return True, None
        message = result.strip()
        if not message:
            return True, None
        return True, message

    def _wait_for_startup_preflight_message(self, *, timeout_s: float) -> tuple[bool, str | None]:
        if self._startup_preflight_ready.wait(timeout=float(timeout_s)):
            return True, self._startup_preflight_message
        self._emit_log_line("bridge startup preflight timed out; continuing UI startup")
        return False, None

    def wait_for_startup_preflight(self, *, timeout_s: float = STARTUP_GATE_TIMEOUT_S) -> str | None:
        self._shutting_down = False
        self._ensure_async_runtime_started()
        future = self._submit_startup()
        if future is None:
            return None
        _completed, message = self._wait_for_startup_preflight_message(timeout_s=float(timeout_s))
        return message

    def start_and_wait_for_startup(self, *, timeout_s: float = STARTUP_GATE_TIMEOUT_S) -> str | None:
        self._shutting_down = False
        self._ensure_async_runtime_started()
        future = self._submit_startup()
        if future is None:
            return None
        preflight_completed, preflight_message = self._wait_for_startup_preflight_message(timeout_s=float(timeout_s))
        if preflight_message is not None:
            return preflight_message
        self._startup_continue_requested.set()
        if not preflight_completed:
            return None
        _completed, message = self._wait_for_future_message(
            future,
            timeout_s=float(timeout_s),
            timeout_log_line="bridge startup wait timed out; continuing UI startup",
            error_context="wait for bridge startup failed",
        )
        return message

    @property
    def studio_service_id(self) -> str:
        return ensure_token(self._cfg.studio_service_id, label="studio_service_id")

    @property
    def managed_active(self) -> bool:
        return bool(self._managed_active)

    def export_monitor_report(self) -> dict[str, Any]:
        return self._monitor_center.export_report_json()

    def get_latest_monitor_snapshot(self, service_id: str) -> dict[str, Any] | None:
        snapshot = self._monitor_center.latest_snapshot(service_id=str(service_id))
        if snapshot is None:
            return None
        return dump_json(snapshot, mode="json", by_alias=True)

    def get_monitor_snapshot_stream(self, service_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
        return self._monitor_center.export_snapshot_stream_json(service_id=str(service_id), limit=int(limit))

    def _collect_known_service_ids(self) -> list[str]:
        known_service_ids = collect_known_service_ids(
            managed_service_ids=self._managed_service_ids,
            managed_service_classes=self._managed_service_classes,
            service_alive_cache=self._service_alive_cache,
            service_status_cache=self._service_status_cache,
            latest_snapshot_by_service=self._monitor_center.latest_snapshots(),
            process_service_ids_provider=self._process_gateway.service_ids,
            on_process_ids_error=lambda exc: self._report_exception("list process service ids failed", exc),
        )
        if self.studio_service_id not in known_service_ids:
            known_service_ids.append(self.studio_service_id)
        return known_service_ids

    def list_service_monitor_rows(self) -> list[ServiceMonitorRow]:
        latest_by_service = self._monitor_center.latest_snapshots()
        managed_service_classes = dict(self._managed_service_classes)
        managed_service_classes[self.studio_service_id] = str(SERVICE_CLASS)
        return build_service_monitor_rows(
            service_ids=self._collect_known_service_ids(),
            latest_snapshot_by_service=latest_by_service,
            is_service_running=self.is_service_running,
            get_cached_service_active=self.get_cached_service_active,
            managed_service_classes=managed_service_classes,
            service_alive_cache=self._service_alive_cache,
        )

    def stop(self) -> None:
        self._shutting_down = True
        self._process_actions.cancel_all()
        self._startup_continue_requested.set()
        if self._async_started:
            try:
                fut = self._async.submit(self._stop_async())
                try:
                    fut.result(timeout=2)
                except concurrent.futures.TimeoutError:
                    self._emit_log_line("bridge stop timeout; continue shutdown")
            except Exception as exc:
                self._report_exception("submit stop failed", exc)
            self._async.stop()
            self._async_started = False
        self._startup_future = None
        self._startup_preflight_ready = threading.Event()
        self._startup_continue_requested = threading.Event()
        self._startup_preflight_message = None

        # Best-effort stop all launched processes.
        for sid in list(self._process_gateway.service_ids()):
            try:
                self._process_gateway.stop(StopServiceRequest(service_id=sid))
            except Exception as exc:
                self._report_exception(f"stop service process failed serviceId={sid}", exc)






























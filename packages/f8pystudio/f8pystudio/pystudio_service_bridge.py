from __future__ import annotations

from f8pysdk.msgspec_codec import dump_json
import asyncio
import concurrent.futures
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from qtpy import QtCore

from f8pysdk import F8RuntimeGraph
from f8pysdk.nats_naming import ensure_token, new_id
from f8pysdk.runtime_node_registry import RuntimeNodeRegistry
from f8pysdk.service_bus.state_write import StateWriteError
from .bridge.async_runtime import AsyncRuntimeThread
from .bridge.command_client import CommandRequest, NatsCommandGateway
from .bridge.json_codec import coerce_json_value
from .bridge.managed_service_inventory import collect_managed_service_inventory
from .bridge.nats_lifecycle import (
    NatsConnectionManager,
    ensure_nats_server_owned_pid,
    stop_owned_nats_server,
)
from .bridge.process_action_scheduler import ServiceProcessActionScheduler
from .bridge.runtime_graph_projection import (
    build_local_state_field_index,
    build_remote_watch_targets,
    build_studio_runtime_graph,
)
from .bridge.rungraph_deploy_flow import RungraphDeployFlow, pick_compiled
from .bridge.service_endpoint_client import (
    decode_json_object,
    message_data_bytes,
    request_service_status,
    request_service_terminate,
    request_set_remote_state,
    request_set_service_active,
)
from .bridge.studio_runtime_flow import (
    apply_remote_state_watches_if_changed,
    install_studio_runtime_graph,
    wait_for_studio_runtime_ready,
)
from .bridge.runtime_session_controller import RuntimeSessionControllerMixin
from .bridge.service_lifecycle_controller import ServiceLifecycleControllerMixin
from .bridge.deploy_state_controller import DeployStateControllerMixin
from .bridge.remote_command_controller import RemoteCommandControllerMixin
from .bridge.service_status_store import ServiceStatusStore
from .bridge.process_lifecycle import (
    LocalServiceProcessGateway,
    StartServiceRequest,
    StopServiceRequest,
)
from .bridge.remote_state_sync import RemoteStateGatewayAdapter
from .bridge.rungraph_deployer import (
    NatsRungraphGateway,
    RungraphDeployConfig,
)
from .error_reporting import ExceptionLogOnce, report_exception
from .nodegraph.runtime_compiler import CompiledRuntimeGraphs
from .pystudio_service import PyStudioService, PyStudioServiceConfig
from .service_process_manager import ServiceProcessConfig, ServiceProcessManager
from .pystudio_node_registry import SERVICE_CLASS, STUDIO_SERVICE_ID
from .remote_state_watcher import RemoteStateWatcher, WatchTarget
from .ui_bus import UiCommand
from .monitoring import MonitorCenter
from .monitoring.service_rows import ServiceMonitorRow, build_service_monitor_rows, collect_known_service_ids

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PyStudioServiceBridgeConfig:
    nats_url: str = "nats://127.0.0.1:4222"
    studio_service_id: str = STUDIO_SERVICE_ID


class PyStudioServiceBridge(RuntimeSessionControllerMixin, ServiceLifecycleControllerMixin, DeployStateControllerMixin, RemoteCommandControllerMixin, QtCore.QObject):
    """
    Orchestrate:
    - singleton studio presence (NATS micro ping/info)
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
        self._rungraph_gateway = NatsRungraphGateway(RungraphDeployConfig(nats_url=self._cfg.nats_url))
        self._rungraph_deploy_flow = RungraphDeployFlow(
            studio_service_id=self.studio_service_id,
            rungraph_gateway=self._rungraph_gateway,
            emit_log=self._emit_log_line,
        )
        self._command_gateway = NatsCommandGateway(nats_url=self._cfg.nats_url)
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
        self._nc: Any = None
        self._owned_nats_server_pid: int | None = None
        self._pending_remote_command_cbs: dict[str, Callable[[dict[str, Any] | None, str | None], None]] = {}
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

    @staticmethod
    def _message_data_bytes(message: Any) -> bytes:
        return message_data_bytes(message)

    @staticmethod
    def _decode_json_object(raw: bytes) -> dict[str, Any] | None:
        return decode_json_object(raw)


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

    def _collect_known_service_ids(self) -> list[str]:
        return collect_known_service_ids(
            managed_service_ids=self._managed_service_ids,
            managed_service_classes=self._managed_service_classes,
            service_alive_cache=self._service_alive_cache,
            service_status_cache=self._service_status_cache,
            latest_snapshot_by_service=self._monitor_center.latest_snapshots(),
            process_service_ids_provider=self._process_gateway.service_ids,
            on_process_ids_error=lambda exc: self._report_exception("list process service ids failed", exc),
        )

    def list_service_monitor_rows(self) -> list[ServiceMonitorRow]:
        latest_by_service = self._monitor_center.latest_snapshots()
        return build_service_monitor_rows(
            service_ids=self._collect_known_service_ids(),
            latest_snapshot_by_service=latest_by_service,
            is_service_running=self.is_service_running,
            get_cached_service_active=self.get_cached_service_active,
            managed_service_classes=self._managed_service_classes,
            service_alive_cache=self._service_alive_cache,
        )

    @QtCore.Slot(bool)

    def start(self) -> None:
        self._shutting_down = False
        self._async.start()
        self._submit_async(self._start_async(), context="submit start failed")

    def stop(self) -> None:
        self._shutting_down = True
        self._process_actions.cancel_all()
        try:
            fut = self._async.submit(self._stop_async())
            try:
                fut.result(timeout=2)
            except concurrent.futures.TimeoutError:
                self._emit_log_line("bridge stop timeout; continue shutdown")
        except Exception as exc:
            self._report_exception("submit stop failed", exc)
        self._async.stop()

        # Best-effort stop all launched processes.
        for sid in list(self._process_gateway.service_ids()):
            try:
                self._process_gateway.stop(StopServiceRequest(service_id=sid))
            except Exception as exc:
                self._report_exception(f"stop service process failed serviceId={sid}", exc)





































from __future__ import annotations

import json
import logging
import os
import secrets
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from qtpy import QtCore

from f8pystudio.nodegraph.runtime_compiler import compile_runtime_graphs_from_studio

from .client import wait_for_connection_file
from .control_protocol import AutomationConnectionInfo
from .domain import decode_graph_patch
from .graph_adapter import StudioGraphAutomationAdapter
from .local_server import LocalAutomationServer
from .observation_store import RuntimeObservationStore
from .paths import automation_dir, default_port_file, default_token_file

logger = logging.getLogger(__name__)
_HOST_METHOD_ERRORS = (Exception,)
_FILE_WRITE_ERRORS = (OSError, RuntimeError, TypeError, ValueError)
_SERVER_THREAD_METHODS = frozenset(
    {
        "runtime.readState",
        "runtime.watchState",
        "runtime.samplePort",
    }
)


class StudioAutomationHost(QtCore.QObject):
    _request = QtCore.Signal(str, object, object)

    def __init__(
        self,
        *,
        main_window: Any,
        studio_graph: Any,
        bridge: Any,
        token_file: str | Path | None = None,
        port_file: str | Path | None = None,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._main_window = main_window
        self._graph = studio_graph
        self._bridge = bridge
        self._graph_adapter = StudioGraphAutomationAdapter(studio_graph)
        self._observations = RuntimeObservationStore()
        self._token_file = Path(token_file).expanduser() if token_file is not None else default_token_file()
        self._port_file = Path(port_file).expanduser() if port_file is not None else default_port_file()
        self._token = ""
        self._server: LocalAutomationServer | None = None
        self._request.connect(self._handle_request_on_qt_thread, QtCore.Qt.ConnectionType.BlockingQueuedConnection)

    @property
    def connection_info(self) -> AutomationConnectionInfo | None:
        server = self._server
        if server is None:
            return None
        return AutomationConnectionInfo(
            pid=os.getpid(),
            host=server.host,
            port=server.port,
            token_file=str(self._token_file),
            studio_service_id=str(self._bridge.studio_service_id),
            created_at=int(time.time()),
        )

    def start(self) -> AutomationConnectionInfo:
        if self._server is not None:
            info = self.connection_info
            if info is None:
                raise RuntimeError("automation server is started but connection info is unavailable")
            return info
        self._token = secrets.token_urlsafe(32)
        self._write_private_text(self._token_file, self._token + "\n")
        server = LocalAutomationServer(token=self._token, request_handler=self._handle_request_from_server_thread)
        server.start()
        self._server = server
        info = self.connection_info
        if info is None:
            raise RuntimeError("failed to create automation connection info")
        self._write_private_json(self._port_file, info.to_dict())
        logger.info("PyStudio automation listening on %s:%s", info.host, info.port)
        return info

    def stop(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.stop()

    def record_runtime_state(self, *, service_id: str, node_id: str, field: str, value: Any, ts_ms: int) -> None:
        self._observations.put_state(
            service_id=service_id,
            node_id=node_id,
            field=field,
            value=value,
            ts_ms=int(ts_ms),
        )

    def _handle_request_from_server_thread(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method in _SERVER_THREAD_METHODS:
            try:
                return self._dispatch_server_thread(str(method), dict(params))
            except _HOST_METHOD_ERRORS as exc:
                logger.exception("automation server-thread method failed method=%s", method)
                return {
                    "ok": False,
                    "error": {
                        "code": "method_failed",
                        "message": f"{type(exc).__name__}: {exc}",
                        "details": {"method": str(method)},
                    },
                    "result": {},
                }
        response_box: dict[str, Any] = {}
        self._request.emit(str(method), dict(params), response_box)
        return dict(response_box)

    @QtCore.Slot(str, object, object)
    def _handle_request_on_qt_thread(self, method: str, params: object, response_box: object) -> None:
        if not isinstance(response_box, dict):
            return
        try:
            params_dict = params if isinstance(params, dict) else {}
            response_box.update(self._dispatch(str(method), dict(params_dict)))
        except _HOST_METHOD_ERRORS as exc:
            logger.exception("automation method failed method=%s", method)
            response_box.update(
                {
                    "ok": False,
                    "error": {
                        "code": "method_failed",
                        "message": f"{type(exc).__name__}: {exc}",
                        "details": {"method": str(method)},
                    },
                    "result": {},
                }
            )

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "studio.status":
            return self._status()
        if method == "graph.snapshot":
            return {"snapshot": self._graph_adapter.snapshot().to_dict()}
        if method == "graph.session":
            return {"session": self._graph_adapter.session_payload()}
        if method == "graph.catalog":
            return self._graph_adapter.node_catalog()
        if method == "graph.previewPatch":
            patch = decode_graph_patch(params.get("patch"))
            return {"preview": self._graph_adapter.preview_patch(patch).to_dict()}
        if method == "graph.applyPatch":
            if _requires_confirm(params) and not bool(params.get("confirm")):
                raise ValueError("graph.applyPatch with destructive ops requires confirm=true")
            patch = decode_graph_patch(params.get("patch"))
            preview = self._graph_adapter.apply_patch(patch)
            self._schedule_studio_runtime_sync()
            return {"preview": preview.to_dict(), "snapshot": self._graph_adapter.snapshot().to_dict()}
        if method == "graph.compile":
            return {"compile": self._graph_adapter.compile_graph()}
        if method == "runtime.deploy":
            if not bool(params.get("confirm")):
                raise ValueError("runtime.deploy requires confirm=true")
            return self._runtime_deploy(params)
        if method == "runtime.serviceStatus":
            return {"service": self._runtime_service_status(str(params.get("serviceId") or ""))}
        if method == "runtime.writeState":
            return self._runtime_write_state(params)
        if method == "runtime.readMonitor":
            return {"monitor": self._runtime_read_monitor(params)}
        if method == "runtime.invokeCommand":
            return self._runtime_invoke_command(params)
        raise ValueError(f"unsupported automation method: {method}")

    def _dispatch_server_thread(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "runtime.readState":
            return {"state": self._runtime_read_state(params)}
        if method == "runtime.watchState":
            return {"state": self._runtime_watch_state(params)}
        if method == "runtime.samplePort":
            return {"samples": self._runtime_sample_port(params)}
        raise ValueError(f"unsupported server-thread automation method: {method}")

    def _status(self) -> dict[str, Any]:
        info = self.connection_info
        return {
            "pid": os.getpid(),
            "automation": info.to_dict() if info is not None else None,
            "graphRevision": self._graph_adapter.revision(),
            "studioServiceId": str(self._bridge.studio_service_id),
        }

    def _runtime_deploy(self, params: dict[str, Any]) -> dict[str, Any]:
        compiled = compile_runtime_graphs_from_studio(self._graph)
        wait = bool(params.get("wait", True))
        timeout_s = float(params.get("timeoutS") or 20.0)
        if wait:
            deploy_result = self._bridge.deploy_and_wait(compiled, timeout_s=timeout_s)
        else:
            self._bridge.deploy(compiled)
            deploy_result = {"submitted": True, "completed": False, "error": ""}
        return {
            "deploy": deploy_result,
            "compileWarnings": list(compiled.warnings or ()),
            "compile": self._graph_adapter.compile_graph(),
        }

    def _runtime_service_status(self, service_id: str) -> dict[str, Any]:
        sid = str(service_id or "").strip() or str(self._bridge.studio_service_id)
        latest_monitor = self._bridge.get_latest_monitor_snapshot(sid)
        return {
            "serviceId": sid,
            "serviceClass": str(self._bridge.get_service_class(sid)),
            "running": bool(self._bridge.is_service_running(sid)),
            "active": self._bridge.get_cached_service_active(sid),
            "latestMonitor": latest_monitor,
        }

    def _runtime_read_state(self, params: dict[str, Any]) -> dict[str, Any] | None:
        value = self._observations.get_state(
            service_id=str(params.get("serviceId") or ""),
            node_id=str(params.get("nodeId") or ""),
            field=str(params.get("field") or ""),
        )
        return _stored_state_to_dict(value)

    def _runtime_watch_state(self, params: dict[str, Any]) -> dict[str, Any] | None:
        value = self._observations.wait_state(
            service_id=str(params.get("serviceId") or ""),
            node_id=str(params.get("nodeId") or ""),
            field=str(params.get("field") or ""),
            after_ts_ms=_optional_int_param(params, "afterTsMs"),
            timeout_s=float(params.get("timeoutS") or (float(params.get("durationMs") or 1000) / 1000.0)),
        )
        return _stored_state_to_dict(value)

    def _runtime_write_state(self, params: dict[str, Any]) -> dict[str, Any]:
        service_id = str(params.get("serviceId") or "").strip()
        node_id = str(params.get("nodeId") or "").strip()
        field = str(params.get("field") or "").strip()
        if "value" not in params:
            raise ValueError("runtime.writeState requires value")
        if not service_id or not node_id or not field:
            raise ValueError("runtime.writeState requires serviceId, nodeId, and field")
        return {
            "state": self._bridge.set_remote_state_and_wait(
                service_id,
                node_id,
                field,
                params.get("value"),
                timeout_s=float(params.get("timeoutS") or 2.0),
            )
        }

    def _runtime_read_monitor(self, params: dict[str, Any]) -> dict[str, Any]:
        service_id = str(params.get("serviceId") or "").strip()
        limit = int(params.get("limit") or 500)
        if service_id:
            return {
                "latest": self._bridge.get_latest_monitor_snapshot(service_id),
                "stream": self._bridge.get_monitor_snapshot_stream(service_id, limit=limit),
            }
        return {"report": self._bridge.export_monitor_report()}

    def _runtime_sample_port(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        service_id = str(params.get("serviceId") or "").strip()
        node_id = str(params.get("nodeId") or "").strip()
        port = str(params.get("port") or "").strip()
        limit = int(params.get("limit") or 1)
        timeout_s = float(params.get("timeoutS") or 2.0)
        if bool(params.get("subscribe", True)):
            result = self._bridge.sample_data_port_and_wait(
                service_id,
                node_id,
                port,
                limit=limit,
                timeout_s=timeout_s,
                include_value=bool(params.get("includeValue", True)),
                max_value_bytes=int(params.get("maxValueBytes") or 65536),
            )
            samples = list(result.get("samples") if isinstance(result.get("samples"), list) else [])
            for sample in samples:
                if isinstance(sample, dict):
                    self._observations.put_port_sample(
                        service_id=service_id,
                        node_id=node_id,
                        port=port,
                        sample=sample,
                    )
            if samples:
                return [dict(item) for item in samples if isinstance(item, dict)]
        return self._observations.wait_port_samples(
            service_id=service_id,
            node_id=node_id,
            port=port,
            min_count=int(params.get("minCount") or 1),
            limit=limit,
            after_observed_at_ms=_optional_int_param(params, "afterObservedAtMs"),
            timeout_s=timeout_s,
        )

    def _runtime_invoke_command(self, params: dict[str, Any]) -> dict[str, Any]:
        service_id = str(params.get("serviceId") or "").strip()
        call = str(params.get("call") or params.get("name") or "").strip()
        if not service_id or not call:
            raise ValueError("runtime.invokeCommand requires serviceId and call")
        return {
            "command": self._bridge.invoke_remote_command_and_wait(
                service_id,
                call,
                params.get("args"),
                timeout_s=float(params.get("timeoutS") or 2.0),
            )
        }

    def _schedule_studio_runtime_sync(self) -> None:
        try:
            self._main_window._schedule_studio_runtime_sync()
        except (AttributeError, RuntimeError, TypeError):
            logger.exception("failed to schedule studio runtime sync after automation patch")

    @staticmethod
    def _write_private_text(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except _FILE_WRITE_ERRORS:
            logger.exception("failed to chmod private automation file path=%s", path)

    @classmethod
    def _write_private_json(cls, path: Path, payload: dict[str, Any]) -> None:
        cls._write_private_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _requires_confirm(params: dict[str, Any]) -> bool:
    patch = params.get("patch")
    if not isinstance(patch, dict):
        return True
    ops = patch.get("ops")
    if not isinstance(ops, list):
        return True
    for op in ops:
        if not isinstance(op, dict):
            return True
        if str(op.get("op") or "") == "deleteNode":
            return True
    return False


def _stored_state_to_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "serviceId": value.service_id,
        "nodeId": value.node_id,
        "field": value.field,
        "value": value.value,
        "tsMs": value.ts_ms,
    }


def _optional_int_param(params: dict[str, Any], key: str) -> int | None:
    value = params.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def launch_pystudio_with_automation(
    *,
    port_file: str | Path | None = None,
    token_file: str | Path | None = None,
    timeout_s: float = 20.0,
) -> AutomationConnectionInfo:
    resolved_port_file = Path(port_file).expanduser() if port_file is not None else default_port_file()
    previous_mtime_ns: int | None = None
    if resolved_port_file.exists():
        previous_mtime_ns = int(resolved_port_file.stat().st_mtime_ns)
    launch_started_at = int(time.time())
    args = [
        sys.executable,
        "-m",
        "f8pystudio.main",
        "--automation",
        "--automation-port-file",
        str(resolved_port_file),
    ]
    if token_file is not None:
        args.extend(["--automation-token-file", str(Path(token_file).expanduser())])
    subprocess.Popen(args, start_new_session=True)
    return wait_for_connection_file(
        resolved_port_file,
        timeout_s=float(timeout_s),
        min_created_at=launch_started_at,
        previous_mtime_ns=previous_mtime_ns,
    )

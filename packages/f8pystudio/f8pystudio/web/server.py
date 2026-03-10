from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import msgspec
import websockets
from websockets.server import WebSocketServerProtocol

from f8pysdk.msgspec_codec import dump_json
from f8pysdk.service_bus.codec import decode_obj
from f8pysdk.service_runtime_tools.catalog import ServiceCatalog
from f8pysdk.service_runtime_tools.discovery import load_discovery_into_catalog

from ..bridge.nats_lifecycle import NatsConnectionManager, ensure_nats_server_owned_pid, stop_owned_nats_server
from ..bridge.runtime_graph_projection import build_remote_watch_targets
from ..constants import STUDIO_SERVICE_ID
from ..deploy import deploy_to_service
from ..error_reporting import ExceptionLogOnce, fingerprint_exception
from ..nodegraph.session import last_session_path
from ..pystudio_program import PyStudioProgram
from ..pystudio_service import PyStudioService, PyStudioServiceConfig
from ..remote_state_watcher import RemoteStateWatcher
from ..variants.variant_models import F8NodeVariantLibraryFile, F8NodeVariantRecord
from ..variants.variant_repository import (
    delete_variant,
    export_to_json,
    import_from_json,
    list_variants_for_base,
    load_library,
    upsert_variant,
)
from .compiler_doc import compile_runtime_graphs_from_doc, compiled_runtime_graphs_to_json
from .connection_validate import ConnectionEndpoint, validate_connection
from .graph_doc import (
    F8STUDIO_GRAPH_SCHEMA_VERSION,
    GraphDocParseResult,
    dump_graph_doc,
    load_graph_doc,
    normalize_graph_doc,
)
from .session_codec import F8STUDIO_SESSION_SCHEMA_VERSION, export_nodegraphqt_session, import_nodegraphqt_session

logger = logging.getLogger(__name__)


def _json_response(handler: BaseHTTPRequestHandler, *, status: int, payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(int(status))
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()
    handler.wfile.write(raw)


def _read_json_body(handler: BaseHTTPRequestHandler) -> Any:
    try:
        length = int(handler.headers.get("Content-Length") or "0")
    except ValueError:
        length = 0
    if length <= 0:
        return None
    raw = handler.rfile.read(length)
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def _error(handler: BaseHTTPRequestHandler, *, status: int, message: str) -> None:
    _json_response(handler, status=status, payload={"ok": False, "error": {"message": str(message)}})


@dataclass(frozen=True)
class ServerConfig:
    http_host: str = "127.0.0.1"
    http_port: int = 8765
    ws_host: str = "127.0.0.1"
    ws_port: int = 8766
    nats_url: str = "nats://127.0.0.1:4222"
    studio_service_id: str = STUDIO_SERVICE_ID


class _EventHub:
    def __init__(self) -> None:
        self._clients: set[WebSocketServerProtocol] = set()
        self._lock = asyncio.Lock()

    async def add(self, ws: WebSocketServerProtocol) -> None:
        async with self._lock:
            self._clients.add(ws)

    async def remove(self, ws: WebSocketServerProtocol) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast_json(self, obj: dict[str, Any]) -> None:
        raw = json.dumps(obj, ensure_ascii=False)
        async with self._lock:
            clients = list(self._clients)
        if not clients:
            return
        await asyncio.gather(*(self._safe_send_text(ws, raw) for ws in clients), return_exceptions=True)

    @staticmethod
    async def _safe_send_text(ws: WebSocketServerProtocol, raw: str) -> None:
        try:
            await ws.send(raw)
        except Exception:
            return


class _RuntimeManager:
    def __init__(self, *, cfg: ServerConfig, hub: _EventHub) -> None:
        self._cfg = cfg
        self._hub = hub
        self._svc: PyStudioService | None = None
        self._remote_state_watcher: RemoteStateWatcher | None = None
        self._nc: Any | None = None
        self._monitor_sub: Any | None = None
        self._owned_nats_pid: int | None = None
        self._blocked_by_singleton_guard = False
        self._lock = asyncio.Lock()
        self._log_once = ExceptionLogOnce()
        self._nats = NatsConnectionManager(
            nats_url=str(cfg.nats_url),
            emit_log=lambda line: logger.info("%s", str(line)),
            report_exception=lambda context, exc: logger.exception("%s", context, exc_info=exc),
        )

    def status(self) -> dict[str, Any]:
        running = self._svc is not None and self._svc.runtime is not None
        return {
            "running": bool(running),
            "blocked": bool(self._blocked_by_singleton_guard),
            "studioServiceId": str(self._cfg.studio_service_id),
            "natsUrl": str(self._cfg.nats_url),
        }

    async def start(self) -> None:
        async with self._lock:
            if self._svc is not None and self._svc.runtime is not None:
                return
            self._blocked_by_singleton_guard = False

            owned_pid = await ensure_nats_server_owned_pid(
                str(self._cfg.nats_url),
                emit_log=lambda line: logger.info("%s", str(line)),
                report_exception=lambda context, exc: logger.exception("%s", context, exc_info=exc),
            )
            if owned_pid is not None:
                self._owned_nats_pid = int(owned_pid)

            # Singleton guard (best-effort).
            self._nc = await self._nats.connect(context="connect nats for singleton guard failed")
            guard = await self._nats.singleton_guard(self._nc, studio_service_id=str(self._cfg.studio_service_id))
            self._nc = guard.connection
            if not bool(guard.should_start):
                self._blocked_by_singleton_guard = True
                await self._hub.broadcast_json({"type": "runtime.status", "payload": self.status()})
                return

            async def _on_ui_command(cmd: Any) -> None:
                try:
                    payload = dump_json(cmd, mode="json", by_alias=True)
                except Exception as exc:
                    await self._report_exception_async("ui_command.dump_json failed", exc)
                    return
                if not isinstance(payload, dict):
                    return
                await self._hub.broadcast_json({"type": "ui_command", "payload": payload})

            cfg = PyStudioServiceConfig(
                nats_url=str(self._cfg.nats_url),
                studio_service_id=str(self._cfg.studio_service_id),
            )
            svc = PyStudioService(cfg)
            try:
                await svc.start(on_ui_command=lambda cmd: asyncio.create_task(_on_ui_command(cmd)))
            except Exception as exc:
                await self._report_exception_async("pystudio runtime start failed", exc)
                raise
            self._svc = svc

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
                    await self._hub.broadcast_json(
                        {
                            "type": "state.update",
                            "payload": {
                                "serviceId": str(service_id),
                                "nodeId": str(node_id),
                                "field": str(field),
                                "value": value,
                                "tsMs": int(ts_ms),
                            },
                        }
                    )

                self._remote_state_watcher = RemoteStateWatcher(
                    nats_url=str(self._cfg.nats_url),
                    studio_service_id=str(self._cfg.studio_service_id),
                    on_state=_on_state,
                )
                try:
                    await self._remote_state_watcher.start()
                except Exception as exc:
                    await self._report_exception_async("remote state watcher start failed", exc)
                    self._remote_state_watcher = None

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
                    value = envelope.get("value") if isinstance(envelope, dict) else None
                    if not isinstance(value, dict):
                        return
                    await self._hub.broadcast_json({"type": "monitor.update", "payload": dict(value)})

                try:
                    self._monitor_sub = await self._nc.subscribe("svc.*.nodes.*.data.monitor", cb=_on_monitor_msg)
                except Exception as exc:
                    await self._report_exception_async("subscribe monitor stream failed", exc)

            await self._hub.broadcast_json({"type": "runtime.status", "payload": self.status()})

    async def stop(self) -> None:
        async with self._lock:
            svc = self._svc
            self._svc = None
            self._blocked_by_singleton_guard = False

            if self._monitor_sub is not None:
                try:
                    await self._monitor_sub.unsubscribe()
                except Exception as exc:
                    await self._report_exception_async("unsubscribe monitor stream failed", exc)
            self._monitor_sub = None

            if self._remote_state_watcher is not None:
                try:
                    await self._remote_state_watcher.stop()
                except Exception as exc:
                    await self._report_exception_async("stop remote state watcher failed", exc)
            self._remote_state_watcher = None

            if svc is not None:
                try:
                    await svc.stop()
                except Exception as exc:
                    await self._report_exception_async("pystudio runtime stop failed", exc)

            await self._close_nats_and_owned_server()
            await self._hub.broadcast_json({"type": "runtime.status", "payload": self.status()})

    async def _close_nats_and_owned_server(self) -> None:
        nc = self._nc
        self._nc = None
        try:
            await self._nats.close(nc, context="close nats connection failed")
        except Exception:
            pass
        owned = self._owned_nats_pid
        self._owned_nats_pid = None
        if owned is not None:
            await stop_owned_nats_server(
                int(owned),
                emit_log=lambda line: logger.info("%s", str(line)),
                report_exception=lambda context, exc: logger.exception("%s", context, exc_info=exc),
            )

    async def apply_watch_targets(self, targets: list[Any]) -> None:
        watcher = self._remote_state_watcher
        if watcher is None:
            return
        try:
            await watcher.apply_targets(list(targets))
        except Exception as exc:
            await self._report_exception_async("apply remote watch targets failed", exc)

    async def _report_exception_async(self, context: str, exc: BaseException) -> None:
        fp = fingerprint_exception(context=str(context), exc=exc)
        if not self._log_once.should_log(fp):
            return
        logger.exception("%s", context, exc_info=exc)
        await self._hub.broadcast_json(
            {
                "type": "log",
                "payload": {
                    "level": "ERROR",
                    "context": str(context),
                    "excType": type(exc).__name__,
                    "message": str(exc),
                },
            }
        )


class _VizSession:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[object]] = {}
        self._closed = False

    async def close(self) -> None:
        self._closed = True
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for t in tasks:
            try:
                t.cancel()
            except Exception:
                pass
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def set_task(self, sub_id: str, task: asyncio.Task[object]) -> None:
        if self._closed:
            try:
                task.cancel()
            except Exception:
                pass
            return
        self._tasks[str(sub_id)] = task

    def cancel(self, sub_id: str) -> None:
        t = self._tasks.pop(str(sub_id), None)
        if t is None:
            return
        try:
            t.cancel()
        except Exception:
            return


class PyStudioWebBackend:
    def __init__(self, *, cfg: ServerConfig) -> None:
        self._cfg = cfg
        self._hub = _EventHub()
        self._runtime = _RuntimeManager(cfg=cfg, hub=self._hub)
        self._http: ThreadingHTTPServer | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._catalog_loaded = False

    def _ensure_catalog_loaded(self) -> None:
        if self._catalog_loaded:
            return
        catalog = ServiceCatalog.instance()
        # Inject PyStudio builtins (Qt-free) + discovery catalog.
        load_discovery_into_catalog(
            catalog=catalog,
            builtin_injectors=(PyStudioProgram._inject_builtin_pystudio_specs,),
        )
        self._catalog_loaded = True

    def serve_forever(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop

        # HTTP runs in a separate thread (stdlib server is blocking).
        self._http = self._start_http_server()

        async def _main() -> None:
            async with websockets.serve(self._ws_router, self._cfg.ws_host, int(self._cfg.ws_port)):
                await self._hub.broadcast_json({"type": "server.ready", "payload": {"wsPort": int(self._cfg.ws_port)}})
                await asyncio.Future()

        try:
            loop.run_until_complete(_main())
        finally:
            try:
                if self._http is not None:
                    self._http.shutdown()
            except Exception:
                pass
            try:
                loop.run_until_complete(self._runtime.stop())
            except Exception:
                pass
            loop.close()

    def _start_http_server(self) -> ThreadingHTTPServer:
        backend = self

        class Handler(BaseHTTPRequestHandler):
            def do_OPTIONS(self) -> None:  # noqa: N802
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()

            def do_GET(self) -> None:  # noqa: N802
                backend._handle_http(self)

            def do_POST(self) -> None:  # noqa: N802
                backend._handle_http(self)

            def do_PUT(self) -> None:  # noqa: N802
                backend._handle_http(self)

            def do_DELETE(self) -> None:  # noqa: N802
                backend._handle_http(self)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                # Suppress stdlib noisy logging; backend uses structured logs.
                _ = (format, args)
                return

        httpd = ThreadingHTTPServer((self._cfg.http_host, int(self._cfg.http_port)), Handler)
        t = threading.Thread(target=httpd.serve_forever, name="pystudio-web-http", daemon=True)
        t.start()
        logger.info("HTTP listening on http://%s:%s", self._cfg.http_host, self._cfg.http_port)
        return httpd

    def _handle_http(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        try:
            if path == "/api/v1/file/open" and handler.command == "POST":
                body = _read_json_body(handler) or {}
                if not isinstance(body, dict):
                    raise ValueError("payload must be a JSON object")
                in_path = str(body.get("path") or "").strip()
                if not in_path:
                    raise ValueError("missing path")
                p = Path(in_path).expanduser()
                if not p.is_file():
                    raise ValueError(f"file not found: {p}")
                payload = json.loads(p.read_text(encoding="utf-8"))
                _json_response(handler, status=200, payload={"ok": True, "path": str(p), "payload": payload})
                return

            if path == "/api/v1/file/save" and handler.command == "POST":
                body = _read_json_body(handler) or {}
                if not isinstance(body, dict):
                    raise ValueError("payload must be a JSON object")
                out_path = str(body.get("path") or "").strip()
                payload = body.get("payload")
                if not out_path:
                    raise ValueError("missing path")
                if not isinstance(payload, dict):
                    raise ValueError("payload.payload must be a JSON object")
                p = Path(out_path).expanduser()
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                _json_response(handler, status=200, payload={"ok": True, "path": str(p)})
                return

            if path == "/api/v1/node-types" and handler.command == "GET":
                self._ensure_catalog_loaded()
                catalog = ServiceCatalog.instance()
                services = [dump_json(s, mode="json", by_alias=True) for s in catalog.services.all()]
                operators = [dump_json(o, mode="json", by_alias=True) for o in catalog.operators.all()]
                _json_response(handler, status=200, payload={"ok": True, "services": services, "operators": operators})
                return

            if path == "/api/v1/variants" and handler.command == "GET":
                lib = load_library()
                _json_response(handler, status=200, payload={"ok": True, "library": dump_json(lib, mode="json")})
                return

            if path == "/api/v1/variants" and handler.command == "POST":
                body = _read_json_body(handler)
                record = msgspec.convert(body, type=F8NodeVariantRecord)
                out = upsert_variant(record)
                _json_response(handler, status=200, payload={"ok": True, "record": dump_json(out, mode="json")})
                return

            if path.startswith("/api/v1/variants/") and handler.command == "DELETE":
                vid = path.split("/")[-1]
                changed = delete_variant(vid)
                _json_response(handler, status=200, payload={"ok": True, "deleted": bool(changed)})
                return

            if path == "/api/v1/variants/import" and handler.command == "POST":
                body = _read_json_body(handler) or {}
                if not isinstance(body, dict):
                    raise ValueError("payload must be a JSON object")
                in_path = str(body.get("path") or "").strip()
                mode = str(body.get("mode") or "merge").strip()
                lib = import_from_json(in_path, mode="replace" if mode == "replace" else "merge")
                _json_response(handler, status=200, payload={"ok": True, "library": dump_json(lib, mode="json")})
                return

            if path == "/api/v1/variants/export" and handler.command == "POST":
                body = _read_json_body(handler) or {}
                if not isinstance(body, dict):
                    raise ValueError("payload must be a JSON object")
                out_path = export_to_json(str(body.get("path") or "").strip())
                _json_response(handler, status=200, payload={"ok": True, "path": str(out_path)})
                return

            if path == "/api/v1/graph/normalize" and handler.command == "POST":
                body = _read_json_body(handler)
                result = self._normalize_graph_payload(body)
                _json_response(
                    handler,
                    status=200,
                    payload={"ok": True, "doc": dump_graph_doc(result.doc), "warnings": list(result.warnings)},
                )
                return

            if path == "/api/v1/graph/compile" and handler.command == "POST":
                body = _read_json_body(handler)
                parsed_doc = self._normalize_graph_payload(body).doc
                compiled = compile_runtime_graphs_from_doc(parsed_doc)
                _json_response(handler, status=200, payload={"ok": True, "compiled": compiled_runtime_graphs_to_json(compiled)})
                return

            if path == "/api/v1/graph/validate-connection" and handler.command == "POST":
                body = _read_json_body(handler) or {}
                if not isinstance(body, dict):
                    raise ValueError("payload must be a JSON object")
                doc_payload = body.get("doc")
                parsed_doc = self._normalize_graph_payload(doc_payload).doc
                from_raw = body.get("from")
                to_raw = body.get("to")
                kind = str(body.get("kind") or "").strip().lower()
                if not isinstance(from_raw, dict) or not isinstance(to_raw, dict):
                    raise ValueError("missing from/to objects")
                allowed, reason = validate_connection(
                    parsed_doc,
                    kind=kind,
                    from_ep=ConnectionEndpoint(
                        nodeId=str(from_raw.get("nodeId") or "").strip(),
                        port=str(from_raw.get("port") or "").strip(),
                    ),
                    to_ep=ConnectionEndpoint(
                        nodeId=str(to_raw.get("nodeId") or "").strip(),
                        port=str(to_raw.get("port") or "").strip(),
                    ),
                )
                _json_response(handler, status=200, payload={"ok": True, "allowed": bool(allowed), "reason": reason})
                return

            if path == "/api/v1/runtime/status" and handler.command == "GET":
                _json_response(handler, status=200, payload={"ok": True, "status": self._runtime.status()})
                return

            if path == "/api/v1/runtime/start" and handler.command == "POST":
                loop = self._loop
                if loop is None:
                    raise RuntimeError("server loop is not available")
                fut = asyncio.run_coroutine_threadsafe(self._runtime.start(), loop)
                fut.result(timeout=10.0)
                _json_response(handler, status=200, payload={"ok": True, "status": self._runtime.status()})
                return

            if path == "/api/v1/runtime/stop" and handler.command == "POST":
                loop = self._loop
                if loop is None:
                    raise RuntimeError("server loop is not available")
                fut = asyncio.run_coroutine_threadsafe(self._runtime.stop(), loop)
                fut.result(timeout=10.0)
                _json_response(handler, status=200, payload={"ok": True, "status": self._runtime.status()})
                return

            if path == "/api/v1/runtime/deploy" and handler.command == "POST":
                body = _read_json_body(handler) or {}
                if not isinstance(body, dict):
                    raise ValueError("payload must be a JSON object")
                service_id = str(body.get("serviceId") or "").strip()
                nats_url = str(body.get("natsUrl") or self._cfg.nats_url).strip()
                doc_payload = body.get("doc")
                parsed_doc = self._normalize_graph_payload(doc_payload).doc
                compiled = compile_runtime_graphs_from_doc(parsed_doc)
                graph = compiled.global_graph
                loop = self._loop
                if loop is None:
                    raise RuntimeError("server loop is not available")
                # Update remote state watch set (best-effort) so UI can mirror live runtime state.
                try:
                    targets = list(build_remote_watch_targets(compiled))
                except Exception:
                    targets = []
                if targets:
                    try:
                        wfut = asyncio.run_coroutine_threadsafe(self._runtime.apply_watch_targets(targets), loop)
                        wfut.result(timeout=6.0)
                    except Exception:
                        pass
                fut = asyncio.run_coroutine_threadsafe(
                    deploy_to_service(service_id=service_id, nats_url=nats_url, graph=graph),
                    loop,
                )
                fut.result(timeout=10.0)
                _json_response(handler, status=200, payload={"ok": True})
                return

            if path == "/api/v1/session/last" and handler.command == "GET":
                p = last_session_path()
                if not p.is_file():
                    _json_response(handler, status=200, payload={"ok": True, "exists": False})
                    return
                data = json.loads(p.read_text(encoding="utf-8"))
                _json_response(handler, status=200, payload={"ok": True, "exists": True, "payload": data})
                return

            if path == "/api/v1/session/last" and handler.command == "PUT":
                body = _read_json_body(handler)
                if not isinstance(body, dict):
                    raise ValueError("payload must be a JSON object")
                p = last_session_path()
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
                _json_response(handler, status=200, payload={"ok": True, "path": str(p)})
                return

            if path == "/api/v1/session/export/nodegraphqt" and handler.command == "POST":
                body = _read_json_body(handler)
                parsed_doc = self._normalize_graph_payload(body).doc
                envelope = export_nodegraphqt_session(parsed_doc)
                _json_response(handler, status=200, payload={"ok": True, "envelope": envelope})
                return

            _error(handler, status=404, message=f"not found: {path}")
        except msgspec.ValidationError as exc:
            _error(handler, status=int(HTTPStatus.BAD_REQUEST), message=str(exc))
        except json.JSONDecodeError as exc:
            _error(handler, status=int(HTTPStatus.BAD_REQUEST), message=f"invalid JSON: {exc}")
        except Exception as exc:
            logger.exception("HTTP handler failed path=%s", path, exc_info=exc)
            _error(handler, status=int(HTTPStatus.INTERNAL_SERVER_ERROR), message=f"{type(exc).__name__}: {exc}")

    def _normalize_graph_payload(self, body: Any) -> GraphDocParseResult:
        if not isinstance(body, dict):
            raise ValueError("payload must be a JSON object")
        schema_version = str(body.get("schemaVersion") or "").strip()
        if schema_version == F8STUDIO_SESSION_SCHEMA_VERSION:
            parsed = import_nodegraphqt_session(body)
        elif schema_version == F8STUDIO_GRAPH_SCHEMA_VERSION:
            parsed = load_graph_doc(body)
        else:
            raise ValueError(f"unsupported schemaVersion: {schema_version!r}")

        normalized = normalize_graph_doc(parsed.doc)
        return GraphDocParseResult(
            doc=normalized.doc,
            warnings=tuple(list(parsed.warnings) + list(normalized.warnings)),
        )

    async def _ws_router(self, ws: WebSocketServerProtocol) -> None:
        path = str(ws.path or "")
        if path == "/ws/v1/events":
            await self._ws_events(ws)
            return
        if path == "/ws/v1/viz":
            await self._ws_viz(ws)
            return
        await ws.close(code=1008, reason="unknown websocket path")

    async def _ws_events(self, ws: WebSocketServerProtocol) -> None:
        await self._hub.add(ws)
        try:
            await ws.send(json.dumps({"type": "runtime.status", "payload": self._runtime.status()}))
            async for raw in ws:
                if not isinstance(raw, str):
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue
                cmd = str(msg.get("type") or "").strip()
                if cmd == "runtime.start":
                    await self._runtime.start()
                elif cmd == "runtime.stop":
                    await self._runtime.stop()
                elif cmd == "ping":
                    await ws.send(json.dumps({"type": "pong", "payload": {"tsMs": int(time.time() * 1000)}}))
        finally:
            await self._hub.remove(ws)

    async def _ws_viz(self, ws: WebSocketServerProtocol) -> None:
        session = _VizSession()
        try:
            async for raw in ws:
                if not isinstance(raw, str):
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue
                msg_type = str(msg.get("type") or "").strip()
                if msg_type == "sub":
                    await self._viz_sub(ws, session=session, msg=msg)
                elif msg_type == "unsub":
                    sub_id = str(msg.get("subId") or "").strip()
                    if sub_id:
                        session.cancel(sub_id)
        finally:
            await session.close()

    async def _viz_sub(self, ws: WebSocketServerProtocol, *, session: _VizSession, msg: dict[str, Any]) -> None:
        sub_id = str(msg.get("subId") or "").strip()
        kind = str(msg.get("kind") or "").strip().lower()
        shm_name = str(msg.get("shmName") or "").strip()
        throttle_ms = int(msg.get("throttleMs") or 33)
        if not sub_id or not kind or not shm_name:
            return

        # Replace existing subscription task.
        session.cancel(sub_id)

        if kind == "video":
            task = asyncio.create_task(
                _stream_video_jpeg(ws, sub_id=sub_id, shm_name=shm_name, throttle_ms=throttle_ms),
                name=f"viz:video:{sub_id}",
            )
            session.set_task(sub_id, task)
            return
        if kind == "audio":
            channel = int(msg.get("channel") or 0)
            history_ms = int(msg.get("historyMs") or 250)
            task = asyncio.create_task(
                _stream_audio_waveform(ws, sub_id=sub_id, shm_name=shm_name, throttle_ms=throttle_ms, channel=channel, history_ms=history_ms),
                name=f"viz:audio:{sub_id}",
            )
            session.set_task(sub_id, task)
            return


async def _send_binary_frame(ws: WebSocketServerProtocol, *, meta: dict[str, Any], payload: bytes) -> None:
    meta_bytes = json.dumps(meta, ensure_ascii=False).encode("utf-8")
    header = int(len(meta_bytes)).to_bytes(4, byteorder="little", signed=False)
    try:
        await ws.send(header + meta_bytes + payload)
    except Exception:
        return


async def _stream_video_jpeg(
    ws: WebSocketServerProtocol,
    *,
    sub_id: str,
    shm_name: str,
    throttle_ms: int,
) -> None:
    from f8pysdk.shm import VideoShmReader

    try:
        import cv2  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
    except Exception:
        await _send_binary_frame(
            ws,
            meta={"subId": sub_id, "kind": "video", "mime": "text/plain", "tsMs": int(time.time() * 1000), "error": "missing opencv/numpy"},
            payload=b"",
        )
        return

    reader = VideoShmReader(str(shm_name))
    try:
        reader.open(use_event=False)
    except Exception:
        return

    try:
        while True:
            start = time.perf_counter()
            try:
                pkt = reader.read_latest(decode="none")
            except Exception:
                await asyncio.sleep(0.05)
                continue
            if not isinstance(pkt, dict):
                await asyncio.sleep(0.05)
                continue
            buf = pkt.get("data")
            width = int(pkt.get("width") or 0)
            height = int(pkt.get("height") or 0)
            pitch = int(pkt.get("pitch") or 0)
            ts_ms = int(pkt.get("tsMs") or int(time.time() * 1000))
            if not isinstance(buf, (bytes, bytearray)) or width <= 0 or height <= 0 or pitch <= 0:
                await asyncio.sleep(0.05)
                continue

            frame = np.frombuffer(buf, dtype=np.uint8)
            try:
                frame = frame.reshape((height, pitch))
                frame = frame[:, : width * 4].reshape((height, width, 4))
            except Exception:
                await asyncio.sleep(0.05)
                continue

            bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            ok, enc = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if not ok:
                await asyncio.sleep(0.05)
                continue
            payload = bytes(enc.tobytes())
            await _send_binary_frame(
                ws,
                meta={"subId": sub_id, "kind": "video", "mime": "image/jpeg", "tsMs": ts_ms, "width": width, "height": height},
                payload=payload,
            )

            elapsed_ms = (time.perf_counter() - start) * 1000.0
            sleep_ms = max(0.0, float(throttle_ms) - float(elapsed_ms))
            await asyncio.sleep(sleep_ms / 1000.0 if sleep_ms > 0 else 0.0)
    finally:
        try:
            reader.close()
        except Exception:
            pass


async def _stream_audio_waveform(
    ws: WebSocketServerProtocol,
    *,
    sub_id: str,
    shm_name: str,
    throttle_ms: int,
    channel: int,
    history_ms: int,
) -> None:
    from f8pysdk.shm import AudioShmReader, read_audio_header, SAMPLE_FORMAT_F32LE

    reader = AudioShmReader(str(shm_name))
    try:
        reader.open(use_event=False)
    except Exception:
        return

    try:
        while True:
            start = time.perf_counter()
            try:
                hdr, raw = reader.read_latest()
            except Exception:
                await asyncio.sleep(0.05)
                continue
            if hdr is None or raw is None:
                await asyncio.sleep(0.05)
                continue
            try:
                meta = read_audio_header(hdr)
            except Exception:
                await asyncio.sleep(0.05)
                continue
            sr = int(meta.sample_rate or 0)
            ch = int(meta.channels or 0)
            fmt = str(meta.sample_format or "")
            if sr <= 0 or ch <= 0 or fmt != SAMPLE_FORMAT_F32LE:
                await asyncio.sleep(0.05)
                continue

            # Simple "view only": send raw float32 window for one channel (interleaved).
            frames = int(len(raw) // (4 * ch))
            want_frames = max(1, int(sr * max(20, int(history_ms)) / 1000))
            start_frame = max(0, frames - want_frames)
            chan = max(0, min(int(channel), ch - 1))

            # Slice interleaved float32 for the channel.
            import struct

            out: list[float] = []
            for i in range(start_frame, frames):
                off = (i * ch + chan) * 4
                try:
                    (val,) = struct.unpack_from("<f", raw, off)
                except struct.error:
                    break
                out.append(float(val))
            payload = msgspec.json.encode(out)
            await _send_binary_frame(
                ws,
                meta={"subId": sub_id, "kind": "audio", "mime": "application/json", "tsMs": int(time.time() * 1000), "sampleRate": sr, "channels": ch, "channel": chan},
                payload=payload,
            )

            elapsed_ms = (time.perf_counter() - start) * 1000.0
            sleep_ms = max(0.0, float(throttle_ms) - float(elapsed_ms))
            await asyncio.sleep(sleep_ms / 1000.0 if sleep_ms > 0 else 0.0)
    finally:
        try:
            reader.close()
        except Exception:
            pass


def run_server(*, cfg: ServerConfig) -> None:
    backend = PyStudioWebBackend(cfg=cfg)
    backend.serve_forever()

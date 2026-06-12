from __future__ import annotations

import json
import logging
import socketserver
import threading
import traceback
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from .control_protocol import decode_request_envelope, error_response, success_response

logger = logging.getLogger(__name__)

AutomationRequestHandler = Callable[[str, dict[str, Any]], dict[str, Any]]
_REQUEST_BOUNDARY_ERRORS = (Exception,)


class _AutomationTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        token: str,
        request_handler: AutomationRequestHandler,
    ) -> None:
        super().__init__(server_address, _AutomationLineHandler)
        self.token = str(token)
        self.request_handler = request_handler


class _AutomationLineHandler(socketserver.StreamRequestHandler):
    server: _AutomationTCPServer

    def handle(self) -> None:
        for raw_line in self.rfile:
            line = raw_line.strip()
            if not line:
                continue
            response = self._handle_line(line)
            self.wfile.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
            self.wfile.flush()

    def _handle_line(self, line: bytes) -> dict[str, Any]:
        request_id = ""
        try:
            payload = json.loads(line.decode("utf-8"))
            request = decode_request_envelope(payload)
            request_id = request.request_id
            if request.token != self.server.token:
                return error_response(request_id, code="unauthorized", message="invalid automation token")
            result = self.server.request_handler(request.method, request.params)
            if isinstance(result, dict) and result.get("ok") is False and "error" in result:
                return result
            return success_response(request_id, result)
        except json.JSONDecodeError as exc:
            return error_response(request_id, code="bad_json", message=str(exc))
        except ValueError as exc:
            return error_response(request_id, code="bad_request", message=str(exc))
        except _REQUEST_BOUNDARY_ERRORS as exc:
            traceback_id = uuid4().hex
            logger.error(
                "automation request failed tracebackId=%s\n%s",
                traceback_id,
                "".join(traceback.format_exception(exc)),
            )
            return error_response(
                request_id,
                code="internal_error",
                message=f"{type(exc).__name__}: {exc}",
                details={"tracebackId": traceback_id},
            )


class LocalAutomationServer:
    def __init__(
        self,
        *,
        token: str,
        request_handler: AutomationRequestHandler,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        self._server = _AutomationTCPServer((host, int(port)), token, request_handler)
        self._thread: threading.Thread | None = None

    @property
    def host(self) -> str:
        return str(self._server.server_address[0])

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def start(self) -> None:
        if self._thread is not None:
            return
        thread = threading.Thread(target=self._server.serve_forever, name="f8pystudio-automation-server", daemon=True)
        thread.start()
        self._thread = thread

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=1.0)

from __future__ import annotations

import base64
import json
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import logging
from dataclasses import dataclass
from queue import Empty, Queue
import secrets
import socket
import threading
from urllib import parse

logger = logging.getLogger(__name__)

_DESKTOP_AUTH_AUTHORIZE_PATH = "/v1/auth/desktop/authorize"
_CALLBACK_PATH = "/callback"
_POST_SIGN_IN_SUCCESS_PATH = "/auth-complete"
_POST_SIGN_IN_ERROR_PATH = "/auth-error"


class AssetCloudBrowserAuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class AssetCloudBrowserAuthSession:
    base_url: str
    client_id: str
    state: str
    code_verifier: str
    code_challenge: str
    redirect_uri: str
    callback_port: int
    authorize_url: str


@dataclass(frozen=True)
class AssetCloudBrowserAuthCallback:
    code: str
    state: str
    error: str | None = None
    error_description: str | None = None


class _CallbackServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        callback_path: str,
        *,
        success_redirect_url: str = "",
        error_redirect_url: str = "",
    ) -> None:
        super().__init__(server_address, _CallbackRequestHandler)
        self.callback_path = callback_path
        self.success_redirect_url = str(success_redirect_url or "").strip()
        self.error_redirect_url = str(error_redirect_url or "").strip()
        self.callback_queue: Queue[AssetCloudBrowserAuthCallback] = Queue(maxsize=1)


class _CallbackRequestHandler(BaseHTTPRequestHandler):
    server_version = "PyStudioBrowserAuth/1.0"

    @property
    def _callback_server(self) -> _CallbackServer:
        return self.server  # type: ignore[return-value]

    def do_GET(self) -> None:  # noqa: N802
        parsed = parse.urlsplit(self.path)
        server = self._callback_server
        if parsed.path != server.callback_path:
            self._write_response(404, _callback_result_html(success=False, redirect_url=server.error_redirect_url))
            return
        query = parse.parse_qs(parsed.query, keep_blank_values=False)
        code = _query_first(query, "code")
        state = _query_first(query, "state")
        error = _query_first(query, "error")
        error_description = _query_first(query, "error_description")
        callback = AssetCloudBrowserAuthCallback(
            code=code,
            state=state,
            error=error or None,
            error_description=error_description or None,
        )
        if callback.error or not callback.code or not callback.state:
            self._queue_callback(callback)
            self._write_response(400, _callback_result_html(success=False, redirect_url=server.error_redirect_url))
            return
        self._queue_callback(callback)
        self._write_response(200, _callback_result_html(success=True, redirect_url=server.success_redirect_url))

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        del format, args
        return

    def _queue_callback(self, callback: AssetCloudBrowserAuthCallback) -> None:
        queue = self._callback_server.callback_queue
        try:
            queue.put_nowait(callback)
        except Exception:
            logger.exception("Failed to enqueue browser auth callback")

    def _write_response(self, status_code: int, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class AssetCloudBrowserCallbackServer:
    def __init__(
        self,
        *,
        callback_port: int,
        callback_path: str = _CALLBACK_PATH,
        success_redirect_url: str = "",
        error_redirect_url: str = "",
    ) -> None:
        requested_port = int(callback_port)
        if requested_port < 0:
            raise ValueError("callback_port must not be negative.")
        self._callback_path = str(callback_path)
        self._server = _CallbackServer(
            ("127.0.0.1", requested_port),
            self._callback_path,
            success_redirect_url=success_redirect_url,
            error_redirect_url=error_redirect_url,
        )
        self._callback_port = int(self._server.server_address[1])
        self._thread: threading.Thread | None = None

    @property
    def callback_port(self) -> int:
        return self._callback_port

    @property
    def callback_path(self) -> str:
        return self._callback_path

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def wait_for_callback(self, *, timeout_seconds: float) -> AssetCloudBrowserAuthCallback:
        try:
            callback = self._server.callback_queue.get(timeout=float(timeout_seconds))
        except Empty as exc:
            raise AssetCloudBrowserAuthError("Timed out waiting for browser sign-in to finish.") from exc
        if callback.error:
            description = str(callback.error_description or "").strip()
            if description:
                raise AssetCloudBrowserAuthError(f"Browser sign-in failed: {callback.error} ({description})")
            raise AssetCloudBrowserAuthError(f"Browser sign-in failed: {callback.error}")
        if not callback.code:
            raise AssetCloudBrowserAuthError("Browser sign-in completed without an authorization code.")
        if not callback.state:
            raise AssetCloudBrowserAuthError("Browser sign-in completed without a state value.")
        return callback

    def poll_callback(self) -> AssetCloudBrowserAuthCallback | None:
        try:
            return self._server.callback_queue.get_nowait()
        except Empty:
            return None

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


def create_browser_auth_session(*, base_url: str, client_id: str = "pystudio") -> AssetCloudBrowserAuthSession:
    return create_browser_auth_session_for_port(
        base_url=base_url,
        client_id=client_id,
        callback_port=find_free_loopback_port(),
    )


def create_browser_auth_session_for_port(
    *,
    base_url: str,
    client_id: str = "pystudio",
    callback_port: int,
) -> AssetCloudBrowserAuthSession:
    normalized_base_url = str(base_url or "").strip().rstrip("/")
    if not normalized_base_url:
        raise ValueError("base_url must not be empty.")
    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = build_pkce_code_challenge(code_verifier)
    redirect_uri = f"http://127.0.0.1:{callback_port}{_CALLBACK_PATH}"
    authorize_url = build_browser_authorize_url(
        base_url=normalized_base_url,
        client_id=client_id,
        redirect_uri=redirect_uri,
        state=state,
        code_challenge=code_challenge,
    )
    return AssetCloudBrowserAuthSession(
        base_url=normalized_base_url,
        client_id=client_id,
        state=state,
        code_verifier=code_verifier,
        code_challenge=code_challenge,
        redirect_uri=redirect_uri,
        callback_port=callback_port,
        authorize_url=authorize_url,
    )


def build_browser_callback_redirect_url(*, base_url: str, success: bool) -> str:
    normalized_base_url = str(base_url or "").strip().rstrip("/")
    if not normalized_base_url:
        raise ValueError("base_url must not be empty.")
    path = _POST_SIGN_IN_SUCCESS_PATH if success else _POST_SIGN_IN_ERROR_PATH
    return f"{normalized_base_url}{path}"


def _callback_result_html(*, success: bool, redirect_url: str) -> str:
    title = "PyStudio Sign-In"
    heading = "Sign-in complete" if success else "Sign-in failed"
    message = (
        "Returning to Asset Cloud…"
        if redirect_url
        else (
            "You can return to PyStudio now."
            if success
            else "The desktop app did not receive a valid authorization response."
        )
    )
    escaped_title = _escape_html(title)
    escaped_heading = _escape_html(heading)
    escaped_message = _escape_html(message)
    script = ""
    normalized_redirect_url = str(redirect_url or "").strip()
    if normalized_redirect_url:
        redirect_literal = json.dumps(normalized_redirect_url)
        script = (
            "<script>"
            "window.close();"
            f"window.setTimeout(function(){{window.location.replace({redirect_literal});}}, 80);"
            "</script>"
        )
    return (
        "<!doctype html><html><head><meta charset='utf-8' />"
        f"<title>{escaped_title}</title></head>"
        f"<body><h1>{escaped_heading}</h1><p>{escaped_message}</p>{script}</body></html>"
    )


def _escape_html(value: str) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def build_browser_authorize_url(
    *,
    base_url: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
) -> str:
    normalized_base_url = str(base_url or "").strip().rstrip("/")
    query = parse.urlencode(
        {
            "client_id": str(client_id),
            "redirect_uri": str(redirect_uri),
            "state": str(state),
            "code_challenge": str(code_challenge),
            "code_challenge_method": "S256",
        }
    )
    return f"{normalized_base_url}{_DESKTOP_AUTH_AUTHORIZE_PATH}?{query}"


def build_pkce_code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(str(code_verifier).encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def find_free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _query_first(query: dict[str, list[str]], key: str) -> str:
    values = query.get(key)
    if not values:
        return ""
    return str(values[0] or "").strip()


__all__ = [
    "AssetCloudBrowserAuthCallback",
    "AssetCloudBrowserAuthError",
    "AssetCloudBrowserAuthSession",
    "AssetCloudBrowserCallbackServer",
    "build_browser_authorize_url",
    "build_browser_callback_redirect_url",
    "build_pkce_code_challenge",
    "create_browser_auth_session",
    "create_browser_auth_session_for_port",
    "find_free_loopback_port",
]

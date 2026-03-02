from __future__ import annotations

import json
import logging
import os
import queue
import shlex
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


class LspClientError(RuntimeError):
    """Raised when the language server cannot be started or queried."""


class PythonLspClient:
    """
    Minimal stdio JSON-RPC client for Python LSP servers.

    This client is intentionally small and only implements what Studio needs:
    initialize/open/change/completion/hover/signatureHelp and publishDiagnostics notifications.
    """

    def __init__(
        self,
        *,
        workspace_root: Path,
        diagnostics_callback: Callable[[str, list[dict[str, Any]]], None] | None = None,
        request_timeout_s: float = 1.2,
    ) -> None:
        self._workspace_root = Path(workspace_root).resolve()
        self._diagnostics_callback = diagnostics_callback
        self._request_timeout_s = max(0.2, float(request_timeout_s))

        self._proc: subprocess.Popen[bytes] | None = None
        self._read_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._next_id = 0
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}

    @staticmethod
    def _resolve_server_command() -> list[str]:
        raw = str(os.environ.get("F8_PY_LSP_CMD") or "").strip()
        if raw:
            return shlex.split(raw, posix=(os.name != "nt"))
        exe_name = "basedpyright-langserver.exe" if os.name == "nt" else "basedpyright-langserver"
        py_dir = Path(sys.executable).resolve().parent
        sibling = py_dir / exe_name
        if sibling.is_file():
            return [str(sibling), "--stdio"]
        scripts_dir = py_dir / ("Scripts" if os.name == "nt" else "bin")
        scripts_candidate = scripts_dir / exe_name
        if scripts_candidate.is_file():
            return [str(scripts_candidate), "--stdio"]
        resolved = shutil.which("basedpyright-langserver")
        if resolved:
            return [resolved, "--stdio"]
        # Last fallback: launch module from current interpreter.
        return [sys.executable, "-m", "basedpyright.langserver", "--stdio"]

    def start(self) -> None:
        if self._proc is not None:
            return

        cmd = self._resolve_server_command()
        if not cmd:
            raise LspClientError("Empty language-server command")

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self._workspace_root),
            )
        except OSError as exc:
            raise LspClientError(f"Failed to launch language server command: {' '.join(cmd)}") from exc

        if self._proc.stdin is None or self._proc.stdout is None or self._proc.stderr is None:
            raise LspClientError("Language server stdio pipes are unavailable")

        self._stop_event.clear()
        self._read_thread = threading.Thread(target=self._read_stdout_loop, name="f8-lsp-stdout", daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr_loop, name="f8-lsp-stderr", daemon=True)
        self._read_thread.start()
        self._stderr_thread.start()

        root_uri = self._workspace_root.as_uri()
        init_params: dict[str, Any] = {
            "processId": os.getpid(),
            "rootUri": root_uri,
            "workspaceFolders": [{"uri": root_uri, "name": self._workspace_root.name}],
            "capabilities": {
                "textDocument": {
                    "completion": {
                        "completionItem": {
                            "documentationFormat": ["markdown", "plaintext"],
                            "snippetSupport": True,
                        }
                    },
                    "hover": {"contentFormat": ["markdown", "plaintext"]},
                    "signatureHelp": {
                        "signatureInformation": {
                            "documentationFormat": ["markdown", "plaintext"],
                        }
                    },
                    "publishDiagnostics": {"relatedInformation": False},
                }
            },
            "initializationOptions": {
                "basedpyright": {
                    "analysis": {
                        "diagnosticMode": "openFilesOnly",
                        "typeCheckingMode": "basic",
                    }
                }
            },
        }
        _ = self._request("initialize", init_params, timeout_s=8.0)
        self._notify("initialized", {})
        self._notify(
            "workspace/didChangeConfiguration",
            {
                "settings": {
                    "basedpyright": {
                        "analysis": {
                            "diagnosticMode": "openFilesOnly",
                            "typeCheckingMode": "basic",
                        }
                    }
                }
            },
        )

    def shutdown(self) -> None:
        proc = self._proc
        if proc is None:
            return

        try:
            _ = self._request("shutdown", {})
        except Exception:
            logger.exception("language server shutdown request failed")
        try:
            self._notify("exit", {})
        except Exception:
            logger.exception("language server exit notification failed")

        self._stop_event.set()
        try:
            proc.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1.0)

        self._proc = None

    def open_document(self, *, uri: str, language_id: str, text: str, version: int) -> None:
        self._notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": str(uri),
                    "languageId": str(language_id),
                    "version": int(version),
                    "text": str(text),
                }
            },
        )

    def change_document(self, *, uri: str, text: str, version: int) -> None:
        self._notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": str(uri), "version": int(version)},
                "contentChanges": [{"text": str(text)}],
            },
        )

    def request_completion(self, *, uri: str, line: int, character: int, timeout_s: float | None = None) -> Any:
        params = {
            "textDocument": {"uri": str(uri)},
            "position": {
                "line": max(0, int(line)),
                "character": max(0, int(character)),
            },
        }
        return self._request("textDocument/completion", params, timeout_s=timeout_s)

    def request_hover(self, *, uri: str, line: int, character: int, timeout_s: float | None = None) -> Any:
        params = {
            "textDocument": {"uri": str(uri)},
            "position": {
                "line": max(0, int(line)),
                "character": max(0, int(character)),
            },
        }
        return self._request("textDocument/hover", params, timeout_s=timeout_s)

    def request_signature_help(self, *, uri: str, line: int, character: int, timeout_s: float | None = None) -> Any:
        params = {
            "textDocument": {"uri": str(uri)},
            "position": {
                "line": max(0, int(line)),
                "character": max(0, int(character)),
            },
        }
        return self._request("textDocument/signatureHelp", params, timeout_s=timeout_s)

    def _read_stderr_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        while not self._stop_event.is_set():
            line = proc.stderr.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                logger.warning("lsp stderr: %s", text)

    def _read_stdout_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return

        stream = proc.stdout
        while not self._stop_event.is_set():
            try:
                headers = self._read_headers(stream)
                if headers is None:
                    return
                content_length_raw = headers.get("content-length")
                if content_length_raw is None:
                    logger.error("lsp protocol error: missing Content-Length header")
                    return
                content_length = int(content_length_raw)
                payload = stream.read(content_length)
                if len(payload) != content_length:
                    return
                msg = json.loads(payload.decode("utf-8"))
                self._handle_message(msg)
            except Exception:
                logger.exception("language server stdout reader failed")
                return

    @staticmethod
    def _read_headers(stream: Any) -> dict[str, str] | None:
        headers: dict[str, str] = {}
        while True:
            line = stream.readline()
            if not line:
                return None
            if line in (b"\r\n", b"\n"):
                return headers
            text = line.decode("ascii", errors="replace").strip()
            if not text:
                continue
            key, sep, value = text.partition(":")
            if not sep:
                continue
            headers[key.strip().lower()] = value.strip()

    def _handle_message(self, msg: dict[str, Any]) -> None:
        if "id" in msg and ("result" in msg or "error" in msg):
            try:
                msg_id = int(msg["id"])
            except (TypeError, ValueError):
                return
            with self._pending_lock:
                pending = self._pending.get(msg_id)
            if pending is not None:
                pending.put(msg)
            return

        method = msg.get("method")
        if method != "textDocument/publishDiagnostics":
            return

        params = msg.get("params")
        if not isinstance(params, dict):
            return
        uri = str(params.get("uri") or "")
        diagnostics_raw = params.get("diagnostics")
        diagnostics: list[dict[str, Any]] = []
        if isinstance(diagnostics_raw, list):
            diagnostics = [d for d in diagnostics_raw if isinstance(d, dict)]

        callback = self._diagnostics_callback
        if callback is None:
            return
        callback(uri, diagnostics)

    def _send(self, payload: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise LspClientError("Language server is not running")

        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        with self._write_lock:
            proc.stdin.write(header)
            proc.stdin.write(body)
            proc.stdin.flush()

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": str(method), "params": params})

    def _request(self, method: str, params: dict[str, Any], *, timeout_s: float | None = None) -> Any:
        with self._pending_lock:
            self._next_id += 1
            msg_id = int(self._next_id)
            pending: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            self._pending[msg_id] = pending

        try:
            self._send({"jsonrpc": "2.0", "id": msg_id, "method": str(method), "params": params})
            timeout = self._request_timeout_s if timeout_s is None else max(0.2, float(timeout_s))
            try:
                reply = pending.get(timeout=timeout)
            except queue.Empty as exc:
                raise LspClientError(f"LSP request timeout for method {method}") from exc
        finally:
            with self._pending_lock:
                self._pending.pop(msg_id, None)

        error = reply.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            message = error.get("message")
            raise LspClientError(f"LSP error {code}: {message}")
        return reply.get("result")

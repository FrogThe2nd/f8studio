from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from .control_protocol import AutomationConnectionInfo
from .paths import default_port_file


@dataclass(frozen=True)
class AutomationClientConfig:
    host: str
    port: int
    token: str
    timeout_s: float = 10.0


class AutomationClient:
    def __init__(self, config: AutomationClientConfig) -> None:
        self._cfg = config

    @classmethod
    def from_connection_file(cls, path: str | Path | None = None, *, timeout_s: float = 10.0) -> "AutomationClient":
        info = load_connection_info(path)
        token = Path(info.token_file).read_text(encoding="utf-8").strip()
        return cls(
            AutomationClientConfig(
                host=info.host,
                port=info.port,
                token=token,
                timeout_s=float(timeout_s),
            )
        )

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = uuid4().hex
        payload = {
            "requestId": request_id,
            "method": str(method),
            "token": self._cfg.token,
            "params": dict(params or {}),
        }
        raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        with socket.create_connection((self._cfg.host, int(self._cfg.port)), timeout=float(self._cfg.timeout_s)) as sock:
            sock.settimeout(float(self._cfg.timeout_s))
            sock.sendall(raw)
            response_line = _recv_line(sock)
        response = json.loads(response_line.decode("utf-8"))
        if not isinstance(response, dict):
            raise RuntimeError("automation response must be a JSON object")
        if not bool(response.get("ok")):
            error = response.get("error")
            if isinstance(error, dict):
                message = str(error.get("message") or "automation request failed")
                code = str(error.get("code") or "error")
                raise RuntimeError(f"{code}: {message}")
            raise RuntimeError("automation request failed")
        result = response.get("result")
        if isinstance(result, dict):
            return dict(result)
        return {"value": result}


def load_connection_info(path: str | Path | None = None) -> AutomationConnectionInfo:
    target = Path(path).expanduser() if path is not None else default_port_file()
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("automation connection file must contain a JSON object")
    return AutomationConnectionInfo(
        pid=int(payload.get("pid") or 0),
        host=str(payload.get("host") or "127.0.0.1"),
        port=int(payload.get("port") or 0),
        token_file=str(payload.get("tokenFile") or ""),
        studio_service_id=str(payload.get("studioServiceId") or ""),
        created_at=int(payload.get("createdAt") or 0),
    )


def wait_for_connection_file(
    path: str | Path | None = None,
    *,
    timeout_s: float = 10.0,
    min_created_at: int | None = None,
    previous_mtime_ns: int | None = None,
) -> AutomationConnectionInfo:
    target = Path(path).expanduser() if path is not None else default_port_file()
    deadline = time.monotonic() + float(timeout_s)
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if target.exists():
            try:
                stat_result = target.stat()
                if previous_mtime_ns is not None and int(stat_result.st_mtime_ns) <= int(previous_mtime_ns):
                    time.sleep(0.05)
                    continue
                info = load_connection_info(target)
                if min_created_at is not None and int(info.created_at) < int(min_created_at):
                    time.sleep(0.05)
                    continue
                return info
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
        time.sleep(0.05)
    if last_error is not None:
        raise TimeoutError(f"automation connection file did not become readable: {target}: {last_error}")
    raise TimeoutError(f"automation connection file not found: {target}")


def _recv_line(sock: socket.socket) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        if b"\n" in chunk:
            before, _sep, _after = chunk.partition(b"\n")
            chunks.append(before)
            break
        chunks.append(chunk)
    if not chunks:
        raise RuntimeError("automation server closed connection without a response")
    return b"".join(chunks)

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AutomationConnectionInfo:
    pid: int
    host: str
    port: int
    token_file: str
    studio_service_id: str
    created_at: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "pid": int(self.pid),
            "host": str(self.host),
            "port": int(self.port),
            "tokenFile": str(self.token_file),
            "studioServiceId": str(self.studio_service_id),
            "createdAt": int(self.created_at),
        }


@dataclass(frozen=True)
class AutomationRequestEnvelope:
    request_id: str
    method: str
    token: str
    params: dict[str, Any]


@dataclass(frozen=True)
class AutomationResponseEnvelope:
    request_id: str
    ok: bool
    result: dict[str, Any]
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "requestId": self.request_id,
            "ok": bool(self.ok),
            "result": dict(self.result),
            "error": self.error,
        }


def decode_request_envelope(payload: Any) -> AutomationRequestEnvelope:
    if not isinstance(payload, dict):
        raise ValueError("request envelope must be a JSON object")
    request_id = str(payload.get("requestId") or "").strip()
    if not request_id:
        raise ValueError("requestId is required")
    method = str(payload.get("method") or "").strip()
    if not method:
        raise ValueError("method is required")
    token = str(payload.get("token") or "")
    params_obj = payload.get("params")
    if params_obj is None:
        params_obj = {}
    if not isinstance(params_obj, dict):
        raise ValueError("params must be a JSON object")
    return AutomationRequestEnvelope(
        request_id=request_id,
        method=method,
        token=token,
        params=dict(params_obj),
    )


def error_response(request_id: str, *, code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return AutomationResponseEnvelope(
        request_id=str(request_id or ""),
        ok=False,
        result={},
        error={
            "code": str(code or "error"),
            "message": str(message or ""),
            "details": dict(details or {}),
        },
    ).to_dict()


def success_response(request_id: str, result: dict[str, Any] | None = None) -> dict[str, Any]:
    return AutomationResponseEnvelope(
        request_id=str(request_id or ""),
        ok=True,
        result=dict(result or {}),
        error=None,
    ).to_dict()

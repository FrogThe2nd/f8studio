from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from f8pysdk.nats_naming import ensure_token, new_id, svc_endpoint_subject
from f8pysdk.service_bus.codec import decode_obj, encode_obj

from .nats_request import NatsRequester


@dataclass(frozen=True)
class SetStateRequestResult:
    accepted: bool
    rejected: bool
    reject_code: str
    reject_message: str


def message_data_bytes(message: Any) -> bytes:
    try:
        data = message.data
    except AttributeError:
        return b""
    try:
        return bytes(data or b"")
    except (TypeError, ValueError):
        return b""


def decode_json_object(raw: bytes) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        decoded = decode_obj(raw)
    except ValueError:
        return None
    return decoded


async def request_service_status(
    requester: NatsRequester,
    *,
    service_id: str,
    timeout_s: float = 0.4,
) -> dict[str, Any] | None:
    sid = ensure_token(str(service_id), label="service_id")
    payload = encode_obj({"reqId": new_id(), "args": {}, "meta": {"actor": "studio", "cmd": "status"}})
    try:
        message = await requester.request(svc_endpoint_subject(sid, "status"), payload, timeout=float(timeout_s))
    except Exception:
        return None
    raw = message_data_bytes(message)
    if not raw:
        return None
    response = decode_json_object(raw)
    if response is None:
        return None
    if not (isinstance(response, dict) and response.get("ok") is True):
        return None
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    if not isinstance(result, dict):
        return None
    output: dict[str, Any] = {"alive": True}
    if "active" in result:
        output["active"] = bool(result.get("active"))
    return output


async def request_set_service_active(
    requester: NatsRequester,
    *,
    service_id: str,
    active: bool,
    attempts: int,
    timeout_s: float,
    retry_sleep_s: float,
) -> bool:
    sid = ensure_token(str(service_id), label="service_id")
    cmd = "activate" if bool(active) else "deactivate"
    payload = encode_obj(
        {"reqId": new_id(), "args": {"active": bool(active)}, "meta": {"actor": "studio", "cmd": cmd}}
    )
    for _ in range(max(int(attempts), 1)):
        try:
            message = await requester.request(svc_endpoint_subject(sid, cmd), payload, timeout=float(timeout_s))
            data = message_data_bytes(message)
            if data:
                response = decode_json_object(data) or {}
                if isinstance(response, dict) and response.get("ok") is True:
                    return True
        except Exception:
            await asyncio.sleep(float(retry_sleep_s))
            continue
    return False


async def request_service_terminate(
    requester: NatsRequester,
    *,
    service_id: str,
    attempts: int,
    timeout_s: float,
    retry_sleep_s: float,
) -> bool:
    sid = ensure_token(str(service_id), label="service_id")
    subject = svc_endpoint_subject(sid, "terminate")
    payload = encode_obj({"reqId": new_id(), "args": {}, "meta": {"actor": "studio", "cmd": "terminate"}})
    for _ in range(max(int(attempts), 1)):
        try:
            message = await requester.request(subject, payload, timeout=float(timeout_s))
            raw = message_data_bytes(message)
            if not raw:
                continue
            response = decode_json_object(raw) or {}
            if isinstance(response, dict) and response.get("ok") is True:
                return True
            return False
        except Exception:
            await asyncio.sleep(float(retry_sleep_s))
            continue
    return False


async def request_set_remote_state(
    requester: NatsRequester,
    *,
    service_id: str,
    node_id: str,
    field: str,
    value: Any,
    attempts: int,
    timeout_s: float,
    retry_sleep_s: float,
) -> SetStateRequestResult:
    sid = ensure_token(str(service_id), label="service_id")
    nid = ensure_token(str(node_id), label="node_id")
    state_field = str(field or "").strip()
    if not state_field:
        return SetStateRequestResult(
            accepted=False,
            rejected=False,
            reject_code="",
            reject_message="",
        )
    payload = encode_obj(
        {
            "reqId": new_id(),
            "args": {"nodeId": nid, "field": state_field, "value": value},
            "meta": {"actor": "studio", "source": "ui"},
        }
    )
    subject = svc_endpoint_subject(sid, "set_state")
    for _ in range(max(int(attempts), 1)):
        try:
            message = await requester.request(subject, payload, timeout=float(timeout_s))
            raw = message_data_bytes(message)
            if not raw:
                continue
            response = decode_json_object(raw) or {}
            if isinstance(response, dict) and response.get("ok") is True:
                return SetStateRequestResult(
                    accepted=True,
                    rejected=False,
                    reject_code="",
                    reject_message="",
                )
            if isinstance(response, dict) and response.get("ok") is False:
                error = response.get("error") if isinstance(response.get("error"), dict) else {}
                return SetStateRequestResult(
                    accepted=False,
                    rejected=True,
                    reject_code=str(error.get("code") or ""),
                    reject_message=str(error.get("message") or ""),
                )
        except Exception:
            await asyncio.sleep(float(retry_sleep_s))
            continue
    return SetStateRequestResult(
        accepted=False,
        rejected=False,
        reject_code="",
        reject_message="",
    )

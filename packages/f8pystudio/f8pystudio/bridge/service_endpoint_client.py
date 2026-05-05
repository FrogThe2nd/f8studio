from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import msgspec
from f8pysdk.specs import (
    F8ActivateRequest,
    F8ActiveReply,
    F8CommandError,
    F8DeactivateRequest,
    F8EmptyArgs,
    F8SetStateArgs,
    F8SetStateReply,
    F8SetStateRequest,
    F8StatusReply,
    F8StatusRequest,
    F8TerminateReply,
    F8TerminateRequest,
)
from f8pysdk.f8_naming import ensure_token, new_id, svc_endpoint_subject
from f8pysdk.codec import decode_as, encode_obj

from .runtime_request import RuntimeRequester

logger = logging.getLogger(__name__)


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


def _error_message(error: F8CommandError | None | msgspec.UnsetType) -> str:
    if error is None or isinstance(error, msgspec.UnsetType):
        return ""
    return str(error.message or "")


def _error_code(error: F8CommandError | None | msgspec.UnsetType) -> str:
    if error is None or isinstance(error, msgspec.UnsetType):
        return ""
    return str(error.code.value)


async def request_service_status(
    requester: RuntimeRequester,
    *,
    service_id: str,
    timeout_s: float = 0.4,
) -> dict[str, Any] | None:
    sid = ensure_token(str(service_id), label="service_id")
    payload = encode_obj(
        F8StatusRequest(
            reqId=new_id(),
            args=F8EmptyArgs(),
            meta={"actor": "studio", "cmd": "status"},
        )
    )
    try:
        message = await requester.request(svc_endpoint_subject(sid, "status"), payload, timeout=float(timeout_s))
    except Exception as exc:
        logger.debug("service status request failed service_id=%s", service_id, exc_info=exc)
        return None
    raw = message_data_bytes(message)
    if not raw:
        return None
    try:
        response = decode_as(raw, F8StatusReply)
    except ValueError:
        return None
    if not response.ok:
        return None
    result = response.result
    if result is None or isinstance(result, msgspec.UnsetType):
        return None
    output: dict[str, Any] = {"alive": True}
    output["active"] = bool(result.active)
    return output


async def request_set_service_active(
    requester: RuntimeRequester,
    *,
    service_id: str,
    active: bool,
    attempts: int,
    timeout_s: float,
    retry_sleep_s: float,
) -> bool:
    sid = ensure_token(str(service_id), label="service_id")
    cmd = "activate" if bool(active) else "deactivate"
    if cmd == "activate":
        payload = encode_obj(
            F8ActivateRequest(reqId=new_id(), args=F8EmptyArgs(), meta={"actor": "studio", "cmd": cmd})
        )
    else:
        payload = encode_obj(
            F8DeactivateRequest(reqId=new_id(), args=F8EmptyArgs(), meta={"actor": "studio", "cmd": cmd})
        )
    for _ in range(max(int(attempts), 1)):
        try:
            message = await requester.request(svc_endpoint_subject(sid, cmd), payload, timeout=float(timeout_s))
            data = message_data_bytes(message)
            if data:
                response = decode_as(data, F8ActiveReply)
                if response.ok:
                    return True
        except Exception as exc:
            logger.debug("set service active request failed service_id=%s active=%s", service_id, active, exc_info=exc)
            await asyncio.sleep(float(retry_sleep_s))
            continue
    return False


async def request_service_terminate(
    requester: RuntimeRequester,
    *,
    service_id: str,
    attempts: int,
    timeout_s: float,
    retry_sleep_s: float,
) -> bool:
    sid = ensure_token(str(service_id), label="service_id")
    subject = svc_endpoint_subject(sid, "terminate")
    payload = encode_obj(
        F8TerminateRequest(
            reqId=new_id(),
            args=F8EmptyArgs(),
            meta={"actor": "studio", "cmd": "terminate"},
        )
    )
    for _ in range(max(int(attempts), 1)):
        try:
            message = await requester.request(subject, payload, timeout=float(timeout_s))
            raw = message_data_bytes(message)
            if not raw:
                continue
            response = decode_as(raw, F8TerminateReply)
            if response.ok:
                return True
            return False
        except Exception as exc:
            logger.debug("service terminate request failed service_id=%s", service_id, exc_info=exc)
            await asyncio.sleep(float(retry_sleep_s))
            continue
    return False


async def request_set_remote_state(
    requester: RuntimeRequester,
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
        F8SetStateRequest(
            reqId=new_id(),
            args=F8SetStateArgs(nodeId=nid, field=state_field, value=value),
            meta={"actor": "studio", "source": "ui"},
        )
    )
    subject = svc_endpoint_subject(sid, "set_state")
    for _ in range(max(int(attempts), 1)):
        try:
            message = await requester.request(subject, payload, timeout=float(timeout_s))
            raw = message_data_bytes(message)
            if not raw:
                continue
            response = decode_as(raw, F8SetStateReply)
            if response.ok:
                return SetStateRequestResult(
                    accepted=True,
                    rejected=False,
                    reject_code="",
                    reject_message="",
                )
            if not response.ok:
                error = response.error
                return SetStateRequestResult(
                    accepted=False,
                    rejected=True,
                    reject_code=_error_code(error),
                    reject_message=_error_message(error),
                )
        except Exception as exc:
            logger.debug(
                "set remote state request failed service_id=%s node_id=%s field=%s",
                service_id,
                node_id,
                field,
                exc_info=exc,
            )
            await asyncio.sleep(float(retry_sleep_s))
            continue
    return SetStateRequestResult(
        accepted=False,
        rejected=False,
        reject_code="",
        reject_message="",
    )

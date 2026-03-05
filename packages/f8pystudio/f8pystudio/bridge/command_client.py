from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import msgspec
from f8pysdk.generated import F8CommandInvokeReply, F8CommandInvokeRequest
from f8pysdk.nats_naming import cmd_channel_subject, ensure_token, new_id

from .json_codec import coerce_json_dict
from .nats_request import request_typed


class CommandGateway(Protocol):
    async def request_command(self, req: "CommandRequest") -> "CommandResponse": ...


@dataclass(frozen=True)
class CommandRequest:
    service_id: str
    call: str
    args: dict[str, Any] | None = None
    timeout_s: float = 2.0
    source: str = "ui"
    actor: str = "studio"


@dataclass(frozen=True)
class CommandResponse:
    ok: bool
    result: dict[str, Any]
    error_message: str
    payload: dict[str, Any]


@dataclass
class NatsCommandGateway:
    nats_url: str
    _nc: Any | None = None

    async def ensure_connected(self) -> Any:
        if self._nc is not None:
            return self._nc
        import nats

        self._nc = await nats.connect(servers=[str(self.nats_url)], connect_timeout=2)
        return self._nc

    async def close(self) -> None:
        if self._nc is None:
            return
        await self._nc.close()
        self._nc = None

    async def request_command(self, req: CommandRequest) -> CommandResponse:
        sid = ensure_token(str(req.service_id), label="service_id")
        call_name = str(req.call or "").strip()
        if not call_name:
            raise ValueError("call is empty")

        nc = await self.ensure_connected()
        payload = F8CommandInvokeRequest(
            reqId=new_id(),
            call=call_name,
            args=coerce_json_dict(req.args or {}),
            meta={"actor": str(req.actor), "source": str(req.source)},
        )
        response_payload = await request_typed(
            nc,
            subject=cmd_channel_subject(sid),
            payload=payload,
            timeout_s=float(req.timeout_s),
            response_type=F8CommandInvokeReply,
        )
        result = response_payload.result
        result_obj = result if isinstance(result, dict) else {"value": result}
        err = response_payload.error
        if err is None or isinstance(err, msgspec.UnsetType):
            err_message = ""
        else:
            err_message = str(err.message or "").strip()
        ok = bool(response_payload.ok)
        if ok:
            err_message = ""
        payload_obj: dict[str, Any] = {
            "reqId": str(response_payload.reqId or ""),
            "ok": ok,
            "result": result,
            "error": (
                None
                if err is None or isinstance(err, msgspec.UnsetType)
                else {"code": err.code.value, "message": err.message, "details": err.details}
            ),
        }
        return CommandResponse(ok=ok, result=result_obj, error_message=err_message, payload=payload_obj)

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import msgspec
from f8pysdk.bus import BusBackend
from f8pysdk.specs import F8CommandInvokeReply
from f8pysdk.nats_naming import cmd_channel_subject, ensure_token, new_id
from f8pysdk.runtime_transport import RuntimeTransport
from f8pysdk.transport import NatsTransport, NatsTransportConfig
from f8pysdk.zenoh_transport import ZenohTransport, ZenohTransportConfig

from .json_codec import coerce_json_value
from .runtime_request import RuntimeRequester, RuntimeTransportRequester, request_typed


class CommandGateway(Protocol):
    async def request_command(self, req: "CommandRequest") -> "CommandResponse": ...


@dataclass(frozen=True)
class CommandRequest:
    service_id: str
    call: str
    args: Any = None
    timeout_s: float = 2.0
    source: str = "ui"
    actor: str = "studio"


@dataclass(frozen=True)
class CommandResponse:
    ok: bool
    result: dict[str, Any]
    error_message: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class RuntimeCommandGatewayConfig:
    bus_backend: BusBackend = "zenoh"
    nats_url: str = "nats://127.0.0.1:4222"
    client_service_id: str = "studio"
    zenoh_config_path: str | None = None
    zenoh_connect: tuple[str, ...] = ()
    zenoh_listen: tuple[str, ...] = ()
    zenoh_shm_pool_bytes: int = 256 * 1024 * 1024


def _build_runtime_transport(config: RuntimeCommandGatewayConfig) -> RuntimeTransport:
    if config.bus_backend == "nats":
        from f8pysdk.nats_naming import kv_bucket_for_service

        return NatsTransport(
            NatsTransportConfig(
                url=str(config.nats_url),
                kv_bucket=kv_bucket_for_service(str(config.client_service_id)),
            )
        )
    return ZenohTransport(
        ZenohTransportConfig(
            service_id=str(config.client_service_id),
            config_path=config.zenoh_config_path,
            connect=config.zenoh_connect,
            listen=config.zenoh_listen,
            shm_pool_bytes=config.zenoh_shm_pool_bytes,
        )
    )


@dataclass
class RuntimeCommandGateway:
    config: RuntimeCommandGatewayConfig
    _transport: RuntimeTransport | None = None
    _requester: RuntimeTransportRequester | None = None

    async def ensure_connected(self) -> RuntimeTransportRequester:
        requester = self._requester
        if requester is not None:
            return requester
        transport = _build_runtime_transport(self.config)
        await transport.connect()
        self._transport = transport
        self._requester = RuntimeTransportRequester(transport=transport)
        return self._requester

    async def close(self) -> None:
        transport = self._transport
        self._transport = None
        self._requester = None
        if transport is not None:
            await transport.close()

    async def request_command(self, req: CommandRequest) -> CommandResponse:
        return await _request_command_with_requester(await self.ensure_connected(), req)


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
        return await _request_command_with_requester(await self.ensure_connected(), req)


async def _request_command_with_requester(requester: RuntimeRequester, req: CommandRequest) -> CommandResponse:
    sid = ensure_token(str(req.service_id), label="service_id")
    call_name = str(req.call or "").strip()
    if not call_name:
        raise ValueError("call is empty")

    payload = {
        "reqId": new_id(),
        "call": call_name,
        "args": coerce_json_value(req.args),
        "meta": {"actor": str(req.actor), "source": str(req.source)},
    }
    response_payload = await request_typed(
        requester,
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

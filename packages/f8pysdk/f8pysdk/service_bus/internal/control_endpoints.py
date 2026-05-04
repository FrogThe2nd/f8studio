from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from ...codec import encode_obj
from ...generated import Code, F8CommandError, F8CommandInvokeReply
from ...nats_naming import cmd_channel_subject, svc_endpoint_subject
from .micro import ServiceBusMicroEndpoints

log = logging.getLogger(__name__)


class ServiceControlEndpointServer(Protocol):
    async def start(self) -> Any: ...

    async def stop(self) -> None: ...


@dataclass
class _TransportEndpointRequest:
    data: bytes
    response: bytes | None = None

    async def respond(self, payload: bytes) -> None:
        self.response = bytes(payload)


class ZenohServiceControlEndpointServer:
    """
    Backend-neutral control endpoint server using RuntimeTransport request/serve.

    It reuses the canonical request handlers from `ServiceBusMicroEndpoints` so
    NATS micro and Zenoh queryables preserve identical wire payload semantics.
    """

    def __init__(self, bus: Any) -> None:
        self._bus = bus
        self._handlers = ServiceBusMicroEndpoints(bus)
        self._handles: list[Any] = []

    async def start(self) -> Any:
        sid = str(self._bus.service_id)
        registrations: tuple[tuple[str, Callable[[Any], Awaitable[None]]], ...] = (
            (svc_endpoint_subject(sid, "activate"), self._handlers._activate),
            (svc_endpoint_subject(sid, "deactivate"), self._handlers._deactivate),
            (svc_endpoint_subject(sid, "set_active"), self._handlers._set_active),
            (svc_endpoint_subject(sid, "status"), self._handlers._status),
            (svc_endpoint_subject(sid, "terminate"), self._handlers._terminate),
            (svc_endpoint_subject(sid, "quit"), self._handlers._terminate),
            (cmd_channel_subject(sid), self._handlers._cmd),
            (svc_endpoint_subject(sid, "set_state"), self._handlers._set_state),
            (svc_endpoint_subject(sid, "set_rungraph"), self._handlers._set_rungraph),
        )
        for subject, handler in registrations:
            self._handles.append(await self._bus._transport.serve(subject, self._wrap_handler(subject, handler)))
        return self

    async def stop(self) -> None:
        for handle in list(self._handles):
            try:
                await handle.unsubscribe()
            except Exception as exc:
                log.error("failed to stop control endpoint service_id=%s", self._bus.service_id, exc_info=exc)
        self._handles.clear()

    @staticmethod
    def _empty_internal_error(req_id: str) -> bytes:
        return encode_obj(
            F8CommandInvokeReply(
                reqId=str(req_id or ""),
                ok=False,
                result=None,
                error=F8CommandError(code=Code.INTERNAL, message="control endpoint failed", details={}),
            )
        )

    def _wrap_handler(
        self,
        subject: str,
        handler: Callable[[Any], Awaitable[None]],
    ) -> Callable[[bytes], Awaitable[bytes | None]]:
        async def _handle(payload: bytes) -> bytes | None:
            req = _TransportEndpointRequest(data=bytes(payload))
            try:
                await handler(req)
            except Exception as exc:
                log.error("control endpoint handler failed subject=%s", subject, exc_info=exc)
                return self._empty_internal_error("")
            return req.response or b""

        return _handle


def create_service_control_endpoint_server(bus: Any) -> ServiceControlEndpointServer:
    if str(bus.bus_backend) == "nats":
        return ServiceBusMicroEndpoints(bus)
    return ZenohServiceControlEndpointServer(bus)


__all__ = [
    "ServiceControlEndpointServer",
    "ZenohServiceControlEndpointServer",
    "create_service_control_endpoint_server",
]

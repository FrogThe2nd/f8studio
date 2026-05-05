from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from f8pysdk.codec import decode_obj, encode_obj

from f8pystudio.bridge.command_client import CommandRequest, NatsCommandGateway


class _FakeRequester:
    def __init__(self, *, response_payload: object) -> None:
        self._response_payload = response_payload
        self.requests: list[tuple[str, bytes, float]] = []

    async def request(self, subject: str, payload: bytes, timeout: float) -> Any:
        self.requests.append((str(subject), bytes(payload), float(timeout)))
        return SimpleNamespace(data=encode_obj(self._response_payload))


def test_nats_command_gateway_serializes_scalar_args_without_wrapping() -> None:
    requester = _FakeRequester(
        response_payload={"reqId": "r1", "ok": True, "result": 7, "error": None},
    )
    gateway = NatsCommandGateway(nats_url="nats://unused")
    gateway._requester = requester

    response = asyncio.run(gateway.request_command(CommandRequest(service_id="svc_alpha", call="ping", args=7)))

    assert len(requester.requests) == 1
    payload = decode_obj(requester.requests[0][1])
    assert payload.get("args") == 7
    assert response.ok is True
    assert response.result == {"value": 7}


def test_nats_command_gateway_serializes_list_args_without_wrapping() -> None:
    requester = _FakeRequester(
        response_payload={"reqId": "r2", "ok": True, "result": {"ok": 1}, "error": None},
    )
    gateway = NatsCommandGateway(nats_url="nats://unused")
    gateway._requester = requester

    response = asyncio.run(gateway.request_command(CommandRequest(service_id="svc_alpha", call="ping", args=[1, 2])))

    assert len(requester.requests) == 1
    payload = decode_obj(requester.requests[0][1])
    assert payload.get("args") == [1, 2]
    assert response.ok is True
    assert response.result == {"ok": 1}

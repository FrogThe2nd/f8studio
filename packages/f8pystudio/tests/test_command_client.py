from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from f8pysdk.codec import decode_obj, encode_obj

from f8pystudio.bridge.command_client import CommandRequest, RuntimeCommandGateway, RuntimeCommandGatewayConfig


class _FakeRequester:
    def __init__(self, *, response_payload: object) -> None:
        self._response_payload = response_payload
        self.requests: list[tuple[str, bytes, float]] = []

    async def request(self, key: str, payload: bytes, timeout: float) -> Any:
        self.requests.append((str(key), bytes(payload), float(timeout)))
        return SimpleNamespace(data=encode_obj(self._response_payload))


def test_runtime_command_gateway_serializes_scalar_args_without_wrapping() -> None:
    requester = _FakeRequester(
        response_payload={"reqId": "r1", "ok": True, "result": 7, "error": None},
    )
    gateway = RuntimeCommandGateway(RuntimeCommandGatewayConfig())
    gateway._requester = requester

    response = asyncio.run(gateway.request_command(CommandRequest(service_id="svc_alpha", call="ping", args=7)))

    assert len(requester.requests) == 1
    payload = decode_obj(requester.requests[0][1])
    assert payload.get("args") == 7
    assert response.ok is True
    assert response.result == {"value": 7}


def test_runtime_command_gateway_serializes_list_args_without_wrapping() -> None:
    requester = _FakeRequester(
        response_payload={"reqId": "r2", "ok": True, "result": {"ok": 1}, "error": None},
    )
    gateway = RuntimeCommandGateway(RuntimeCommandGatewayConfig())
    gateway._requester = requester

    response = asyncio.run(gateway.request_command(CommandRequest(service_id="svc_alpha", call="ping", args=[1, 2])))

    assert len(requester.requests) == 1
    payload = decode_obj(requester.requests[0][1])
    assert payload.get("args") == [1, 2]
    assert response.ok is True
    assert response.result == {"ok": 1}


class _SlowConnectTransport:
    def __init__(self) -> None:
        self.connect_calls = 0
        self.close_calls = 0

    async def connect(self) -> None:
        self.connect_calls += 1
        await asyncio.sleep(0.01)

    async def close(self) -> None:
        self.close_calls += 1

    async def publish(self, key: str, payload: bytes) -> None:
        _ = (key, payload)

    async def subscribe(self, key_expr: str, *, queue: str | None = None, cb: object | None = None) -> object:
        _ = (key_expr, queue, cb)
        return object()

    async def request(
        self,
        key: str,
        payload: bytes,
        *,
        timeout: float = 1.0,
        raise_on_error: bool = False,
    ) -> bytes | None:
        _ = (key, payload, timeout, raise_on_error)
        return None

    async def serve(self, key: str, handler: object) -> object:
        _ = (key, handler)
        return object()

    async def retained_put(self, key: str, value: bytes) -> None:
        _ = (key, value)

    async def retained_get(self, key: str) -> bytes | None:
        _ = key
        return None

    async def retained_watch(self, key_expr: str, *, cb: object, with_initial: bool = True) -> object:
        _ = (key_expr, cb, with_initial)
        return object()


class _ConnectCountingCommandGateway(RuntimeCommandGateway):
    def __init__(self) -> None:
        super().__init__(RuntimeCommandGatewayConfig())
        self.created_transports: list[_SlowConnectTransport] = []

    def _build_transport(self) -> _SlowConnectTransport:
        transport = _SlowConnectTransport()
        self.created_transports.append(transport)
        return transport


def test_runtime_command_gateway_serializes_concurrent_connects() -> None:
    async def _run() -> None:
        gateway = _ConnectCountingCommandGateway()

        first, second = await asyncio.gather(gateway.ensure_connected(), gateway.ensure_connected())

        assert first is second
        assert len(gateway.created_transports) == 1
        assert gateway.created_transports[0].connect_calls == 1
        await gateway.close()

    asyncio.run(_run())

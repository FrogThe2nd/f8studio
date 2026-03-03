from __future__ import annotations

import asyncio
from typing import Any

from nats.js.errors import NotFoundError as JsNotFoundError

from f8pysdk.nats_transport import NatsTransport, NatsTransportConfig


class _FakeJs:
    def __init__(self) -> None:
        self._kv = object()
        self.created_cfg: Any | None = None
        self.raise_delete = False
        self.raise_get_kv = False

    async def delete_key_value(self, _bucket: str) -> None:
        if self.raise_delete:
            raise JsNotFoundError(code=404, err_code=10059, description="stream not found")

    async def key_value(self, _bucket: str) -> Any:
        if self.raise_get_kv:
            raise JsNotFoundError(code=404, err_code=10059, description="stream not found")
        return self._kv

    async def create_key_value(self, config: Any) -> Any:
        self.created_cfg = config
        return self._kv


class _FakeNc:
    def __init__(self, js: _FakeJs) -> None:
        self._js = js
        self.drain_called = False

    def jetstream(self) -> _FakeJs:
        return self._js

    async def drain(self) -> None:
        self.drain_called = True


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_connect_ignores_delete_key_value_not_found(monkeypatch) -> None:
    js = _FakeJs()
    js.raise_delete = True
    nc = _FakeNc(js)

    async def _fake_connect(*args: Any, **kwargs: Any) -> _FakeNc:
        del args, kwargs
        return nc

    monkeypatch.setattr("f8pysdk.nats_transport.nats.connect", _fake_connect)

    tr = NatsTransport(
        NatsTransportConfig(
            url="nats://127.0.0.1:4222",
            kv_bucket="kv.test",
            delete_bucket_on_connect=True,
        )
    )

    _run(tr.connect())

    assert tr.is_connected is True
    assert tr._kv is js._kv
    assert tr._kv_stores.get("kv.test") is js._kv


def test_open_kv_creates_bucket_when_js_not_found(monkeypatch) -> None:
    js = _FakeJs()
    js.raise_get_kv = True
    nc = _FakeNc(js)

    async def _fake_connect(*args: Any, **kwargs: Any) -> _FakeNc:
        del args, kwargs
        return nc

    monkeypatch.setattr("f8pysdk.nats_transport.nats.connect", _fake_connect)

    tr = NatsTransport(
        NatsTransportConfig(
            url="nats://127.0.0.1:4222",
            kv_bucket="kv.create",
        )
    )

    _run(tr.connect())

    assert js.created_cfg is not None
    assert str(js.created_cfg.bucket) == "kv.create"
    assert tr._kv is js._kv


def test_close_ignores_delete_key_value_not_found(monkeypatch) -> None:
    js = _FakeJs()
    nc = _FakeNc(js)

    async def _fake_connect(*args: Any, **kwargs: Any) -> _FakeNc:
        del args, kwargs
        return nc

    monkeypatch.setattr("f8pysdk.nats_transport.nats.connect", _fake_connect)

    tr = NatsTransport(
        NatsTransportConfig(
            url="nats://127.0.0.1:4222",
            kv_bucket="kv.close",
            delete_bucket_on_close=True,
        )
    )

    _run(tr.connect())
    js.raise_delete = True

    _run(tr.close())

    assert nc.drain_called is True
    assert tr.is_connected is False

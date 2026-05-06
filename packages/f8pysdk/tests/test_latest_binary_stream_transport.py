from __future__ import annotations

from dataclasses import dataclass

from f8pysdk.binary_stream_transport import ZenohLatestBinaryStreamTransport


class _Session:
    def close(self) -> None:
        return


@dataclass(frozen=True)
class _Sample:
    payload: bytes


def test_zenoh_latest_binary_stream_transport_keeps_latest_payload_only() -> None:
    transport = ZenohLatestBinaryStreamTransport(
        key_expr="f8/test/stream/raw",
        session=_Session(),
        log_context="test",
    )
    try:
        transport._on_sample(_Sample(b"old"))
        transport._on_sample(_Sample(b"new"))

        assert transport.poll_latest_raw() == b"new"
        assert transport.poll_latest_raw() is None
    finally:
        transport.close()


def test_zenoh_latest_binary_stream_transport_wait_returns_none_after_close() -> None:
    transport = ZenohLatestBinaryStreamTransport(
        key_expr="f8/test/stream/closed",
        session=_Session(),
        log_context="test",
    )
    transport.close()

    assert transport.wait_latest_raw(timeout_ms=10) is None

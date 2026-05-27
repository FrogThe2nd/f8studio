from __future__ import annotations

from types import SimpleNamespace

from f8pystudio.bridge.service_liveliness import (
    ServiceLivelinessIdentity,
    format_runtime_instances,
    is_zenoh_liveliness_reply_channel_drained,
    query_service_liveliness_instances_sync,
    service_id_from_zenoh_liveliness_key,
    service_liveliness_identity_from_zenoh_key,
)


def test_service_liveliness_key_extracts_service_id() -> None:
    assert service_id_from_zenoh_liveliness_key("f8/live/svc/engine/instances/inst1") == "engine"
    assert service_id_from_zenoh_liveliness_key("/f8/live/svc/detector/instances/inst2/") == "detector"
    assert service_id_from_zenoh_liveliness_key("f8/live/studio/studio") is None
    assert service_id_from_zenoh_liveliness_key("f8/live/svc/") is None
    assert service_id_from_zenoh_liveliness_key("f8/live/svc/bad/path") is None


def test_service_liveliness_key_extracts_instance_identity() -> None:
    assert service_liveliness_identity_from_zenoh_key("f8/live/svc/engine/instances/inst1") == (
        ServiceLivelinessIdentity(service_id="engine", runtime_instance_id="inst1")
    )
    assert service_liveliness_identity_from_zenoh_key("f8/live/svc/engine") is None


def test_format_runtime_instances() -> None:
    assert format_runtime_instances(None) == "<unknown>"
    assert format_runtime_instances(set()) == "<none>"
    assert format_runtime_instances({"inst_b", "inst_a"}) == "inst_a,inst_b"


def test_query_service_liveliness_instances_sync_collects_matching_instances() -> None:
    class _ZError(Exception):
        pass

    class _Replies:
        def __init__(self) -> None:
            self._items = [
                SimpleNamespace(ok=SimpleNamespace(key_expr="f8/live/svc/engine/instances/inst1")),
                SimpleNamespace(ok=SimpleNamespace(key_expr="f8/live/svc/other/instances/inst2")),
                SimpleNamespace(ok=None),
            ]

        def try_recv(self) -> object:
            if self._items:
                return self._items.pop(0)
            raise _ZError("channel is empty and closed")

    class _Liveliness:
        def get(self, key_expr: str, *, timeout: float) -> _Replies:
            assert key_expr == "f8/live/svc/engine/instances/**"
            assert timeout == 0.25
            return _Replies()

    session = SimpleNamespace(liveliness=lambda: _Liveliness())
    zenoh_module = SimpleNamespace(ZError=_ZError)

    result = query_service_liveliness_instances_sync(
        zenoh_module=zenoh_module,
        session=session,
        service_id="engine",
        timeout_s=0.25,
    )

    assert result.query_ok is True
    assert result.instances == {"inst1"}
    assert result.error is None


def test_query_service_liveliness_instances_sync_reports_query_errors() -> None:
    class _ZError(Exception):
        pass

    class _Replies:
        def try_recv(self) -> object:
            raise _ZError("router unavailable")

    class _Liveliness:
        def get(self, key_expr: str, *, timeout: float) -> _Replies:
            _ = (key_expr, timeout)
            return _Replies()

    session = SimpleNamespace(liveliness=lambda: _Liveliness())
    zenoh_module = SimpleNamespace(ZError=_ZError)

    result = query_service_liveliness_instances_sync(
        zenoh_module=zenoh_module,
        session=session,
        service_id="engine",
        timeout_s=0.25,
    )

    assert result.query_ok is False
    assert result.instances == set()
    assert isinstance(result.error, _ZError)


def test_zenoh_liveliness_reply_channel_drained_detection() -> None:
    assert is_zenoh_liveliness_reply_channel_drained(RuntimeError("channel is empty and closed")) is True
    assert is_zenoh_liveliness_reply_channel_drained(RuntimeError("router unavailable")) is False

from __future__ import annotations

import enum
from types import SimpleNamespace

import pytest

from f8pystudio.ui.dialogs.schema_builder_dialog import schema_from_json_obj
from f8pystudio.ui.support import node_property_support as support


class _BoomNode:
    @property
    def spec(self) -> object:
        raise RuntimeError("spec unavailable")


class _BoomModelNode:
    @property
    def model(self) -> object:
        raise RuntimeError("model unavailable")


class _Color(enum.Enum):
    RED = "red"


def test_get_node_spec_logs_failed_access(monkeypatch: pytest.MonkeyPatch) -> None:
    debug_messages: list[str] = []

    def _debug(message: str, *args: object, **kwargs: object) -> None:
        assert kwargs.get("exc_info") is True
        debug_messages.append(str(message) % args)

    monkeypatch.setattr(support.logger, "debug", _debug)

    assert support.get_node_spec(_BoomNode()) is None
    assert debug_messages == ["Failed to read node spec for property panel."]


def test_node_missing_lock_info_logs_failed_model_access(monkeypatch: pytest.MonkeyPatch) -> None:
    debug_messages: list[str] = []

    def _debug(message: str, *args: object, **kwargs: object) -> None:
        assert kwargs.get("exc_info") is True
        debug_messages.append(str(message) % args)

    monkeypatch.setattr(support.logger, "debug", _debug)

    assert support.node_missing_lock_info(_BoomModelNode()) == (False, "")
    assert debug_messages == ["Failed to read node model for missing-lock info."]


def test_to_jsonable_converts_enum_values_explicitly() -> None:
    assert support.to_jsonable({"color": _Color.RED}) == {"color": "red"}


def test_to_jsonable_logs_dump_failure_and_uses_string(monkeypatch: pytest.MonkeyPatch) -> None:
    debug_messages: list[str] = []

    def _debug(message: str, *args: object, **kwargs: object) -> None:
        assert kwargs.get("exc_info") is True
        debug_messages.append(str(message) % args)

    def _raise_dump(value: object, *args: object, **kwargs: object) -> object:
        _ = value
        _ = args
        _ = kwargs
        raise TypeError("cannot dump")

    value = SimpleNamespace(name="sample")
    monkeypatch.setattr(support.logger, "debug", _debug)
    monkeypatch.setattr(support, "dump_json", _raise_dump)

    assert support.to_jsonable(value) == "namespace(name='sample')"
    assert debug_messages == ["Failed to convert value to JSON-compatible payload for property panel."]


def test_schema_to_json_obj_loose_reports_strict_schema_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    exception_messages: list[str] = []

    def _exception(message: str, *args: object, **kwargs: object) -> None:
        _ = kwargs
        exception_messages.append(str(message) % args)

    def _raise_schema_to_json(schema: object) -> dict[str, object]:
        _ = schema
        raise ValueError("schema serialization failed")

    schema = schema_from_json_obj({"type": "string"})
    monkeypatch.setattr(support.logger, "exception", _exception)
    monkeypatch.setattr(support, "schema_to_json_obj", _raise_schema_to_json)

    assert support.schema_to_json_obj_loose(schema) is None
    assert exception_messages == ["strict schema_to_json_obj failed for F8DataTypeSchema"]

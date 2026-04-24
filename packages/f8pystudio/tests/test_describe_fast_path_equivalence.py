from __future__ import annotations

from pathlib import Path
from typing import Any

from f8pysdk._specs.builtin_fields import normalize_describe_payload_dict
from f8pysdk.codec import dump_json, validate_as
from f8pysdk.monitoring import validate_describe_monitor_contract
from f8pysdk.service_runtime_tools.inventory.describe import describe_entry, read_static_describe_payload
from f8pysdk.service_runtime_tools.inventory.entry import load_service_entry
from f8pysdk.specs import F8ServiceDescribe


def _generic_describe_payload(payload: dict[str, Any], launch: object) -> dict[str, Any]:
    data = normalize_describe_payload_dict(dict(payload))
    validate_describe_monitor_contract(data)
    validated = validate_as(F8ServiceDescribe, data)
    out = dump_json(validated, mode="json")
    assert isinstance(out, dict)
    service_payload = out.get("service")
    assert isinstance(service_payload, dict)
    if not service_payload.get("launch"):
        service_payload["launch"] = dump_json(launch, mode="json")
    return out


def _describe_payload_for(service_dir: str) -> tuple[Path, dict[str, Any]]:
    path = Path(service_dir)
    entry = load_service_entry(path)
    payload, _source = read_static_describe_payload(path, entry)
    assert payload is not None
    return path, payload


def test_static_describe_entry_matches_generic_validation_for_python_service() -> None:
    service_dir, payload = _describe_payload_for("services/f8/engine")
    entry = load_service_entry(service_dir)

    assert describe_entry(service_dir, entry, initial_payload=payload) == _generic_describe_payload(payload, entry.launch)


def test_static_describe_entry_matches_generic_validation_for_cpp_service() -> None:
    service_dir, payload = _describe_payload_for("services/f8/cvkit/template_match")
    entry = load_service_entry(service_dir)

    assert describe_entry(service_dir, entry, initial_payload=payload) == _generic_describe_payload(payload, entry.launch)


def test_load_service_entry_matches_expected_python_path_absolutization() -> None:
    service_dir = Path("services/f8/engine")
    entry = load_service_entry(service_dir)
    launch_payload = dump_json(entry.launch, mode="json")

    assert launch_payload["workdir"] == str(Path(".").resolve())
    assert launch_payload["command"] == "pixi"
    assert launch_payload["args"] == ["run", "-e", "default", "f8pyengine"]
    assert launch_payload["env"] == {}


def test_load_service_entry_matches_expected_relative_binary_resolution() -> None:
    service_dir = Path("services/f8/cvkit/template_match")
    entry = load_service_entry(service_dir)
    launch_payload = dump_json(entry.launch, mode="json")

    expected_workdir = service_dir.resolve()
    expected_command = (expected_workdir / "../linux/f8cvkit_template_match_service").resolve()
    assert launch_payload["workdir"] == str(expected_workdir)
    assert launch_payload["command"] == str(expected_command)
    assert launch_payload["args"] == []
    assert launch_payload["env"] == {}

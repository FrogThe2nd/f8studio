from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from f8pysdk.codec import dump_json
from f8pysdk.specs import F8OperatorSpec

from f8pyengine.operators.buttplug_out import ButtplugOutRuntimeNode
from f8pyengine.operators.handy_out import HandyOutRuntimeNode
from f8pyengine.operators.lovense_out import LovenseOutRuntimeNode
from f8pyengine.operators.serial_out import SerialOutRuntimeNode
from f8pyengine.operators.tcode import TCodeRuntimeNode


REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO_ROOT / "packages" / "f8pystudio" / "f8pystudio" / "resources" / "components"
PUBLISHED_AT = "2026-07-21T00:00:00Z"


@dataclass(frozen=True)
class OfficialComponentDefinition:
    filename: str
    component_id: str
    node_id: str
    name: str
    description: str
    tags: tuple[str, ...]
    node_name: str
    operator_spec: F8OperatorSpec
    custom: dict[str, Any]


def _definitions() -> tuple[OfficialComponentDefinition, ...]:
    shared_tags = ("official", "distribution:bundled", "level:starter")
    return (
        OfficialComponentDefinition(
            filename="position-to-lovense.json",
            component_id="f8.official.component.position-to-lovense",
            node_id="official_lovense_out",
            name="Position to Lovense",
            description=(
                "Lovense position output tail. Connect a normalized 0..1 signal to position and execution "
                "to sendPositionCmd. Configure the local API, then explicitly enable output."
            ),
            tags=("role:output", "signal:position", "protocol:lovense", *shared_tags),
            node_name="Lovense Output (Disabled)",
            operator_spec=LovenseOutRuntimeNode.SPEC,
            custom={"enabled": False, "defaultToy": ""},
        ),
        OfficialComponentDefinition(
            filename="position-to-buttplug.json",
            component_id="f8.official.component.position-to-buttplug",
            node_id="official_buttplug_out",
            name="Position to Buttplug",
            description=(
                "Buttplug/Intiface position output tail. Connect a normalized 0..1 signal to position and "
                "execution to sendPositionCmd. Select a device, then explicitly enable output."
            ),
            tags=("role:output", "signal:position", "protocol:buttplug", *shared_tags),
            node_name="Buttplug Output (Disabled)",
            operator_spec=ButtplugOutRuntimeNode.SPEC,
            custom={"enabled": False, "selectedDevice": ""},
        ),
        OfficialComponentDefinition(
            filename="position-to-handy.json",
            component_id="f8.official.component.position-to-handy",
            node_id="official_handy_out",
            name="Position to Handy",
            description=(
                "The Handy position output tail. Connect a normalized 0..1 signal to value and execution "
                "to exec. Enter the connection key, then explicitly enable output."
            ),
            tags=("role:output", "signal:position", "protocol:handy", *shared_tags),
            node_name="Handy Output (Disabled)",
            operator_spec=HandyOutRuntimeNode.SPEC,
            custom={"enabled": False, "connectionKey": ""},
        ),
        OfficialComponentDefinition(
            filename="tcode-to-serial.json",
            component_id="f8.official.component.tcode-to-serial",
            node_id="official_serial_out",
            name="TCode to Serial",
            description=(
                "TCode serial transport tail. Connect a TCode string to value and execution to exec. "
                "Choose the serial port, then explicitly enable output."
            ),
            tags=("role:output", "signal:tcode", "protocol:tcode", "protocol:serial", *shared_tags),
            node_name="TCode Serial Output (Disabled)",
            operator_spec=SerialOutRuntimeNode.SPEC,
            custom={"enabled": False, "port": ""},
        ),
        OfficialComponentDefinition(
            filename="position-to-tcode.json",
            component_id="f8.official.component.position-to-tcode",
            node_id="official_tcode_encoder",
            name="Position to TCode",
            description=(
                "TCode v0.3 encoder. Connect normalized 0..1 motion to L0 or another axis and use the "
                "tcode output with a serial or network transport."
            ),
            tags=("role:shape", "signal:position", "protocol:tcode", *shared_tags),
            node_name="TCode Encoder",
            operator_spec=TCodeRuntimeNode.SPEC,
            custom={"intervalMs": 20},
        ),
    )


def _component_payload(definition: OfficialComponentDefinition) -> dict[str, Any]:
    spec_payload = dump_json(definition.operator_spec, mode="json")
    if not isinstance(spec_payload, dict):
        raise TypeError(f"operator spec did not serialize to an object: {definition.operator_spec.operatorClass}")
    node_type = f"{definition.operator_spec.serviceClass}.{definition.operator_spec.operatorClass}"
    content = {
        "schemaVersion": "f8studio-session/1",
        "layout": {
            "graph": {
                "layout_direction": 0,
                "acyclic": True,
                "pipe_collision": False,
                "pipe_slicing": True,
                "pipe_style": 1,
                "accept_connection_types": "{}",
                "reject_connection_types": "{}",
            },
            "nodes": {
                definition.node_id: {
                    "type_": node_type,
                    "name": definition.node_name,
                    "disabled": False,
                    "selected": False,
                    "visible": True,
                    "pos": [0.0, 0.0],
                    "layout_direction": 0,
                    "f8_spec": spec_payload,
                    "f8_sys": {},
                    "f8_ui_overrides": {},
                    "f8_ui_state": {},
                    "custom": dict(definition.custom),
                }
            },
            "connections": [],
            "f8_layers": [
                {
                    "id": "base",
                    "label": "Base",
                    "description": "Default base layer for unassigned nodes.",
                    "color": "#64748B",
                    "defaultVisible": True,
                    "isBase": True,
                }
            ],
        },
    }
    return {
        "componentId": definition.component_id,
        "assetType": "component",
        "versionNumber": 1,
        "record": {
            "componentId": definition.component_id,
            "name": definition.name,
            "description": definition.description,
            "tags": list(definition.tags),
            "content": content,
            "createdAt": PUBLISHED_AT,
            "updatedAt": PUBLISHED_AT,
        },
    }


def _serialized_assets() -> dict[str, str]:
    return {
        definition.filename: json.dumps(
            _component_payload(definition),
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n"
        for definition in _definitions()
    }


def _check_assets(expected: dict[str, str]) -> int:
    existing_names = {path.name for path in OUTPUT_DIR.glob("*.json")}
    expected_names = set(expected)
    mismatches: list[str] = []
    for filename, expected_text in expected.items():
        path = OUTPUT_DIR / filename
        if not path.is_file() or path.read_text(encoding="utf-8") != expected_text:
            mismatches.append(filename)
    mismatches.extend(sorted(existing_names - expected_names))
    if mismatches:
        print("Official components are out of date:")
        for filename in sorted(set(mismatches)):
            print(f"- {filename}")
        return 1
    print(f"Official components are current ({len(expected)} assets).")
    return 0


def _write_assets(expected: dict[str, str]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for old_path in OUTPUT_DIR.glob("*.json"):
        if old_path.name not in expected:
            old_path.unlink()
    for filename, text in expected.items():
        (OUTPUT_DIR / filename).write_text(text, encoding="utf-8")
    print(f"Wrote {len(expected)} official components to {OUTPUT_DIR}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate bundled official F8Studio components.")
    parser.add_argument("--check", action="store_true", help="Fail when generated assets differ from the repository.")
    args = parser.parse_args()
    expected = _serialized_assets()
    if args.check:
        return _check_assets(expected)
    _write_assets(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

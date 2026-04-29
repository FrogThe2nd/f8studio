from __future__ import annotations

import json
from pathlib import Path
import uuid

from f8pystudio.app.debug_monaco_editor import load_session_editor_target, load_session_editor_targets


def _editable_policy(*collections: str) -> dict[str, object]:
    policy = {"canAdd": True, "canDelete": True, "canEditExisting": True}
    return {name: dict(policy) for name in collections}


def _write_session(tmp_path: Path) -> Path:
    payload = {
        "schemaVersion": "f8studio-session/1",
        "layout": {
            "nodes": {
                "other": {
                    "f8_spec": {
                        "schemaVersion": "f8operator/1",
                        "serviceClass": "f8.other",
                        "operatorClass": "f8.other_op",
                        "label": "Other",
                        "rendererClass": "default_op",
                        "version": "0.0.1",
                        "description": "",
                        "tags": [],
                        "stateFields": [],
                        "editPolicy": {},
                        "execInPorts": [],
                        "execOutPorts": [],
                        "dataInPorts": [],
                        "dataOutPorts": [],
                    }
                },
                "nodeA": {
                    "f8_spec": {
                        "schemaVersion": "f8operator/1",
                        "serviceClass": "f8.pyengine",
                        "operatorClass": "f8.python_script",
                        "label": "Python Script",
                        "rendererClass": "default_op",
                        "version": "0.0.1",
                        "description": "",
                        "tags": ["script"],
                        "stateFields": [
                            {
                                "name": "code",
                                "valueSchema": {"type": "string", "default": "print('default')\n"},
                                "access": "rw",
                                "required": True,
                                "uiControl": "code[python]",
                                "showOnNode": False,
                                "editorAssist": {
                                    "version": 1,
                                    "language": "python",
                                    "python": {
                                        "support_files": {
                                            "f8_script_api.pyi": (
                                                "from f8_dynamic_inputs import F8Inputs\n"
                                                "from f8_dynamic_states import F8States\n"
                                                "class F8PyEngineContext:\n"
                                                "    states: F8States\n"
                                            )
                                        },
                                        "overlay_prefix": "from f8_script_api import F8PyEngineContext\n",
                                        "dynamic_bindings": {
                                            "inputs": {
                                                "enabled": True,
                                                "source": "data_in_ports",
                                                "type_name": "F8Inputs",
                                                "module_name": "f8_dynamic_inputs",
                                                "schema_mode": "basic_recursive",
                                                "access_mode": "object_and_mapping",
                                            },
                                            "states": {
                                                "enabled": True,
                                                "source": "state_fields",
                                                "type_name": "F8States",
                                                "module_name": "f8_dynamic_states",
                                                "schema_mode": "basic_recursive",
                                                "access_mode": "object_and_mapping",
                                            },
                                        },
                                    },
                                },
                            },
                            {
                                "name": "lastError",
                                "valueSchema": {"type": "string", "default": ""},
                                "access": "wo",
                                "required": True,
                            },
                        ],
                        "editPolicy": _editable_policy(
                            "stateFields",
                            "execInPorts",
                            "execOutPorts",
                            "dataInPorts",
                            "dataOutPorts",
                        ),
                        "execInPorts": [],
                        "execOutPorts": [],
                        "dataInPorts": [
                            {
                                "name": "track",
                                "valueSchema": {
                                    "type": "object",
                                    "properties": {"frameId": {"type": "integer"}},
                                    "required": [],
                                    "additionalProperties": False,
                                },
                                "required": True,
                            }
                        ],
                        "dataOutPorts": [],
                    },
                    "custom": {"code": "print('live')\n"},
                },
                "nodeB": {
                    "f8_spec": {
                        "schemaVersion": "f8operator/1",
                        "serviceClass": "f8.pyengine",
                        "operatorClass": "f8.python_script",
                        "label": "Python Script B",
                        "rendererClass": "default_op",
                        "version": "0.0.1",
                        "description": "",
                        "tags": ["script"],
                        "stateFields": [
                            {
                                "name": "code",
                                "valueSchema": {"type": "string", "default": "print('default b')\n"},
                                "access": "rw",
                                "required": True,
                                "uiControl": "code[python]",
                                "showOnNode": False,
                                "editorAssist": {
                                    "version": 1,
                                    "language": "python",
                                    "python": {
                                        "support_files": {
                                            "f8_script_api.pyi": (
                                                "from f8_dynamic_inputs import F8Inputs\n"
                                                "class F8PyEngineContext:\n"
                                                "    ...\n"
                                            )
                                        },
                                        "overlay_prefix": "from f8_script_api import *\n",
                                    },
                                },
                            }
                        ],
                        "editPolicy": _editable_policy(
                            "stateFields",
                            "execInPorts",
                            "execOutPorts",
                            "dataInPorts",
                            "dataOutPorts",
                        ),
                        "execInPorts": [],
                        "execOutPorts": [],
                        "dataInPorts": [],
                        "dataOutPorts": [],
                    },
                    "custom": {"code": "print('live b')\n"},
                },
            }
        },
    }
    path = tmp_path / "session.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_session_editor_target_reads_matching_node_and_code() -> None:
    temp_dir = Path(".tmp") / "test_monaco_editor_debug" / uuid.uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    path = _write_session(temp_dir)

    target = load_session_editor_target(path)

    assert target.node_id == "nodeA"
    assert target.spec.operatorClass == "f8.python_script"
    assert target.spec.serviceClass == "f8.pyengine"
    assert target.code == "print('live')\n"
    assert target.context.language == "python"
    assert target.context.dynamic_inputs_binding is not None
    assert target.context.dynamic_states_binding is not None
    assert tuple(port.name for port in target.context.data_in_ports) == ("track",)
    assert tuple(field.name for field in target.context.state_fields) == ("code",)


def test_load_session_editor_target_falls_back_to_state_default() -> None:
    temp_dir = Path(".tmp") / "test_monaco_editor_debug" / uuid.uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    path = _write_session(temp_dir)
    payload = json.loads(path.read_text(encoding="utf-8"))
    node = payload["layout"]["nodes"]["nodeA"]
    del node["custom"]["code"]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    target = load_session_editor_target(path)

    assert target.code == "print('default')\n"


def test_load_session_editor_targets_returns_all_matching_nodes() -> None:
    temp_dir = Path(".tmp") / "test_monaco_editor_debug" / uuid.uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    path = _write_session(temp_dir)

    targets = load_session_editor_targets(path)

    assert [target.node_id for target in targets] == ["nodeA", "nodeB"]
    assert [target.code for target in targets] == ["print('live')\n", "print('live b')\n"]

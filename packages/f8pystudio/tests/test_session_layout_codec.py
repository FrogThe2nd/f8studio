from __future__ import annotations

from f8pysdk.codec import dump_json
from copy import deepcopy
from types import SimpleNamespace

from f8pysdk.specs import (
    F8Command,
    F8DataPortSpec,
    F8ServiceSpec,
    F8SpecEditPolicy,
    F8StateAccess,
    F8StateFieldEditPolicy,
    F8StateSpec,
)
from f8pysdk.specs import (
    any_schema,
    editable_collection_edit_policy,
    number_schema,
    string_schema,
)
from f8pystudio.nodegraph.layers import F8LayerDef
from f8pystudio.nodegraph.session_layout_codec import SessionLayoutCodecMixin
from NodeGraphQt import NodeGraph
from unittest.mock import patch


def _service_spec_payload(
    *,
    service_class: str,
    data_in: list[str] | None = None,
    data_out: list[str] | None = None,
    commands: list[str] | None = None,
) -> dict:
    spec = F8ServiceSpec(
        serviceClass=service_class,
        label="Service",
        dataInPorts=[F8DataPortSpec(name=name, valueSchema=number_schema()) for name in list(data_in or [])],
        dataOutPorts=[F8DataPortSpec(name=name, valueSchema=any_schema()) for name in list(data_out or [])],
        commands=[F8Command(name=name, params=[]) for name in list(commands or [])],
    )
    return dump_json(spec, mode="json")


def test_strip_port_restore_data_removes_port_restore_keys() -> None:
    layout = {
        "nodes": {
            "svc.a": {
                "port_deletion_allowed": True,
                "input_ports": [{"name": "[D]x"}],
                "output_ports": [{"name": "y[D]"}],
            }
        }
    }

    out = SessionLayoutCodecMixin._strip_port_restore_data(deepcopy(layout))

    assert "port_deletion_allowed" not in out["nodes"]["svc.a"]
    assert "input_ports" not in out["nodes"]["svc.a"]
    assert "output_ports" not in out["nodes"]["svc.a"]


def test_inject_node_ids_sets_missing_id_fields() -> None:
    layout = {"nodes": {"svc.a": {"type_": "svc.a.type"}, "svc.b": {"id": "keep"}}}

    SessionLayoutCodecMixin._inject_node_ids(layout)

    assert layout["nodes"]["svc.a"]["id"] == "svc.a"
    assert layout["nodes"]["svc.b"]["id"] == "keep"


def test_strip_unknown_session_custom_properties_keeps_state_fields_only() -> None:
    layout = {
        "nodes": {
            "svc.a": {
                "f8_spec": {
                    "stateFields": [{"name": "gain"}, {"name": "enabled"}],
                },
                "custom": {
                    "gain": 0.5,
                    "enabled": True,
                    "unexpected": "drop",
                },
            }
        }
    }

    out = SessionLayoutCodecMixin._strip_unknown_session_custom_properties(deepcopy(layout))

    assert out["nodes"]["svc.a"]["custom"] == {"gain": 0.5, "enabled": True}


def test_merge_session_specs_respects_explicit_required_state_value_schema_lock() -> None:
    class _Node:
        SPEC_TEMPLATE = F8ServiceSpec(
            serviceClass="f8.locked",
            label="Locked",
            editPolicy=F8SpecEditPolicy(stateFields=editable_collection_edit_policy()),
            stateFields=[
                F8StateSpec(
                    name="builtin",
                    valueSchema=string_schema(),
                    access=F8StateAccess.ro,
                    required=True,
                    editPolicy=F8StateFieldEditPolicy(canEditValueSchema=False),
                )
            ],
        )

    codec = SessionLayoutCodecMixin.__new__(SessionLayoutCodecMixin)
    codec._node_factory = SimpleNamespace(nodes={"svc.locked": _Node})
    session_spec = F8ServiceSpec(
        serviceClass="f8.locked",
        label="Locked",
        stateFields=[
            F8StateSpec(
                name="builtin",
                valueSchema=number_schema(),
                access=F8StateAccess.ro,
                required=True,
            )
        ],
    )
    layout = {
        "nodes": {
            "svc1": {
                "type_": "svc.locked",
                "f8_spec": dump_json(session_spec, mode="json"),
            }
        }
    }

    out = codec._merge_session_specs(deepcopy(layout))
    merged_spec = out["nodes"]["svc1"]["f8_spec"]

    assert merged_spec["stateFields"][0]["valueSchema"]["type"] == "string"
    assert merged_spec["stateFields"][0]["required"] is True
    assert merged_spec["stateFields"][0]["editPolicy"] == {"canEditValueSchema": False}


def test_merge_session_specs_preserves_required_rw_state_structure_and_value_schema_edits() -> None:
    class _Node:
        SPEC_TEMPLATE = F8ServiceSpec(
            serviceClass="f8.editable",
            label="Editable",
            editPolicy=F8SpecEditPolicy(stateFields=editable_collection_edit_policy()),
            stateFields=[
                F8StateSpec(
                    name="value",
                    valueSchema=number_schema(minimum=0.0, maximum=1.0),
                    access=F8StateAccess.rw,
                    required=True,
                )
            ],
        )

    codec = SessionLayoutCodecMixin.__new__(SessionLayoutCodecMixin)
    codec._node_factory = SimpleNamespace(nodes={"svc.editable": _Node})
    session_spec = F8ServiceSpec(
        serviceClass="f8.editable",
        label="Editable",
        stateFields=[
            F8StateSpec(
                name="value",
                valueSchema=number_schema(minimum=-1.0, maximum=2.0),
                access=F8StateAccess.ro,
                required=False,
            )
        ],
    )
    layout = {
        "nodes": {
            "svc1": {
                "type_": "svc.editable",
                "f8_spec": dump_json(session_spec, mode="json"),
            }
        }
    }

    out = codec._merge_session_specs(deepcopy(layout))
    merged_spec = out["nodes"]["svc1"]["f8_spec"]

    assert merged_spec["stateFields"][0]["valueSchema"]["minimum"] == -1.0
    assert merged_spec["stateFields"][0]["valueSchema"]["maximum"] == 2.0
    assert merged_spec["stateFields"][0]["access"] == "ro"
    assert merged_spec["stateFields"][0]["required"] is False


def test_merge_session_specs_allows_deleting_required_state_when_policy_allows() -> None:
    class _Node:
        SPEC_TEMPLATE = F8ServiceSpec(
            serviceClass="f8.required.delete",
            label="Required Delete",
            editPolicy=F8SpecEditPolicy(stateFields=editable_collection_edit_policy()),
            stateFields=[
                F8StateSpec(
                    name="value",
                    valueSchema=number_schema(),
                    access=F8StateAccess.rw,
                    required=True,
                )
            ],
        )

    codec = SessionLayoutCodecMixin.__new__(SessionLayoutCodecMixin)
    codec._node_factory = SimpleNamespace(nodes={"svc.required.delete": _Node})
    session_spec = F8ServiceSpec(
        serviceClass="f8.required.delete",
        label="Required Delete",
        stateFields=[],
    )
    layout = {
        "nodes": {
            "svc1": {
                "type_": "svc.required.delete",
                "f8_spec": dump_json(session_spec, mode="json"),
            }
        }
    }

    out = codec._merge_session_specs(deepcopy(layout))
    merged_spec = out["nodes"]["svc1"]["f8_spec"]

    assert merged_spec["stateFields"] == []


def test_merge_session_specs_keeps_identity_locked_required_state() -> None:
    class _Node:
        SPEC_TEMPLATE = F8ServiceSpec(
            serviceClass="f8.required.locked",
            label="Required Locked",
            editPolicy=F8SpecEditPolicy(stateFields=editable_collection_edit_policy()),
            stateFields=[
                F8StateSpec(
                    name="value",
                    valueSchema=number_schema(),
                    access=F8StateAccess.rw,
                    required=True,
                    editPolicy=F8StateFieldEditPolicy(canRename=False),
                )
            ],
        )

    codec = SessionLayoutCodecMixin.__new__(SessionLayoutCodecMixin)
    codec._node_factory = SimpleNamespace(nodes={"svc.required.locked": _Node})
    session_spec = F8ServiceSpec(
        serviceClass="f8.required.locked",
        label="Required Locked",
        stateFields=[],
    )
    layout = {
        "nodes": {
            "svc1": {
                "type_": "svc.required.locked",
                "f8_spec": dump_json(session_spec, mode="json"),
            }
        }
    }

    out = codec._merge_session_specs(deepcopy(layout))
    merged_spec = out["nodes"]["svc1"]["f8_spec"]

    assert [field["name"] for field in merged_spec["stateFields"]] == ["value"]


def test_merge_session_specs_keeps_explicitly_locked_required_rw_state_schema() -> None:
    class _Node:
        SPEC_TEMPLATE = F8ServiceSpec(
            serviceClass="f8.lockedrw",
            label="Locked RW",
            editPolicy=F8SpecEditPolicy(stateFields=editable_collection_edit_policy()),
            stateFields=[
                F8StateSpec(
                    name="value",
                    valueSchema=string_schema(),
                    access=F8StateAccess.rw,
                    required=True,
                    editPolicy=F8StateFieldEditPolicy(canEditValueSchema=False),
                )
            ],
        )

    codec = SessionLayoutCodecMixin.__new__(SessionLayoutCodecMixin)
    codec._node_factory = SimpleNamespace(nodes={"svc.locked-rw": _Node})
    session_spec = F8ServiceSpec(
        serviceClass="f8.lockedrw",
        label="Locked RW",
        stateFields=[
            F8StateSpec(
                name="value",
                valueSchema=number_schema(),
                access=F8StateAccess.rw,
                required=True,
            )
        ],
    )
    layout = {
        "nodes": {
            "svc1": {
                "type_": "svc.locked-rw",
                "f8_spec": dump_json(session_spec, mode="json"),
            }
        }
    }

    out = codec._merge_session_specs(deepcopy(layout))
    merged_spec = out["nodes"]["svc1"]["f8_spec"]

    assert merged_spec["stateFields"][0]["valueSchema"]["type"] == "string"
    assert merged_spec["stateFields"][0]["required"] is True
    assert merged_spec["stateFields"][0]["editPolicy"] == {"canEditValueSchema": False}


def test_strip_invalid_connections_drops_nonexistent_ports() -> None:
    layout = {
        "nodes": {
            "svc.src": {
                "f8_spec": _service_spec_payload(service_class="f8.src", data_out=["out"]),
            },
            "svc.dst": {
                "f8_spec": _service_spec_payload(service_class="f8.dst", data_in=["in"]),
            },
        },
        "connections": [
            {"out": ["svc.src", "out[D]"], "in": ["svc.dst", "[D]in"]},
            {"out": ["svc.src", "missing[D]"], "in": ["svc.dst", "[D]in"]},
        ],
    }

    out = SessionLayoutCodecMixin._strip_invalid_connections(deepcopy(layout))

    assert out["connections"] == [{"out": ["svc.src", "out[D]"], "in": ["svc.dst", "[D]in"]}]


def test_strip_invalid_connections_keeps_command_ports() -> None:
    layout = {
        "nodes": {
            "svc.src": {
                "f8_spec": _service_spec_payload(service_class="f8.src", commands=["run"]),
            },
            "svc.dst": {
                "f8_spec": _service_spec_payload(service_class="f8.dst", commands=["apply"]),
            },
        },
        "connections": [
            {"out": ["svc.src", "run[C]"], "in": ["svc.dst", "[C]apply"]},
        ],
    }

    out = SessionLayoutCodecMixin._strip_invalid_connections(deepcopy(layout))

    assert out["connections"] == [{"out": ["svc.src", "run[C]"], "in": ["svc.dst", "[C]apply"]}]


def test_serialize_session_includes_f8_layers_top_level() -> None:
    class _Graph(SessionLayoutCodecMixin, NodeGraph):
        def session_layer_defs(self) -> tuple[F8LayerDef, ...]:
            return (
                F8LayerDef(id="base", label="Base", default_visible=True, is_base=True),
                F8LayerDef(id="logic", label="Logic", color="#112233", default_visible=False),
            )

    graph = _Graph.__new__(_Graph)
    raw_layout = {"nodes": {}, "connections": []}

    with patch.object(NodeGraph, "serialize_session", return_value=deepcopy(raw_layout)):
        out = graph.serialize_session()

    assert out["layout"]["f8_layers"] == [
        {
            "id": "base",
            "label": "Base",
            "description": "Default base layer for unassigned nodes.",
            "color": "#64748B",
            "defaultVisible": True,
            "isBase": True,
        },
        {
            "id": "logic",
            "label": "Logic",
            "description": "",
            "color": "#112233",
            "defaultVisible": False,
            "isBase": False,
        },
    ]


def test_serialize_session_preserves_base_default_visible_false() -> None:
    class _Graph(SessionLayoutCodecMixin, NodeGraph):
        def session_layer_defs(self) -> tuple[F8LayerDef, ...]:
            return (F8LayerDef(id="base", label="Base", default_visible=False, is_base=True),)

    graph = _Graph.__new__(_Graph)
    raw_layout = {"nodes": {}, "connections": []}

    with patch.object(NodeGraph, "serialize_session", return_value=deepcopy(raw_layout)):
        out = graph.serialize_session()

    assert out["layout"]["f8_layers"] == [
        {
            "id": "base",
            "label": "Base",
            "description": "Default base layer for unassigned nodes.",
            "color": "#64748B",
            "defaultVisible": False,
            "isBase": True,
        }
    ]

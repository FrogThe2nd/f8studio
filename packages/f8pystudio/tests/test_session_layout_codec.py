from __future__ import annotations

from f8pysdk.msgspec_codec import dump_json
from copy import deepcopy

from f8pysdk.generated import F8Command, F8DataPortSpec, F8ServiceSpec
from f8pysdk.schema_helpers import any_schema, number_schema
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
            "description": "Default compatibility layer for unassigned nodes.",
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
            return (
                F8LayerDef(id="base", label="Base", default_visible=False, is_base=True),
            )

    graph = _Graph.__new__(_Graph)
    raw_layout = {"nodes": {}, "connections": []}

    with patch.object(NodeGraph, "serialize_session", return_value=deepcopy(raw_layout)):
        out = graph.serialize_session()

    assert out["layout"]["f8_layers"] == [
        {
            "id": "base",
            "label": "Base",
            "description": "Default compatibility layer for unassigned nodes.",
            "color": "#64748B",
            "defaultVisible": False,
            "isBase": True,
        }
    ]

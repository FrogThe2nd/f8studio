from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from NodeGraphQt import NodeGraph
from f8pysdk.codec import dump_json
from f8pysdk.specs import (
    F8DataPortSpec,
    F8StateAccess,
    F8StateSpec,
    F8OperatorSchemaVersion,
    F8OperatorSpec,
    F8ServiceSchemaVersion,
    F8ServiceSpec,
)
from f8pysdk.specs import any_schema, boolean_schema, string_schema

from f8pystudio.nodegraph.node_graph import F8StudioGraph
from f8pystudio.nodegraph.missing_operator_basenode import F8StudioOperatorMissingNode
from f8pystudio.nodegraph.missing_service_basenode import F8StudioServiceMissingNode
from f8pystudio.app.program import PyStudioProgram
from f8pysdk.service_runtime_tools.inventory.catalog import ServiceCatalog

MISSING_SERVICE_NODE_TYPE = "svc.f8.missing.service"
MISSING_OPERATOR_NODE_TYPE = "svc.f8.missing.operator"


def _new_graph_with_registry(registered_types: dict[str, Any]) -> F8StudioGraph:
    graph = F8StudioGraph.__new__(F8StudioGraph)
    graph._node_factory = SimpleNamespace(nodes=dict(registered_types))
    return graph


def _service_spec_payload(service_class: str) -> dict[str, Any]:
    return dump_json(
        F8ServiceSpec(
            schemaVersion=F8ServiceSchemaVersion.f8service_1,
            serviceClass=service_class,
            version="0.0.1",
            label="Service",
        ),
        mode="json",
    )


def _operator_spec_payload(
    service_class: str,
    operator_class: str,
    *,
    exec_in: list[str] | None = None,
    exec_out: list[str] | None = None,
    data_in: list[str] | None = None,
    data_out: list[str] | None = None,
) -> dict[str, Any]:
    return dump_json(
        F8OperatorSpec(
            schemaVersion=F8OperatorSchemaVersion.f8operator_1,
            serviceClass=service_class,
            operatorClass=operator_class,
            version="0.0.1",
            label="Operator",
            execInPorts=list(exec_in or []),
            execOutPorts=list(exec_out or []),
            dataInPorts=[F8DataPortSpec(name=n, valueSchema=any_schema()) for n in list(data_in or [])],
            dataOutPorts=[F8DataPortSpec(name=n, valueSchema=any_schema()) for n in list(data_out or [])],
        ),
        mode="json",
    )


def test_coerce_missing_session_nodes_recovers_unknown_type() -> None:
    graph = _new_graph_with_registry(
        {
            "svc.f8.pyengine": object(),
            MISSING_SERVICE_NODE_TYPE: object(),
            MISSING_OPERATOR_NODE_TYPE: object(),
        }
    )
    layout = {
        "nodes": {
            "n1": {
                "type_": "svc.unknown.operator",
                "f8_spec": _operator_spec_payload("f8.pyengine", "f8.pyengine.op"),
            }
        }
    }
    out = graph._coerce_missing_session_nodes(layout)
    node = out["nodes"]["n1"]
    assert node["type_"] == MISSING_OPERATOR_NODE_TYPE
    assert node["f8_spec"]["operatorClass"] == "f8.pyengine.op"
    assert node["f8_sys"]["missingLocked"] is True
    assert node["f8_sys"]["missingType"] == "svc.unknown.operator"
    assert isinstance(node["f8_sys"]["missingSpec"], dict)


def test_restore_missing_session_nodes_recovers_original_type_spec() -> None:
    graph = _new_graph_with_registry(
        {
            MISSING_SERVICE_NODE_TYPE: object(),
            MISSING_OPERATOR_NODE_TYPE: object(),
        }
    )
    spec = _service_spec_payload("f8.audio")
    layout = {
        "nodes": {
            "svcA": {
                "type_": MISSING_SERVICE_NODE_TYPE,
                "f8_spec": _service_spec_payload("f8.missing"),
                "f8_sys": {
                    "missingLocked": True,
                    "missingType": "svc.f8.audio",
                    "missingSpec": spec,
                },
            }
        }
    }
    out = graph._restore_missing_session_nodes(layout)
    node = out["nodes"]["svcA"]
    assert node["type_"] == "svc.f8.audio"
    assert node["f8_spec"]["serviceClass"] == "f8.audio"


def test_serialize_session_restores_original_type_and_strips_missing_flags() -> None:
    graph = _new_graph_with_registry({})
    input_layout = {
        "nodes": {
            "svcA": {
                "type_": MISSING_SERVICE_NODE_TYPE,
                "name": "DenseFlow [Missing]",
                "f8_spec": _service_spec_payload("f8.missing"),
                "f8_sys": {
                    "svcId": "svcA",
                    "nodePurpose": "Keep this placeholder node labelled for operator recovery.",
                    "missingLocked": True,
                    "missingType": "svc.f8.cvkit.denseoptflow",
                    "missingReason": "unregistered node type",
                    "missingSpec": _service_spec_payload("f8.cvkit.denseoptflow"),
                    "missingOriginalName": "DenseFlow",
                },
            }
        }
    }

    with patch.object(NodeGraph, "serialize_session", return_value=input_layout):
        out = graph.serialize_session()

    node = out["layout"]["nodes"]["svcA"]
    assert node["type_"] == "svc.f8.cvkit.denseoptflow"
    assert node["f8_spec"]["serviceClass"] == "f8.cvkit.denseoptflow"
    assert node["name"] == "DenseFlow"
    assert node["f8_sys"]["svcId"] == "svcA"
    assert node["f8_sys"]["nodePurpose"] == "Keep this placeholder node labelled for operator recovery."
    assert "missingLocked" not in node["f8_sys"]
    assert "missingType" not in node["f8_sys"]
    assert "missingSpec" not in node["f8_sys"]
    assert "missingOriginalName" not in node["f8_sys"]


def test_serialize_publish_session_redacts_only_marked_state_values() -> None:
    graph = _new_graph_with_registry({})
    input_layout = {
        "nodes": {
            "svcA": {
                "type_": "svc.f8.implayer",
                "f8_spec": dump_json(
                    F8ServiceSpec(
                        schemaVersion=F8ServiceSchemaVersion.f8service_1,
                        serviceClass="f8.implayer",
                        version="0.0.1",
                        label="IM Player",
                        stateFields=[
                            F8StateSpec(
                                name="authCookiesFile",
                                valueSchema=string_schema(),
                                access=F8StateAccess.rw,
                                redactOnPublish=True,
                            ),
                            F8StateSpec(
                                name="showHud",
                                valueSchema=boolean_schema(default=True),
                                access=F8StateAccess.rw,
                            ),
                        ],
                    ),
                    mode="json",
                ),
                "custom": {
                    "authCookiesFile": "C:\\Users\\me\\cookies.txt",
                    "showHud": False,
                },
            }
        }
    }

    with patch.object(NodeGraph, "serialize_session", return_value=input_layout):
        normal = graph.serialize_session()
        published = graph.serialize_publish_session()

    normal_custom = normal["layout"]["nodes"]["svcA"]["custom"]
    publish_custom = published["layout"]["nodes"]["svcA"]["custom"]
    assert normal_custom["authCookiesFile"] == "C:\\Users\\me\\cookies.txt"
    assert normal_custom["showHud"] is False
    assert publish_custom["authCookiesFile"] == ""
    assert publish_custom["showHud"] is False
    assert published["layout"]["nodes"]["svcA"]["f8_spec"]["stateFields"][0]["redactOnPublish"] is True


def test_serialize_publish_session_uses_schema_default_when_redacting() -> None:
    graph = _new_graph_with_registry({})
    input_layout = {
        "nodes": {
            "svcA": {
                "type_": "svc.f8.cvkit.templatematch",
                "f8_spec": dump_json(
                    F8ServiceSpec(
                        schemaVersion=F8ServiceSchemaVersion.f8service_1,
                        serviceClass="f8.cvkit.templatematch",
                        version="0.0.1",
                        label="Template Match",
                        stateFields=[
                            F8StateSpec(
                                name="enabled",
                                valueSchema=boolean_schema(default=True),
                                access=F8StateAccess.rw,
                                redactOnPublish=True,
                            ),
                        ],
                    ),
                    mode="json",
                ),
                "custom": {"enabled": False},
            }
        }
    }

    with patch.object(NodeGraph, "serialize_session", return_value=input_layout):
        published = graph.serialize_publish_session()

    assert published["layout"]["nodes"]["svcA"]["custom"]["enabled"] is True


def test_serialize_session_strips_service_launch_from_persisted_specs() -> None:
    graph = _new_graph_with_registry({})
    input_layout = {
        "nodes": {
            "svcA": {
                "type_": "svc.f8.cvkit.tracker",
                "f8_spec": {
                    "schemaVersion": "f8service/1",
                    "specKind": "service",
                    "serviceClass": "f8.cvkit.tracker",
                    "version": "0.0.1",
                    "label": "Tracker",
                    "launch": {
                        "command": "H:\\Feel8\\f8studio\\services\\f8\\cvkit\\win\\f8cvkit_tracking_service.exe",
                        "args": [],
                        "env": {},
                        "workdir": "H:\\Feel8\\f8studio\\services\\f8\\cvkit\\tracking",
                    },
                },
            }
        }
    }

    with patch.object(NodeGraph, "serialize_session", return_value=input_layout):
        saved = graph.serialize_session()

    saved_spec = saved["layout"]["nodes"]["svcA"]["f8_spec"]
    assert saved_spec["serviceClass"] == "f8.cvkit.tracker"
    assert saved_spec["label"] == "Tracker"
    assert "launch" not in saved_spec


def test_serialize_session_drops_runtime_outputs_but_keeps_user_authored_wo_and_identity_fields() -> None:
    graph = _new_graph_with_registry({})
    input_layout = {
        "nodes": {
            "opA": {
                "type_": "svc.f8.pyengine.op",
                "f8_spec": dump_json(
                    F8OperatorSpec(
                        schemaVersion=F8OperatorSchemaVersion.f8operator_1,
                        serviceClass="f8.pyengine",
                        operatorClass="f8.test.persistence",
                        version="0.0.1",
                        label="Persistence Test",
                        stateFields=[
                            F8StateSpec(name="gain", valueSchema=any_schema(), access=F8StateAccess.rw),
                            F8StateSpec(name="preview", valueSchema=any_schema(), access=F8StateAccess.ro),
                            F8StateSpec(name="svcId", valueSchema=string_schema(default=""), access=F8StateAccess.ro),
                            F8StateSpec(name="sequence", valueSchema=any_schema(), access=F8StateAccess.wo),
                            F8StateSpec(name="lastError", valueSchema=string_schema(default=""), access=F8StateAccess.wo),
                        ],
                    ),
                    mode="json",
                ),
                "custom": {
                    "gain": 2.5,
                    "preview": {"value": 99},
                    "svcId": "svc_parent",
                    "sequence": {"points": [1, 2, 3]},
                    "lastError": "Traceback (most recent call last): ...",
                },
            }
        }
    }

    with patch.object(NodeGraph, "serialize_session", return_value=input_layout):
        saved = graph.serialize_session()

    saved_custom = saved["layout"]["nodes"]["opA"]["custom"]
    assert saved_custom["gain"] == 2.5
    assert saved_custom["svcId"] == "svc_parent"
    assert saved_custom["sequence"] == {"points": [1, 2, 3]}
    assert "preview" not in saved_custom
    assert "lastError" not in saved_custom


def test_build_node_classes_registers_missing_specialized_classes() -> None:
    ServiceCatalog.instance().clear()
    node_classes = PyStudioProgram.build_node_classes()
    by_type = {str(cls.type_): cls for cls in node_classes}
    assert MISSING_SERVICE_NODE_TYPE in by_type
    assert MISSING_OPERATOR_NODE_TYPE in by_type
    assert issubclass(by_type[MISSING_SERVICE_NODE_TYPE], F8StudioServiceMissingNode)
    assert issubclass(by_type[MISSING_OPERATOR_NODE_TYPE], F8StudioOperatorMissingNode)


def test_strip_unknown_session_custom_properties_keeps_only_exact_spec_names() -> None:
    layout = {
        "nodes": {
            "n1": {
                "type_": "svc.f8.pyengine.op",
                "f8_spec": _operator_spec_payload("f8.pyengine", "f8.pyengine.op"),
                "custom": {
                    "Event": "old-cased-key",
                    "event": "canonical",
                    "unused": 1,
                },
            }
        }
    }
    out = F8StudioGraph._strip_unknown_session_custom_properties(layout)
    assert out["nodes"]["n1"]["custom"] == {}


def test_strip_invalid_connections_drops_cross_service_exec_and_mixed_kind() -> None:
    layout = {
        "nodes": {
            "svc1": {"type_": "svc.f8.pyengine", "f8_spec": _service_spec_payload("f8.pyengine")},
            "svc2": {"type_": "svc.f8.pyengine", "f8_spec": _service_spec_payload("f8.pyengine")},
            "op1": {
                "type_": "svc.f8.pyengine.op",
                "f8_spec": _operator_spec_payload(
                    "f8.pyengine",
                    "f8.pyengine.op1",
                    exec_out=["next"],
                    data_out=["out"],
                ),
                "custom": {"svcId": "svc1"},
            },
            "op2": {
                "type_": "svc.f8.pyengine.op",
                "f8_spec": _operator_spec_payload(
                    "f8.pyengine",
                    "f8.pyengine.op2",
                    exec_in=["in"],
                    data_in=["din"],
                ),
                "custom": {"svcId": "svc2"},
            },
            "op3": {
                "type_": "svc.f8.pyengine.op",
                "f8_spec": _operator_spec_payload(
                    "f8.pyengine",
                    "f8.pyengine.op3",
                    exec_in=["in"],
                    data_in=["din"],
                ),
                "custom": {"svcId": "svc1"},
            },
        },
        "connections": [
            {"out": ["op1", "next[E]"], "in": ["op2", "[E]in"]},
            {"out": ["op1", "next[E]"], "in": ["op3", "[D]din"]},
            {"out": ["op1", "next[E]"], "in": ["op3", "[E]in"]},
            {"out": ["op1", "out[D]"], "in": ["op2", "[D]din"]},
        ],
    }
    out = F8StudioGraph._strip_invalid_connections(layout)
    assert out["connections"] == [
        {"out": ["op1", "next[E]"], "in": ["op3", "[E]in"]},
        {"out": ["op1", "out[D]"], "in": ["op2", "[D]din"]},
    ]

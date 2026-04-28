from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from f8pysdk.specs import F8OperatorSchemaVersion, F8OperatorSpec
from f8pysdk.codec import dump_json
from f8pystudio.nodegraph.node_graph import F8StudioGraph


MISSING_OPERATOR_NODE_TYPE = "svc.f8.missing.operator"


def _new_graph_with_registry(registered_types: dict[str, Any]) -> F8StudioGraph:
    graph = F8StudioGraph.__new__(F8StudioGraph)
    graph._node_factory = SimpleNamespace(nodes=dict(registered_types))
    return graph


def test_missing_plugin_viz_tcode_is_coerced_to_missing_operator_node() -> None:
    graph = _new_graph_with_registry({MISSING_OPERATOR_NODE_TYPE: object(), "svc.f8.pyengine": object()})

    layout = {
        "nodes": {
            "n1": {
                "type_": "f8.pystudio.f8.viz.tcode",
                "f8_spec": dump_json(
                    F8OperatorSpec(
                        schemaVersion=F8OperatorSchemaVersion.f8operator_1,
                        serviceClass="f8.pystudio",
                        operatorClass="f8.viz.tcode",
                        version="0.0.1",
                        label="TCodeViz",
                        rendererClass="viz_tcode",
                    ),
                    mode="json",
                ),
            }
        }
    }

    out = graph._coerce_missing_session_nodes(layout)
    node = out["nodes"]["n1"]
    assert node["type_"] == MISSING_OPERATOR_NODE_TYPE
    assert node["f8_sys"]["missingLocked"] is True
    assert node["f8_sys"]["missingType"] == "f8.pystudio.f8.viz.tcode"

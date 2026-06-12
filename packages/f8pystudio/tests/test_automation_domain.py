from __future__ import annotations

import pytest

from f8pystudio.automation.domain import (
    ConnectPortsOp,
    CreateNodeOp,
    DeleteNodeOp,
    DisconnectPortsOp,
    MoveNodeOp,
    SetNodeNameOp,
    SetNodeStateFieldsOp,
    SetNodeStateOp,
    SetUiOverrideOp,
    decode_graph_patch,
    graph_patch_to_dict,
)


def test_decode_graph_patch_all_explicit_ops() -> None:
    patch = decode_graph_patch(
        {
            "expectedRevision": 4,
            "label": "edit graph",
            "ops": [
                {"op": "createNode", "nodeType": "svc.demo.Demo", "nodeId": "n1", "pos": [1, 2]},
                {"op": "deleteNode", "nodeId": "old"},
                {"op": "connectPorts", "fromNodeId": "a", "fromPort": "out[D]", "toNodeId": "b", "toPort": "[D]in"},
                {
                    "op": "disconnectPorts",
                    "fromNodeId": "a",
                    "fromPort": "out[D]",
                    "toNodeId": "b",
                    "toPort": "[D]in",
                },
                {"op": "setNodeState", "nodeId": "n1", "field": "enabled", "value": True},
                {"op": "setNodeName", "nodeId": "n1", "name": "Readable"},
                {
                    "op": "setNodeStateFields",
                    "nodeId": "n1",
                    "stateFields": [{"name": "mode", "valueSchema": {"type": "string"}, "access": "rw"}],
                },
                {"op": "moveNode", "nodeId": "n1", "pos": [10.5, -2]},
                {"op": "setUiOverride", "nodeId": "n1", "key": "stateFields", "value": {"enabled": {"showOnNode": True}}},
            ],
        }
    )

    assert patch.expected_revision == 4
    assert isinstance(patch.ops[0], CreateNodeOp)
    assert isinstance(patch.ops[1], DeleteNodeOp)
    assert isinstance(patch.ops[2], ConnectPortsOp)
    assert isinstance(patch.ops[3], DisconnectPortsOp)
    assert isinstance(patch.ops[4], SetNodeStateOp)
    assert isinstance(patch.ops[5], SetNodeNameOp)
    assert isinstance(patch.ops[6], SetNodeStateFieldsOp)
    assert isinstance(patch.ops[7], MoveNodeOp)
    assert isinstance(patch.ops[8], SetUiOverrideOp)
    assert graph_patch_to_dict(patch)["ops"][0]["op"] == "createNode"


def test_decode_graph_patch_rejects_unknown_op() -> None:
    with pytest.raises(ValueError, match="unsupported op"):
        decode_graph_patch({"ops": [{"op": "doAnything"}]})


def test_decode_graph_patch_rejects_missing_required_fields() -> None:
    with pytest.raises(ValueError, match="nodeType"):
        decode_graph_patch({"ops": [{"op": "createNode"}]})


def test_decode_graph_patch_rejects_bad_revision() -> None:
    with pytest.raises(ValueError, match="expectedRevision"):
        decode_graph_patch({"expectedRevision": True, "ops": []})

from __future__ import annotations

from f8pystudio.nodegraph.insert_layout_utils import (
    IdRemapPlan,
    build_insert_id_remap,
    compute_layout_bbox,
    remap_insert_layout,
    shift_insert_layout_nodes,
)


def test_compute_layout_bbox_from_nodes() -> None:
    layout_data = {
        "nodes": {
            "n1": {"pos": [10, 20]},
            "n2": {"pos": [-5, 100]},
            "n3": {"pos": [7.5, -2]},
        }
    }

    bbox = compute_layout_bbox(layout_data)

    assert bbox.min_x == -5.0
    assert bbox.min_y == -2.0
    assert bbox.max_x == 10.0
    assert bbox.max_y == 100.0


def test_build_insert_id_remap_avoids_collisions() -> None:
    plan = build_insert_id_remap(
        ["svcA", "svcB", "svcA"],
        existing_node_ids={"svcA", "svcA_2", "svcB"},
    )

    assert plan.mapping["svcA"] == "svcA_4"
    assert plan.mapping["svcB"] == "svcB_2"


def test_remap_insert_layout_rewrites_identity_fields_and_connections() -> None:
    layout_data = {
        "nodes": {
            "svcA": {
                "id": "svcA",
                "custom": {"svcId": "svcA", "operatorId": "op1"},
                "f8_sys": {"svcId": "svcA"},
            },
            "op1": {"id": "op1", "custom": {"svcId": "svcA", "operatorId": "op1"}},
        },
        "connections": [
            {"out": ["svcA", "out0"], "in": ["op1", "in0"]},
            {"out": ["op1", "out1"], "in": ["svcA", "in1"]},
        ],
    }
    remap_plan = IdRemapPlan(mapping={"svcA": "svcA_2", "op1": "op1_2"})

    rewritten = remap_insert_layout(layout_data, remap_plan)

    assert set(rewritten["nodes"].keys()) == {"svcA_2", "op1_2"}
    svc = rewritten["nodes"]["svcA_2"]
    assert svc["id"] == "svcA_2"
    assert svc["custom"]["svcId"] == "svcA_2"
    assert svc["custom"]["operatorId"] == "op1_2"
    assert svc["f8_sys"]["svcId"] == "svcA_2"
    assert rewritten["connections"][0]["out"][0] == "svcA_2"
    assert rewritten["connections"][0]["in"][0] == "op1_2"
    assert rewritten["connections"][1]["out"][0] == "op1_2"
    assert rewritten["connections"][1]["in"][0] == "svcA_2"


def test_shift_insert_layout_nodes_moves_positions() -> None:
    layout_data = {
        "nodes": {
            "n1": {"pos": [10, 20]},
            "n2": {"pos": [-3, 4]},
        }
    }

    shift_insert_layout_nodes(layout_data, dx=5.5, dy=-2.0)

    assert layout_data["nodes"]["n1"]["pos"] == [15.5, 18.0]
    assert layout_data["nodes"]["n2"]["pos"] == [2.5, 2.0]

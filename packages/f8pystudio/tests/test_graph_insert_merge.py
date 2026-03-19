from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from NodeGraphQt import NodeGraph

from f8pystudio.nodegraph.node_graph import (
    F8StudioGraph,
    GraphBounds,
    GraphInsertRequest,
)


class _FakeNode:
    def __init__(self, node_id: str, *, view: Any | None = None) -> None:
        self.id = node_id
        self.selected = False
        self.view = view if view is not None else _FakeView()

    def set_property(self, name: str, value: Any, push_undo: bool = True) -> None:
        _ = push_undo
        if name == "selected":
            self.selected = bool(value)


class _FakeView:
    def __init__(self) -> None:
        self.draw_calls = 0
        self.sync_calls: list[bool] = []

    def draw_node(self) -> None:
        self.draw_calls += 1

    def sync_proxy_mode(self, *, force: bool = False) -> None:
        self.sync_calls.append(bool(force))


class _FakeViewer:
    def __init__(self) -> None:
        self.fit_calls = 0
        self.center_calls = 0
        self.refresh_calls: list[bool] = []

    def fitInView(self, *args: Any, **kwargs: Any) -> None:
        _ = (args, kwargs)
        self.fit_calls += 1

    def centerOn(self, *args: Any, **kwargs: Any) -> None:
        _ = (args, kwargs)
        self.center_calls += 1

    def refresh_auto_proxy_mode(self, *, force: bool = False) -> None:
        self.refresh_calls.append(bool(force))


def _new_graph_stub() -> F8StudioGraph:
    graph = F8StudioGraph.__new__(F8StudioGraph)
    graph._loading_session = False
    graph._viewer = None
    graph._inject_node_ids = lambda layout: None  # type: ignore[method-assign]
    graph._restore_missing_session_nodes = lambda layout: layout  # type: ignore[method-assign]
    graph._coerce_missing_session_nodes = lambda layout: layout  # type: ignore[method-assign]
    graph._merge_session_specs = lambda layout: layout  # type: ignore[method-assign]
    graph._strip_port_restore_data = lambda layout: layout  # type: ignore[method-assign]
    graph._strip_unknown_session_custom_properties = lambda layout: layout  # type: ignore[method-assign]
    graph._strip_invalid_connections = lambda layout: layout  # type: ignore[method-assign]
    graph._rebind_container_children = lambda: None  # type: ignore[method-assign]
    graph._refresh_all_inline_state_read_only = lambda: None  # type: ignore[method-assign]
    return graph


def _write_session(path: Path, *, layout: dict[str, Any], schema_version: str = "f8studio-session/1") -> Path:
    payload = {"schemaVersion": schema_version, "layout": layout}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def test_prepare_insert_graph_from_file_computes_bbox_and_counts(tmp_path: Path) -> None:
    graph = _new_graph_stub()
    graph.all_nodes = lambda: []  # type: ignore[method-assign]

    layout = {
        "nodes": {
            "svcA": {"id": "svcA", "pos": [10, 20]},
            "op1": {"id": "op1", "pos": [30, 5]},
        },
        "connections": [
            {"out": ["op1", "next[E]"], "in": ["svcA", "[E]in"]},
        ],
    }
    file_path = _write_session(tmp_path / "insert.json", layout=layout)
    request = graph.prepare_insert_graph_from_file(str(file_path))

    assert request.node_count == 2
    assert request.connection_count == 1
    assert request.source_bbox.min_x == 10.0
    assert request.source_bbox.min_y == 5.0
    assert request.source_bbox.max_x == 30.0
    assert request.source_bbox.max_y == 20.0


def test_prepare_insert_graph_from_file_rejects_invalid_schema(tmp_path: Path) -> None:
    graph = _new_graph_stub()
    graph.all_nodes = lambda: []  # type: ignore[method-assign]

    file_path = _write_session(tmp_path / "bad.json", layout={"nodes": {}}, schema_version="legacy")
    with pytest.raises(ValueError, match="unsupported session schemaVersion"):
        graph.prepare_insert_graph_from_file(str(file_path))


def test_prepare_insert_graph_from_file_accepts_utf8_bom_and_chinese(tmp_path: Path) -> None:
    graph = _new_graph_stub()
    graph.all_nodes = lambda: []  # type: ignore[method-assign]

    payload = {
        "schemaVersion": "f8studio-session/1",
        "layout": {
            "nodes": {
                "svc.zh": {
                    "id": "svc.zh",
                    "name": "中文服务",
                    "custom": {"label": "相机输入"},
                    "pos": [12, 34],
                }
            },
            "connections": [],
        },
    }
    file_path = tmp_path / "insert_utf8_bom_zh.json"
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8-sig")

    request = graph.prepare_insert_graph_from_file(str(file_path))

    assert request.node_count == 1
    assert request.source_bbox.min_x == 12.0
    assert request.source_bbox.min_y == 34.0


def test_build_insert_id_remap_uses_suffix_strategy() -> None:
    graph = _new_graph_stub()
    graph.all_nodes = lambda: [_FakeNode("cam"), _FakeNode("cam_2"), _FakeNode("op")]  # type: ignore[method-assign]

    remap_plan = graph._build_insert_id_remap(["cam", "op", "new"])
    assert remap_plan.mapping["cam"] == "cam_3"
    assert remap_plan.mapping["op"] == "op_2"
    assert remap_plan.mapping["new"] == "new"


def test_apply_insert_graph_remaps_ids_connections_identity_and_offsets() -> None:
    graph = _new_graph_stub()
    graph.all_nodes = lambda: [_FakeNode("svcA")]  # type: ignore[method-assign]

    request = GraphInsertRequest(
        source_path="x.json",
        layout_data={
            "nodes": {
                "svcA": {
                    "id": "svcA",
                    "pos": [100, 200],
                    "custom": {"svcId": "svcA"},
                    "f8_sys": {"svcId": "svcA"},
                },
                "op1": {
                    "id": "op1",
                    "pos": [140, 240],
                    "custom": {"svcId": "svcA", "operatorId": "op1"},
                },
            },
            "connections": [
                {"out": ["op1", "next[E]"], "in": ["svcA", "[E]in"]},
            ],
        },
        source_bbox=GraphBounds(100.0, 200.0, 140.0, 240.0),
        node_count=2,
        connection_count=1,
    )

    captured: dict[str, Any] = {}

    def _fake_deserialize(self, layout_data: dict[str, Any], clear_session: bool, clear_undo_stack: bool) -> None:
        captured["layout"] = deepcopy(layout_data)
        captured["clear_session"] = clear_session
        captured["clear_undo_stack"] = clear_undo_stack

    with patch.object(NodeGraph, "deserialize_session", new=_fake_deserialize):
        result = graph.apply_insert_graph(request, anchor_x=300.0, anchor_y=400.0)

    inserted_layout = captured["layout"]
    assert captured["clear_session"] is False
    assert captured["clear_undo_stack"] is False

    assert "svcA_2" in inserted_layout["nodes"]
    assert "op1" in inserted_layout["nodes"]

    svc_data = inserted_layout["nodes"]["svcA_2"]
    op_data = inserted_layout["nodes"]["op1"]
    assert svc_data["custom"]["svcId"] == "svcA_2"
    assert svc_data["f8_sys"]["svcId"] == "svcA_2"
    assert op_data["custom"]["svcId"] == "svcA_2"
    assert op_data["custom"]["operatorId"] == "op1"

    # Anchor at (300, 400) means dx=200, dy=200 for source min=(100, 200).
    assert svc_data["pos"] == [300.0, 400.0]
    assert op_data["pos"] == [340.0, 440.0]

    conn = inserted_layout["connections"][0]
    assert conn["out"][0] == "op1"
    assert conn["in"][0] == "svcA_2"

    assert result.id_remap_plan.mapping["svcA"] == "svcA_2"
    assert result.inserted_node_ids == ["svcA_2", "op1"]


def test_apply_insert_graph_preserves_view_and_redraws_inserted_nodes() -> None:
    graph = _new_graph_stub()
    existing_view = _FakeView()
    existing_node = _FakeNode("svcA", view=existing_view)
    all_nodes = [existing_node]
    graph.all_nodes = lambda: all_nodes  # type: ignore[method-assign]

    viewer = _FakeViewer()
    graph.viewer = lambda: viewer  # type: ignore[method-assign]

    request = GraphInsertRequest(
        source_path="x.json",
        layout_data={
            "nodes": {
                "svcA": {"id": "svcA", "pos": [100, 200]},
                "op1": {"id": "op1", "pos": [140, 240]},
            },
            "connections": [],
        },
        source_bbox=GraphBounds(100.0, 200.0, 140.0, 240.0),
        node_count=2,
        connection_count=0,
    )

    inserted_service_view = _FakeView()
    inserted_operator_view = _FakeView()

    def _fake_deserialize(self, layout_data: dict[str, Any], clear_session: bool, clear_undo_stack: bool) -> None:
        _ = (self, layout_data, clear_session, clear_undo_stack)
        all_nodes.append(_FakeNode("svcA_2", view=inserted_service_view))
        all_nodes.append(_FakeNode("op1", view=inserted_operator_view))

    with patch("f8pystudio.nodegraph.graph_insert_flow.F8StudioNodeViewer", _FakeViewer):
        with patch.object(NodeGraph, "deserialize_session", new=_fake_deserialize):
            result = graph.apply_insert_graph(request, anchor_x=300.0, anchor_y=400.0)

    assert result.inserted_node_ids == ["svcA_2", "op1"]
    assert viewer.fit_calls == 0
    assert viewer.center_calls == 0
    assert viewer.refresh_calls == [True]
    assert existing_view.draw_calls == 0
    assert existing_view.sync_calls == []
    assert inserted_service_view.draw_calls == 1
    assert inserted_service_view.sync_calls == [True]
    assert inserted_operator_view.draw_calls == 1
    assert inserted_operator_view.sync_calls == [True]

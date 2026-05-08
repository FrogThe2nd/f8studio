from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from NodeGraphQt import NodeGraph
from qtpy import QtWidgets

from f8pystudio.nodegraph.layers import F8LayerDef, normalize_layer_defs
from f8pystudio.nodegraph.node_graph import (
    F8StudioGraph,
    GraphBounds,
    GraphInsertRequest,
)
from f8pystudio.render_nodes.backdrop import BackdropRenderNode


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


class _RefreshControllerStub:
    def __init__(self) -> None:
        self.schedule_calls = 0

    def schedule_refresh(self) -> None:
        self.schedule_calls += 1

    def on_graph_property_changed(self, node: object, name: str, value: object) -> None:
        _ = (node, value)
        if str(name or "").strip() in {"f8_ui_state", "f8_spec"}:
            self.schedule_refresh()


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def _new_graph_stub() -> F8StudioGraph:
    graph = F8StudioGraph.__new__(F8StudioGraph)
    graph._loading_session = False
    graph._global_hotkey_controller = None
    graph._viewer = None
    graph._undo_calls: list[tuple[str, str]] = []
    graph._session_layer_defs = normalize_layer_defs(())
    graph._active_layer_ids = ("base",)
    graph.layers_changed = type("_Signal", (), {"emit": staticmethod(lambda *args: None)})()
    graph.active_layers_changed = type("_Signal", (), {"emit": staticmethod(lambda *args: None)})()
    graph.begin_undo = lambda name: graph._undo_calls.append(("begin", str(name)))  # type: ignore[method-assign]
    graph.end_undo = lambda: graph._undo_calls.append(("end", ""))  # type: ignore[method-assign]
    graph._inject_node_ids = lambda layout: None  # type: ignore[method-assign]
    graph._restore_missing_session_nodes = lambda layout: layout  # type: ignore[method-assign]
    graph._coerce_missing_session_nodes = lambda layout: layout  # type: ignore[method-assign]
    graph._merge_session_specs = lambda layout: layout  # type: ignore[method-assign]
    graph._strip_port_restore_data = lambda layout: layout  # type: ignore[method-assign]
    graph._strip_unknown_session_custom_properties = lambda layout: layout  # type: ignore[method-assign]
    graph._strip_invalid_connections = lambda layout: layout  # type: ignore[method-assign]
    graph._rebind_container_children = lambda: None  # type: ignore[method-assign]
    graph._refresh_all_inline_state_read_only = lambda: None  # type: ignore[method-assign]
    graph._emit_graph_inserted = lambda: None  # type: ignore[method-assign]
    graph.session_layer_defs = lambda: graph._session_layer_defs  # type: ignore[method-assign]
    graph.active_layer_ids = lambda: graph._active_layer_ids  # type: ignore[method-assign]
    graph.set_session_layer_defs = (  # type: ignore[method-assign]
        lambda defs, preserve_active, activate_layer_ids=None: (
            setattr(graph, "_session_layer_defs", tuple(defs)),
            setattr(
                graph,
                "_active_layer_ids",
                tuple(sorted(set(activate_layer_ids or graph._active_layer_ids))),
            ),
        )
    )
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
    assert graph._undo_calls == [('begin', 'insert graph: "x.json"'), ("end", "")]


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


def test_apply_insert_graph_schedules_global_hotkey_refresh_after_batch_insert() -> None:
    graph = _new_graph_stub()
    controller = _RefreshControllerStub()
    graph.set_global_hotkey_controller(controller)
    inserted_nodes: list[_FakeNode] = []
    graph.all_nodes = lambda: inserted_nodes  # type: ignore[method-assign]

    request = GraphInsertRequest(
        source_path="component:Controls",
        layout_data={
            "nodes": {
                "triggerA": {"id": "triggerA", "pos": [100, 200]},
            },
            "connections": [],
        },
        source_bbox=GraphBounds(100.0, 200.0, 100.0, 200.0),
        node_count=1,
        connection_count=0,
    )

    def _fake_deserialize(self, layout_data: dict[str, Any], clear_session: bool, clear_undo_stack: bool) -> None:
        _ = (self, layout_data, clear_session, clear_undo_stack)
        inserted_nodes.append(_FakeNode("triggerA"))

    with patch.object(NodeGraph, "deserialize_session", new=_fake_deserialize):
        result = graph.apply_insert_graph(request, anchor_x=300.0, anchor_y=400.0)

    assert result.inserted_node_ids == ["triggerA"]
    assert controller.schedule_calls == 1


def test_apply_insert_graph_emits_inserted_signal_after_batch_insert() -> None:
    _ensure_app()
    graph = F8StudioGraph()
    graph.node_factory.clear_registered_nodes()
    graph.node_factory.register_node(BackdropRenderNode)
    emitted_count = 0

    def _on_inserted() -> None:
        nonlocal emitted_count
        emitted_count += 1

    graph.graph_inserted.connect(_on_inserted)  # type: ignore[attr-defined]

    node_type = str(BackdropRenderNode.type_ or "")
    source = F8StudioGraph()
    source.node_factory.clear_registered_nodes()
    source.node_factory.register_node(BackdropRenderNode)
    _ = source.create_node(node_type, name="Source", selected=False, push_undo=False, pos=(10.0, 20.0))
    request = graph.prepare_insert_graph_from_component(source.serialize_publish_session(), component_name="Signal Test")

    result = graph.apply_insert_graph(request, anchor_x=300.0, anchor_y=400.0)

    assert result.inserted_node_ids
    assert emitted_count == 1


def test_load_session_payload_emits_session_loaded_signal() -> None:
    _ensure_app()
    graph = F8StudioGraph()
    graph.node_factory.clear_registered_nodes()
    graph.node_factory.register_node(BackdropRenderNode)
    emitted_count = 0

    def _on_loaded() -> None:
        nonlocal emitted_count
        emitted_count += 1

    graph.session_loaded.connect(_on_loaded)  # type: ignore[attr-defined]

    node_type = str(BackdropRenderNode.type_ or "")
    payload = {
        "schemaVersion": "f8studio-session/1",
        "layout": {
            "nodes": {
                "nodeA": {
                    "id": "nodeA",
                    "type_": node_type,
                    "name": "Loaded A",
                    "pos": [10.0, 20.0],
                },
            },
            "connections": [],
        },
    }

    graph.load_session_payload(payload)

    assert graph.get_node_by_id("nodeA") is not None
    assert emitted_count == 1


def test_apply_insert_graph_remaps_conflicting_layer_ids() -> None:
    graph = _new_graph_stub()
    graph._session_layer_defs = (
        F8LayerDef(id="base", label="Base", default_visible=True, is_base=True),
        F8LayerDef(id="logic", label="Local Logic", color="#112233", default_visible=True),
    )
    graph.all_nodes = lambda: []  # type: ignore[method-assign]

    request = GraphInsertRequest(
        source_path="layers.json",
        layout_data={
            "f8_layers": [
                {
                    "id": "logic",
                    "label": "Imported Logic",
                    "description": "from another graph",
                    "color": "#445566",
                    "defaultVisible": True,
                    "isBase": False,
                }
            ],
            "nodes": {
                "nodeA": {
                    "id": "nodeA",
                    "pos": [10, 20],
                    "f8_ui_state": {"layerIds": ["logic"]},
                }
            },
            "connections": [],
        },
        source_bbox=GraphBounds(10.0, 20.0, 10.0, 20.0),
        node_count=1,
        connection_count=0,
    )

    captured: dict[str, Any] = {}

    def _fake_deserialize(self, layout_data: dict[str, Any], clear_session: bool, clear_undo_stack: bool) -> None:
        _ = (self, clear_session, clear_undo_stack)
        captured["layout"] = deepcopy(layout_data)

    with patch.object(NodeGraph, "deserialize_session", new=_fake_deserialize):
        graph.apply_insert_graph(request, anchor_x=10.0, anchor_y=20.0)

    inserted_node = captured["layout"]["nodes"]["nodeA"]
    inserted_layer_ids = inserted_node["f8_ui_state"]["layerIds"]
    assert inserted_layer_ids == ["logic_2"]
    assert [layer.id for layer in graph._session_layer_defs] == ["base", "logic", "logic_2"]


def test_apply_insert_graph_adds_single_real_undo_command_for_component_insert() -> None:
    _ensure_app()
    source_graph = F8StudioGraph()
    target_graph = F8StudioGraph()
    for graph in (source_graph, target_graph):
        graph.node_factory.clear_registered_nodes()
        graph.node_factory.register_node(BackdropRenderNode)
    controller = _RefreshControllerStub()
    target_graph.set_global_hotkey_controller(controller)

    node_type = str(BackdropRenderNode.type_ or "")
    _ = source_graph.create_node(node_type, name="Source A", selected=False, push_undo=False, pos=(10.0, 20.0))
    _ = source_graph.create_node(node_type, name="Source B", selected=False, push_undo=False, pos=(140.0, 170.0))
    payload = source_graph.serialize_publish_session()

    request = target_graph.prepare_insert_graph_from_component(payload, component_name="Reusable Region")
    baseline_count = int(target_graph._undo_stack.count())
    baseline_index = int(target_graph._undo_stack.index())
    baseline_refresh_count = int(controller.schedule_calls)

    result = target_graph.apply_insert_graph(request, anchor_x=260.0, anchor_y=320.0)

    assert len(result.inserted_node_ids) == 2
    assert controller.schedule_calls > baseline_refresh_count
    assert int(target_graph._undo_stack.count()) == baseline_count + 1
    assert int(target_graph._undo_stack.index()) == baseline_index + 1
    assert str(target_graph._undo_stack.undoText() or "") == 'insert component: "Reusable Region"'
    assert all(target_graph.get_node_by_id(node_id) is not None for node_id in result.inserted_node_ids)

    baseline_refresh_count = int(controller.schedule_calls)
    target_graph._undo_stack.undo()

    assert controller.schedule_calls > baseline_refresh_count
    assert all(target_graph.get_node_by_id(node_id) is None for node_id in result.inserted_node_ids)
    assert int(target_graph._undo_stack.index()) == baseline_index

    baseline_refresh_count = int(controller.schedule_calls)
    target_graph._undo_stack.redo()

    assert controller.schedule_calls > baseline_refresh_count
    assert all(target_graph.get_node_by_id(node_id) is not None for node_id in result.inserted_node_ids)
    assert int(target_graph._undo_stack.index()) == baseline_index + 1

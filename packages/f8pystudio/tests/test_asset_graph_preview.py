from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass
from typing import cast

from qtpy import QtCore, QtTest, QtWidgets

from f8pysdk.specs import (
    F8Command,
    F8CommandParam,
    F8DataPortSpec,
    F8OperatorSpec,
    F8ServiceSpec,
    F8SpecEditPolicy,
    F8StateAccess,
    F8StateSpec,
    F8VariantKind,
    F8VariantRecord,
    editable_collection_edit_policy,
)
from f8pysdk.codec import copy_model, dump_json
from f8pysdk.specs import any_schema, number_schema, string_schema

from f8pystudio.assets.ui.asset_graph_preview import AssetGraphPreviewPane
from f8pystudio.assets.ui.component_catalog_dialog import ComponentCatalogDialog
from f8pystudio.assets.ui.variant_catalog_dialog import VariantCatalogDialog
from f8pystudio.assets.components.component_catalog import ComponentCatalogService
from f8pystudio.assets.variants.variant_catalog import LocalVariantProvider, RemoteCacheProvider, VariantCatalogService
from f8pystudio.assets.components.component_sync import ComponentSyncClient
from f8pystudio.assets.components.component_models import (
    F8ComponentDraftOriginKind,
    F8ComponentEntry,
    F8ComponentRecord,
    F8ComponentRemoteUser,
    F8ComponentSourceKind,
    F8ComponentVisibility,
    component_now_iso,
)
from f8pystudio.assets.variants.variant_models import (
    F8VariantDraftOriginKind,
    F8VariantEntry,
    F8VariantRemoteUser,
    F8VariantSourceKind,
    F8VariantVisibility,
    variant_now_iso,
)
from f8pystudio.assets.variants.variant_sync import VariantSyncClient
from f8pystudio.nodegraph.node_graph import F8StudioGraph
from f8pystudio.nodegraph.operator_basenode import F8StudioOperatorBaseNode
from f8pystudio.nodegraph.service_basenode import F8StudioServiceBaseNode
from f8pystudio.nodegraph.session_schema import wrap_layout_for_save
from f8pystudio.ui.components.controls import F8ValueBar
from f8pystudio.ui.components.state_editors import F8ValueBarEditor
from f8pystudio.ui.widgets.node_property_panel import (
    F8StudioNodePropEditorWidget,
    _F8SpecCommandEditor,
    _F8SpecPortEditor,
)
from f8pystudio.ui.widgets.node_property_panel import commands as node_property_commands
from f8pystudio.ui.widgets.node_property_panel import editor as node_property_editor
from f8pystudio.ui.widgets.node_property_panel import ports as node_property_ports

_PREVIEW_BUILD_WAIT_STEP_MS = 10
_PREVIEW_BUILD_WAIT_LIMIT_MS = 500


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def _wait_for_preview_completion(pane: AssetGraphPreviewPane) -> None:
    elapsed_ms = 0
    while pane._pending_request_kind is not None and elapsed_ms < _PREVIEW_BUILD_WAIT_LIMIT_MS:  # type: ignore[attr-defined]
        QtTest.QTest.qWait(_PREVIEW_BUILD_WAIT_STEP_MS)
        elapsed_ms += _PREVIEW_BUILD_WAIT_STEP_MS
    QtWidgets.QApplication.processEvents()


def _run_background_workers_immediately(monkeypatch) -> None:
    monkeypatch.setattr(
        "f8pystudio.assets.ui.background_tasks.BackgroundCallWorker.start",
        lambda self: self._run(),
    )


@dataclass
class _FakeComponentSaveSelectedNode:
    id: str


class _FakeComponentSaveGraph:
    def __init__(self, *, payload: dict[str, object], selected_nodes: list[_FakeComponentSaveSelectedNode]) -> None:
        self._payload = dump_json(payload, mode="json")
        self._selected_nodes = list(selected_nodes)

    def selected_nodes(self) -> list[object]:
        return list(self._selected_nodes)

    def serialize_publish_session(self) -> dict[str, object]:
        return dump_json(self._payload, mode="json")


def _make_service_node_class() -> type[F8StudioServiceBaseNode]:
    spec = F8ServiceSpec(
        serviceClass="svc.preview.service",
        label="Preview Service",
        paletteCategory="svc.preview",
        dataInPorts=[F8DataPortSpec(name="in", valueSchema=number_schema())],
        dataOutPorts=[F8DataPortSpec(name="out", valueSchema=any_schema())],
    )
    return cast(
        type[F8StudioServiceBaseNode],
        type(
            "PreviewServiceNode",
            (F8StudioServiceBaseNode,),
            {
                "__identifier__": "svc.preview",
                "NODE_NAME": "Preview Service",
                "SPEC_TEMPLATE": spec,
            },
        ),
    )


def _make_inspectable_service_node_class() -> type[F8StudioServiceBaseNode]:
    spec = F8ServiceSpec(
        serviceClass="svc.preview.inspect",
        label="Inspectable Preview Service",
        paletteCategory="svc.preview",
        editPolicy=F8SpecEditPolicy(
            stateFields=editable_collection_edit_policy(),
            commands=editable_collection_edit_policy(),
            dataInPorts=editable_collection_edit_policy(),
            dataOutPorts=editable_collection_edit_policy(),
        ),
        dataInPorts=[F8DataPortSpec(name="modeIn", valueSchema=string_schema(default="idle"), showOnNode=True)],
        dataOutPorts=[F8DataPortSpec(name="out", valueSchema=any_schema())],
        stateFields=[
            F8StateSpec(
                name="mode",
                valueSchema=string_schema(default="idle"),
                access=F8StateAccess.rw,
                showOnNode=True,
            )
        ],
        commands=[
            F8Command(
                name="Run Value",
                description="Inspect-only preview command.",
                showOnNode=True,
                params=[F8CommandParam(name="mode", required=True, valueSchema=string_schema(default="idle"))],
            )
        ],
    )
    return cast(
        type[F8StudioServiceBaseNode],
        type(
            "InspectablePreviewServiceNode",
            (F8StudioServiceBaseNode,),
            {
                "__identifier__": "svc.preview",
                "NODE_NAME": "Inspectable Preview Service",
                "SPEC_TEMPLATE": spec,
            },
        ),
    )


def _make_slider_preview_node_class() -> type[F8StudioServiceBaseNode]:
    spec = F8ServiceSpec(
        serviceClass="svc.preview.slider",
        label="Preview Slider Service",
        paletteCategory="svc.preview",
        stateFields=[
            F8StateSpec(
                name="gain",
                valueSchema=number_schema(default=0.2, minimum=0.0, maximum=1.0),
                access=F8StateAccess.rw,
                showOnNode=True,
                uiControl="slider",
            )
        ],
        dataOutPorts=[F8DataPortSpec(name="out", valueSchema=any_schema())],
    )
    return cast(
        type[F8StudioServiceBaseNode],
        type(
            "PreviewSliderServiceNode",
            (F8StudioServiceBaseNode,),
            {
                "__identifier__": "svc.preview",
                "NODE_NAME": "Preview Slider Service",
                "SPEC_TEMPLATE": spec,
            },
        ),
    )


def _make_operator_node_class() -> type[F8StudioOperatorBaseNode]:
    spec = F8OperatorSpec(
        serviceClass="svc.preview.service",
        operatorClass="op.preview.operator",
        label="Preview Operator",
        paletteCategory="svc.preview",
    )
    return cast(
        type[F8StudioOperatorBaseNode],
        type(
            "PreviewOperatorNode",
            (F8StudioOperatorBaseNode,),
            {
                "__identifier__": "svc.preview",
                "NODE_NAME": "Preview Operator",
                "SPEC_TEMPLATE": spec,
            },
        ),
    )


def _build_host_graph(*node_classes: type[object]) -> F8StudioGraph:
    _ensure_app()
    graph = F8StudioGraph()
    graph.node_factory.clear_registered_nodes()
    for node_cls in node_classes:
        graph.node_factory.register_node(node_cls)
    return graph


def _single_node_component_payload_for_node(node_cls: type[F8StudioServiceBaseNode]) -> dict[str, object]:
    node_type = str(node_cls.type_)
    spec_payload = dump_json(node_cls.SPEC_TEMPLATE, mode="json")
    return wrap_layout_for_save(
        {
            "nodes": {
                "node_a": {
                    "id": "node_a",
                    "type_": node_type,
                    "name": "Node A",
                    "pos": [0.0, 0.0],
                    "f8_spec": spec_payload,
                }
            },
            "connections": [],
        }
    )


def _component_payload_for_node(node_cls: type[F8StudioServiceBaseNode]) -> dict[str, object]:
    node_type = str(node_cls.type_)
    spec_payload = dump_json(node_cls.SPEC_TEMPLATE, mode="json")
    return wrap_layout_for_save(
        {
            "nodes": {
                "node_a": {
                    "id": "node_a",
                    "type_": node_type,
                    "name": "Node A",
                    "pos": [0.0, 0.0],
                    "f8_spec": spec_payload,
                },
                "node_b": {
                    "id": "node_b",
                    "type_": node_type,
                    "name": "Node B",
                    "pos": [240.0, 0.0],
                    "f8_spec": spec_payload,
                },
            },
            "connections": [
                {
                    "out": ["node_a", "out[D]"],
                    "in": ["node_b", "[D]in"],
                }
            ],
        }
    )


def _large_component_payload_for_node(
    node_cls: type[F8StudioServiceBaseNode],
    *,
    node_count: int = 11,
) -> dict[str, object]:
    node_type = str(node_cls.type_)
    spec_payload = dump_json(node_cls.SPEC_TEMPLATE, mode="json")
    nodes: dict[str, object] = {}
    connections: list[dict[str, object]] = []
    for index in range(node_count):
        node_id = f"node_{index}"
        nodes[node_id] = {
            "id": node_id,
            "type_": node_type,
            "name": f"Node {index}",
            "pos": [float(index * 180), 0.0],
            "f8_spec": spec_payload,
        }
        if index == 0:
            continue
        previous_node_id = f"node_{index - 1}"
        connections.append(
            {
                "out": [previous_node_id, "out[D]"],
                "in": [node_id, "[D]in"],
            }
        )
    return wrap_layout_for_save(
        {
            "nodes": nodes,
            "connections": connections,
        }
    )


def _inspector_editor(pane: AssetGraphPreviewPane) -> F8StudioNodePropEditorWidget | None:
    editor = getattr(pane._inspector, "_editor", None)  # type: ignore[attr-defined]
    if isinstance(editor, F8StudioNodePropEditorWidget):
        return editor
    return None


def _tab_names(editor: F8StudioNodePropEditorWidget) -> list[str]:
    tab_widget = editor.get_tab_widget()
    return [str(tab_widget.tabText(index) or "") for index in range(tab_widget.count())]


def _viewport_pos_for_scene_item(viewer: QtWidgets.QGraphicsView, item: object) -> QtCore.QPoint:
    scene_item = cast(QtWidgets.QGraphicsItem, item)
    return viewer.mapFromScene(scene_item.sceneBoundingRect().center())


def _find_tool_button(widget: QtWidgets.QWidget, tooltip_prefix: str) -> QtWidgets.QToolButton:
    for child in widget.findChildren(QtWidgets.QToolButton):
        if str(child.toolTip() or "").startswith(tooltip_prefix):
            return child
    raise AssertionError(f"Unable to find tool button with tooltip prefix {tooltip_prefix!r}")


def test_asset_graph_preview_renders_component_payload() -> None:
    _ensure_app()
    service_node_cls = _make_service_node_class()
    host_graph = _build_host_graph(service_node_cls)
    pane = AssetGraphPreviewPane(parent=None, host_graph=host_graph)

    pane.show_component_payload(_component_payload_for_node(service_node_cls))
    _wait_for_preview_completion(pane)

    assert len(list(pane.preview_graph.all_nodes() or [])) == 2
    viewer = pane.preview_graph.viewer()
    assert viewer is not None
    assert len(list(viewer.all_pipes() or [])) == 1

    pane.close()
    host_graph.widget.close()


def test_asset_graph_preview_renders_operator_variant_without_container() -> None:
    _ensure_app()
    operator_node_cls = _make_operator_node_class()
    host_graph = _build_host_graph(operator_node_cls)
    pane = AssetGraphPreviewPane(parent=None, host_graph=host_graph)
    variant_record = F8VariantRecord(
        variantId="variant-preview",
        kind=F8VariantKind.operator,
        baseNodeType=str(operator_node_cls.type_),
        serviceClass="svc.preview.service",
        operatorClass="op.preview.operator",
        name="Variant Preview",
        description="",
        tags=[],
        spec=dump_json(
            F8OperatorSpec(
                serviceClass="svc.preview.service",
                operatorClass="op.preview.operator",
                label="Variant Preview",
                paletteCategory="svc.preview",
            ),
            mode="json",
        ),
        createdAt=variant_now_iso(),
        updatedAt=variant_now_iso(),
    )
    pane.preview_graph._variant_record = lambda variant_id: variant_record if variant_id == "variant-preview" else None  # type: ignore[method-assign]

    pane.show_variant_record(variant_record)
    _wait_for_preview_completion(pane)

    nodes = list(pane.preview_graph.all_nodes() or [])
    assert len(nodes) == 1
    assert str(nodes[0].spec.label) == "Variant Preview"

    pane.close()
    host_graph.widget.close()


def test_asset_graph_preview_clears_graph_and_shows_error_for_invalid_component_payload() -> None:
    _ensure_app()
    service_node_cls = _make_service_node_class()
    host_graph = _build_host_graph(service_node_cls)
    pane = AssetGraphPreviewPane(parent=None, host_graph=host_graph)

    pane.show_component_payload({"schemaVersion": "bad-schema"})
    _wait_for_preview_completion(pane)

    assert len(list(pane.preview_graph.all_nodes() or [])) == 0
    assert "Failed to preview component." in pane.current_status_text()

    pane.close()
    host_graph.widget.close()


def test_preview_viewer_allows_selection_but_blocks_edit_shortcuts_and_live_connections() -> None:
    _ensure_app()
    service_node_cls = _make_service_node_class()
    host_graph = _build_host_graph(service_node_cls)
    pane = AssetGraphPreviewPane(parent=None, host_graph=host_graph)
    pane.resize(800, 600)
    pane.show()
    pane.show_component_payload(_component_payload_for_node(service_node_cls))
    _wait_for_preview_completion(pane)

    viewer = cast(QtWidgets.QGraphicsView, pane.preview_graph.viewer())
    internal_viewer = cast(object, viewer)
    assert internal_viewer._shortcut_search.isEnabled() is False  # type: ignore[attr-defined]
    assert internal_viewer._shortcut_delete.isEnabled() is False  # type: ignore[attr-defined]
    assert internal_viewer._shortcut_backspace.isEnabled() is False  # type: ignore[attr-defined]
    nodes = list(pane.preview_graph.all_nodes() or [])
    node_a = nodes[0]
    node_b = nodes[1]

    QtTest.QTest.mouseClick(viewer.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=_viewport_pos_for_scene_item(viewer, node_a.view))
    QtWidgets.QApplication.processEvents()

    assert [node.id for node in list(pane.preview_graph.selected_nodes() or [])] == ["node_a"]

    out_port = node_a.view.outputs[0]
    port_pos = _viewport_pos_for_scene_item(viewer, out_port)
    QtTest.QTest.mousePress(viewer.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=port_pos)
    QtWidgets.QApplication.processEvents()
    assert internal_viewer._LIVE_PIPE.isVisible() is False  # type: ignore[attr-defined]
    QtTest.QTest.mouseMove(viewer.viewport(), port_pos + QtCore.QPoint(40, 0), delay=10)
    QtWidgets.QApplication.processEvents()
    assert internal_viewer._LIVE_PIPE.isVisible() is False  # type: ignore[attr-defined]
    QtTest.QTest.mouseRelease(viewer.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=port_pos + QtCore.QPoint(40, 0))
    QtWidgets.QApplication.processEvents()

    assert [node.id for node in list(pane.preview_graph.selected_nodes() or [])] == ["node_a"]
    assert node_b.input_ports()[0].connected_ports() != []

    pane.close()
    host_graph.widget.close()


def test_single_node_preview_auto_populates_inspector_and_keeps_inline_state_browsable() -> None:
    _ensure_app()
    service_node_cls = _make_inspectable_service_node_class()
    host_graph = _build_host_graph(service_node_cls)
    pane = AssetGraphPreviewPane(parent=None, host_graph=host_graph)
    pane.resize(1000, 700)
    pane.show()

    pane.show_component_payload(_single_node_component_payload_for_node(service_node_cls))
    _wait_for_preview_completion(pane)

    assert [node.id for node in list(pane.preview_graph.selected_nodes() or [])] == ["node_a"]
    inspector_editor = _inspector_editor(pane)
    assert inspector_editor is not None
    assert _tab_names(inspector_editor) == ["State", "Command", "Port", "Node"]

    node = list(pane.preview_graph.all_nodes() or [])[0]
    toggle = node.view._state_inline_toggles["mode"]  # type: ignore[attr-defined]
    body = node.view._state_inline_bodies["mode"]  # type: ignore[attr-defined]
    inline_editor = cast(QtWidgets.QLineEdit | None, body.findChild(QtWidgets.QLineEdit))
    assert inline_editor is not None
    assert inline_editor.isReadOnly() is True
    assert inline_editor.isEnabled() is True
    assert toggle.isEnabled() is True
    assert body.isVisible() is False

    QtTest.QTest.mouseClick(toggle, QtCore.Qt.MouseButton.LeftButton)
    QtWidgets.QApplication.processEvents()
    assert body.isVisible() is True

    QtTest.QTest.mouseClick(toggle, QtCore.Qt.MouseButton.LeftButton)
    QtWidgets.QApplication.processEvents()
    assert body.isVisible() is False

    pane.close()
    host_graph.widget.close()


def test_preview_split_defaults_to_graph_favoring_layout() -> None:
    _ensure_app()
    service_node_cls = _make_inspectable_service_node_class()
    host_graph = _build_host_graph(service_node_cls)
    pane = AssetGraphPreviewPane(parent=None, host_graph=host_graph)
    pane.resize(1000, 700)
    pane.show()
    pane.show_component_payload(_single_node_component_payload_for_node(service_node_cls))
    _wait_for_preview_completion(pane)

    split_sizes = pane._preview_split.sizes()  # type: ignore[attr-defined]
    total = sum(split_sizes)

    assert total > 0
    assert split_sizes[0] > split_sizes[1]
    assert split_sizes[1] <= int(total * 0.4)

    pane.close()
    host_graph.widget.close()


def test_preview_slider_controls_are_read_only_inline_and_in_inspector() -> None:
    _ensure_app()
    service_node_cls = _make_slider_preview_node_class()
    host_graph = _build_host_graph(service_node_cls)
    pane = AssetGraphPreviewPane(parent=None, host_graph=host_graph)
    pane.resize(1200, 700)
    pane.show()

    pane.show_component_payload(_single_node_component_payload_for_node(service_node_cls))
    _wait_for_preview_completion(pane)

    node = list(pane.preview_graph.all_nodes() or [])[0]
    initial_value = float(node.get_property("gain"))
    inline_slider = cast(F8ValueBar, node.view._state_inline_controls["gain"])  # type: ignore[attr-defined]
    inline_center = inline_slider.rect().center()
    QtTest.QTest.mousePress(inline_slider, QtCore.Qt.MouseButton.LeftButton, pos=inline_center)
    QtTest.QTest.mouseMove(inline_slider, inline_center + QtCore.QPoint(40, 0), delay=10)
    QtTest.QTest.mouseRelease(
        inline_slider,
        QtCore.Qt.MouseButton.LeftButton,
        pos=inline_center + QtCore.QPoint(40, 0),
    )
    QtWidgets.QApplication.processEvents()

    assert float(inline_slider.value()) == initial_value
    assert float(node.get_property("gain")) == initial_value

    inspector_editor = _inspector_editor(pane)
    assert inspector_editor is not None
    inspector_slider_editor = cast(F8ValueBarEditor, inspector_editor.get_widget("gain"))
    inspector_slider = cast(F8ValueBar, inspector_slider_editor.findChild(F8ValueBar))
    inspector_center = inspector_slider.rect().center()
    QtTest.QTest.mousePress(inspector_slider, QtCore.Qt.MouseButton.LeftButton, pos=inspector_center)
    QtTest.QTest.mouseMove(inspector_slider, inspector_center + QtCore.QPoint(40, 0), delay=10)
    QtTest.QTest.mouseRelease(
        inspector_slider,
        QtCore.Qt.MouseButton.LeftButton,
        pos=inspector_center + QtCore.QPoint(40, 0),
    )
    QtWidgets.QApplication.processEvents()

    assert float(inspector_slider.value()) == initial_value
    assert float(node.get_property("gain")) == initial_value

    pane.close()
    host_graph.widget.close()


def test_multi_node_preview_selection_updates_inspector_and_dragging_is_ephemeral() -> None:
    _ensure_app()
    service_node_cls = _make_service_node_class()
    host_graph = _build_host_graph(service_node_cls)
    pane = AssetGraphPreviewPane(parent=None, host_graph=host_graph)
    pane.resize(1000, 700)
    pane.show()
    payload = _component_payload_for_node(service_node_cls)

    pane.show_component_payload(payload)
    _wait_for_preview_completion(pane)

    viewer = cast(QtWidgets.QGraphicsView, pane.preview_graph.viewer())
    nodes = list(pane.preview_graph.all_nodes() or [])
    node_a = nodes[0]
    node_b = nodes[1]

    assert _inspector_editor(pane) is None

    QtTest.QTest.mouseClick(viewer.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=_viewport_pos_for_scene_item(viewer, node_a.view))
    QtWidgets.QApplication.processEvents()
    assert getattr(pane._inspector, "_node_id", None) == "node_a"  # type: ignore[attr-defined]

    QtTest.QTest.mouseClick(viewer.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=_viewport_pos_for_scene_item(viewer, node_b.view))
    QtWidgets.QApplication.processEvents()
    assert getattr(pane._inspector, "_node_id", None) == "node_b"  # type: ignore[attr-defined]

    start_pos = tuple(float(v) for v in list(node_b.pos()))
    drag_start = _viewport_pos_for_scene_item(viewer, node_b.view)
    drag_end = drag_start + QtCore.QPoint(90, 50)
    QtTest.QTest.mousePress(viewer.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=drag_start)
    QtTest.QTest.mouseMove(viewer.viewport(), drag_end, delay=10)
    QtTest.QTest.mouseRelease(viewer.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=drag_end)
    QtWidgets.QApplication.processEvents()

    dragged_pos = tuple(float(v) for v in list(node_b.pos()))
    assert dragged_pos != start_pos

    pane.show_component_payload(payload)
    _wait_for_preview_completion(pane)
    reloaded_nodes = list(pane.preview_graph.all_nodes() or [])
    reloaded_node_b = next(node for node in reloaded_nodes if str(node.id or "") == "node_b")
    assert tuple(float(v) for v in list(reloaded_node_b.pos())) == start_pos

    pane.close()
    host_graph.widget.close()


def test_preview_inspector_is_read_only_but_view_details_remain_available(monkeypatch) -> None:
    _ensure_app()
    service_node_cls = _make_inspectable_service_node_class()
    host_graph = _build_host_graph(service_node_cls)
    pane = AssetGraphPreviewPane(parent=None, host_graph=host_graph)
    pane.resize(1000, 700)
    pane.show()

    pane.show_component_payload(_single_node_component_payload_for_node(service_node_cls))
    _wait_for_preview_completion(pane)

    inspector_editor = _inspector_editor(pane)
    assert inspector_editor is not None
    node = list(pane.preview_graph.all_nodes() or [])[0]
    spec_before = dump_json(copy_model(node.spec, deep=True), mode="json")

    state_container = inspector_editor._F8StudioNodePropEditorWidget__tab_windows["State"]  # type: ignore[attr-defined]
    state_row = state_container._sec.rows()[0]  # type: ignore[attr-defined]
    state_edit_btn = _find_tool_button(state_row, "View state field...")
    state_eye_btn = _find_tool_button(state_row, "Show on node")
    assert state_edit_btn.isEnabled() is True
    assert state_eye_btn.isEnabled() is False

    command_editor = inspector_editor.findChild(_F8SpecCommandEditor)
    port_editor = inspector_editor.findChild(_F8SpecPortEditor)
    assert command_editor is not None
    assert port_editor is not None

    command_row = command_editor._cmd_rows["Run Value"]  # type: ignore[attr-defined]
    assert command_editor._sec._add_btn.isVisible() is False  # type: ignore[attr-defined]
    assert command_editor._sec._list.drag_enabled() is False  # type: ignore[attr-defined]
    assert command_row._btn_invoke.isEnabled() is False  # type: ignore[attr-defined]
    assert "Preview inspector is read-only" in str(command_row._btn_invoke.toolTip() or "")  # type: ignore[attr-defined]
    assert command_row._btn_edit.isEnabled() is True  # type: ignore[attr-defined]
    assert command_row._eye_btn.isEnabled() is False  # type: ignore[attr-defined]
    assert command_row._btn_del.isVisible() is False  # type: ignore[attr-defined]

    port_row = port_editor._sec_data_in.rows()[0]  # type: ignore[attr-defined]
    assert port_editor._sec_data_in._add_btn.isVisible() is False  # type: ignore[attr-defined]
    assert port_editor._sec_data_in._list.drag_enabled() is False  # type: ignore[attr-defined]
    assert port_row.edit_btn.isEnabled() is True
    assert str(port_row.edit_btn.toolTip() or "").startswith("View data port...")
    assert port_row.eye_btn.isEnabled() is False
    assert port_row.del_btn.isVisible() is False

    captured_state: list[dict[str, object]] = []
    captured_command: list[dict[str, object]] = []
    captured_port: list[dict[str, object]] = []

    class _FakeStateDialog:
        def __init__(self, _parent=None, **kwargs: object) -> None:
            captured_state.append(dict(kwargs))

        def exec_(self) -> int:
            return int(QtWidgets.QDialog.Rejected)

    class _FakeCommandDialog:
        def __init__(self, _parent=None, **kwargs: object) -> None:
            captured_command.append(dict(kwargs))

        def exec_(self) -> int:
            return int(QtWidgets.QDialog.Rejected)

    class _FakeDataDialog:
        def __init__(self, _parent=None, **kwargs: object) -> None:
            captured_port.append(dict(kwargs))

        def exec_(self) -> int:
            return int(QtWidgets.QDialog.Rejected)

    monkeypatch.setattr(node_property_editor.F8StudioNodePropEditorWidget, "_STATE_FIELD_DIALOG_CLS", _FakeStateDialog)
    monkeypatch.setattr(node_property_commands, "_F8EditCommandDialog", _FakeCommandDialog)
    monkeypatch.setattr(node_property_ports, "_F8EditDataPortDialog", _FakeDataDialog)

    inspector_editor.open_state_field_editor("mode")
    command_editor._edit_command("Run Value")  # type: ignore[attr-defined]
    port_editor._edit_data(port_row)  # type: ignore[attr-defined]

    assert captured_state[0]["read_only"] is True
    assert captured_state[0]["title"] == "View state field"
    assert captured_command[0]["read_only"] is True
    assert captured_command[0]["title"] == "View command"
    assert captured_port[0]["read_only"] is True
    assert captured_port[0]["title"] == "View data port"
    assert dump_json(copy_model(node.spec, deep=True), mode="json") == spec_before

    pane.close()
    host_graph.widget.close()


def test_component_preview_shows_loading_placeholder_before_graph_build(monkeypatch) -> None:
    _ensure_app()
    service_node_cls = _make_service_node_class()
    host_graph = _build_host_graph(service_node_cls)
    pane = AssetGraphPreviewPane(parent=None, host_graph=host_graph)
    seen_loading: list[str] = []

    def _fake_load_session_payload(_payload: object) -> None:
        seen_loading.append(pane.current_status_text())

    monkeypatch.setattr(pane.preview_graph, "load_session_payload", _fake_load_session_payload)

    pane.show_component_payload(_component_payload_for_node(service_node_cls))

    assert "Building preview" in pane.current_status_text()
    _wait_for_preview_completion(pane)

    assert seen_loading == ["Building preview..."]

    pane.close()
    host_graph.widget.close()


def test_preview_loading_message_skips_redundant_ui_reset(monkeypatch) -> None:
    _ensure_app()
    service_node_cls = _make_service_node_class()
    host_graph = _build_host_graph(service_node_cls)
    pane = AssetGraphPreviewPane(parent=None, host_graph=host_graph)
    clear_calls: list[str] = []
    original_clear_graph = pane._clear_graph

    def _record_clear_graph() -> None:
        clear_calls.append("clear")
        original_clear_graph()

    monkeypatch.setattr(pane, "_clear_graph", _record_clear_graph)

    pane.show_loading_message("Loading remote preview…")
    pane.show_loading_message("Loading remote preview…")

    assert clear_calls == ["clear"]

    pane.close()
    host_graph.widget.close()


def test_preview_deferred_action_skips_redundant_ui_reset(monkeypatch) -> None:
    _ensure_app()
    service_node_cls = _make_service_node_class()
    host_graph = _build_host_graph(service_node_cls)
    pane = AssetGraphPreviewPane(parent=None, host_graph=host_graph)
    clear_calls: list[str] = []
    original_clear_graph = pane._clear_graph

    def _record_clear_graph() -> None:
        clear_calls.append("clear")
        original_clear_graph()

    def _load_preview() -> None:
        return None

    monkeypatch.setattr(pane, "_clear_graph", _record_clear_graph)

    pane.show_deferred_action(
        message="Remote preview is available on demand.",
        button_text="Load preview",
        callback=_load_preview,
    )
    pane.show_deferred_action(
        message="Remote preview is available on demand.",
        button_text="Load preview",
        callback=_load_preview,
    )

    assert clear_calls == ["clear"]

    pane.close()
    host_graph.widget.close()


def test_preview_sync_registered_nodes_reuses_cached_factory_registration(monkeypatch) -> None:
    _ensure_app()
    service_node_cls = _make_service_node_class()
    host_graph = _build_host_graph(service_node_cls)
    pane = AssetGraphPreviewPane(parent=None, host_graph=host_graph)
    preview_factory = pane.preview_graph.node_factory
    clear_calls = 0
    register_calls: list[object] = []
    original_clear = preview_factory.clear_registered_nodes
    original_register = preview_factory.register_node

    def _counting_clear() -> None:
        nonlocal clear_calls
        clear_calls += 1
        original_clear()

    def _counting_register(node_cls: object, alias: str | None = None) -> None:
        register_calls.append(node_cls)
        original_register(node_cls, alias=alias)

    monkeypatch.setattr(preview_factory, "clear_registered_nodes", _counting_clear)
    monkeypatch.setattr(preview_factory, "register_node", _counting_register)

    assert pane._sync_registered_nodes() is True
    assert pane._sync_registered_nodes() is True

    assert clear_calls == 1
    assert register_calls == [service_node_cls]

    pane.close()
    host_graph.widget.close()


def test_preview_fit_updates_viewer_scene_range_for_new_payload() -> None:
    _ensure_app()
    service_node_cls = _make_service_node_class()
    host_graph = _build_host_graph(service_node_cls)
    pane = AssetGraphPreviewPane(parent=None, host_graph=host_graph)
    first_payload = _component_payload_for_node(service_node_cls)
    second_payload = _component_payload_for_node(service_node_cls)
    second_layout = cast(dict[str, object], second_payload["layout"])
    second_nodes = cast(dict[str, object], second_layout["nodes"])
    node_b = cast(dict[str, object], second_nodes["node_b"])
    node_b["pos"] = [900.0, 320.0]

    pane.show_component_payload(first_payload)
    _wait_for_preview_completion(pane)
    viewer = cast(object, pane.preview_graph.viewer())
    first_scene_rect = tuple(float(v) for v in viewer.scene_rect())  # type: ignore[attr-defined]

    pane.show_component_payload(second_payload)
    _wait_for_preview_completion(pane)
    second_scene_rect = tuple(float(v) for v in viewer.scene_rect())  # type: ignore[attr-defined]

    assert second_scene_rect != first_scene_rect
    assert second_scene_rect[2] > first_scene_rect[2]
    assert second_scene_rect[3] > first_scene_rect[3]

    pane.close()
    host_graph.widget.close()


def test_stale_preview_fit_retry_callbacks_do_not_refocus_new_request(monkeypatch) -> None:
    _ensure_app()
    service_node_cls = _make_service_node_class()
    host_graph = _build_host_graph(service_node_cls)
    pane = AssetGraphPreviewPane(parent=None, host_graph=host_graph)
    focus_calls: list[str] = []

    monkeypatch.setattr(pane, "_focus_loaded_nodes", lambda: focus_calls.append("focus"))

    request_id = pane._next_request_id()
    pane._schedule_focus_loaded_nodes(request_id=request_id)
    pane._next_request_id()
    QtTest.QTest.qWait(220)

    assert focus_calls == ["focus"]

    pane.close()
    host_graph.widget.close()


def test_component_catalog_selection_updates_preview_and_raw(monkeypatch) -> None:
    _ensure_app()
    service_node_cls = _make_service_node_class()
    host_graph = _build_host_graph(service_node_cls)
    payload = _component_payload_for_node(service_node_cls)
    entry = F8ComponentEntry(
        record=F8ComponentRecord(
            componentId="component-preview",
            name="Preview Component",
            content=payload,
        ),
        source=F8ComponentSourceKind.local,
        installed=True,
    )
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_browser.subscribe_components_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(ComponentCatalogDialog, "_render_browser_from_state", lambda self, *_args: None)
    dialog = ComponentCatalogDialog(parent=None, node_graph=host_graph)
    dialog._entries = [entry]
    item = QtWidgets.QListWidgetItem()
    item.setData(QtCore.Qt.ItemDataRole.UserRole, "component-preview")
    dialog._list.addItem(item)

    dialog._list.setCurrentRow(0)
    _wait_for_preview_completion(dialog._preview)

    assert "component-preview" in dialog._raw.toPlainText()
    assert len(list(dialog._preview.preview_graph.all_nodes() or [])) == 2

    dialog.close()


def test_component_catalog_large_selection_defers_preview_until_requested(monkeypatch) -> None:
    _ensure_app()
    service_node_cls = _make_service_node_class()
    host_graph = _build_host_graph(service_node_cls)
    payload = _large_component_payload_for_node(service_node_cls, node_count=11)
    entry = F8ComponentEntry(
        record=F8ComponentRecord(
            componentId="component-large-preview",
            name="Large Preview Component",
            content=payload,
        ),
        source=F8ComponentSourceKind.local,
        installed=True,
    )
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_browser.subscribe_components_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(ComponentCatalogDialog, "_render_browser_from_state", lambda self, *_args: None)
    dialog = ComponentCatalogDialog(parent=None, node_graph=host_graph)
    dialog._entries = [entry]
    item = QtWidgets.QListWidgetItem()
    item.setData(QtCore.Qt.ItemDataRole.UserRole, "component-large-preview")
    dialog._list.addItem(item)

    dialog._list.setCurrentRow(0)
    QtWidgets.QApplication.processEvents()

    assert "11 nodes" in dialog._preview.current_status_text()
    assert len(list(dialog._preview.preview_graph.all_nodes() or [])) == 0
    load_button = dialog._preview._deferred_button  # type: ignore[attr-defined]
    assert load_button.text() == "Load preview"

    dialog._preview._load_deferred_preview()  # type: ignore[attr-defined]
    _wait_for_preview_completion(dialog._preview)

    assert len(list(dialog._preview.preview_graph.all_nodes() or [])) == 11

    dialog.close()


def test_component_catalog_context_menu_matches_variant_style_for_community(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_browser.subscribe_components_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(ComponentCatalogDialog, "_render_browser_from_state", lambda self, *_args: None)
    dialog = ComponentCatalogDialog(parent=None, node_graph=None)
    entry = F8ComponentEntry(
        record=F8ComponentRecord(
            componentId="component-community",
            name="Community Component",
            content=_component_payload_for_node(_make_service_node_class()),
        ),
        source=F8ComponentSourceKind.remote_public,
        installed=False,
        subscribed=False,
    )
    dialog._entries = [entry]
    dialog._sync_client._catalog_service._remote_provider.save_entries([entry])
    item = QtWidgets.QListWidgetItem()
    item.setData(QtCore.Qt.ItemDataRole.UserRole, "component-community")
    dialog._list.addItem(item)

    dialog._scope_tabs.setCurrentIndex(dialog._TAB_COMMUNITY)
    dialog._list.setCurrentRow(0)
    QtWidgets.QApplication.processEvents()

    menu = dialog._build_list_context_menu(
        current_tab=dialog._TAB_COMMUNITY,
        selected_entry=entry,
        local_entry=None,
        remote_entry=entry,
    )

    assert [action.text() for action in menu.actions()] == ["Subscribe", "Copy to Draft", "History"]

    dialog.close()


def test_component_dialog_ui_state_handlers_use_semantic_rebuilds(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr(
        "f8pystudio.assets.ui.component_catalog_browser.subscribe_components_changed",
        lambda _cb: (lambda: None),
    )
    monkeypatch.setattr(ComponentCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)
    dialog = ComponentCatalogDialog(parent=None, node_graph=None)
    monkeypatch.setattr(dialog, "_selected_component_id", lambda: "component-ui-state")

    events: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        dialog,
        "_rebuild_browser_after_tab_ui_state_changed",
        lambda *, preserve_component_id=None: events.append(("tab", preserve_component_id)),
    )
    monkeypatch.setattr(
        dialog,
        "_rebuild_browser_after_query_ui_state_changed",
        lambda *, preserve_component_id=None: events.append(("query", preserve_component_id)),
    )
    monkeypatch.setattr(
        dialog,
        "_rebuild_browser_after_filter_ui_state_changed",
        lambda *, preserve_component_id=None: events.append(("filter", preserve_component_id)),
    )

    dialog._on_scope_tab_changed(dialog._TAB_COMMUNITY)
    dialog._search_input.setText("draft")
    dialog._tab_queries[dialog._TAB_DRAFTS] = ""
    dialog._on_search_submitted()
    dialog._filter_combo.blockSignals(True)
    dialog._filter_combo.clear()
    dialog._filter_combo.addItem("All Drafts", "all")
    dialog._filter_combo.addItem("Linked Drafts", "linked")
    dialog._filter_combo.setCurrentIndex(1)
    dialog._filter_combo.blockSignals(False)
    dialog._tab_filters[dialog._TAB_DRAFTS] = "all"
    dialog._on_filter_changed()

    assert events == [
        ("tab", "component-ui-state"),
        ("query", "component-ui-state"),
        ("filter", "component-ui-state"),
    ]

    dialog.close()


def test_component_dialog_browser_rebuild_distinguishes_pure_vs_preserve(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr(
        "f8pystudio.assets.ui.component_catalog_browser.subscribe_components_changed",
        lambda _cb: (lambda: None),
    )
    monkeypatch.setattr(ComponentCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)
    dialog = ComponentCatalogDialog(parent=None, node_graph=None)

    reload_calls: list[dict[str, object]] = []

    def _record_reload(*_args, **kwargs) -> None:
        reload_calls.append(dict(kwargs))

    monkeypatch.setattr(dialog, "_render_browser_from_state", _record_reload)
    monkeypatch.setattr(dialog, "_selected_component_id", lambda: "component-preserve")

    dialog._rebuild_browser_from_asset_cache()
    dialog._rebuild_browser_from_asset_cache_preserving_selection()
    dialog._rebuild_browser_from_asset_cache_for_change()
    dialog._rebuild_browser_from_asset_cache_for_change(preserve_component_id="component-explicit")

    assert reload_calls == [
        {},
        {"preserve_component_id": "component-preserve"},
        {},
        {"preserve_component_id": "component-explicit"},
    ]

    dialog.close()


def test_component_selection_preview_loads_on_demand_without_installing(monkeypatch, tmp_path: Path) -> None:
    _ensure_app()
    _run_background_workers_immediately(monkeypatch)
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_browser.subscribe_components_changed", lambda _cb: (lambda: None))
    original_render_browser_from_state = ComponentCatalogDialog._render_browser_from_state
    monkeypatch.setattr(ComponentCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)
    dialog = ComponentCatalogDialog(parent=None, node_graph=None)
    monkeypatch.setattr(ComponentCatalogDialog, "_render_browser_from_state", original_render_browser_from_state)

    settings = QtCore.QSettings(str(tmp_path / "component-preview-cache.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = ComponentCatalogService(db_path=db_path)
    dialog._sync_client = ComponentSyncClient(settings=settings, catalog_service=service)

    entry = F8ComponentEntry(
        record=F8ComponentRecord(
            componentId="component-preview-cache",
            name="Preview Cache Component",
            description="Preview only",
            tags=["preview"],
            content={},
        ),
        source=F8ComponentSourceKind.remote_private,
        visibility=F8ComponentVisibility.private,
        ownerUserId="u1",
        ownerDisplayName="User One",
        installed=False,
        hasCachedContent=False,
    )
    dialog._entries = [entry]
    dialog._sync_client._catalog_service._remote_provider.save_entries([entry])

    cached_entry = copy_model(
        entry,
        update={
            "record": copy_model(
                entry.record,
                update={"content": _component_payload_for_node(_make_service_node_class())},
            ),
            "installed": False,
            "hasCachedContent": True,
        },
    )
    hydrate_calls: list[str] = []
    preview_calls: list[str] = []
    monkeypatch.setattr(
        dialog._sync_client,
        "load_component_preview_entry",
        lambda preview_entry: preview_calls.append(str(preview_entry.record.componentId)) or cached_entry,
    )
    monkeypatch.setattr(dialog._sync_client, "clone_for_background", lambda: dialog._sync_client)
    monkeypatch.setattr(
        dialog._sync_client,
        "hydrate_component",
        lambda component_id: hydrate_calls.append(str(component_id)) or cached_entry,
    )

    item = QtWidgets.QListWidgetItem()
    item.setData(QtCore.Qt.ItemDataRole.UserRole, "component-preview-cache")
    dialog._list.addItem(item)

    dialog._list.setCurrentRow(0)
    QtWidgets.QApplication.processEvents()

    assert dialog._preview.current_status_text() == "Remote preview is available on demand."
    assert preview_calls == []
    dialog._preview._load_deferred_preview()  # type: ignore[attr-defined]
    QtWidgets.QApplication.processEvents()

    assert preview_calls == ["component-preview-cache"]
    assert hydrate_calls == []
    assert dialog._sync_client._catalog_service.entry("component-preview-cache", include_uninstalled=False) is None

    dialog.close()


def test_variant_catalog_context_menu_shows_draft_actions(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(VariantCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)
    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.preview.variant",
        base_node_name="Preview Variant",
        node_graph=None,
    )
    entry = F8VariantEntry(
        record=F8VariantRecord(
            variantId="variant-draft-menu",
            kind=F8VariantKind.service,
            baseNodeType="svc.preview.service",
            serviceClass="svc.preview.service",
            operatorClass=None,
            name="Draft Variant",
            description="",
            tags=[],
            spec={"label": "Draft Variant"},
            createdAt=variant_now_iso(),
            updatedAt=variant_now_iso(),
        ),
        source=F8VariantSourceKind.local,
        installed=True,
        hasCachedContent=True,
        isLocalDraft=True,
    )
    dialog._entries = [entry]
    item = QtWidgets.QListWidgetItem()
    item.setData(QtCore.Qt.ItemDataRole.UserRole, "variant-draft-menu")
    dialog._list.addItem(item)

    dialog._scope_tabs.setCurrentIndex(dialog._TAB_DRAFTS)
    dialog._list.setCurrentRow(0)
    QtWidgets.QApplication.processEvents()

    menu_actions: list[str] = []

    class _FakeMenu:
        def __init__(self, _parent: object) -> None:
            self._actions: list[QtWidgets.QAction] = []

        def addAction(self, text: str) -> QtWidgets.QAction:
            action = QtWidgets.QAction(text, dialog)
            self._actions.append(action)
            return action

        def addSeparator(self) -> QtWidgets.QAction:
            action = QtWidgets.QAction(dialog)
            action.setSeparator(True)
            self._actions.append(action)
            return action

        def actions(self) -> list[QtWidgets.QAction]:
            return list(self._actions)

        def exec(self, _pos: object) -> None:
            menu_actions.extend(action.text() for action in self._actions)

    monkeypatch.setattr(QtWidgets, "QMenu", _FakeMenu)

    dialog._on_list_context_menu_requested(QtCore.QPoint(0, 0))

    assert menu_actions == [
        "Edit Draft Metadata",
        "Publish Draft",
        "Copy to Draft",
        "Delete Draft",
        "History",
    ]

    dialog.close()


def test_component_dialog_shows_linked_draft_reference(monkeypatch, tmp_path: Path) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_browser.subscribe_components_changed", lambda _cb: (lambda: None))
    original_render_browser_from_state = ComponentCatalogDialog._render_browser_from_state
    monkeypatch.setattr(ComponentCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)
    dialog = ComponentCatalogDialog(parent=None, node_graph=None)
    monkeypatch.setattr(ComponentCatalogDialog, "_render_browser_from_state", original_render_browser_from_state)
    settings = QtCore.QSettings(str(tmp_path / "component-linked-draft.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = ComponentCatalogService(db_path=db_path)
    dialog._sync_client = ComponentSyncClient(settings=settings, catalog_service=service)
    monkeypatch.setattr(dialog, "_refresh_remote_catalog_if_needed", lambda: None)
    dialog._sync_client._catalog_service._remote_provider.save_entries(
        [
            F8ComponentEntry(
                record=F8ComponentRecord(
                    componentId="remote-component-1",
                    name="Cloud Reference",
                    description="",
                    content={"schemaVersion": "f8studio-session/1", "layout": {"nodes": {}, "connections": []}},
                    createdAt=component_now_iso(),
                    updatedAt=component_now_iso(),
                ),
                source=F8ComponentSourceKind.remote_private,
                visibility=F8ComponentVisibility.private,
                ownerUserId="u1",
                ownerDisplayName="User One",
                installed=False,
            )
        ]
    )
    _ = dialog._draft_service_for_catalog().create_draft_from_record(
        F8ComponentRecord(
            componentId="draft-linked-component",
            name="Local Draft Component",
            description="",
            content={"schemaVersion": "f8studio-session/1", "layout": {"nodes": {}, "connections": []}},
            createdAt=component_now_iso(),
            updatedAt=component_now_iso(),
        ),
        origin_kind=F8ComponentDraftOriginKind.copy_remote,
        publish_target_asset_id="remote-component-1",
        publish_base_remote_version_number=1,
        draft_id="draft-linked-component",
    )

    dialog._render_browser_from_state()

    item = dialog._list.item(0)
    assert item is not None
    row_widget = dialog._list.itemWidget(item)
    assert row_widget is not None
    label_texts = [label.text() for label in row_widget.findChildren(QtWidgets.QLabel)]

    assert "Linked Draft" in label_texts
    assert "linked to User One.Cloud Reference" in label_texts

    dialog.close()


def test_component_dialog_remote_row_shows_draft_badge(monkeypatch, tmp_path: Path) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_browser.subscribe_components_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(ComponentCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)
    dialog = ComponentCatalogDialog(parent=None, node_graph=None)
    settings = QtCore.QSettings(str(tmp_path / "component-draft-badge.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = ComponentCatalogService(db_path=db_path)
    dialog._sync_client = ComponentSyncClient(settings=settings, catalog_service=service)
    remote_entry = F8ComponentEntry(
        record=F8ComponentRecord(
            componentId="remote-component-draft-badge",
            name="Remote Mine Component",
            description="",
            content={"schemaVersion": "f8studio-session/1", "layout": {"nodes": {}, "connections": []}},
            createdAt=component_now_iso(),
            updatedAt=component_now_iso(),
        ),
        source=F8ComponentSourceKind.remote_private,
        visibility=F8ComponentVisibility.private,
        ownerUserId="u1",
        ownerDisplayName="User One",
        installed=False,
    )
    dialog._sync_client._catalog_service._remote_provider.save_entries([remote_entry])
    _ = dialog._draft_service_for_catalog().create_draft_from_record(
        F8ComponentRecord(
            componentId="local-linked-component",
            name="Local Linked Component",
            description="",
            content={"schemaVersion": "f8studio-session/1", "layout": {"nodes": {}, "connections": []}},
            createdAt=component_now_iso(),
            updatedAt=component_now_iso(),
        ),
        origin_kind=F8ComponentDraftOriginKind.copy_remote,
        publish_target_asset_id="remote-component-draft-badge",
        publish_base_remote_version_number=2,
        draft_id="local-linked-component",
    )

    row_widget = dialog._build_list_row(remote_entry)
    row_labels = [label.text() for label in row_widget.findChildren(QtWidgets.QLabel)]

    assert "draft" in row_labels

    dialog.close()


def test_variant_dialog_shows_linked_draft_label(monkeypatch, tmp_path: Path) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", lambda _cb: (lambda: None))
    original_render_browser_from_state = VariantCatalogDialog._render_browser_from_state
    monkeypatch.setattr(VariantCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)
    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.preview.service",
        base_node_name="Preview Service",
        node_graph=None,
    )
    monkeypatch.setattr(VariantCatalogDialog, "_render_browser_from_state", original_render_browser_from_state)
    settings = QtCore.QSettings(str(tmp_path / "variant-linked-draft.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=db_path),
        remote_provider=RemoteCacheProvider(db_path=db_path),
    )
    dialog._sync_client = VariantSyncClient(settings=settings, catalog_service=service)
    monkeypatch.setattr(dialog, "_refresh_remote_catalog_if_needed", lambda: None)
    dialog._sync_client._catalog_service._remote_provider.save_entries(
        [
            F8VariantEntry(
                record=F8VariantRecord(
                    variantId="remote-variant-1",
                    kind=F8VariantKind.service,
                    baseNodeType="svc.preview.service",
                    serviceClass="svc.preview.service",
                    operatorClass=None,
                    name="Cloud Reference Variant",
                    description="",
                    tags=[],
                    spec={"label": "Cloud Reference Variant"},
                    createdAt=variant_now_iso(),
                    updatedAt=variant_now_iso(),
                ),
                source=F8VariantSourceKind.remote_private,
                visibility=F8VariantVisibility.private,
                ownerUserId="u1",
                ownerDisplayName="User One",
                installed=False,
            )
        ]
    )
    _ = dialog._draft_service_for_catalog().create_draft_from_record(
        F8VariantRecord(
            variantId="draft-linked",
            kind=F8VariantKind.service,
            baseNodeType="svc.preview.service",
            serviceClass="svc.preview.service",
            operatorClass=None,
            name="Linked Draft Variant",
            description="",
            tags=[],
            spec={"label": "Linked Draft Variant"},
            createdAt=variant_now_iso(),
            updatedAt=variant_now_iso(),
        ),
        origin_kind=F8VariantDraftOriginKind.copy_remote,
        publish_target_asset_id="remote-variant-1",
        publish_base_remote_version_number=1,
        draft_id="draft-linked",
    )

    dialog._render_browser_from_state()

    item = dialog._list.item(0)
    assert item is not None
    row_widget = dialog._list.itemWidget(item)
    assert row_widget is not None
    label_texts = [label.text() for label in row_widget.findChildren(QtWidgets.QLabel)]

    assert "Linked Draft" in label_texts
    assert "linked to User One.Cloud Reference Variant" in label_texts

    dialog.close()


def test_variant_dialog_remote_row_shows_draft_badge(monkeypatch, tmp_path: Path) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(VariantCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)
    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.preview.service",
        base_node_name="Preview Service",
        node_graph=None,
    )
    settings = QtCore.QSettings(str(tmp_path / "variant-draft-badge.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=db_path),
        remote_provider=RemoteCacheProvider(db_path=db_path),
    )
    dialog._sync_client = VariantSyncClient(settings=settings, catalog_service=service)
    remote_entry = F8VariantEntry(
        record=F8VariantRecord(
            variantId="remote-variant-draft-badge",
            kind=F8VariantKind.service,
            baseNodeType="svc.preview.service",
            serviceClass="svc.preview.service",
            operatorClass=None,
            name="Remote Mine Variant",
            description="",
            tags=[],
            spec={"label": "Remote Mine Variant"},
            createdAt=variant_now_iso(),
            updatedAt=variant_now_iso(),
        ),
        source=F8VariantSourceKind.remote_private,
        visibility=F8VariantVisibility.private,
        ownerUserId="u1",
        ownerDisplayName="User One",
        installed=False,
    )
    dialog._sync_client._catalog_service._remote_provider.save_entries([remote_entry])
    _ = dialog._draft_service_for_catalog().create_draft_from_record(
        F8VariantRecord(
            variantId="local-linked-variant",
            kind=F8VariantKind.service,
            baseNodeType="svc.preview.service",
            serviceClass="svc.preview.service",
            operatorClass=None,
            name="Local Linked Variant",
            description="",
            tags=[],
            spec={"label": "Local Linked Variant"},
            createdAt=variant_now_iso(),
            updatedAt=variant_now_iso(),
        ),
        origin_kind=F8VariantDraftOriginKind.copy_remote,
        publish_target_asset_id="remote-variant-draft-badge",
        publish_base_remote_version_number=2,
        draft_id="local-linked-variant",
    )

    row_widget = dialog._build_list_row(remote_entry)
    row_labels = [label.text() for label in row_widget.findChildren(QtWidgets.QLabel)]

    assert "draft" in row_labels

    dialog.close()


def test_variant_dialog_node_type_combo_tracks_current_tab_entries(monkeypatch, tmp_path: Path) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", lambda _cb: (lambda: None))
    original_render_browser_from_state = VariantCatalogDialog._render_browser_from_state
    monkeypatch.setattr(VariantCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)
    dialog = VariantCatalogDialog(parent=None, node_graph=None)
    monkeypatch.setattr(VariantCatalogDialog, "_render_browser_from_state", original_render_browser_from_state)
    settings = QtCore.QSettings(str(tmp_path / "variant-node-types.ini"), QtCore.QSettings.IniFormat)
    db_path = tmp_path / "assets.db"
    service = VariantCatalogService(
        local_provider=LocalVariantProvider(db_path=db_path),
        remote_provider=RemoteCacheProvider(db_path=db_path),
    )
    dialog._sync_client = VariantSyncClient(settings=settings, catalog_service=service)
    monkeypatch.setattr(dialog, "_refresh_remote_catalog_if_needed", lambda: None)
    monkeypatch.setattr(
        dialog._sync_client,
        "current_user",
        lambda: F8VariantRemoteUser(userId="u1", name="User One", email="user-one@example.com"),
    )
    monkeypatch.setattr(dialog._sync_client, "current_access_token", lambda: "token")

    _ = dialog._draft_service_for_catalog().create_draft_from_record(
        F8VariantRecord(
            variantId="draft-node-type",
            kind=F8VariantKind.service,
            baseNodeType="svc.draft.node",
            serviceClass="svc.preview.service",
            operatorClass=None,
            name="Draft Node Type",
            description="",
            tags=[],
            spec={"label": "Draft Node Type"},
            createdAt=variant_now_iso(),
            updatedAt=variant_now_iso(),
        ),
        origin_kind=F8VariantDraftOriginKind.new,
        publish_target_asset_id=None,
        publish_base_remote_version_number=None,
        draft_id="draft-node-type",
    )
    dialog._sync_client._catalog_service._remote_provider.save_entries(
        [
            F8VariantEntry(
                record=F8VariantRecord(
                    variantId="community-node-type",
                    kind=F8VariantKind.service,
                    baseNodeType="svc.community.node",
                    serviceClass="svc.preview.service",
                    operatorClass=None,
                    name="Community Node Type",
                    description="",
                    tags=[],
                    spec={"label": "Community Node Type"},
                    createdAt=variant_now_iso(),
                    updatedAt=variant_now_iso(),
                ),
                source=F8VariantSourceKind.remote_public,
                visibility=F8VariantVisibility.public,
                installed=False,
                subscribed=False,
            ),
            F8VariantEntry(
                record=F8VariantRecord(
                    variantId="mine-node-type",
                    kind=F8VariantKind.service,
                    baseNodeType="svc.mine.node",
                    serviceClass="svc.preview.service",
                    operatorClass=None,
                    name="Mine Node Type",
                    description="",
                    tags=[],
                    spec={"label": "Mine Node Type"},
                    createdAt=variant_now_iso(),
                    updatedAt=variant_now_iso(),
                ),
                source=F8VariantSourceKind.remote_private,
                visibility=F8VariantVisibility.private,
                ownerUserId="u1",
                ownerDisplayName="User One",
                installed=False,
                subscribed=False,
            ),
        ]
    )

    dialog._render_browser_from_state()
    QtWidgets.QApplication.processEvents()

    assert dialog._toolbar.isAncestorOf(dialog._node_type_combo) is False
    assert dialog._list_column.isAncestorOf(dialog._node_type_combo) is True
    assert dialog._list_column.isAncestorOf(dialog._node_type_label) is True
    assert dialog._list_column.isAncestorOf(dialog._list) is True

    assert [dialog._node_type_combo.itemText(index) for index in range(dialog._node_type_combo.count())] == [
        "All Types",
        "svc.draft.node",
    ]

    dialog._scope_tabs.setCurrentIndex(dialog._TAB_COMMUNITY)
    dialog._render_browser_from_state()
    QtWidgets.QApplication.processEvents()

    assert [dialog._node_type_combo.itemText(index) for index in range(dialog._node_type_combo.count())] == [
        "All Types",
        "svc.community.node",
    ]

    dialog._scope_tabs.setCurrentIndex(dialog._TAB_MINE)
    dialog._render_browser_from_state()
    QtWidgets.QApplication.processEvents()

    assert [dialog._node_type_combo.itemText(index) for index in range(dialog._node_type_combo.count())] == [
        "All Types",
        "svc.mine.node",
    ]

    dialog.close()


def test_variant_dialog_ui_state_handlers_use_semantic_rebuilds(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr(
        "f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed",
        lambda _cb: (lambda: None),
    )
    monkeypatch.setattr(VariantCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)
    dialog = VariantCatalogDialog(parent=None, node_graph=None)
    monkeypatch.setattr(dialog, "_selected_variant_id", lambda: "variant-ui-state")

    events: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        dialog,
        "_rebuild_browser_after_tab_ui_state_changed",
        lambda *, preserve_variant_id=None: events.append(("tab", preserve_variant_id)),
    )
    monkeypatch.setattr(
        dialog,
        "_rebuild_browser_after_query_ui_state_changed",
        lambda *, preserve_variant_id=None: events.append(("query", preserve_variant_id)),
    )
    monkeypatch.setattr(
        dialog,
        "_rebuild_browser_after_filter_ui_state_changed",
        lambda *, preserve_variant_id=None: events.append(("filter", preserve_variant_id)),
    )
    monkeypatch.setattr(
        dialog,
        "_rebuild_browser_after_node_type_ui_state_changed",
        lambda *, preserve_variant_id=None: events.append(("node_type", preserve_variant_id)),
    )

    dialog._on_scope_tab_changed(dialog._TAB_COMMUNITY)
    dialog._search_input.setText("draft")
    dialog._tab_queries[dialog._TAB_DRAFTS] = ""
    dialog._on_search_submitted()
    dialog._filter_combo.blockSignals(True)
    dialog._filter_combo.clear()
    dialog._filter_combo.addItem("All Drafts", "all")
    dialog._filter_combo.addItem("Linked Drafts", "linked")
    dialog._filter_combo.setCurrentIndex(1)
    dialog._filter_combo.blockSignals(False)
    dialog._tab_filters[dialog._TAB_DRAFTS] = "all"
    dialog._on_filter_changed()
    dialog._node_type_combo.blockSignals(True)
    dialog._node_type_combo.clear()
    dialog._node_type_combo.addItem("All Types", "")
    dialog._node_type_combo.addItem("svc.example.node", "svc.example.node")
    dialog._node_type_combo.setCurrentIndex(1)
    dialog._node_type_combo.blockSignals(False)
    dialog._on_node_type_filter_changed()

    assert events == [
        ("tab", "variant-ui-state"),
        ("query", "variant-ui-state"),
        ("filter", "variant-ui-state"),
        ("node_type", "variant-ui-state"),
    ]

    dialog.close()


def test_variant_dialog_browser_rebuild_distinguishes_pure_vs_preserve(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr(
        "f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed",
        lambda _cb: (lambda: None),
    )
    monkeypatch.setattr(VariantCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)
    dialog = VariantCatalogDialog(parent=None, node_graph=None)

    reload_calls: list[dict[str, object]] = []

    def _record_reload(*_args, **kwargs) -> None:
        reload_calls.append(dict(kwargs))

    monkeypatch.setattr(dialog, "_render_browser_from_state", _record_reload)
    monkeypatch.setattr(dialog, "_selected_variant_id", lambda: "variant-preserve")

    dialog._rebuild_browser_from_asset_cache()
    dialog._rebuild_browser_from_asset_cache_preserving_selection()
    dialog._rebuild_browser_from_asset_cache_for_change()
    dialog._rebuild_browser_from_asset_cache_for_change(preserve_variant_id="variant-explicit")

    assert reload_calls == [
        {},
        {"preserve_variant_id": "variant-preserve"},
        {},
        {"preserve_variant_id": "variant-explicit"},
    ]

    dialog.close()


def test_component_catalog_context_menu_shows_current_mine_actions(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_browser.subscribe_components_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(ComponentCatalogDialog, "_render_browser_from_state", lambda self, *_args: None)
    dialog = ComponentCatalogDialog(parent=None, node_graph=None)
    entry = F8ComponentEntry(
        record=F8ComponentRecord(
            componentId="component-mine-remote",
            name="Mine Remote Component",
            content=_component_payload_for_node(_make_service_node_class()),
        ),
        source=F8ComponentSourceKind.remote_private,
        visibility=F8ComponentVisibility.private,
        ownerUserId="u1",
        ownerDisplayName="User One",
        installed=False,
    )
    dialog._entries = [entry]
    dialog._sync_client._catalog_service._remote_provider.save_entries([entry])
    monkeypatch.setattr(
        dialog._sync_client,
        "current_user",
        lambda: F8ComponentRemoteUser(userId="u1", name="User One", email="user-one@example.com"),
    )
    item = QtWidgets.QListWidgetItem()
    item.setData(QtCore.Qt.ItemDataRole.UserRole, "component-mine-remote")
    dialog._list.addItem(item)

    dialog._scope_tabs.setCurrentIndex(dialog._TAB_MINE)
    dialog._list.setCurrentRow(0)
    QtWidgets.QApplication.processEvents()

    menu = dialog._build_list_context_menu(
        current_tab=dialog._TAB_MINE,
        selected_entry=entry,
        local_entry=None,
        remote_entry=entry,
    )

    assert [action.text() for action in menu.actions()] == [
        "Open Draft",
        "Load",
        "Delete",
        "Make Public",
        "History",
    ]

    dialog.close()


def test_component_dialog_mine_toolbar_uses_open_draft_and_load(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_browser.subscribe_components_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(ComponentCatalogDialog, "_render_browser_from_state", lambda self, *_args: None)
    dialog = ComponentCatalogDialog(parent=None, node_graph=None)
    entry = F8ComponentEntry(
        record=F8ComponentRecord(
            componentId="component-mine-toolbar",
            name="Mine Toolbar Component",
            content=_component_payload_for_node(_make_service_node_class()),
        ),
        source=F8ComponentSourceKind.remote_private,
        visibility=F8ComponentVisibility.private,
        ownerUserId="u1",
        ownerDisplayName="User One",
        installed=False,
    )
    dialog._entries = [entry]
    dialog._sync_client._catalog_service._remote_provider.save_entries([entry])
    monkeypatch.setattr(
        dialog._sync_client,
        "current_user",
        lambda: F8ComponentRemoteUser(userId="u1", name="User One", email="user-one@example.com"),
    )
    item = QtWidgets.QListWidgetItem()
    item.setData(QtCore.Qt.ItemDataRole.UserRole, "component-mine-toolbar")
    dialog._list.addItem(item)

    dialog._scope_tabs.setCurrentIndex(dialog._TAB_MINE)
    dialog._list.setCurrentRow(0)
    QtWidgets.QApplication.processEvents()

    assert dialog._btn_copy_local.isHidden() is False
    assert dialog._btn_copy_local.toolTip() == "Open Draft"
    assert dialog._btn_install.isHidden() is False
    assert dialog._btn_install.toolTip() == "Load"
    assert dialog._btn_edit.isHidden() is True

    dialog.close()


def test_component_dialog_installed_uses_remove_from_installed(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_browser.subscribe_components_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(ComponentCatalogDialog, "_render_browser_from_state", lambda self, *_args: None)
    dialog = ComponentCatalogDialog(parent=None, node_graph=None)
    entry = F8ComponentEntry(
        record=F8ComponentRecord(
            componentId="component-installed",
            name="Installed Component",
            content=_component_payload_for_node(_make_service_node_class()),
        ),
        source=F8ComponentSourceKind.remote_private,
        visibility=F8ComponentVisibility.private,
        ownerUserId="u1",
        ownerDisplayName="User One",
        installed=True,
        hasCachedContent=True,
    )
    dialog._entries = [entry]
    dialog._sync_client._catalog_service._remote_provider.save_entries([entry])
    item = QtWidgets.QListWidgetItem()
    item.setData(QtCore.Qt.ItemDataRole.UserRole, "component-installed")
    dialog._list.addItem(item)

    dialog._scope_tabs.setCurrentIndex(dialog._TAB_INSTALLED)
    dialog._list.setCurrentRow(0)
    QtWidgets.QApplication.processEvents()

    assert dialog._btn_delete.isHidden() is False
    assert dialog._btn_delete.toolTip() == "Remove from Installed"

    menu = dialog._build_list_context_menu(
        current_tab=dialog._TAB_INSTALLED,
        selected_entry=entry,
        local_entry=None,
        remote_entry=entry,
    )
    assert [action.text() for action in menu.actions()] == [
        "Remove from Installed",
        "Pull",
        "History",
    ]

    dialog.close()


def test_component_dialog_installed_delete_offloads_without_remote_delete(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_browser.subscribe_components_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(ComponentCatalogDialog, "_render_browser_from_state", lambda self, *_args: None)
    dialog = ComponentCatalogDialog(parent=None, node_graph=None)
    entry = F8ComponentEntry(
        record=F8ComponentRecord(
            componentId="component-installed-delete",
            name="Installed Delete Component",
            content=_component_payload_for_node(_make_service_node_class()),
        ),
        source=F8ComponentSourceKind.remote_private,
        visibility=F8ComponentVisibility.private,
        ownerUserId="u1",
        ownerDisplayName="User One",
        installed=True,
        hasCachedContent=True,
    )
    dialog._entries = [entry]
    dialog._sync_client._catalog_service._remote_provider.save_entries([entry])
    item = QtWidgets.QListWidgetItem()
    item.setData(QtCore.Qt.ItemDataRole.UserRole, "component-installed-delete")
    dialog._list.addItem(item)
    dialog._scope_tabs.setCurrentIndex(dialog._TAB_INSTALLED)
    dialog._list.setCurrentRow(0)
    QtWidgets.QApplication.processEvents()

    offload_calls: list[str] = []
    delete_calls: list[str] = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *args, **kwargs: QtWidgets.QMessageBox.Yes)
    monkeypatch.setattr(
        dialog,
        "_offload_selected_component",
        lambda *, local_entry, remote_entry: offload_calls.append(str(remote_entry.record.componentId)) or True,
    )
    monkeypatch.setattr(
        dialog._sync_client,
        "delete_component",
        lambda component_id: delete_calls.append(str(component_id)),
    )

    dialog._on_delete_clicked()

    assert offload_calls == ["component-installed-delete"]
    assert delete_calls == []

    dialog.close()


def test_component_dialog_toolbar_matches_variant_style_for_community(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_browser.subscribe_components_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(ComponentCatalogDialog, "_render_browser_from_state", lambda self, *_args: None)
    dialog = ComponentCatalogDialog(parent=None, node_graph=None)
    entry = F8ComponentEntry(
        record=F8ComponentRecord(
            componentId="component-community-toolbar",
            name="Community Toolbar Component",
            content=_component_payload_for_node(_make_service_node_class()),
        ),
        source=F8ComponentSourceKind.remote_public,
        installed=False,
        subscribed=False,
    )
    dialog._entries = [entry]
    dialog._sync_client._catalog_service._remote_provider.save_entries([entry])
    item = QtWidgets.QListWidgetItem()
    item.setData(QtCore.Qt.ItemDataRole.UserRole, "component-community-toolbar")
    dialog._list.addItem(item)

    dialog._scope_tabs.setCurrentIndex(dialog._TAB_COMMUNITY)
    dialog._list.setCurrentRow(0)
    QtWidgets.QApplication.processEvents()

    assert dialog._btn_subscribe.isHidden() is False
    assert dialog._btn_subscribe.toolTip() == "Subscribe"
    assert dialog._btn_copy_local.isHidden() is False
    assert dialog._btn_copy_local.toolTip() == "Copy to Draft"
    assert dialog._btn_install.isHidden() is True
    assert dialog._btn_upload.isHidden() is True
    assert dialog._btn_delete.isHidden() is True
    assert dialog._btn_edit.isHidden() is True
    assert dialog._btn_visibility.isHidden() is True

    dialog.close()


def test_component_catalog_create_on_canvas_keeps_dialog_open(monkeypatch) -> None:
    _ensure_app()

    class _FakeInsertRequest:
        node_count = 2

    class _FakeGraph:
        def __init__(self) -> None:
            self.prepare_calls: list[tuple[object, str]] = []
            self.placement_calls: list[tuple[object, str]] = []

        def prepare_insert_graph_from_component(self, payload: object, *, component_name: str) -> _FakeInsertRequest:
            self.prepare_calls.append((payload, component_name))
            return _FakeInsertRequest()

        def begin_graph_placement(self, request: object, *, label: str = "") -> None:
            self.placement_calls.append((request, str(label)))

    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_browser.subscribe_components_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(ComponentCatalogDialog, "_render_browser_from_state", lambda self, *_args: None)
    dialog = ComponentCatalogDialog(parent=None, node_graph=_FakeGraph())
    entry = F8ComponentEntry(
        record=F8ComponentRecord(
            componentId="component-create",
            name="Create Component",
            content=_component_payload_for_node(_make_service_node_class()),
        ),
        source=F8ComponentSourceKind.local,
        installed=True,
    )
    accept_calls: list[str] = []

    monkeypatch.setattr(dialog, "_selected_entry", lambda: entry)
    monkeypatch.setattr(dialog, "_ensure_component_hydrated", lambda selected_entry, operation_name: selected_entry)
    monkeypatch.setattr(dialog, "accept", lambda: accept_calls.append("accept"))

    fake_graph = dialog._graph
    assert fake_graph is not None

    dialog._on_insert_clicked()

    assert accept_calls == []
    assert fake_graph.prepare_calls == [(entry.record.content, "Create Component")]
    assert len(fake_graph.placement_calls) == 1
    assert fake_graph.placement_calls[0][1] == "Component: Create Component\n2 nodes"

    dialog.close()


def test_component_catalog_save_as_component_uses_selected_subgraph(monkeypatch) -> None:
    _ensure_app()
    payload = wrap_layout_for_save(
        {
            "nodes": {
                "node_a": {"id": "node_a", "name": "Node A"},
                "node_b": {"id": "node_b", "name": "Node B"},
                "node_c": {"id": "node_c", "name": "Node C"},
            },
            "connections": [
                {"out": ["node_a", "out"], "in": ["node_b", "in"]},
                {"out": ["node_b", "out"], "in": ["node_c", "in"]},
            ],
        }
    )
    graph = _FakeComponentSaveGraph(
        payload=payload,
        selected_nodes=[_FakeComponentSaveSelectedNode("node_a"), _FakeComponentSaveSelectedNode("node_b")],
    )
    saved_records: list[object] = []
    info_messages: list[tuple[str, str]] = []

    class _FakeMetaDialog:
        def __init__(
            self,
            *,
            parent: QtWidgets.QWidget | None,
            title: str,
            name: str,
            description: str,
            tags: list[str],
            overwrite_choices: list[object],
            overwrite_label: str,
            name_validator: object,
        ) -> None:
            del parent, title, overwrite_choices, overwrite_label, name_validator
            self._name = name
            self._description = description
            self._tags = list(tags)

        def exec(self) -> int:
            return QtWidgets.QDialog.Accepted

        def values(self) -> tuple[str, str, list[str], str | None]:
            return (self._name, self._description, list(self._tags), None)

    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_browser.subscribe_components_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(ComponentCatalogDialog, "_render_browser_from_state", lambda self, *_args: None)
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_actions_mixin.AssetOverwriteMetaDialog", _FakeMetaDialog)
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_actions_mixin.upsert_component", lambda record: saved_records.append(record))
    monkeypatch.setattr(
        "f8pystudio.assets.ui.component_catalog_actions_mixin.show_info",
        lambda _parent, title, message: info_messages.append((str(title), str(message))),
    )
    dialog = ComponentCatalogDialog(parent=None, node_graph=graph)

    dialog._on_add_clicked()

    assert len(saved_records) == 1
    saved_record = saved_records[0]
    assert saved_record.name == "Untitled Component"
    assert set(saved_record.content["layout"]["nodes"].keys()) == {"node_a", "node_b"}
    assert saved_record.content["layout"]["connections"] == [{"out": ["node_a", "out"], "in": ["node_b", "in"]}]
    assert info_messages == [("Saved", "Saved component:\nUntitled Component")]

    dialog.close()


def test_component_catalog_save_as_component_overwrite_choices_only_include_drafts(monkeypatch, tmp_path: Path) -> None:
    _ensure_app()
    payload = wrap_layout_for_save(
        {
            "nodes": {
                "node_a": {"id": "node_a", "name": "Node A"},
            },
            "connections": [],
        }
    )
    graph = _FakeComponentSaveGraph(payload=payload, selected_nodes=[])
    saved_records: list[object] = []
    captured_choice_ids: list[str] = []

    class _FakeMetaDialog:
        def __init__(
            self,
            *,
            parent: QtWidgets.QWidget | None,
            title: str,
            name: str,
            description: str,
            tags: list[str],
            overwrite_choices: list[object],
            overwrite_label: str,
            name_validator: object,
        ) -> None:
            del parent, title, overwrite_label, name_validator
            self._name = name
            self._description = description
            self._tags = list(tags)
            captured_choice_ids.extend([str(choice.asset_id) for choice in overwrite_choices])

        def exec(self) -> int:
            return QtWidgets.QDialog.Accepted

        def values(self) -> tuple[str, str, list[str], str | None]:
            return (self._name, self._description, list(self._tags), None)

    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_browser.subscribe_components_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(ComponentCatalogDialog, "_render_browser_from_state", lambda self, *_args: None)
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_actions_mixin.AssetOverwriteMetaDialog", _FakeMetaDialog)
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_actions_mixin.upsert_component", lambda record: saved_records.append(record))
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_actions_mixin.show_info", lambda *_args: None)
    dialog = ComponentCatalogDialog(parent=None, node_graph=graph)
    dialog._sync_client = ComponentSyncClient(
        settings=QtCore.QSettings(str(tmp_path / "component-save-overwrite.ini"), QtCore.QSettings.IniFormat),
        catalog_service=ComponentCatalogService(db_path=tmp_path / "assets.db"),
    )
    dialog._sync_client._catalog_service._remote_provider.save_entries(
        [
            F8ComponentEntry(
                record=F8ComponentRecord(
                    componentId="remote-mine-component",
                    name="Existing Name",
                    description="",
                    content={"schemaVersion": "f8studio-session/1", "layout": {"nodes": {}, "connections": []}},
                    createdAt=component_now_iso(),
                    updatedAt=component_now_iso(),
                ),
                source=F8ComponentSourceKind.remote_private,
                visibility=F8ComponentVisibility.private,
                ownerUserId="u1",
                ownerDisplayName="User One",
                installed=False,
            )
        ]
    )
    _ = dialog._draft_service_for_catalog().create_draft_from_record(
        F8ComponentRecord(
            componentId="draft-existing-component",
            name="Existing Name",
            description="",
            content={"schemaVersion": "f8studio-session/1", "layout": {"nodes": {}, "connections": []}},
            createdAt=component_now_iso(),
            updatedAt=component_now_iso(),
        ),
        origin_kind=F8ComponentDraftOriginKind.new,
        publish_target_asset_id=None,
        publish_base_remote_version_number=None,
        draft_id="draft-existing-component",
    )

    dialog._on_add_clicked()

    assert captured_choice_ids == ["draft-existing-component"]
    assert len(saved_records) == 1

    dialog.close()


def test_component_catalog_save_as_component_uses_full_graph_without_selection(monkeypatch) -> None:
    _ensure_app()
    payload = wrap_layout_for_save(
        {
            "nodes": {
                "node_a": {"id": "node_a", "name": "Node A"},
                "node_b": {"id": "node_b", "name": "Node B"},
            },
            "connections": [
                {"out": ["node_a", "out"], "in": ["node_b", "in"]},
            ],
        }
    )
    graph = _FakeComponentSaveGraph(payload=payload, selected_nodes=[])
    saved_records: list[object] = []

    class _FakeMetaDialog:
        def __init__(
            self,
            *,
            parent: QtWidgets.QWidget | None,
            title: str,
            name: str,
            description: str,
            tags: list[str],
            overwrite_choices: list[object],
            overwrite_label: str,
            name_validator: object,
        ) -> None:
            del parent, title, overwrite_choices, overwrite_label, name_validator
            self._name = name
            self._description = description
            self._tags = list(tags)

        def exec(self) -> int:
            return QtWidgets.QDialog.Accepted

        def values(self) -> tuple[str, str, list[str], str | None]:
            return (self._name, self._description, list(self._tags), None)

    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_browser.subscribe_components_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(ComponentCatalogDialog, "_render_browser_from_state", lambda self, *_args: None)
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_actions_mixin.AssetOverwriteMetaDialog", _FakeMetaDialog)
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_actions_mixin.upsert_component", lambda record: saved_records.append(record))
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_actions_mixin.show_info", lambda *_args: None)
    dialog = ComponentCatalogDialog(parent=None, node_graph=graph)

    dialog._on_add_clicked()

    assert len(saved_records) == 1
    assert saved_records[0].content == payload

    dialog.close()


def test_component_catalog_dialog_defers_initial_remote_refresh(monkeypatch, tmp_path: Path) -> None:
    _ensure_app()
    refresh_calls: list[str] = []
    scheduled_callbacks: list[object] = []

    monkeypatch.setattr("f8pystudio.assets.db.asset_db.assets_db_path", lambda: tmp_path / "assets.db")
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_browser.subscribe_components_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(
        ComponentCatalogDialog,
        "_refresh_remote_catalog_if_needed",
        lambda self: refresh_calls.append("refresh"),
    )
    monkeypatch.setattr(
        QtCore.QTimer,
        "singleShot",
        staticmethod(lambda _delay_ms, callback: scheduled_callbacks.append(callback)),
    )

    dialog = ComponentCatalogDialog(parent=None, node_graph=None)

    assert refresh_calls == []
    refresh_callbacks = [callback for callback in scheduled_callbacks if getattr(callback, "__name__", "") == "_run_initial_remote_refresh"]
    assert len(refresh_callbacks) == 1

    dialog.close()


def test_component_catalog_browser_coalesces_asset_cache_rebuilds(monkeypatch) -> None:
    _ensure_app()
    callbacks: list[object] = []

    def _subscribe(callback):
        callbacks.append(callback)
        return lambda: callbacks.remove(callback) if callback in callbacks else None

    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_browser.subscribe_components_changed", _subscribe)
    monkeypatch.setattr(ComponentCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)
    dialog = ComponentCatalogDialog(parent=None, node_graph=None)

    rebuild_calls: list[str] = []
    monkeypatch.setattr(
        dialog,
        "_rebuild_browser_from_asset_cache_preserving_selection",
        lambda *, preserve_component_id=None: rebuild_calls.append(str(preserve_component_id or "")),
    )

    callback = callbacks[0]
    callback()
    callback()
    QtTest.QTest.qWait(10)
    QtWidgets.QApplication.processEvents()

    assert rebuild_calls == [""]

    dialog.close()


def test_component_catalog_browser_ignores_deleted_rebuild_timer(monkeypatch) -> None:
    _ensure_app()
    callbacks: list[object] = []

    def _subscribe(callback):
        callbacks.append(callback)
        return lambda: callbacks.remove(callback) if callback in callbacks else None

    class _DeletedTimer:
        def isActive(self) -> bool:
            raise RuntimeError("Internal C++ object (PySide6.QtCore.QTimer) already deleted.")

        def start(self, _interval: int) -> None:
            raise AssertionError("start should not run after deleted timer access")

    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_browser.subscribe_components_changed", _subscribe)
    monkeypatch.setattr(ComponentCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)
    dialog = ComponentCatalogDialog(parent=None, node_graph=None)
    dialog._asset_cache_rebuild_timer = _DeletedTimer()  # type: ignore[assignment]

    callback = callbacks[0]
    callback()

    assert callbacks == []

    dialog.close()


def test_component_catalog_render_reuses_single_source_snapshot(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_browser.subscribe_components_changed", lambda _cb: (lambda: None))
    original_render_browser_from_state = ComponentCatalogDialog._render_browser_from_state
    monkeypatch.setattr(ComponentCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)
    dialog = ComponentCatalogDialog(parent=None, node_graph=None)
    monkeypatch.setattr(ComponentCatalogDialog, "_render_browser_from_state", original_render_browser_from_state)

    local_calls: list[str] = []
    remote_calls: list[str] = []
    original_list_catalog_entries = dialog._draft_service_for_catalog().list_catalog_entries
    original_load_remote_entries = dialog._sync_client._catalog_service.load_remote_entries
    monkeypatch.setattr(
        dialog._draft_service_for_catalog(),
        "list_catalog_entries",
        lambda: local_calls.append("local") or original_list_catalog_entries(),
    )
    monkeypatch.setattr(
        dialog._sync_client._catalog_service,
        "load_remote_entries",
        lambda: remote_calls.append("remote") or original_load_remote_entries(),
    )

    dialog._render_browser_from_state()

    assert local_calls == ["local"]
    assert remote_calls == ["remote"]

    dialog.close()


def test_component_catalog_render_skips_list_rebuild_when_signature_is_unchanged(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_browser.subscribe_components_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(ComponentCatalogDialog, "_schedule_initial_remote_refresh_if_needed", lambda self: None)
    dialog = ComponentCatalogDialog(parent=None, node_graph=None)
    entry = F8ComponentEntry(
        record=F8ComponentRecord(
            componentId="component-list-row",
            name="Component List Row",
            description="",
            content={},
            createdAt="2026-04-21T00:00:00+00:00",
            updatedAt="2026-04-21T00:00:00+00:00",
        ),
        source=F8ComponentSourceKind.local,
        installed=True,
        hasCachedContent=True,
        isLocalDraft=True,
    )

    monkeypatch.setattr(
        dialog,
        "_refresh_catalog_source_snapshot",
        lambda: (
            setattr(dialog, "_catalog_local_entries_snapshot", [entry]),
            setattr(dialog, "_catalog_remote_entries_snapshot", []),
        ),
    )
    monkeypatch.setattr(dialog, "_on_selection_changed", lambda: None)
    build_calls: list[str] = []
    original_build_list_row = dialog._build_list_row
    monkeypatch.setattr(
        dialog,
        "_build_list_row",
        lambda current_entry: build_calls.append(str(current_entry.record.componentId)) or original_build_list_row(current_entry),
    )

    dialog._render_browser_from_state()
    dialog._render_browser_from_state()

    assert build_calls == ["component-list-row"]

    dialog.close()


def test_variant_catalog_dialog_defers_initial_remote_refresh(monkeypatch, tmp_path: Path) -> None:
    _ensure_app()
    refresh_calls: list[str] = []
    scheduled_callbacks: list[object] = []

    monkeypatch.setattr("f8pystudio.assets.db.asset_db.assets_db_path", lambda: tmp_path / "assets.db")
    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(
        VariantCatalogDialog,
        "_refresh_remote_catalog_if_needed",
        lambda self: refresh_calls.append("refresh"),
    )
    monkeypatch.setattr(
        QtCore.QTimer,
        "singleShot",
        staticmethod(lambda _delay_ms, callback: scheduled_callbacks.append(callback)),
    )

    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.preview.variant",
        base_node_name="Preview Variant",
        node_graph=None,
    )

    assert refresh_calls == []
    refresh_callbacks = [callback for callback in scheduled_callbacks if getattr(callback, "__name__", "") == "_run_initial_remote_refresh"]
    assert len(refresh_callbacks) == 1

    dialog.close()


def test_variant_catalog_render_skips_list_rebuild_when_signature_is_unchanged(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(VariantCatalogDialog, "_schedule_initial_remote_refresh_if_needed", lambda self: None)
    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.preview.variant",
        base_node_name="Preview Variant",
        node_graph=None,
    )
    entry = F8VariantEntry(
        record=F8VariantRecord(
            variantId="variant-list-row",
            kind=F8VariantKind.service,
            baseNodeType="svc.preview.variant",
            serviceClass="svc.preview.variant",
            operatorClass=None,
            name="Variant List Row",
            description="",
            tags=[],
            spec={},
            createdAt=variant_now_iso(),
            updatedAt=variant_now_iso(),
        ),
        source=F8VariantSourceKind.local,
        installed=True,
        hasCachedContent=True,
        isLocalDraft=True,
    )

    monkeypatch.setattr(
        dialog,
        "_refresh_catalog_source_snapshot",
        lambda: (
            setattr(dialog, "_catalog_local_entries_snapshot", [entry]),
            setattr(dialog, "_catalog_remote_entries_snapshot", []),
        ),
    )
    monkeypatch.setattr(dialog, "_on_selection_changed", lambda: None)
    build_calls: list[str] = []
    original_build_list_row = dialog._build_list_row
    monkeypatch.setattr(
        dialog,
        "_build_list_row",
        lambda current_entry: build_calls.append(str(current_entry.record.variantId)) or original_build_list_row(current_entry),
    )

    dialog._render_browser_from_state()
    dialog._render_browser_from_state()

    assert build_calls == ["variant-list-row"]

    dialog.close()


def test_variant_catalog_render_reuses_single_source_snapshot(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", lambda _cb: (lambda: None))
    original_render_browser_from_state = VariantCatalogDialog._render_browser_from_state
    monkeypatch.setattr(VariantCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)
    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.preview.variant",
        base_node_name="Preview Variant",
        node_graph=None,
    )
    monkeypatch.setattr(VariantCatalogDialog, "_render_browser_from_state", original_render_browser_from_state)

    local_calls: list[str] = []
    remote_calls: list[str] = []
    original_list_catalog_entries = dialog._draft_service_for_catalog().list_catalog_entries
    original_load_remote_entries = dialog._sync_client._catalog_service.load_remote_entries
    monkeypatch.setattr(
        dialog._draft_service_for_catalog(),
        "list_catalog_entries",
        lambda: local_calls.append("local") or original_list_catalog_entries(),
    )
    monkeypatch.setattr(
        dialog._sync_client._catalog_service,
        "load_remote_entries",
        lambda: remote_calls.append("remote") or original_load_remote_entries(),
    )

    dialog._render_browser_from_state()

    assert local_calls == ["local"]
    assert remote_calls == ["remote"]

    dialog.close()


def test_variant_dialog_skips_redundant_preview_refresh_for_same_selection(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(VariantCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)
    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.preview.variant",
        base_node_name="Preview Variant",
        node_graph=None,
    )
    entry = F8VariantEntry(
        record=F8VariantRecord(
            variantId="variant-preview",
            kind=F8VariantKind.service,
            baseNodeType="svc.preview.service",
            serviceClass="svc.preview.service",
            operatorClass=None,
            name="Variant Preview",
            description="",
            tags=[],
            spec={"label": "Variant Preview"},
            createdAt=variant_now_iso(),
            updatedAt=variant_now_iso(),
        ),
        source=F8VariantSourceKind.local,
        installed=True,
        hasCachedContent=True,
    )

    dialog._entries = [entry]
    item = QtWidgets.QListWidgetItem()
    item.setData(QtCore.Qt.ItemDataRole.UserRole, "variant-preview")
    dialog._list.addItem(item)

    preview_calls: list[str] = []
    monkeypatch.setattr(
        dialog._preview,
        "show_variant_record",
        lambda record: preview_calls.append(str(record.variantId)),
    )

    dialog._list.setCurrentRow(0)
    assert preview_calls == ["variant-preview"]

    dialog._on_selection_changed()
    dialog._on_selection_changed()

    assert preview_calls == ["variant-preview"]

    dialog.close()


def test_variant_catalog_browser_coalesces_asset_cache_rebuilds(monkeypatch) -> None:
    _ensure_app()
    callbacks: list[object] = []

    def _subscribe(callback):
        callbacks.append(callback)
        return lambda: callbacks.remove(callback) if callback in callbacks else None

    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", _subscribe)
    monkeypatch.setattr(VariantCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)
    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.preview.variant",
        base_node_name="Preview Variant",
        node_graph=None,
    )

    rebuild_calls: list[str] = []
    monkeypatch.setattr(
        dialog,
        "_rebuild_browser_after_installed_state_changed",
        lambda *, preserve_variant_id=None: rebuild_calls.append(str(preserve_variant_id or "")),
    )

    callback = callbacks[0]
    callback()
    callback()
    QtTest.QTest.qWait(10)
    QtWidgets.QApplication.processEvents()

    assert rebuild_calls == [""]

    dialog.close()


def test_variant_catalog_browser_ignores_deleted_rebuild_timer(monkeypatch) -> None:
    _ensure_app()
    callbacks: list[object] = []

    def _subscribe(callback):
        callbacks.append(callback)
        return lambda: callbacks.remove(callback) if callback in callbacks else None

    class _DeletedTimer:
        def isActive(self) -> bool:
            raise RuntimeError("Internal C++ object (PySide6.QtCore.QTimer) already deleted.")

        def start(self, _interval: int) -> None:
            raise AssertionError("start should not run after deleted timer access")

    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", _subscribe)
    monkeypatch.setattr(VariantCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)
    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.preview.variant",
        base_node_name="Preview Variant",
        node_graph=None,
    )
    dialog._asset_cache_rebuild_timer = _DeletedTimer()  # type: ignore[assignment]

    callback = callbacks[0]
    callback()

    assert callbacks == []

    dialog.close()


def test_component_catalog_ignores_deleted_selection_wrapper() -> None:
    _ensure_app()
    dialog = ComponentCatalogDialog(parent=None, node_graph=None)

    class _DeletedListWidget:
        def currentItem(self) -> None:
            raise RuntimeError("Internal C++ object (PySide6.QtWidgets.QListWidget) already deleted.")

    dialog._list = _DeletedListWidget()  # type: ignore[assignment]

    assert dialog._selected_entry() is None
    dialog._on_selection_changed()

    dialog.close()


def test_variant_dialog_hydration_failure_updates_raw_and_preview(monkeypatch) -> None:
    _ensure_app()
    _run_background_workers_immediately(monkeypatch)
    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(VariantCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)
    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.preview.variant",
        base_node_name="Preview Variant",
        node_graph=None,
    )
    entry = F8VariantEntry(
        record=F8VariantRecord(
            variantId="variant-remote",
            kind=F8VariantKind.service,
            baseNodeType="svc.preview.service",
            serviceClass="svc.preview.service",
            operatorClass=None,
            name="Remote Variant",
            description="",
            tags=[],
            spec={"label": "Remote Variant"},
            createdAt=variant_now_iso(),
            updatedAt=variant_now_iso(),
        ),
        source=F8VariantSourceKind.remote_public,
        installed=False,
    )
    dialog._entries = [entry]
    item = QtWidgets.QListWidgetItem()
    item.setData(QtCore.Qt.ItemDataRole.UserRole, "variant-remote")
    dialog._list.addItem(item)
    dialog._sync_client.load_variant_preview_entry = lambda _entry: (_ for _ in ()).throw(ValueError("boom"))  # type: ignore[method-assign]
    dialog._sync_client.clone_for_background = lambda: dialog._sync_client  # type: ignore[method-assign]

    dialog._list.setCurrentRow(0)
    QtWidgets.QApplication.processEvents()

    assert dialog._preview.current_status_text() == "Remote preview is available on demand."

    dialog._preview._load_deferred_preview()  # type: ignore[attr-defined]
    QtWidgets.QApplication.processEvents()

    assert "load_variant_preview_entry" in dialog._raw.toPlainText()
    assert "boom" in dialog._preview.current_status_text()

    dialog.close()


def test_variant_dialog_install_allows_anonymous_public_variant(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(VariantCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)
    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_actions_mixin.show_info", lambda *_args, **_kwargs: None)
    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.preview.variant",
        base_node_name="Preview Variant",
        node_graph=None,
    )
    entry = F8VariantEntry(
        record=F8VariantRecord(
            variantId="variant-public",
            kind=F8VariantKind.service,
            baseNodeType="svc.preview.service",
            serviceClass="svc.preview.service",
            operatorClass=None,
            name="Public Variant",
            description="",
            tags=[],
            spec={"label": "Public Variant"},
            createdAt=variant_now_iso(),
            updatedAt=variant_now_iso(),
        ),
        source=F8VariantSourceKind.remote_public,
        installed=False,
    )
    install_calls: list[str] = []

    dialog._selected_entry = lambda: entry  # type: ignore[method-assign]
    dialog._selected_remote_entry = lambda: entry  # type: ignore[method-assign]
    dialog._ensure_logged_in = lambda: (_ for _ in ()).throw(AssertionError("install should not require login"))  # type: ignore[method-assign]
    dialog._sync_client.install_variant = lambda variant_id: install_calls.append(str(variant_id)) or copy_model(entry, update={"installed": True, "hasCachedContent": True})  # type: ignore[method-assign]

    dialog._on_install_clicked()

    assert install_calls == ["variant-public"]

    dialog.close()


def test_variant_dialog_remote_preview_loads_on_demand(monkeypatch) -> None:
    _ensure_app()
    _run_background_workers_immediately(monkeypatch)
    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", lambda _cb: (lambda: None))
    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.preview.variant",
        base_node_name="Preview Variant",
        node_graph=None,
    )
    entry = F8VariantEntry(
        record=F8VariantRecord(
            variantId="variant-remote",
            kind=F8VariantKind.service,
            baseNodeType="svc.preview.service",
            serviceClass="svc.preview.service",
            operatorClass=None,
            name="Remote Variant",
            description="",
            tags=[],
            spec={"label": "Remote Variant"},
            createdAt=variant_now_iso(),
            updatedAt=variant_now_iso(),
        ),
        source=F8VariantSourceKind.remote_private,
        installed=False,
    )
    dialog._entries = [entry]
    item = QtWidgets.QListWidgetItem()
    item.setData(QtCore.Qt.ItemDataRole.UserRole, "variant-remote")
    dialog._list.addItem(item)

    cached_entry = copy_model(entry, update={"installed": False, "hasCachedContent": True})
    preview_calls: list[str] = []
    dialog._sync_client.load_variant_preview_entry = (  # type: ignore[method-assign]
        lambda preview_entry: preview_calls.append(str(preview_entry.record.variantId)) or cached_entry
    )
    dialog._sync_client.clone_for_background = lambda: dialog._sync_client  # type: ignore[method-assign]

    dialog._list.setCurrentRow(0)
    QtWidgets.QApplication.processEvents()

    assert dialog._preview.current_status_text() == "Remote preview is available on demand."
    assert preview_calls == []

    dialog._preview._load_deferred_preview()  # type: ignore[attr-defined]
    QtWidgets.QApplication.processEvents()

    assert preview_calls == ["variant-remote"]
    assert dialog._selected_variant_id() == "variant-remote"
    assert dialog._btn_install.isEnabled() is False
    assert dialog._btn_create.isEnabled() is False
    assert dialog._btn_create.isHidden() is True

    dialog.close()


def test_variant_dialog_ignores_variants_changed_after_list_deleted(monkeypatch) -> None:
    _ensure_app()
    callbacks: list[object] = []

    def _subscribe(callback):
        callbacks.append(callback)
        return lambda: callbacks.remove(callback) if callback in callbacks else None

    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", _subscribe)
    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.preview.variant",
        base_node_name="Preview Variant",
        node_graph=None,
    )

    class _DeletedListWidget:
        def currentItem(self) -> None:
            raise RuntimeError("Internal C++ object (PySide6.QtWidgets.QListWidget) already deleted.")

    def _raise_deleted(*_args, **_kwargs) -> None:
        raise RuntimeError("Internal C++ object (PySide6.QtWidgets.QListWidget) already deleted.")

    dialog._list = _DeletedListWidget()  # type: ignore[assignment]
    dialog._render_browser_from_state = _raise_deleted  # type: ignore[method-assign]

    callback = callbacks[0]
    callback()
    QtTest.QTest.qWait(10)
    QtWidgets.QApplication.processEvents()

    assert callbacks == []
    assert dialog._asset_cache_changed_unsubscribe is None

    dialog.close()


def test_variant_dialog_subscribe_also_loads_public_variant(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(VariantCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)
    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_actions_mixin.show_info", lambda *_args, **_kwargs: None)
    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.preview.variant",
        base_node_name="Preview Variant",
        node_graph=None,
    )
    entry = F8VariantEntry(
        record=F8VariantRecord(
            variantId="variant-community",
            kind=F8VariantKind.service,
            baseNodeType="svc.preview.service",
            serviceClass="svc.preview.service",
            operatorClass=None,
            name="Community Variant",
            description="",
            tags=[],
            spec={"label": "Community Variant"},
            createdAt=variant_now_iso(),
            updatedAt=variant_now_iso(),
        ),
        source=F8VariantSourceKind.remote_public,
        installed=False,
        subscribed=False,
    )
    calls: list[str] = []
    subscribed_entry = copy_model(entry, update={"subscribed": True})
    loaded_entry = copy_model(subscribed_entry, update={"installed": True, "hasCachedContent": True})

    dialog._selected_entry = lambda: entry  # type: ignore[method-assign]
    dialog._ensure_logged_in = lambda: True  # type: ignore[method-assign]
    dialog._sync_client.subscribe_variant = lambda variant_id: calls.append(f"subscribe:{variant_id}") or subscribed_entry  # type: ignore[method-assign]
    dialog._sync_client.install_variant = lambda variant_id: calls.append(f"install:{variant_id}") or loaded_entry  # type: ignore[method-assign]

    dialog._on_subscribe_clicked()

    assert calls == ["subscribe:variant-community", "install:variant-community"]

    dialog.close()


def test_variant_dialog_community_actions_hide_load_and_show_fork(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(VariantCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)
    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.preview.variant",
        base_node_name="Preview Variant",
        node_graph=None,
    )
    entry = F8VariantEntry(
        record=F8VariantRecord(
            variantId="variant-community",
            kind=F8VariantKind.service,
            baseNodeType="svc.preview.service",
            serviceClass="svc.preview.service",
            operatorClass=None,
            name="Community Variant",
            description="",
            tags=[],
            spec={"label": "Community Variant"},
            createdAt=variant_now_iso(),
            updatedAt=variant_now_iso(),
        ),
        source=F8VariantSourceKind.remote_public,
        installed=False,
        subscribed=False,
    )
    dialog._entries = [entry]
    dialog._remote_entry_for_variant_id = lambda _variant_id: entry  # type: ignore[method-assign]
    item = QtWidgets.QListWidgetItem()
    item.setData(QtCore.Qt.ItemDataRole.UserRole, "variant-community")
    dialog._list.addItem(item)

    dialog._scope_tabs.setCurrentIndex(dialog._TAB_COMMUNITY)
    dialog._list.setCurrentRow(0)
    QtWidgets.QApplication.processEvents()

    assert dialog._btn_subscribe.isHidden() is False
    assert dialog._btn_subscribe.toolTip() == "Subscribe"
    assert dialog._btn_copy_local.isHidden() is False
    assert dialog._btn_copy_local.toolTip() == "Copy to Draft"
    assert dialog._btn_install.isHidden() is True
    assert dialog._btn_upload.isHidden() is True

    dialog.close()


def test_variant_dialog_mine_toolbar_uses_open_draft_and_load(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(VariantCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)
    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.preview.variant",
        base_node_name="Preview Variant",
        node_graph=None,
    )
    entry = F8VariantEntry(
        record=F8VariantRecord(
            variantId="variant-mine-toolbar",
            kind=F8VariantKind.service,
            baseNodeType="svc.preview.service",
            serviceClass="svc.preview.service",
            operatorClass=None,
            name="Mine Toolbar Variant",
            description="",
            tags=[],
            spec={"label": "Mine Toolbar Variant"},
            createdAt=variant_now_iso(),
            updatedAt=variant_now_iso(),
        ),
        source=F8VariantSourceKind.remote_private,
        visibility=F8VariantVisibility.private,
        ownerUserId="u1",
        ownerDisplayName="User One",
        installed=False,
    )
    dialog._entries = [entry]
    dialog._sync_client._catalog_service._remote_provider.save_entries([entry])
    monkeypatch.setattr(
        dialog._sync_client,
        "current_user",
        lambda: F8VariantRemoteUser(userId="u1", name="User One", email="user-one@example.com"),
    )
    item = QtWidgets.QListWidgetItem()
    item.setData(QtCore.Qt.ItemDataRole.UserRole, "variant-mine-toolbar")
    dialog._list.addItem(item)

    dialog._scope_tabs.setCurrentIndex(dialog._TAB_MINE)
    dialog._list.setCurrentRow(0)
    QtWidgets.QApplication.processEvents()

    assert dialog._btn_copy_local.isHidden() is False
    assert dialog._btn_copy_local.toolTip() == "Open Draft"
    assert dialog._btn_install.isHidden() is False
    assert dialog._btn_install.toolTip() == "Load"
    assert dialog._btn_edit.isHidden() is True

    dialog.close()


def test_variant_dialog_installed_uses_remove_from_installed(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(VariantCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)
    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.preview.variant",
        base_node_name="Preview Variant",
        node_graph=None,
    )
    entry = F8VariantEntry(
        record=F8VariantRecord(
            variantId="variant-installed",
            kind=F8VariantKind.service,
            baseNodeType="svc.preview.service",
            serviceClass="svc.preview.service",
            operatorClass=None,
            name="Installed Variant",
            description="",
            tags=[],
            spec={"label": "Installed Variant"},
            createdAt=variant_now_iso(),
            updatedAt=variant_now_iso(),
        ),
        source=F8VariantSourceKind.remote_private,
        visibility=F8VariantVisibility.private,
        ownerUserId="u1",
        ownerDisplayName="User One",
        installed=True,
        hasCachedContent=True,
    )
    dialog._entries = [entry]
    dialog._sync_client._catalog_service._remote_provider.save_entries([entry])
    item = QtWidgets.QListWidgetItem()
    item.setData(QtCore.Qt.ItemDataRole.UserRole, "variant-installed")
    dialog._list.addItem(item)

    dialog._scope_tabs.setCurrentIndex(dialog._TAB_INSTALLED)
    dialog._list.setCurrentRow(0)
    QtWidgets.QApplication.processEvents()

    assert dialog._btn_delete.isHidden() is False
    assert dialog._btn_delete.toolTip() == "Remove from Installed"

    menu_actions: list[str] = []

    class _FakeMenu:
        def __init__(self, _parent: object) -> None:
            self._actions: list[QtWidgets.QAction] = []

        def addAction(self, text: str) -> QtWidgets.QAction:
            action = QtWidgets.QAction(text, dialog)
            self._actions.append(action)
            return action

        def addSeparator(self) -> QtWidgets.QAction:
            action = QtWidgets.QAction(dialog)
            action.setSeparator(True)
            self._actions.append(action)
            return action

        def actions(self) -> list[QtWidgets.QAction]:
            return list(self._actions)

        def exec(self, _pos: object) -> None:
            menu_actions.extend(action.text() for action in self._actions if not action.isSeparator())

    monkeypatch.setattr(QtWidgets, "QMenu", _FakeMenu)
    dialog._on_list_context_menu_requested(QtCore.QPoint(0, 0))

    assert menu_actions == [
        "Remove from Installed",
        "Pull",
        "History",
    ]

    dialog.close()


def test_variant_catalog_row_without_description_stays_compact_and_shows_remote_version_number(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(VariantCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)

    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.preview.service",
        base_node_name="Preview Service",
        node_graph=None,
    )
    entry = F8VariantEntry(
        record=F8VariantRecord(
            variantId="compact-revision-row",
            kind=F8VariantKind.service,
            baseNodeType="svc.preview.service",
            serviceClass="svc.preview.service",
            operatorClass=None,
            name="Compact Revision Row",
            description="This description should not appear in the row.",
            tags=[],
            spec={"label": "Compact Revision Row"},
            createdAt=variant_now_iso(),
            updatedAt=variant_now_iso(),
        ),
        source=F8VariantSourceKind.remote_private,
        visibility=F8VariantVisibility.private,
        ownerUserId="u1",
        ownerDisplayName="User One",
        remoteVersionNumber=1,
        installed=True,
        hasCachedContent=True,
    )

    row_widget = dialog._build_list_row(entry)
    row_labels = [label.text() for label in row_widget.findChildren(QtWidgets.QLabel)]

    assert row_widget.sizeHint().height() <= 56
    assert "This description should not appear in the row." not in row_labels
    assert "v1" in row_labels

    dialog.close()


def test_variant_dialog_installed_delete_offloads_without_remote_delete(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(VariantCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)
    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.preview.variant",
        base_node_name="Preview Variant",
        node_graph=None,
    )
    entry = F8VariantEntry(
        record=F8VariantRecord(
            variantId="variant-installed-delete",
            kind=F8VariantKind.service,
            baseNodeType="svc.preview.service",
            serviceClass="svc.preview.service",
            operatorClass=None,
            name="Installed Delete Variant",
            description="",
            tags=[],
            spec={"label": "Installed Delete Variant"},
            createdAt=variant_now_iso(),
            updatedAt=variant_now_iso(),
        ),
        source=F8VariantSourceKind.remote_private,
        visibility=F8VariantVisibility.private,
        ownerUserId="u1",
        ownerDisplayName="User One",
        installed=True,
        hasCachedContent=True,
    )
    dialog._entries = [entry]
    dialog._sync_client._catalog_service._remote_provider.save_entries([entry])
    item = QtWidgets.QListWidgetItem()
    item.setData(QtCore.Qt.ItemDataRole.UserRole, "variant-installed-delete")
    dialog._list.addItem(item)
    dialog._scope_tabs.setCurrentIndex(dialog._TAB_INSTALLED)
    dialog._list.setCurrentRow(0)
    QtWidgets.QApplication.processEvents()

    offload_calls: list[str] = []
    delete_calls: list[str] = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *args, **kwargs: QtWidgets.QMessageBox.Yes)
    monkeypatch.setattr(
        dialog,
        "_offload_selected_variant",
        lambda *, local_entry, remote_entry: offload_calls.append(str(remote_entry.record.variantId)) or True,
    )
    monkeypatch.setattr(
        dialog._sync_client,
        "delete_variant",
        lambda variant_id: delete_calls.append(str(variant_id)),
    )

    dialog._on_delete_clicked()

    assert offload_calls == ["variant-installed-delete"]
    assert delete_calls == []

    dialog.close()


def test_variant_dialog_cached_remote_preview_does_not_refetch_content(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(VariantCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)
    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.preview.variant",
        base_node_name="Preview Variant",
        node_graph=None,
    )
    cached_entry = F8VariantEntry(
        record=F8VariantRecord(
            variantId="variant-cached",
            kind=F8VariantKind.service,
            baseNodeType="svc.preview.service",
            serviceClass="svc.preview.service",
            operatorClass=None,
            name="Cached Variant",
            description="",
            tags=[],
            spec={"label": "Cached Variant"},
            createdAt=variant_now_iso(),
            updatedAt=variant_now_iso(),
        ),
        source=F8VariantSourceKind.remote_public,
        installed=False,
        hasCachedContent=True,
    )
    dialog._entries = [cached_entry]
    item = QtWidgets.QListWidgetItem()
    item.setData(QtCore.Qt.ItemDataRole.UserRole, "variant-cached")
    dialog._list.addItem(item)
    dialog._remote_entry_for_variant_id = lambda _variant_id: cached_entry  # type: ignore[method-assign]
    cache_calls: list[str] = []
    dialog._sync_client.cache_variant_content = lambda variant_id: cache_calls.append(str(variant_id)) or cached_entry  # type: ignore[method-assign]

    dialog._list.setCurrentRow(0)
    QtWidgets.QApplication.processEvents()

    assert cache_calls == []
    assert "variant-cached" in dialog._raw.toPlainText()

    dialog.close()


def test_variant_dialog_enables_history_for_local_entry(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(VariantCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)
    dialog = VariantCatalogDialog(
        parent=None,
        base_node_type="svc.preview.variant",
        base_node_name="Preview Variant",
        node_graph=None,
    )
    entry = F8VariantEntry(
        record=F8VariantRecord(
            variantId="variant-local-history",
            kind=F8VariantKind.service,
            baseNodeType="svc.preview.service",
            serviceClass="svc.preview.service",
            operatorClass=None,
            name="Local History Variant",
            description="",
            tags=[],
            spec={"label": "Local History Variant"},
            createdAt=variant_now_iso(),
            updatedAt=variant_now_iso(),
        ),
        source=F8VariantSourceKind.local,
        installed=True,
    )
    dialog._entries = [entry]
    dialog._local_entry_for_variant_id = lambda _variant_id: entry  # type: ignore[method-assign]
    dialog._remote_entry_for_variant_id = lambda _variant_id: None  # type: ignore[method-assign]
    item = QtWidgets.QListWidgetItem()
    item.setData(QtCore.Qt.ItemDataRole.UserRole, "variant-local-history")
    dialog._list.addItem(item)

    dialog._scope_tabs.setCurrentIndex(dialog._TAB_MINE)
    dialog._list.setCurrentRow(0)
    QtWidgets.QApplication.processEvents()

    assert dialog._btn_history.isEnabled() is True

    dialog.close()

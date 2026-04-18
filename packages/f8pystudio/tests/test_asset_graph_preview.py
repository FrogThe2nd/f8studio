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
from f8pystudio.assets.components.component_models import F8ComponentEntry, F8ComponentRecord, F8ComponentSourceKind
from f8pystudio.assets.variants.variant_models import F8VariantEntry, F8VariantSourceKind, variant_now_iso
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
    monkeypatch.setattr(ComponentCatalogDialog, "_reload", lambda self, *_args: None)
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
    monkeypatch.setattr(ComponentCatalogDialog, "_reload", lambda self, *_args: None)
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
    assert load_button.text() == "Load preview manually"

    dialog._preview._load_deferred_preview()  # type: ignore[attr-defined]
    _wait_for_preview_completion(dialog._preview)

    assert len(list(dialog._preview.preview_graph.all_nodes() or [])) == 11

    dialog.close()


def test_component_catalog_context_menu_matches_variant_style_for_community(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_browser.subscribe_components_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(ComponentCatalogDialog, "_reload", lambda self, *_args: None)
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

    assert [action.text() for action in menu.actions()] == ["Subscribe", "Copy to Draft"]

    dialog.close()


def test_component_catalog_context_menu_shows_mine_actions_and_insert(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_browser.subscribe_components_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(ComponentCatalogDialog, "_reload", lambda self, *_args: None)
    dialog = ComponentCatalogDialog(parent=None, node_graph=None)
    entry = F8ComponentEntry(
        record=F8ComponentRecord(
            componentId="component-mine-local",
            name="Mine Local Component",
            content=_component_payload_for_node(_make_service_node_class()),
        ),
        source=F8ComponentSourceKind.local,
        installed=True,
    )
    dialog._entries = [entry]
    dialog._sync_client._catalog_service._local_provider.save_entry(entry)
    item = QtWidgets.QListWidgetItem()
    item.setData(QtCore.Qt.ItemDataRole.UserRole, "component-mine-local")
    dialog._list.addItem(item)

    dialog._scope_tabs.setCurrentIndex(dialog._TAB_MINE)
    dialog._list.setCurrentRow(0)
    QtWidgets.QApplication.processEvents()

    menu = dialog._build_list_context_menu(
        current_tab=dialog._TAB_MINE,
        selected_entry=entry,
        local_entry=entry,
        remote_entry=None,
    )

    assert [action.text() for action in menu.actions()] == ["Offload", "Delete", "Copy to Draft", "Sync", "Make Public", "", "Create on canvas"]

    dialog.close()


def test_component_dialog_toolbar_matches_variant_style_for_community(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_browser.subscribe_components_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(ComponentCatalogDialog, "_reload", lambda self, *_args: None)
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
    monkeypatch.setattr(ComponentCatalogDialog, "_reload", lambda self, *_args: None)
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
        ) -> None:
            del parent, title
            self._name = name
            self._description = description
            self._tags = list(tags)

        def exec(self) -> int:
            return QtWidgets.QDialog.Accepted

        def values(self) -> tuple[str, str, list[str]]:
            return (self._name, self._description, list(self._tags))

    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_browser.subscribe_components_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(ComponentCatalogDialog, "_reload", lambda self, *_args: None)
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_actions_mixin.ProjectAssetMetaDialog", _FakeMetaDialog)
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
        ) -> None:
            del parent, title
            self._name = name
            self._description = description
            self._tags = list(tags)

        def exec(self) -> int:
            return QtWidgets.QDialog.Accepted

        def values(self) -> tuple[str, str, list[str]]:
            return (self._name, self._description, list(self._tags))

    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_browser.subscribe_components_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(ComponentCatalogDialog, "_reload", lambda self, *_args: None)
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_actions_mixin.ProjectAssetMetaDialog", _FakeMetaDialog)
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


def test_variant_dialog_hydration_failure_updates_raw_and_preview(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(VariantCatalogDialog, "_reload", lambda self, *_args, **_kwargs: None)
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
    dialog._sync_client.cache_variant_content = lambda _variant_id: (_ for _ in ()).throw(ValueError("boom"))  # type: ignore[method-assign]

    dialog._list.setCurrentRow(0)
    _wait_for_preview_completion(dialog._preview)

    assert "cache_variant_content" in dialog._raw.toPlainText()
    assert "boom" in dialog._preview.current_status_text()

    dialog.close()


def test_variant_dialog_install_allows_anonymous_public_variant(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(VariantCatalogDialog, "_reload", lambda self, *_args, **_kwargs: None)
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


def test_variant_dialog_defers_reload_during_selection_cache(monkeypatch) -> None:
    _ensure_app()
    callbacks: list[object] = []

    def _subscribe(callback):
        callbacks.append(callback)
        return lambda: callbacks.remove(callback)

    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", _subscribe)
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

    events: list[str] = []
    def _recording_reload(*args, **kwargs) -> None:
        del args, kwargs
        events.append("reload")

    cached_entry = copy_model(entry, update={"installed": False, "hasCachedContent": True})
    dialog._selected_remote_entry = lambda: cached_entry  # type: ignore[method-assign]
    dialog._remote_entry_for_variant_id = lambda _variant_id: cached_entry  # type: ignore[method-assign]

    def _cache_variant_content(_variant_id: str) -> F8VariantEntry:
        events.append("cache:start")
        callback = callbacks[0]
        callback()
        events.append("cache:end")
        return cached_entry

    dialog._reload = _recording_reload  # type: ignore[method-assign]
    dialog._sync_client.cache_variant_content = _cache_variant_content  # type: ignore[method-assign]
    events.clear()

    dialog._list.setCurrentRow(0)
    QtWidgets.QApplication.processEvents()

    assert events[:3] == ["cache:start", "cache:end", "reload"]
    assert dialog._selected_variant_id() == "variant-remote"
    assert dialog._btn_install.isEnabled() is True
    assert dialog._btn_create.isEnabled() is False

    dialog.close()


def test_variant_dialog_subscribe_also_loads_public_variant(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(VariantCatalogDialog, "_reload", lambda self, *_args, **_kwargs: None)
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
    monkeypatch.setattr(VariantCatalogDialog, "_reload", lambda self, *_args, **_kwargs: None)
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


def test_variant_dialog_cached_remote_preview_does_not_refetch_content(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.variant_catalog_browser.subscribe_variants_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(VariantCatalogDialog, "_reload", lambda self, *_args, **_kwargs: None)
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
    monkeypatch.setattr(VariantCatalogDialog, "_reload", lambda self, *_args, **_kwargs: None)
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

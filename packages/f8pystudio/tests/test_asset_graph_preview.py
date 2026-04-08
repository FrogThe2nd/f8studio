from __future__ import annotations

from typing import cast

from qtpy import QtCore, QtTest, QtWidgets

from f8pysdk.specs import F8DataPortSpec, F8OperatorSpec, F8ServiceSpec, F8VariantKind, F8VariantRecord
from f8pysdk.codec import dump_json
from f8pysdk.specs import any_schema, number_schema

from f8pystudio.assets.ui.asset_graph_preview import AssetGraphPreviewPane
from f8pystudio.assets.ui.component_catalog_dialog import ComponentCatalogDialog
from f8pystudio.assets.ui.variant_manager_dialog import VariantManagerDialog
from f8pystudio.assets.components.component_models import F8ComponentEntry, F8ComponentRecord, F8ComponentSourceKind
from f8pystudio.assets.variants.variant_models import F8VariantEntry, F8VariantSourceKind, variant_now_iso
from f8pystudio.nodegraph.node_graph import F8StudioGraph
from f8pystudio.nodegraph.operator_basenode import F8StudioOperatorBaseNode
from f8pystudio.nodegraph.service_basenode import F8StudioServiceBaseNode
from f8pystudio.nodegraph.session_schema import wrap_layout_for_save

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


def test_preview_viewer_ignores_left_click_selection_and_disables_edit_shortcuts() -> None:
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
    QtTest.QTest.mouseClick(
        viewer.viewport(),
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        viewer.viewport().rect().center(),
    )
    QtWidgets.QApplication.processEvents()

    assert pane.preview_graph.selected_nodes() == []

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
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_dialog.subscribe_components_changed", lambda _cb: (lambda: None))
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
    host_graph.widget.close()


def test_component_catalog_dialog_defers_initial_remote_refresh(monkeypatch, tmp_path: Path) -> None:
    _ensure_app()
    refresh_calls: list[str] = []
    scheduled_callbacks: list[object] = []

    monkeypatch.setattr("f8pystudio.assets.db.asset_db.assets_db_path", lambda: tmp_path / "assets.db")
    monkeypatch.setattr("f8pystudio.assets.ui.component_catalog_dialog.subscribe_components_changed", lambda _cb: (lambda: None))
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
    assert len(scheduled_callbacks) == 1

    dialog.close()


def test_variant_dialog_hydration_failure_updates_raw_and_preview(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.variant_manager_dialog.subscribe_variants_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(VariantManagerDialog, "_reload", lambda self, *_args: None)
    dialog = VariantManagerDialog(
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
    dialog._sync_client.hydrate_variant = lambda _variant_id: (_ for _ in ()).throw(ValueError("boom"))  # type: ignore[method-assign]

    dialog._list.setCurrentRow(0)
    _wait_for_preview_completion(dialog._preview)

    assert "hydrate_variant" in dialog._raw.toPlainText()
    assert "boom" in dialog._preview.current_status_text()

    dialog.close()


def test_variant_dialog_install_allows_anonymous_public_variant(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.assets.ui.variant_manager_dialog.subscribe_variants_changed", lambda _cb: (lambda: None))
    monkeypatch.setattr(VariantManagerDialog, "_reload", lambda self, *_args: None)
    monkeypatch.setattr("f8pystudio.assets.ui.variant_manager_dialog.show_info", lambda *_args, **_kwargs: None)
    dialog = VariantManagerDialog(
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
    dialog._ensure_logged_in = lambda: (_ for _ in ()).throw(AssertionError("install should not require login"))  # type: ignore[method-assign]
    dialog._sync_client.hydrate_variant = lambda variant_id: install_calls.append(str(variant_id)) or entry  # type: ignore[method-assign]

    dialog._on_install_clicked()

    assert install_calls == ["variant-public"]

    dialog.close()

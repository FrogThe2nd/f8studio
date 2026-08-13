from __future__ import annotations

import os
import sys
from types import SimpleNamespace

from qtpy import QtCore, QtWidgets
from f8pysdk.specs import F8ServiceSpec

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f8pystudio.assets.variants.variant_ids import build_variant_node_type  # noqa: E402
from f8pystudio.nodegraph.node_roles import NodeRole  # noqa: E402
from f8pystudio.ui.mainwin.main_window import F8StudioMainWin  # noqa: E402
from f8pystudio.ui.mainwin.node_library_widget import F8StudioNodeLibraryWidget  # noqa: E402
from f8pystudio.ui.support.node_category_labels import display_node_category_label  # noqa: E402


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def _build_library_tree(
    widget: F8StudioNodeLibraryWidget,
    *,
    categories_by_node_id: dict[str, str],
) -> None:
    node_classes: dict[str, type] = {}
    node_names: dict[str, list[str]] = {}
    for index, (node_id, category) in enumerate(categories_by_node_id.items(), start=1):
        node_name = f"Node {index}"
        node_classes[node_id] = type(
            f"FakeNode{index}",
            (),
            {
                "NODE_NAME": node_name,
                "SPEC_TEMPLATE": F8ServiceSpec(
                    serviceClass=node_id,
                    label=node_name,
                    paletteCategory=category,
                ),
            },
        )
        node_names[node_name] = [node_id]
    widget._tree._factory = SimpleNamespace(names=node_names, nodes=node_classes)
    widget._tree.activate_tree_build()
    widget._tree._start_tree_build()


class _FakeGraph:
    def __init__(self, pending_node_type: str | None) -> None:
        self._pending_node_type = pending_node_type
        self.cancel_node_placement_calls = 0

    def pending_node_placement_type(self) -> str | None:
        return self._pending_node_type

    def cancel_node_placement(self) -> None:
        self.cancel_node_placement_calls += 1


def test_library_cancels_only_when_pending_variant_missing(monkeypatch) -> None:
    _ensure_app()
    widget = F8StudioNodeLibraryWidget(node_graph=None)
    fake_graph = _FakeGraph(build_variant_node_type("variant_a"))
    widget._node_graph = fake_graph

    monkeypatch.setattr("f8pystudio.ui.mainwin.node_library_widget.variant_exists", lambda _vid: False)
    widget._cancel_invalid_variant_placement_if_needed()
    assert fake_graph.cancel_node_placement_calls == 1


def test_library_keeps_placement_when_pending_variant_still_exists(monkeypatch) -> None:
    _ensure_app()
    widget = F8StudioNodeLibraryWidget(node_graph=None)
    fake_graph = _FakeGraph(build_variant_node_type("variant_a"))
    widget._node_graph = fake_graph

    monkeypatch.setattr("f8pystudio.ui.mainwin.node_library_widget.variant_exists", lambda _vid: True)
    widget._cancel_invalid_variant_placement_if_needed()
    assert fake_graph.cancel_node_placement_calls == 0


def test_library_keeps_placement_for_non_variant(monkeypatch) -> None:
    _ensure_app()
    widget = F8StudioNodeLibraryWidget(node_graph=None)
    fake_graph = _FakeGraph("svc.f8.engine.operator")
    widget._node_graph = fake_graph

    monkeypatch.setattr("f8pystudio.ui.mainwin.node_library_widget.variant_exists", lambda _vid: False)
    widget._cancel_invalid_variant_placement_if_needed()
    assert fake_graph.cancel_node_placement_calls == 0


def test_library_unsubscribes_when_asset_cache_changed_hits_deleted_tree(monkeypatch) -> None:
    _ensure_app()
    callbacks: list[object] = []

    def _subscribe(callback):
        callbacks.append(callback)
        return lambda: callbacks.remove(callback) if callback in callbacks else None

    monkeypatch.setattr("f8pystudio.ui.mainwin.node_library_widget.subscribe_asset_cache_changed", _subscribe)
    widget = F8StudioNodeLibraryWidget(node_graph=None)

    class _DeletedTreeWidget:
        def update(self) -> None:
            raise RuntimeError("Internal C++ object (_F8StudioNodesTreeWidget) already deleted.")

    widget._tree = _DeletedTreeWidget()  # type: ignore[assignment]

    callback = callbacks[0]
    callback()

    assert callbacks == []
    assert widget._unsubscribe_asset_cache_changed is None


def test_library_search_variants_checkbox_persists_user_choice(monkeypatch, tmp_path) -> None:
    _ensure_app()
    settings_path = tmp_path / "node-library.ini"
    settings = QtCore.QSettings(str(settings_path), QtCore.QSettings.IniFormat)
    settings.clear()
    settings.sync()

    monkeypatch.setattr(F8StudioNodeLibraryWidget, "_settings", lambda self: settings)

    widget = F8StudioNodeLibraryWidget(node_graph=None)
    assert widget._search_variants.isChecked() is False

    widget._search_variants.setChecked(True)
    assert widget._search_variants.isChecked() is True
    widget.deleteLater()

    widget2 = F8StudioNodeLibraryWidget(node_graph=None)
    assert widget2._search_variants.isChecked() is True


def test_library_expansion_state_persists_user_choice(monkeypatch, tmp_path) -> None:
    _ensure_app()
    settings_path = tmp_path / "node-library-tree.ini"
    settings = QtCore.QSettings(str(settings_path), QtCore.QSettings.IniFormat)
    settings.clear()
    settings.sync()

    monkeypatch.setattr(F8StudioNodeLibraryWidget, "_settings", lambda self: settings)

    widget = F8StudioNodeLibraryWidget(node_graph=None)
    _build_library_tree(widget, categories_by_node_id={"svc.test.node": "demo.category"})

    category_item = widget._tree.topLevelItem(0)
    assert category_item is not None
    base_item = category_item.child(0)
    assert base_item is not None

    base_item.setExpanded(True)
    category_item.setExpanded(False)
    widget.deleteLater()

    widget2 = F8StudioNodeLibraryWidget(node_graph=None)
    _build_library_tree(widget2, categories_by_node_id={"svc.test.node": "demo.category"})

    restored_category_item = widget2._tree.topLevelItem(0)
    assert restored_category_item is not None
    assert restored_category_item.isExpanded() is False

    restored_category_item.setExpanded(True)
    restored_base_item = restored_category_item.child(0)
    assert restored_base_item is not None
    assert restored_base_item.isExpanded() is True


def test_library_search_refresh_keeps_hidden_category_expansion_state(monkeypatch, tmp_path) -> None:
    _ensure_app()
    settings_path = tmp_path / "node-library-search.ini"
    settings = QtCore.QSettings(str(settings_path), QtCore.QSettings.IniFormat)
    settings.clear()
    settings.sync()

    monkeypatch.setattr(F8StudioNodeLibraryWidget, "_settings", lambda self: settings)

    widget = F8StudioNodeLibraryWidget(node_graph=None)
    _build_library_tree(
        widget,
        categories_by_node_id={
            "svc.test.alpha": "alpha.category",
            "svc.test.beta": "beta.category",
        },
    )

    alpha_item = widget._tree.topLevelItem(0)
    beta_item = widget._tree.topLevelItem(1)
    assert alpha_item is not None
    assert beta_item is not None

    alpha_item.setExpanded(False)
    widget._tree.set_search_text("Node 1")
    widget._tree._start_tree_build()
    widget._tree.set_search_text("")
    widget._tree._start_tree_build()

    restored_alpha_item = widget._tree.topLevelItem(0)
    restored_beta_item = widget._tree.topLevelItem(1)
    assert restored_alpha_item is not None
    assert restored_beta_item is not None
    assert restored_alpha_item.isExpanded() is False
    assert restored_beta_item.isExpanded() is True


def test_library_role_filter_keeps_only_matching_categories() -> None:
    _ensure_app()
    widget = F8StudioNodeLibraryWidget(node_graph=None)
    _build_library_tree(
        widget,
        categories_by_node_id={
            "svc.test.source": "f8.pyengine.input",
            "svc.test.shape": "f8.pyengine.signal",
            "svc.test.output": "f8.pyengine.output",
        },
    )

    widget._tree.set_node_role_filter(NodeRole.OUTPUT)
    widget._tree._start_tree_build()

    assert widget._tree.topLevelItemCount() == 1
    category_item = widget._tree.topLevelItem(0)
    assert category_item is not None
    assert category_item.data(0, widget._tree._ROLE_CATEGORY_ID) == "f8.pyengine.output"


def test_library_role_combo_applies_and_clears_filter() -> None:
    _ensure_app()
    widget = F8StudioNodeLibraryWidget(node_graph=None)

    output_index = widget._role_filter.findData(NodeRole.OUTPUT.value)
    assert output_index >= 0
    widget._role_filter.setCurrentIndex(output_index)
    assert widget._tree._node_role_filter == NodeRole.OUTPUT

    widget._role_filter.setCurrentIndex(0)
    assert widget._tree._node_role_filter is None


def test_display_node_category_label_uses_readable_pyengine_alias() -> None:
    assert display_node_category_label("f8.pyengine.execution") == "PyEngine / Execution"
    assert display_node_category_label("f8.pyengine.analysis") == "PyEngine / Analysis"
    assert display_node_category_label("f8.pyengine.flow") == "PyEngine / Flow"
    assert display_node_category_label("custom.category") == "custom.category"


def test_display_node_category_label_uses_readable_cppengine_alias() -> None:
    assert display_node_category_label("f8.cppengine.analysis") == "CppEngine / Analysis"
    assert display_node_category_label("f8.cppengine.io") == "CppEngine / I/O"
    assert display_node_category_label("f8.cppengine.wave") == "CppEngine / Wave"


def test_display_node_category_label_uses_readable_pystudio_alias() -> None:
    assert display_node_category_label("f8.pystudio.viz") == "PyStudio / Viz"
    assert display_node_category_label("f8.pystudio.control") == "PyStudio / Control"


def test_display_node_category_label_uses_readable_service_alias() -> None:
    assert display_node_category_label("svc") == "Services"


class _FakeViewer:
    def __init__(self, *, graph_active: bool, node_active: bool) -> None:
        self._graph_active = bool(graph_active)
        self._node_active = bool(node_active)

    def is_graph_placement_active(self) -> bool:
        return self._graph_active

    def is_node_placement_active(self) -> bool:
        return self._node_active


class _FakeMainGraph:
    def __init__(self, viewer: _FakeViewer) -> None:
        self._viewer = viewer
        self.cancel_graph_placement_calls = 0
        self.cancel_node_placement_calls = 0

    def viewer(self) -> _FakeViewer:
        return self._viewer

    def cancel_graph_placement(self) -> None:
        self.cancel_graph_placement_calls += 1

    def cancel_node_placement(self) -> None:
        self.cancel_node_placement_calls += 1


def test_escape_cancels_graph_placement_first(monkeypatch) -> None:
    _ensure_app()
    fake_app = SimpleNamespace(activePopupWidget=lambda: None)
    monkeypatch.setattr(QtWidgets.QApplication, "instance", staticmethod(lambda: fake_app))

    fake_graph = _FakeMainGraph(_FakeViewer(graph_active=True, node_active=True))
    fake_main = SimpleNamespace(studio_graph=fake_graph)

    F8StudioMainWin._on_escape_cancel_placement(fake_main)
    assert fake_graph.cancel_graph_placement_calls == 1
    assert fake_graph.cancel_node_placement_calls == 0


def test_escape_cancels_node_placement_when_no_graph_placement(monkeypatch) -> None:
    _ensure_app()
    fake_app = SimpleNamespace(activePopupWidget=lambda: None)
    monkeypatch.setattr(QtWidgets.QApplication, "instance", staticmethod(lambda: fake_app))

    fake_graph = _FakeMainGraph(_FakeViewer(graph_active=False, node_active=True))
    fake_main = SimpleNamespace(studio_graph=fake_graph)

    F8StudioMainWin._on_escape_cancel_placement(fake_main)
    assert fake_graph.cancel_graph_placement_calls == 0
    assert fake_graph.cancel_node_placement_calls == 1


def test_escape_noop_when_popup_active(monkeypatch) -> None:
    _ensure_app()
    fake_app = SimpleNamespace(activePopupWidget=lambda: object())
    monkeypatch.setattr(QtWidgets.QApplication, "instance", staticmethod(lambda: fake_app))

    fake_graph = _FakeMainGraph(_FakeViewer(graph_active=True, node_active=True))
    fake_main = SimpleNamespace(studio_graph=fake_graph)
    F8StudioMainWin._on_escape_cancel_placement(fake_main)
    assert fake_graph.cancel_graph_placement_calls == 0
    assert fake_graph.cancel_node_placement_calls == 0



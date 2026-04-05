from __future__ import annotations

import os
import sys
from types import SimpleNamespace

from qtpy import QtCore, QtWidgets

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from f8pystudio.assets.variants.variant_ids import build_variant_node_type  # noqa: E402
from f8pystudio.ui.mainwin.main_window import F8StudioMainWin  # noqa: E402
from f8pystudio.ui.mainwin.node_library_widget import F8StudioNodeLibraryWidget  # noqa: E402


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


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

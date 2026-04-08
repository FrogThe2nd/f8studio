from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from types import ModuleType
from unittest.mock import patch
import uuid

from qtpy import QtCore, QtWidgets

from f8pysdk.specs import F8OperatorSpec

from f8pystudio.ui.mainwin.ai_assist_sidebar import AiAssistSidebarWidget
from f8pystudio.ui.support import webengine_utils


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


class _FakeWebPage(QtCore.QObject):
    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.web_channel = None

    def setWebChannel(self, channel: object) -> None:
        self.web_channel = channel


class _FakeWebEngineView(QtWidgets.QWidget):
    created: list["_FakeWebEngineView"] = []

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._page = _FakeWebPage(self)
        self.html = ""
        self.base_url = None
        self.created.append(self)

    def page(self) -> _FakeWebPage:
        return self._page

    def setHtml(self, html: str, base_url=None) -> None:
        self.html = html
        self.base_url = base_url


class _FakeWebChannel(QtCore.QObject):
    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.objects: dict[str, object] = {}

    def registerObject(self, name: str, obj: object) -> None:
        self.objects[name] = obj


class _FakeGraph(QtCore.QObject):
    node_selected = QtCore.Signal(object)
    node_selection_changed = QtCore.Signal(list, list)
    nodes_deleted = QtCore.Signal(list)
    property_changed = QtCore.Signal(object, str, object)
    port_connected = QtCore.Signal(object, object)
    port_disconnected = QtCore.Signal(object, object)

    def __init__(self) -> None:
        super().__init__()
        self._selected_nodes: list[object] = []

    def selected_nodes(self) -> list[object]:
        return list(self._selected_nodes)

    def set_selected_nodes(self, nodes: list[object]) -> None:
        self._selected_nodes = list(nodes)
        self.node_selection_changed.emit(list(nodes), [])


@dataclass
class _FakeSnapshotNode:
    id: str
    _name: str
    spec: F8OperatorSpec

    def name(self) -> str:
        return self._name

    def effective_state_fields(self) -> list[object]:
        return []

    def get_property(self, name: str) -> object:
        _ = name
        return None

    def input_ports(self) -> list[object]:
        return []

    def output_ports(self) -> list[object]:
        return []


def _install_fake_pyside6(monkeypatch) -> None:
    py_side6 = ModuleType("PySide6")
    qt_web_channel = ModuleType("QtWebChannel")
    qt_web_channel.QWebChannel = _FakeWebChannel
    qt_web_engine_widgets = ModuleType("QtWebEngineWidgets")
    qt_web_engine_widgets.QWebEngineView = _FakeWebEngineView
    py_side6.QtWebChannel = qt_web_channel
    py_side6.QtWebEngineWidgets = qt_web_engine_widgets
    monkeypatch.setitem(sys.modules, "PySide6", py_side6)


def _make_sidebar(monkeypatch) -> tuple[AiAssistSidebarWidget, _FakeGraph]:
    _ensure_app()
    _install_fake_pyside6(monkeypatch)
    graph = _FakeGraph()
    temp_dir = Path(".tmp") / "test_ai_assist_sidebar" / uuid.uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    store_path = temp_dir / "ai_providers.json"
    with patch("f8pystudio.ui.mainwin.ai_assist_sidebar.AiProviderStore._resolve_storage_path", return_value=store_path):
        widget = AiAssistSidebarWidget(studio_graph=graph)
    return widget, graph


def _reset_webengine_prewarm_state() -> None:
    webengine_utils._WEBENGINE_PROFILE_CONFIGURED = False
    webengine_utils._WEBENGINE_VIEW_PREWARMED = False
    webengine_utils._WEBENGINE_PREWARM_VIEW = None


def _make_node(node_id: str, name: str) -> _FakeSnapshotNode:
    return _FakeSnapshotNode(
        id=node_id,
        _name=name,
        spec=F8OperatorSpec(serviceClass="f8.pydl", operatorClass="f8.sample", label=name),
    )


def test_sidebar_pin_is_frozen_until_cleared(monkeypatch) -> None:
    widget, graph = _make_sidebar(monkeypatch)
    first = _make_node("node-a", "Node A")
    second = _make_node("node-b", "Node B")

    graph.set_selected_nodes([first])
    QtWidgets.QApplication.processEvents()

    assert widget._pin_context_btn.isEnabled()
    assert "Node A" in widget._selected_node_label.text()

    widget._pin_selected_context()
    assert "Node A" in widget._pinned_node_label.text()

    graph.set_selected_nodes([second])
    QtWidgets.QApplication.processEvents()

    assert "Node B" in widget._selected_node_label.text()
    assert "Node A" in widget._pinned_node_label.text()


def test_sidebar_supports_multi_select_subgraph_context_and_reset_clears_pin(monkeypatch) -> None:
    widget, graph = _make_sidebar(monkeypatch)
    first = _make_node("node-a", "Node A")
    second = _make_node("node-b", "Node B")

    graph.set_selected_nodes([first, second])
    QtWidgets.QApplication.processEvents()

    assert widget._pin_context_btn.isEnabled()
    assert "2 selected nodes" in widget._selected_node_label.text()
    widget._pin_selected_context()
    assert "2 selected nodes" in widget._pinned_node_label.text()
    assert widget._clear_context_btn.isEnabled()

    widget._ai_bridge.reset_chat_history()
    QtWidgets.QApplication.processEvents()

    assert "Pin: none" == widget._pinned_node_label.text()
    assert not widget._clear_context_btn.isEnabled()


def test_take_prewarmed_webengine_view_returns_cached_instance(monkeypatch) -> None:
    _ensure_app()
    _install_fake_pyside6(monkeypatch)
    _FakeWebEngineView.created = []
    _reset_webengine_prewarm_state()
    monkeypatch.setattr("f8pystudio.ui.support.webengine_utils.configure_default_webengine_profile", lambda: None)

    assert webengine_utils.prewarm_webengine_view() is True
    assert len(_FakeWebEngineView.created) == 1
    prewarmed_view = _FakeWebEngineView.created[0]
    parent = QtWidgets.QWidget()

    taken_view = webengine_utils.take_prewarmed_webengine_view(parent=parent)

    assert taken_view is prewarmed_view
    assert taken_view.parent() is parent
    assert webengine_utils._WEBENGINE_PREWARM_VIEW is None


def test_sidebar_reuses_prewarmed_webengine_view(monkeypatch) -> None:
    _ensure_app()
    _install_fake_pyside6(monkeypatch)
    _FakeWebEngineView.created = []
    _reset_webengine_prewarm_state()
    monkeypatch.setattr("f8pystudio.ui.support.webengine_utils.configure_default_webengine_profile", lambda: None)

    assert webengine_utils.prewarm_webengine_view() is True
    prewarmed_view = webengine_utils._WEBENGINE_PREWARM_VIEW
    assert prewarmed_view is not None

    widget, _graph = _make_sidebar(monkeypatch)

    assert widget._view is prewarmed_view
    assert len(_FakeWebEngineView.created) == 1
    assert widget._view.parent() is widget
    assert widget._view.base_url is not None

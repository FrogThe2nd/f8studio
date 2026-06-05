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
from f8pystudio.ui.support.ai_assist_page import build_ai_assist_html
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
        self.stopped = False
        self.urls: list[object] = []
        self.deleted = False
        self.created.append(self)

    def page(self) -> _FakeWebPage:
        return self._page

    def setHtml(self, html: str, base_url=None) -> None:
        self.html = html
        self.base_url = base_url

    def stop(self) -> None:
        self.stopped = True

    def setUrl(self, url: object) -> None:
        self.urls.append(url)

    def deleteLater(self) -> None:  # type: ignore[override]
        self.deleted = True
        super().deleteLater()


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
        self._undo_stack = _FakeUndoStack()

    def selected_nodes(self) -> list[object]:
        return list(self._selected_nodes)

    def set_selected_nodes(self, nodes: list[object]) -> None:
        self._selected_nodes = list(nodes)
        self.node_selection_changed.emit(list(nodes), [])


class _FakeUndoStack:
    def index(self) -> int:
        return 7


class _FakePropertyEditor:
    def __init__(self, node_id: str = "") -> None:
        self.node_id = node_id

    def current_node_id(self) -> str:
        return self.node_id


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


def test_sidebar_selection_label_tracks_current_selection(monkeypatch) -> None:
    widget, graph = _make_sidebar(monkeypatch)
    first = _make_node("node-a", "Node A")
    second = _make_node("node-b", "Node B")

    graph.set_selected_nodes([first])
    QtWidgets.QApplication.processEvents()

    assert "Node A" in widget._selected_node_label.text()

    graph.set_selected_nodes([second])
    QtWidgets.QApplication.processEvents()

    assert "Node B" in widget._selected_node_label.text()


def test_sidebar_selection_is_tool_state_not_auto_chat_context(monkeypatch) -> None:
    widget, graph = _make_sidebar(monkeypatch)
    first = _make_node("node-a", "Node A")

    graph.set_selected_nodes([first])
    QtWidgets.QApplication.processEvents()

    prompt = widget._ai_bridge._get_system_prompt("Base prompt.")
    assert "Focused Graph Subgraph Snapshot" not in prompt
    assert "Node A" not in prompt
    assert "PyStudio Graph Tools" in prompt
    assert "graph_ui_context" in prompt
    assert "graph_find_nodes" in prompt
    assert "graph_diagnostics" in prompt
    assert widget._tools_button.text().startswith("Tools: ")
    assert widget._tools_button.text() != "Tools: off"
    assert widget.graph_ui_context() == {
        "graphRevision": 7,
        "selectedNodeIds": ["node-a"],
        "selectionLabel": "Node A",
        "selectionCount": 1,
        "propertyPanelNodeId": "",
        "primaryNodeId": "node-a",
        "primaryNodeSource": "singleSelection",
    }


def test_sidebar_injects_runtime_bridge_and_logs_into_graph_tools(monkeypatch) -> None:
    _ensure_app()
    _install_fake_pyside6(monkeypatch)
    graph = _FakeGraph()
    runtime_bridge = object()
    log_source = object()
    observation_source = object()
    created: list[dict[str, object]] = []

    class FakeToolExecutor:
        def __init__(
            self,
            studio_graph: object,
            *,
            bridge: object | None = None,
            log_source: object | None = None,
            observation_source: object | None = None,
            ui_context_source: object | None = None,
            on_graph_patch_applied=None,
            on_tool_trace=None,
            on_tool_approval_requested=None,
            parent: object | None = None,
        ) -> None:
            created.append(
                {
                    "studio_graph": studio_graph,
                    "bridge": bridge,
                    "log_source": log_source,
                    "observation_source": observation_source,
                    "ui_context_source": ui_context_source,
                    "on_graph_patch_applied": on_graph_patch_applied,
                    "on_tool_trace": on_tool_trace,
                    "on_tool_approval_requested": on_tool_approval_requested,
                    "parent": parent,
                }
            )

        def resolve_approval(self, approval_id: str, approved: bool) -> None:
            _ = (approval_id, approved)

    class FakeGraphTools:
        def __init__(self, executor: object) -> None:
            self.executor = executor

        def available_tools(self) -> tuple[object, ...]:
            return (self.executor,)

        def available_tool_names(self) -> tuple[str, ...]:
            return ("fake_tool",)

        def available_codeact_diagnostic_tools(self) -> tuple[object, ...]:
            return (self.executor,)

    monkeypatch.setattr("f8pystudio.ui.mainwin.ai_assist_sidebar.LocalStudioGraphToolExecutor", FakeToolExecutor)
    monkeypatch.setattr("f8pystudio.ui.mainwin.ai_assist_sidebar.LocalStudioGraphTools", FakeGraphTools)
    temp_dir = Path(".tmp") / "test_ai_assist_sidebar" / uuid.uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    store_path = temp_dir / "ai_providers.json"
    with patch("f8pystudio.ui.mainwin.ai_assist_sidebar.AiProviderStore._resolve_storage_path", return_value=store_path):
        widget = AiAssistSidebarWidget(
            studio_graph=graph,
            runtime_bridge=runtime_bridge,
            log_source=log_source,
            observation_source=observation_source,
        )

    assert created
    assert created[0]["studio_graph"] is graph
    assert created[0]["bridge"] is runtime_bridge
    assert created[0]["log_source"] is log_source
    assert created[0]["observation_source"] is observation_source
    assert created[0]["ui_context_source"] is widget
    assert created[0]["on_tool_trace"] == widget._ai_bridge.publish_tool_trace
    assert created[0]["on_tool_approval_requested"] == widget._ai_bridge.publish_tool_approval
    assert widget._ai_bridge._agent_tools
    assert widget._graph_tool_names == ("fake_tool",)
    widget._populate_graph_tools_menu()
    tool_menu_texts = [action.text() for action in widget._tools_menu.actions() if not action.isSeparator()]
    assert tool_menu_texts == ["fake_tool"]
    widget._populate_graph_skills_menu()
    skill_menu_texts = [action.text() for action in widget._skills_menu.actions() if not action.isSeparator()]
    assert skill_menu_texts == ["CodeAct diagnostics: unavailable"]


def test_sidebar_supports_multi_select_ui_context(monkeypatch) -> None:
    _ensure_app()
    _install_fake_pyside6(monkeypatch)
    graph = _FakeGraph()
    property_editor = _FakePropertyEditor("node-b")
    temp_dir = Path(".tmp") / "test_ai_assist_sidebar" / uuid.uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    store_path = temp_dir / "ai_providers.json"
    with patch("f8pystudio.ui.mainwin.ai_assist_sidebar.AiProviderStore._resolve_storage_path", return_value=store_path):
        widget = AiAssistSidebarWidget(studio_graph=graph, property_editor=property_editor)
    first = _make_node("node-a", "Node A")
    second = _make_node("node-b", "Node B")

    graph.set_selected_nodes([first, second])
    QtWidgets.QApplication.processEvents()

    assert widget._selected_node_label.text().startswith("Sel:")
    assert "Selected nodes: 2" in widget._selected_node_label.toolTip()
    assert widget.graph_ui_context() == {
        "graphRevision": 7,
        "selectedNodeIds": ["node-a", "node-b"],
        "selectionLabel": "2 selected nodes",
        "selectionCount": 2,
        "propertyPanelNodeId": "node-b",
        "primaryNodeId": "node-b",
        "primaryNodeSource": "propertyPanel",
    }


def test_ai_assist_html_includes_tool_trace_and_approval_handlers() -> None:
    html = build_ai_assist_html()

    assert "tool_trace_ready" in html
    assert "tool_approval_requested" in html
    assert "resolve_tool_approval" in html
    assert "f8-agent-tool-trace" in html
    assert "f8-agent-approval" in html
    assert "list_conversations" in html
    assert "save_conversation_messages" in html
    assert "set_active_conversation" in html
    assert "f8-ai-conversation-select" in html
    assert "f8-ai-delete-conversation" in html


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


def test_release_prewarmed_webengine_view_tears_down_cached_instance(monkeypatch) -> None:
    _ensure_app()
    _install_fake_pyside6(monkeypatch)
    _FakeWebEngineView.created = []
    _reset_webengine_prewarm_state()
    monkeypatch.setattr("f8pystudio.ui.support.webengine_utils.configure_default_webengine_profile", lambda: None)

    assert webengine_utils.prewarm_webengine_view() is True
    prewarmed_view = _FakeWebEngineView.created[0]

    webengine_utils.release_prewarmed_webengine_view()

    assert webengine_utils._WEBENGINE_PREWARM_VIEW is None
    assert webengine_utils._WEBENGINE_VIEW_PREWARMED is False
    assert prewarmed_view.stopped is True
    assert prewarmed_view.page().web_channel is None
    assert prewarmed_view.deleted is True


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


def test_sidebar_shutdown_releases_webengine_view(monkeypatch) -> None:
    widget, _graph = _make_sidebar(monkeypatch)
    view = widget._view
    assert isinstance(view, _FakeWebEngineView)

    widget.shutdown()

    assert view.stopped is True
    assert view.page().web_channel is None
    assert view.deleted is True

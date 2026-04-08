from __future__ import annotations

from typing import Any

from qtpy import QtCore, QtWidgets

from f8pysdk.specs import F8Command, F8OperatorSpec, F8ServiceSpec
from f8pysdk.command_state import command_input_state_field

from f8pystudio.nodegraph.items.inline_command_panel import (
    COMMAND_INLINE_BUTTON_STYLE,
    _on_command_pressed,
    _restore_selected_node_ids,
    _snapshot_selected_node_ids,
    ensure_inline_command_rows,
    refresh_inline_command_rows,
    invoke_command,
)
from f8pystudio.nodegraph.items.service_node_graph_hooks import on_bridge_service_process_state


class _FakeNode:
    def __init__(self, node_id: str, selected: bool = False) -> None:
        self.id = node_id
        self.selected = selected

    def set_property(self, name: str, value: Any, push_undo: bool = True) -> None:
        del push_undo
        if name == "selected":
            self.selected = bool(value)


class _FakeGraph:
    def __init__(self, nodes: list[_FakeNode]) -> None:
        self._nodes = list(nodes)

    def all_nodes(self) -> list[_FakeNode]:
        return list(self._nodes)

    def selected_nodes(self) -> list[_FakeNode]:
        return [node for node in self._nodes if node.selected]


class _FakeBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.state_writes: list[tuple[str, str, str, Any]] = []

    def invoke_remote_command(self, service_id: str, name: str, args: dict[str, Any] | None = None) -> None:
        self.calls.append((service_id, name, dict(args or {})))

    def set_remote_state(self, service_id: str, node_id: str, field: str, value: Any) -> None:
        self.state_writes.append((service_id, node_id, field, value))


class _FakeNodeItem(QtWidgets.QGraphicsRectItem):
    def __init__(self, *, graph: _FakeGraph, service_running: bool = True) -> None:
        super().__init__(0.0, 0.0, 10.0, 10.0)
        self._fake_graph = graph
        self._service_running = service_running
        self._bridge_obj = _FakeBridge()
        self._invoke_count = 0
        self._draw_count = 0
        self.id = "A"

        self._command_inline_proxies: dict[str, QtWidgets.QGraphicsProxyWidget] = {}
        self._command_inline_headers: dict[str, QtWidgets.QWidget] = {}
        self._command_inline_buttons: dict[str, QtWidgets.QPushButton] = {}
        self._command_inline_descriptions: dict[str, str] = {}
        self._command_inline_serials: dict[str, str] = {}
        self._tooltip_filters: list[Any] = []

        self._backend = _FakeBackendNode()

    def _graph(self) -> _FakeGraph:
        return self._fake_graph

    def _invoke_command(self, command: Any) -> None:
        del command
        self._invoke_count += 1
        # Simulate NodeGraph selection side effect: command press briefly selects node A.
        for node in self._fake_graph.all_nodes():
            node.selected = (node.id == "A")

    def _ensure_bridge_process_hook(self) -> None:
        return

    def _backend_node(self) -> Any:
        return self._backend

    def _is_service_running(self) -> bool:
        return self._service_running

    def _bridge(self) -> _FakeBridge:
        return self._bridge_obj

    def _service_id(self) -> str:
        return "svcA"

    def viewer(self) -> None:
        return None

    def _schema_enum_items(self, schema: Any) -> list[str]:
        del schema
        return []

    def _schema_numeric_range(self, schema: Any) -> tuple[float | None, float | None]:
        del schema
        return None, None

    def _invalidate_layout_metrics(self) -> None:
        return

    def _prepare_layout_metrics(self) -> None:
        return

    def sync_proxy_mode(self, *, force: bool = False) -> None:
        del force
        return

    def _refresh_inline_command_rows(self) -> None:
        refresh_inline_command_rows(self)

    def draw_node(self) -> None:
        self._draw_count += 1


class _FakeBackendNode:
    def __init__(self) -> None:
        self.spec = None
        self.commands: list[Any] = [_FakeCommand("Run", "Run command", True, [])]

    def effective_commands(self) -> list[Any]:
        return list(self.commands)


class _FakeCommand:
    def __init__(self, name: str, description: str, show_on_node: bool, params: list[Any]) -> None:
        self.name = name
        self.description = description
        self.showOnNode = show_on_node
        self.params = list(params)


class _InvokeNodeItem:
    def __init__(self, *, service_running: bool, backend_node: Any | None = None, node_id: str = "nodeA") -> None:
        self._service_running = service_running
        self._bridge_obj = _FakeBridge()
        self._backend = backend_node
        self.id = node_id

    def _bridge(self) -> _FakeBridge:
        return self._bridge_obj

    def _service_id(self) -> str:
        return "svcA"

    def _is_service_running(self) -> bool:
        return self._service_running

    def _backend_node(self) -> Any | None:
        return self._backend


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def test_snapshot_and_restore_selected_ids() -> None:
    _ensure_app()

    a = _FakeNode("A", selected=False)
    b = _FakeNode("B", selected=True)
    graph = _FakeGraph([a, b])
    node_item = _FakeNodeItem(graph=graph)

    ids = _snapshot_selected_node_ids(node_item)
    assert ids == ["B"]

    _restore_selected_node_ids(node_item, ["A"])
    assert a.selected is True
    assert b.selected is False


def test_invoke_command_skips_when_service_not_running() -> None:
    node_item = _InvokeNodeItem(service_running=False)

    invoke_command(node_item, _FakeCommand("Run", "Run command", True, []))

    assert node_item._bridge_obj.calls == []
    assert node_item._bridge_obj.state_writes == []


def test_ensure_inline_command_rows_creates_header_only_command_rows() -> None:
    _ensure_app()
    node_item = _FakeNodeItem(graph=_FakeGraph([_FakeNode("A")]))

    ensure_inline_command_rows(node_item)

    assert list(node_item._command_inline_proxies.keys()) == ["Run"]
    assert list(node_item._command_inline_buttons.keys()) == ["Run"]
    button = node_item._command_inline_buttons["Run"]
    assert "QToolButton:pressed" in button.styleSheet()
    assert button.styleSheet() == COMMAND_INLINE_BUTTON_STYLE


def test_ensure_inline_command_rows_reuses_row_when_only_description_changes() -> None:
    _ensure_app()
    node_item = _FakeNodeItem(graph=_FakeGraph([_FakeNode("A")]))

    ensure_inline_command_rows(node_item)

    proxy_before = node_item._command_inline_proxies["Run"]
    button_before = node_item._command_inline_buttons["Run"]
    node_item._backend.commands = [_FakeCommand("Run", "Updated description", True, [])]

    ensure_inline_command_rows(node_item)

    assert node_item._command_inline_proxies["Run"] is proxy_before
    assert node_item._command_inline_buttons["Run"] is button_before
    assert node_item._command_inline_descriptions["Run"] == "Updated description"
    assert button_before.toolTip() == "Updated description"


def test_ensure_inline_command_rows_removes_row_when_show_on_node_becomes_false() -> None:
    _ensure_app()
    node_item = _FakeNodeItem(graph=_FakeGraph([_FakeNode("A")]))

    ensure_inline_command_rows(node_item)
    assert "Run" in node_item._command_inline_proxies

    node_item._backend.commands = [_FakeCommand("Run", "Run command", False, [])]
    ensure_inline_command_rows(node_item)

    assert node_item._command_inline_proxies == {}
    assert node_item._command_inline_buttons == {}
    assert node_item._command_inline_descriptions == {}


def test_ensure_inline_command_rows_disposes_detached_widget_without_reparent_flash(monkeypatch) -> None:
    _ensure_app()
    node_item = _FakeNodeItem(graph=_FakeGraph([_FakeNode("A")]))
    ensure_inline_command_rows(node_item)
    old_widget = node_item._command_inline_proxies["Run"].widget()
    assert old_widget is not None

    seen: list[tuple[QtWidgets.QWidget, str]] = []

    def _record(widget: QtWidgets.QWidget | None, *, context: str) -> None:
        if widget is not None:
            seen.append((widget, context))

    monkeypatch.setattr(
        "f8pystudio.nodegraph.items.inline_command_panel.dispose_detached_proxy_widget",
        _record,
    )

    node_item._backend.commands = [_FakeCommand("Run 2", "Second command", True, [])]
    ensure_inline_command_rows(node_item)

    assert seen == [(old_widget, "inline-command-remove:Run")]


def test_refresh_inline_command_rows_updates_existing_button_state() -> None:
    _ensure_app()
    node_item = _FakeNodeItem(graph=_FakeGraph([_FakeNode("A")]))
    ensure_inline_command_rows(node_item)

    button = node_item._command_inline_buttons["Run"]
    assert button.isEnabled() is True

    node_item._service_running = False
    refresh_inline_command_rows(node_item)

    assert button.isEnabled() is False
    assert button.cursor().shape() == QtCore.Qt.ArrowCursor
    assert "Service not running" in button.toolTip()


def test_service_process_hook_refreshes_new_command_row_buttons() -> None:
    _ensure_app()
    node_item = _FakeNodeItem(graph=_FakeGraph([_FakeNode("A")]))
    ensure_inline_command_rows(node_item)
    button = node_item._command_inline_buttons["Run"]

    node_item._service_running = False
    on_bridge_service_process_state(node_item, "A", False)

    assert button.isEnabled() is False
    assert "Service not running" in button.toolTip()


def test_ensure_inline_command_rows_logs_and_skips_failed_row_build(monkeypatch, caplog) -> None:
    _ensure_app()
    node_item = _FakeNodeItem(graph=_FakeGraph([_FakeNode("A")]))

    def _fail_build(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "f8pystudio.nodegraph.items.inline_command_panel._build_command_row_widget",
        _fail_build,
    )

    ensure_inline_command_rows(node_item)

    assert node_item._command_inline_proxies == {}
    assert "build command row failed" in caplog.text


def test_ensure_inline_command_rows_builds_rows() -> None:
    _ensure_app()
    node_item = _FakeNodeItem(graph=_FakeGraph([_FakeNode("A")]))

    ensure_inline_command_rows(node_item)

    assert list(node_item._command_inline_proxies.keys()) == ["Run"]


def test_invoke_command_uses_hidden_input_state_for_operator_commands() -> None:
    operator_spec = F8OperatorSpec(
        serviceClass="f8.test.service",
        operatorClass="f8.test.operator",
        label="Operator",
        commands=[F8Command(name="Run", description="Run command", showOnNode=True, params=[])],
    )
    backend = type("Backend", (), {"spec": operator_spec})()
    node_item = _InvokeNodeItem(service_running=True, backend_node=backend, node_id="opA")

    invoke_command(node_item, _FakeCommand("Run", "Run command", True, []))

    assert node_item._bridge_obj.calls == []
    assert node_item._bridge_obj.state_writes == [
        ("svcA", "opA", command_input_state_field("Run"), {})
    ]


def test_invoke_command_uses_hidden_input_state_for_service_commands() -> None:
    service_spec = F8ServiceSpec(
        serviceClass="f8.test.service",
        label="Service",
        commands=[F8Command(name="Run", description="Run command", showOnNode=True, params=[])],
    )
    backend = type("Backend", (), {"spec": service_spec})()
    node_item = _InvokeNodeItem(service_running=True, backend_node=backend, node_id="svcA")

    invoke_command(node_item, _FakeCommand("Run", "Run command", True, []))

    assert node_item._bridge_obj.calls == []
    assert node_item._bridge_obj.state_writes == [
        ("svcA", "svcA", command_input_state_field("Run"), {})
    ]

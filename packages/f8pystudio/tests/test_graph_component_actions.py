from __future__ import annotations

from dataclasses import dataclass

from qtpy import QtWidgets

from f8pysdk.specs import F8OperatorSchemaVersion, F8OperatorSpec
from f8pystudio.nodegraph.graph_backdrop_actions import GraphBackdropActionsMixin
from f8pystudio.nodegraph.graph_component_actions import GraphComponentActionsMixin
from f8pystudio.nodegraph.node_graph import F8StudioGraph
from f8pystudio.nodegraph.graph_variant_actions import GraphVariantActionsMixin
from f8pystudio.render_nodes.backdrop import BackdropRenderNode
from f8pystudio.studio_specs.identifiers import SERVICE_CLASS as STUDIO_SERVICE_CLASS

BACKDROP_OPERATOR_CLASS = "f8.backdrop"


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


@dataclass
class _FakeSelectedNode:
    id: str
    label: str

    def name(self) -> str:
        return self.label


class _FakeMenu:
    def __init__(self) -> None:
        self.commands: list[tuple[str, object, str]] = []

    def add_command(self, label: str, *, func: object, node_type: str) -> object:
        self.commands.append((str(label), func, str(node_type)))
        return object()


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
        self.parent = parent
        self.title = title
        self.name = name
        self.description = description
        self.tags = list(tags)

    def exec(self) -> int:
        return QtWidgets.QDialog.Accepted

    def values(self) -> tuple[str, str, list[str]]:
        return (self.name, self.description, list(self.tags))


class _FakeComponentHost(GraphComponentActionsMixin):
    def __init__(self, *, payload: dict[str, object], selected_nodes: list[_FakeSelectedNode]) -> None:
        self._payload = payload
        self._selected_nodes = list(selected_nodes)
        self._menu = _FakeMenu()
        self._component_menu_node_types = set()
        self._parent = QtWidgets.QWidget()

    def _notification_parent(self) -> QtWidgets.QWidget | None:
        return self._parent

    def context_nodes_menu(self) -> _FakeMenu | None:
        return self._menu

    def selected_nodes(self) -> list[object]:
        return list(self._selected_nodes)

    def serialize_publish_session(self) -> dict[str, object]:
        return dict(self._payload)


class _FakeVariantHost(GraphVariantActionsMixin):
    def __init__(self, *, selected_nodes: list[object]) -> None:
        self._selected_nodes = list(selected_nodes)
        self._parent = QtWidgets.QWidget()

    def _notification_parent(self) -> QtWidgets.QWidget | None:
        return self._parent

    def context_nodes_menu(self) -> None:
        return None

    def selected_nodes(self) -> list[object]:
        return list(self._selected_nodes)

    def create_node(self, node_type: str, *, pos: tuple[float, float] | None = None, selected: bool = True, push_undo: bool = True) -> None:
        _ = (node_type, pos, selected, push_undo)
        return None


class _FakeBackdropNode:
    def __init__(self) -> None:
        self.wrapped_nodes: list[object] = []
        self.wrap_calls: list[tuple[bool, bool]] = []

    def wrap_nodes(
        self,
        nodes: list[object],
        *,
        push_undo: bool = True,
        begin_undo_macro: bool = True,
    ) -> None:
        self.wrapped_nodes = list(nodes)
        self.wrap_calls.append((bool(push_undo), bool(begin_undo_macro)))


class _FakeBackdropHost(GraphBackdropActionsMixin):
    def __init__(self, *, selected_nodes: list[object], created_node: object | None = None) -> None:
        self._selected_nodes = list(selected_nodes)
        self._created_node = created_node
        self.create_node_calls: list[tuple[str, bool, bool, bool]] = []
        self.undo_calls: list[tuple[str, str]] = []
        self._menu = _FakeMenu()
        self._backdrop_create_menu_node_types = set()
        self._backdrop_wrap_menu_node_types = set()
        self._backdrop_registered_node_type: str | None = None
        self._parent = QtWidgets.QWidget()

    def _notification_parent(self) -> QtWidgets.QWidget | None:
        return self._parent

    def context_nodes_menu(self) -> _FakeMenu | None:
        return self._menu

    def selected_nodes(self) -> list[object]:
        return list(self._selected_nodes)

    def begin_undo(self, name: str) -> None:
        self.undo_calls.append(("begin", str(name)))

    def end_undo(self) -> None:
        self.undo_calls.append(("end", ""))

    def create_node(
        self,
        node_type: str,
        name: str | None = None,
        selected: bool = True,
        color: object | None = None,
        text_color: object | None = None,
        pos: object | None = None,
        push_undo: bool = True,
        begin_undo_macro: bool = True,
    ) -> object:
        _ = (name, color, text_color, pos)
        self.create_node_calls.append((str(node_type), bool(selected), bool(push_undo), bool(begin_undo_macro)))
        if self._created_node is not None:
            return self._created_node
        return _FakeBackdropNode()


def test_save_selected_nodes_as_component_trims_external_connections(monkeypatch) -> None:
    _ensure_app()
    saved_records: list[object] = []
    info_messages: list[tuple[str, str]] = []
    warning_messages: list[tuple[str, str]] = []
    payload = {
        "schemaVersion": "f8studio-session/1",
        "layout": {
            "nodes": {
                "node_a": {"id": "node_a", "name": "Node A"},
                "node_b": {"id": "node_b", "name": "Node B"},
                "node_c": {"id": "node_c", "name": "Node C"},
            },
            "connections": [
                {"out": ["node_a", "out"], "in": ["node_b", "in"]},
                {"out": ["node_b", "out"], "in": ["node_c", "in"]},
            ],
        },
    }
    host = _FakeComponentHost(
        payload=payload,
        selected_nodes=[_FakeSelectedNode("node_a", "Node A"), _FakeSelectedNode("node_b", "Node B")],
    )

    monkeypatch.setattr("f8pystudio.nodegraph.graph_component_actions.ProjectAssetMetaDialog", _FakeMetaDialog)
    monkeypatch.setattr("f8pystudio.nodegraph.graph_component_actions.upsert_component", lambda record: saved_records.append(record))
    monkeypatch.setattr(
        "f8pystudio.nodegraph.graph_component_actions.show_info",
        lambda _parent, title, message: info_messages.append((str(title), str(message))),
    )
    monkeypatch.setattr(
        "f8pystudio.nodegraph.graph_component_actions.show_warning",
        lambda _parent, title, message: warning_messages.append((str(title), str(message))),
    )

    host._save_selected_nodes_as_component()

    assert warning_messages == []
    assert len(saved_records) == 1
    saved_record = saved_records[0]
    assert saved_record.name == "Selection Component"
    assert set(saved_record.content["layout"]["nodes"].keys()) == {"node_a", "node_b"}
    assert saved_record.content["layout"]["connections"] == [{"out": ["node_a", "out"], "in": ["node_b", "in"]}]
    assert info_messages == [("Component saved", "Saved component:\nSelection Component")]


def test_install_component_context_menu_for_nodes_adds_save_command() -> None:
    _ensure_app()
    host = _FakeComponentHost(
        payload={"schemaVersion": "f8studio-session/1", "layout": {"nodes": {}, "connections": []}},
        selected_nodes=[_FakeSelectedNode("node_a", "Node A")],
    )

    class _FakeNodeClass:
        type_ = "svc.a.op"

    host.install_component_context_menu_for_nodes([_FakeNodeClass])

    assert host._menu.commands == [("Save As Component...", host._on_save_component_menu_action, "svc.a.op")]


def test_install_backdrop_context_menu_for_nodes_adds_create_command_for_regular_nodes() -> None:
    _ensure_app()
    host = _FakeBackdropHost(selected_nodes=[])

    class _FakeNodeClass:
        type_ = "svc.a.op"
        SPEC_TEMPLATE = object()

    host.install_backdrop_context_menu_for_nodes([_FakeNodeClass])

    assert host._menu.commands == []


def test_install_backdrop_context_menu_for_nodes_adds_create_and_wrap_commands_for_backdrop() -> None:
    _ensure_app()
    host = _FakeBackdropHost(selected_nodes=[])

    class _FakeBackdropNodeClass:
        type_ = "f8.pystudio.f8.backdrop"
        SPEC_TEMPLATE = F8OperatorSpec(
            schemaVersion=F8OperatorSchemaVersion.f8operator_1,
            serviceClass=STUDIO_SERVICE_CLASS,
            operatorClass=BACKDROP_OPERATOR_CLASS,
            label="Backdrop",
        )

    host.install_backdrop_context_menu_for_nodes([_FakeBackdropNodeClass])

    assert host._menu.commands == [
        ("Create Backdrop From Selection", host._on_create_backdrop_from_selection_action, "f8.pystudio.f8.backdrop"),
        ("Wrap Selected Nodes", host._on_wrap_selected_nodes_menu_action, "f8.pystudio.f8.backdrop"),
    ]
    assert host._backdrop_registered_node_type == "f8.pystudio.f8.backdrop"


def test_install_backdrop_context_menu_for_nodes_adds_create_command_when_backdrop_exists() -> None:
    _ensure_app()
    host = _FakeBackdropHost(selected_nodes=[])

    class _FakeNodeClass:
        type_ = "svc.a.op"
        SPEC_TEMPLATE = object()

    class _FakeBackdropNodeClass:
        type_ = "f8.pystudio.f8.backdrop"
        SPEC_TEMPLATE = F8OperatorSpec(
            schemaVersion=F8OperatorSchemaVersion.f8operator_1,
            serviceClass=STUDIO_SERVICE_CLASS,
            operatorClass=BACKDROP_OPERATOR_CLASS,
            label="Backdrop",
        )

    host.install_backdrop_context_menu_for_nodes([_FakeNodeClass, _FakeBackdropNodeClass])

    assert host._menu.commands == [
        ("Create Backdrop From Selection", host._on_create_backdrop_from_selection_action, "svc.a.op"),
        ("Create Backdrop From Selection", host._on_create_backdrop_from_selection_action, "f8.pystudio.f8.backdrop"),
        ("Wrap Selected Nodes", host._on_wrap_selected_nodes_menu_action, "f8.pystudio.f8.backdrop"),
    ]


def test_wrap_selected_nodes_menu_wraps_current_selection(monkeypatch) -> None:
    _ensure_app()
    selected_node = _FakeSelectedNode("node_a", "Node A")

    class _BackdropRenderNodeStub(_FakeBackdropNode):
        pass

    backdrop_stub = _BackdropRenderNodeStub()
    host = _FakeBackdropHost(selected_nodes=[backdrop_stub, selected_node])
    monkeypatch.setattr("f8pystudio.nodegraph.graph_backdrop_actions.BackdropRenderNode", _BackdropRenderNodeStub)

    host._on_wrap_selected_nodes_menu_action(graph=None, node=backdrop_stub)

    assert backdrop_stub.wrapped_nodes == [selected_node]


def test_create_backdrop_from_selection_wraps_selected_nodes() -> None:
    _ensure_app()
    selected_nodes = [_FakeSelectedNode("node_a", "Node A"), _FakeSelectedNode("node_b", "Node B")]
    created_backdrop = _FakeBackdropNode()
    host = _FakeBackdropHost(selected_nodes=selected_nodes, created_node=created_backdrop)
    host._backdrop_registered_node_type = "f8.pystudio.f8.backdrop"

    created = host._create_backdrop_from_selection()

    assert created is created_backdrop
    assert host.create_node_calls == [("f8.pystudio.f8.backdrop", True, True, False)]
    assert host.undo_calls == [("begin", "create backdrop from selection"), ("end", "")]
    assert created_backdrop.wrapped_nodes == selected_nodes
    assert created_backdrop.wrap_calls == [(True, False)]


def test_create_backdrop_from_selection_requires_selection(monkeypatch) -> None:
    _ensure_app()
    warnings: list[tuple[str, str]] = []
    host = _FakeBackdropHost(selected_nodes=[])

    monkeypatch.setattr(
        "f8pystudio.nodegraph.graph_backdrop_actions.show_warning",
        lambda _parent, title, message: warnings.append((str(title), str(message))),
    )

    created = host._create_backdrop_from_selection()

    assert created is None
    assert host.create_node_calls == []
    assert warnings == [("Create backdrop failed", "Select one or more nodes first.")]


def test_create_backdrop_from_selection_requires_registered_backdrop_type(monkeypatch) -> None:
    _ensure_app()
    warnings: list[tuple[str, str]] = []
    host = _FakeBackdropHost(selected_nodes=[_FakeSelectedNode("node_a", "Node A")])

    monkeypatch.setattr(
        "f8pystudio.nodegraph.graph_backdrop_actions.show_warning",
        lambda _parent, title, message: warnings.append((str(title), str(message))),
    )

    created = host._create_backdrop_from_selection()

    assert created is None
    assert host.create_node_calls == []
    assert warnings == [("Create backdrop failed", "Backdrop node type is not registered in this graph.")]


def test_create_backdrop_from_selection_adds_single_real_undo_command() -> None:
    _ensure_app()
    graph = F8StudioGraph()
    graph.node_factory.clear_registered_nodes()
    graph.node_factory.register_node(BackdropRenderNode)
    graph.install_backdrop_context_menu_for_nodes([BackdropRenderNode])

    node_type = str(BackdropRenderNode.type_ or "")
    existing_backdrop = graph.create_node(node_type, selected=True, push_undo=False)
    assert existing_backdrop is not None

    baseline_count = int(graph._undo_stack.count())
    baseline_index = int(graph._undo_stack.index())

    created = graph._create_backdrop_from_selection()

    assert created is not None
    assert int(graph._undo_stack.count()) == baseline_count + 1
    assert int(graph._undo_stack.index()) == baseline_index + 1
    assert str(graph._undo_stack.undoText() or "") == "create backdrop from selection"
    assert graph.get_node_by_id(str(created.id or "")) is created

    graph._undo_stack.undo()

    assert graph.get_node_by_id(str(created.id or "")) is None
    assert int(graph._undo_stack.index()) == baseline_index

    graph._undo_stack.redo()

    assert graph.get_node_by_id(str(created.id or "")) is not None
    assert int(graph._undo_stack.index()) == baseline_index + 1


def test_save_variant_menu_requires_single_selected_node(monkeypatch) -> None:
    _ensure_app()
    warnings: list[tuple[str, str]] = []
    saved_nodes: list[object] = []
    host = _FakeVariantHost(selected_nodes=[object(), object()])

    monkeypatch.setattr(
        "f8pystudio.nodegraph.graph_variant_actions.show_warning",
        lambda _parent, title, message: warnings.append((str(title), str(message))),
    )
    monkeypatch.setattr(
        GraphVariantActionsMixin,
        "_save_node_as_variant",
        lambda self, node: saved_nodes.append(node),
    )

    host._on_save_variant_menu_action(graph=None, node=None)

    assert saved_nodes == []
    assert warnings == [("Save variant failed", "Select exactly one node before saving a variant.")]

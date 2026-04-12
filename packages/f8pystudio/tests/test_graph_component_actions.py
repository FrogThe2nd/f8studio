from __future__ import annotations

from dataclasses import dataclass

from qtpy import QtWidgets

from f8pystudio.nodegraph.graph_component_actions import GraphComponentActionsMixin
from f8pystudio.nodegraph.graph_variant_actions import GraphVariantActionsMixin


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

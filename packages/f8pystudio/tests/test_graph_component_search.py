from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from f8pystudio.assets.components.component_models import F8ComponentEntry, F8ComponentRecord, F8ComponentSourceKind
from f8pystudio.nodegraph.graph_insert_flow import GraphInsertRequest
from f8pystudio.nodegraph.insert_layout_utils import GraphBounds
from f8pystudio.nodegraph.node_graph import F8StudioGraph


def _component_entry() -> F8ComponentEntry:
    record = F8ComponentRecord(
        componentId="component-1",
        name="Searchable Component",
        description="Reusable graph component",
        tags=["graph", "component"],
        content={
            "schemaVersion": "f8studio-session/1",
            "layout": {
                "nodes": {
                    "node-1": {
                        "id": "node-1",
                        "name": "Node 1",
                        "pos": [1, 2],
                    }
                },
                "connections": [],
            },
        },
    )
    return F8ComponentEntry(record=record, source=F8ComponentSourceKind.local)


def test_toggle_node_search_includes_components_and_selection_inserts_graph(monkeypatch) -> None:
    entry = _component_entry()
    captured_nodes: dict[str, list[str]] = {}
    inserted: dict[str, Any] = {}

    graph = F8StudioGraph.__new__(F8StudioGraph)
    graph._node_factory = SimpleNamespace(names={}, nodes={})
    graph._viewer = SimpleNamespace(
        tab_search_set_nodes=lambda nodes: captured_nodes.update(nodes),
        tab_search_toggle=lambda: None,
    )
    graph._tab_search_node_type_aliases = {}
    graph._tab_search_component_ids = {}
    graph.create_node = lambda node_type, pos: inserted.update({"created_node_type": node_type, "pos": pos})  # type: ignore[method-assign]
    graph.prepare_insert_graph_from_component = (  # type: ignore[method-assign]
        lambda component_payload, component_name: GraphInsertRequest(
            source_path=f"component:{component_name}",
            layout_data=component_payload["layout"],
            source_bbox=GraphBounds(1.0, 2.0, 1.0, 2.0),
            node_count=1,
            connection_count=0,
        )
    )
    graph.apply_insert_graph = (  # type: ignore[method-assign]
        lambda request, anchor_x, anchor_y: inserted.update(
            {
                "request": request,
                "anchor_x": anchor_x,
                "anchor_y": anchor_y,
            }
        )
    )
    graph._notification_parent = lambda: None  # type: ignore[method-assign]

    monkeypatch.setattr("f8pystudio.nodegraph.graph_search_actions.list_component_entries", lambda include_uninstalled=False: [entry])
    monkeypatch.setattr(
        "f8pystudio.nodegraph.graph_search_actions.component_entry",
        lambda component_id, include_uninstalled=False: entry if component_id == "component-1" else None,
    )

    graph.toggle_node_search()

    assert "Component | Searchable Component" in captured_nodes
    alias_id = captured_nodes["Component | Searchable Component"][0]

    graph._on_search_triggered(alias_id, (320.0, 180.0))

    assert "created_node_type" not in inserted
    assert inserted["anchor_x"] == 320.0
    assert inserted["anchor_y"] == 180.0
    assert inserted["request"].source_path == "component:Searchable Component"

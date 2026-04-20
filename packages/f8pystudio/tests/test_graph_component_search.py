from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from f8pysdk.specs import F8OperatorSchemaVersion, F8OperatorSpec, F8VariantRecord
from f8pystudio.assets.components.component_models import F8ComponentEntry, F8ComponentRecord, F8ComponentSourceKind
from f8pystudio.assets.variants.variant_models import F8VariantEntry, F8VariantKind, F8VariantSourceKind, variant_now_iso
from f8pystudio.assets.variants.variant_ids import build_variant_node_type
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


def _variant_record(*, variant_id: str, base_node_type: str, name: str) -> F8VariantRecord:
    now = variant_now_iso()
    return F8VariantRecord(
        variantId=variant_id,
        kind=F8VariantKind.operator,
        baseNodeType=base_node_type,
        serviceClass="svc.test",
        operatorClass="op.test",
        name=name,
        description="Reusable variant",
        tags=["variant"],
        spec={"label": name},
        createdAt=now,
        updatedAt=now,
    )


def _variant_entry(*, variant_id: str, base_node_type: str, name: str, owner_display_name: str) -> F8VariantEntry:
    return F8VariantEntry(
        record=_variant_record(
            variant_id=variant_id,
            base_node_type=base_node_type,
            name=name,
        ),
        source=F8VariantSourceKind.remote_public,
        ownerDisplayName=owner_display_name,
        installed=True,
        hasCachedContent=True,
    )


def test_toggle_node_search_includes_components_and_selection_inserts_graph(monkeypatch) -> None:
    entry = _component_entry()
    captured_nodes: dict[str, list[str]] = {}
    inserted: dict[str, Any] = {}

    graph = F8StudioGraph.__new__(F8StudioGraph)
    graph._node_factory = SimpleNamespace(names={}, nodes={})
    graph._viewer = SimpleNamespace(
        tab_search_rebuild_nodes=lambda nodes: (captured_nodes.clear(), captured_nodes.update(nodes)),
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

    assert "Component | Searchable Component (Draft)" in captured_nodes
    alias_id = captured_nodes["Component | Searchable Component (Draft)"][0]

    graph._on_search_triggered(alias_id, (320.0, 180.0))

    assert "created_node_type" not in inserted
    assert inserted["anchor_x"] == 320.0
    assert inserted["anchor_y"] == 180.0
    assert inserted["request"].source_path == "component:Searchable Component"


def test_toggle_node_search_batches_variant_catalog_lookup(monkeypatch) -> None:
    captured_nodes: dict[str, list[str]] = {}

    graph = F8StudioGraph.__new__(F8StudioGraph)
    graph._node_factory = SimpleNamespace(
        names={"Base Node": ["svc.test.base"]},
        nodes={},
    )
    graph._viewer = SimpleNamespace(
        tab_search_rebuild_nodes=lambda nodes: (captured_nodes.clear(), captured_nodes.update(nodes)),
        tab_search_toggle=lambda: None,
    )
    graph._tab_search_node_type_aliases = {}
    graph._tab_search_component_ids = {}

    grouped_calls: list[bool] = []

    monkeypatch.setattr(
        "f8pystudio.nodegraph.graph_search_actions.list_variant_entries_grouped_by_base",
        lambda include_uninstalled=False: grouped_calls.append(bool(include_uninstalled))
        or {
            "svc.test.base": [
                _variant_entry(
                    variant_id="variant-1",
                    base_node_type="svc.test.base",
                    name="Fast Search Variant",
                    owner_display_name="Author Name",
                )
            ]
        },
    )
    monkeypatch.setattr("f8pystudio.nodegraph.graph_search_actions.list_component_entries", lambda include_uninstalled=False: [])

    graph.toggle_node_search()

    assert grouped_calls == [False]
    assert "Base Node | Fast Search Variant (by Author Name)" in captured_nodes
    alias_id = captured_nodes["Base Node | Fast Search Variant (by Author Name)"][0]
    assert graph._tab_search_node_type_aliases[alias_id] == build_variant_node_type("variant-1")


def test_toggle_node_search_uses_readable_category_aliases_for_pyengine_nodes(monkeypatch) -> None:
    class _FakePyEngineTickNode:
        NODE_NAME = "Tick"
        SPEC_TEMPLATE = F8OperatorSpec(
            schemaVersion=F8OperatorSchemaVersion.f8operator_1,
            serviceClass="f8.pyengine",
            operatorClass="f8.tick",
            version="1.0.0",
            label="Tick",
            paletteCategory="f8.pyengine.execution",
        )

    captured_nodes: dict[str, list[str]] = {}

    graph = F8StudioGraph.__new__(F8StudioGraph)
    graph._node_factory = SimpleNamespace(
        names={"Tick": ["f8.pyengine.f8.tick"]},
        nodes={"f8.pyengine.f8.tick": _FakePyEngineTickNode},
    )
    graph._viewer = SimpleNamespace(
        tab_search_rebuild_nodes=lambda nodes: (captured_nodes.clear(), captured_nodes.update(nodes)),
        tab_search_toggle=lambda: None,
    )
    graph._tab_search_node_type_aliases = {}
    graph._tab_search_component_ids = {}

    monkeypatch.setattr("f8pystudio.nodegraph.graph_search_actions.list_variant_entries_grouped_by_base", lambda include_uninstalled=False: {})
    monkeypatch.setattr("f8pystudio.nodegraph.graph_search_actions.list_component_entries", lambda include_uninstalled=False: [])

    graph.toggle_node_search()

    alias_id = captured_nodes["Tick"][0]
    assert alias_id.startswith("PyEngine / Execution.")
    assert not alias_id.startswith("f8.pyengine.execution.")


def test_refresh_tab_search_if_visible_rebuilds_component_and_variant_entries(monkeypatch) -> None:
    captured_nodes: dict[str, list[str]] = {}

    graph = F8StudioGraph.__new__(F8StudioGraph)
    graph._node_factory = SimpleNamespace(
        names={"Base Node": ["svc.test.base"]},
        nodes={},
    )
    graph._viewer = SimpleNamespace(
        is_tab_search_visible=lambda: True,
        tab_search_rebuild_nodes=lambda nodes: (captured_nodes.clear(), captured_nodes.update(nodes)),
        tab_search_toggle=lambda: None,
    )
    graph._tab_search_node_type_aliases = {}
    graph._tab_search_component_ids = {}

    monkeypatch.setattr(
        "f8pystudio.nodegraph.graph_search_actions.list_variant_entries_grouped_by_base",
        lambda include_uninstalled=False: {
            "svc.test.base": [
                _variant_entry(
                    variant_id="variant-refresh",
                    base_node_type="svc.test.base",
                    name="Refresh Variant",
                    owner_display_name="Refresh Author",
                )
            ]
        },
    )
    monkeypatch.setattr(
        "f8pystudio.nodegraph.graph_search_actions.list_component_entries",
        lambda include_uninstalled=False: [_component_entry()],
    )

    graph.refresh_tab_search_if_visible()

    assert "Base Node | Refresh Variant (by Refresh Author)" in captured_nodes
    assert "Component | Searchable Component (Draft)" in captured_nodes


def test_refresh_tab_search_if_visible_replaces_removed_installed_entries(monkeypatch) -> None:
    captured_nodes: dict[str, list[str]] = {}
    include_component = {"value": True}

    graph = F8StudioGraph.__new__(F8StudioGraph)
    graph._node_factory = SimpleNamespace(
        names={"Base Node": ["svc.test.base"]},
        nodes={},
    )
    graph._viewer = SimpleNamespace(
        is_tab_search_visible=lambda: True,
        tab_search_rebuild_nodes=lambda nodes: (captured_nodes.clear(), captured_nodes.update(nodes)),
        tab_search_toggle=lambda: None,
    )
    graph._tab_search_node_type_aliases = {}
    graph._tab_search_component_ids = {}

    monkeypatch.setattr("f8pystudio.nodegraph.graph_search_actions.list_variant_entries_grouped_by_base", lambda include_uninstalled=False: {})
    monkeypatch.setattr(
        "f8pystudio.nodegraph.graph_search_actions.list_component_entries",
        lambda include_uninstalled=False: [_component_entry()] if include_component["value"] else [],
    )

    graph.refresh_tab_search_if_visible()
    assert "Component | Searchable Component (Draft)" in captured_nodes

    include_component["value"] = False
    graph.refresh_tab_search_if_visible()

    assert "Component | Searchable Component (Draft)" not in captured_nodes


def test_on_asset_cache_changed_rebuilds_asset_search_sources() -> None:
    graph = F8StudioGraph.__new__(F8StudioGraph)
    calls: list[str] = []
    graph.rebuild_asset_search_sources = lambda: calls.append("rebuild")  # type: ignore[method-assign]

    graph._on_asset_cache_changed()

    assert calls == ["rebuild"]

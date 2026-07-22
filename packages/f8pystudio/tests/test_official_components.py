from __future__ import annotations

from pathlib import Path

from qtpy import QtWidgets

from f8pysdk.codec import dump_json
from f8pysdk.registry import Registry
from f8pysdk.service_runtime_tools.inventory.catalog import ServiceCatalog
from f8pysdk.specs import F8DataPortSpec, number_schema

from f8pyengine.constants import SERVICE_CLASS
from f8pyengine.operators.buttplug_out import ButtplugOutRuntimeNode
from f8pyengine.operators.handy_out import HandyOutRuntimeNode
from f8pyengine.operators.lovense_out import LovenseOutRuntimeNode
from f8pyengine.operators.serial_out import SerialOutRuntimeNode
from f8pyengine.operators.tcode import TCodeRuntimeNode
from f8pyengine.pyengine_node_registry import create_pyengine_registry
from f8pystudio.agents.graph_builder import match_graph_library_candidates
from f8pystudio.app.program import PyStudioProgram
from f8pystudio.assets.components.component_compatibility import SemanticSignal
from f8pystudio.assets.components.component_catalog import ComponentCatalogService
from f8pystudio.assets.components.component_models import F8ComponentSourceKind
from f8pystudio.assets.components.official_components import (
    BUNDLED_OFFICIAL_COMPONENT_TAG,
    bundled_official_component_entries,
    component_entry_is_bundled_official,
)
from f8pystudio.assets.ui.component_catalog_selection import ComponentCatalogSelectionMixin
from f8pystudio.nodegraph.node_graph import F8StudioGraph
from f8pystudio.nodegraph.session_schema import extract_layout


_EXPECTED_COMPONENT_NAMES = {
    "Position to Buttplug",
    "Position to Handy",
    "Position to Lovense",
    "Position to TCode",
    "TCode to Serial",
}
_PHYSICAL_OUTPUT_CLASSES = {
    "f8.buttplug_out",
    "f8.handy_out",
    "f8.lovense_out",
    "f8.serial_out",
}
_CURRENT_SPECS = {
    "f8.buttplug_out": ButtplugOutRuntimeNode.SPEC,
    "f8.handy_out": HandyOutRuntimeNode.SPEC,
    "f8.lovense_out": LovenseOutRuntimeNode.SPEC,
    "f8.serial_out": SerialOutRuntimeNode.SPEC,
    "f8.tcode": TCodeRuntimeNode.SPEC,
}


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if isinstance(app, QtWidgets.QApplication):
        return app
    return QtWidgets.QApplication([])


def _pyengine_graph() -> F8StudioGraph:
    _ensure_app()
    catalog = ServiceCatalog.instance()
    catalog.clear()
    registry = Registry.wrap(create_pyengine_registry())
    service_spec = registry.service_spec(SERVICE_CLASS)
    assert service_spec is not None
    catalog.register_service(service_spec)
    for operator_spec in registry.operator_specs(SERVICE_CLASS):
        catalog.register_operator(operator_spec)
    graph = F8StudioGraph(asset_cache_auto_refresh=False)
    graph.node_factory.clear_registered_nodes()
    for node_class in PyStudioProgram.build_node_classes():
        graph.node_factory.register_node(node_class)
    return graph


def test_bundled_official_components_have_stable_metadata_and_safe_outputs() -> None:
    entries = bundled_official_component_entries()

    assert {entry.record.name for entry in entries} == _EXPECTED_COMPONENT_NAMES
    assert len({entry.record.componentId for entry in entries}) == len(entries)
    for entry in entries:
        assert entry.source == F8ComponentSourceKind.remote_official
        assert entry.installed is True
        assert entry.hasCachedContent is True
        assert entry.remoteVersionNumber == 1
        assert BUNDLED_OFFICIAL_COMPONENT_TAG in entry.record.tags
        assert component_entry_is_bundled_official(entry) is True
        layout = extract_layout(entry.record.content)
        nodes = layout["nodes"]
        assert isinstance(nodes, dict)
        assert len(nodes) == 1
        node_payload = next(iter(nodes.values()))
        assert isinstance(node_payload, dict)
        spec_payload = node_payload["f8_spec"]
        assert isinstance(spec_payload, dict)
        operator_class = str(spec_payload["operatorClass"])
        assert spec_payload == dump_json(_CURRENT_SPECS[operator_class], mode="json")
        if operator_class in _PHYSICAL_OUTPUT_CLASSES:
            custom = node_payload["custom"]
            assert isinstance(custom, dict)
            assert custom["enabled"] is False


def test_component_catalog_merges_bundled_official_entries_without_database_seeding(tmp_path: Path) -> None:
    db_path = tmp_path / "components.sqlite3"
    service = ComponentCatalogService(db_path=db_path)

    remote_entries = service.load_remote_entries()
    installed_entries = service.list_entries()

    assert {entry.record.name for entry in remote_entries} >= _EXPECTED_COMPONENT_NAMES
    assert {entry.record.name for entry in installed_entries} >= _EXPECTED_COMPONENT_NAMES
    assert all(component_entry_is_bundled_official(entry) for entry in remote_entries)
    assert service.load_persisted_remote_entries() == []

    service.replace_remote_entries(remote_entries)

    assert service.load_persisted_remote_entries() == []


def test_bundled_official_components_cannot_be_loaded_or_offloaded() -> None:
    entry = bundled_official_component_entries()[0]

    can_load, can_offload = ComponentCatalogSelectionMixin._load_action_availability(
        local_entry=None,
        remote_entry=entry,
    )

    assert can_load is False
    assert can_offload is False


def test_agent_matcher_returns_compatible_bundled_lovense_component() -> None:
    result = match_graph_library_candidates(
        goal="lovense position output",
        node_catalog={"nodes": []},
        component_entries=bundled_official_component_entries(),
        source_port=F8DataPortSpec(
            name="position",
            valueSchema=number_schema(minimum=0.0, maximum=1.0),
        ),
        signal=SemanticSignal.POSITION,
    )

    candidate = next(
        item for item in result.components if item.component_id == "f8.official.component.position-to-lovense"
    )
    assert candidate.compatibility.evaluated is True
    assert candidate.compatibility.compatible is True


def test_every_bundled_component_deserializes_to_registered_nodes() -> None:
    graph = _pyengine_graph()
    try:
        for entry in bundled_official_component_entries():
            request = graph.prepare_insert_graph_from_component(
                entry.record.content,
                component_name=entry.record.name,
            )
            result = graph.apply_insert_graph(request, anchor_x=0.0, anchor_y=0.0)
            assert len(result.inserted_node_ids) == 1
            node = graph.get_node_by_id(result.inserted_node_ids[0])
            assert node is not None
            operator_class = str(node.spec.operatorClass)
            assert operator_class in _CURRENT_SPECS
            if operator_class in _PHYSICAL_OUTPUT_CLASSES:
                assert node.get_property("enabled") is False
            graph.delete_node(node, push_undo=False)
    finally:
        graph.deleteLater()

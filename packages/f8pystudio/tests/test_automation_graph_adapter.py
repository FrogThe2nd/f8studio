from __future__ import annotations

from qtpy import QtWidgets

from f8pysdk.registry import Registry
from f8pysdk.service_runtime_tools.inventory.catalog import ServiceCatalog
from f8pyengine.constants import SERVICE_CLASS as PYENGINE_SERVICE_CLASS
from f8pyengine.pyengine_node_registry import create_pyengine_registry
from f8pystudio.app.program import PyStudioProgram
from f8pystudio.automation.domain import decode_graph_patch
from f8pystudio.automation.graph_adapter import StudioGraphAutomationAdapter
from f8pystudio.nodegraph.node_graph import F8StudioGraph
from f8pystudio.studio_specs.identifiers import SERVICE_CLASS as STUDIO_SERVICE_CLASS
from f8pystudio.studio_specs.registry import SERVICE_CLASS, create_pystudio_registry


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if isinstance(app, QtWidgets.QApplication):
        return app
    return QtWidgets.QApplication([])


def _graph_with_pystudio_nodes() -> F8StudioGraph:
    _ensure_app()
    catalog = ServiceCatalog.instance()
    catalog.clear()
    registry = Registry.wrap(create_pystudio_registry())
    service_spec = registry.service_spec(SERVICE_CLASS)
    assert service_spec is not None
    catalog.register_service(service_spec)
    for operator_spec in registry.operator_specs(SERVICE_CLASS):
        catalog.register_operator(operator_spec)
    graph = F8StudioGraph(asset_cache_auto_refresh=False)
    graph.node_factory.clear_registered_nodes()
    for node_cls in PyStudioProgram.build_node_classes():
        graph.node_factory.register_node(node_cls)
    return graph


def _graph_with_pyengine_nodes() -> F8StudioGraph:
    _ensure_app()
    catalog = ServiceCatalog.instance()
    catalog.clear()
    registry = Registry.wrap(create_pyengine_registry())
    service_spec = registry.service_spec(PYENGINE_SERVICE_CLASS)
    assert service_spec is not None
    catalog.register_service(service_spec)
    for operator_spec in registry.operator_specs(PYENGINE_SERVICE_CLASS):
        catalog.register_operator(operator_spec)
    graph = F8StudioGraph(asset_cache_auto_refresh=False)
    graph.node_factory.clear_registered_nodes()
    for node_cls in PyStudioProgram.build_node_classes():
        graph.node_factory.register_node(node_cls)
    return graph


def test_graph_patch_preview_restores_session() -> None:
    graph = _graph_with_pystudio_nodes()
    adapter = StudioGraphAutomationAdapter(graph)
    before = graph.serialize_session()
    catalog = adapter.node_catalog()
    node_type = next(item["nodeType"] for item in catalog["nodes"] if item["kind"] == "operator")
    patch = decode_graph_patch(
        {
            "expectedRevision": adapter.revision(),
            "ops": [{"op": "createNode", "nodeType": node_type, "nodeId": "preview_node", "pos": [12, 34]}],
        }
    )

    preview = adapter.preview_patch(patch)

    assert preview.valid is True
    assert graph.serialize_session() == before


def test_graph_patch_rejects_stale_revision() -> None:
    graph = _graph_with_pystudio_nodes()
    adapter = StudioGraphAutomationAdapter(graph)
    patch = decode_graph_patch({"expectedRevision": adapter.revision() + 1, "ops": []})

    preview = adapter.apply_patch(patch)

    assert preview.valid is False
    assert "stale graph revision" in preview.errors[0]


def test_graph_adapter_finds_node_detail_connections_and_diagnostics() -> None:
    graph = _graph_with_pystudio_nodes()
    adapter = StudioGraphAutomationAdapter(graph)
    catalog = adapter.node_catalog()
    service_node_type = next(item["nodeType"] for item in catalog["nodes"] if item["kind"] == "service")
    operator_node_type = next(
        item["nodeType"]
        for item in catalog["nodes"]
        if item["kind"] == "operator" and item["serviceClass"] == STUDIO_SERVICE_CLASS
    )
    patch = decode_graph_patch(
        {
            "expectedRevision": adapter.revision(),
            "ops": [
                {
                    "op": "createNode",
                    "nodeType": service_node_type,
                    "nodeId": "studio_service_for_query",
                    "name": "Studio Service",
                    "pos": [0, 0],
                },
                {
                    "op": "createNode",
                    "nodeType": operator_node_type,
                    "nodeId": "studio_operator_for_query",
                    "name": "Studio Operator",
                    "pos": [220, 40],
                },
            ],
        }
    )

    preview = adapter.apply_patch(patch)
    found = adapter.find_nodes(query="Studio Operator")
    detail = adapter.node_detail("studio_operator_for_query")
    connections = adapter.connections(node_id="studio_operator_for_query")
    diagnostics = adapter.diagnostics()

    assert preview.valid is True
    assert found["nodes"][0]["node_id"] == "studio_operator_for_query"
    assert detail["node"]["node_id"] == "studio_operator_for_query"
    assert detail["runtimeBinding"]["serviceId"] == "studio"
    assert connections["connections"] == []
    assert diagnostics["ok"] is True


def test_graph_adapter_diagnostics_reports_orphan_service_operator() -> None:
    graph = _graph_with_pyengine_nodes()
    adapter = StudioGraphAutomationAdapter(graph)
    catalog = adapter.node_catalog()
    service_node_type = next(item["nodeType"] for item in catalog["nodes"] if item["kind"] == "service")
    operator_node_type = next(
        item["nodeType"]
        for item in catalog["nodes"]
        if item["kind"] == "operator" and item["serviceClass"] == PYENGINE_SERVICE_CLASS
    )
    patch = decode_graph_patch(
        {
            "expectedRevision": adapter.revision(),
            "ops": [
                {
                    "op": "createNode",
                    "nodeType": service_node_type,
                    "nodeId": "service_container",
                    "name": "Container",
                    "pos": [0, 0],
                },
                {
                    "op": "createNode",
                    "nodeType": operator_node_type,
                    "nodeId": "orphan_operator",
                    "name": "Orphan",
                    "pos": [40, 40],
                },
            ],
        }
    )

    before = graph._loading_session
    graph._loading_session = True
    try:
        preview = adapter.apply_patch(patch)
    finally:
        graph._loading_session = before

    diagnostics = adapter.diagnostics()

    assert preview.valid is True
    issue_codes = {str(issue["code"]) for issue in diagnostics["issues"]}
    assert "operator_missing_service_container" in issue_codes

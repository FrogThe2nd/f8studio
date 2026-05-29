from __future__ import annotations

from qtpy import QtWidgets

from f8pysdk.registry import Registry
from f8pysdk.service_runtime_tools.inventory.catalog import ServiceCatalog
from f8pystudio.app.program import PyStudioProgram
from f8pystudio.automation.domain import decode_graph_patch
from f8pystudio.automation.graph_adapter import StudioGraphAutomationAdapter
from f8pystudio.nodegraph.node_graph import F8StudioGraph
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

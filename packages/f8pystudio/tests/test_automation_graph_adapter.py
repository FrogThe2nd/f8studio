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


def test_graph_patch_preview_preserves_revision_for_unbound_operator_inside_container() -> None:
    graph = _graph_with_pyengine_nodes()
    adapter = StudioGraphAutomationAdapter(graph)
    catalog = adapter.node_catalog()
    service_node_type = next(
        item["nodeType"]
        for item in catalog["nodes"]
        if item["kind"] == "service" and item["serviceClass"] == PYENGINE_SERVICE_CLASS
    )
    operator_node_type = next(
        item["nodeType"]
        for item in catalog["nodes"]
        if item["kind"] == "operator" and item["operatorClass"] == "f8.python_script"
    )
    create_patch = decode_graph_patch(
        {
            "expectedRevision": adapter.revision(),
            "ops": [
                {
                    "op": "createNode",
                    "nodeType": service_node_type,
                    "nodeId": "pyengine_service",
                    "name": "PyEngine Service",
                    "pos": [0, 0],
                },
                {
                    "op": "createNode",
                    "nodeType": operator_node_type,
                    "nodeId": "vam_script",
                    "name": "VAM Script",
                    "pos": [80, 80],
                },
            ],
        }
    )
    create_preview = adapter.apply_patch(create_patch)
    assert create_preview.valid is True
    before_revision = adapter.revision()
    before_detail = adapter.node_detail("vam_script")
    assert before_detail["runtimeBinding"]["serviceId"] == ""

    ports_patch = decode_graph_patch(
        {
            "expectedRevision": before_revision,
            "ops": [
                {
                    "op": "setNodePorts",
                    "nodeId": "vam_script",
                    "dataInPorts": [{"name": "skeletons", "valueSchema": {"type": "any"}, "required": False}],
                }
            ],
        }
    )

    preview = adapter.preview_patch(ports_patch)
    after_detail = adapter.node_detail("vam_script")

    assert preview.valid is True
    assert adapter.revision() == before_revision
    assert after_detail["runtimeBinding"]["serviceId"] == ""


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


def test_graph_patch_set_svc_id_updates_runtime_binding() -> None:
    graph = _graph_with_pyengine_nodes()
    adapter = StudioGraphAutomationAdapter(graph)
    catalog = adapter.node_catalog()
    service_node_type = next(
        item["nodeType"]
        for item in catalog["nodes"]
        if item["kind"] == "service" and item["serviceClass"] == PYENGINE_SERVICE_CLASS
    )
    operator_node_type = next(
        item["nodeType"]
        for item in catalog["nodes"]
        if item["kind"] == "operator" and item["operatorClass"] == "f8.python_script"
    )
    create_patch = decode_graph_patch(
        {
            "expectedRevision": adapter.revision(),
            "ops": [
                {
                    "op": "createNode",
                    "nodeType": service_node_type,
                    "nodeId": "pyengine_service",
                    "name": "PyEngine Service",
                    "pos": [0, 0],
                },
                {
                    "op": "createNode",
                    "nodeType": operator_node_type,
                    "nodeId": "vam_script",
                    "name": "VAM Script",
                    "pos": [80, 80],
                },
            ],
        }
    )
    create_preview = adapter.apply_patch(create_patch)
    assert create_preview.valid is True
    before_detail = adapter.node_detail("vam_script")
    assert before_detail["runtimeBinding"]["serviceId"] == ""

    bind_patch = decode_graph_patch(
        {
            "expectedRevision": adapter.revision(),
            "ops": [
                {
                    "op": "setNodeState",
                    "nodeId": "vam_script",
                    "field": "svcId",
                    "value": "pyengine_service",
                }
            ],
        }
    )

    bind_preview = adapter.apply_patch(bind_patch)
    detail = adapter.node_detail("vam_script")
    diagnostics = adapter.diagnostics()
    issue_codes = {str(issue["code"]) for issue in diagnostics["issues"]}

    assert bind_preview.valid is True
    assert detail["stateValues"]["svcId"] == "pyengine_service"
    assert detail["runtimeBinding"]["serviceId"] == "pyengine_service"
    assert "operator_missing_service_container" not in issue_codes
    assert "operator_service_container_missing" not in issue_codes


def test_graph_patch_set_svc_id_expands_service_container_boundary() -> None:
    graph = _graph_with_pyengine_nodes()
    adapter = StudioGraphAutomationAdapter(graph)
    catalog = adapter.node_catalog()
    service_node_type = next(
        item["nodeType"]
        for item in catalog["nodes"]
        if item["kind"] == "service" and item["serviceClass"] == PYENGINE_SERVICE_CLASS
    )
    operator_node_type = next(
        item["nodeType"]
        for item in catalog["nodes"]
        if item["kind"] == "operator" and item["operatorClass"] == "f8.python_script"
    )
    create_patch = decode_graph_patch(
        {
            "expectedRevision": adapter.revision(),
            "ops": [
                {
                    "op": "createNode",
                    "nodeType": service_node_type,
                    "nodeId": "pyengine_service",
                    "name": "PyEngine Service",
                    "pos": [0, 0],
                },
                {
                    "op": "createNode",
                    "nodeType": operator_node_type,
                    "nodeId": "vam_script",
                    "name": "VAM Script",
                    "pos": [900, 700],
                },
            ],
        }
    )
    create_preview = adapter.apply_patch(create_patch)
    assert create_preview.valid is True
    before_bind_session = graph.serialize_session()

    bind_patch = decode_graph_patch(
        {
            "expectedRevision": adapter.revision(),
            "ops": [
                {
                    "op": "setNodeState",
                    "nodeId": "vam_script",
                    "field": "svcId",
                    "value": "pyengine_service",
                }
            ],
        }
    )

    preview = adapter.preview_patch(bind_patch)
    assert preview.valid is True
    assert set(preview.changed_node_ids) == {"pyengine_service", "vam_script"}
    assert graph.serialize_session() == before_bind_session

    applied = adapter.apply_patch(bind_patch)
    session_payload = graph.serialize_session()
    nodes = session_payload["layout"]["nodes"]
    service_layout = nodes["pyengine_service"]
    operator_layout = nodes["vam_script"]
    service_pos = service_layout["pos"]
    operator_pos = operator_layout["pos"]
    service_right = float(service_pos[0]) + float(service_layout["width"])
    service_bottom = float(service_pos[1]) + float(service_layout["height"])
    operator_right = float(operator_pos[0]) + float(operator_layout["width"])
    operator_bottom = float(operator_pos[1]) + float(operator_layout["height"])

    assert applied.valid is True
    assert set(applied.changed_node_ids) == {"pyengine_service", "vam_script"}
    assert service_right >= operator_right
    assert service_bottom >= operator_bottom

    graph._undo_stack.undo()
    assert graph.serialize_session() == before_bind_session


def test_graph_patch_can_set_editable_operator_ports() -> None:
    graph = _graph_with_pyengine_nodes()
    adapter = StudioGraphAutomationAdapter(graph)
    catalog = adapter.node_catalog()
    service_node_type = next(
        item["nodeType"]
        for item in catalog["nodes"]
        if item["kind"] == "service" and item["serviceClass"] == PYENGINE_SERVICE_CLASS
    )
    operator_node_type = next(
        item["nodeType"]
        for item in catalog["nodes"]
        if item["kind"] == "operator" and item["operatorClass"] == "f8.python_script"
    )
    create_patch = decode_graph_patch(
        {
            "expectedRevision": adapter.revision(),
            "ops": [
                {
                    "op": "createNode",
                    "nodeType": service_node_type,
                    "nodeId": "pyengine_service",
                    "name": "PyEngine Service",
                    "pos": [0, 0],
                },
                {
                    "op": "createNode",
                    "nodeType": operator_node_type,
                    "nodeId": "vam_script",
                    "name": "VAM Script",
                    "pos": [80, 80],
                }
            ],
        }
    )
    create_preview = adapter.apply_patch(create_patch)
    assert create_preview.valid is True

    ports_patch = decode_graph_patch(
        {
            "expectedRevision": adapter.revision(),
            "ops": [
                {
                    "op": "setNodePorts",
                    "nodeId": "vam_script",
                    "dataInPorts": [
                        {
                            "name": "skeletons",
                            "valueSchema": {"type": "any"},
                            "required": False,
                            "description": "Decoded skeleton cache.",
                        }
                    ],
                    "dataOutPorts": [
                        {
                            "name": "status",
                            "valueSchema": {"type": "any"},
                            "required": False,
                            "description": "Resolver status.",
                        },
                        {
                            "name": "axes",
                            "valueSchema": {"type": "any"},
                            "required": False,
                            "description": "Raw VAM axes.",
                        },
                    ],
                }
            ],
        }
    )

    preview = adapter.preview_patch(ports_patch)
    assert preview.valid is True
    applied = adapter.apply_patch(ports_patch)
    detail = adapter.node_detail("vam_script")
    snapshot = adapter.snapshot()

    assert applied.valid is True
    assert [port["name"] for port in detail["spec"]["dataInPorts"]] == ["skeletons"]
    assert [port["name"] for port in detail["spec"]["dataOutPorts"]] == ["status", "axes"]
    node = next(node for node in snapshot.nodes if node.node_id == "vam_script")
    assert [port.name for port in node.inputs if port.kind == "data"] == ["[D]skeletons"]
    assert {port.name for port in node.outputs if port.kind == "data"} == {"status[D]", "axes[D]"}


def test_graph_patch_can_set_editable_operator_state_fields() -> None:
    graph = _graph_with_pyengine_nodes()
    adapter = StudioGraphAutomationAdapter(graph)
    catalog = adapter.node_catalog()
    service_node_type = next(
        item["nodeType"]
        for item in catalog["nodes"]
        if item["kind"] == "service" and item["serviceClass"] == PYENGINE_SERVICE_CLASS
    )
    operator_node_type = next(
        item["nodeType"]
        for item in catalog["nodes"]
        if item["kind"] == "operator" and item["operatorClass"] == "f8.python_script"
    )
    create_patch = decode_graph_patch(
        {
            "expectedRevision": adapter.revision(),
            "ops": [
                {
                    "op": "createNode",
                    "nodeType": service_node_type,
                    "nodeId": "pyengine_service",
                    "name": "PyEngine Service",
                    "pos": [0, 0],
                },
                {
                    "op": "createNode",
                    "nodeType": operator_node_type,
                    "nodeId": "vam_script",
                    "name": "VAM Script",
                    "pos": [80, 80],
                },
            ],
        }
    )
    create_preview = adapter.apply_patch(create_patch)
    assert create_preview.valid is True

    state_fields_patch = decode_graph_patch(
        {
            "expectedRevision": adapter.revision(),
            "ops": [
                {
                    "op": "setNodeStateFields",
                    "nodeId": "vam_script",
                    "stateFields": [
                        {
                            "name": "trackingMode",
                            "label": "Tracking Mode",
                            "description": "auto or manual target selection.",
                            "valueSchema": {"type": "string", "default": "auto", "enum": ["auto", "manual"]},
                            "access": "rw",
                            "required": False,
                            "showOnNode": True,
                        },
                        {
                            "name": "availableReferenceKeys",
                            "description": "Diagnostic reference keys.",
                            "valueSchema": {"type": "array", "items": {"type": "string"}, "default": []},
                            "access": "ro",
                            "required": False,
                            "showOnNode": False,
                        },
                    ],
                }
            ],
        }
    )

    preview = adapter.preview_patch(state_fields_patch)
    assert preview.valid is True
    applied = adapter.apply_patch(state_fields_patch)
    detail = adapter.node_detail("vam_script")
    snapshot = adapter.snapshot()

    assert applied.valid is True
    assert [field["name"] for field in detail["spec"]["stateFields"]] == [
        "code",
        "inputMode",
        "trackingMode",
        "availableReferenceKeys",
    ]
    node = next(node for node in snapshot.nodes if node.node_id == "vam_script")
    assert [field.name for field in node.state_fields] == [
        "code",
        "inputMode",
        "trackingMode",
        "availableReferenceKeys",
    ]

from __future__ import annotations

from qtpy import QtWidgets

from f8pysdk import F8OperatorSpec, F8SpecEditPolicy, editable_collection_edit_policy
from f8pystudio.studio_specs.identifiers import SERVICE_CLASS as STUDIO_SERVICE_CLASS
from f8pystudio.nodegraph.operator_basenode import F8StudioOperatorBaseNode
from f8pystudio.nodegraph.service_basenode import F8StudioServiceBaseNode
from f8pystudio.operators.data_expr import OPERATOR_CLASS as DATA_EXPR_OPERATOR_CLASS
from f8pystudio.operators.note import OPERATOR_CLASS as NOTE_OPERATOR_CLASS
from f8pystudio.operators.patch_hub import OPERATOR_CLASS as PATCH_HUB_OPERATOR_CLASS
from f8pystudio.operators.state_expr import OPERATOR_CLASS as STATE_EXPR_OPERATOR_CLASS
from f8pystudio.operators.value_stepper import OPERATOR_CLASS as VALUE_STEPPER_OPERATOR_CLASS
from f8pystudio.studio_specs.registry import register_pystudio_specs
from f8pystudio.render_nodes.note import NoteRenderNode
from f8pystudio.render_nodes.patch_hub import PatchHubRenderNode
from f8pystudio.render_nodes.registry import RenderNodeRegistry
from f8pystudio.ui.widgets.node_property_panel.editor import _should_show_commands_tab
from f8pystudio.ui.widgets.node_property_panel.ports import _F8SpecPortEditor
from f8pysdk.service_runtime_tools.catalog import ServiceCatalog
from f8pysdk.service_runtime_tools.discovery import load_discovery_into_catalog


def _inject_builtin_pystudio_specs(catalog: ServiceCatalog) -> str | None:
    registry = register_pystudio_specs()
    service_spec = registry.service_spec(STUDIO_SERVICE_CLASS)
    if service_spec is None:
        return None
    catalog.register_service(service_spec)
    for operator_spec in registry.operator_specs(STUDIO_SERVICE_CLASS):
        catalog.register_operator(operator_spec)
    return str(service_spec.serviceClass)


def _reset_service_catalog() -> ServiceCatalog:
    catalog = ServiceCatalog.instance()
    catalog.clear()
    return catalog


def test_discovery_injects_builtin_pystudio_without_service_yml() -> None:
    catalog = _reset_service_catalog()

    found = load_discovery_into_catalog(
        roots=[],
        catalog=catalog,
        builtin_injectors=(_inject_builtin_pystudio_specs,),
    )

    assert STUDIO_SERVICE_CLASS in found
    assert catalog.services.has(STUDIO_SERVICE_CLASS)
    service_spec = catalog.services.get(STUDIO_SERVICE_CLASS)
    assert service_spec is not None
    assert service_spec.specKind == "service"
    assert service_spec.paletteCategory == "svc"
    assert all(op.serviceClass == STUDIO_SERVICE_CLASS for op in catalog.operators.all())
    assert catalog.service_entry_path(STUDIO_SERVICE_CLASS) is None


def test_discovery_builtin_pystudio_injection_is_idempotent() -> None:
    catalog = _reset_service_catalog()

    first_found = load_discovery_into_catalog(
        roots=[],
        catalog=catalog,
        builtin_injectors=(_inject_builtin_pystudio_specs,),
    )
    first_operators = [op for op in catalog.operators.all() if op.serviceClass == STUDIO_SERVICE_CLASS]

    second_found = load_discovery_into_catalog(
        roots=[],
        catalog=catalog,
        builtin_injectors=(_inject_builtin_pystudio_specs,),
    )
    second_operators = [op for op in catalog.operators.all() if op.serviceClass == STUDIO_SERVICE_CLASS]

    assert STUDIO_SERVICE_CLASS in first_found
    assert STUDIO_SERVICE_CLASS in second_found
    assert len(first_operators) > 0
    assert len(second_operators) == len(first_operators)


def test_renderer_registry_fallback_service_and_operator() -> None:
    reg = RenderNodeRegistry()
    svc = reg.get("not_registered_service_renderer", node_kind="service")
    op = reg.get("not_registered_operator_renderer", node_kind="operator")
    assert svc is F8StudioServiceBaseNode
    assert op is F8StudioOperatorBaseNode


def test_discovery_registers_note_operator_spec() -> None:
    catalog = _reset_service_catalog()
    load_discovery_into_catalog(
        roots=[],
        catalog=catalog,
        builtin_injectors=(_inject_builtin_pystudio_specs,),
    )

    note = next((op for op in catalog.operators.all() if op.operatorClass == NOTE_OPERATOR_CLASS), None)
    assert note is not None
    assert note.specKind == "operator"
    assert note.paletteCategory == STUDIO_SERVICE_CLASS
    assert list(note.dataInPorts or []) == []
    assert list(note.dataOutPorts or []) == []
    assert list(note.execInPorts or []) == []
    assert list(note.execOutPorts or []) == []
    state_fields = list(note.stateFields or [])
    assert len(state_fields) == 1
    assert state_fields[0].name == "content"


def test_renderer_registry_resolves_note_renderer() -> None:
    reg = RenderNodeRegistry()
    renderer = reg.get("note_markdown", node_kind="operator")
    assert renderer is NoteRenderNode


def test_discovery_registers_patch_hub_operator_spec() -> None:
    catalog = _reset_service_catalog()
    load_discovery_into_catalog(
        roots=[],
        catalog=catalog,
        builtin_injectors=(_inject_builtin_pystudio_specs,),
    )

    patch_hub = next((op for op in catalog.operators.all() if op.operatorClass == PATCH_HUB_OPERATOR_CLASS), None)
    assert patch_hub is not None
    assert patch_hub.rendererClass == "patch_hub"
    assert [str(port.name or "") for port in list(patch_hub.dataInPorts or [])] == ["data"]
    assert [str(port.name or "") for port in list(patch_hub.dataOutPorts or [])] == ["data"]
    assert [str(field.name or "") for field in list(patch_hub.stateFields or [])] == ["state"]


def test_renderer_registry_resolves_patch_hub_renderer() -> None:
    reg = RenderNodeRegistry()
    renderer = reg.get("patch_hub", node_kind="operator")
    assert renderer is PatchHubRenderNode


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def test_patch_hub_renderer_stays_terminal_only() -> None:
    _ensure_app()
    node = PatchHubRenderNode()
    input_names = set(node.inputs().keys())
    output_names = set(node.outputs().keys())

    assert "[D]data" in input_names
    assert "data[D]" in output_names
    assert "[S]state" in input_names
    assert "state[S]" in output_names
    assert "state" not in node.model.custom_properties
    assert not node.view._state_inline_proxies


def test_patch_hub_terminal_labels_are_visible_on_input_side() -> None:
    _ensure_app()
    node = PatchHubRenderNode()
    node.view.draw_node()

    def _port_name(port: object) -> str:
        try:
            return str(port.name() or "")
        except (AttributeError, TypeError):
            return str(getattr(port, "name", "") or "")

    input_texts = {
        _port_name(port): text for port, text in node.view._input_items.items() if _port_name(port).startswith(("[D]", "[S]"))
    }
    output_texts = {
        _port_name(port): text for port, text in node.view._output_items.items() if _port_name(port).endswith(("[D]", "[S]"))
    }

    assert input_texts["[D]data"].isVisible()
    assert input_texts["[S]state"].isVisible()
    assert not output_texts["data[D]"].isVisible()
    assert not output_texts["state[S]"].isVisible()


def test_patch_hub_port_editor_renames_mirrored_terminals() -> None:
    _ensure_app()
    node = PatchHubRenderNode()
    editor = _F8SpecPortEditor(node=node)

    data_row = editor._sec_patch_data.rows()[0]
    state_row = editor._sec_patch_state.rows()[0]
    editor._rename_patch_data(data_row, "fanout")
    editor._rename_patch_state(state_row, "bridge")

    assert [str(port.name or "") for port in list(node.spec.dataInPorts or [])] == ["fanout"]
    assert [str(port.name or "") for port in list(node.spec.dataOutPorts or [])] == ["fanout"]
    assert [str(field.name or "") for field in list(node.spec.stateFields or [])] == ["bridge"]


def test_patch_hub_commands_tab_stays_hidden_without_commands_or_editability() -> None:
    node = PatchHubRenderNode()
    assert _should_show_commands_tab(node.spec) is False


def test_commands_tab_stays_visible_when_commands_are_editable_even_if_empty() -> None:
    spec = F8OperatorSpec(
        serviceClass="f8.test",
        operatorClass="f8.test.operator",
        label="Operator",
        editPolicy=F8SpecEditPolicy(commands=editable_collection_edit_policy()),
        commands=[],
    )

    assert _should_show_commands_tab(spec) is True


def test_discovery_registers_value_stepper_operator_spec() -> None:
    catalog = _reset_service_catalog()
    load_discovery_into_catalog(
        roots=[],
        catalog=catalog,
        builtin_injectors=(_inject_builtin_pystudio_specs,),
    )

    value_stepper = next(
        (op for op in catalog.operators.all() if op.operatorClass == VALUE_STEPPER_OPERATOR_CLASS),
        None,
    )
    assert value_stepper is not None
    state_fields = {field.name: field for field in list(value_stepper.stateFields or [])}
    assert state_fields["value"].uiControl == "slider"
    assert state_fields["increaseTrigger"].uiControl == "button"
    assert state_fields["decreaseTrigger"].uiControl == "button"


def test_discovery_registers_expr_operator_specs() -> None:
    catalog = _reset_service_catalog()
    load_discovery_into_catalog(
        roots=[],
        catalog=catalog,
        builtin_injectors=(_inject_builtin_pystudio_specs,),
    )

    data_expr = next((op for op in catalog.operators.all() if op.operatorClass == DATA_EXPR_OPERATOR_CLASS), None)
    state_expr = next((op for op in catalog.operators.all() if op.operatorClass == STATE_EXPR_OPERATOR_CLASS), None)

    assert data_expr is not None
    assert state_expr is not None
    assert data_expr.serviceClass == STUDIO_SERVICE_CLASS
    assert state_expr.serviceClass == STUDIO_SERVICE_CLASS
    assert data_expr.paletteCategory == STUDIO_SERVICE_CLASS
    assert state_expr.paletteCategory == STUDIO_SERVICE_CLASS
    assert data_expr.label == "Studio Data Expr"
    assert state_expr.label == "Studio State Expr"

from __future__ import annotations

from typing import Any

from f8pysdk import F8StateAccess

from ...components.state_builders import StateControlSpec, build_panel_control_binding
from ...ui_control import parse_ui_control
from ...editor_assist.protocol import editor_assist_context_for_field
from ...editor_assist.session import EditorSessionKey
from ...editor_assist.workspace import EditorAssistContext
from .descriptors import ControlBuildContext
from ...nodegraph.state_pool_resolver import build_node_pool_resolver, parse_multiselect_pool, parse_select_pool
from ...nodegraph.state_schema import (
    schema_enum_items,
    schema_numeric_range,
    schema_type_any,
    state_field_label,
    state_field_schema,
    state_field_ui_control,
)


def _editor_assist_context_for_field(node: Any, prop_name: str, language: str) -> EditorAssistContext | None:
    field_name = str(prop_name or "").strip()
    lang = str(language or "").strip().lower()
    if not field_name or not lang:
        return None
    try:
        spec = node.spec
    except Exception:
        return None
    return editor_assist_context_for_field(spec, field_kind="state", field_key=field_name, language=lang, node=node)


def _editor_session_key_for_node(node: Any, prop_name: str) -> EditorSessionKey | None:
    field_name = str(prop_name or "").strip()
    if not field_name:
        return None
    try:
        graph = node.graph
        node_id = str(node.id or "").strip()
    except AttributeError:
        return None
    if graph is None or not node_id:
        return None
    return EditorSessionKey.studio_node(
        graph_id=f"graph:{id(graph)}",
        node_id=node_id,
        field_name=field_name,
    )


def build_state_panel_control(context: ControlBuildContext) -> Any:
    node = context.node
    prop_name = str(context.prop_name or "")
    schema = state_field_schema(node, prop_name)
    schema_t = schema_type_any(schema) if schema is not None else ""
    ui_control = state_field_ui_control(node, prop_name)
    parsed_ui = parse_ui_control(ui_control)
    ui_control_l = parsed_ui.control_name
    ui_language = parsed_ui.ui_language
    field_label = state_field_label(node, prop_name) or prop_name
    enum_items = schema_enum_items(schema) if schema is not None else []
    lo, hi = schema_numeric_range(schema) if schema is not None else (None, None)

    pool_field = parse_select_pool(ui_control)
    multi_pool_field = parse_multiselect_pool(ui_control)
    pool_resolver = build_node_pool_resolver(node)
    spec = StateControlSpec(
        name=prop_name,
        label=field_label,
        ui_control=ui_control,
        ui_language=ui_language or "plaintext",
        schema_type=schema_t,
        enum_items=enum_items,
        minimum=lo,
        maximum=hi,
        select_pool_field=pool_field,
        multiselect_pool_field=multi_pool_field,
        is_image_b64=schema_t == "string" and (ui_control_l in {"image", "image_b64", "img"} or "b64" in prop_name.lower()),
    )
    try:
        title = f"{node.name()} - {prop_name}"
    except AttributeError:
        title = f"Edit {prop_name}"
    binding = build_panel_control_binding(
        spec=spec,
        fallback_widget=context.widget_factory.get_widget(context.widget_type),
        pool_resolver=pool_resolver,
        editor_title=title,
        assist_context=_editor_assist_context_for_field(node, prop_name, ui_language),
        assist_context_provider=lambda current_node=node, current_prop=prop_name, current_lang=ui_language: _editor_assist_context_for_field(
            current_node,
            current_prop,
            current_lang,
        ),
        editor_session_key=_editor_session_key_for_node(node, prop_name),
    )
    if binding.refresh_options is not None:
        if multi_pool_field and context.register_option_pool_dependent is not None:
            context.register_option_pool_dependent(multi_pool_field, binding.widget)
        if pool_field and context.register_option_pool_dependent is not None:
            context.register_option_pool_dependent(pool_field, binding.widget)
    return binding.widget


def state_field_is_readonly(access: F8StateAccess | None) -> bool:
    if access is None:
        return False
    return bool(access == F8StateAccess.ro)

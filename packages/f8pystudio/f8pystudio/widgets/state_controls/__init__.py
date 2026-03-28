from typing import Any, Callable

from NodeGraphQt.custom_widgets.properties_bin.node_property_factory import NodePropertyWidgetFactory

from f8pysdk import F8StateAccess

from .descriptors import ControlBuildContext, ControlBuildResult, StateFieldDescriptor
from .factory import build_state_panel_control as _build_state_panel_control
from .pool_resolver import parse_multiselect_pool, parse_select_pool, resolve_pool_items
from .readonly_policy import set_widget_read_only
from .schema_introspect import (
    effective_state_fields,
    schema_enum_items,
    schema_numeric_range,
    schema_type_any,
    state_field_access,
    state_field_label,
    state_field_schema,
    state_field_ui_control,
)

__all__ = [
    "ControlBuildContext",
    "ControlBuildResult",
    "StateFieldDescriptor",
    "build_state_panel_control",
    "effective_state_fields",
    "parse_multiselect_pool",
    "parse_select_pool",
    "resolve_pool_items",
    "schema_enum_items",
    "schema_numeric_range",
    "schema_type_any",
    "set_widget_read_only",
    "state_field_access",
    "state_field_label",
    "state_field_schema",
    "state_field_ui_control",
    "F8StateAccess",
]


def build_state_panel_control(
    *,
    node: Any,
    prop_name: str,
    widget_type: int,
    widget_factory: NodePropertyWidgetFactory,
    register_option_pool_dependent: Callable[[str, Any], None] | None = None,
) -> Any:
    context = ControlBuildContext(
        node=node,
        prop_name=str(prop_name or ""),
        widget_type=int(widget_type),
        widget_factory=widget_factory,
        register_option_pool_dependent=register_option_pool_dependent,
    )
    return _build_state_panel_control(context)

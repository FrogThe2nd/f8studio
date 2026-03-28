from __future__ import annotations

"""
Inline node-hosted controls for state values.

This module binds state-value controls into the nodegraph item environment,
handling node callbacks, option-pool refresh, styling, and graph sync.
"""

import enum
import json
import logging
from typing import Any

from qtpy import QtCore, QtGui, QtWidgets

from f8pysdk.schema_helpers import schema_type

from ...components.controls import parse_multiselect_pool, parse_select_pool
from ...ui_control import parse_ui_control
from ...components.state_builders import (
    StateControlSpec,
    build_inline_control_binding,
    set_control_read_only,
)
from ...components.wave import (
    WAVE_PATTERN_EDITOR_DEPENDENCY_FIELDS,
    WAVE_PREVIEW_DEPENDENCY_FIELDS,
)
from ...editor_assist.protocol import editor_assist_context_for_field
from ...editor_assist.workspace import EditorAssistContext
from ...widgets.state_controls.pool_resolver import resolve_pool_items
from ...widgets.studio_node_code_editor import get_node_text, resolve_node, set_node_text, studio_session_key
from ...widgets.ui_state_mutations import set_state_inline_expanded, state_inline_expanded
from .node_item_core import StateFieldInfo, state_field_info
from .service_toolbar_host import F8ElideToolButton, F8ForceGlobalToolTipFilter

logger = logging.getLogger(__name__)

INLINE_HEADER_BUTTON_STYLE = """
    QToolButton {
        color: rgb(235, 235, 235);
        background: transparent;
        border: 1px solid rgba(255, 255, 255, 18);
        border-radius: 4px;
        padding: 2px 8px;
        text-align: left;
    }
    QToolButton:hover { background: transparent; }
    QToolButton:checked { background: transparent; }
"""


def _json_safe_schema_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (list, tuple)):
        return [_json_safe_schema_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe_schema_value(item) for key, item in value.items()}
    return str(value)


def state_inline_control_serial(node_item: Any, info: StateFieldInfo) -> str:
    """
    Signature used to decide whether an inline state control must be rebuilt.

    Include schema details that affect the control widget itself, not just
    cosmetic metadata like label/description.
    """
    try:
        value_schema = info.value_schema
        enum_items = node_item._schema_enum_items(value_schema)
        minimum, maximum = node_item._schema_numeric_range(value_schema)
        default_value = None
        if value_schema is not None:
            try:
                default_value = value_schema.default
            except AttributeError:
                default_value = None
        return json.dumps(
            {
                "access": info.access_str,
                "required": info.required,
                "uiControl": info.ui_control,
                "schemaType": str(schema_type(value_schema) or ""),
                "enum": [str(item) for item in enum_items],
                "minimum": minimum,
                "maximum": maximum,
                "default": _json_safe_schema_value(default_value),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    except Exception:
        return ""


def _refresh_embedded_text_palette(widget: QtWidgets.QWidget) -> None:
    palette = widget.palette()
    text_color = QtGui.QColor(235, 235, 235)
    placeholder_color = QtGui.QColor(200, 200, 200, 140)

    for group in (
        QtGui.QPalette.ColorGroup.Active,
        QtGui.QPalette.ColorGroup.Inactive,
        QtGui.QPalette.ColorGroup.Disabled,
    ):
        palette.setColor(group, QtGui.QPalette.ColorRole.Text, text_color)
        palette.setColor(group, QtGui.QPalette.ColorRole.WindowText, text_color)
        palette.setColor(group, QtGui.QPalette.ColorRole.ButtonText, text_color)
        palette.setColor(group, QtGui.QPalette.ColorRole.BrightText, text_color)
        try:
            palette.setColor(group, QtGui.QPalette.ColorRole.PlaceholderText, placeholder_color)
        except AttributeError:
            pass

    try:
        palette.setBrush(QtGui.QPalette.ColorRole.PlaceholderText, placeholder_color)
    except (AttributeError, TypeError):
        pass

    widget.setPalette(palette)
    if isinstance(widget, QtWidgets.QAbstractScrollArea):
        viewport = widget.viewport()
        if viewport is not None:
            viewport.setPalette(palette)
    try:
        widget.update()
    except (AttributeError, RuntimeError, TypeError):
        pass


def _apply_text_palette(widget: QtWidgets.QWidget) -> None:
    _refresh_embedded_text_palette(widget)


def build_inline_header_button(
    *,
    label: str,
    tooltip: str,
    expandable: bool,
    expanded: bool = False,
) -> tuple[QtWidgets.QWidget, F8ElideToolButton]:
    header = QtWidgets.QWidget()
    header_lay = QtWidgets.QHBoxLayout(header)
    header_lay.setContentsMargins(0, 0, 0, 0)
    header_lay.setSpacing(6)
    header.setAttribute(QtCore.Qt.WA_StyledBackground, True)
    header.setStyleSheet("background: transparent;")

    btn = F8ElideToolButton()
    btn.setCheckable(bool(expandable))
    btn.setChecked(bool(expanded) if expandable else False)
    btn.setAutoRaise(True)
    if expandable:
        btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        btn.setArrowType(QtCore.Qt.DownArrow if expanded else QtCore.Qt.RightArrow)
    else:
        btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        btn.setArrowType(QtCore.Qt.NoArrow)
    btn.set_full_text(label)
    btn.setToolTip(tooltip)
    btn.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
    btn.setStyleSheet(INLINE_HEADER_BUTTON_STYLE)

    header_lay.addWidget(btn, 1)
    return header, btn


def _editor_assist_context(
    graph: Any,
    *,
    node_id: str,
    state_field_name: str,
    language: str,
) -> EditorAssistContext | None:
    field_name = str(state_field_name or "").strip()
    lang = str(language or "").strip().lower()
    if not field_name or not lang:
        return None

    node = resolve_node(graph, node_id)
    if node is None:
        return None
    spec = None
    try:
        spec = node.spec  # type: ignore[attr-defined]
    except Exception:
        spec = None
    if spec is None:
        return None

    return editor_assist_context_for_field(spec, field_kind="state", field_key=field_name, language=lang, node=node)


def is_state_inline_input_connected(node_item: Any, field_name: str) -> bool:
    """
    True if the state field is upstream-driven via a state-edge.
    """
    name = str(field_name or "").strip()
    if not name:
        return False
    node = node_item._backend_node()
    if node is None:
        return False
    port = node.get_input(f"[S]{name}")
    if port is None:
        return False
    return bool(port.connected_ports())


def set_state_inline_control_read_only(control: QtWidgets.QWidget, *, read_only: bool) -> None:
    """
    Best-effort toggle for inline state controls hosted in the node item.
    """
    set_control_read_only(control, read_only=read_only)


def refresh_state_inline_control_read_only(node_item: Any) -> None:
    """
    Refresh readonly state for already-built inline state controls.
    """
    node = node_item._backend_node()
    if node is None:
        return
    try:
        fields = list(node.effective_state_fields() or [])
    except Exception:
        fields = []
    for field in fields:
        info = state_field_info(field)
        if info is None or not info.show_on_node:
            continue
        name = info.name
        read_only = info.access_str == "ro" or is_state_inline_input_connected(node_item, name)
        try:
            binding = node_item._state_inline_bindings.get(name)
        except AttributeError:
            binding = None
        if binding is not None:
            binding.set_read_only(bool(read_only))
            continue
        ctrl = node_item._state_inline_controls.get(name)
        if ctrl is not None:
            set_state_inline_control_read_only(ctrl, read_only=bool(read_only))


def sync_state_inline_controls_from_graph_property(node_item: Any, node: Any, name: str, value: Any) -> None:
    """
    Keep inline state widgets in sync with NodeGraphQt properties.

    The inspector already tracks these through NodeGraphQt's own property
    widgets; since inline widgets are custom QWidgets, mirror updates here to
    get the same "two-way binding" behavior.
    """
    try:
        if str(node.id or "") != str(node_item.id or ""):
            return
    except (AttributeError, TypeError):
        return
    key = str(name or "").strip()
    if not key:
        return
    preview_updater = None
    if key in WAVE_PREVIEW_DEPENDENCY_FIELDS:
        preview_updater = node_item._state_inline_updaters.get("preview")
    pattern_updater = None
    if key in WAVE_PATTERN_EDITOR_DEPENDENCY_FIELDS:
        pattern_updater = node_item._state_inline_updaters.get("points")

    updater = node_item._state_inline_updaters.get(key)
    if updater is not None:
        try:
            updater(value)
        except Exception:
            try:
                node_id = str(node_item.id or "")
            except Exception:
                node_id = ""
            logger.exception("inline state updater failed nodeId=%s key=%s", node_id, key)

    if preview_updater is not None and preview_updater is not updater:
        try:
            preview_value = node.get_property("preview")
        except KeyError:
            preview_value = None
        try:
            preview_updater(preview_value)
        except Exception:
            try:
                node_id = str(node_item.id or "")
            except Exception:
                node_id = ""
            logger.exception("inline wave preview updater failed nodeId=%s key=%s", node_id, key)

    if pattern_updater is not None and pattern_updater is not updater:
        try:
            points_value = node.get_property("points")
        except KeyError:
            points_value = None
        try:
            pattern_updater(points_value)
        except Exception:
            try:
                node_id = str(node_item.id or "")
            except Exception:
                node_id = ""
            logger.exception("inline wave pattern updater failed nodeId=%s key=%s", node_id, key)

    refresh_state_inline_option_pools(node_item, key)


def refresh_state_inline_option_pools(node_item: Any, changed_field: str) -> None:
    """
    If `changed_field` is used as an option-pool, refresh all dependent option controls.
    """
    pool = str(changed_field or "").strip()
    if not pool:
        return
    if pool not in set(node_item._state_inline_option_pools.values()):
        return
    node = node_item._backend_node()
    if node is None:
        return
    try:
        bindings = node_item._state_inline_bindings
    except AttributeError:
        bindings = {}
    for field, pool_name in list(node_item._state_inline_option_pools.items()):
        if pool_name != pool:
            continue
        binding = bindings.get(field)
        if binding is not None and binding.refresh_options is not None:
            try:
                binding.refresh_options()
            except (RuntimeError, TypeError):
                continue
            continue
        ctrl = node_item._state_inline_controls.get(field)
        if ctrl is None:
            continue
        try:
            pool_value = node.get_property(pool)
        except Exception:
            pool_value = None
        items = resolve_pool_items(pool_value)
        try:
            selected_value = node.get_property(field)
        except Exception:
            try:
                selected_value = ctrl.value()
            except Exception:
                selected_value = None
        try:
            ctrl.set_options(items, labels=items)
            ctrl.set_value(selected_value)
        except (AttributeError, RuntimeError, TypeError):
            continue


def toggle_state_inline_section(node_item: Any, name: str, expanded: bool) -> None:
    state_name = str(name)
    old_scene_rect = None
    try:
        old_scene_rect = node_item.mapToScene(node_item.boundingRect()).boundingRect()
    except RuntimeError:
        old_scene_rect = None

    node_item._state_inline_expanded[state_name] = bool(expanded)
    node = node_item._backend_node()
    if node is not None:
        try:
            set_state_inline_expanded(node, state_name=state_name, expanded=bool(expanded))
        except AttributeError:
            logger.exception("node missing ui_state/set_ui_state; cannot persist expand state")
    btn = node_item._state_inline_toggles.get(state_name)
    if btn is not None:
        try:
            btn.setArrowType(QtCore.Qt.DownArrow if expanded else QtCore.Qt.RightArrow)
        except RuntimeError:
            pass
    body = node_item._state_inline_bodies.get(state_name)
    if body is not None:
        try:
            body.setVisible(bool(expanded))
        except RuntimeError:
            pass

    def _redraw_and_invalidate() -> None:
        node_item.draw_node()
        new_scene_rect = node_item.mapToScene(node_item.boundingRect()).boundingRect()
        rect = new_scene_rect
        if old_scene_rect is not None:
            rect = old_scene_rect.united(new_scene_rect)
        rect = rect.adjusted(-6, -6, 6, 6)
        scene = node_item.scene()
        if scene is not None:
            scene.update(rect)
        viewer = node_item.viewer()
        if viewer is not None:
            viewer.viewport().update()

    try:
        QtCore.QTimer.singleShot(0, _redraw_and_invalidate)
    except RuntimeError:
        _redraw_and_invalidate()


def build_state_inline_control(node_item: Any, state_field: StateFieldInfo) -> QtWidgets.QWidget:
    name = state_field.name
    ui_raw = state_field.ui_control
    parsed_ui = parse_ui_control(ui_raw)
    ui = parsed_ui.control_name
    schema = state_field.value_schema
    access_s = state_field.access_str
    schema_type_value = (schema_type(schema) or "") if schema is not None else ""

    enum_items = node_item._schema_enum_items(schema)
    lo, hi = node_item._schema_numeric_range(schema)
    select_pool_field = parse_select_pool(ui_raw)
    multiselect_pool_field = parse_multiselect_pool(ui_raw)
    field_tooltip = state_field.tooltip if state_field.tooltip != name else ""

    def _common_style(widget: QtWidgets.QWidget) -> None:
        # Make controls readable on dark node themes.
        widget.setStyleSheet(
            """
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {
                color: rgb(235, 235, 235);
                background: rgba(0, 0, 0, 45);
                border: 1px solid rgba(255, 255, 255, 55);
                border-radius: 3px;
                padding: 1px 4px;
            }
            QPlainTextEdit, QTextEdit {
                selection-background-color: rgb(80, 130, 180);
            }
            QComboBox::drop-down { border: 0px; }
            QComboBox QAbstractItemView {
                color: rgb(235, 235, 235);
                background: rgb(35, 35, 35);
                selection-background-color: rgb(80, 130, 180);
            }
            QCheckBox { color: rgb(235, 235, 235); }
            QCheckBox::indicator {
                width: 13px;
                height: 13px;
                border: 1px solid rgba(255, 255, 255, 90);
                background: rgba(0, 0, 0, 35);
                border-radius: 2px;
            }
            QCheckBox::indicator:checked { background: rgba(120, 200, 255, 90); }
            """
        )

    def _install_global_tooltip_filter(widget: QtWidgets.QWidget) -> None:
        tooltip_filter = F8ForceGlobalToolTipFilter(widget)
        widget.installEventFilter(tooltip_filter)
        node_item._tooltip_filters.append(tooltip_filter)

    def _set_node_value(value: Any, *, push_undo: bool) -> None:
        node = node_item._backend_node()
        if node is None or not name:
            return
        try:
            node.set_property(name, value, push_undo=push_undo)
        except TypeError:
            node.set_property(name, value)

    def _get_node_value() -> Any:
        node = node_item._backend_node()
        if node is None or not name:
            return None
        try:
            return node.get_property(name)
        except KeyError:
            return None

    def _get_node_property(field_name: str) -> Any:
        node = node_item._backend_node()
        if node is None:
            return None
        try:
            return node.get_property(field_name)
        except KeyError:
            return None

    def _pool_items(pool_field: str | None) -> list[str]:
        if not pool_field:
            return []
        node = node_item._backend_node()
        if node is None:
            return []
        try:
            value = node.get_property(pool_field)
        except Exception:
            return []
        return resolve_pool_items(value)

    read_only = access_s == "ro" or node_item._is_state_inline_input_connected(name)
    spec = StateControlSpec(
        name=name,
        label=state_field.label or name,
        ui_control=ui_raw,
        ui_language=state_field.ui_language or "plaintext",
        schema_type=schema_type_value,
        enum_items=enum_items,
        minimum=lo,
        maximum=hi,
        field_tooltip=field_tooltip,
        select_pool_field=select_pool_field,
        multiselect_pool_field=multiselect_pool_field,
        is_image_b64=schema_type_value == "string" and (ui in {"image", "image_b64", "img"} or "b64" in name.lower()),
    )
    try:
        graph = node_item._graph()
    except AttributeError:
        graph = None
    node = node_item._backend_node()
    node_id = ""
    if node is not None:
        node_id = str(node.id or "").strip()

    try:
        viewer = node_item.viewer()
    except AttributeError:
        viewer = None
    warning_parent = None
    if viewer is not None:
        try:
            warning_parent = viewer.window() if viewer.window() is not None else viewer
        except (AttributeError, RuntimeError, TypeError):
            warning_parent = viewer

    def _get_persisted_code_value() -> str:
        if graph is None or not node_id:
            current = _get_node_value()
            return "" if current is None else str(current)
        text = get_node_text(graph, node_id, name)
        if text:
            return text
        current = _get_node_value()
        return "" if current is None else str(current)

    def _set_persisted_code_value(updated: str) -> None:
        if graph is None or not node_id:
            _set_node_value(updated, push_undo=True)
            return
        set_node_text(
            graph,
            node_id,
            name,
            updated,
            push_undo=True,
            warning_parent=warning_parent,
        )

    binding = build_inline_control_binding(
        spec=spec,
        read_only=read_only,
        value_getter=_get_node_value,
        value_setter=_set_node_value,
        property_value_getter=_get_node_property,
        pool_resolver=lambda pool_field: _pool_items(pool_field),
        code_title=f"{node_item.name} - {spec.label}",
        code_value_getter=_get_persisted_code_value,
        code_value_setter=_set_persisted_code_value,
        assist_context=_editor_assist_context(
            graph,
            node_id=node_id,
            state_field_name=name,
            language=parsed_ui.ui_language or "plaintext",
        ),
        assist_context_provider=lambda: _editor_assist_context(
            graph,
            node_id=node_id,
            state_field_name=name,
            language=parsed_ui.ui_language or "plaintext",
        ),
        editor_session_key=studio_session_key(graph, node_id, name) if graph is not None and node_id else None,
        style_applier=_common_style,
        text_palette_applier=_apply_text_palette,
        tooltip_filter_installer=_install_global_tooltip_filter,
    )
    try:
        bindings = node_item._state_inline_bindings
    except AttributeError:
        bindings = {}
        node_item._state_inline_bindings = bindings
    bindings[name] = binding
    node_item._state_inline_updaters[name] = binding.apply_value
    if select_pool_field:
        node_item._state_inline_option_pools[name] = select_pool_field
    if multiselect_pool_field:
        node_item._state_inline_option_pools[name] = multiselect_pool_field
    return binding.widget


def ensure_state_inline_controls(node_item: Any) -> None:
    node_item._ensure_graph_property_hook()
    node = node_item._backend_node()
    if node is None:
        return
    try:
        fields = list(node.effective_state_fields() or [])
    except Exception:
        try:
            spec = node.spec
        except Exception:
            spec = None
        if spec is None:
            fields = []
        else:
            try:
                fields = list(spec.stateFields or [])
            except Exception:
                fields = []

    show: list[StateFieldInfo] = []
    for field in fields:
        info = state_field_info(field)
        if info is None or not info.show_on_node:
            continue
        show.append(info)

    desired = [info.name for info in show]

    # delete stale widgets.
    for name in list(node_item._state_inline_proxies.keys()):
        if name in desired:
            continue
        proxy = node_item._state_inline_proxies.pop(name, None)
        node_item._state_inline_controls.pop(name, None)
        node_item._state_inline_bindings.pop(name, None)
        node_item._state_inline_updaters.pop(name, None)
        node_item._state_inline_toggles.pop(name, None)
        node_item._state_inline_headers.pop(name, None)
        node_item._state_inline_bodies.pop(name, None)
        node_item._state_inline_expanded.pop(name, None)
        node_item._state_inline_option_pools.pop(name, None)
        node_item._state_inline_ctrl_serial.pop(name, None)
        if proxy is None:
            continue
        old = None
        try:
            old = proxy.widget()
        except Exception:
            old = None
        try:
            proxy.setWidget(None)
        except RuntimeError:
            pass
        if old is not None:
            try:
                old.setParent(None)
            except RuntimeError:
                pass
            try:
                old.deleteLater()
            except RuntimeError:
                pass
        try:
            proxy.setParentItem(None)
            if node_item.scene() is not None:
                node_item.scene().removeItem(proxy)
        except RuntimeError:
            pass

    for info in show:
        # Always keep label/tooltip up to date without rebuilding.
        name = info.name
        label = info.label or name
        tip = info.tooltip or name
        btn_existing = node_item._state_inline_toggles.get(name)
        if btn_existing is not None:
            try:
                btn_existing.set_full_text(label)
            except RuntimeError:
                pass
            try:
                btn_existing.setToolTip(tip)
            except RuntimeError:
                pass

        ctrl_sig = state_inline_control_serial(node_item, info)
        if name in node_item._state_inline_proxies and ctrl_sig and ctrl_sig == node_item._state_inline_ctrl_serial.get(name, ""):
            continue

        # Default collapsed; restore persisted expand state from ui overrides.
        expanded = False
        persisted_expanded = state_inline_expanded(node, name)
        if persisted_expanded is not None:
            expanded = bool(persisted_expanded)
        expanded = bool(node_item._state_inline_expanded.get(name, expanded))
        control = node_item._build_state_inline_control(info)

        # Header: toggle button (state name).
        header, btn = build_inline_header_button(label=label, tooltip=tip, expandable=True, expanded=expanded)

        # Body: control widget (collapsed by default).
        body = QtWidgets.QWidget()
        body_lay = QtWidgets.QVBoxLayout(body)
        body_lay.setContentsMargins(8, 0, 8, 6)
        body_lay.setSpacing(0)
        body_lay.addWidget(control)
        body.setVisible(expanded)
        body.setStyleSheet(
            """
            QWidget {
                background: transparent;
                border: 0px;
            }
            """
        )

        panel = QtWidgets.QWidget()
        panel_lay = QtWidgets.QVBoxLayout(panel)
        panel_lay.setContentsMargins(0, 0, 0, 0)
        panel_lay.setSpacing(0)
        panel_lay.addWidget(header)
        panel_lay.addWidget(body)
        panel.setProperty("_f8_state_panel", True)
        panel.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        # panel.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        panel.setStyleSheet("background: transparent;")

        # Connect toggle.
        btn.toggled.connect(lambda v, _n=name: node_item._toggle_state_inline_section(_n, bool(v)))  # type: ignore[attr-defined]
        btn.pressed.connect(node_item._select_node_from_embedded_widget)  # type: ignore[attr-defined]

        # Install/replace proxy.
        proxy = node_item._state_inline_proxies.get(name)
        if proxy is None:
            proxy = QtWidgets.QGraphicsProxyWidget(node_item)
            node_item._state_inline_proxies[name] = proxy

        old = None
        try:
            old = proxy.widget()
        except Exception:
            old = None
        proxy.setWidget(panel)
        
        if old is not None and old is not panel:
            try:
                old.setParent(None)
            except RuntimeError:
                pass
            try:
                old.deleteLater()
            except RuntimeError:
                pass

        node_item._state_inline_controls[name] = control
        node_item._state_inline_toggles[name] = btn
        node_item._state_inline_headers[name] = header
        node_item._state_inline_bodies[name] = body
        node_item._state_inline_expanded[name] = expanded
        if ctrl_sig:
            node_item._state_inline_ctrl_serial[name] = ctrl_sig
        try:
            node_item._invalidate_layout_metrics()
            node_item._prepare_layout_metrics()
            node_item.sync_proxy_mode(force=True)
        except (AttributeError, RuntimeError, TypeError):
            pass

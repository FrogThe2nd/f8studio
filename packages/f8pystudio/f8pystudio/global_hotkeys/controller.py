from __future__ import annotations

import logging
from typing import Any, Callable

from qtpy import QtCore

from ..nodegraph.state_pool_resolver import resolve_pool_items
from ..ui.support.ui_control import ParsedUiControl, parse_ui_control
from ..nodegraph.state_schema import effective_state_fields, schema_enum_items, schema_type_any
from ..nodegraph.ui_state_mutations import state_field_global_hotkey
from .backend import GlobalHotkeyBackend, create_global_hotkey_backend
from .models import (
    GlobalHotkeyAction,
    GlobalHotkeyBinding,
    GlobalHotkeyParseError,
    GlobalHotkeyRegistrationError,
    GlobalHotkeyRegistryEntry,
    GlobalHotkeyUnsupportedError,
)
from .parser import parse_global_hotkey

logger = logging.getLogger(__name__)
_HOTKEY_COMBO_CONTROLS = {"select", "dropdown", "dropbox", "combo", "combobox"}


class ControlPanelGlobalHotkeyController(QtCore.QObject):
    binding_activated = QtCore.Signal(str)
    registry_changed = QtCore.Signal()

    def __init__(
        self,
        *,
        studio_graph: Any,
        emit_log_line: Callable[[str], None] | None = None,
        backend: GlobalHotkeyBackend | None = None,
        platform_name: str | None = None,
    ) -> None:
        super().__init__()
        self._studio_graph = studio_graph
        self._emit_log_line = emit_log_line
        self._backend_error: str | None = None
        self._bindings_by_id: dict[str, GlobalHotkeyBinding] = {}
        self._registry_entries: list[GlobalHotkeyRegistryEntry] = []
        self._logged_messages: set[str] = set()
        self._suspend_depth = 0
        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(50)
        self._refresh_timer.timeout.connect(self.refresh_bindings)  # type: ignore[attr-defined]
        self.binding_activated.connect(self._on_binding_activated)  # type: ignore[attr-defined]
        if backend is not None:
            self._backend = backend
        else:
            try:
                self._backend = create_global_hotkey_backend(
                    activation_callback=lambda binding_id: self.binding_activated.emit(binding_id),
                    platform_name=platform_name,
                )
            except GlobalHotkeyUnsupportedError as exc:
                self._backend = None
                self._backend_error = str(exc)
                self._log_once(f"backend:{self._backend_error}", f"[hotkey] disabled: {self._backend_error}")

    def close(self) -> None:
        self._refresh_timer.stop()
        backend = self._backend
        self._bindings_by_id.clear()
        self._registry_entries = []
        self.registry_changed.emit()
        if backend is None:
            return
        try:
            backend.close()
        except Exception:
            logger.exception("Failed to close global hotkey backend")

    def suspend_hotkeys(self) -> None:
        self._suspend_depth += 1
        if self._suspend_depth != 1:
            return
        backend = self._backend
        if backend is not None:
            try:
                backend.unregister_all()
            except Exception:
                logger.exception("Failed to suspend global hotkeys")
        self.refresh_bindings()

    def resume_hotkeys(self) -> None:
        if self._suspend_depth <= 0:
            return
        self._suspend_depth -= 1
        if self._suspend_depth != 0:
            return
        self.refresh_bindings()

    def is_suspended(self) -> bool:
        return bool(self._suspend_depth > 0)

    def registry_entries(self) -> list[GlobalHotkeyRegistryEntry]:
        return list(self._registry_entries)

    def entries_for_hotkey(self, hotkey_text: str, *, exclude_binding_id: str = "") -> list[GlobalHotkeyRegistryEntry]:
        normalized_hotkey = str(hotkey_text or "").strip()
        excluded_binding_id = str(exclude_binding_id or "").strip()
        if not normalized_hotkey:
            return []
        try:
            normalized_hotkey = parse_global_hotkey(normalized_hotkey).display_text
        except GlobalHotkeyParseError:
            return []
        matches: list[GlobalHotkeyRegistryEntry] = []
        for binding in self._discover_bindings():
            if binding.binding_id == excluded_binding_id:
                continue
            if binding.hotkey_text != normalized_hotkey:
                continue
            matches.append(
                GlobalHotkeyRegistryEntry(
                    binding_id=binding.binding_id,
                    node_id=binding.node_id,
                    node_label=binding.node_label,
                    field_name=binding.field_name,
                    control_label=binding.control_label,
                    hotkey_text=binding.hotkey_text,
                    status="configured",
                )
            )
        return matches

    def schedule_refresh(self) -> None:
        if self._backend is None:
            return
        self._refresh_timer.start()

    def refresh_bindings(self) -> None:
        backend = self._backend
        next_bindings = self._discover_bindings()
        if backend is not None:
            try:
                backend.unregister_all()
            except Exception:
                logger.exception("Failed to clear global hotkeys before refresh")
        self._bindings_by_id = {}
        next_entries: list[GlobalHotkeyRegistryEntry] = []
        claimed_hotkeys: dict[str, GlobalHotkeyBinding] = {}
        registration_enabled = backend is not None and not self.is_suspended()
        for binding in next_bindings:
            existing_binding = claimed_hotkeys.get(binding.hotkey_text)
            if existing_binding is not None:
                next_entries.append(
                    GlobalHotkeyRegistryEntry(
                        binding_id=binding.binding_id,
                        node_id=binding.node_id,
                        node_label=binding.node_label,
                        field_name=binding.field_name,
                        control_label=binding.control_label,
                        hotkey_text=binding.hotkey_text,
                        status="conflict",
                        message=(
                            f"Already assigned to {existing_binding.node_id}: "
                            f"{existing_binding.node_label or existing_binding.node_id} - "
                            f"{existing_binding.control_label or existing_binding.field_name}"
                        ),
                    )
                )
                continue
            claimed_hotkeys[binding.hotkey_text] = binding
            if not registration_enabled:
                next_entries.append(
                    GlobalHotkeyRegistryEntry(
                        binding_id=binding.binding_id,
                        node_id=binding.node_id,
                        node_label=binding.node_label,
                        field_name=binding.field_name,
                        control_label=binding.control_label,
                        hotkey_text=binding.hotkey_text,
                        status="paused" if self.is_suspended() else "disabled",
                        message="Global hotkeys are temporarily paused." if self.is_suspended() else "",
                    )
                )
                continue
            try:
                backend.register_hotkey(binding)
            except GlobalHotkeyRegistrationError as exc:
                next_entries.append(
                    GlobalHotkeyRegistryEntry(
                        binding_id=binding.binding_id,
                        node_id=binding.node_id,
                        node_label=binding.node_label,
                        field_name=binding.field_name,
                        control_label=binding.control_label,
                        hotkey_text=binding.hotkey_text,
                        status="error",
                        message=str(exc),
                    )
                )
                self._log_once(
                    f"register:{binding.binding_id}:{binding.hotkey_text}:{exc}",
                    f"[hotkey] register failed nodeId={binding.node_id} field={binding.field_name} "
                    f"hotkey={binding.hotkey_text}: {exc}",
                )
                continue
            except Exception as exc:
                next_entries.append(
                    GlobalHotkeyRegistryEntry(
                        binding_id=binding.binding_id,
                        node_id=binding.node_id,
                        node_label=binding.node_label,
                        field_name=binding.field_name,
                        control_label=binding.control_label,
                        hotkey_text=binding.hotkey_text,
                        status="error",
                        message=str(exc),
                    )
                )
                self._log_once(
                    f"register:{binding.binding_id}:{binding.hotkey_text}:{type(exc).__name__}:{exc}",
                    f"[hotkey] register failed nodeId={binding.node_id} field={binding.field_name} "
                    f"hotkey={binding.hotkey_text}: {exc}",
                )
                continue
            self._bindings_by_id[binding.binding_id] = binding
            next_entries.append(
                GlobalHotkeyRegistryEntry(
                    binding_id=binding.binding_id,
                    node_id=binding.node_id,
                    node_label=binding.node_label,
                    field_name=binding.field_name,
                    control_label=binding.control_label,
                    hotkey_text=binding.hotkey_text,
                    status="registered",
                )
            )
        self._registry_entries = next_entries
        self.registry_changed.emit()

    def on_graph_property_changed(self, node: Any, name: str, value: Any) -> None:
        _ = (node, value)
        property_name = str(name or "").strip()
        if property_name not in {"f8_ui_state", "f8_spec"}:
            return
        self.schedule_refresh()

    def on_nodes_deleted(self, node_ids: list[str]) -> None:
        _ = node_ids
        self.schedule_refresh()

    def _discover_bindings(self) -> list[GlobalHotkeyBinding]:
        discovered: list[GlobalHotkeyBinding] = []
        nodes = list(self._studio_graph.all_nodes() or [])
        for node in nodes:
            try:
                node_id = str(node.id or "").strip()
            except Exception:
                node_id = ""
            if not node_id:
                continue
            for field in effective_state_fields(node):
                field_name = self._field_name(field)
                if not field_name:
                    continue
                parsed_ui_control = self._field_ui_control(field)
                schema = self._field_schema(field)
                numeric_type = schema_type_any(schema)
                action = self._binding_action(
                    parsed_ui_control=parsed_ui_control,
                    numeric_type=numeric_type,
                    field_has_enum=bool(schema_enum_items(schema)),
                )
                if action is None:
                    continue
                hotkey_text = state_field_global_hotkey(node, field_name)
                if not hotkey_text:
                    continue
                try:
                    hotkey_spec = parse_global_hotkey(hotkey_text)
                except GlobalHotkeyParseError as exc:
                    self._log_once(
                        f"parse:{node_id}:{field_name}:{hotkey_text}:{exc}",
                        f"[hotkey] invalid hotkey nodeId={node_id} field={field_name} hotkey={hotkey_text}: {exc}",
                    )
                    continue
                discovered.append(
                    GlobalHotkeyBinding(
                        binding_id=f"{node_id}:{field_name}",
                        node_id=node_id,
                        node_label=self._node_label(node),
                        field_name=field_name,
                        control_label=self._field_label(field, field_name),
                        hotkey_text=hotkey_spec.display_text,
                        hotkey_spec=hotkey_spec,
                        numeric_type=numeric_type,
                        action=action,
                        allow_repeat=True,
                    )
                )
        return discovered

    def _on_binding_activated(self, binding_id: str) -> None:
        binding = self._bindings_by_id.get(str(binding_id or ""))
        if binding is None:
            return
        try:
            node = self._studio_graph.get_node_by_id(binding.node_id)
        except Exception:
            node = None
        if node is None:
            self._log_once(
                f"missing-node:{binding.binding_id}",
                f"[hotkey] target node missing nodeId={binding.node_id} field={binding.field_name}",
            )
            return
        next_value = self._next_value_for_binding(node=node, binding=binding)
        if next_value is None:
            return
        try:
            node.set_property(binding.field_name, next_value, push_undo=False)
        except TypeError:
            node.set_property(binding.field_name, next_value)
        except Exception as exc:
            self._log_once(
                f"trigger:{binding.binding_id}:{type(exc).__name__}:{exc}",
                f"[hotkey] trigger failed nodeId={binding.node_id} field={binding.field_name}: {exc}",
            )

    def _next_value_for_binding(self, *, node: Any, binding: GlobalHotkeyBinding) -> Any | None:
        try:
            current_value = node.get_property(binding.field_name)
        except Exception:
            current_value = None
        if binding.action == "select_next":
            field = self._state_field_for_binding(node=node, field_name=binding.field_name)
            if field is None:
                return None
            parsed_ui_control = self._field_ui_control(field)
            choices = self._choice_values(node=node, field=field, parsed_ui_control=parsed_ui_control)
            return self._next_choice_value(choices, current_value)
        return self._increment_value(binding.numeric_type, current_value)

    @staticmethod
    def _increment_value(numeric_type: str, value: Any) -> int | float:
        if numeric_type == "number":
            try:
                return float(value) + 1.0
            except (TypeError, ValueError):
                return 1.0
        try:
            return int(value) + 1
        except (TypeError, ValueError):
            return 1

    @staticmethod
    def _next_choice_value(choices: list[str], current_value: Any) -> str | None:
        if not choices:
            return None
        current_text = "" if current_value is None else str(current_value)
        try:
            current_index = choices.index(current_text)
        except ValueError:
            return choices[0]
        return choices[(current_index + 1) % len(choices)]

    @staticmethod
    def _binding_action(
        *,
        parsed_ui_control: ParsedUiControl,
        numeric_type: str,
        field_has_enum: bool,
    ) -> GlobalHotkeyAction | None:
        control_name = parsed_ui_control.control_name
        if control_name == "button" and numeric_type in {"integer", "number"}:
            return "increment"
        if control_name in _HOTKEY_COMBO_CONTROLS or (not control_name and field_has_enum):
            return "select_next"
        return None

    @staticmethod
    def _choice_values(*, node: Any, field: Any, parsed_ui_control: ParsedUiControl) -> list[str]:
        pool_field = parsed_ui_control.select_pool_field
        if pool_field:
            try:
                return resolve_pool_items(node.get_property(pool_field))
            except (AttributeError, KeyError, TypeError):
                return []
        return schema_enum_items(ControlPanelGlobalHotkeyController._field_schema(field))

    @staticmethod
    def _state_field_for_binding(*, node: Any, field_name: str) -> Any | None:
        target_name = str(field_name or "").strip()
        if not target_name:
            return None
        for field in effective_state_fields(node):
            if ControlPanelGlobalHotkeyController._field_name(field) == target_name:
                return field
        return None

    @staticmethod
    def _field_name(field: Any) -> str:
        try:
            return str(field.name or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _field_ui_control(field: Any) -> ParsedUiControl:
        try:
            return parse_ui_control(str(field.uiControl or ""))
        except Exception:
            return parse_ui_control("")

    @staticmethod
    def _field_schema(field: Any) -> Any | None:
        try:
            return field.valueSchema
        except Exception:
            return None

    @staticmethod
    def _field_label(field: Any, fallback_name: str) -> str:
        try:
            label = str(field.label or "").strip()
        except Exception:
            label = ""
        return label or str(fallback_name or "").strip()

    @staticmethod
    def _node_label(node: Any) -> str:
        try:
            spec = node.spec
        except Exception:
            spec = None
        if spec is not None:
            try:
                label = str(spec.label or "").strip()
            except Exception:
                label = ""
            if label:
                return label
        try:
            label = str(node.name() or "").strip()
        except Exception:
            label = ""
        return label

    def _log_once(self, key: str, message: str) -> None:
        if key in self._logged_messages:
            return
        self._logged_messages.add(key)
        if self._emit_log_line is not None:
            try:
                self._emit_log_line(message)
            except Exception:
                logger.exception("Failed to emit global hotkey log line")
        logger.warning("%s", message)

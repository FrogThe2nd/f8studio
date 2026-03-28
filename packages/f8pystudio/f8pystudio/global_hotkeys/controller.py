from __future__ import annotations

import logging
from typing import Any, Callable

from qtpy import QtCore

from ..widgets.state_controls.schema_introspect import effective_state_fields, schema_type_any
from ..widgets.ui_state_mutations import state_field_global_hotkey
from .backend import GlobalHotkeyBackend, create_global_hotkey_backend
from .models import (
    GlobalHotkeyBinding,
    GlobalHotkeyParseError,
    GlobalHotkeyRegistrationError,
    GlobalHotkeyUnsupportedError,
)
from .parser import parse_global_hotkey

logger = logging.getLogger(__name__)


class ControlPanelGlobalHotkeyController(QtCore.QObject):
    binding_activated = QtCore.Signal(str)

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
        self._logged_messages: set[str] = set()
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
        if backend is None:
            return
        self._bindings_by_id.clear()
        try:
            backend.close()
        except Exception:
            logger.exception("Failed to close global hotkey backend")

    def schedule_refresh(self) -> None:
        if self._backend is None:
            return
        self._refresh_timer.start()

    def refresh_bindings(self) -> None:
        backend = self._backend
        if backend is None:
            return
        next_bindings = self._discover_bindings()
        try:
            backend.unregister_all()
        except Exception:
            logger.exception("Failed to clear global hotkeys before refresh")
        self._bindings_by_id = {}
        for binding in next_bindings:
            try:
                backend.register_hotkey(binding)
            except GlobalHotkeyRegistrationError as exc:
                self._log_once(
                    f"register:{binding.binding_id}:{binding.hotkey_text}:{exc}",
                    f"[hotkey] register failed nodeId={binding.node_id} field={binding.field_name} "
                    f"hotkey={binding.hotkey_text}: {exc}",
                )
                continue
            except Exception as exc:
                self._log_once(
                    f"register:{binding.binding_id}:{binding.hotkey_text}:{type(exc).__name__}:{exc}",
                    f"[hotkey] register failed nodeId={binding.node_id} field={binding.field_name} "
                    f"hotkey={binding.hotkey_text}: {exc}",
                )
                continue
            self._bindings_by_id[binding.binding_id] = binding

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
                ui_control = self._field_ui_control(field)
                if ui_control != "button":
                    continue
                numeric_type = schema_type_any(self._field_schema(field))
                if numeric_type not in {"integer", "number"}:
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
                        field_name=field_name,
                        hotkey_text=hotkey_spec.display_text,
                        hotkey_spec=hotkey_spec,
                        numeric_type=numeric_type,
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
        try:
            current_value = node.get_property(binding.field_name)
        except Exception:
            current_value = None
        next_value = self._increment_value(binding.numeric_type, current_value)
        try:
            node.set_property(binding.field_name, next_value, push_undo=False)
        except TypeError:
            node.set_property(binding.field_name, next_value)
        except Exception as exc:
            self._log_once(
                f"trigger:{binding.binding_id}:{type(exc).__name__}:{exc}",
                f"[hotkey] trigger failed nodeId={binding.node_id} field={binding.field_name}: {exc}",
            )

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
    def _field_name(field: Any) -> str:
        try:
            return str(field.name or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _field_ui_control(field: Any) -> str:
        try:
            return str(field.uiControl or "").strip().lower()
        except Exception:
            return ""

    @staticmethod
    def _field_schema(field: Any) -> Any | None:
        try:
            return field.valueSchema
        except Exception:
            return None

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

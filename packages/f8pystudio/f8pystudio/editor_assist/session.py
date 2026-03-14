from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from qtpy import QtCore

from ..ai_assist.llm_bridge import AiLlmBridge
from ..ai_assist.store import AiProviderStore
from .bridge import PythonEditorAssistBridge
from .workspace import EditorAssistContext

logger = logging.getLogger(__name__)

_SHARED_AI_STORE: AiProviderStore | None = None


def shared_ai_store() -> AiProviderStore:
    global _SHARED_AI_STORE
    if _SHARED_AI_STORE is None:
        _SHARED_AI_STORE = AiProviderStore()
    return _SHARED_AI_STORE


def assist_context_requires_python(context: EditorAssistContext | None) -> bool:
    if context is None:
        return False
    language = str(context.language or "").strip().lower()
    if language != "python":
        return False
    return bool(context.support_files)


def python_assist_warning(context: EditorAssistContext | None) -> str:
    if context is None:
        return ""
    return str(context.error_message or "").strip()


def resolve_assist_context(
    *,
    assist_context: EditorAssistContext | None,
    assist_context_provider: Callable[[], EditorAssistContext | None] | None,
) -> EditorAssistContext | None:
    provider = assist_context_provider
    if provider is None:
        return assist_context
    try:
        return provider()
    except Exception:
        logger.exception("Failed to build editor assist context from provider")
        return assist_context


def assist_context_fingerprint(context: EditorAssistContext | None) -> str:
    if context is None:
        return ""

    def _jsonable(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(key): _jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [_jsonable(item) for item in value]
        return str(value)

    payload = {
        "language": str(context.language or ""),
        "node_kind": str(context.node_kind or ""),
        "service_class": str(context.service_class or ""),
        "operator_class": str(context.operator_class or ""),
        "node_description": str(context.node_description or ""),
        "support_files": [[str(name), str(text)] for name, text in context.support_files],
        "overlay_prefix": str(context.overlay_prefix or ""),
        "dynamic_inputs_binding": (
            {
                "source": str(context.dynamic_inputs_binding.source or ""),
                "type_name": str(context.dynamic_inputs_binding.type_name or ""),
                "module_name": str(context.dynamic_inputs_binding.module_name or ""),
                "schema_mode": str(context.dynamic_inputs_binding.schema_mode or ""),
                "access_mode": str(context.dynamic_inputs_binding.access_mode or ""),
            }
            if context.dynamic_inputs_binding is not None
            else None
        ),
        "dynamic_outputs_binding": (
            {
                "source": str(context.dynamic_outputs_binding.source or ""),
                "type_name": str(context.dynamic_outputs_binding.type_name or ""),
                "module_name": str(context.dynamic_outputs_binding.module_name or ""),
                "schema_mode": str(context.dynamic_outputs_binding.schema_mode or ""),
                "access_mode": str(context.dynamic_outputs_binding.access_mode or ""),
            }
            if context.dynamic_outputs_binding is not None
            else None
        ),
        "data_in_ports": [
            {
                "name": str(port.name or ""),
                "required": bool(port.required),
                "value_schema": _jsonable(port.value_schema),
                "description": str(port.description or ""),
            }
            for port in context.data_in_ports
        ],
        "data_out_ports": [
            {
                "name": str(port.name or ""),
                "required": bool(port.required),
                "value_schema": _jsonable(port.value_schema),
                "description": str(port.description or ""),
            }
            for port in context.data_out_ports
        ],
        "dynamic_states_binding": (
            {
                "source": str(context.dynamic_states_binding.source or ""),
                "type_name": str(context.dynamic_states_binding.type_name or ""),
                "module_name": str(context.dynamic_states_binding.module_name or ""),
                "schema_mode": str(context.dynamic_states_binding.schema_mode or ""),
                "access_mode": str(context.dynamic_states_binding.access_mode or ""),
            }
            if context.dynamic_states_binding is not None
            else None
        ),
        "state_fields": [
            {
                "name": str(field.name or ""),
                "required": bool(field.required),
                "value_schema": _jsonable(field.value_schema),
                "access": str(field.access or ""),
                "description": str(field.description or ""),
            }
            for field in context.state_fields
        ],
        "error_message": str(context.error_message or ""),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class EditorSessionKey:
    scope: str
    node_id: str
    field_name: str

    @classmethod
    def debug_target(cls, *, session_path: Path, node_id: str, field_name: str) -> EditorSessionKey:
        return cls(
            scope=str(session_path.expanduser().resolve()),
            node_id=str(node_id or "").strip(),
            field_name=str(field_name or "").strip(),
        )

    @classmethod
    def studio_node(cls, *, graph_id: str, node_id: str, field_name: str) -> EditorSessionKey:
        return cls(
            scope=str(graph_id or "").strip(),
            node_id=str(node_id or "").strip(),
            field_name=str(field_name or "").strip(),
        )

    def as_id(self) -> str:
        return f"{self.scope}:{self.node_id}:{self.field_name}"

    def __str__(self) -> str:
        return self.as_id()


@dataclass(frozen=True)
class EditorSessionState:
    title: str
    code: str
    language: str
    session_key: EditorSessionKey | None = None
    dirty: bool = False
    close_on_save: bool = True
    assist_context: EditorAssistContext | None = None
    assist_context_provider: Callable[[], EditorAssistContext | None] | None = None


class EditorAssistContextController(QtCore.QObject):
    context_changed = QtCore.Signal(object)
    refresh_failed = QtCore.Signal(str)

    def __init__(
        self,
        *,
        initial_context: EditorAssistContext | None,
        provider: Callable[[], EditorAssistContext | None] | None,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._provider = provider
        self._context = initial_context
        self._fingerprint = assist_context_fingerprint(initial_context)
        self._pending_context: EditorAssistContext | None = None
        self._pending_fingerprint = ""
        self._last_error_sig = ""
        self._last_error_ts = 0.0
        self._poll_timer: QtCore.QTimer | None = None
        self._debounce_timer: QtCore.QTimer | None = None

    def current_context(self) -> EditorAssistContext | None:
        return self._context

    def start(self) -> None:
        if self._provider is None:
            return
        if self._poll_timer is None:
            poll_timer = QtCore.QTimer(self)
            poll_timer.setInterval(320)
            poll_timer.timeout.connect(self._poll_context_change)  # type: ignore[attr-defined]
            self._poll_timer = poll_timer
        if self._debounce_timer is None:
            debounce_timer = QtCore.QTimer(self)
            debounce_timer.setSingleShot(True)
            debounce_timer.setInterval(480)
            debounce_timer.timeout.connect(self._apply_pending_context)  # type: ignore[attr-defined]
            self._debounce_timer = debounce_timer
        self._poll_timer.start()

    def stop(self) -> None:
        poll_timer = self._poll_timer
        if poll_timer is not None:
            poll_timer.stop()
        debounce_timer = self._debounce_timer
        if debounce_timer is not None:
            debounce_timer.stop()
        self._pending_context = None
        self._pending_fingerprint = ""

    @QtCore.Slot()
    def _poll_context_change(self) -> None:
        provider = self._provider
        if provider is None:
            return
        try:
            context = provider()
        except Exception as exc:
            self._log_refresh_error("providerRefresh", exc)
            return
        fingerprint = assist_context_fingerprint(context)
        if fingerprint == self._fingerprint:
            self._pending_context = None
            self._pending_fingerprint = ""
            return
        self._pending_context = context
        self._pending_fingerprint = fingerprint
        debounce_timer = self._debounce_timer
        if debounce_timer is None:
            self._apply_pending_context()
            return
        debounce_timer.start()

    @QtCore.Slot()
    def _apply_pending_context(self) -> None:
        fingerprint = str(self._pending_fingerprint or "")
        if not fingerprint or fingerprint == self._fingerprint:
            return
        self._context = self._pending_context
        self._fingerprint = fingerprint
        self._pending_context = None
        self._pending_fingerprint = ""
        self.context_changed.emit(self._context)

    def _log_refresh_error(self, operation: str, exc: Exception) -> None:
        sig = f"{operation}:{type(exc).__name__}:{exc}"
        now = time.monotonic()
        if sig == self._last_error_sig and (now - self._last_error_ts) < 5.0:
            return
        self._last_error_sig = sig
        self._last_error_ts = now
        logger.exception("Failed to refresh editor assist context; operation=%s", operation)
        self.refresh_failed.emit(sig)


class EditorSessionController(QtCore.QObject):
    code_saved = QtCore.Signal(str)
    dirty_changed = QtCore.Signal(bool)
    close_requested = QtCore.Signal()
    context_reload_failed = QtCore.Signal(str)

    def __init__(
        self,
        *,
        title: str,
        code: str,
        language: str,
        session_key: EditorSessionKey | None = None,
        assist_context: EditorAssistContext | None = None,
        assist_context_provider: Callable[[], EditorAssistContext | None] | None = None,
        close_on_save: bool = True,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        resolved_context = resolve_assist_context(
            assist_context=assist_context,
            assist_context_provider=assist_context_provider,
        )
        effective_language = str(language or "plaintext").strip() or "plaintext"
        if assist_context_requires_python(resolved_context):
            effective_language = "python"
        self._state = EditorSessionState(
            title=str(title or "Edit Code"),
            code=str(code or ""),
            language=effective_language,
            session_key=session_key,
            close_on_save=bool(close_on_save),
            assist_context=resolved_context,
            assist_context_provider=assist_context_provider,
        )
        self._ai_store = shared_ai_store()
        self._ai_bridge = AiLlmBridge(self._ai_store, self)
        self._ai_bridge.set_assist_context(self._state.assist_context)
        self._assist_bridge: PythonEditorAssistBridge | None = None
        if effective_language.lower() == "python" and assist_context_requires_python(self._state.assist_context):
            self._assist_bridge = PythonEditorAssistBridge(
                code=self._state.code,
                language="python",
                context=self._state.assist_context,
                parent=self,
            )
            self._ai_bridge.set_lsp_bridge(self._assist_bridge)
        self._context_controller = EditorAssistContextController(
            initial_context=self._state.assist_context,
            provider=assist_context_provider,
            parent=self,
        )
        self._context_controller.context_changed.connect(self._on_context_changed)  # type: ignore[attr-defined]
        self._context_controller.refresh_failed.connect(self.context_reload_failed)  # type: ignore[attr-defined]
        if self._assist_bridge is not None:
            self._context_controller.start()

    def ai_store(self) -> AiProviderStore:
        return self._ai_store

    def ai_bridge(self) -> AiLlmBridge:
        return self._ai_bridge

    def assist_bridge(self) -> PythonEditorAssistBridge | None:
        return self._assist_bridge

    def session_key(self) -> EditorSessionKey | None:
        return self._state.session_key

    def title(self) -> str:
        return self._state.title

    def code(self) -> str:
        return self._state.code

    def language(self) -> str:
        return self._state.language

    def dirty(self) -> bool:
        return bool(self._state.dirty)

    def close_on_save(self) -> bool:
        return bool(self._state.close_on_save)

    def assist_context(self) -> EditorAssistContext | None:
        return self._state.assist_context

    def assist_context_provider(self) -> Callable[[], EditorAssistContext | None] | None:
        return self._state.assist_context_provider

    def set_close_on_save(self, close_on_save: bool) -> None:
        self._state = replace(self._state, close_on_save=bool(close_on_save))

    def set_dirty(self, dirty: bool) -> None:
        next_dirty = bool(dirty)
        if next_dirty == self._state.dirty:
            return
        self._state = replace(self._state, dirty=next_dirty)
        self.dirty_changed.emit(next_dirty)

    def save_code(self, code: str) -> None:
        text = str(code or "")
        self._state = replace(self._state, code=text, dirty=False)
        self.dirty_changed.emit(False)
        self.code_saved.emit(text)

    def request_close(self) -> None:
        self.close_requested.emit()

    def shutdown(self) -> None:
        self._context_controller.stop()
        bridge = self._assist_bridge
        if bridge is not None:
            self._assist_bridge = None
            try:
                bridge.shutdown()
            except Exception:
                logger.exception("Failed to shutdown Python editor assist bridge")

    @QtCore.Slot(object)
    def _on_context_changed(self, context_obj: object) -> None:
        context = context_obj if isinstance(context_obj, EditorAssistContext) else None
        self._ai_bridge.set_assist_context(context)
        bridge = self._assist_bridge
        if bridge is None:
            self._state = replace(self._state, assist_context=context)
            return
        if bridge.reload_context(context):
            self._state = replace(self._state, assist_context=context)
            logger.debug("python editor assist context reloaded")
            return
        message = "Failed to reload python editor assist context"
        logger.warning(message)
        self.context_reload_failed.emit(message)

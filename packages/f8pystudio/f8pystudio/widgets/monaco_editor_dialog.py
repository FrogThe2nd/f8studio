from __future__ import annotations

import json
import logging
import math
import os
import time
from typing import Any, Callable

from qtpy import QtCore, QtGui, QtWidgets

from ..ai_assist.llm_bridge import AiLlmBridge
from ..ai_assist.store import AiProviderStore
from ..editor_assist.bridge import PythonEditorAssistBridge
from ..editor_assist.workspace import EditorAssistContext
from ..qt_font_utils import normalize_font_point_size
from ..ui_notifications import show_warning
from .ai_quick_panel import AiQuickPanel

logger = logging.getLogger(__name__)

# Shared store instance — one per process so model caches are shared
_SHARED_AI_STORE: AiProviderStore | None = None


def _get_shared_ai_store() -> AiProviderStore:
    global _SHARED_AI_STORE
    if _SHARED_AI_STORE is None:
        _SHARED_AI_STORE = AiProviderStore()
    return _SHARED_AI_STORE


def _assist_context_requires_python(context: EditorAssistContext | None) -> bool:
    if context is None:
        return False
    language = str(context.language or "").strip().lower()
    if language != "python":
        return False
    return bool(tuple(context.support_files))


def _python_assist_warning(context: EditorAssistContext | None) -> str:
    if context is None:
        return ""
    return str(context.error_message or "").strip()


def _set_tool_button_point_size(button: QtWidgets.QToolButton, point_size: int) -> None:
    font = normalize_font_point_size(button.font(), fallback_point_size=point_size)
    font.setPointSize(max(1, int(point_size)))
    button.setFont(font)


def _usage_pie_icon(*, used_ratio: float, color: QtGui.QColor, size: int = 14) -> QtGui.QIcon:
    ratio = max(0.0, min(1.0, float(used_ratio)))
    pix = QtGui.QPixmap(size, size)
    pix.fill(QtCore.Qt.GlobalColor.transparent)

    painter = QtGui.QPainter(pix)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

    outer = QtCore.QRectF(1.0, 1.0, float(size - 2), float(size - 2))
    center = QtCore.QPointF(outer.center())

    base = QtGui.QColor("#4a4f57")
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    painter.setBrush(base)
    painter.drawEllipse(outer)

    if ratio > 0.0:
        painter.setBrush(color)
        start_angle = 90 * 16
        span_angle = int(-360 * 16 * ratio)
        painter.drawPie(outer, start_angle, span_angle)

    # punch a hole to make a donut/pie hybrid that reads well on dark UI
    inner_diameter = max(2.0, outer.width() * 0.46)
    inner = QtCore.QRectF(
        center.x() - inner_diameter / 2.0,
        center.y() - inner_diameter / 2.0,
        inner_diameter,
        inner_diameter,
    )
    painter.setBrush(QtGui.QColor("#1f2328"))
    painter.drawEllipse(inner)

    painter.setPen(QtGui.QPen(QtGui.QColor("#6c7380"), 1.0))
    painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
    painter.drawEllipse(outer)
    painter.end()
    return QtGui.QIcon(pix)


def _assist_context_fingerprint(context: EditorAssistContext | None) -> str:
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


def _resolve_assist_context(
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


def _ask_save_before_close(parent: QtWidgets.QWidget) -> QtWidgets.QMessageBox.StandardButton:
    return QtWidgets.QMessageBox.question(
        parent,
        "Unsaved Changes",
        "You have unsaved changes. Save before closing?",
        QtWidgets.QMessageBox.StandardButton.Yes
        | QtWidgets.QMessageBox.StandardButton.No
        | QtWidgets.QMessageBox.StandardButton.Cancel,
        QtWidgets.QMessageBox.StandardButton.Yes,
    )


class _EditorUiBridge(QtCore.QObject):
    dirty_changed = QtCore.Signal(bool)
    save_requested = QtCore.Signal()
    close_requested = QtCore.Signal()

    @QtCore.Slot(bool)
    def notify_dirty(self, dirty: bool) -> None:
        self.dirty_changed.emit(bool(dirty))

    @QtCore.Slot()
    def request_save(self) -> None:
        self.save_requested.emit()

    @QtCore.Slot()
    def request_close(self) -> None:
        self.close_requested.emit()

    @QtCore.Slot(str)
    def log_js(self, message: str) -> None:
        logger.debug("monaco js: %s", str(message or ""))

    @QtCore.Slot(str)
    def logJs(self, message: str) -> None:
        self.log_js(message)


class F8MonacoEditorDialog(QtWidgets.QDialog):
    """
    Monaco-based editor dialog with AI-assisted editing.

    Modes:
    - Inline suggestions: FIM ghost-text via AI (replaces pure LSP)
    - Chat: streaming side-panel conversation with code context
    - Edit: LLM generates patch shown in diff editor
    - Plan: agent mode with clarifying Q&A before execution

    Monaco assets from ``F8_MONACO_BASE_URL`` env var or CDN fallback.
    """

    code_saved = QtCore.Signal(str)

    def __init__(
        self,
        parent=None,
        *,
        title: str,
        code: str,
        language: str = "python",
        assist_context: EditorAssistContext | None = None,
        assist_context_provider: Callable[[], EditorAssistContext | None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(str(title or "Edit Code"))
        self._code: str = str(code or "")
        self._dirty: bool = False
        self._close_on_save: bool = True
        self._language: str = str(language or "plaintext").strip() or "plaintext"
        self._assist_context_provider = assist_context_provider
        self._assist_context_fingerprint = _assist_context_fingerprint(assist_context)
        self._assist_pending_context: EditorAssistContext | None = None
        self._assist_pending_fingerprint = ""
        self._assist_reload_poll_timer: QtCore.QTimer | None = None
        self._assist_reload_debounce_timer: QtCore.QTimer | None = None
        self._assist_error_sig = ""
        self._assist_error_ts = 0.0

        from PySide6 import QtWebChannel, QtWebEngineWidgets  # type: ignore[import-not-found]

        self._view = QtWebEngineWidgets.QWebEngineView(self)
        self._view.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.NoContextMenu)
        self._ui_bridge = _EditorUiBridge(self)
        self._assist_bridge: PythonEditorAssistBridge | None = None

        # AI assist
        self._ai_store = _get_shared_ai_store()
        self._ai_bridge = AiLlmBridge(self._ai_store, self)
        if assist_context:
            self._ai_bridge.set_assist_context(assist_context)

        self._web_channel: Any = QtWebChannel.QWebChannel(self._view.page())
        self._web_channel.registerObject("f8EditorUi", self._ui_bridge)
        self._web_channel.registerObject("aiAssist", self._ai_bridge)
        python_assist_enabled = self._language.lower() == "python" and _assist_context_requires_python(assist_context)
        if python_assist_enabled:
            self._assist_bridge = PythonEditorAssistBridge(
                code=self._code,
                language="python",
                context=assist_context,
                parent=self,
            )
            self._web_channel.registerObject("pyAssist", self._assist_bridge)
            self._ai_bridge.set_lsp_bridge(self._assist_bridge)
        self._view.page().setWebChannel(self._web_channel)

        # Context usage indicator
        self._ctx_btn = QtWidgets.QToolButton()
        self._ctx_btn.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._ctx_btn.setIconSize(QtCore.QSize(14, 14))
        self._ctx_btn.setIcon(_usage_pie_icon(used_ratio=0.0, color=QtGui.QColor("#4fc3f7")))
        self._ctx_btn.setText("100% free")
        self._ctx_btn.setToolTip("AI context usage\nUsed: 0 / 0 tok")
        _set_tool_button_point_size(self._ctx_btn, 10)
        self._ctx_btn.setStyleSheet(
            "QToolButton { color: #9aa4b2; border: none; padding: 0 4px; }"
            "QToolButton:hover { color: #d7deea; }"
        )
        self._ai_bridge.context_usage_updated.connect(self._on_context_usage_updated)  # type: ignore[attr-defined]

        # AI settings toggle button
        self._ai_panel_btn = QtWidgets.QToolButton()
        self._ai_panel_btn.setText("🤖")
        self._ai_panel_btn.setCheckable(True)
        self._ai_panel_btn.setToolTip("Toggle AI settings panel")
        _set_tool_button_point_size(self._ai_panel_btn, 16)
        self._ai_panel_btn.setStyleSheet(
            "QToolButton { border: none; padding: 0 4px; }"
            "QToolButton:checked { background: #2d2d2d; border-radius: 3px; }"
        )
        self._ai_panel_btn.toggled.connect(self._on_ai_panel_toggle)  # type: ignore[attr-defined]

        # AI quick panel (hidden by default, floating overlay)
        self._ai_quick_panel = AiQuickPanel(self._ai_store, self._ai_bridge, self)
        self._ai_quick_panel.setVisible(False)
        self._ai_quick_panel.open_full_config_requested.connect(self._open_full_ai_config)  # type: ignore[attr-defined]
        self._ai_quick_panel.raise_()

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_save_clicked)  # type: ignore[attr-defined]
        buttons.rejected.connect(self.reject)  # type: ignore[attr-defined]
        self._save_button = buttons.button(QtWidgets.QDialogButtonBox.Save)
        self._save_button.setEnabled(False)

        self._ui_bridge.dirty_changed.connect(self._on_dirty_changed)  # type: ignore[attr-defined]
        self._ui_bridge.save_requested.connect(self._on_save_clicked)  # type: ignore[attr-defined]
        self._ui_bridge.close_requested.connect(self.close)  # type: ignore[attr-defined]

        self._save_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+S"), self)
        self._save_shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._save_shortcut.activated.connect(self._on_save_clicked)
        self._close_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Q"), self)
        self._close_shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._close_shortcut.activated.connect(self.close)

        # Build layout: editor fills the space, AI panel is a floating overlay
        editor_layout = QtWidgets.QHBoxLayout()
        editor_layout.setSpacing(0)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.addWidget(self._view, 1)

        bottom_bar = QtWidgets.QHBoxLayout()
        bottom_bar.addWidget(self._ctx_btn)
        bottom_bar.addWidget(self._ai_panel_btn)
        bottom_bar.addStretch()
        bottom_bar.addWidget(buttons)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(2)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(editor_layout, 1)
        layout.addLayout(bottom_bar)

        self.resize(1120, 720)
        self._load_page()
        self._start_assist_context_sync()

    def code(self) -> str:
        return str(self._code or "")

    def _monaco_base_url(self) -> str:
        value = str(os.environ.get("F8_MONACO_BASE_URL") or "").strip().rstrip("/")
        if value:
            return value
        return "https://cdn.jsdelivr.net/npm/monaco-editor/min"

    def _load_page(self) -> None:
        base = self._monaco_base_url()
        initial = {
            "code": self._code,
            "language": self._language,
            "theme": "vs-dark",
            "pythonAssistEnabled": bool(self._assist_bridge is not None),
        }
        initial_json = json.dumps(initial, ensure_ascii=False)
        
        html = f"""
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <style>
      html, body, #container {{
        height: 100%;
        width: 100%;
        margin: 0;
        padding: 0;
        overflow: hidden;
        background: #1e1e1e;
      }}
      .f8-hunk-actions {{
        background: rgba(37,37,38,0.9);
        border: 1px solid #454545;
        border-radius: 4px;
        padding: 2px;
        display: flex;
        flex-direction: row;
        align-items: center;
        width: max-content;
        white-space: nowrap;
        gap: 2px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.5);
        pointer-events: auto;
        z-index: 100;
        backdrop-filter: blur(4px);
      }}
      .f8-hunk-btn {{
        background: transparent;
        border: 1px solid transparent;
        color: #cccccc;
        cursor: pointer;
        font-size: 13px;
        font-weight: bold;
        font-family: inherit;
        padding: 2px 8px;
        border-radius: 3px;
        transition: all 0.2s;
        line-height: 1;
      }}
      .f8-hunk-btn:hover {{
        color: #ffffff;
        background: #333333;
        border-color: #555555;
      }}
      .f8-accept:hover {{ background: #235a39; border-color: #2ea043; color: #4aff8a; }}
      .f8-reject:hover {{ background: #5a2323; border-color: #f85149; color: #ff8a8a; }}
    </style>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/themes/prism-twilight.min.css" rel="stylesheet" />
    <script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/prism.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/components/prism-python.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/components/prism-javascript.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/components/prism-bash.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/components/prism-json.min.js"></script>
    <script>
      window.__F8_INITIAL__ = {initial_json};
    </script>
    <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
    <script src="{base}/vs/loader.js"></script>
    <script>
      window._f8_editor = null;
      window._f8_editorUi = null;
      window._f8_pyAssist = null;
      window._f8_pendingCompletions = Object.create(null);
      window._f8_pendingCompletionResolves = Object.create(null);
      window._f8_pendingHovers = Object.create(null);
      window._f8_pendingSignatures = Object.create(null);
      window._f8_completionCache = null;
      window._f8_forceSuggestOnce = false;
      window._f8_lastDirty = false;
      window._f8_savedValue = '';
      window._f8_getValue = function() {{
        try {{
          if (!window._f8_editor) return "";
          return window._f8_editor.getValue();
        }} catch (e) {{
          return "";
        }}
      }};
      window._f8_isDirty = function() {{
        try {{
          if (!window._f8_editor) return false;
          return window._f8_editor.getValue() !== String(window._f8_savedValue || '');
        }} catch (e) {{
          return false;
        }}
      }};
      window._f8_notifyDirty = function() {{
        try {{
          const dirty = Boolean(window._f8_isDirty());
          if (dirty === window._f8_lastDirty) return;
          window._f8_lastDirty = dirty;
          if (window._f8_editorUi && window._f8_editorUi.notify_dirty) {{
            window._f8_editorUi.notify_dirty(dirty);
          }}
        }} catch (e) {{
        }}
      }};
      window._f8_markSaved = function() {{
        try {{
          window._f8_savedValue = window._f8_getValue();
          window._f8_lastDirty = false;
          if (window._f8_editorUi && window._f8_editorUi.notify_dirty) {{
            window._f8_editorUi.notify_dirty(false);
          }}
        }} catch (e) {{
        }}
      }};

      require.config({{ paths: {{ 'vs': '{base}/vs' }} }});
      require(['vs/editor/editor.main'], function() {{
        const init = window.__F8_INITIAL__ || {{ code: '', language: 'plaintext', theme: 'vs-dark' }};
        window._f8_editor = monaco.editor.create(document.getElementById('container'), {{
          value: String(init.code || ''),
          language: String(init.language || 'plaintext'),
          theme: String(init.theme || 'vs-dark'),
          automaticLayout: true,
          quickSuggestions: {{ other: true, comments: false, strings: true }},
          quickSuggestionsDelay: 160,
          parameterHints: {{ enabled: true, cycle: true }},
          suggest: {{ showInlineDetails: true, showStatusBar: true }},
          suggestOnTriggerCharacters: true,
          minimap: {{ enabled: false }},
          fontLigatures: true,
          fontSize: 13,
          tabSize: 4,
          insertSpaces: true,
          scrollBeyondLastLine: false,
          wordWrap: 'off',
        }});
        window._f8_savedValue = String(init.code || '');
        window._f8_lastDirty = false;
        window._f8_editor.onDidChangeModelContent(function() {{
          window._f8_notifyDirty();
        }});
        window._f8_editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, function() {{
          if (window._f8_editorUi && window._f8_editorUi.request_save) {{
            window._f8_editorUi.request_save();
          }}
        }});
        window._f8_editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyQ, function() {{
          if (window._f8_editorUi && window._f8_editorUi.request_close) {{
            window._f8_editorUi.request_close();
          }}
        }});

        function _completionKind(kind) {{
          const k = Number(kind);
          if (!Number.isFinite(k)) return monaco.languages.CompletionItemKind.Text;
          if (k < 1 || k > 25) return monaco.languages.CompletionItemKind.Text;
          return k;
        }}

        function _completionKindRank(kind) {{
          switch (Number(kind)) {{
            case monaco.languages.CompletionItemKind.Method:
            case monaco.languages.CompletionItemKind.Function:
              return 0;
            case monaco.languages.CompletionItemKind.Property:
            case monaco.languages.CompletionItemKind.Field:
            case monaco.languages.CompletionItemKind.Variable:
            case monaco.languages.CompletionItemKind.Constant:
              return 1;
            case monaco.languages.CompletionItemKind.Class:
            case monaco.languages.CompletionItemKind.Struct:
            case monaco.languages.CompletionItemKind.TypeParameter:
              return 2;
            case monaco.languages.CompletionItemKind.Module:
            case monaco.languages.CompletionItemKind.File:
            case monaco.languages.CompletionItemKind.Folder:
              return 3;
            case monaco.languages.CompletionItemKind.Keyword:
              return 4;
            default:
              return 5;
          }}
        }}

        function _setupPythonAssist(channel) {{
          if (String(init.language || '').toLowerCase() !== 'python') return;
          if (!Boolean(init.pythonAssistEnabled)) return;
          const assist = channel && channel.objects ? channel.objects.pyAssist : null;
          window._f8_pyAssist = assist || null;
          if (!assist) return;
          const completionSignal = assist.completion_ready || assist.completionReady || null;
          const completionResolveSignal = assist.completion_item_resolved || assist.completionItemResolved || null;
          const hoverSignal = assist.hover_ready || assist.hoverReady || null;
          const signatureHelpSignal = assist.signature_help_ready || assist.signatureHelpReady || null;
          const diagnosticsSignal = assist.diagnostics_ready || assist.diagnosticsReady || null;
          const requestCompletions = assist.request_completions || assist.requestCompletions || null;
          const requestCompletionResolve = assist.request_completion_item_resolve || assist.requestCompletionItemResolve || null;
          const requestHover = assist.request_hover || assist.requestHover || null;
          const requestSignatureHelp = assist.request_signature_help || assist.requestSignatureHelp || null;
          const syncDocument = assist.sync_document || assist.syncDocument || null;
          const jsLog = (window._f8_editorUi && (window._f8_editorUi.log_js || window._f8_editorUi.logJs)) || null;

          function _log(msg) {{
            try {{
              if (jsLog) jsLog(String(msg || ''));
            }} catch (e) {{
            }}
          }}

          function _toArray(value) {{
            if (Array.isArray(value)) return value;
            if (!value || typeof value !== 'object') return [];
            if (Array.isArray(value.items)) return value.items;
            const numericKeys = Object.keys(value).filter(function(k) {{
              return /^\\d+$/.test(k);
            }}).sort(function(a, b) {{
              return Number(a) - Number(b);
            }});
            if (numericKeys.length) {{
              return numericKeys.map(function(k) {{ return value[k]; }});
            }}
            return Object.values(value);
          }}

          function _decodeJson(value) {{
            if (typeof value !== 'string') return value;
            try {{
              return JSON.parse(value);
            }} catch (e) {{
              return value;
            }}
          }}

          function _asMarkdownDoc(value) {{
            const text = String(value || '').trim();
            if (!text) return null;
            return {{ value: text }};
          }}

          function _currentPrefix(model, position) {{
            try {{
              const lineText = String(model.getLineContent(position.lineNumber) || '');
              const before = lineText.slice(0, Math.max(0, Number(position.column) - 1));
              const m = before.match(/[A-Za-z0-9_]+$/);
              if (!m) return '';
              return String(m[0] || '');
            }} catch (e) {{
              return '';
            }}
          }}

          function _completionCacheKey(model, position, prefix) {{
            try {{
              const lineText = String(model.getLineContent(position.lineNumber) || '');
              const before = lineText.slice(0, Math.max(0, Number(position.column) - 1));
              const pfx = String(prefix || '');
              const base = pfx ? before.slice(0, Math.max(0, before.length - pfx.length)) : before;
              return String(position.lineNumber) + '|' + base;
            }} catch (e) {{
              return '';
            }}
          }}

          function _completionPrefixRank(label, filterText, prefixLower) {{
            if (!prefixLower) return 0;
            const l = String(label || '').toLowerCase();
            const f = String(filterText || l).toLowerCase();
            if (f.startsWith(prefixLower) || l.startsWith(prefixLower)) return 0;
            if (f.includes(prefixLower) || l.includes(prefixLower)) return 1;
            return 2;
          }}

          function _sortWeightText(value) {{
            const n = Math.max(0, Math.min(99, Number(value) || 0));
            return String(n).padStart(2, '0');
          }}

          function _toMonacoSuggestions(items, prefix) {{
            const src = _toArray(items);
            const out = [];
            const typedPrefix = String(prefix || '');
            const typedPrefixLower = typedPrefix.toLowerCase();
            const allowPrivate = typedPrefix.startsWith('_');
            function _appendSuggestion(item, includeDunder) {{
              const label = String((item && item.label) || '');
              if (!label) return;
              if (!includeDunder && !allowPrivate && label.startsWith('__')) {{
                return;
              }}
              const insertText = String((item && item.insertText) || label);
              const detail = String((item && item.detail) || '');
              const documentation = String((item && item.documentation) || '');
              const kind = _completionKind((item && item.kind) || 1);
              const entry = {{ label, insertText, detail, kind }};
              const sourceSortText = String((item && item.sortText) || label);
              const sourceFilterText = String((item && item.filterText) || label);
              const resolveKey = String((item && item.resolveKey) || '');
              const privacyRank = !allowPrivate && label.startsWith('_') ? 1 : 0;
              const prefixRank = _completionPrefixRank(label, sourceFilterText, typedPrefixLower);
              const kindRank = _completionKindRank(kind);
              const rankText = _sortWeightText(privacyRank) + _sortWeightText(prefixRank) + _sortWeightText(kindRank);
              entry.sortText = rankText + ':' + sourceSortText;
              entry.filterText = sourceFilterText;
              if (resolveKey) {{
                entry._f8ResolveKey = resolveKey;
              }}
              if (documentation) {{
                entry.documentation = _asMarkdownDoc(documentation);
              }}
              out.push(entry);
            }}
            for (const item of src) {{
              _appendSuggestion(item, false);
            }}
            if (!allowPrivate && out.length === 0 && src.length > 0) {{
              for (const item of src) {{
                _appendSuggestion(item, true);
              }}
            }}
            return out;
          }}

          function _toMonacoSignatureHelp(payload) {{
            if (!payload || typeof payload !== 'object') return null;
            const signaturesRaw = _toArray(payload.signatures);
            if (!signaturesRaw.length) return null;
            const parsedSignatures = [];
            for (const signatureItem of signaturesRaw) {{
              const signatureLabel = String((signatureItem && signatureItem.label) || '');
              if (!signatureLabel) continue;
              const parsed = {{
                label: signatureLabel,
                documentation: String((signatureItem && signatureItem.documentation) || '').trim(),
                parameters: [],
              }};
              const paramsRaw = _toArray(signatureItem && signatureItem.parameters);
              for (const paramItem of paramsRaw) {{
                const paramLabelRaw = paramItem && paramItem.label;
                let paramLabel = '';
                if (typeof paramLabelRaw === 'string') {{
                  paramLabel = String(paramLabelRaw || '').trim();
                }} else if (Array.isArray(paramLabelRaw) && paramLabelRaw.length === 2) {{
                  const start = Number(paramLabelRaw[0]);
                  const end = Number(paramLabelRaw[1]);
                  if (Number.isFinite(start) && Number.isFinite(end) && end >= start) {{
                    paramLabel = [Math.max(0, Math.floor(start)), Math.max(0, Math.floor(end))];
                  }}
                }}
                if (!paramLabel || (Array.isArray(paramLabel) && paramLabel.length !== 2)) continue;
                parsed.parameters.push({{
                  label: paramLabel,
                  documentation: String((paramItem && paramItem.documentation) || '').trim(),
                }});
              }}
              parsedSignatures.push(parsed);
            }}
            if (!parsedSignatures.length) return null;

            const activeSignatureRaw = Number(payload.activeSignature);
            const activeParameterRaw = Number(payload.activeParameter);
            const activeSignatureInitial = Number.isFinite(activeSignatureRaw)
              ? Math.max(0, Math.min(Math.floor(activeSignatureRaw), parsedSignatures.length - 1))
              : 0;
            const activeParamsInitial = parsedSignatures[activeSignatureInitial].parameters || [];
            const activeParameterInitial = Number.isFinite(activeParameterRaw) && activeParamsInitial.length > 0
              ? Math.max(0, Math.min(Math.floor(activeParameterRaw), activeParamsInitial.length - 1))
              : 0;

            const ordered = [];
            ordered.push({{ signature: parsedSignatures[activeSignatureInitial], sourceIndex: activeSignatureInitial }});
            for (let i = 0; i < parsedSignatures.length; i += 1) {{
              if (i === activeSignatureInitial) continue;
              ordered.push({{ signature: parsedSignatures[i], sourceIndex: i }});
            }}

            function _paramLabelText(signatureLabel, paramLabel) {{
              if (typeof paramLabel === 'string') {{
                return String(paramLabel || '').trim();
              }}
              if (!Array.isArray(paramLabel) || paramLabel.length !== 2) {{
                return '';
              }}
              const start = Math.max(0, Number(paramLabel[0]) || 0);
              const end = Math.max(start, Number(paramLabel[1]) || start);
              const extracted = String(signatureLabel || '').slice(start, end).trim();
              return extracted;
            }}

            function _signatureDocMarkdown(signatureObj, options) {{
              const overloadIdx = Number((options && options.overloadIdx) || 0);
              const overloadTotal = Number((options && options.overloadTotal) || 0);
              const activeParamIdx = Number((options && options.activeParamIdx) || -1);
              const blocks = [];
              if (overloadTotal > 1) {{
                blocks.push('**Overload ' + String(overloadIdx + 1) + '/' + String(overloadTotal) + '**');
              }}
              if (signatureObj.documentation) {{
                blocks.push(signatureObj.documentation);
              }}
              const paramRows = [];
              const signatureParams = _toArray(signatureObj.parameters);
              for (let i = 0; i < signatureParams.length; i += 1) {{
                const p = signatureParams[i];
                const labelText = _paramLabelText(signatureObj.label, p.label);
                if (!labelText) continue;
                const nameMd = '`' + labelText.replace(/`/g, '\\`') + '`';
                const isActive = activeParamIdx >= 0 && i === activeParamIdx;
                const head = isActive ? '- **' + nameMd + '**' : '- ' + nameMd;
                const pdoc = String((p && p.documentation) || '').trim();
                paramRows.push(pdoc ? head + ': ' + pdoc : head);
              }}
              if (paramRows.length) {{
                blocks.push('**Parameters**\\n' + paramRows.join('\\n'));
              }}
              return blocks.join('\\n\\n').trim();
            }}

            const signaturesOut = [];
            for (let i = 0; i < ordered.length; i += 1) {{
              const orderedItem = ordered[i];
              const sig = orderedItem.signature;
              const isActiveSig = i === 0;
              const activeParamForDoc = isActiveSig ? activeParameterInitial : -1;
              const docMarkdown = _signatureDocMarkdown(
                sig,
                {{
                  overloadIdx: Number(orderedItem.sourceIndex || 0),
                  overloadTotal: parsedSignatures.length,
                  activeParamIdx: activeParamForDoc,
                }},
              );
              const outSig = {{
                label: sig.label,
                parameters: sig.parameters,
              }};
              if (docMarkdown) {{
                outSig.documentation = {{ value: docMarkdown }};
              }}
              signaturesOut.push(outSig);
            }}
            return {{
              value: {{
                signatures: signaturesOut,
                activeSignature: 0,
                activeParameter: activeParameterInitial,
              }},
              dispose: function() {{}},
            }};
          }}

          let syncTimer = null;
          function _syncNow() {{
            if (!window._f8_editor || !syncDocument) return;
            const code = String(window._f8_editor.getValue() || '');
            syncDocument(code);
          }}
          function _scheduleSync() {{
            if (syncTimer !== null) {{
              clearTimeout(syncTimer);
            }}
            syncTimer = setTimeout(function() {{
              syncTimer = null;
              _syncNow();
            }}, 140);
          }}
          _syncNow();

          if (completionSignal && completionSignal.connect) completionSignal.connect(function(requestId, items) {{
            const id = String(requestId || '');
            const pending = window._f8_pendingCompletions[id];
            if (!pending) return;
            delete window._f8_pendingCompletions[id];
            const resolver = typeof pending === 'function' ? pending : pending.resolve;
            if (!resolver) return;
            const prefix = typeof pending === 'object' ? String(pending.prefix || '') : '';
            const cacheKey = typeof pending === 'object' ? String(pending.cacheKey || '') : '';
            const decoded = _decodeJson(items);
            const rawItems = _toArray(decoded);
            if (cacheKey) {{
              window._f8_completionCache = {{ cacheKey: cacheKey, prefix: prefix, items: rawItems }};
            }}
            const out = _toMonacoSuggestions(rawItems, prefix);
            _log('completion signal id=' + id + ' raw=' + String(rawItems.length) + ' items=' + String(out.length));
            resolver({{ suggestions: out }});
          }});

          if (completionResolveSignal && completionResolveSignal.connect) completionResolveSignal.connect(function(requestId, payload) {{
            const id = String(requestId || '');
            const resolver = window._f8_pendingCompletionResolves[id];
            if (!resolver) return;
            delete window._f8_pendingCompletionResolves[id];
            const decoded = _decodeJson(payload);
            _log('completion resolve signal id=' + id + ' hasResult=' + String(Boolean(decoded)));
            resolver(decoded);
          }});

          if (hoverSignal && hoverSignal.connect) hoverSignal.connect(function(requestId, payload) {{
            const id = String(requestId || '');
            const resolver = window._f8_pendingHovers[id];
            if (!resolver) return;
            delete window._f8_pendingHovers[id];
            resolver(_decodeJson(payload) || null);
          }});

          if (signatureHelpSignal && signatureHelpSignal.connect) signatureHelpSignal.connect(function(requestId, payload) {{
            const id = String(requestId || '');
            const resolver = window._f8_pendingSignatures[id];
            if (!resolver) return;
            delete window._f8_pendingSignatures[id];
            const decoded = _decodeJson(payload);
            const result = _toMonacoSignatureHelp(decoded);
            _log('signatureHelp signal id=' + id + ' hasResult=' + String(Boolean(result)));
            resolver(result);
          }});

          if (diagnosticsSignal && diagnosticsSignal.connect) diagnosticsSignal.connect(function(markers) {{
            if (!window._f8_editor) return;
            const model = window._f8_editor.getModel();
            if (!model) return;
            const payload = _toArray(markers);
            monaco.editor.setModelMarkers(model, 'f8-python-lsp', payload);
          }});

          monaco.languages.registerCompletionItemProvider('python', {{
            triggerCharacters: ['.', '_'],
            provideCompletionItems: function(model, position, context) {{
              return new Promise(function(resolve) {{
                try {{
                  const code = model.getValue();
                  const line = Number(position.lineNumber);
                  const col = Number(position.column - 1);
                  const prefix = _currentPrefix(model, position);
                  const cacheKey = _completionCacheKey(model, position, prefix);
                  const cached = window._f8_completionCache;
                  if (cached && cached.cacheKey === cacheKey && prefix.startsWith(String(cached.prefix || ''))) {{
                    const cachedOut = _toMonacoSuggestions(cached.items, prefix);
                    _log('completion cache items=' + String(cachedOut.length) + ' prefix=' + prefix);
                    resolve({{ suggestions: cachedOut }});
                    return;
                  }}
                  const triggerCharacter = context && typeof context.triggerCharacter === 'string'
                    ? String(context.triggerCharacter || '')
                    : '';
                  const forceSuggest = Boolean(window._f8_forceSuggestOnce);
                  window._f8_forceSuggestOnce = false;
                  if (!forceSuggest && !triggerCharacter && prefix.length <= 1) {{
                    resolve({{ suggestions: [] }});
                    return;
                  }}
                  if (requestCompletions) {{
                    const id = String(crypto.randomUUID ? crypto.randomUUID() : Math.random());
                    window._f8_pendingCompletions[id] = {{ resolve, prefix, cacheKey }};
                    requestCompletions(id, code, line, col);
                    setTimeout(function() {{
                      if (window._f8_pendingCompletions[id]) {{
                        delete window._f8_pendingCompletions[id];
                        resolve({{ suggestions: [] }});
                      }}
                    }}, 2500);
                    return;
                  }}
                  resolve({{ suggestions: [] }});
                }} catch (e) {{
                  resolve({{ suggestions: [] }});
                }}
              }});
            }},
            resolveCompletionItem: function(item) {{
              return new Promise(function(resolve) {{
                try {{
                  if (!requestCompletionResolve) {{
                    resolve(item);
                    return;
                  }}
                  const resolveKey = String((item && item._f8ResolveKey) || '');
                  if (!resolveKey) {{
                    resolve(item);
                    return;
                  }}
                  const id = String(crypto.randomUUID ? crypto.randomUUID() : Math.random());
                  window._f8_pendingCompletionResolves[id] = function(payload) {{
                    try {{
                      if (payload && typeof payload === 'object') {{
                        const detail = String(payload.detail || '');
                        const documentation = String(payload.documentation || '');
                        const insertText = String(payload.insertText || '');
                        if (detail) item.detail = detail;
                        if (documentation) item.documentation = _asMarkdownDoc(documentation);
                        if (insertText) item.insertText = insertText;
                        _log('completion resolve apply key=' + resolveKey + ' detailLen=' + String(detail.length) + ' docLen=' + String(documentation.length));
                      }}
                    }} catch (e) {{
                    }}
                    resolve(item);
                  }};
                  requestCompletionResolve(id, resolveKey);
                  setTimeout(function() {{
                    if (window._f8_pendingCompletionResolves[id]) {{
                      delete window._f8_pendingCompletionResolves[id];
                      resolve(item);
                    }}
                  }}, 1800);
                }} catch (e) {{
                  resolve(item);
                }}
              }});
            }},
          }});

          monaco.languages.registerHoverProvider('python', {{
            provideHover: function(model, position) {{
              return new Promise(function(resolve) {{
                try {{
                  const code = model.getValue();
                  const line = Number(position.lineNumber);
                  const col = Number(position.column - 1);
                  if (requestHover) {{
                    const id = String(crypto.randomUUID ? crypto.randomUUID() : Math.random());
                    window._f8_pendingHovers[id] = resolve;
                    requestHover(id, code, line, col);
                    setTimeout(function() {{
                      if (window._f8_pendingHovers[id]) {{
                        delete window._f8_pendingHovers[id];
                        resolve(null);
                      }}
                    }}, 2500);
                    return;
                  }}
                  resolve(null);
                }} catch (e) {{
                  resolve(null);
                }}
              }});
            }}
          }});

          monaco.languages.registerSignatureHelpProvider('python', {{
            signatureHelpTriggerCharacters: ['(', ','],
            signatureHelpRetriggerCharacters: [','],
            provideSignatureHelp: function(model, position) {{
              return new Promise(function(resolve) {{
                try {{
                  if (!requestSignatureHelp) {{
                    resolve(null);
                    return;
                  }}
                  const code = model.getValue();
                  const line = Number(position.lineNumber);
                  const col = Number(position.column - 1);
                  const id = String(crypto.randomUUID ? crypto.randomUUID() : Math.random());
                  window._f8_pendingSignatures[id] = resolve;
                  requestSignatureHelp(id, code, line, col);
                  setTimeout(function() {{
                    if (window._f8_pendingSignatures[id]) {{
                      delete window._f8_pendingSignatures[id];
                      resolve(null);
                    }}
                  }}, 2500);
                }} catch (e) {{
                  resolve(null);
                }}
              }});
            }},
          }});

          window._f8_editor.onDidChangeModelContent(function() {{
            window._f8_completionCache = null;
            _scheduleSync();
          }});
          window._f8_editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Space, function() {{
            try {{
              window._f8_forceSuggestOnce = true;
              window._f8_editor.trigger('keyboard', 'editor.action.triggerSuggest', {{}});
            }} catch (e) {{
            }}
          }});
          window._f8_editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyJ, function() {{
            try {{
              window._f8_forceSuggestOnce = true;
              window._f8_editor.trigger('keyboard', 'editor.action.triggerSuggest', {{}});
            }} catch (e) {{
            }}
          }});
          window._f8_editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.Space, function() {{
            try {{
              window._f8_editor.trigger('keyboard', 'editor.action.triggerParameterHints', {{}});
            }} catch (e) {{
            }}
          }});
          window._f8_editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.KeyJ, function() {{
            try {{
              window._f8_editor.trigger('keyboard', 'editor.action.triggerParameterHints', {{}});
            }} catch (e) {{
            }}
          }});
        }}

        if (typeof QWebChannel !== 'undefined' && window.qt && qt.webChannelTransport) {{
          new QWebChannel(qt.webChannelTransport, function(channel) {{
            window._f8_editorUi = channel.objects.f8EditorUi || null;
            window._f8_aiAssist = channel.objects.aiAssist || null;
            window._f8_notifyDirty();
            _setupPythonAssist(channel);
            _setupAiAssist(channel);
          }});
        }}
      }});
    </script>

    <!-- AI Chat Panel Styles -->
    <style>
      #f8-ai-panel.f8-drag-over {{
        border: 2px dashed #cba6f7 !important;
        background: rgba(203, 166, 247, 0.05);
      }}
      #f8-ai-panel {{
        position: fixed;
        top: 0; right: 0; bottom: 0;
        width: 320px;
        min-width: 200px;
        max-width: 800px;
        background: #1e1e2e;
        border-left: 1px solid #313244;
        display: flex;
        flex-direction: column;
        font-family: 'Segoe UI', system-ui, sans-serif;
        font-size: 13px;
        color: #cdd6f4;
        z-index: 100;
        transform: translateX(100%);
        transition: transform 0.2s ease;
        box-shadow: -4px 0 16px rgba(0,0,0,0.4);
      }}
      #f8-ai-resizer {{
        position: absolute;
        top: 0; left: -4px; bottom: 0;
        width: 8px;
        cursor: ew-resize;
        z-index: 110;
      }}
      #f8-ai-resizer:hover {{
        background: rgba(137, 180, 250, 0.2);
      }}
      #f8-ai-panel.open {{ transform: translateX(0); }}
      #f8-ai-toggle {{
        position: fixed;
        top: 8px; right: 8px;
        z-index: 200;
        background: #313244;
        border: none;
        border-radius: 4px;
        color: #cba6f7;
        font-size: 18px;
        width: 32px; height: 32px;
        cursor: pointer;
        display: flex; align-items: center; justify-content: center;
        transition: background 0.15s;
      }}
      #f8-ai-toggle:hover {{ background: #45475a; }}
      #f8-ai-mode-bar {{
        display: flex;
        gap: 4px;
        padding: 8px 48px 8px 8px;
        border-bottom: 1px solid #313244;
        background: #181825;
      }}
      .f8-mode-btn {{
        flex: 1;
        padding: 4px;
        border: 1px solid #45475a;
        border-radius: 4px;
        background: transparent;
        color: #a6adc8;
        font-size: 11px;
        cursor: pointer;
        transition: all 0.15s;
      }}
      .f8-mode-btn.active {{
        background: #313244;
        color: #cba6f7;
        border-color: #cba6f7;
      }}
      .f8-mode-btn:hover:not(.active) {{ background: #313244; color: #cdd6f4; }}
      .f8-new-chat {{
        border: 1px solid #45475a;
        border-radius: 4px;
        background: #313244;
        color: #a6adc8;
        font-size: 16px;
        width: 32px; height: 32px;
        cursor: pointer;
        transition: background 0.15s;
        display: flex; align-items: center; justify-content: center;
      }}
      .f8-new-chat:hover {{ background: #45475a; color: #cdd6f4; }}
      #f8-ai-messages {{
        flex: 1;
        overflow-y: auto;
        padding: 8px;
        display: flex;
        flex-direction: column;
        gap: 8px;
      }}
      .f8-msg {{ padding: 8px 10px; border-radius: 6px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }}
      .f8-msg.user {{ background: #313244; align-self: flex-end; max-width: 85%; }}
      .f8-msg.assistant {{ background: #1e1e2e; border: 1px solid #313244; align-self: flex-start; max-width: 100%; }}
      .f8-think-content {{ white-space: pre-wrap; }}
      .f8-msg pre {{
        background: #11111b;
        border: 1px solid #45475a;
        border-radius: 4px;
        padding: 8px;
        overflow-x: auto;
        position: relative;
        margin: 6px 0;
      }}
      .f8-msg code {{ font-family: 'Fira Code', 'Cascadia Code', monospace; font-size: 12px; }}
      .f8-copy-btn {{
        position: absolute; top: 4px; right: 4px;
        background: #45475a; border: none; border-radius: 3px;
        color: #cdd6f4; font-size: 10px; padding: 2px 6px;
        cursor: pointer; opacity: 0; transition: opacity 0.15s;
      }}
      .f8-msg pre:hover .f8-copy-btn {{ opacity: 1; }}
      .f8-copy-btn:hover {{ background: #585b70; }}
      .f8-diff-bar {{
        display: none;
        padding: 6px 8px;
        background: #181825;
        border-top: 1px solid #313244;
        gap: 6px;
      }}
      .f8-diff-bar.visible {{ display: flex; }}
      .f8-diff-accept {{ background: #a6e3a1; color: #1e1e2e; border: none; border-radius: 4px; padding: 4px 12px; cursor: pointer; font-weight: bold; }}
      .f8-diff-reject {{ background: #f38ba8; color: #1e1e2e; border: none; border-radius: 4px; padding: 4px 12px; cursor: pointer; font-weight: bold; }}
      #f8-ai-input-area {{
        padding: 12px;
        background: #181825;
        border-top: 1px solid #313244;
      }}
      .f8-input-wrapper {{
        background: #313244;
        border: 1px solid #45475a;
        border-radius: 8px;
        display: flex;
        flex-direction: column;
        transition: border-color 0.2s, box-shadow 0.2s;
      }}
      .f8-input-wrapper:focus-within {{
        border-color: #cba6f7;
        box-shadow: 0 0 0 2px rgba(203, 166, 247, 0.1);
      }}
      #f8-ai-input {{
        width: 100%;
        background: transparent;
        border: none;
        color: #cdd6f4;
        padding: 12px 12px 4px 12px;
        font-size: 13px;
        resize: none;
        min-height: 24px;
        max-height: 300px;
        outline: none;
        font-family: inherit;
        box-sizing: border-box;
        overflow-y: auto;
      }}
      #f8-ai-input::-webkit-scrollbar {{
        width: 6px;
      }}
      #f8-ai-input::-webkit-scrollbar-track {{
        background: transparent;
      }}
      #f8-ai-input::-webkit-scrollbar-thumb {{
        background: #45475a;
        border-radius: 3px;
      }}
      #f8-ai-input::-webkit-scrollbar-thumb:hover {{
        background: #585b70;
      }}
      .f8-input-toolbar {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 4px 8px 8px 8px;
      }}
      .f8-toolbar-left {{
        display: flex;
        gap: 4px;
      }}
      #f8-ai-send {{
        background: #cba6f7;
        border: none;
        border-radius: 6px;
        color: #1e1e2e;
        width: 28px; height: 28px;
        cursor: pointer;
        transition: transform 0.1s, background 0.1s;
        display: flex; align-items: center; justify-content: center;
        padding: 0;
      }}
      #f8-ai-send svg {{ width: 20px; height: 20px; stroke-width: 2; }}
      #f8-ai-send:hover {{ background: #b4befe; transform: scale(1.05); }}
      #f8-ai-send:active {{ transform: scale(0.95); }}
      #f8-ai-attach-btn svg, .f8-new-chat svg {{ width: 18px; height: 18px; stroke-width: 1.5; }}
      #f8-ai-send:hover {{ background: #d0bcff; }}
      #f8-ai-thinking {{
        display: none;
        padding: 4px 8px;
        color: #6c7086;
        font-size: 11px;
        font-style: italic;
      }}
      #f8-ai-thinking.visible {{ display: block; }}
      #f8-ai-attachments {{
        display: none;
        padding: 8px;
        gap: 8px;
        overflow-x: auto;
        background: #181825;
      }}
      #f8-ai-attachments.visible {{ display: flex; }}
      .f8-att-thumb {{
        position: relative;
        width: 48px; height: 48px;
        border-radius: 4px;
        border: 1px solid #45475a;
        flex-shrink: 0;
        background-size: cover;
        background-position: center;
      }}
      .f8-att-remove {{
        position: absolute; top: -4px; right: -4px;
        background: #f38ba8; color: #1e1e2e;
        border-radius: 50%; width: 14px; height: 14px;
        font-size: 10px; display: flex; align-items: center; justify-content: center;
        cursor: pointer; font-weight: bold;
        box-shadow: 0 1px 4px rgba(0,0,0,0.5);
      }}
      #f8-ai-attach-btn, .f8-new-chat {{
        background: transparent;
        border: none;
        border-radius: 4px;
        color: #9399b2;
        font-size: 14px;
        width: 24px; height: 24px;
        cursor: pointer;
        display: flex; align-items: center; justify-content: center;
        transition: background 0.1s, color 0.1s;
      }}
      #f8-ai-attach-btn:hover, .f8-new-chat:hover {{ 
        background: #45475a; 
        color: #cdd6f4; 
      }}
      .f8-plan-confirm {{
        margin-top: 8px;
        padding: 6px 12px;
        background: #89b4fa;
        color: #1e1e2e;
        border: none;
        border-radius: 4px;
        cursor: pointer;
        font-weight: bold;
        font-size: 12px;
      }}
      .f8-plan-confirm:hover {{ background: #74c7ec; }}
      #f8-diff-container {{
        display: none;
        position: fixed;
        top: 0; left: 0; right: 0; bottom: 0;
        z-index: 50;
        background: #1e1e1e;
      }}
      .f8-think {{
        margin: 8px 0;
        border-left: 2px solid #585b70;
        padding-left: 10px;
      }}
      .f8-think summary {{
        color: #a6adc8;
        cursor: pointer;
        font-size: 11px;
        user-select: none;
        font-style: italic;
      }}
      .f8-think summary:hover {{ color: #cdd6f4; }}
      .f8-think-content {{
        color: #9399b2;
        font-size: 12px;
        margin-top: 6px;
        white-space: pre-wrap;
      }}
    </style>

    <!-- AI JS Layer -->
    <script>
      window._f8_aiAssist = null;
      window._f8_aiMode = 'chat'; // 'chat' | 'edit' | 'plan'
      window._f8_chatMessages = [];
      window._f8_diffEditor = null;
      window._f8_diffOriginalCode = '';
      window._f8_inlinePending = Object.create(null);
      window._f8_inlineDebounceTimer = null;

      // ---- simple markdown → HTML (no deps) ----
      function _f8_md(text) {{
        let html = String(text || '');
        
        // think blocks: <think>...</think>
        // If streaming and unclosed, keep open. When closed, collapse it.
        html = html.replace(/<think>([\\s\\S]*?)(<\\/think>|$)/g, function(_, content, end_tag) {{
          const isOpen = end_tag ? '' : ' open';
          // Make sure code blocks inside <think> aren't destroyed
          return '<details class="f8-think"' + isOpen + '><summary>🤔 Thinking Process</summary><div class="f8-think-content">' + content + '</div></details>';
        }});

        // code blocks
        html = html.replace(/```(\\w*)\\n?([\\s\\S]*?)```/g, function(_, lang, code) {{
          const escaped = code.replace(/</g,'&lt;').replace(/>/g,'&gt;');
          return '<pre><button class="f8-copy-btn" onclick="_f8_copy(this)">copy</button><code class="language-' + (lang||'') + '">' + escaped + '</code></pre>';
        }});
        // inline code
        html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
        // bold
        html = html.replace(/\\*\\*(.+?)\\*\\*/g, '<b>$1</b>');
        // italic
        html = html.replace(/\\*(.+?)\\*/g, '<i>$1</i>');
        // headings
        html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
        html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
        html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');
        
        return html;
      }}

      function _f8_copy(btn) {{
        try {{
          const pre = btn.closest('pre');
          const codeEl = pre ? pre.querySelector('code') : null;
          const text = codeEl ? codeEl.textContent : '';
          if (!text) return;

          function done() {{
            btn.textContent = '✓';
            setTimeout(function() {{ btn.textContent = 'copy'; }}, 1500);
          }}

          // Try Python bridge first (most reliable in Qt)
          if (window._f8_aiAssist && window._f8_aiAssist.copy_to_clipboard) {{
            window._f8_aiAssist.copy_to_clipboard(text);
            done();
            return;
          }}

          // Fallback to modern navigator.clipboard
          if (navigator.clipboard && navigator.clipboard.writeText) {{
            navigator.clipboard.writeText(text).then(done).catch(function() {{
                if (_f8_fallbackCopy(text)) done();
            }});
          }} else {{
            if (_f8_fallbackCopy(text)) done();
          }}
        }} catch(e) {{}}
      }}

      function _f8_fallbackCopy(val) {{
        try {{
          const textArea = document.createElement("textarea");
          textArea.value = val;
          textArea.style.position = "fixed";
          textArea.style.left = "-9999px";
          textArea.style.top = "0";
          document.body.appendChild(textArea);
          textArea.focus();
          textArea.select();
          const success = document.execCommand('copy');
          document.body.removeChild(textArea);
          return success;
        }} catch (err) {{ return false; }}
      }}

      function _f8_setupAiPanel() {{
        const panel = document.getElementById('f8-ai-panel');
        const toggle = document.getElementById('f8-ai-toggle');
        const sendBtn = document.getElementById('f8-ai-send');
        const input = document.getElementById('f8-ai-input');
        const modeBtns = document.querySelectorAll('.f8-mode-btn');
        const diffBar = document.getElementById('f8-diff-bar');
        const acceptBtn = document.getElementById('f8-diff-accept');
        const rejectBtn = document.getElementById('f8-diff-reject');
        const thinking = document.getElementById('f8-ai-thinking');
        const attachments = document.getElementById('f8-ai-attachments');

        if (!panel || !toggle) return;

        // Restore saved states
        if (window._f8_aiAssist && window._f8_aiAssist.get_ui_state) {{
          const savedOpen = window._f8_aiAssist.get_ui_state('ai_panel_open', false);
          const savedWidth = window._f8_aiAssist.get_ui_state('ai_panel_width', 320);
          if (savedOpen) panel.classList.add('open');
          panel.style.width = savedWidth + 'px';
        }}

        // Drag and Drop support
        panel.addEventListener('dragover', (e) => {{
          e.preventDefault();
          panel.classList.add('f8-drag-over');
        }});
        panel.addEventListener('dragleave', () => {{
          panel.classList.remove('f8-drag-over');
        }});
        panel.addEventListener('drop', (e) => {{
          e.preventDefault();
          panel.classList.remove('f8-drag-over');
          const files = e.dataTransfer.files;
          if (files && files.length) {{
            for (let i = 0; i < files.length; i++) {{
              const file = files[i];
              if (file.type.startsWith('image/')) {{
                _f8_handleImageFile(file, file.type);
              }}
            }}
          }}
        }});

        toggle.addEventListener('click', function() {{
          const isOpen = panel.classList.toggle('open');
          if (window._f8_aiAssist && window._f8_aiAssist.set_ui_state) {{
            window._f8_aiAssist.set_ui_state('ai_panel_open', isOpen);
          }}
        }});

        modeBtns.forEach(function(btn) {{
          btn.addEventListener('click', function() {{
            modeBtns.forEach(function(b) {{ b.classList.remove('active'); }});
            btn.classList.add('active');
            window._f8_aiMode = btn.dataset.mode;
            if (window._f8_aiMode !== 'edit') {{
              _f8_closeDiff();
            }}
          }});
        }});

        if (input) {{
          input.addEventListener('keydown', function(e) {{
            if (e.key === 'Enter' && !e.shiftKey) {{
              e.preventDefault();
              _f8_sendMessage();
            }}
          }});
          input.addEventListener('input', function() {{
            input.style.height = 'auto';
            input.style.height = Math.min(input.scrollHeight, 300) + 'px';
          }});
          input.addEventListener('paste', function(e) {{
            _f8_handlePaste(e);
          }});
        }}

        // Global paste listener for when focusing might be slightly off
        document.addEventListener('paste', function(e) {{
          if (panel.classList.contains('open') && e.target !== input) {{
            _f8_handlePaste(e);
          }}
        }});
        if (sendBtn) sendBtn.addEventListener('click', _f8_sendMessage);
        const attachBtn = document.getElementById('f8-ai-attach-btn');
        if (attachBtn) {{
          attachBtn.addEventListener('click', function() {{
            if (window._f8_aiAssist && window._f8_aiAssist.select_images) {{
              window._f8_aiAssist.select_images(function(results) {{
                if (results && results.length) {{
                  _f8_addAttachments(results);
                }}
              }});
            }}
          }});
        }}
        if (acceptBtn) acceptBtn.addEventListener('click', _f8_acceptDiff);
        if (rejectBtn) rejectBtn.addEventListener('click', _f8_closeDiff);
        
        const newChatBtn = document.querySelector('.f8-new-chat');
        if (newChatBtn) newChatBtn.addEventListener('click', _f8_newConversation);

        _f8_setupAiResizer();
      }}

      function _f8_setupAiResizer() {{
        const panel = document.getElementById('f8-ai-panel');
        const resizer = document.getElementById('f8-ai-resizer');
        if (!panel || !resizer) return;

        let isResizing = false;
        let startX = 0;
        let startWidth = 0;

        resizer.addEventListener('mousedown', function(e) {{
          isResizing = true;
          startX = e.clientX;
          startWidth = panel.offsetWidth;
          panel.style.transition = 'none';
          document.body.style.cursor = 'ew-resize';
          document.addEventListener('mousemove', _onMouseMove);
          document.addEventListener('mouseup', _onMouseUp);
          e.preventDefault();
        }});

        function _onMouseMove(e) {{
          if (!isResizing) return;
          const delta = startX - e.clientX;
          const newWidth = Math.min(800, Math.max(200, startWidth + delta));
          panel.style.width = newWidth + 'px';
        }}

        function _onMouseUp() {{
          isResizing = false;
          panel.style.transition = 'transform 0.3s ease';
          document.body.style.cursor = 'default';
          document.removeEventListener('mousemove', _onMouseMove);
          document.removeEventListener('mouseup', _onMouseUp);

          // Save width
          if (window._f8_aiAssist && window._f8_aiAssist.set_ui_state) {{
            window._f8_aiAssist.set_ui_state('ai_panel_width', panel.offsetWidth);
          }}
        }}
      }}

      function _f8_newConversation() {{
        const msgs = document.getElementById('f8-ai-messages');
        if (msgs) msgs.innerHTML = '';
        window._f8_chatMessages = [];
        if (window._f8_aiAssist && window._f8_aiAssist.reset_chat_history) {{
          window._f8_aiAssist.reset_chat_history();
        }}
        _f8_appendMessage('assistant', 'Chat reset. Context cleared.');
        _f8_clearAttachments();
      }}

      function _f8_sendMessage() {{
        const input = document.getElementById('f8-ai-input');
        const text = input ? input.value.trim() : '';
        if (!text && window._f8_attachments.length === 0 || !window._f8_aiAssist) return;
        input.value = '';
        input.style.height = 'auto';

        const code = window._f8_getValue ? window._f8_getValue() : '';
        const selection = window._f8_editor ? (window._f8_editor.getModel().getValueInRange(window._f8_editor.getSelection()) || '') : '';

        // Notify Python of updated context
        if (window._f8_aiAssist.update_code_context) window._f8_aiAssist.update_code_context(code);

        _f8_appendMessage('user', text);

        const rid = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()));
        const thinking = document.getElementById('f8-ai-thinking');

        if (window._f8_aiMode === 'chat') {{
          window._f8_chatMessages.push({{role: 'user', content: text, attachments: window._f8_attachments}});
          if (thinking) thinking.classList.add('visible');
          const assistantEl = _f8_appendMessage('assistant', '');
          window._f8_aiAssist.chat_chunk_ready.connect(function(id, delta) {{
            if (id !== rid) return;
            const cur = assistantEl.dataset.raw || '';
            assistantEl.dataset.raw = cur + delta;
            assistantEl.innerHTML = _f8_md(assistantEl.dataset.raw);
            if (window.Prism) {{ try {{ Prism.highlightAllUnder(assistantEl); }} catch(e) {{}} }}
            _f8_scrollBottom();
          }});
          window._f8_aiAssist.chat_done.connect(function(id, err) {{
            if (id !== rid) return;
            if (thinking) thinking.classList.remove('visible');
            if (err) _f8_appendMessage('assistant', '⚠ ' + err);
            else window._f8_chatMessages.push({{role: 'assistant', content: assistantEl.dataset.raw || ''}});
            if (window._f8_aiAssist.update_chat_context) {{
              window._f8_aiAssist.update_chat_context(JSON.stringify(window._f8_chatMessages));
            }}
          }});
          window._f8_aiAssist.request_chat(rid, JSON.stringify(window._f8_chatMessages), code, selection, JSON.stringify(window._f8_attachments || []));
          _f8_clearAttachments();

        }} else if (window._f8_aiMode === 'edit') {{
          if (thinking) thinking.classList.add('visible');
          _f8_appendMessage('assistant', 'Generating edit…');
          window._f8_aiAssist.edit_result_ready.connect(function(id, newCode, err) {{
            if (id !== rid) return;
            if (thinking) thinking.classList.remove('visible');
            if (err) {{ _f8_appendMessage('assistant', '⚠ ' + err); return; }}
            _f8_showDiff(newCode);
          }});
          window._f8_aiAssist.request_edit(rid, code, text, JSON.stringify(window._f8_chatMessages), JSON.stringify(window._f8_attachments || []));
          _f8_clearAttachments();

        }} else if (window._f8_aiMode === 'plan') {{
          window._f8_chatMessages.push({{role: 'user', content: text, attachments: window._f8_attachments}});
          if (thinking) thinking.classList.add('visible');
          const assistantEl = _f8_appendMessage('assistant', '');
          window._f8_aiAssist.plan_step_ready.connect(function(id, delta) {{
            if (id !== rid) return;
            const cur = assistantEl.dataset.raw || '';
            assistantEl.dataset.raw = cur + delta;
            assistantEl.innerHTML = _f8_md(assistantEl.dataset.raw);
            if (window.Prism) {{ try {{ Prism.highlightAllUnder(assistantEl); }} catch(e) {{}} }}
            _f8_scrollBottom();
          }});
          window._f8_aiAssist.plan_done.connect(function(id, err) {{
            if (id !== rid) return;
            if (thinking) thinking.classList.remove('visible');
            if (!err) {{
              const confirmBtn = document.createElement('button');
              confirmBtn.className = 'f8-plan-confirm';
              confirmBtn.textContent = 'Confirm & Execute';
              confirmBtn.addEventListener('click', function() {{
                window._f8_aiMode = 'edit';
                document.querySelectorAll('.f8-mode-btn').forEach(function(b) {{
                  b.classList.toggle('active', b.dataset.mode === 'edit');
                }});
                const input = document.getElementById('f8-ai-input');
                if (input) input.value = "Implement the plan we just discussed.";
                _f8_sendMessage();
              }});
              assistantEl.appendChild(confirmBtn);
            }}
          }});
          window._f8_aiAssist.request_plan(rid, text, code, JSON.stringify(window._f8_chatMessages), JSON.stringify(window._f8_attachments || []));
          _f8_clearAttachments();
        }}
      }}

      window._f8_attachments = [];
      function _f8_addAttachments(newAtts) {{
        newAtts.forEach(att => {{
          window._f8_attachments.push(att);
        }});
        _f8_renderAttachments();
      }}
      function _f8_removeAttachment(idx) {{
        window._f8_attachments.splice(idx, 1);
        _f8_renderAttachments();
      }}
      function _f8_clearAttachments() {{
        window._f8_attachments = [];
        _f8_renderAttachments();
      }}
      function _f8_renderAttachments() {{
        const container = document.getElementById('f8-ai-attachments');
        if (!container) return;
        container.innerHTML = '';
        if (window._f8_attachments.length > 0) {{
          container.classList.add('visible');
          window._f8_attachments.forEach((att, idx) => {{
            const thumb = document.createElement('div');
            thumb.className = 'f8-att-thumb';
            thumb.style.backgroundImage = 'url(data:' + att.mime + ';base64,' + att.content + ')';
            thumb.title = att.name;
            const rm = document.createElement('div');
            rm.className = 'f8-att-remove';
            rm.textContent = '×';
            rm.onclick = (e) => {{
              e.stopPropagation();
              _f8_removeAttachment(idx);
            }};
            thumb.appendChild(rm);
            container.appendChild(thumb);
          }});
        }} else {{
          container.classList.remove('visible');
        }}
      }}

      function _f8_handleImageFile(file, mimeType) {{
        const reader = new FileReader();
        reader.onload = function(event) {{
          const base64 = event.target.result.split(',')[1];
          _f8_addAttachments([{{
            name: file.name || "pasted_image.png",
            content: base64,
            mime: mimeType || "image/png"
          }}]);
        }};
        reader.readAsDataURL(file);
      }}

      function _f8_handlePaste(e) {{
        const clipboardData = e.clipboardData || window.clipboardData;
        if (!clipboardData) return;

        let foundInJs = false;
        const items = clipboardData.items;
        if (items) {{
          for (let i = 0; i < items.length; i++) {{
            const item = items[i];
            if (item.type.indexOf('image') !== -1) {{
              const blob = item.getAsFile();
              if (blob) {{
                _f8_handleImageFile(blob, item.type);
                foundInJs = true;
              }}
            }} else if (item.type === 'text/html') {{
              item.getAsString(function(html) {{
                // Try many flavors of data-urls
                const matches = html.matchAll(/src="([^"]+)"/gi);
                for (const match of matches) {{
                  const src = match[1];
                  if (src.startsWith('data:image/')) {{
                     const p = src.split(',');
                     if (p.length > 1) {{
                       const m = p[0].split(':')[1].split(';')[0];
                       _f8_addAttachments([{{ name: "web_snippet.png", content: p[1], mime: m }}]);
                       foundInJs = true;
                     }}
                  }}
                }}
              }});
            }}
          }}
        }}

        // Native fallback (much more powerful for "Copy Image" from web)
        if (!foundInJs && window._f8_aiAssist && window._f8_aiAssist.get_clipboard_image) {{
          window._f8_aiAssist.get_clipboard_image(function(res) {{
            if (res && res.content) {{
              _f8_addAttachments([res]);
            }}
          }});
        }}
      }}

      function _f8_appendMessage(role, text) {{
        const msgs = document.getElementById('f8-ai-messages');
        if (!msgs) return document.createElement('div');
        const div = document.createElement('div');
        div.className = 'f8-msg ' + role;
        div.dataset.raw = text;
        div.innerHTML = role === 'assistant' ? _f8_md(text) : _f8_escHtml(text);
        if (role === 'assistant' && window.Prism) {{
          try {{ Prism.highlightAllUnder(div); }} catch(e) {{}}
        }}
        msgs.appendChild(div);
        _f8_scrollBottom();
        return div;
      }}

      function _f8_escHtml(s) {{
        return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      }}

      function _f8_scrollBottom() {{
        const msgs = document.getElementById('f8-ai-messages');
        if (msgs) msgs.scrollTop = msgs.scrollHeight;
      }}

      function _f8_showDiff(newCode) {{
        // Save original code for potential reject
        window._f8_diffOriginalCode = window._f8_getValue ? window._f8_getValue() : '';
        // Use the OVERLAY container, never touching #container / the live editor
        const overlay = document.getElementById('f8-diff-container');
        if (!overlay) return;
        overlay.style.display = 'block';
        // Dispose any previous diff editor
        if (window._f8_diffEditor) {{
          window._f8_diffEditor.dispose();
          window._f8_diffEditor = null;
        }}
        window._f8_diffEditor = monaco.editor.createDiffEditor(overlay, {{
          theme: 'vs-dark',
          automaticLayout: true,
          readOnly: false,
          renderSideBySide: false,
          originalEditable: false,
        }});
        window._f8_diffEditor.setModel({{
          original: monaco.editor.createModel(window._f8_diffOriginalCode, 'python'),
          modified: monaco.editor.createModel(newCode, 'python'),
        }});
        const diffBar = document.getElementById('f8-diff-bar');
        if (diffBar) {{
            diffBar.classList.add('visible');
            const hint = diffBar.querySelector('.f8-diff-hint') || document.createElement('span');
            hint.className = 'f8-diff-hint';
            hint.textContent = 'Review changes';
            hint.style.marginRight = 'auto';
            hint.style.color = '#888';
            if (!diffBar.contains(hint)) diffBar.insertBefore(hint, diffBar.firstChild);
        }}

        window._f8_hunkWidgets = [];
        window._f8_diffEditor.onDidUpdateDiff(function() {{
            const modEditor = window._f8_diffEditor.getModifiedEditor();
            window._f8_hunkWidgets.forEach(function(w) {{
                modEditor.removeContentWidget(w);
            }});
            window._f8_hunkWidgets = [];

            const changes = window._f8_diffEditor.getLineChanges();
            if (!changes) return;
            if (changes.length === 0) {{
               setTimeout(_f8_acceptDiff, 100);
               return;
            }}

            changes.forEach(function(change, index) {{
                if (change.originalEndLineNumber === 0 && change.modifiedEndLineNumber === 0) return;

                const widgetId = 'hunk-widget-' + index + '-' + Date.now();
                const domNode = document.createElement('div');
                domNode.className = 'f8-hunk-actions';
                
                const acceptBtn = document.createElement('button');
                acceptBtn.className = 'f8-hunk-btn f8-accept';
                acceptBtn.innerHTML = '✓';
                acceptBtn.title = 'Accept this chunk';
                acceptBtn.onclick = function() {{ _f8_acceptHunk(change); }};
                
                const rejectBtn = document.createElement('button');
                rejectBtn.className = 'f8-hunk-btn f8-reject';
                rejectBtn.innerHTML = '✗';
                rejectBtn.title = 'Reject this chunk';
                rejectBtn.onclick = function() {{ _f8_rejectHunk(change); }};
                
                domNode.appendChild(acceptBtn);
                domNode.appendChild(rejectBtn);

                let targetLine = Math.max(1, change.modifiedStartLineNumber || change.modifiedEndLineNumber);

                const widget = {{
                    getId: function() {{ return widgetId; }},
                    getDomNode: function() {{ return domNode; }},
                    getPosition: function() {{
                        return {{
                            position: {{ lineNumber: targetLine, column: 1 }},
                            preference: [monaco.editor.ContentWidgetPositionPreference.BELOW, monaco.editor.ContentWidgetPositionPreference.ABOVE]
                        }};
                    }}
                }};
                
                modEditor.addContentWidget(widget);
                window._f8_hunkWidgets.push(widget);
            }});
        }});
      }}

      function _f8_acceptDiff() {{
        if (!window._f8_diffEditor) return;
        const modifiedModel = window._f8_diffEditor.getModifiedEditor().getModel();
        const newCode = modifiedModel ? modifiedModel.getValue() : '';
        // Apply as a single undoable edit on the LIVE editor (never disposed)
        if (window._f8_editor && newCode) {{
          const model = window._f8_editor.getModel();
          if (model) {{
            const fullRange = model.getFullModelRange();
            // pushEditOperations adds to the undo stack → Ctrl+Z works
            model.pushEditOperations(
              [],
              [{{ range: fullRange, text: newCode }}],
              function() {{ return null; }}
            );
          }}
        }}
        _f8_closeDiff();
        window._f8_notifyDirty();
      }}

      function _f8_closeDiff() {{
        if (window._f8_diffEditor) {{
          window._f8_diffEditor.dispose();
          window._f8_diffEditor = null;
        }}
        const overlay = document.getElementById('f8-diff-container');
        if (overlay) overlay.style.display = 'none';
        const diffBar = document.getElementById('f8-diff-bar');
        if (diffBar) diffBar.classList.remove('visible');
        // Return focus to the live editor
        if (window._f8_editor) {{
          try {{ window._f8_editor.focus(); }} catch(e) {{}}
        }}
      }}

      function _f8_acceptHunk(change) {{
          if (!window._f8_diffEditor) return;
          const origModel = window._f8_diffEditor.getOriginalEditor().getModel();
          const modModel = window._f8_diffEditor.getModifiedEditor().getModel();
          if (!origModel || !modModel) return;

          let modText = '';
          if (change.modifiedEndLineNumber > 0) {{
              let lines = [];
              for(let i = change.modifiedStartLineNumber; i <= change.modifiedEndLineNumber; i++) {{
                  lines.push(modModel.getLineContent(i));
              }}
              modText = lines.join(modModel.getEOL());
          }}

          let range;
          let origStart = change.originalStartLineNumber;
          let origEnd = change.originalEndLineNumber;
          if (change.modifiedEndLineNumber === 0) {{
              if (origEnd < origModel.getLineCount()) {{
                  origEnd += 1;
                  range = new monaco.Range(origStart, 1, origEnd, 1);
              }} else if (origStart > 1) {{
                  const prevMax = origModel.getLineMaxColumn(origStart - 1);
                  range = new monaco.Range(origStart - 1, prevMax, origEnd, origModel.getLineMaxColumn(origEnd));
              }} else {{
                  range = new monaco.Range(origStart, 1, origEnd, origModel.getLineMaxColumn(origEnd));
              }}
          }} else if (change.originalEndLineNumber === 0) {{
              if (change.originalStartLineNumber === 0) {{
                   range = new monaco.Range(1, 1, 1, 1);
                   modText = modText + origModel.getEOL();
              }} else {{
                   range = new monaco.Range(change.originalStartLineNumber, origModel.getLineMaxColumn(change.originalStartLineNumber), change.originalStartLineNumber, origModel.getLineMaxColumn(change.originalStartLineNumber));
                   modText = origModel.getEOL() + modText;
              }}
          }} else {{
              range = new monaco.Range(origStart, 1, origEnd, origModel.getLineMaxColumn(origEnd));
          }}
          origModel.pushEditOperations([], [{{range: range, text: modText}}], () => null);
      }}

      function _f8_rejectHunk(change) {{
          if (!window._f8_diffEditor) return;
          const origModel = window._f8_diffEditor.getOriginalEditor().getModel();
          const modModel = window._f8_diffEditor.getModifiedEditor().getModel();
          if (!origModel || !modModel) return;

          let origText = '';
          if (change.originalEndLineNumber > 0) {{
              let lines = [];
              for(let i = change.originalStartLineNumber; i <= change.originalEndLineNumber; i++) {{
                  lines.push(origModel.getLineContent(i));
              }}
              origText = lines.join(origModel.getEOL());
          }}

          let range;
          let modStart = change.modifiedStartLineNumber;
          let modEnd = change.modifiedEndLineNumber;
          if (change.originalEndLineNumber === 0) {{
              if (modEnd < modModel.getLineCount()) {{
                  modEnd += 1;
                  range = new monaco.Range(modStart, 1, modEnd, 1);
              }} else if (modStart > 1) {{
                  const prevMax = modModel.getLineMaxColumn(modStart - 1);
                  range = new monaco.Range(modStart - 1, prevMax, modEnd, modModel.getLineMaxColumn(modEnd));
              }} else {{
                  range = new monaco.Range(modStart, 1, modEnd, modModel.getLineMaxColumn(modEnd));
              }}
          }} else if (change.modifiedEndLineNumber === 0) {{
              if (change.modifiedStartLineNumber === 0) {{
                   range = new monaco.Range(1, 1, 1, 1);
                   origText = origText + modModel.getEOL();
              }} else {{
                   range = new monaco.Range(change.modifiedStartLineNumber, modModel.getLineMaxColumn(change.modifiedStartLineNumber), change.modifiedStartLineNumber, modModel.getLineMaxColumn(change.modifiedStartLineNumber));
                   origText = modModel.getEOL() + origText;
              }}
          }} else {{
              range = new monaco.Range(modStart, 1, modEnd, modModel.getLineMaxColumn(modEnd));
          }}
          modModel.pushEditOperations([], [{{range: range, text: origText}}], () => null);
      }}

      // ---- Inline AI Suggestions (FIM) ----
      function _f8_setupInlineSuggestions() {{
        if (typeof monaco === 'undefined') return;
        const lang = String((window.__F8_INITIAL__ || {{}}).language || '');
        monaco.languages.registerInlineCompletionsProvider(lang || 'python', {{
          provideInlineCompletions: function(model, position, context, token) {{
            return new Promise(function(resolve) {{
              if (!window._f8_aiAssist) {{ resolve({{ items: [] }}); return; }}
              if (window._f8_inlineDebounceTimer !== null) {{
                clearTimeout(window._f8_inlineDebounceTimer);
              }}
              window._f8_inlineDebounceTimer = setTimeout(function() {{
                window._f8_inlineDebounceTimer = null;
                const fullText = model.getValue();
                const offset = model.getOffsetAt(position);
                const prefix = fullText.slice(Math.max(0, offset - 2000), offset);
                const suffix = fullText.slice(offset, Math.min(fullText.length, offset + 500));
                const rid = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()));
                window._f8_inlinePending[rid] = resolve;
                setTimeout(function() {{
                  if (window._f8_inlinePending[rid]) {{
                    delete window._f8_inlinePending[rid];
                    resolve({{ items: [] }});
                  }}
                }}, 8000);
                window._f8_aiAssist.request_inline_suggestion(rid, prefix, suffix, Number(position.lineNumber), Number(position.column));
              }}, 650);
              token.onCancellationRequested(function() {{
                if (window._f8_inlineDebounceTimer !== null) {{
                  clearTimeout(window._f8_inlineDebounceTimer);
                  window._f8_inlineDebounceTimer = null;
                }}
                resolve({{ items: [] }});
              }});
            }});
          }},
          freeInlineCompletions: function() {{}},
          disposeInlineCompletions: function() {{}}
        }});
      }}

      function _setupAiAssist(channel) {{
        const aiAssist = channel.objects.aiAssist || null;
        window._f8_aiAssist = aiAssist;
        if (!aiAssist) return;

        // Wire inline suggestion results
        if (aiAssist.inline_suggestion_ready && aiAssist.inline_suggestion_ready.connect) {{
          aiAssist.inline_suggestion_ready.connect(function(rid, text) {{
            const resolver = window._f8_inlinePending[rid];
            if (!resolver) return;
            delete window._f8_inlinePending[rid];
            if (!text) {{ resolver({{ items: [] }}); return; }}
            resolver({{ items: [{{ insertText: text, range: null }}] }});
          }});
        }}

        _f8_setupInlineSuggestions();
        _f8_setupAiPanel();
      }}
    </script>
  </head>
  <body>
    <div id="container"></div>
    <!-- Diff overlay: separate div keeps #container/live editor alive -->
    <div id="f8-diff-container"></div>

    <!-- AI Toggle Button (overlaid on monaco) -->
    <button id="f8-ai-toggle" title="Open AI Chat">✦</button>

    <!-- AI Chat/Edit/Plan Panel -->
    <div id="f8-ai-panel">
      <div id="f8-ai-resizer"></div>
      <div id="f8-ai-mode-bar">
        <button class="f8-mode-btn active" data-mode="chat">💬 Chat</button>
        <button class="f8-mode-btn" data-mode="edit">✏ Edit</button>
        <button class="f8-mode-btn" data-mode="plan">🗺 Plan</button>
      </div>
      <div id="f8-ai-messages"></div>
      <div id="f8-ai-thinking">AI is thinking…</div>
      <div id="f8-ai-attachments"></div>
      <div class="f8-diff-bar" id="f8-diff-bar">
        <button class="f8-diff-accept" id="f8-diff-accept">✓</button>
        <button class="f8-diff-reject" id="f8-diff-reject">✗</button>
        <span style="color:#6c7086;font-size:11px;margin-left:6px;"></span>
      </div>
      <div id="f8-ai-input-area">
        <div class="f8-input-wrapper">
          <textarea id="f8-ai-input" placeholder="Ask AI… (Enter to send, Shift+Enter for newline)" rows="1"></textarea>
          <div class="f8-input-toolbar">
            <div class="f8-toolbar-left">
              <button id="f8-ai-attach-btn" title="Attach Images">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M15 7l-6.5 6.5a1.5 1.5 0 0 0 3 3l6.5 -6.5a3 3 0 0 0 -6 -6l-6.5 6.5a4.5 4.5 0 0 0 9 9l6.5 -6.5" /></svg>
              </button>
              <button class="f8-new-chat" title="New Conversation / Clear Context">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M4.05 11a8 8 0 1 1 .5 4m-.5 5v-5h5" /></svg>
              </button>
            </div>
            <button id="f8-ai-send" title="Send (Enter)">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0 -18" /><path d="M16 12l-4 -4" /><path d="M16 12h-8" /><path d="M16 12l-4 4" /></svg>
            </button>
          </div>
        </div>
      </div>
    </div>
  </body>
</html>
"""
        self._view.setHtml(html)

    def _start_assist_context_sync(self) -> None:
        if self._assist_bridge is None:
            return
        if self._assist_context_provider is None:
            return
        poll_timer = QtCore.QTimer(self)
        poll_timer.setInterval(320)
        poll_timer.timeout.connect(self._poll_assist_context_change)  # type: ignore[attr-defined]
        self._assist_reload_poll_timer = poll_timer

        debounce_timer = QtCore.QTimer(self)
        debounce_timer.setSingleShot(True)
        debounce_timer.setInterval(480)
        debounce_timer.timeout.connect(self._apply_assist_context_reload)  # type: ignore[attr-defined]
        self._assist_reload_debounce_timer = debounce_timer

        poll_timer.start()

    def _stop_assist_context_sync(self) -> None:
        poll_timer = self._assist_reload_poll_timer
        if poll_timer is not None:
            poll_timer.stop()
            self._assist_reload_poll_timer = None
        debounce_timer = self._assist_reload_debounce_timer
        if debounce_timer is not None:
            debounce_timer.stop()
            self._assist_reload_debounce_timer = None
        self._assist_pending_context = None
        self._assist_pending_fingerprint = ""

    def _poll_assist_context_change(self) -> None:
        if self._assist_bridge is None:
            return
        provider = self._assist_context_provider
        if provider is None:
            return
        try:
            context = provider()
        except Exception as exc:
            self._log_assist_context_error("providerRefresh", exc)
            return
        fingerprint = _assist_context_fingerprint(context)
        if fingerprint == self._assist_context_fingerprint:
            self._assist_pending_context = None
            self._assist_pending_fingerprint = ""
            return
        self._assist_pending_context = context
        self._assist_pending_fingerprint = fingerprint
        debounce_timer = self._assist_reload_debounce_timer
        if debounce_timer is None:
            self._apply_assist_context_reload()
            return
        debounce_timer.start()

    @QtCore.Slot()
    def _apply_assist_context_reload(self) -> None:
        bridge = self._assist_bridge
        if bridge is None:
            return
        fingerprint = str(self._assist_pending_fingerprint or "")
        if not fingerprint:
            return
        if fingerprint == self._assist_context_fingerprint:
            return
        context = self._assist_pending_context
        self._assist_pending_context = None
        self._assist_pending_fingerprint = ""
        if not bridge.reload_context(context):
            logger.warning("Failed to reload python editor assist context")
            return
        self._assist_context_fingerprint = fingerprint
        logger.debug("python editor assist context reloaded")

    def _log_assist_context_error(self, operation: str, exc: Exception) -> None:
        sig = f"{operation}:{type(exc).__name__}:{exc}"
        now = time.monotonic()
        if sig == self._assist_error_sig and (now - self._assist_error_ts) < 5.0:
            return
        self._assist_error_sig = sig
        self._assist_error_ts = now
        logger.exception("Failed to refresh python editor assist context; operation=%s", operation)

    def _on_save_clicked(self) -> None:
        if not self._dirty:
            return
        self._save_current(close_after=self._close_on_save)

    def _save_current(self, *, close_after: bool) -> None:
        try:
            page = self._view.page()
        except Exception:
            page = None
        if page is None:
            if close_after:
                self.accept()
            return

        def _on_value(value: Any) -> None:
            try:
                self._code = "" if value is None else str(value)
            except Exception:
                self._code = ""
            self._set_dirty(False)
            self.code_saved.emit(self._code)
            if close_after:
                self.accept()

        try:
            page.runJavaScript("window._f8_getValue && window._f8_getValue();", _on_value)  # type: ignore[call-arg]
            page.runJavaScript("window._f8_markSaved && window._f8_markSaved();")
        except (AttributeError, RuntimeError, TypeError):
            pass

    def _shutdown_assist(self) -> None:
        self._stop_assist_context_sync()
        bridge = self._assist_bridge
        if bridge is None:
            return
        self._assist_bridge = None
        try:
            bridge.shutdown()
        except Exception:
            logger.exception("Failed to shutdown Python editor assist bridge")

    # ------------------------------------------------------------------
    # AI panel slots
    # ------------------------------------------------------------------

    @QtCore.Slot(bool)
    def _on_ai_panel_toggle(self, checked: bool) -> None:
        self._ai_quick_panel.setVisible(checked)
        if checked:
            self._ai_quick_panel.raise_()
            self._reposition_ai_panel()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._reposition_ai_panel()

    def _reposition_ai_panel(self) -> None:
        if not hasattr(self, "_ai_quick_panel"):
            return
        # Position floating panel at bottom-left of the editor view
        rect = self._view.geometry()
        if rect.width() <= 0:
            return
        
        # We want it to be above the bottom bar, anchored to left
        self._ai_quick_panel.adjustSize()
        pw = self._ai_quick_panel.width()
        ph = self._ai_quick_panel.height()
        
        margin = 10
        x = rect.x() + margin
        y = rect.y() + rect.height() - ph - margin
        
        self._ai_quick_panel.move(x, y)

    @QtCore.Slot(int, int)
    def _on_context_usage_updated(self, used: int, total: int) -> None:
        if total > 0:
            used_ratio = max(0.0, min(1.0, used / total))
            free_ratio = max(0.0, 1.0 - used_ratio)
            if used_ratio < 0.5:
                color = "#4fc3f7"
            elif used_ratio < 0.8:
                color = "#ffd54f"
            else:
                color = "#ef9a9a"

            def _fmt(n: int) -> str:
                return f"{n / 1000:.0f}k" if n >= 1000 else str(n)

            free_pct = int(round(free_ratio * 100.0))
            self._ctx_btn.setIcon(_usage_pie_icon(used_ratio=used_ratio, color=QtGui.QColor(color)))
            self._ctx_btn.setText(f"{free_pct}% free")
            _set_tool_button_point_size(self._ctx_btn, 10)
            self._ctx_btn.setStyleSheet(
                f"QToolButton {{ color: {color}; border: none; padding: 0 4px; }}"
                "QToolButton:hover { color: white; }"
            )
            try:
                breakdown = self._ai_bridge.get_context_breakdown()
                tip = (
                    "AI Context Usage\n"
                    f"System: {breakdown['system_tokens']} tok\n"
                    f"Code: {breakdown['code_tokens']} tok\n"
                    f"Chat: {breakdown['chat_tokens']} tok\n"
                    f"Free: {free_pct}%\n"
                    f"Used: {breakdown['used_tokens']} / {breakdown['total_tokens']} tok"
                )
                self._ctx_btn.setToolTip(tip)
            except Exception:
                pass

    def _open_full_ai_config(self) -> None:
        from .ai_provider_config_dialog import AiProviderConfigDialog
        dlg = AiProviderConfigDialog(self._ai_store, self)
        dlg.exec()

    @QtCore.Slot(bool)
    def _on_dirty_changed(self, dirty: bool) -> None:
        self._set_dirty(bool(dirty))

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = bool(dirty)
        self._save_button.setEnabled(self._dirty)

    def set_close_on_save(self, close_on_save: bool) -> None:
        self._close_on_save = bool(close_on_save)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        if not self._dirty:
            self._shutdown_assist()
            event.accept()
            return
        answer = _ask_save_before_close(self)
        if answer == QtWidgets.QMessageBox.StandardButton.Yes:
            self._save_current(close_after=True)
            event.ignore()
            return
        if answer == QtWidgets.QMessageBox.StandardButton.No:
            self._shutdown_assist()
            event.accept()
            return
        event.ignore()


def open_code_editor_dialog(
    parent: QtWidgets.QWidget | None,
    *,
    title: str,
    code: str,
    language: str,
    assist_context: EditorAssistContext | None = None,
    assist_context_provider: Callable[[], EditorAssistContext | None] | None = None,
) -> str | None:
    resolved_context = _resolve_assist_context(
        assist_context=assist_context,
        assist_context_provider=assist_context_provider,
    )
    effective_language = str(language or "plaintext").strip() or "plaintext"
    if _assist_context_requires_python(resolved_context):
        effective_language = "python"
    warn_text = _python_assist_warning(resolved_context)
    if effective_language.lower() == "python" and warn_text:
        show_warning(parent, "Python Assist Warning", warn_text)
    dlg = F8MonacoEditorDialog(
        parent,
        title=title,
        code=code,
        language=effective_language,
        assist_context=resolved_context,
        assist_context_provider=assist_context_provider,
    )
    if dlg.exec() != QtWidgets.QDialog.Accepted:
        return None
    return dlg.code()


def open_code_editor_window(
    parent: QtWidgets.QWidget | None,
    *,
    title: str,
    code: str,
    language: str,
    on_saved: Callable[[str], None],
    assist_context: EditorAssistContext | None = None,
    assist_context_provider: Callable[[], EditorAssistContext | None] | None = None,
) -> QtWidgets.QDialog:
    resolved_context = _resolve_assist_context(
        assist_context=assist_context,
        assist_context_provider=assist_context_provider,
    )
    effective_language = str(language or "plaintext").strip() or "plaintext"
    if _assist_context_requires_python(resolved_context):
        effective_language = "python"
    warn_text = _python_assist_warning(resolved_context)
    if effective_language.lower() == "python" and warn_text:
        show_warning(parent, "Python Assist Warning", warn_text)
    dlg = F8MonacoEditorDialog(
        None,
        title=title,
        code=code,
        language=effective_language,
        assist_context=resolved_context,
        assist_context_provider=assist_context_provider,
    )

    dlg.setModal(False)
    dlg.setWindowModality(QtCore.Qt.WindowModality.NonModal)
    dlg.setWindowFlag(QtCore.Qt.WindowType.Window, True)
    dlg.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
    dlg.set_close_on_save(False)
    dlg.code_saved.connect(on_saved)  # type: ignore[arg-type]

    if parent is not None:
        try:
            anchor = parent.window() if parent.window() is not None else parent
            center = anchor.frameGeometry().center()
            frame = dlg.frameGeometry()
            frame.moveCenter(center)
            dlg.move(frame.topLeft())
        except (AttributeError, RuntimeError, TypeError):
            pass

    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg

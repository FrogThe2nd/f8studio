from __future__ import annotations

import json
import logging
import math
import os
from typing import Any, Callable

from qtpy import QtCore, QtGui, QtWidgets

from ..editor_assist.bridge import PythonEditorAssistBridge
from ..editor_assist.workspace import EditorAssistContext
from ..ui_notifications import show_warning
from ..ui_icons import StudioIcon, icon_for

logger = logging.getLogger(__name__)


def _assist_context_requires_python(context: EditorAssistContext | None) -> bool:
    if context is None:
        return False
    mode = str(context.mode or "").strip().lower()
    return mode in {"f8.pyscript_service", "f8.pyengine_operator"}

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
    Monaco-based editor dialog (syntax highlighting, modern keybindings).

    Monaco assets can be loaded from:
    - `F8_MONACO_BASE_URL` (recommended for packaged/offline builds)
    - CDN fallback (default) for dev
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
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(str(title or "Edit Code"))
        self._code: str = str(code or "")
        self._dirty: bool = False
        self._close_on_save: bool = True
        self._language: str = str(language or "plaintext").strip() or "plaintext"

        from PySide6 import QtWebChannel, QtWebEngineWidgets  # type: ignore[import-not-found]

        self._view = QtWebEngineWidgets.QWebEngineView(self)
        self._view.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.NoContextMenu)
        self._ui_bridge = _EditorUiBridge(self)
        self._assist_bridge: PythonEditorAssistBridge | None = None
        self._web_channel: Any = QtWebChannel.QWebChannel(self._view.page())
        self._web_channel.registerObject("f8EditorUi", self._ui_bridge)
        if self._language.lower() == "python":
            self._assist_bridge = PythonEditorAssistBridge(
                code=self._code,
                language="python",
                context=assist_context,
                parent=self,
            )
            self._web_channel.registerObject("pyAssist", self._assist_bridge)
        self._view.page().setWebChannel(self._web_channel)

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
        self._close_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Esc"), self)
        self._close_shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._close_shortcut.activated.connect(self.close)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self._view, 1)
        layout.addWidget(buttons)

        self.resize(1020, 720)
        self._load_page()

    def code(self) -> str:
        return str(self._code or "")

    def _monaco_base_url(self) -> str:
        v = str(os.environ.get("F8_MONACO_BASE_URL") or "").strip().rstrip("/")
        if v:
            return v
        return "https://cdn.jsdelivr.net/npm/monaco-editor@0.50.0/min"

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
    </style>
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
      window._f8_pendingHovers = Object.create(null);
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
        window._f8_editor.addCommand(monaco.KeyCode.Escape, function() {{
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
          const hoverSignal = assist.hover_ready || assist.hoverReady || null;
          const diagnosticsSignal = assist.diagnostics_ready || assist.diagnosticsReady || null;
          const requestCompletions = assist.request_completions || assist.requestCompletions || null;
          const requestHover = assist.request_hover || assist.requestHover || null;
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
              const privacyRank = !allowPrivate && label.startsWith('_') ? 1 : 0;
              const prefixRank = _completionPrefixRank(label, sourceFilterText, typedPrefixLower);
              const kindRank = _completionKindRank(kind);
              const rankText = _sortWeightText(privacyRank) + _sortWeightText(prefixRank) + _sortWeightText(kindRank);
              entry.sortText = rankText + ':' + sourceSortText;
              entry.filterText = sourceFilterText;
              if (documentation) {{
                entry.documentation = documentation;
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

          if (hoverSignal && hoverSignal.connect) hoverSignal.connect(function(requestId, payload) {{
            const id = String(requestId || '');
            const resolver = window._f8_pendingHovers[id];
            if (!resolver) return;
            delete window._f8_pendingHovers[id];
            resolver(_decodeJson(payload) || null);
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
            }}
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
        }}

        if (typeof QWebChannel !== 'undefined' && window.qt && qt.webChannelTransport) {{
          new QWebChannel(qt.webChannelTransport, function(channel) {{
            window._f8_editorUi = channel.objects.f8EditorUi || null;
            window._f8_notifyDirty();
            _setupPythonAssist(channel);
          }});
        }}
      }});
    </script>
  </head>
  <body>
    <div id="container"></div>
  </body>
</html>
"""
        self._view.setHtml(html)

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
        bridge = self._assist_bridge
        if bridge is None:
            return
        self._assist_bridge = None
        try:
            bridge.shutdown()
        except Exception:
            logger.exception("Failed to shutdown Python editor assist bridge")

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
) -> str | None:
    """
    Open the Monaco code editor dialog and return updated code, or None if cancelled.
    """
    effective_language = str(language or "plaintext").strip() or "plaintext"
    if _assist_context_requires_python(assist_context):
        effective_language = "python"
    dlg = F8MonacoEditorDialog(
        parent,
        title=title,
        code=code,
        language=effective_language,
        assist_context=assist_context,
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
) -> QtWidgets.QDialog:
    dlg: QtWidgets.QDialog
    # Always create as a top-level window (no Qt parent) so it behaves as an
    # independent editor window in the OS window manager/task switcher.
    effective_language = str(language or "plaintext").strip() or "plaintext"
    if _assist_context_requires_python(assist_context):
        effective_language = "python"
    dlg = F8MonacoEditorDialog(
        None,
        title=title,
        code=code,
        language=effective_language,
        assist_context=assist_context,
    )

    dlg.setModal(False)
    dlg.setWindowModality(QtCore.Qt.WindowModality.NonModal)
    dlg.setWindowFlag(QtCore.Qt.WindowType.Window, True)
    dlg.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
    if isinstance(dlg, F8MonacoEditorDialog):
        dlg.set_close_on_save(False)
        dlg.code_saved.connect(on_saved)  # type: ignore[arg-type]

    # Best-effort initial placement near caller without making it a child window.
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


class F8CodePropWidget(QtWidgets.QWidget):
    """
    Read-only preview with an "Edit..." button that opens a code editor dialog.
    """

    value_changed = QtCore.Signal(str, object)

    def __init__(self, parent=None, *, title: str = "Edit Code"):
        super().__init__(parent)
        self._name = ""
        self._value = ""
        self._title = str(title or "Edit Code")
        self._assist_context: EditorAssistContext | None = None
        self._editor_window: QtWidgets.QDialog | None = None

        self._preview = QtWidgets.QLineEdit()
        self._preview.setReadOnly(True)
        self._preview.setClearButtonEnabled(False)

        self._btn = QtWidgets.QPushButton("Edit...")
        self._btn.clicked.connect(self._on_edit_clicked)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._preview, 1)
        layout.addWidget(self._btn, 0)

    def set_name(self, name: str) -> None:
        self._name = str(name or "")

    def get_name(self) -> str:
        return self._name

    def get_value(self) -> str:
        return str(self._value or "")

    def set_value(self, value: Any) -> None:
        self._value = str(value or "")
        lines = self._value.splitlines()
        n = len(lines)
        preview = f"{n} line" if n == 1 else f"{n} lines"
        if lines:
            head = lines[0].strip()
            if head:
                preview = f"{preview} - {head[:80]}"
        self._preview.setText(preview)

    def set_editor_assist_context(self, context: EditorAssistContext | None) -> None:
        self._assist_context = context
        if _assist_context_requires_python(context):
            self._language = "python"

    def _on_edit_clicked(self) -> None:
        if self._editor_window is not None:
            try:
                self._editor_window.raise_()
                self._editor_window.activateWindow()
                return
            except Exception:
                self._editor_window = None

        def _on_saved(updated: str) -> None:
            self.set_value(updated)
            self.value_changed.emit(self.get_name(), updated)

        dlg = open_code_editor_window(
            self,
            title=self._title,
            code=self.get_value(),
            language="python",
            on_saved=_on_saved,
            assist_context=self._assist_context,
        )
        self._editor_window = dlg
        dlg.destroyed.connect(self._on_editor_destroyed)  # type: ignore[attr-defined]

    @QtCore.Slot()
    def _on_editor_destroyed(self) -> None:
        self._editor_window = None


class F8CodeButtonPropWidget(QtWidgets.QWidget):
    """
    A single "Edit..." button that opens a code editor dialog.
    """

    value_changed = QtCore.Signal(str, object)

    def __init__(self, parent=None, *, title: str = "Edit Code", language: str = "python"):
        super().__init__(parent)
        self._name = ""
        self._value = ""
        self._title = str(title or "Edit Code")
        self._language = str(language or "plaintext").strip() or "plaintext"
        self._assist_context: EditorAssistContext | None = None
        self._editor_window: QtWidgets.QDialog | None = None

        self._btn = QtWidgets.QPushButton("Edit...")
        self._btn.setIcon(icon_for(self._btn, StudioIcon.CODE))
        self._btn.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self._btn.clicked.connect(self._on_edit_clicked)  # type: ignore[attr-defined]

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._btn, 1)

    def set_name(self, name: str) -> None:
        self._name = str(name or "")

    def get_name(self) -> str:
        return self._name

    def get_value(self) -> str:
        return str(self._value or "")

    def set_value(self, value: Any) -> None:
        self._value = str(value or "")

    def set_read_only(self, read_only: bool) -> None:
        self._btn.setEnabled(not bool(read_only))

    def set_editor_assist_context(self, context: EditorAssistContext | None) -> None:
        self._assist_context = context

    def _on_edit_clicked(self) -> None:
        if self._editor_window is not None:
            try:
                self._editor_window.raise_()
                self._editor_window.activateWindow()
                return
            except Exception:
                self._editor_window = None

        def _on_saved(updated: str) -> None:
            self.set_value(updated)
            self.value_changed.emit(self.get_name(), updated)

        dlg = open_code_editor_window(
            self,
            title=self._title,
            code=self.get_value(),
            language=self._language,
            on_saved=_on_saved,
            assist_context=self._assist_context,
        )
        self._editor_window = dlg
        dlg.destroyed.connect(self._on_editor_destroyed)  # type: ignore[attr-defined]

    @QtCore.Slot()
    def _on_editor_destroyed(self) -> None:
        self._editor_window = None


class F8InlineCodePropWidget(QtWidgets.QPlainTextEdit):
    """
    Inline multiline editor used for lightweight expressions (`uiControl=code_inline`).

    Emits `value_changed` on focus-out and on Ctrl+Enter.
    """

    value_changed = QtCore.Signal(str, object)

    def __init__(self, parent=None, *, language: str = "plaintext"):
        super().__init__(parent)
        self._name: str = ""
        self._prev_text: str = ""
        self._language = str(language or "plaintext").strip().lower() or "plaintext"

        self.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" "))
        try:
            font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
            self.setFont(font)
        except (AttributeError, RuntimeError, TypeError):
            pass
        self.setMinimumHeight(44)
        self.setMaximumHeight(96)

    def set_name(self, name: str) -> None:
        self._name = str(name or "")

    def get_name(self) -> str:
        return self._name

    def focusInEvent(self, event):  # type: ignore[override]
        super().focusInEvent(event)
        self._prev_text = self.toPlainText()

    def focusOutEvent(self, event):  # type: ignore[override]
        super().focusOutEvent(event)
        self._emit_if_changed()

    def keyPressEvent(self, event):  # type: ignore[override]
        try:
            if event.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter) and bool(
                event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier
            ):
                self._emit_if_changed(force=True)
                event.accept()
                return
        except (AttributeError, RuntimeError, TypeError):
            pass
        super().keyPressEvent(event)

    def _emit_if_changed(self, *, force: bool = False) -> None:
        text = str(self.toPlainText() or "")
        if not force and text == self._prev_text:
            return
        self._prev_text = text
        self.value_changed.emit(self.get_name(), text)

    def set_value(self, value: Any) -> None:
        with QtCore.QSignalBlocker(self):
            self.setPlainText("" if value is None else str(value))
        self._prev_text = self.toPlainText()


class F8WrapLinePropWidget(QtWidgets.QPlainTextEdit):
    """
    Single-line editor that wraps long text.

    Intended for short expressions that must not contain newlines, but can be
    visually wrapped to fit the node width.

    Emits `value_changed` on focus-out and on Enter/Ctrl+Enter.
    """

    value_changed = QtCore.Signal(str, object)

    def __init__(self, parent=None, *, language: str = "plaintext"):
        super().__init__(parent)
        self._name: str = ""
        self._prev_text: str = ""
        self._language = str(language or "plaintext").strip().lower() or "plaintext"

        self.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" "))
        try:
            font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
            self.setFont(font)
        except (AttributeError, RuntimeError, TypeError):
            pass
        self.document().setDocumentMargin(4.0)

        self.setMinimumHeight(38)
        self.setMaximumHeight(64)

    def set_name(self, name: str) -> None:
        self._name = str(name or "")

    def get_name(self) -> str:
        return self._name

    @staticmethod
    def _normalize(value: str) -> str:
        s = str(value or "")
        if "\n" not in s and "\r" not in s:
            return s
        parts = [p.strip() for p in s.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
        return " ".join([p for p in parts if p]).strip()

    def focusInEvent(self, event):  # type: ignore[override]
        super().focusInEvent(event)
        self._prev_text = str(self.toPlainText() or "")

    def focusOutEvent(self, event):  # type: ignore[override]
        super().focusOutEvent(event)
        self._emit_if_changed()

    def keyPressEvent(self, event):  # type: ignore[override]
        try:
            is_enter = event.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter)
            if is_enter:
                # Never insert newlines. Treat Enter as commit.
                self._emit_if_changed(force=True)
                try:
                    self.clearFocus()
                except RuntimeError:
                    pass
                event.accept()
                return
        except (AttributeError, RuntimeError, TypeError):
            pass
        super().keyPressEvent(event)

    def insertFromMimeData(self, source: QtCore.QMimeData) -> None:  # type: ignore[override]
        try:
            txt = ""
            if source is not None and source.hasText():
                txt = self._normalize(str(source.text() or ""))
            if txt:
                self.textCursor().insertText(txt)
            return
        except Exception:
            return super().insertFromMimeData(source)

    def _emit_if_changed(self, *, force: bool = False) -> None:
        text = self._normalize(str(self.toPlainText() or ""))
        if text != str(self.toPlainText() or ""):
            with QtCore.QSignalBlocker(self):
                self.setPlainText(text)
        if not force and text == self._prev_text:
            return
        self._prev_text = text
        self.value_changed.emit(self.get_name(), text)

    def set_value(self, value: Any) -> None:
        text = self._normalize("" if value is None else str(value))
        with QtCore.QSignalBlocker(self):
            self.setPlainText(text)
        self._prev_text = text


class F8JsonPropTextEdit(QtWidgets.QTextEdit):
    """
    QTextEdit property widget that round-trips JSON values as python objects.
    """

    value_changed = QtCore.Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._name: str | None = None
        self._prev_text = ""
        self._prev_value: Any = None

    def get_name(self) -> str:
        return self._name or ""

    def set_name(self, name: str) -> None:
        self._name = name

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._prev_text = self.toPlainText()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        if self._prev_text == self.toPlainText():
            return
        text = self.toPlainText().strip()
        if not text:
            self._prev_value = None
            self.value_changed.emit(self.get_name(), None)
            self._prev_text = ""
            return
        try:
            obj = json.loads(text)
        except Exception as e:
            show_warning(self, "Invalid JSON", str(e))
            self.setPlainText(self._prev_text)
            return
        self._prev_value = obj
        self.value_changed.emit(self.get_name(), obj)
        self._prev_text = text

    def get_value(self):
        return self._prev_value

    def set_value(self, value: Any) -> None:
        self._prev_value = value
        with QtCore.QSignalBlocker(self):
            if value is None:
                self.setPlainText("")
            else:
                self.setPlainText(json.dumps(value, ensure_ascii=False, indent=2))


class F8NumberPropLineEdit(QtWidgets.QLineEdit):
    """
    LineEdit that validates and emits int/float values.
    """

    value_changed = QtCore.Signal(str, object)
    value_changing = QtCore.Signal(str, object)

    def __init__(self, parent=None, *, data_type: type = float):
        super().__init__(parent)
        self._name = ""
        self._data_type = data_type
        self._min: float | None = None
        self._max: float | None = None
        self._scrub_enabled = True
        self._scrub_base_step: float | None = None
        self._scrub_active = False
        self._scrub_start_global_x = 0.0
        self._scrub_start_value = 0.0
        self._scrub_start_text = ""
        self._base_tooltip = ""
        self.setMinimumWidth(120)
        self._update_validator()
        self._refresh_tooltip()
        self.editingFinished.connect(self._emit_value)

    def set_name(self, name: str) -> None:
        self._name = str(name or "")

    def get_name(self) -> str:
        return self._name

    def set_min(self, v) -> None:
        try:
            self._min = float(v)
        except Exception:
            self._min = None
        self._update_validator()

    def set_max(self, v) -> None:
        try:
            self._max = float(v)
        except Exception:
            self._max = None
        self._update_validator()

    def _update_validator(self) -> None:
        if self._data_type is int:
            vmin = int(self._min) if self._min is not None else -(2**31)
            vmax = int(self._max) if self._max is not None else (2**31 - 1)
            self.setValidator(QtGui.QIntValidator(vmin, vmax, self))
            return
        vmin = float(self._min) if self._min is not None else -1.0e18
        vmax = float(self._max) if self._max is not None else 1.0e18
        dv = QtGui.QDoubleValidator(vmin, vmax, 6, self)
        try:
            dv.setNotation(QtGui.QDoubleValidator.Notation.StandardNotation)
        except (AttributeError, RuntimeError, TypeError):
            pass
        self.setValidator(dv)

    def set_scrub_enabled(self, enabled: bool) -> None:
        self._scrub_enabled = bool(enabled)
        self._refresh_tooltip()

    def set_scrub_base_step(self, step: float | None) -> None:
        if step is None:
            self._scrub_base_step = None
            return
        try:
            out = abs(float(step))
        except (TypeError, ValueError):
            self._scrub_base_step = None
            return
        if out <= 0.0:
            self._scrub_base_step = None
            return
        self._scrub_base_step = out

    def setToolTip(self, text: str) -> None:  # type: ignore[override]
        self._base_tooltip = str(text or "").strip()
        self._refresh_tooltip()

    def get_value(self):
        t = str(self.text() or "").strip()
        if t == "":
            return None
        try:
            v = float(t)
            if self._min is not None:
                v = max(v, self._min)
            if self._max is not None:
                v = min(v, self._max)
            if self._data_type is int:
                return int(round(v))
            return float(v)
        except Exception:
            return None

    def set_value(self, value) -> None:
        if value is None:
            with QtCore.QSignalBlocker(self):
                self.setText("")
            return
        with QtCore.QSignalBlocker(self):
            self.setText(str(value))

    def _emit_value(self) -> None:
        v = self.get_value()
        if v is None and str(self.text() or "").strip() != "":
            # invalid -> keep focus and don't emit.
            return
        self.value_changed.emit(self.get_name(), v)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        is_middle_drag = bool(event.button() == QtCore.Qt.MiddleButton)
        if is_middle_drag and self._scrub_enabled and self.isEnabled() and not self.isReadOnly():
            self._scrub_begin(event)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if self._scrub_active:
            self._scrub_update(event, commit=False)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if self._scrub_active and event.button() == QtCore.Qt.MiddleButton:
            self._scrub_update(event, commit=True)
            self._scrub_end()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:  # type: ignore[override]
        if self._scrub_active and event.key() == QtCore.Qt.Key_Escape:
            with QtCore.QSignalBlocker(self):
                self.setText(self._scrub_start_text)
            self._scrub_end()
            event.accept()
            return
        super().keyPressEvent(event)

    def _scrub_begin(self, event: QtGui.QMouseEvent) -> None:
        self._scrub_active = True
        self._scrub_start_global_x = float(event.globalPosition().x())
        self._scrub_start_text = str(self.text() or "")
        current = self.get_value()
        self._scrub_start_value = 0.0 if current is None else float(current)
        self.setCursor(QtCore.Qt.SizeHorCursor)
        self.grabMouse()
        self.setFocus(QtCore.Qt.MouseFocusReason)

    def _scrub_end(self) -> None:
        self._scrub_active = False
        self.unsetCursor()
        self.releaseMouse()

    def _scrub_update(self, event: QtGui.QMouseEvent, *, commit: bool) -> None:
        dx = float(event.globalPosition().x()) - self._scrub_start_global_x
        step = self._resolve_scrub_step()
        mult = self._resolve_scrub_multiplier(event.modifiers())
        candidate = self._scrub_start_value + dx * step * mult
        out = self._coerce_value(candidate)
        with QtCore.QSignalBlocker(self):
            self.setText(self._format_value(out))
        if commit:
            self.value_changed.emit(self.get_name(), out)
        else:
            self.value_changing.emit(self.get_name(), out)

    def _resolve_scrub_step(self) -> float:
        if self._scrub_base_step is not None:
            step = max(1e-12, float(self._scrub_base_step))
            if self._data_type is int:
                return max(1.0, step)
            return step
        magnitude = max(abs(float(self._scrub_start_value)), 1.0)
        exponent = math.floor(math.log10(magnitude))
        step = math.pow(10.0, float(exponent)) * 0.01
        if self._data_type is int:
            return max(1.0, step)
        return max(1e-12, step)

    @staticmethod
    def _resolve_scrub_multiplier(modifiers: QtCore.Qt.KeyboardModifiers) -> float:
        has_shift = bool(modifiers & QtCore.Qt.ShiftModifier)
        has_ctrl = bool(modifiers & QtCore.Qt.ControlModifier)
        if has_shift and has_ctrl:
            return 1.0
        if has_shift:
            return 0.1
        if has_ctrl:
            return 10.0
        return 1.0

    def _coerce_value(self, v: float) -> float | int:
        out = float(v)
        if self._min is not None and out < self._min:
            out = float(self._min)
        if self._max is not None and out > self._max:
            out = float(self._max)
        if self._data_type is int:
            return int(round(out))
        return float(out)

    def _format_value(self, v: float | int) -> str:
        if self._data_type is int:
            return str(int(v))
        return ("{:.6f}".format(float(v))).rstrip("0").rstrip(".")

    def _refresh_tooltip(self) -> None:
        hint = "Middle-Drag to scrub" if self._scrub_enabled else ""
        if self._base_tooltip and hint:
            text = f"{self._base_tooltip}\n{hint}"
        elif self._base_tooltip:
            text = self._base_tooltip
        else:
            text = hint
        super().setToolTip(text)


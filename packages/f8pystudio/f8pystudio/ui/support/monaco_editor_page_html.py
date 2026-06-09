"""Monaco page resource builder used by the hosted multi-session editor."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from qtpy import QtWidgets

from ...agents.graph_context import GraphContextSnapshot
from ...editor_assist.agent_context import EditorAgentContext
from ...editor_assist.agent_scope import EditorAgentScope
from ...editor_assist.session import EditorSessionKey
from ...editor_assist.workspace import EditorAssistContext

__all__ = [
    "MonacoEditorPageConfig",
    "build_monaco_editor_html",
    "open_code_editor_dialog",
    "open_code_editor_window",
]


@dataclass(frozen=True)
class MonacoEditorPageConfig:
    code: str
    language: str
    monaco_base_url: str
    python_assist_enabled: bool = False
    theme: str = "vs-dark"
    prism_asset_html: str = ""


def build_monaco_editor_html(config: MonacoEditorPageConfig) -> str:
    prism_asset_html_block = str(config.prism_asset_html or "").strip()
    if prism_asset_html_block:
        prism_asset_html_block += "\n"
    initial = {
        "code": str(config.code or ""),
        "language": str(config.language or "plaintext").strip() or "plaintext",
        "theme": str(config.theme or "vs-dark").strip() or "vs-dark",
        "pythonAssistEnabled": bool(config.python_assist_enabled),
    }
    template = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <style>
      html, body, #f8-root {
        height: 100%;
        width: 100%;
        margin: 0;
        padding: 0;
        overflow: hidden;
        background: #1e1e1e;
      }
      #f8-root {
        position: relative;
        height: 100%;
        width: 100%;
      }
      #container {
        position: absolute;
        inset: 0;
        background: #1e1e1e;
      }
    </style>
    __F8_PRISM_ASSET_HTML__    <script>
      window.__F8_INITIAL__ = __F8_INITIAL_JSON__;
    </script>
    <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
    <script src="vs/loader.js"></script>
    <script>
      window._f8_editor = null;
      window._f8_editorUi = null;
      window._f8_pyAssist = null;
      window._f8_pendingCompletions = Object.create(null);
      window._f8_pendingCompletionResolves = Object.create(null);
      window._f8_pendingHovers = Object.create(null);
      window._f8_pendingSignatures = Object.create(null);
      window._f8_completionResolveItems = Object.create(null);
      window._f8_completionCache = null;
      window._f8_forceSuggestOnce = false;
      window._f8_lastDirty = false;
      window._f8_savedValue = '';

      window._f8_getValue = function() {
        try {
          if (!window._f8_editor) return "";
          return window._f8_editor.getValue();
        } catch (e) {
          return "";
        }
      };

      window._f8_getSelection = function() {
        try {
          if (!window._f8_editor) return "";
          const model = window._f8_editor.getModel();
          const selection = window._f8_editor.getSelection();
          if (!model || !selection) return "";
          return model.getValueInRange(selection) || "";
        } catch (e) {
          return "";
        }
      };

      window._f8_isDirty = function() {
        try {
          if (!window._f8_editor) return false;
          return window._f8_editor.getValue() !== String(window._f8_savedValue || '');
        } catch (e) {
          return false;
        }
      };

      window._f8_notifyDirty = function() {
        try {
          const dirty = Boolean(window._f8_isDirty());
          if (dirty === window._f8_lastDirty) return;
          window._f8_lastDirty = dirty;
          if (window._f8_editorUi && window._f8_editorUi.notify_dirty) {
            window._f8_editorUi.notify_dirty(dirty);
          }
        } catch (e) {
        }
      };

      window._f8_markSaved = function() {
        try {
          window._f8_savedValue = window._f8_getValue();
          window._f8_lastDirty = false;
          if (window._f8_editorUi && window._f8_editorUi.notify_dirty) {
            window._f8_editorUi.notify_dirty(false);
          }
        } catch (e) {
        }
      };

      function _f8_decodeJson(value, fallbackValue) {
        if (typeof value !== 'string') return value === undefined ? fallbackValue : value;
        try {
          return JSON.parse(value);
        } catch (e) {
          return fallbackValue;
        }
      }

      function _f8_toArray(value) {
        if (Array.isArray(value)) return value;
        if (!value || typeof value !== 'object') return [];
        if (Array.isArray(value.items)) return value.items;
        return [];
      }

      function _f8_asMarkdown(value) {
        const text = String(value || '').trim();
        return text ? { value: text } : undefined;
      }

      function _f8_completionKind(kind) {
        const value = Number(kind);
        if (!Number.isFinite(value)) return monaco.languages.CompletionItemKind.Text;
        if (value < 1 || value > 25) return monaco.languages.CompletionItemKind.Text;
        return value;
      }

      function _f8_toSuggestions(items) {
        const suggestions = [];
        for (const item of _f8_toArray(items)) {
          const label = String((item && item.label) || '').trim();
          if (!label) continue;
          const entry = {
            label: label,
            insertText: String((item && item.insertText) || label),
            kind: _f8_completionKind(item && item.kind),
            detail: String((item && item.detail) || ''),
          };
          const documentation = String((item && item.documentation) || '').trim();
          if (documentation) entry.documentation = _f8_asMarkdown(documentation);
          const sortText = String((item && item.sortText) || '').trim();
          if (sortText) entry.sortText = sortText;
          const filterText = String((item && item.filterText) || '').trim();
          if (filterText) entry.filterText = filterText;
          const resolveKey = String((item && item.resolveKey) || '').trim();
          if (resolveKey) {
            entry._f8ResolveKey = resolveKey;
            window._f8_completionResolveItems[resolveKey] = entry;
          }
          suggestions.push(entry);
        }
        return suggestions;
      }

      function _f8_signatureHelp(payload) {
        if (!payload || typeof payload !== 'object') return null;
        const signatures = _f8_toArray(payload.signatures);
        if (!signatures.length) return null;
        return {
          value: {
            signatures: signatures,
            activeSignature: Number(payload.activeSignature || 0),
            activeParameter: Number(payload.activeParameter || 0)
          },
          dispose: function() {}
        };
      }

      function _f8_resolveEditorLanguage(language) {
        const normalized = String(language || 'plaintext').trim().toLowerCase() || 'plaintext';
        try {
          const languages = monaco.languages.getLanguages();
          for (const item of languages) {
            if (String((item && item.id) || '').toLowerCase() === normalized) return normalized;
          }
        } catch (e) {
        }
        return 'plaintext';
      }

      function _f8_setupPythonAssist(channel) {
        const init = window.__F8_INITIAL__ || {};
        if (String(init.language || '').toLowerCase() !== 'python') return;
        if (!Boolean(init.pythonAssistEnabled)) return;
        const assist = channel && channel.objects ? channel.objects.pyAssist : null;
        window._f8_pyAssist = assist || null;
        if (!assist || typeof monaco === 'undefined') return;

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

        if (completionSignal && completionSignal.connect) {
          completionSignal.connect(function(requestId, payloadJson) {
            const resolver = window._f8_pendingCompletions[String(requestId || '')];
            if (!resolver) return;
            delete window._f8_pendingCompletions[String(requestId || '')];
            resolver(_f8_toSuggestions(_f8_decodeJson(payloadJson, [])));
          });
        }

        if (completionResolveSignal && completionResolveSignal.connect) {
          completionResolveSignal.connect(function(requestId, payloadJson) {
            const resolver = window._f8_pendingCompletionResolves[String(requestId || '')];
            if (!resolver) return;
            delete window._f8_pendingCompletionResolves[String(requestId || '')];
            const payload = _f8_decodeJson(payloadJson, null);
            resolver(payload && typeof payload === 'object' ? payload : null);
          });
        }

        if (hoverSignal && hoverSignal.connect) {
          hoverSignal.connect(function(requestId, payloadJson) {
            const resolver = window._f8_pendingHovers[String(requestId || '')];
            if (!resolver) return;
            delete window._f8_pendingHovers[String(requestId || '')];
            resolver(_f8_decodeJson(payloadJson, null));
          });
        }

        if (signatureHelpSignal && signatureHelpSignal.connect) {
          signatureHelpSignal.connect(function(requestId, payloadJson) {
            const resolver = window._f8_pendingSignatures[String(requestId || '')];
            if (!resolver) return;
            delete window._f8_pendingSignatures[String(requestId || '')];
            resolver(_f8_signatureHelp(_f8_decodeJson(payloadJson, null)));
          });
        }

        if (diagnosticsSignal && diagnosticsSignal.connect) {
          diagnosticsSignal.connect(function(markersPayload) {
            const markers = _f8_toArray(markersPayload);
            const model = window._f8_editor ? window._f8_editor.getModel() : null;
            if (model) {
              monaco.editor.setModelMarkers(model, 'f8-python-lsp', markers);
            }
          });
        }

        function _syncDocument() {
          if (!syncDocument || !window._f8_editor) return;
          try {
            syncDocument(window._f8_editor.getValue());
          } catch (e) {
          }
        }

        window._f8_editor.onDidChangeModelContent(function() {
          window._f8_completionCache = null;
          _syncDocument();
        });

        monaco.languages.registerCompletionItemProvider('python', {
          triggerCharacters: ['.', '_'],
          provideCompletionItems: function(model, position) {
            return new Promise(function(resolve) {
              if (!requestCompletions) {
                resolve({ suggestions: [] });
                return;
              }
              const requestId = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()));
              window._f8_pendingCompletions[requestId] = function(items) {
                resolve({ suggestions: items });
              };
              setTimeout(function() {
                if (!window._f8_pendingCompletions[requestId]) return;
                delete window._f8_pendingCompletions[requestId];
                resolve({ suggestions: [] });
              }, 5000);
              requestCompletions(
                requestId,
                model.getValue(),
                Number(position.lineNumber),
                Number(position.column)
              );
            });
          },
          resolveCompletionItem: function(item) {
            return new Promise(function(resolve) {
              const resolveKey = String((item && item._f8ResolveKey) || '').trim();
              if (!resolveKey || !requestCompletionResolve) {
                resolve(item);
                return;
              }
              const requestId = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()));
              window._f8_pendingCompletionResolves[requestId] = function(payload) {
                if (payload && typeof payload === 'object') {
                  if (payload.detail) item.detail = String(payload.detail || '');
                  if (payload.documentation) item.documentation = _f8_asMarkdown(payload.documentation);
                  if (payload.insertText) item.insertText = String(payload.insertText || '');
                }
                resolve(item);
              };
              setTimeout(function() {
                if (!window._f8_pendingCompletionResolves[requestId]) return;
                delete window._f8_pendingCompletionResolves[requestId];
                resolve(item);
              }, 2500);
              requestCompletionResolve(requestId, resolveKey);
            });
          }
        });

        monaco.languages.registerHoverProvider('python', {
          provideHover: function(model, position) {
            return new Promise(function(resolve) {
              if (!requestHover) {
                resolve(null);
                return;
              }
              const requestId = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()));
              window._f8_pendingHovers[requestId] = resolve;
              setTimeout(function() {
                if (!window._f8_pendingHovers[requestId]) return;
                delete window._f8_pendingHovers[requestId];
                resolve(null);
              }, 3000);
              requestHover(requestId, model.getValue(), Number(position.lineNumber), Number(position.column));
            });
          }
        });

        monaco.languages.registerSignatureHelpProvider('python', {
          signatureHelpTriggerCharacters: ['(', ','],
          signatureHelpRetriggerCharacters: [','],
          provideSignatureHelp: function(model, position) {
            return new Promise(function(resolve) {
              if (!requestSignatureHelp) {
                resolve(null);
                return;
              }
              const requestId = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()));
              window._f8_pendingSignatures[requestId] = resolve;
              setTimeout(function() {
                if (!window._f8_pendingSignatures[requestId]) return;
                delete window._f8_pendingSignatures[requestId];
                resolve(null);
              }, 3000);
              requestSignatureHelp(requestId, model.getValue(), Number(position.lineNumber), Number(position.column));
            });
          }
        });

        _syncDocument();
      }

      require.config({ paths: { 'vs': 'vs' } });
      require(['vs/editor/editor.main'], function() {
        const init = window.__F8_INITIAL__ || { code: '', language: 'plaintext', theme: 'vs-dark' };
        const editorLanguage = _f8_resolveEditorLanguage(init.language || 'plaintext');
        window._f8_resolveEditorLanguage = _f8_resolveEditorLanguage;
        window._f8_editor = monaco.editor.create(document.getElementById('container'), {
          value: String(init.code || ''),
          language: editorLanguage,
          theme: String(init.theme || 'vs-dark'),
          automaticLayout: true,
          quickSuggestions: { other: true, comments: false, strings: true },
          quickSuggestionsDelay: 160,
          parameterHints: { enabled: true, cycle: true },
          suggest: { showInlineDetails: true, showStatusBar: true },
          suggestOnTriggerCharacters: true,
          minimap: { enabled: false },
          fontLigatures: true,
          fontSize: 13,
          tabSize: 4,
          insertSpaces: true,
          scrollBeyondLastLine: false,
          wordWrap: 'off',
        });
        window._f8_savedValue = String(init.code || '');
        window._f8_lastDirty = false;
        window._f8_editor.onDidChangeModelContent(function() {
          window._f8_notifyDirty();
        });
        window._f8_editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, function() {
          if (window._f8_editorUi && window._f8_editorUi.request_save) {
            window._f8_editorUi.request_save();
          }
        });
        window._f8_editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyQ, function() {
          if (window._f8_editorUi && window._f8_editorUi.request_close) {
            window._f8_editorUi.request_close();
          }
        });
        window._f8_editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Space, function() {
          window._f8_forceSuggestOnce = true;
          window._f8_editor.trigger('keyboard', 'editor.action.triggerSuggest', {});
        });
        window._f8_editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyJ, function() {
          window._f8_forceSuggestOnce = true;
          window._f8_editor.trigger('keyboard', 'editor.action.triggerSuggest', {});
        });
        window._f8_editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.Space, function() {
          window._f8_editor.trigger('keyboard', 'editor.action.triggerParameterHints', {});
        });
        window._f8_editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.KeyJ, function() {
          window._f8_editor.trigger('keyboard', 'editor.action.triggerParameterHints', {});
        });

        if (typeof QWebChannel !== 'undefined' && window.qt && qt.webChannelTransport) {
          new QWebChannel(qt.webChannelTransport, function(channel) {
            window._f8_editorUi = channel.objects.f8EditorUi || null;
            window._f8_notifyDirty();
            _f8_setupPythonAssist(channel);
          });
        }
      });
    </script>
  </head>
  <body>
    <div id="f8-root">
      <div id="container"></div>
    </div>
  </body>
</html>
"""
    return (
        template.replace("__F8_INITIAL_JSON__", json.dumps(initial, ensure_ascii=False))
        .replace("__F8_PRISM_ASSET_HTML__", prism_asset_html_block)
    )


def open_code_editor_dialog(
    parent: QtWidgets.QWidget | None,
    *,
    title: str,
    code: str,
    language: str,
    assist_context: EditorAssistContext | None = None,
    assist_context_provider: Callable[[], EditorAssistContext | None] | None = None,
    agent_scope: EditorAgentScope | None = None,
    agent_tools: tuple[object, ...] = (),
    agent_context_providers: tuple[object, ...] = (),
    graph_context_snapshot_provider: Callable[[], GraphContextSnapshot | None] | None = None,
    retained_agent_dependencies: tuple[object, ...] = (),
    agent_sidebar_launcher: Callable[[EditorAgentContext], None] | None = None,
) -> str | None:
    from .monaco_editor_host import open_code_editor_dialog as open_hosted_code_editor_dialog

    return open_hosted_code_editor_dialog(
        parent,
        title=title,
        code=code,
        language=language,
        assist_context=assist_context,
        assist_context_provider=assist_context_provider,
        agent_scope=agent_scope,
        agent_tools=agent_tools,
        agent_context_providers=agent_context_providers,
        graph_context_snapshot_provider=graph_context_snapshot_provider,
        retained_agent_dependencies=retained_agent_dependencies,
        agent_sidebar_launcher=agent_sidebar_launcher,
    )


def open_code_editor_window(
    parent: QtWidgets.QWidget | None,
    *,
    title: str,
    code: str,
    language: str,
    on_saved: Callable[[str], bool | None],
    target_exists_provider: Callable[[], bool] | None = None,
    assist_context: EditorAssistContext | None = None,
    assist_context_provider: Callable[[], EditorAssistContext | None] | None = None,
    session_key: EditorSessionKey | None = None,
    agent_scope: EditorAgentScope | None = None,
    agent_tools: tuple[object, ...] = (),
    agent_context_providers: tuple[object, ...] = (),
    graph_context_snapshot_provider: Callable[[], GraphContextSnapshot | None] | None = None,
    retained_agent_dependencies: tuple[object, ...] = (),
    agent_sidebar_launcher: Callable[[EditorAgentContext], None] | None = None,
) -> QtWidgets.QDialog:
    from .monaco_editor_host import open_code_editor_window as open_hosted_code_editor_window

    return open_hosted_code_editor_window(
        parent,
        title=title,
        code=code,
        language=language,
        on_saved=on_saved,
        target_exists_provider=target_exists_provider,
        assist_context=assist_context,
        assist_context_provider=assist_context_provider,
        session_key=session_key,
        agent_scope=agent_scope,
        agent_tools=agent_tools,
        agent_context_providers=agent_context_providers,
        graph_context_snapshot_provider=graph_context_snapshot_provider,
        retained_agent_dependencies=retained_agent_dependencies,
        agent_sidebar_launcher=agent_sidebar_launcher,
    )

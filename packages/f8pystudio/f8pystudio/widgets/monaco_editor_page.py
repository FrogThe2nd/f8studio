"""Monaco page resource builder used by the hosted multi-session editor."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from qtpy import QtWidgets

from ..editor_assist.workspace import EditorAssistContext

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


def build_monaco_editor_html(config: MonacoEditorPageConfig) -> str:
    base = str(config.monaco_base_url or "").strip().rstrip("/")
    initial = {
        "code": str(config.code or ""),
        "language": str(config.language or "plaintext").strip() or "plaintext",
        "theme": str(config.theme or "vs-dark").strip() or "vs-dark",
        "pythonAssistEnabled": bool(config.python_assist_enabled),
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
      
      #f8-ai-stop {{
        display: none;
        background: #f38ba8;
        border: none;
        border-radius: 6px;
        color: #1e1e2e;
        width: 28px; height: 28px;
        cursor: pointer;
        transition: transform 0.1s;
        align-items: center; justify-content: center;
        padding: 0;
      }}
      #f8-ai-stop:hover {{ background: #eba0ac; transform: scale(1.05); }}
      #f8-ai-stop:active {{ transform: scale(0.95); }}
      #f8-ai-stop.visible {{ display: flex; }}
      
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
      window._f8_chatRequests = Object.create(null);
      window._f8_editRequests = Object.create(null);
      window._f8_planRequests = Object.create(null);
      window._f8_attachments = [];
      window._f8_currentRid = null;
      window._f8_aiSignalsConnected = false;

      function _f8_editorLanguage() {{
        const init = window.__F8_INITIAL__ || {{}};
        return String(init.language || 'plaintext');
      }}

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
        
        window._f8_chatMessages.push({{role: 'user', content: text, attachments: window._f8_attachments}});
        if (thinking) thinking.classList.add('visible');
        const assistantEl = _f8_appendMessage('assistant', '');
        
        window._f8_currentRid = rid;
        document.getElementById('f8-ai-send').style.display = 'none';
        document.getElementById('f8-ai-stop').classList.add('visible');

        const attachmentsPayload = JSON.stringify(window._f8_attachments);
        
        if (window._f8_aiMode === 'chat') {{
          window._f8_chatRequests[rid] = {{ assistantEl, thinking }};
          window._f8_aiAssist.request_chat(rid, JSON.stringify(window._f8_chatMessages), code, selection, attachmentsPayload);
        }} else if (window._f8_aiMode === 'edit') {{
          // Original edit mode used 'statusEl' and didn't push to chatMessages yet
          window._f8_editRequests[rid] = {{ statusEl: assistantEl, thinking }}; 
          window._f8_aiAssist.request_edit(rid, code, text, JSON.stringify(window._f8_chatMessages), attachmentsPayload);
        }} else if (window._f8_aiMode === 'plan') {{
          window._f8_planRequests[rid] = {{ assistantEl, thinking }};
          window._f8_aiAssist.request_plan(rid, text, code, JSON.stringify(window._f8_chatMessages), attachmentsPayload);
        }}
        _f8_clearAttachments();
      }}

      function _f8_stopMessage() {{
        if (!window._f8_currentRid || !window._f8_aiAssist) return;
        window._f8_aiAssist.abort_request(window._f8_currentRid);
        // UI reset on signal
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
          original: monaco.editor.createModel(window._f8_diffOriginalCode, _f8_editorLanguage()),
          modified: monaco.editor.createModel(newCode, _f8_editorLanguage()),
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
        if (window._f8_editor && modifiedModel) {{
          const model = window._f8_editor.getModel();
          if (model) {{
            const fullRange = model.getFullModelRange();
            const selection = window._f8_editor.getSelection();
            
            // Explicitly push a stack element so this AI edit is a distinct undo step.
            // This ensures Ctrl+Z will correctly revert the entire AI edit as one block.
            model.pushStackElement();
            
            model.pushEditOperations(
              [selection],
              [{{ range: fullRange, text: newCode }}],
              function() {{ return [selection]; }}
            );
            
            model.pushStackElement();
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

        if (!window._f8_aiSignalsConnected) {{
          if (aiAssist.chat_chunk_ready && aiAssist.chat_chunk_ready.connect) {{
            aiAssist.chat_chunk_ready.connect(function(rid, delta) {{
              const request = window._f8_chatRequests[rid];
              if (!request) return;
              const assistantEl = request.assistantEl;
              const cur = assistantEl.dataset.raw || '';
              assistantEl.dataset.raw = cur + delta;
              assistantEl.innerHTML = _f8_md(assistantEl.dataset.raw);
              if (window.Prism) {{ try {{ Prism.highlightAllUnder(assistantEl); }} catch(e) {{}} }}
              _f8_scrollBottom();
            }});
          }}
          if (aiAssist.chat_done && aiAssist.chat_done.connect) {{
            aiAssist.chat_done.connect(function(rid, err) {{
              const request = window._f8_chatRequests[rid];
              if (!request) return;
              delete window._f8_chatRequests[rid];
              if (window._f8_currentRid === rid) {{
                window._f8_currentRid = null;
                document.getElementById('f8-ai-send').style.display = 'flex';
                document.getElementById('f8-ai-stop').classList.remove('visible');
              }}
              if (request.thinking) request.thinking.classList.remove('visible');
              const assistantEl = request.assistantEl;
              if (err) {{
                const errText = '⚠ ' + err;
                const cur = assistantEl.dataset.raw || '';
                assistantEl.dataset.raw = cur ? (cur + '\\n\\n' + errText) : errText;
                assistantEl.innerHTML = _f8_md(assistantEl.dataset.raw);
              }} else {{
                window._f8_chatMessages.push({{role: 'assistant', content: assistantEl.dataset.raw || ''}});
              }}
              if (window._f8_aiAssist.update_chat_context) {{
                window._f8_aiAssist.update_chat_context(JSON.stringify(window._f8_chatMessages));
              }}
            }});
          }}
          if (aiAssist.edit_result_ready && aiAssist.edit_result_ready.connect) {{
            aiAssist.edit_result_ready.connect(function(rid, newCode, err) {{
              const request = window._f8_editRequests[rid];
              if (!request) return;
              delete window._f8_editRequests[rid];
              if (window._f8_currentRid === rid) {{
                window._f8_currentRid = null;
                document.getElementById('f8-ai-send').style.display = 'flex';
                document.getElementById('f8-ai-stop').classList.remove('visible');
              }}
              if (request.thinking) request.thinking.classList.remove('visible');
              if (err) {{
                request.statusEl.dataset.raw = '⚠ ' + err;
                request.statusEl.innerHTML = _f8_md(request.statusEl.dataset.raw);
                return;
              }}
              if (request.statusEl && request.statusEl.parentNode) {{
                request.statusEl.parentNode.removeChild(request.statusEl);
              }}
              _f8_showDiff(newCode);
            }});
          }}
          if (aiAssist.plan_step_ready && aiAssist.plan_step_ready.connect) {{
            aiAssist.plan_step_ready.connect(function(rid, delta) {{
              const request = window._f8_planRequests[rid];
              if (!request) return;
              const assistantEl = request.assistantEl;
              const cur = assistantEl.dataset.raw || '';
              assistantEl.dataset.raw = cur + delta;
              assistantEl.innerHTML = _f8_md(assistantEl.dataset.raw);
              if (window.Prism) {{ try {{ Prism.highlightAllUnder(assistantEl); }} catch(e) {{}} }}
              _f8_scrollBottom();
            }});
          }}
          if (aiAssist.plan_done && aiAssist.plan_done.connect) {{
            aiAssist.plan_done.connect(function(rid, err) {{
              const request = window._f8_planRequests[rid];
              if (!request) return;
              delete window._f8_planRequests[rid];
              if (window._f8_currentRid === rid) {{
                window._f8_currentRid = null;
                document.getElementById('f8-ai-send').style.display = 'flex';
                document.getElementById('f8-ai-stop').classList.remove('visible');
              }}
              if (request.thinking) request.thinking.classList.remove('visible');
              const assistantEl = request.assistantEl;
              if (err) {{
                const errText = '⚠ ' + err;
                const cur = assistantEl.dataset.raw || '';
                assistantEl.dataset.raw = cur ? (cur + '\\n\\n' + errText) : errText;
                assistantEl.innerHTML = _f8_md(assistantEl.dataset.raw);
                return;
              }}
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
            }});
          }}
          window._f8_aiSignalsConnected = true;
          document.getElementById('f8-ai-send').onclick = _f8_sendMessage;
          document.getElementById('f8-ai-stop').onclick = _f8_stopMessage;
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
            <button id="f8-ai-stop" title="Stop">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="6" width="12" height="12" rx="2" /></svg>
            </button>
          </div>
        </div>
      </div>
    </div>
  </body>
</html>
"""
    return html


def open_code_editor_dialog(
    parent: QtWidgets.QWidget | None,
    *,
    title: str,
    code: str,
    language: str,
    assist_context: EditorAssistContext | None = None,
    assist_context_provider: Callable[[], EditorAssistContext | None] | None = None,
) -> str | None:
    from .monaco_editor_dialog import open_code_editor_dialog as open_hosted_code_editor_dialog

    return open_hosted_code_editor_dialog(
        parent,
        title=title,
        code=code,
        language=language,
        assist_context=assist_context,
        assist_context_provider=assist_context_provider,
    )


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
    from .monaco_editor_dialog import open_code_editor_window as open_hosted_code_editor_window

    return open_hosted_code_editor_window(
        parent,
        title=title,
        code=code,
        language=language,
        on_saved=on_saved,
        assist_context=assist_context,
        assist_context_provider=assist_context_provider,
    )

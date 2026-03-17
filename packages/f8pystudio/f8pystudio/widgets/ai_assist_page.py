"""
Standalone AI Assist page builder for the Studio Sidebar.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

def build_ai_assist_html() -> str:
    """Builds a standalone HTML page for the AI assist sidebar."""
    
    html = f"""
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <style>
      html, body, #f8-ai-panel {{
        height: 100%;
        width: 100%;
        margin: 0;
        padding: 0;
        overflow: hidden;
        background: #1e1e2e;
      }}
      #f8-ai-panel {{
        display: flex;
        flex-direction: column;
        font-family: 'Segoe UI', system-ui, sans-serif;
        font-size: 13px;
        color: #cdd6f4;
        z-index: 100;
      }}
      #f8-ai-messages {{
        flex: 1;
        overflow-y: auto;
        padding: 8px;
        display: flex;
        flex-direction: column;
        gap: 8px;
        scrollbar-width: thin;
        scrollbar-color: #45475a transparent;
      }}
      #f8-ai-messages::-webkit-scrollbar {{
        width: 6px;
      }}
      #f8-ai-messages::-webkit-scrollbar-track {{
        background: transparent;
      }}
      #f8-ai-messages::-webkit-scrollbar-thumb {{
        background: #45475a;
        border-radius: 3px;
      }}
      .f8-msg {{ padding: 8px 10px; border-radius: 6px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }}
      .f8-msg.user {{ background: #313244; align-self: flex-end; max-width: 85%; }}
      .f8-msg.assistant {{ background: #1e1e2e; border: 1px solid #313244; align-self: flex-start; max-width: 100%; }}
      
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
      #f8-ai-attach-btn, .f8-new-chat {{
        border: none;
        border-radius: 4px;
        background: transparent;
        color: #9399b2;
        width: 24px;
        height: 24px;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 0;
        transition: background 0.1s, color 0.1s;
      }}
      #f8-ai-attach-btn svg, .f8-new-chat svg {{
        width: 18px;
        height: 18px;
        stroke-width: 1.5;
      }}
      #f8-ai-attach-btn:hover, .f8-new-chat:hover {{
        background: #45475a;
        color: #cdd6f4;
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
      #f8-ai-stop svg {{ width: 20px; height: 20px; stroke-width: 2; }}
      #f8-ai-stop:hover {{ background: #eba0ac; transform: scale(1.05); }}
      #f8-ai-stop:active {{ transform: scale(0.95); }}
      #f8-ai-stop.visible {{ display: flex; }}
      
      #f8-ai-thinking {{
        display: none;
        padding: 4px 8px;
        color: #6c7086;
        font-size: 11px;
        font-style: italic;
      }}
      #f8-ai-thinking.visible {{ display: block; }}
      
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
      .f8-think-content {{
        color: #9399b2;
        font-size: 12px;
        margin-top: 6px;
        white-space: pre-wrap;
      }}
      
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
      }}
    </style>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/themes/prism-twilight.min.css" rel="stylesheet" />
    <script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/prism.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/components/prism-python.min.js"></script>
    <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
    <script>
      window._f8_aiAssist = null;
      window._f8_chatMessages = [];
      window._f8_chatRequests = Object.create(null);
      window._f8_attachments = [];
      window._f8_currentRid = null;

      function _f8_md(text) {{
        let html = String(text || '');
        html = html.replace(/<think>([\\s\\S]*?)(<\\/think>|$)/g, function(_, content, end_tag) {{
          const isOpen = end_tag ? '' : ' open';
          return '<details class="f8-think"' + isOpen + '><summary>🤔 Thinking Process</summary><div class="f8-think-content">' + content + '</div></details>';
        }});
        html = html.replace(/```(\\w*)\\n?([\\s\\S]*?)```/g, function(_, lang, code) {{
          const escaped = code.replace(/</g,'&lt;').replace(/>/g,'&gt;');
          return '<pre><button class="f8-copy-btn" onclick="_f8_copy(this)">copy</button><code class="language-' + (lang||'') + '">' + escaped + '</code></pre>';
        }});
        html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
        html = html.replace(/\\*\\*(.+?)\\*\\*/g, '<b>$1</b>');
        html = html.replace(/\\*(.+?)\\*/g, '<i>$1</i>');
        return html;
      }}

      function _f8_copy(btn) {{
        const pre = btn.closest('pre');
        const codeEl = pre ? pre.querySelector('code') : null;
        const text = codeEl ? codeEl.textContent : '';
        if (window._f8_aiAssist && window._f8_aiAssist.copy_to_clipboard) {{
          window._f8_aiAssist.copy_to_clipboard(text);
          btn.textContent = '✓';
          setTimeout(() => {{ btn.textContent = 'copy'; }}, 1500);
        }}
      }}

      function _f8_sendMessage() {{
        const input = document.getElementById('f8-ai-input');
        const text = input ? input.value.trim() : '';
        if (!text && window._f8_attachments.length === 0 || !window._f8_aiAssist) return;
        input.value = '';
        input.style.height = 'auto';

        _f8_appendMessage('user', text);
        
        const rid = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()));
        const thinking = document.getElementById('f8-ai-thinking');
        
        window._f8_chatMessages.push({{role: 'user', content: text, attachments: window._f8_attachments}});
        if (thinking) thinking.classList.add('visible');
        const assistantEl = _f8_appendMessage('assistant', '');
        
        window._f8_currentRid = rid;
        document.getElementById('f8-ai-send').style.display = 'none';
        document.getElementById('f8-ai-stop').classList.add('visible');

        window._f8_chatRequests[rid] = {{ assistantEl, thinking }};
        window._f8_aiAssist.request_chat(rid, JSON.stringify(window._f8_chatMessages), '', '', JSON.stringify(window._f8_attachments));
        _f8_clearAttachments();
      }}

      function _f8_stopMessage() {{
        if (!window._f8_currentRid || !window._f8_aiAssist) return;
        window._f8_aiAssist.abort_request(window._f8_currentRid);
        // UI will be reset when chat_done is received
      }}

      function _f8_appendMessage(role, text) {{
        const msgs = document.getElementById('f8-ai-messages');
        const div = document.createElement('div');
        div.className = 'f8-msg ' + role;
        div.dataset.raw = text;
        div.innerHTML = role === 'assistant' ? _f8_md(text) : _f8_escHtml(text);
        if (role === 'assistant' && window.Prism) Prism.highlightAllUnder(div);
        msgs.appendChild(div);
        msgs.scrollTop = msgs.scrollHeight;
        return div;
      }}

      function _f8_escHtml(s) {{
        return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      }}

      function _f8_newConversation() {{
        document.getElementById('f8-ai-messages').innerHTML = '';
        window._f8_chatMessages = [];
        if (window._f8_aiAssist.reset_chat_history) window._f8_aiAssist.reset_chat_history();
        _f8_appendMessage('assistant', 'Conversation reset.');
        _f8_clearAttachments();
      }}

      function _f8_clearAttachments() {{
          window._f8_attachments = [];
          const container = document.getElementById('f8-ai-attachments');
          container.innerHTML = '';
          container.classList.remove('visible');
      }}

      function _f8_addAttachments(newAtts) {{
          newAtts.forEach(att => window._f8_attachments.push(att));
          _f8_renderAttachments();
      }}
      
      function _f8_renderAttachments() {{
          const container = document.getElementById('f8-ai-attachments');
          container.innerHTML = '';
          if (window._f8_attachments.length > 0) {{
              container.classList.add('visible');
              window._f8_attachments.forEach((att, idx) => {{
                  const thumb = document.createElement('div');
                  thumb.className = 'f8-att-thumb';
                  thumb.style.backgroundImage = 'url(data:' + att.mime + ';base64,' + att.content + ')';
                  const rm = document.createElement('div');
                  rm.className = 'f8-att-remove';
                  rm.textContent = '×';
                  rm.onclick = () => {{ window._f8_attachments.splice(idx, 1); _f8_renderAttachments(); }};
                  thumb.appendChild(rm);
                  container.appendChild(thumb);
              }});
          }} else {{
              container.classList.remove('visible');
          }}
      }}

      window.onload = function() {{
        new QWebChannel(qt.webChannelTransport, function(channel) {{
          window._f8_aiAssist = channel.objects.aiAssist;
          
          window._f8_aiAssist.chat_chunk_ready.connect(function(rid, delta) {{
            const req = window._f8_chatRequests[rid];
            if (!req) return;
            req.assistantEl.dataset.raw = (req.assistantEl.dataset.raw || '') + delta;
            req.assistantEl.innerHTML = _f8_md(req.assistantEl.dataset.raw);
            if (window.Prism) Prism.highlightAllUnder(req.assistantEl);
            document.getElementById('f8-ai-messages').scrollTop = document.getElementById('f8-ai-messages').scrollHeight;
          }});
          
          window._f8_aiAssist.chat_done.connect(function(rid, err) {{
            const req = window._f8_chatRequests[rid];
            if (!req) return;
            delete window._f8_chatRequests[rid];
            if (window._f8_currentRid === rid) {{
                window._f8_currentRid = null;
                document.getElementById('f8-ai-send').style.display = 'flex';
                document.getElementById('f8-ai-stop').classList.remove('visible');
            }}
            req.thinking.classList.remove('visible');
            if (err) {{
               req.assistantEl.innerHTML += '<div style="color:#f38ba8;padding-top:4px;">⚠ Error: ' + _f8_escHtml(err) + '</div>';
            }} else {{
               window._f8_chatMessages.push({{role: 'assistant', content: req.assistantEl.dataset.raw}});
            }}
          }});

          document.getElementById('f8-ai-send').onclick = _f8_sendMessage;
          document.getElementById('f8-ai-stop').onclick = _f8_stopMessage;
          document.getElementById('f8-ai-input').onkeydown = function(e) {{
            if (e.key === 'Enter' && !e.shiftKey) {{ e.preventDefault(); _f8_sendMessage(); }}
          }};
          document.querySelector('.f8-new-chat').onclick = _f8_newConversation;
          
          document.getElementById('f8-ai-attach-btn').onclick = function() {{
              window._f8_aiAssist.select_images(function(res) {{
                  if(res) _f8_addAttachments(res);
              }});
          }};
          
          _f8_appendMessage('assistant', 'How can I help you with your graph?');
        }});
      }};
    </script>
  </head>
  <body>
    <div id="f8-ai-panel">
      <div id="f8-ai-messages"></div>
      <div id="f8-ai-thinking">AI is thinking…</div>
      <div id="f8-ai-attachments"></div>
      <div id="f8-ai-input-area">
        <div class="f8-input-wrapper">
          <textarea id="f8-ai-input" placeholder="Ask AI…" rows="1"></textarea>
          <div class="f8-input-toolbar">
            <div class="f8-toolbar-left">
              <button id="f8-ai-attach-btn" title="Attach Images">
                <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M15 7l-6.5 6.5a1.5 1.5 0 0 0 3 3l6.5 -6.5a3 3 0 0 0 -6 -6l-6.5 6.5a4.5 4.5 0 0 0 9 9l6.5 -6.5" /></svg>
              </button>
              <button class="f8-new-chat" title="New Conversation">
                <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0 -18"/><path d="M12 8v8"/><path d="M8 12h8"/></svg>
              </button>
            </div>
            <button id="f8-ai-send" title="Send">
               <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 14l11 -11"/><path d="M21 3l-6.5 18a.55 .55 0 0 1 -1 0l-3.5 -7l-7 -3.5a.55 .55 0 0 1 0 -1l18 -6.5"/></svg>
            </button>
            <button id="f8-ai-stop" title="Stop">
               <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>
            </button>
          </div>
        </div>
      </div>
    </div>
  </body>
</html>
    """
    return html

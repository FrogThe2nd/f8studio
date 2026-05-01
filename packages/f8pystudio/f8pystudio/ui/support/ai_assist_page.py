"""
Standalone AI Assist page builder for the Studio Sidebar.
"""

from __future__ import annotations

from .studio_theme import qss_rgba, studio_dark_theme


def build_ai_assist_html(*, prism_asset_html: str = "") -> str:
    """Builds a standalone HTML page for the AI assist sidebar."""
    prism_asset_html_block = str(prism_asset_html or "").strip()
    if prism_asset_html_block:
        prism_asset_html_block += "\n"
    p = studio_dark_theme().palette

    html = f"""
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <style>
      :root {{
        --bg-primary: {p.window_bg};
        --bg-secondary: {p.panel_bg};
        --bg-tertiary: {p.panel_alt_bg};
        --bg-hover: {p.button_hover_bg};
        --border-color: {p.border};
        --text-primary: {p.text_primary};
        --text-secondary: {p.text_secondary};
        --text-muted: {p.text_muted};
        --accent-purple: {p.purple};
        --accent-blue: {p.accent};
        --accent-red: {p.error};
        --accent-green: {p.success};
        --accent-yellow: {p.warning};
        --accent-purple-hover: {p.accent_hover};
        --accent-red-hover: {p.error};
        --focus-ring: {qss_rgba(p.purple, 40)};
        --thinking-bg: {p.field_bg};
        --thinking-border: {p.border};
      }}

      html, body, #f8-ai-panel {{
        height: 100%;
        width: 100%;
        margin: 0;
        padding: 0;
        overflow: hidden;
        background: var(--bg-primary);
      }}

      #f8-ai-panel {{
        display: flex;
        flex-direction: column;
        font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
        font-size: 13px;
        color: var(--text-primary);
      }}

      /* Messages Container */
      #f8-ai-messages {{
        flex: 1;
        overflow-y: auto;
        padding: 12px;
        display: flex;
        flex-direction: column;
        gap: 12px;
        scrollbar-width: thin;
        scrollbar-color: var(--bg-hover) transparent;
      }}

      #f8-ai-messages::-webkit-scrollbar {{
        width: 8px;
      }}

      #f8-ai-messages::-webkit-scrollbar-track {{
        background: transparent;
      }}

      #f8-ai-messages::-webkit-scrollbar-thumb {{
        background: var(--bg-hover);
        border-radius: 4px;
      }}

      #f8-ai-messages::-webkit-scrollbar-thumb:hover {{
        background: var(--border-color);
      }}

      /* Message Bubbles */
      .f8-msg {{
        padding: 12px 14px;
        border-radius: 8px;
        line-height: 1.6;
        word-break: break-word;
        animation: fadeIn 0.2s ease-out;
      }}

      @keyframes fadeIn {{
        from {{ opacity: 0; transform: translateY(4px); }}
        to {{ opacity: 1; transform: translateY(0); }}
      }}

      .f8-msg.user {{
        background: var(--bg-tertiary);
        align-self: flex-end;
        max-width: 85%;
        border: 1px solid var(--border-color);
      }}

      .f8-msg.assistant {{
        background: var(--bg-secondary);
        border: 1px solid var(--border-color);
        align-self: flex-start;
        max-width: 100%;
      }}

      /* Thinking Process */
      .f8-thinking-block {{
        margin: 12px 0;
        border: 1px solid var(--thinking-border);
        border-radius: 6px;
        background: var(--thinking-bg);
        overflow: hidden;
      }}

      .f8-thinking-header {{
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 10px 12px;
        background: var(--bg-secondary);
        cursor: pointer;
        user-select: none;
        transition: background 0.15s;
      }}

      .f8-thinking-header:hover {{
        background: var(--bg-tertiary);
      }}

      .f8-thinking-icon {{
        font-size: 16px;
        transition: transform 0.2s;
      }}

      .f8-thinking-block.collapsed .f8-thinking-icon {{
        transform: rotate(-90deg);
      }}

      .f8-thinking-title {{
        flex: 1;
        font-size: 12px;
        font-weight: 500;
        color: var(--accent-blue);
      }}

      .f8-thinking-content {{
        padding: 12px;
        color: var(--text-secondary);
        font-size: 12px;
        line-height: 1.5;
        white-space: pre-wrap;
        border-top: 1px solid var(--thinking-border);
        max-height: 400px;
        overflow-y: auto;
      }}

      .f8-thinking-block.collapsed .f8-thinking-content {{
        display: none;
      }}

      /* Code Blocks */
      .f8-msg pre {{
        background: var(--thinking-bg);
        border: 1px solid var(--border-color);
        border-radius: 6px;
        padding: 12px;
        overflow-x: auto;
        position: relative;
        margin: 8px 0;
        font-size: 12px;
      }}

      .f8-msg code {{
        font-family: 'Fira Code', 'Cascadia Code', 'Consolas', monospace;
        font-size: 12px;
      }}

      .f8-msg :not(pre) > code {{
        background: var(--bg-tertiary);
        padding: 2px 6px;
        border-radius: 3px;
        font-size: 11.5px;
      }}

      .f8-code-header {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 8px;
        padding-bottom: 6px;
        border-bottom: 1px solid var(--border-color);
      }}

      .f8-code-lang {{
        font-size: 10px;
        text-transform: uppercase;
        color: var(--text-muted);
        font-weight: 600;
        letter-spacing: 0.5px;
      }}

      .f8-copy-btn {{
        background: var(--bg-hover);
        border: none;
        border-radius: 4px;
        color: var(--text-primary);
        font-size: 10px;
        padding: 4px 8px;
        cursor: pointer;
        transition: all 0.15s;
        font-weight: 500;
      }}

      .f8-copy-btn:hover {{
        background: var(--border-color);
        transform: translateY(-1px);
      }}

      .f8-copy-btn:active {{
        transform: translateY(0);
      }}

      .f8-copy-btn.copied {{
        background: var(--accent-green);
        color: var(--bg-primary);
      }}

      /* Markdown Elements */
      .f8-msg h1, .f8-msg h2, .f8-msg h3 {{
        margin: 16px 0 8px 0;
        font-weight: 600;
        line-height: 1.3;
      }}

      .f8-msg h1 {{ font-size: 18px; color: var(--accent-purple); }}
      .f8-msg h2 {{ font-size: 16px; color: var(--accent-blue); }}
      .f8-msg h3 {{ font-size: 14px; color: var(--accent-green); }}

      .f8-msg ul, .f8-msg ol {{
        margin: 8px 0;
        padding-left: 24px;
      }}

      .f8-msg li {{
        margin: 4px 0;
      }}

      .f8-msg a {{
        color: var(--accent-blue);
        text-decoration: none;
        border-bottom: 1px solid transparent;
        transition: border-color 0.15s;
      }}

      .f8-msg a:hover {{
        border-bottom-color: var(--accent-blue);
      }}

      .f8-msg blockquote {{
        margin: 8px 0;
        padding-left: 12px;
        border-left: 3px solid var(--accent-purple);
        color: var(--text-secondary);
        font-style: italic;
      }}

      .f8-msg strong {{ font-weight: 600; color: var(--text-primary); }}
      .f8-msg em {{ font-style: italic; color: var(--text-secondary); }}

      /* Input Area */
      #f8-ai-input-area {{
        padding: 12px;
        background: var(--bg-secondary);
        border-top: 1px solid var(--border-color);
      }}

      .f8-input-wrapper {{
        background: var(--bg-tertiary);
        border: 1px solid var(--border-color);
        border-radius: 10px;
        display: flex;
        flex-direction: column;
        transition: all 0.2s;
      }}

      .f8-input-wrapper:focus-within {{
        border-color: var(--accent-purple);
        box-shadow: 0 0 0 3px var(--focus-ring);
      }}

      #f8-ai-input {{
        width: 100%;
        background: transparent;
        border: none;
        color: var(--text-primary);
        padding: 12px 14px 4px 14px;
        font-size: 13px;
        resize: none;
        min-height: 24px;
        max-height: 300px;
        outline: none;
        font-family: inherit;
        line-height: 1.5;
      }}

      #f8-ai-input::placeholder {{
        color: var(--text-muted);
      }}

      .f8-input-toolbar {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 6px 10px 10px 10px;
      }}

      .f8-toolbar-left {{
        display: flex;
        gap: 6px;
      }}

      .f8-toolbar-btn {{
        border: none;
        border-radius: 6px;
        background: transparent;
        color: var(--text-secondary);
        width: 28px;
        height: 28px;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 0;
        transition: all 0.15s;
      }}

      .f8-toolbar-btn svg {{
        width: 18px;
        height: 18px;
        stroke-width: 1.8;
      }}

      .f8-toolbar-btn:hover {{
        background: var(--bg-hover);
        color: var(--text-primary);
      }}

      #f8-ai-send {{
        background: var(--accent-purple);
        color: var(--bg-primary);
        width: 32px;
        height: 32px;
      }}

      #f8-ai-send svg {{
        width: 20px;
        height: 20px;
        stroke-width: 2;
      }}

      #f8-ai-send:hover {{
        background: var(--accent-purple-hover);
        transform: scale(1.05);
      }}

      #f8-ai-send:active {{
        transform: scale(0.95);
      }}

      #f8-ai-stop {{
        display: none;
        background: var(--accent-red);
        color: var(--bg-primary);
        width: 32px;
        height: 32px;
      }}

      #f8-ai-stop.visible {{
        display: flex;
      }}

      #f8-ai-stop:hover {{
        background: var(--accent-red-hover);
        transform: scale(1.05);
      }}

      /* Thinking Indicator */
      #f8-ai-thinking {{
        display: none;
        padding: 8px 12px;
        color: var(--text-muted);
        font-size: 11px;
        font-style: italic;
        background: var(--bg-secondary);
        border-top: 1px solid var(--border-color);
      }}

      #f8-ai-thinking.visible {{
        display: flex;
        align-items: center;
        gap: 8px;
      }}

      .f8-thinking-spinner {{
        width: 12px;
        height: 12px;
        border: 2px solid var(--border-color);
        border-top-color: var(--accent-blue);
        border-radius: 50%;
        animation: spin 0.8s linear infinite;
      }}

      @keyframes spin {{
        to {{ transform: rotate(360deg); }}
      }}

      /* Attachments */
      #f8-ai-attachments {{
        display: none;
        padding: 8px 12px;
        gap: 8px;
        overflow-x: auto;
        background: var(--bg-secondary);
        border-top: 1px solid var(--border-color);
      }}

      #f8-ai-attachments.visible {{
        display: flex;
      }}

      .f8-att-thumb {{
        position: relative;
        width: 56px;
        height: 56px;
        border-radius: 6px;
        border: 1px solid var(--border-color);
        flex-shrink: 0;
        background-size: cover;
        background-position: center;
        transition: transform 0.15s;
      }}

      .f8-att-thumb:hover {{
        transform: scale(1.05);
      }}

      .f8-att-remove {{
        position: absolute;
        top: -6px;
        right: -6px;
        background: var(--accent-red);
        color: var(--bg-primary);
        border-radius: 50%;
        width: 18px;
        height: 18px;
        font-size: 12px;
        display: flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
        font-weight: bold;
        transition: all 0.15s;
      }}

      .f8-att-remove:hover {{
        background: var(--accent-red-hover);
        transform: scale(1.1);
      }}
    </style>
    {prism_asset_html_block}
    <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
    <script>
      window._f8_aiAssist = null;
      window._f8_chatMessages = [];
      window._f8_chatRequests = Object.create(null);
      window._f8_attachments = [];
      window._f8_currentRid = null;

      // Enhanced markdown parser with thinking block support
      function _f8_md(text) {{
        let html = String(text || '');

        // Parse thinking blocks with better formatting
        html = html.replace(/<think>([\\s\\S]*?)(<\\/think>|$)/g, function(_, content, end_tag) {{
          const thinkingContent = content.trim();
          return `
            <div class="f8-thinking-block" onclick="this.classList.toggle('collapsed')">
              <div class="f8-thinking-header">
                <span class="f8-thinking-icon">▼</span>
                <span class="f8-thinking-title">🤔 Thinking Process</span>
              </div>
              <div class="f8-thinking-content">${{thinkingContent}}</div>
            </div>
          `;
        }});

        // Code blocks with language label
        html = html.replace(/```(\\w*)\\n?([\\s\\S]*?)```/g, function(_, lang, code) {{
          const escaped = code.trim().replace(/</g,'&lt;').replace(/>/g,'&gt;');
          const langLabel = lang || 'text';
          return `
            <pre>
              <div class="f8-code-header">
                <span class="f8-code-lang">${{langLabel}}</span>
                <button class="f8-copy-btn" onclick="_f8_copy(this, event)">Copy</button>
              </div>
              <code class="language-${{langLabel}}">${{escaped}}</code>
            </pre>
          `;
        }});

        // Inline code
        html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

        // Headers
        html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
        html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
        html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');

        // Bold and italic
        html = html.replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>');
        html = html.replace(/\\*(.+?)\\*/g, '<em>$1</em>');

        // Links
        html = html.replace(/\\[([^\\]]+)\\]\\(([^)]+)\\)/g, '<a href="$2" target="_blank">$1</a>');

        // Lists (basic support)
        html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
        html = html.replace(/(<li>.*<\\/li>)/s, '<ul>$1</ul>');

        // Blockquotes
        html = html.replace(/^> (.+)$/gm, '<blockquote>$1</blockquote>');

        return html;
      }}

      function _f8_copy(btn, event) {{
        event.stopPropagation();
        const pre = btn.closest('pre');
        const codeEl = pre ? pre.querySelector('code') : null;
        const text = codeEl ? codeEl.textContent : '';
        if (window._f8_aiAssist && window._f8_aiAssist.copy_to_clipboard) {{
          window._f8_aiAssist.copy_to_clipboard(text);
          btn.textContent = '✓ Copied';
          btn.classList.add('copied');
          setTimeout(() => {{
            btn.textContent = 'Copy';
            btn.classList.remove('copied');
          }}, 2000);
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
        return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\\n/g, '<br>');
      }}

      function _f8_newConversation() {{
        document.getElementById('f8-ai-messages').innerHTML = '';
        window._f8_chatMessages = [];
        if (window._f8_aiAssist.reset_chat_history) window._f8_aiAssist.reset_chat_history();
        _f8_appendMessage('assistant', 'Conversation reset. How can I help you?');
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
            rm.onclick = (e) => {{
              e.stopPropagation();
              window._f8_attachments.splice(idx, 1);
              _f8_renderAttachments();
            }};
            thumb.appendChild(rm);
            container.appendChild(thumb);
          }});
        }} else {{
          container.classList.remove('visible');
        }}
      }}

      // Auto-resize textarea
      function _f8_autoResize(textarea) {{
        textarea.style.height = 'auto';
        textarea.style.height = Math.min(textarea.scrollHeight, 300) + 'px';
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
            const msgs = document.getElementById('f8-ai-messages');
            msgs.scrollTop = msgs.scrollHeight;
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
              req.assistantEl.innerHTML += '<div style="color:var(--accent-red);padding-top:8px;font-weight:500;">⚠ Error: ' + _f8_escHtml(err) + '</div>';
            }} else {{
              window._f8_chatMessages.push({{role: 'assistant', content: req.assistantEl.dataset.raw}});
            }}
          }});

          const input = document.getElementById('f8-ai-input');
          const sendBtn = document.getElementById('f8-ai-send');
          const stopBtn = document.getElementById('f8-ai-stop');

          sendBtn.onclick = _f8_sendMessage;
          stopBtn.onclick = _f8_stopMessage;

          input.oninput = function() {{ _f8_autoResize(this); }};
          input.onkeydown = function(e) {{
            if (e.key === 'Enter' && !e.shiftKey) {{
              e.preventDefault();
              _f8_sendMessage();
            }}
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
      <div id="f8-ai-thinking">
        <div class="f8-thinking-spinner"></div>
        <span>AI is thinking…</span>
      </div>
      <div id="f8-ai-attachments"></div>
      <div id="f8-ai-input-area">
        <div class="f8-input-wrapper">
          <textarea id="f8-ai-input" placeholder="Ask AI about your graph…" rows="1"></textarea>
          <div class="f8-input-toolbar">
            <div class="f8-toolbar-left">
              <button id="f8-ai-attach-btn" class="f8-toolbar-btn" title="Attach Images">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M15 7l-6.5 6.5a1.5 1.5 0 0 0 3 3l6.5 -6.5a3 3 0 0 0 -6 -6l-6.5 6.5a4.5 4.5 0 0 0 9 9l6.5 -6.5" /></svg>
              </button>
              <button class="f8-new-chat f8-toolbar-btn" title="New Conversation">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0 -18"/><path d="M12 8v8"/><path d="M8 12h8"/></svg>
              </button>
            </div>
            <button id="f8-ai-send" title="Send (Enter)">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M10 14l11 -11"/><path d="M21 3l-6.5 18a.55 .55 0 0 1 -1 0l-3.5 -7l-7 -3.5a.55 .55 0 0 1 0 -1l18 -6.5"/></svg>
            </button>
            <button id="f8-ai-stop" title="Stop Generation">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>
            </button>
          </div>
        </div>
      </div>
    </div>
  </body>
</html>
    """
    return html

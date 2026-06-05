from __future__ import annotations

from dataclasses import dataclass

from f8pystudio.ui.support.studio_theme import studio_dark_theme


@dataclass(frozen=True)
class AgentCardClassNames:
    card: str = "f8-agent-card"
    tool_trace: str = "f8-agent-tool-trace"
    approval: str = "f8-agent-approval"


def agent_card_css() -> str:
    p = studio_dark_theme().palette
    return f"""
      .f8-agent-tool-host {{
        margin-top: 6px;
      }}

      .f8-agent-card {{
        border: 1px solid {p.border};
        border-radius: 5px;
        background: {p.panel_bg};
        color: {p.text_secondary};
        padding: 7px 8px;
        margin: 6px 0 0 0;
        font-size: 12px;
        line-height: 1.45;
      }}

      .f8-agent-tool-trace {{
        margin: 1px 0;
        color: {p.text_muted};
        font-size: 11px;
      }}

      .f8-agent-tool-trace summary {{
        display: flex;
        align-items: center;
        gap: 6px;
        min-height: 20px;
        cursor: pointer;
        list-style: none;
        outline: none;
      }}

      .f8-agent-tool-trace summary::-webkit-details-marker {{
        display: none;
      }}

      .f8-agent-tool-caret {{
        width: 12px;
        color: {p.text_muted};
        flex: 0 0 auto;
      }}

      .f8-agent-tool-trace[open] .f8-agent-tool-caret {{
        transform: rotate(90deg);
      }}

      .f8-agent-tool-title {{
        color: {p.text_secondary};
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }}

      .f8-agent-tool-status {{
        margin-left: auto;
        font-size: 10px;
        color: {p.text_muted};
      }}

      .f8-agent-tool-status.started {{
        color: {p.info};
      }}

      .f8-agent-tool-status.completed {{
        color: {p.success};
      }}

      .f8-agent-tool-status.failed {{
        color: {p.error};
      }}

      .f8-agent-tool-detail {{
        margin: 2px 0 6px 18px;
        color: {p.text_muted};
        overflow-wrap: anywhere;
      }}

      .f8-agent-card-row {{
        display: flex;
        align-items: center;
        gap: 8px;
        min-width: 0;
      }}

      .f8-agent-card-title {{
        color: {p.text_primary};
        font-weight: 600;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }}

      .f8-agent-card-status {{
        margin-left: auto;
        border-radius: 999px;
        padding: 2px 7px;
        font-size: 10px;
        line-height: 1.4;
        color: {p.text_primary};
        border: 1px solid {p.border};
      }}

      .f8-agent-card-status.started {{
        color: {p.info};
        border-color: {p.info};
      }}

      .f8-agent-card-status.completed {{
        color: {p.success};
        border-color: {p.success};
      }}

      .f8-agent-card-status.failed {{
        color: {p.error};
        border-color: {p.error};
      }}

      .f8-agent-card-body {{
        margin-top: 4px;
        color: {p.text_muted};
        overflow-wrap: anywhere;
      }}

      .f8-agent-card-actions {{
        display: flex;
        justify-content: flex-end;
        gap: 6px;
        margin-top: 8px;
      }}

      .f8-agent-card-button {{
        border: 1px solid {p.border};
        border-radius: 5px;
        background: {p.button_bg};
        color: {p.text_primary};
        padding: 4px 9px;
        font-size: 11px;
        cursor: pointer;
      }}

      .f8-agent-card-button:hover {{
        background: {p.button_hover_bg};
      }}

      .f8-agent-card-button.primary {{
        border-color: {p.success};
        color: {p.success};
      }}

      .f8-agent-card-button.danger {{
        border-color: {p.error};
        color: {p.error};
      }}

      .f8-agent-card-button:disabled {{
        opacity: 0.55;
        cursor: default;
      }}
    """

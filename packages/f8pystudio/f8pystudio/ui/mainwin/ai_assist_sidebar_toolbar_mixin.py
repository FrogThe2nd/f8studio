from __future__ import annotations

import logging
from typing import Protocol, cast

from qtpy import QtWidgets

from ...agents.codeact import StudioAgentSkillStatus
from ...agents.graph_context import GraphContextSnapshot
from ..support.ai_context_controls import set_status_label_text

logger = logging.getLogger(__name__)


class _AiAssistSidebarToolbarHost(Protocol):
    _selection_mode: str
    _current_selected_snapshot_preview: GraphContextSnapshot | None
    _graph_tool_count: int
    _graph_tool_names: tuple[str, ...]
    _graph_skill_statuses: tuple[StudioAgentSkillStatus, ...]
    _selected_node_label: QtWidgets.QLabel
    _tools_button: QtWidgets.QToolButton
    _tools_menu: QtWidgets.QMenu
    _skills_button: QtWidgets.QToolButton
    _skills_menu: QtWidgets.QMenu


class AiAssistSidebarToolbarMixin:
    def _refresh_context_toolbar(self) -> None:
        host = cast(_AiAssistSidebarToolbarHost, self)
        selection_mode = host._selection_mode
        selected_snapshot = host._current_selected_snapshot_preview

        if selection_mode == "active" and selected_snapshot is not None:
            selected_text = f"Sel: {selected_snapshot.selection_label}"
            selected_tooltip = (
                f"Selected nodes: {selected_snapshot.total_selected_count}\n"
                f"One-hop context nodes: {selected_snapshot.total_one_hop_count}\n"
                f"Included connections: {selected_snapshot.total_connection_count}"
            )
        else:
            selected_text = "Sel: none"
            selected_tooltip = "Select one or more nodes; graph tools can query the current selection."
        set_status_label_text(host._selected_node_label, selected_text, max_width=host._selected_node_label.maximumWidth())
        host._selected_node_label.setToolTip(selected_tooltip)

        tool_count = int(host._graph_tool_count)
        if tool_count > 0:
            host._tools_button.setText(f"Tools: {tool_count}")
            host._tools_button.setToolTip(
                "PyStudio graph, library, runtime, monitor, and log tools are available to the chat agent."
            )
        else:
            host._tools_button.setText("Tools: off")
            host._tools_button.setToolTip("Graph tools are not available because no Studio graph was attached to this sidebar.")

        skill_statuses = host._graph_skill_statuses
        enabled_skill_count = len([status for status in skill_statuses if status.enabled and status.available])
        if skill_statuses:
            host._skills_button.setText(f"Skills: {enabled_skill_count}/{len(skill_statuses)}")
            host._skills_button.setToolTip(_skills_tooltip(skill_statuses))
        else:
            host._skills_button.setText("Skills: off")
            host._skills_button.setToolTip("No agent skills are attached to this sidebar yet.")

    def _populate_graph_tools_menu(self) -> None:
        host = cast(_AiAssistSidebarToolbarHost, self)
        menu = host._tools_menu
        menu.clear()

        if host._graph_tool_names:
            for tool_name in host._graph_tool_names:
                action = menu.addAction(str(tool_name))
                action.setEnabled(False)
        else:
            action = menu.addAction("No graph tools available")
            action.setEnabled(False)

    def _populate_graph_skills_menu(self) -> None:
        host = cast(_AiAssistSidebarToolbarHost, self)
        menu = host._skills_menu
        menu.clear()

        if host._graph_skill_statuses:
            for skill_status in host._graph_skill_statuses:
                action = menu.addAction(skill_status.label())
                tooltip_parts = [skill_status.description]
                if skill_status.reason:
                    tooltip_parts.append(skill_status.reason)
                action.setToolTip("\n".join(tooltip_parts))
                action.setEnabled(False)
        else:
            action = menu.addAction("No skills attached")
            action.setEnabled(False)


def _skills_tooltip(skill_statuses: tuple[StudioAgentSkillStatus, ...]) -> str:
    lines: list[str] = []
    for skill_status in skill_statuses:
        line = skill_status.label()
        if skill_status.reason:
            line = f"{line} - {skill_status.reason}"
        lines.append(line)
    return "\n".join(lines)

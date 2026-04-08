from __future__ import annotations

import json
from typing import Any

from f8pysdk.specs import F8OperatorSpec, F8ServiceSpec
from f8pysdk.codec import dump_json
from qtpy import QtCore, QtWidgets

from ..support.json_text_editor import attach_json_enhancements

SpecTemplate = F8OperatorSpec | F8ServiceSpec


def schema_to_json_text(schema_obj: Any) -> str:
    if schema_obj is None:
        return "{}"
    try:
        payload = dump_json(schema_obj, mode="json", by_alias=True)
    except (TypeError, ValueError):
        payload = schema_obj
    try:
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        return str(payload)


def md_code_block(text: str) -> str:
    body = str(text or "").strip()
    if not body:
        body = "{}"
    return f"```json\n{body}\n```"


def render_data_ports_md(title: str, ports: list[Any]) -> str:
    lines: list[str] = [f"## {title}"]
    if not ports:
        lines.append("_None_")
        return "\n".join(lines)
    for port in ports:
        lines.append(f"### `{port.name}`")
        lines.append(f"- **Description**: {port.description or ''}")
        lines.append(f"- **Required**: `{bool(port.required)}`")
        lines.append("**Schema**")
        lines.append(md_code_block(schema_to_json_text(port.valueSchema)))
    return "\n".join(lines)


def render_state_fields_md(fields: list[Any]) -> str:
    lines: list[str] = ["## State Fields"]
    if not fields:
        lines.append("_None_")
        return "\n".join(lines)
    for field in fields:
        lines.append(f"### `{field.name}`")
        lines.append(f"- **Label**: {field.label or ''}")
        lines.append(f"- **Access**: `{field.access}`")
        lines.append(f"- **Required**: `{bool(field.required)}`")
        lines.append(f"- **Show On Node**: `{bool(field.showOnNode)}`")
        lines.append(f"- **Description**: {field.description or ''}")
        lines.append("- **Schema**:")
        lines.append(md_code_block(schema_to_json_text(field.valueSchema)))
    return "\n".join(lines)


def render_operator_doc(spec: F8OperatorSpec) -> str:
    lines: list[str] = [f"# {spec.label or spec.operatorClass}"]
    lines.append(f"**Operator Class**: `{spec.operatorClass}`  ")
    lines.append(f"**Service Class**: `{spec.serviceClass}`  ")
    lines.append(f"**Version**: `{spec.version or ''}`")
    lines.append("")
    lines.append(spec.description or "_No description._")
    lines.append("")
    tags = ", ".join(str(tag) for tag in list(spec.tags or []))
    lines.append(f"**Tags**: {tags or '_none_'}")
    lines.append("")
    lines.append("## Exec Ports")
    lines.append(f"- **In**: {', '.join(str(port) for port in list(spec.execInPorts or [])) or '_none_'}")
    lines.append(f"- **Out**: {', '.join(str(port) for port in list(spec.execOutPorts or [])) or '_none_'}")
    lines.append("")
    lines.append("")
    lines.append(render_data_ports_md("Data In Ports", list(spec.dataInPorts or [])))
    lines.append("")
    lines.append(render_data_ports_md("Data Out Ports", list(spec.dataOutPorts or [])))
    lines.append("")
    lines.append(render_state_fields_md(list(spec.stateFields or [])))
    return "\n".join(lines)


def render_service_doc(spec: F8ServiceSpec) -> str:
    lines: list[str] = [f"# {spec.label or spec.serviceClass}"]
    lines.append(f"**Service Class**: `{spec.serviceClass}`  ")
    lines.append(f"**Version**: `{spec.version or ''}`")
    lines.append("")
    lines.append(spec.description or "_No description._")
    lines.append("")
    tags = ", ".join(str(tag) for tag in list(spec.tags or []))
    lines.append(f"**Tags**: {tags or '_none_'}")
    lines.append("")
    lines.append(render_data_ports_md("Data In Ports", list(spec.dataInPorts or [])))
    lines.append("")
    lines.append(render_data_ports_md("Data Out Ports", list(spec.dataOutPorts or [])))
    lines.append("")
    lines.append(render_state_fields_md(list(spec.stateFields or [])))
    lines.append("")
    lines.append("## Commands")
    commands = list(spec.commands or [])
    if not commands:
        lines.append("_None_")
    else:
        for command in commands:
            lines.append(f"### `{command.name}`")
            lines.append(f"- **Description**: {command.description or ''}")
    return "\n".join(lines)


def show_node_docs_dialog(*, parent: QtWidgets.QWidget | None, spec: SpecTemplate, node_id: str, node_name: str) -> None:
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle(f"Node Info - {node_name}")
    dialog.resize(860, 620)

    title = QtWidgets.QLabel(f"{node_name}  ({node_id})", dialog)
    title.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)

    tabs = QtWidgets.QTabWidget(dialog)
    overview = QtWidgets.QTextBrowser(dialog)
    overview.setOpenExternalLinks(False)
    overview.setOpenLinks(False)
    overview.setStyleSheet(
        "QTextBrowser {"
        "  background: #1f2329;"
        "  border: 1px solid #2d333b;"
        "  color: #e6edf3;"
        "  font-size: 12px;"
        "}"
    )
    raw = QtWidgets.QPlainTextEdit(dialog)
    raw.setReadOnly(True)
    raw.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
    attach_json_enhancements(raw, read_only=True)

    if isinstance(spec, F8OperatorSpec):
        overview.setMarkdown(render_operator_doc(spec))
    else:
        overview.setMarkdown(render_service_doc(spec))

    raw.setPlainText(json.dumps(dump_json(spec, mode="json", by_alias=True), ensure_ascii=False, indent=2, default=str))

    tabs.addTab(overview, "Overview")
    tabs.addTab(raw, "Raw JSON")

    close_btn = QtWidgets.QPushButton("Close", dialog)
    close_btn.clicked.connect(dialog.accept)  # type: ignore[attr-defined]

    layout = QtWidgets.QVBoxLayout(dialog)
    layout.addWidget(title)
    layout.addWidget(tabs, 1)
    layout.addWidget(close_btn, 0, QtCore.Qt.AlignRight)
    dialog.exec()

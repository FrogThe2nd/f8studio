from __future__ import annotations

import keyword
import re
from typing import Any


def _normalize_type_name(raw_name: str, *, fallback: str) -> str:
    txt = str(raw_name or "").strip()
    if not txt:
        txt = fallback
    txt = re.sub(r"[^A-Za-z0-9_]", "_", txt)
    if not txt:
        txt = fallback
    if txt[0].isdigit():
        txt = f"_{txt}"
    return txt


def _is_valid_identifier(name: str) -> bool:
    return bool(name and name.isidentifier() and not keyword.iskeyword(name))


def _schema_type_name(
    schema_obj: Any,
    *,
    base_name: str,
    class_blocks: list[str],
    used_names: set[str],
    in_progress: set[int],
) -> str:
    if not isinstance(schema_obj, dict):
        return "Any"

    schema_id = id(schema_obj)
    if schema_id in in_progress:
        return "Any"
    in_progress.add(schema_id)

    schema_type = str(schema_obj.get("type") or "any").strip().lower()
    try:
        if schema_type == "string":
            return "str"
        if schema_type == "integer":
            return "int"
        if schema_type == "number":
            return "float"
        if schema_type == "boolean":
            return "bool"
        if schema_type == "null":
            return "None"
        if schema_type == "any":
            return "Any"
        if schema_type == "array":
            items_schema = schema_obj.get("items")
            item_name = _schema_type_name(
                items_schema,
                base_name=f"{base_name}_item",
                class_blocks=class_blocks,
                used_names=used_names,
                in_progress=in_progress,
            )
            return f"list[{item_name}]"
        if schema_type != "object":
            return "Any"

        properties = schema_obj.get("properties")
        if not isinstance(properties, dict):
            return "_F8ObjectView"

        required_raw = schema_obj.get("required")
        required_fields = (
            {str(item or "").strip() for item in required_raw}
            if isinstance(required_raw, list)
            else set()
        )

        class_name_base = _normalize_type_name(f"{base_name}_obj", fallback="DynamicObject")
        class_name = class_name_base
        suffix = 1
        while class_name in used_names:
            class_name = f"{class_name_base}_{suffix}"
            suffix += 1
        used_names.add(class_name)

        block_lines = [f"class {class_name}(_F8ObjectView):"]
        valid_attr_count = 0
        skipped_names: list[str] = []
        for prop_name_raw, prop_schema in properties.items():
            prop_name = str(prop_name_raw or "").strip()
            if not prop_name:
                continue
            prop_type = _schema_type_name(
                prop_schema,
                base_name=f"{class_name}_{prop_name}",
                class_blocks=class_blocks,
                used_names=used_names,
                in_progress=in_progress,
            )
            if prop_name not in required_fields:
                prop_type = f"{prop_type} | None"
            if _is_valid_identifier(prop_name):
                block_lines.append(f"    {prop_name}: {prop_type}")
                valid_attr_count += 1
            else:
                skipped_names.append(prop_name)

        if valid_attr_count == 0:
            block_lines.append("    pass")
        if skipped_names:
            block_lines.append("")
            block_lines.append("    # Non-identifier keys are accessible only via mapping methods.")
            for skipped in skipped_names:
                block_lines.append(f"    # - {skipped!r}")

        class_blocks.append("\n".join(block_lines))
        return class_name
    finally:
        in_progress.discard(schema_id)


def build_dynamic_inputs_stub(
    *,
    type_name: str,
    data_in_ports: tuple[Any, ...],
) -> str:
    """Build a per-node `F8Inputs` stub module for pyright/monaco."""

    root_type_name = _normalize_type_name(type_name, fallback="F8Inputs")
    class_blocks: list[str] = []
    attribute_lines: list[str] = []
    skipped_names: list[str] = []
    used_names: set[str] = set()
    in_progress: set[int] = set()

    for raw_port in data_in_ports:
        name = str(getattr(raw_port, "name", "") or "").strip()
        if not name:
            continue
        required = bool(getattr(raw_port, "required", True))
        value_schema = getattr(raw_port, "value_schema", None)
        value_type = _schema_type_name(
            value_schema,
            base_name=f"{root_type_name}_{name}",
            class_blocks=class_blocks,
            used_names=used_names,
            in_progress=in_progress,
        )
        if not required:
            value_type = f"{value_type} | None"
        if _is_valid_identifier(name):
            attribute_lines.append(f"    {name}: {value_type}")
        else:
            skipped_names.append(name)

    lines: list[str] = [
        "from __future__ import annotations",
        "",
        "from typing import Any, ItemsView, Iterator, KeysView, ValuesView",
        "",
        "class _F8ObjectView:",
        '    """Base mapping/object view for dynamic payload values."""',
        "    def __getitem__(self, key: str) -> Any: ...",
        "    def get(self, key: str, default: Any = None) -> Any: ...",
        "    def keys(self) -> KeysView[str]: ...",
        "    def items(self) -> ItemsView[str, Any]: ...",
        "    def values(self) -> ValuesView[Any]: ...",
        "    def __contains__(self, key: object) -> bool: ...",
        "    def __iter__(self) -> Iterator[str]: ...",
        "    def __len__(self) -> int: ...",
        "    def to_dict(self) -> dict[str, Any]: ...",
        "",
    ]

    if class_blocks:
        lines.extend(class_blocks)
        lines.append("")

    lines.extend(
        [
            f"class {root_type_name}(_F8ObjectView):",
            '    """Dynamic input payload view for python_script hooks."""',
            "    pass",
        ]
    )

    if attribute_lines:
        lines.append("")
        lines.extend(attribute_lines)

    if skipped_names:
        lines.append("")
        lines.append("    # Non-identifier input names are accessible only via mapping methods.")
        for skipped in skipped_names:
            lines.append(f"    # - {skipped!r}")

    lines.append("")
    return "\n".join(lines)

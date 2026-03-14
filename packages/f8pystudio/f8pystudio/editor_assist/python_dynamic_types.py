from __future__ import annotations

import keyword
import re
from typing import Any


def _escape_docstring(text: str) -> str:
    return str(text or "").replace('"""', '\\"\\"\\"').strip()


def _single_line_comment(text: str) -> str:
    return " ".join(str(text or "").split()).strip()


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


def _attribute_name_for_key(name: str) -> str | None:
    if not name or not name.isidentifier():
        return None
    if keyword.iskeyword(name):
        alias = f"{name}_"
        if alias.isidentifier() and not keyword.iskeyword(alias):
            return alias
        return None
    return name


def _optional_type_name(type_name: str) -> str:
    raw = str(type_name or "").strip()
    if raw == "None":
        return raw
    if raw.endswith(" | None") or raw.startswith("None | "):
        return raw
    return f"{raw} | None"


def _schema_type_name(
    schema_obj: Any,
    *,
    base_name: str,
    class_blocks: list[str],
    used_names: set[str],
    in_progress: set[int],
    field_doc: str = "",
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
        doc = _escape_docstring(field_doc)
        if doc:
            block_lines.append(f'    """Nested object view for {doc}."""')
        valid_attr_count = 0
        attr_names_in_class: set[str] = set()
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
            prop_attr = _attribute_name_for_key(prop_name)
            if prop_attr is not None and prop_attr not in attr_names_in_class:
                block_lines.append(f"    {prop_attr}: {prop_type}")
                attr_names_in_class.add(prop_attr)
                valid_attr_count += 1
            else:
                skipped_names.append(prop_name)

        if valid_attr_count == 0 and doc:
            block_lines.append("    ...")
        elif valid_attr_count == 0:
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
    node_description: str = "",
) -> str:
    """Build a per-node `F8Inputs` stub module for pyright/monaco."""

    return _build_dynamic_mapping_stub(
        type_name=type_name,
        fallback_type_name="F8Inputs",
        root_doc="Dynamic input payload view for python_script hooks.",
        skipped_doc="Non-identifier input names are accessible only via mapping methods.",
        fields=data_in_ports,
        guard_prefix="is_port",
        guard_selector_name="port",
        node_description=node_description,
    )


def build_dynamic_outputs_stub(
    *,
    type_name: str,
    data_out_ports: tuple[Any, ...],
    node_description: str = "",
) -> str:
    """Build a per-node `F8Outputs` stub module for pyright/monaco."""

    return _build_dynamic_mapping_stub(
        type_name=type_name,
        fallback_type_name="F8Outputs",
        root_doc="Dynamic output payload view for python_script hooks.",
        skipped_doc="Non-identifier output names are accessible only via mapping methods.",
        fields=data_out_ports,
        guard_prefix="is_output",
        guard_selector_name="port",
        node_description=node_description,
    )


def build_dynamic_states_stub(
    *,
    type_name: str,
    state_fields: tuple[Any, ...],
    node_description: str = "",
) -> str:
    """Build a per-node `F8States` stub module for pyright/monaco."""

    return _build_dynamic_mapping_stub(
        type_name=type_name,
        fallback_type_name="F8States",
        root_doc="Dynamic state snapshot view for script contexts.",
        skipped_doc="Non-identifier state names are accessible only via mapping methods.",
        fields=state_fields,
        guard_prefix="is_state",
        guard_selector_name="field",
        node_description=node_description,
    )


def _build_dynamic_mapping_stub(
    *,
    type_name: str,
    fallback_type_name: str,
    root_doc: str,
    skipped_doc: str,
    fields: tuple[Any, ...],
    guard_prefix: str | None,
    guard_selector_name: str,
    node_description: str = "",
) -> str:
    root_type_name = _normalize_type_name(type_name, fallback=fallback_type_name)

    class_blocks: list[str] = []
    attribute_lines: list[str] = []
    keyword_attribute_lines: list[str] = []
    skipped_names: list[tuple[str, str]] = []
    type_guards: list[tuple[str, str]] = []
    occupied_attr_names: set[str] = set()
    occupied_guard_names: set[str] = set()
    used_names: set[str] = set()
    in_progress: set[int] = set()

    for raw_field in fields:
        name = str(getattr(raw_field, "name", "") or "").strip()
        if not name:
            continue
        field_description = str(getattr(raw_field, "description", "") or "").strip()
        value_schema = getattr(raw_field, "value_schema", None)
        value_type = _optional_type_name(
            _schema_type_name(
                value_schema,
                base_name=f"{root_type_name}_{name}",
                class_blocks=class_blocks,
                used_names=used_names,
                in_progress=in_progress,
                field_doc=field_description or name,
            )
        )
        if guard_prefix:
            alias_base = _normalize_type_name(name, fallback=guard_selector_name)
            guard_name_base = f"{guard_prefix}_{alias_base}"
            guard_name = guard_name_base
            guard_index = 1
            while guard_name in occupied_guard_names:
                guard_name = f"{guard_name_base}_{guard_index}"
                guard_index += 1
            occupied_guard_names.add(guard_name)
            type_guards.append((guard_name, value_type))
        attr_name = _attribute_name_for_key(name)
        if attr_name is None:
            skipped_names.append((name, field_description))
            continue
        if attr_name in occupied_attr_names:
            skipped_names.append((name, field_description))
            continue
        occupied_attr_names.add(attr_name)
        comment = _single_line_comment(field_description)
        if attr_name == name:
            if comment:
                attribute_lines.append(f"    # {comment}")
            attribute_lines.append(f"    {attr_name}: {value_type}")
        else:
            if comment:
                keyword_attribute_lines.append(f"    # {comment}")
            keyword_attribute_lines.append(f"    {attr_name}: {value_type}")

    lines: list[str] = [
        "from __future__ import annotations",
        "",
        "from typing import Any, ItemsView, Iterator, KeysView, TypeGuard, ValuesView",
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

    root_doc_parts = [_escape_docstring(root_doc)]
    node_doc = _escape_docstring(node_description)
    if node_doc:
        root_doc_parts.append(f"Node: {node_doc}")
    lines.extend(
        [
            f"class {root_type_name}(_F8ObjectView):",
            f'    """{" ".join(part for part in root_doc_parts if part)}"""',
            "    pass",
        ]
    )

    if attribute_lines:
        lines.append("")
        lines.extend(attribute_lines)
    if keyword_attribute_lines:
        lines.append("")
        lines.extend(keyword_attribute_lines)

    if skipped_names:
        lines.append("")
        lines.append(f"    # {skipped_doc}")
        for skipped_name, skipped_desc in skipped_names:
            if skipped_desc:
                lines.append(f"    # - {skipped_name!r}: {_single_line_comment(skipped_desc)}")
            else:
                lines.append(f"    # - {skipped_name!r}")

    if type_guards:
        lines.append("")
        for guard_name, guard_type in type_guards:
            lines.append(f"def {guard_name}(value: Any, {guard_selector_name}: str) -> TypeGuard[{guard_type}]: ...")

    lines.append("")
    return "\n".join(lines)

from __future__ import annotations

from urllib.parse import quote, unquote


VARIANT_NODE_TYPE_PREFIX = "__variant__."


def build_variant_node_type(variant_id: str) -> str:
    value = str(variant_id or "").strip()
    if not value:
        return VARIANT_NODE_TYPE_PREFIX
    return f"{VARIANT_NODE_TYPE_PREFIX}{quote(value, safe='')}"


def is_variant_node_type(node_type: str) -> bool:
    value = str(node_type or "").strip()
    return value.startswith(VARIANT_NODE_TYPE_PREFIX)


def parse_variant_node_type(node_type: str) -> str | None:
    value = str(node_type or "").strip()
    if value.startswith(VARIANT_NODE_TYPE_PREFIX):
        raw = value[len(VARIANT_NODE_TYPE_PREFIX) :]
        if not raw:
            return None
        return unquote(raw) or None
    return None

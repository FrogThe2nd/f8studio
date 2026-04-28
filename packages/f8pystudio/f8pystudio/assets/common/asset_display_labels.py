from __future__ import annotations

from ..components.component_models import F8ComponentEntry, F8ComponentSourceKind
from ..variants.variant_models import F8VariantEntry, F8VariantSourceKind


def variant_search_display_name(entry: F8VariantEntry, *, base_node_name: str) -> str:
    variant_name = str(entry.record.name or "").strip() or str(entry.record.variantId or "").strip() or "Variant"
    return f"{base_node_name} | {variant_name}{_entry_suffix(owner_display_name=entry.ownerDisplayName, is_local=entry.source == F8VariantSourceKind.local)}"


def variant_tree_display_name(entry: F8VariantEntry) -> str:
    variant_name = str(entry.record.name or "").strip() or str(entry.record.variantId or "").strip() or "Variant"
    return f"|{variant_name}{_entry_suffix(owner_display_name=entry.ownerDisplayName, is_local=entry.source == F8VariantSourceKind.local)}|"


def component_search_display_name(entry: F8ComponentEntry) -> str:
    component_name = str(entry.record.name or "").strip() or str(entry.record.componentId or "").strip() or "Component"
    return f"Component | {component_name}{_entry_suffix(owner_display_name=entry.ownerDisplayName, is_local=entry.source == F8ComponentSourceKind.local)}"


def _entry_suffix(*, owner_display_name: str | None, is_local: bool) -> str:
    if is_local:
        return " (Draft)"
    owner_text = str(owner_display_name or "").strip()
    if owner_text:
        return f" (by {owner_text})"
    return ""

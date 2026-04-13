from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Any, Iterable


BASE_LAYER_ID = "base"
DEFAULT_LAYER_COLOR = "#64748B"
_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


@dataclass(frozen=True)
class F8LayerDef:
    id: str
    label: str
    description: str = ""
    color: str = DEFAULT_LAYER_COLOR
    default_visible: bool = True
    is_base: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "color": self.color,
            "defaultVisible": self.default_visible,
            "isBase": self.is_base,
        }


def base_layer_def(
    *,
    label: str = "Base",
    description: str = "Default base layer for unassigned nodes.",
    color: str = DEFAULT_LAYER_COLOR,
    default_visible: bool = True,
) -> F8LayerDef:
    return F8LayerDef(
        id=BASE_LAYER_ID,
        label=str(label or "Base").strip() or "Base",
        description=str(description or "").strip() or "Default base layer for unassigned nodes.",
        color=_normalize_color(color),
        default_visible=bool(default_visible),
        is_base=True,
    )


def _normalize_color(value: object) -> str:
    color = str(value or "").strip()
    if _HEX_COLOR_RE.match(color):
        return color.upper()
    return DEFAULT_LAYER_COLOR


def normalize_layer_id(value: object, *, fallback: str = "layer") -> str:
    raw = str(value or "").strip().lower()
    if raw == BASE_LAYER_ID:
        return BASE_LAYER_ID
    token = "".join(ch if ch.isalnum() else "_" for ch in raw)
    token = re.sub(r"_+", "_", token).strip("_")
    return token or str(fallback or "layer")


def next_unique_layer_id(base_id: str, used_ids: set[str]) -> str:
    root = normalize_layer_id(base_id, fallback="layer")
    if root == BASE_LAYER_ID:
        root = "layer"
    if root not in used_ids:
        return root
    suffix = 2
    while True:
        candidate = f"{root}_{suffix}"
        if candidate not in used_ids:
            return candidate
        suffix += 1


def coerce_layer_def(value: object, *, fallback_index: int = 0, used_ids: set[str] | None = None) -> F8LayerDef | None:
    if isinstance(value, F8LayerDef):
        layer_id = normalize_layer_id(value.id, fallback=f"layer_{fallback_index + 1}")
        is_base = layer_id == BASE_LAYER_ID or bool(value.is_base)
        if is_base:
            return base_layer_def(
                label=value.label,
                description=value.description,
                color=value.color,
                default_visible=value.default_visible,
            )
        unique_id = layer_id
        if used_ids is not None:
            unique_id = next_unique_layer_id(layer_id, used_ids)
            used_ids.add(unique_id)
        label = str(value.label or "").strip() or unique_id
        return F8LayerDef(
            id=unique_id,
            label=label,
            description=str(value.description or "").strip(),
            color=_normalize_color(value.color),
            default_visible=bool(value.default_visible),
            is_base=False,
        )
    if not isinstance(value, dict):
        return None
    layer_id = normalize_layer_id(value.get("id"), fallback=f"layer_{fallback_index + 1}")
    is_base = layer_id == BASE_LAYER_ID or bool(value.get("isBase"))
    if is_base:
        return base_layer_def(
            label=str(value.get("label") or "Base"),
            description=str(value.get("description") or ""),
            color=value.get("color"),
            default_visible=bool(value.get("defaultVisible", True)),
        )
    unique_id = layer_id
    if used_ids is not None:
        unique_id = next_unique_layer_id(layer_id, used_ids)
        used_ids.add(unique_id)
    label = str(value.get("label") or "").strip() or unique_id
    description = str(value.get("description") or "").strip()
    color = _normalize_color(value.get("color"))
    default_visible = bool(value.get("defaultVisible", True))
    return F8LayerDef(
        id=unique_id,
        label=label,
        description=description,
        color=color,
        default_visible=default_visible,
        is_base=False,
    )


def normalize_layer_defs(values: object) -> tuple[F8LayerDef, ...]:
    used_ids: set[str] = {BASE_LAYER_ID}
    base_layer = base_layer_def()
    out: list[F8LayerDef] = [base_layer]
    seen_non_base: set[str] = set()
    if isinstance(values, dict):
        iterable: Iterable[object] = list(values.values())
    elif isinstance(values, (list, tuple)):
        iterable = list(values)
    else:
        iterable = []
    for index, raw in enumerate(iterable):
        layer = coerce_layer_def(raw, fallback_index=index, used_ids=used_ids)
        if layer is None:
            continue
        if layer.id == BASE_LAYER_ID:
            out[0] = layer
            continue
        if layer.id in seen_non_base:
            continue
        seen_non_base.add(layer.id)
        out.append(layer)
    return tuple(out)


def layer_defs_to_json(layer_defs: Iterable[F8LayerDef]) -> list[dict[str, Any]]:
    return [layer.to_json() for layer in normalize_layer_defs(list(layer_defs))]


def normalize_layer_ids(
    values: object,
    *,
    known_layer_ids: Iterable[str] | None = None,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    known = {str(item or "").strip() for item in list(known_layer_ids or []) if str(item or "").strip()}
    ordered: list[str] = []
    seen: set[str] = set()
    if isinstance(values, (list, tuple)):
        for raw in list(values):
            layer_id = normalize_layer_id(raw, fallback="")
            if not layer_id:
                continue
            if known and layer_id not in known:
                continue
            if layer_id in seen:
                continue
            seen.add(layer_id)
            ordered.append(layer_id)
    if not ordered:
        if allow_empty:
            return ()
        return (BASE_LAYER_ID,)
    return tuple(ordered)


def layer_defs_equal(left: F8LayerDef, right: F8LayerDef) -> bool:
    return left.to_json() == right.to_json()


def merge_layer_defs(
    current_defs: Iterable[F8LayerDef],
    imported_defs: Iterable[F8LayerDef],
) -> tuple[tuple[F8LayerDef, ...], dict[str, str]]:
    merged = list(normalize_layer_defs(list(current_defs)))
    merged_by_id = {layer.id: layer for layer in merged}
    used_ids = {layer.id for layer in merged}
    remap: dict[str, str] = {BASE_LAYER_ID: BASE_LAYER_ID}

    for imported in normalize_layer_defs(list(imported_defs)):
        if imported.id == BASE_LAYER_ID:
            remap[imported.id] = BASE_LAYER_ID
            continue
        existing = merged_by_id.get(imported.id)
        if existing is None:
            merged.append(imported)
            merged_by_id[imported.id] = imported
            used_ids.add(imported.id)
            remap[imported.id] = imported.id
            continue
        if layer_defs_equal(existing, imported):
            remap[imported.id] = imported.id
            continue
        new_id = next_unique_layer_id(imported.id, used_ids)
        used_ids.add(new_id)
        renamed = replace(imported, id=new_id)
        merged.append(renamed)
        merged_by_id[new_id] = renamed
        remap[imported.id] = new_id

    return tuple(merged), remap


def extract_node_layer_ids_from_ui_state(ui_state: object, *, known_layer_ids: Iterable[str] | None = None) -> tuple[str, ...]:
    if not isinstance(ui_state, dict):
        return (BASE_LAYER_ID,)
    return normalize_layer_ids(ui_state.get("layerIds"), known_layer_ids=known_layer_ids)


def set_node_layer_ids_in_ui_state(
    ui_state: object,
    *,
    layer_ids: Iterable[str],
    known_layer_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    output = dict(ui_state) if isinstance(ui_state, dict) else {}
    normalized = normalize_layer_ids(list(layer_ids), known_layer_ids=known_layer_ids)
    if normalized == (BASE_LAYER_ID,):
        output.pop("layerIds", None)
        return output
    output["layerIds"] = list(normalized)
    return output


def layout_layer_defs_from_layout(layout_data: dict[str, Any]) -> tuple[F8LayerDef, ...]:
    return normalize_layer_defs(layout_data.get("f8_layers"))


def augment_layer_defs_for_layout_nodes(
    layer_defs: Iterable[F8LayerDef],
    layout_nodes: object,
) -> tuple[F8LayerDef, ...]:
    normalized = list(normalize_layer_defs(list(layer_defs)))
    known_ids = {layer.id for layer in normalized}
    used_ids = set(known_ids)
    if not isinstance(layout_nodes, dict):
        return tuple(normalized)

    for node_data in list(layout_nodes.values()):
        if not isinstance(node_data, dict):
            continue
        layer_ids = extract_node_layer_ids_from_ui_state(node_data.get("f8_ui_state"))
        for layer_id in layer_ids:
            if layer_id in known_ids or layer_id == BASE_LAYER_ID:
                continue
            new_id = next_unique_layer_id(layer_id, used_ids)
            used_ids.add(new_id)
            known_ids.add(new_id)
            normalized.append(
                F8LayerDef(
                    id=new_id,
                    label=str(layer_id),
                    description="Recovered from node layerIds without a matching layer definition.",
                    color=DEFAULT_LAYER_COLOR,
                    default_visible=True,
                    is_base=False,
                )
            )
    return tuple(normalized)


def remap_layout_node_layer_ids(layout_nodes: object, layer_id_remap: dict[str, str]) -> None:
    if not isinstance(layout_nodes, dict):
        return
    known_layer_ids = {
        str(target or "").strip()
        for target in list(layer_id_remap.values())
        if str(target or "").strip()
    }
    if BASE_LAYER_ID not in known_layer_ids:
        known_layer_ids.add(BASE_LAYER_ID)
    for node_data in list(layout_nodes.values()):
        if not isinstance(node_data, dict):
            continue
        ui_state = node_data.get("f8_ui_state")
        current_ids = extract_node_layer_ids_from_ui_state(ui_state)
        remapped_ids: list[str] = []
        for layer_id in current_ids:
            remapped_ids.append(layer_id_remap.get(layer_id, layer_id))
        node_data["f8_ui_state"] = set_node_layer_ids_in_ui_state(
            ui_state,
            layer_ids=remapped_ids,
            known_layer_ids=known_layer_ids,
        )

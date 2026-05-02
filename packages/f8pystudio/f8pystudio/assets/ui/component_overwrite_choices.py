from __future__ import annotations

from ..components.component_models import F8ComponentEntry
from .project_asset_dialogs import AssetOverwriteChoice


def component_draft_overwrite_choice(entry: F8ComponentEntry) -> AssetOverwriteChoice:
    component_id = str(entry.record.componentId or "").strip()
    component_name = str(entry.record.name or "").strip() or component_id or "Component"
    origin_asset_id = str(entry.draftOriginAssetId or "").strip()
    origin_version = entry.draftOriginVersionNumber
    draft_kind = "Linked Local Draft" if origin_asset_id else "Local Draft"
    tooltip_lines = [
        draft_kind,
        f"Draft ID: {component_id}",
    ]
    if origin_asset_id:
        tooltip_lines.append(f"Linked cloud component: {origin_asset_id}")
    if origin_version is not None:
        tooltip_lines.append(f"Linked base version: v{int(origin_version)}")
    return AssetOverwriteChoice(
        asset_id=component_id,
        label=component_name,
        description=str(entry.record.description),
        tags=[str(tag) for tag in list(entry.record.tags or []) if str(tag).strip()],
        display_label=f"{component_name} ({draft_kind})",
        tooltip="\n".join(tooltip_lines),
    )


__all__ = ["component_draft_overwrite_choice"]

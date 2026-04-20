from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AssetCatalogPresence(Enum):
    local = "local"
    remote = "remote"
    both = "both"


@dataclass(frozen=True, slots=True)
class AssetCatalogRowState:
    asset_id: str
    has_local_head: bool
    has_remote_head: bool
    has_cached_remote_content: bool
    visibility: str | None
    owner_display_name: str | None
    subscribed: bool
    presence: AssetCatalogPresence

    @property
    def has_local_presence(self) -> bool:
        return self.has_local_head or self.has_cached_remote_content

    def badge_texts(self) -> list[str]:
        texts = [self.presence.value]
        visibility_key = self.visibility_icon_key()
        if visibility_key is not None:
            texts.append(visibility_key)
        return texts

    def visibility_icon_key(self) -> str | None:
        if not self.has_remote_head or not self.visibility:
            return None
        visibility = str(self.visibility).strip().lower()
        if visibility == "public":
            return "public"
        if visibility == "private":
            return "private"
        return None


def build_asset_catalog_row_state(
    *,
    asset_id: str,
    has_local_head: bool,
    has_remote_head: bool,
    has_cached_remote_content: bool,
    visibility: str | None,
    owner_display_name: str | None,
    subscribed: bool,
) -> AssetCatalogRowState:
    has_local_presence = bool(has_local_head or has_cached_remote_content)
    if has_local_presence and has_remote_head:
        presence = AssetCatalogPresence.both
    elif has_local_presence:
        presence = AssetCatalogPresence.local
    else:
        presence = AssetCatalogPresence.remote
    return AssetCatalogRowState(
        asset_id=str(asset_id),
        has_local_head=bool(has_local_head),
        has_remote_head=bool(has_remote_head),
        has_cached_remote_content=bool(has_cached_remote_content),
        visibility=None if visibility is None else str(visibility),
        owner_display_name=None if owner_display_name is None else str(owner_display_name),
        subscribed=bool(subscribed),
        presence=presence,
    )


__all__ = [
    "AssetCatalogPresence",
    "AssetCatalogRowState",
    "build_asset_catalog_row_state",
]

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AssetCatalogPresence(Enum):
    local = "local"
    remote = "remote"
    both = "both"


class AssetCatalogSyncHealth(Enum):
    synced = "synced"
    local_changes = "local changes"
    remote_newer = "remote newer"
    conflict = "conflict"


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
    sync_health: AssetCatalogSyncHealth | None
    local_version_number: int | None
    remote_version_number: int | None

    @property
    def has_local_presence(self) -> bool:
        return self.has_local_head or self.has_cached_remote_content

    def badge_texts(self) -> list[str]:
        badges = [self.presence.value]
        if self.has_remote_head and self.visibility:
            badges.append(self.visibility)
        if self.sync_health is not None:
            badges.append(self.sync_health.value)
        if self.local_version_number is not None:
            badges.append(f"L{int(self.local_version_number)}")
        if self.remote_version_number is not None:
            badges.append(f"R{int(self.remote_version_number)}")
        return badges


def build_asset_catalog_row_state(
    *,
    asset_id: str,
    has_local_head: bool,
    has_remote_head: bool,
    has_cached_remote_content: bool,
    visibility: str | None,
    owner_display_name: str | None,
    subscribed: bool,
    local_version_number: int | None,
    remote_version_number: int | None,
    local_sync_state: str | None,
    remote_sync_state: str | None,
) -> AssetCatalogRowState:
    has_local_presence = bool(has_local_head or has_cached_remote_content)
    if has_local_presence and has_remote_head:
        presence = AssetCatalogPresence.both
    elif has_local_presence:
        presence = AssetCatalogPresence.local
    else:
        presence = AssetCatalogPresence.remote
    sync_health = _sync_health_for_presence(
        presence=presence,
        local_sync_state=local_sync_state,
        remote_sync_state=remote_sync_state,
    )
    return AssetCatalogRowState(
        asset_id=str(asset_id),
        has_local_head=bool(has_local_head),
        has_remote_head=bool(has_remote_head),
        has_cached_remote_content=bool(has_cached_remote_content),
        visibility=None if visibility is None else str(visibility),
        owner_display_name=None if owner_display_name is None else str(owner_display_name),
        subscribed=bool(subscribed),
        presence=presence,
        sync_health=sync_health,
        local_version_number=local_version_number,
        remote_version_number=remote_version_number,
    )


def _sync_health_for_presence(
    *,
    presence: AssetCatalogPresence,
    local_sync_state: str | None,
    remote_sync_state: str | None,
) -> AssetCatalogSyncHealth | None:
    if presence != AssetCatalogPresence.both:
        return None
    for sync_state in (remote_sync_state, local_sync_state):
        if sync_state == "conflict":
            return AssetCatalogSyncHealth.conflict
    for sync_state in (local_sync_state, remote_sync_state):
        if sync_state == "modified_local":
            return AssetCatalogSyncHealth.local_changes
        if sync_state == "stale_remote":
            return AssetCatalogSyncHealth.remote_newer
    return AssetCatalogSyncHealth.synced


__all__ = [
    "AssetCatalogPresence",
    "AssetCatalogRowState",
    "AssetCatalogSyncHealth",
    "build_asset_catalog_row_state",
]

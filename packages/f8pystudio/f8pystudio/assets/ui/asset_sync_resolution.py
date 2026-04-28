from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AssetSyncDirection(Enum):
    push = "push"
    pull = "pull"
    conflict = "conflict"
    noop = "noop"


@dataclass(frozen=True, slots=True)
class AssetSyncDecision:
    direction: AssetSyncDirection
    local_changed: bool
    remote_changed: bool
    base_known: bool


def determine_asset_sync_direction(
    *,
    has_local_entry: bool,
    has_remote_entry: bool,
    local_version_number: int | None,
    remote_version_number: int | None,
    sync_base_remote_version_number: int | None,
    sync_base_local_version_number: int | None,
    current_remote_version_number: int | None,
) -> AssetSyncDecision:
    if has_local_entry and not has_remote_entry:
        return AssetSyncDecision(
            direction=AssetSyncDirection.push,
            local_changed=True,
            remote_changed=False,
            base_known=False,
        )
    if has_remote_entry and not has_local_entry:
        return AssetSyncDecision(
            direction=AssetSyncDirection.pull,
            local_changed=False,
            remote_changed=True,
            base_known=False,
        )
    if not has_local_entry or not has_remote_entry:
        return AssetSyncDecision(
            direction=AssetSyncDirection.noop,
            local_changed=False,
            remote_changed=False,
            base_known=False,
        )

    base_known = sync_base_remote_version_number is not None
    local_current_version = 1 if local_version_number is None else int(local_version_number)
    base_local_version = None if sync_base_local_version_number is None else int(sync_base_local_version_number)
    local_changed = base_local_version is None or local_current_version > base_local_version
    remote_changed = bool(base_known and sync_base_remote_version_number != current_remote_version_number)

    if not base_known:
        if local_version_number is not None and remote_version_number is not None:
            if int(local_version_number) > int(remote_version_number):
                return AssetSyncDecision(
                    direction=AssetSyncDirection.push,
                    local_changed=True,
                    remote_changed=False,
                    base_known=False,
                )
            if int(local_version_number) < int(remote_version_number):
                return AssetSyncDecision(
                    direction=AssetSyncDirection.pull,
                    local_changed=False,
                    remote_changed=True,
                    base_known=False,
                )
            return AssetSyncDecision(
                direction=AssetSyncDirection.noop,
                local_changed=False,
                remote_changed=False,
                base_known=False,
            )
        if local_changed:
            return AssetSyncDecision(
                direction=AssetSyncDirection.conflict,
                local_changed=True,
                remote_changed=True,
                base_known=False,
            )
        return AssetSyncDecision(
            direction=AssetSyncDirection.pull,
            local_changed=False,
            remote_changed=True,
            base_known=False,
        )

    if local_changed and remote_changed:
        return AssetSyncDecision(
            direction=AssetSyncDirection.conflict,
            local_changed=True,
            remote_changed=True,
            base_known=True,
        )
    if local_changed:
        return AssetSyncDecision(
            direction=AssetSyncDirection.push,
            local_changed=True,
            remote_changed=False,
            base_known=True,
        )
    if remote_changed:
        return AssetSyncDecision(
            direction=AssetSyncDirection.pull,
            local_changed=False,
            remote_changed=True,
            base_known=True,
        )
    return AssetSyncDecision(
        direction=AssetSyncDirection.noop,
        local_changed=False,
        remote_changed=False,
        base_known=True,
    )


__all__ = [
    "AssetSyncDecision",
    "AssetSyncDirection",
    "determine_asset_sync_direction",
]

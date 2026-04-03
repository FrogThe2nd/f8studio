from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast
REMOTE_CACHE_METADATA_SELECT_SQL = """
source, visibility, owner_user_id, owner_display_name, library_slug,
remote_revision, sync_state, downloaded_at, installed, subscribed
""".strip()

REMOTE_CACHE_METADATA_INSERT_COLUMNS_SQL = """
source, visibility, owner_user_id, owner_display_name, library_slug,
remote_revision, sync_state, downloaded_at, installed, subscribed
""".strip()

REMOTE_CACHE_METADATA_INSERT_VALUES_SQL = "?, ?, ?, ?, ?, ?, ?, ?, ?, ?"


@dataclass(frozen=True, slots=True)
class RemoteCacheMetadata:
    source: str
    visibility: str | None
    owner_user_id: str | None
    owner_display_name: str | None
    library_slug: str | None
    remote_revision: str | None
    sync_state: str
    downloaded_at: str | None
    installed: bool
    subscribed: bool

    @classmethod
    def from_row(cls, row: object) -> RemoteCacheMetadata:
        mapping = cast(Mapping[object, object], row)
        return cls(
            source=str(mapping["source"]),
            visibility=_optional_text(mapping["visibility"]),
            owner_user_id=_optional_text(mapping["owner_user_id"]),
            owner_display_name=_optional_text(mapping["owner_display_name"]),
            library_slug=_optional_text(mapping["library_slug"]),
            remote_revision=_optional_text(mapping["remote_revision"]),
            sync_state=str(mapping["sync_state"]),
            downloaded_at=_optional_text(mapping["downloaded_at"]),
            installed=_sqlite_bool(mapping["installed"]),
            subscribed=_sqlite_bool(mapping["subscribed"]),
        )

    def as_entry_payload(self) -> dict[str, object]:
        return {
            "source": self.source,
            "visibility": self.visibility,
            "ownerUserId": self.owner_user_id,
            "ownerDisplayName": self.owner_display_name,
            "librarySlug": self.library_slug,
            "remoteRevision": self.remote_revision,
            "syncState": self.sync_state,
            "downloadedAt": self.downloaded_at,
            "installed": self.installed,
            "subscribed": self.subscribed,
        }

    def as_db_tuple(self) -> tuple[str, str | None, str | None, str | None, str | None, str | None, str, str | None, int, int]:
        return (
            self.source,
            self.visibility,
            self.owner_user_id,
            self.owner_display_name,
            self.library_slug,
            self.remote_revision,
            self.sync_state,
            self.downloaded_at,
            1 if self.installed else 0,
            1 if self.subscribed else 0,
        )


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _sqlite_bool(value: object) -> bool:
    return bool(int(str(value)))


def remote_cache_metadata_from_fields(
    *,
    source: str,
    visibility: str | None,
    owner_user_id: str | None,
    owner_display_name: str | None,
    library_slug: str | None,
    remote_revision: str | None,
    sync_state: str,
    downloaded_at: str | None,
    installed: bool,
    subscribed: bool,
) -> RemoteCacheMetadata:
    return RemoteCacheMetadata(
        source=source,
        visibility=visibility,
        owner_user_id=owner_user_id,
        owner_display_name=owner_display_name,
        library_slug=library_slug,
        remote_revision=remote_revision,
        sync_state=sync_state,
        downloaded_at=downloaded_at,
        installed=installed,
        subscribed=subscribed,
    )


__all__ = [
    "REMOTE_CACHE_METADATA_INSERT_COLUMNS_SQL",
    "REMOTE_CACHE_METADATA_INSERT_VALUES_SQL",
    "REMOTE_CACHE_METADATA_SELECT_SQL",
    "RemoteCacheMetadata",
    "remote_cache_metadata_from_fields",
]

from __future__ import annotations

from enum import Enum
from msgspec import Struct, field

from ...session_migration import SESSION_SCHEMA_VERSION
from ..common import JsonObject, now_iso


class F8ComponentSourceKind(Enum):
    local = "local"
    remote_official = "remote_official"
    remote_public = "remote_public"
    remote_private = "remote_private"


class F8ComponentVisibility(Enum):
    private = "private"
    public = "public"


class F8ComponentSyncState(Enum):
    synced = "synced"
    local_only = "local_only"
    modified_local = "modified_local"
    stale_remote = "stale_remote"
    conflict = "conflict"


class F8ComponentRecord(Struct, kw_only=True):
    componentId: str
    name: str
    description: str = ""
    usageNotes: str = ""
    tags: list[str] = field(default_factory=list)
    schemaVersion: str = SESSION_SCHEMA_VERSION
    content: JsonObject = field(default_factory=dict)
    createdAt: str = field(default_factory=now_iso)
    updatedAt: str = field(default_factory=now_iso)


class F8ComponentEntry(Struct, kw_only=True):
    record: F8ComponentRecord
    source: F8ComponentSourceKind
    visibility: F8ComponentVisibility | None = None
    ownerUserId: str | None = None
    ownerDisplayName: str | None = None
    librarySlug: str | None = None
    remoteRevision: str | None = None
    syncState: F8ComponentSyncState = F8ComponentSyncState.local_only
    downloadedAt: str | None = None
    installed: bool = True
    subscribed: bool = False
    localVersionNumber: int | None = None
    remoteVersionNumber: int | None = None


class F8ComponentCatalogSnapshot(Struct, kw_only=True):
    schemaVersion: str = "f8componentcatalog/1"
    entries: list[F8ComponentEntry] = field(default_factory=list)


class F8ComponentRemoteUser(Struct, kw_only=True):
    userId: str
    displayName: str
    username: str | None = None


class F8ComponentRemoteAuth(Struct, kw_only=True):
    sessionCookie: str
    user: F8ComponentRemoteUser


class F8ComponentRemoteListPage(Struct, kw_only=True):
    entries: list[F8ComponentEntry] = field(default_factory=list)
    nextCursor: str | None = None


class F8ComponentRemoteVersionEntry(Struct, kw_only=True):
    componentId: str
    assetType: str
    versionNumber: int
    revision: str
    createdAt: str
    createdByUserId: str
    changeSummary: str | None = None


class F8ComponentRemoteVersionList(Struct, kw_only=True):
    versions: list[F8ComponentRemoteVersionEntry] = field(default_factory=list)


class F8ComponentLocalVersionSummary(Struct, kw_only=True):
    componentId: str
    versionNumber: int
    createdAt: str = field(default_factory=now_iso)


class F8ComponentRemoteSession(Struct, kw_only=True):
    accountId: str
    baseUrl: str
    sessionCookie: str
    user: F8ComponentRemoteUser
    lastUsedAt: str


class F8ComponentRemoteConflictError(Exception):
    def __init__(self, message: str, *, component_id: str, remote_revision: str | None = None) -> None:
        super().__init__(message)
        self.component_id = str(component_id)
        self.remote_revision = None if remote_revision is None else str(remote_revision)


class F8ComponentRemoteAuthError(Exception):
    pass


class F8ComponentRemoteRequestError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def component_now_iso() -> str:
    return now_iso()


__all__ = [
    "F8ComponentSourceKind",
    "F8ComponentVisibility",
    "F8ComponentSyncState",
    "F8ComponentRecord",
    "F8ComponentEntry",
    "F8ComponentCatalogSnapshot",
    "F8ComponentRemoteUser",
    "F8ComponentRemoteAuth",
    "F8ComponentRemoteListPage",
    "F8ComponentRemoteVersionEntry",
    "F8ComponentRemoteVersionList",
    "F8ComponentLocalVersionSummary",
    "F8ComponentRemoteSession",
    "F8ComponentRemoteConflictError",
    "F8ComponentRemoteAuthError",
    "F8ComponentRemoteRequestError",
    "component_now_iso",
]

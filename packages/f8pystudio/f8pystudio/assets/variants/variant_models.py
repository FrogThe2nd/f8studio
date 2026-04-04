from __future__ import annotations

from enum import Enum

from msgspec import Struct, field

from f8pysdk import F8VariantKind, F8VariantLibrary, F8VariantRecord, F8VariantRef
from ..common import now_iso


class F8VariantSourceKind(Enum):
    builtin = "builtin"
    local = "local"
    remote_official = "remote_official"
    remote_public = "remote_public"
    remote_private = "remote_private"


class F8VariantVisibility(Enum):
    private = "private"
    public = "public"


class F8VariantSyncState(Enum):
    synced = "synced"
    local_only = "local_only"
    modified_local = "modified_local"
    stale_remote = "stale_remote"
    conflict = "conflict"


class F8VariantEntry(Struct, kw_only=True):
    record: F8VariantRecord
    source: F8VariantSourceKind
    visibility: F8VariantVisibility | None = None
    ownerUserId: str | None = None
    ownerDisplayName: str | None = None
    librarySlug: str | None = None
    remoteRevision: str | None = None
    syncState: F8VariantSyncState = F8VariantSyncState.local_only
    downloadedAt: str | None = None
    installed: bool = True
    subscribed: bool = False


class F8VariantCatalogSnapshot(Struct, kw_only=True):
    schemaVersion: str = "f8variantcatalog/1"
    entries: list[F8VariantEntry] = field(default_factory=list)


class F8VariantRemoteUser(Struct, kw_only=True):
    userId: str
    displayName: str
    username: str | None = None


class F8VariantRemoteAuth(Struct, kw_only=True):
    accessToken: str
    refreshToken: str
    user: F8VariantRemoteUser


class F8VariantRemoteListPage(Struct, kw_only=True):
    entries: list[F8VariantEntry] = field(default_factory=list)
    nextCursor: str | None = None


class F8VariantRemoteVersionEntry(Struct, kw_only=True):
    variantId: str
    assetType: str
    versionNumber: int
    revision: str
    createdAt: str
    createdByUserId: str
    changeSummary: str | None = None


class F8VariantRemoteVersionList(Struct, kw_only=True):
    versions: list[F8VariantRemoteVersionEntry] = field(default_factory=list)


class F8VariantRemoteSession(Struct, kw_only=True):
    accountId: str
    baseUrl: str
    refreshToken: str
    user: F8VariantRemoteUser
    lastUsedAt: str


class F8VariantRemoteConflictError(Exception):
    def __init__(self, message: str, *, variant_id: str, remote_revision: str | None = None) -> None:
        super().__init__(message)
        self.variant_id = str(variant_id)
        self.remote_revision = None if remote_revision is None else str(remote_revision)


class F8VariantRemoteAuthError(Exception):
    pass


class F8VariantRemoteRequestError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def variant_now_iso() -> str:
    return now_iso()

__all__ = [
    "F8VariantKind",
    "F8VariantRef",
    "F8VariantRecord",
    "F8VariantLibrary",
    "F8VariantSourceKind",
    "F8VariantVisibility",
    "F8VariantSyncState",
    "F8VariantEntry",
    "F8VariantCatalogSnapshot",
    "F8VariantRemoteUser",
    "F8VariantRemoteAuth",
    "F8VariantRemoteListPage",
    "F8VariantRemoteVersionEntry",
    "F8VariantRemoteVersionList",
    "F8VariantRemoteSession",
    "F8VariantRemoteConflictError",
    "F8VariantRemoteAuthError",
    "F8VariantRemoteRequestError",
    "variant_now_iso",
]

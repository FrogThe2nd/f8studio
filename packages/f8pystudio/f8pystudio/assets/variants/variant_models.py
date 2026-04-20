from __future__ import annotations

from enum import Enum

from msgspec import Struct, field

from f8pysdk.specs import F8VariantKind, F8VariantLibrary, F8VariantRecord, F8VariantRef
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


class F8VariantDraftOriginKind(Enum):
    new = "new"
    copy_local = "copy_local"
    copy_remote = "copy_remote"


class F8VariantEntry(Struct, kw_only=True):
    record: F8VariantRecord
    source: F8VariantSourceKind
    visibility: F8VariantVisibility | None = None
    ownerUserId: str | None = None
    ownerDisplayName: str | None = None
    remoteRevision: str | None = None
    downloadedAt: str | None = None
    installed: bool = True
    hasCachedContent: bool | None = None
    subscribed: bool = False
    isLocalDraft: bool = False
    draftOriginKind: F8VariantDraftOriginKind | None = None
    draftOriginAssetId: str | None = None
    draftOriginRevision: str | None = None


class F8VariantDraftEntry(Struct, kw_only=True):
    draftId: str
    record: F8VariantRecord
    originKind: F8VariantDraftOriginKind | None = None
    publishTargetAssetId: str | None = None
    publishBaseRemoteRevision: str | None = None
    createdAt: str = field(default_factory=now_iso)
    updatedAt: str = field(default_factory=now_iso)


class F8VariantCatalogSnapshot(Struct, kw_only=True):
    schemaVersion: str = "f8variantcatalog/1"
    entries: list[F8VariantEntry] = field(default_factory=list)


class F8VariantLocalVersionSummary(Struct, kw_only=True):
    variantId: str
    versionNumber: int
    createdAt: str


class F8VariantRemoteUser(Struct, kw_only=True):
    userId: str
    name: str | None = None
    displayName: str
    email: str | None = None


class F8VariantRemoteAuth(Struct, kw_only=True):
    sessionCookie: str
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
    sessionCookie: str
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
    "F8VariantDraftOriginKind",
    "F8VariantDraftEntry",
    "F8VariantEntry",
    "F8VariantCatalogSnapshot",
    "F8VariantLocalVersionSummary",
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

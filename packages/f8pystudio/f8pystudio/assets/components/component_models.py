from __future__ import annotations

from enum import Enum
from msgspec import Struct, field

from f8pysdk.specs import F8ComponentRecord
from ..common import JsonObject, now_iso


class F8ComponentSourceKind(Enum):
    local = "local"
    remote_official = "remote_official"
    remote_public = "remote_public"
    remote_private = "remote_private"


class F8ComponentVisibility(Enum):
    private = "private"
    public = "public"


class F8ComponentDraftOriginKind(Enum):
    new = "new"
    copy_local = "copy_local"
    copy_remote = "copy_remote"


class F8ComponentEntry(Struct, kw_only=True):
    record: F8ComponentRecord
    source: F8ComponentSourceKind
    visibility: F8ComponentVisibility | None = None
    ownerUserId: str | None = None
    ownerDisplayName: str | None = None
    remoteVersionNumber: int | None = None
    downloadedAt: str | None = None
    installed: bool = True
    hasCachedContent: bool | None = None
    subscribed: bool = False
    isLocalDraft: bool = False
    draftOriginKind: F8ComponentDraftOriginKind | None = None
    draftOriginAssetId: str | None = None
    draftOriginVersionNumber: int | None = None


class F8ComponentDraftEntry(Struct, kw_only=True):
    draftId: str
    record: F8ComponentRecord
    originKind: F8ComponentDraftOriginKind | None = None
    publishTargetAssetId: str | None = None
    publishBaseRemoteVersionNumber: int | None = None
    createdAt: str = field(default_factory=now_iso)
    updatedAt: str = field(default_factory=now_iso)


class F8ComponentCatalogSnapshot(Struct, kw_only=True):
    schemaVersion: str = "f8componentcatalog/1"
    entries: list[F8ComponentEntry] = field(default_factory=list)


class F8ComponentRemoteUser(Struct, kw_only=True):
    userId: str
    name: str
    email: str | None = None


class F8ComponentRemoteAuth(Struct, kw_only=True):
    accessToken: str
    accessTokenExpiresAt: str
    refreshToken: str
    refreshTokenExpiresAt: str
    user: F8ComponentRemoteUser

    @property
    def sessionCookie(self) -> str:
        return str(self.accessToken)


class F8ComponentRemoteListPage(Struct, kw_only=True):
    entries: list[F8ComponentEntry] = field(default_factory=list)
    nextCursor: str | None = None


class F8ComponentRemoteVersionEntry(Struct, kw_only=True):
    componentId: str
    assetType: str
    versionNumber: int
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
    refreshToken: str
    accessTokenExpiresAt: str
    refreshTokenExpiresAt: str
    user: F8ComponentRemoteUser
    lastUsedAt: str
    accessToken: str = ""

    @property
    def sessionCookie(self) -> str:
        return str(self.accessToken)


class F8ComponentRemoteConflictError(Exception):
    def __init__(self, message: str, *, component_id: str, remote_version_number: int | None = None) -> None:
        super().__init__(message)
        self.component_id = str(component_id)
        self.remote_version_number = remote_version_number


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
    "F8ComponentDraftOriginKind",
    "F8ComponentDraftEntry",
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

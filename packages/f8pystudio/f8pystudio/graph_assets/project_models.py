from __future__ import annotations

from msgspec import Struct, field

from ..session_migration import SESSION_SCHEMA_VERSION
from .common import JsonObject, now_iso


class F8ProjectRecord(Struct, kw_only=True):
    projectId: str
    name: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    schemaVersion: str = SESSION_SCHEMA_VERSION
    content: JsonObject = field(default_factory=dict)
    createdAt: str = field(default_factory=now_iso)
    updatedAt: str = field(default_factory=now_iso)


class F8ProjectSummary(Struct, kw_only=True):
    projectId: str
    name: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    latestVersionNumber: int = 1
    createdAt: str = field(default_factory=now_iso)
    updatedAt: str = field(default_factory=now_iso)


class F8ProjectVersionSummary(Struct, kw_only=True):
    projectId: str
    versionNumber: int
    createdAt: str = field(default_factory=now_iso)

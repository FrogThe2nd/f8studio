from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from msgspec import Struct, field


class F8VariantKind(str, Enum):
    operator = "operator"
    service = "service"


class F8NodeVariantRecord(Struct, kw_only=True):
    variantId: str
    kind: F8VariantKind
    baseNodeType: str
    serviceClass: str
    operatorClass: str | None = None
    name: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    spec: dict[str, Any]
    createdAt: str
    updatedAt: str

    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class F8NodeVariantLibraryFile(Struct, kw_only=True):
    schemaVersion: str = "f8variantlib/1"
    variants: list[F8NodeVariantRecord] = field(default_factory=list)

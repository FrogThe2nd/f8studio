from __future__ import annotations

from datetime import datetime, timezone

from f8pysdk import F8VariantKind, F8VariantLibrary, F8VariantRecord, F8VariantRef


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _variant_ref(self: F8VariantRecord) -> F8VariantRef:
    return F8VariantRef(
        variantId=str(self.variantId),
        kind=self.kind,
        baseNodeType=str(self.baseNodeType),
        serviceClass=str(self.serviceClass),
        operatorClass=(None if self.operatorClass is None else str(self.operatorClass)),
        name=str(self.name),
    )


F8VariantRecord.now_iso = staticmethod(_now_iso)  # type: ignore[attr-defined]
F8VariantRecord.variant_ref = _variant_ref  # type: ignore[attr-defined]

__all__ = [
    "F8VariantKind",
    "F8VariantRef",
    "F8VariantRecord",
    "F8VariantLibrary",
]

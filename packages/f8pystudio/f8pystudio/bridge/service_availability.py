from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any


class ServiceStatusReuseCode(str, Enum):
    REUSABLE = "reusable"
    UNREACHABLE = "unreachable"
    OLD_PROTOCOL = "old_protocol"
    SERVICE_CLASS_MISMATCH = "service_class_mismatch"


@dataclass(frozen=True)
class ServiceStatusIdentity:
    active: bool | None
    identity_valid: bool
    service_class: str
    runtime_instance_id: str

    @property
    def protocol_complete(self) -> bool:
        return bool(self.identity_valid and self.service_class and self.runtime_instance_id)


@dataclass(frozen=True)
class ServiceStatusReuseEvaluation:
    code: ServiceStatusReuseCode
    identity: ServiceStatusIdentity | None = None
    desired_service_class: str = ""

    @property
    def reusable(self) -> bool:
        return self.code is ServiceStatusReuseCode.REUSABLE

    @property
    def running_service_class(self) -> str:
        identity = self.identity
        if identity is None:
            return ""
        return identity.service_class


def service_status_identity_from_payload(status: Mapping[str, Any]) -> ServiceStatusIdentity:
    active_raw = status.get("active") if "active" in status else None
    active = active_raw if isinstance(active_raw, bool) else None
    return ServiceStatusIdentity(
        active=active,
        identity_valid=bool(status.get("identityValid")),
        service_class=str(status.get("serviceClass") or "").strip(),
        runtime_instance_id=str(status.get("runtimeInstanceId") or "").strip(),
    )


def service_status_identity_valid(status: Mapping[str, Any]) -> bool:
    return service_status_identity_from_payload(status).protocol_complete


def evaluate_service_status_reuse(
    status: Mapping[str, Any] | None,
    *,
    desired_service_class: str,
) -> ServiceStatusReuseEvaluation:
    desired_class = str(desired_service_class or "").strip()
    if status is None:
        return ServiceStatusReuseEvaluation(
            code=ServiceStatusReuseCode.UNREACHABLE,
            desired_service_class=desired_class,
        )
    identity = service_status_identity_from_payload(status)
    if not identity.protocol_complete:
        return ServiceStatusReuseEvaluation(
            code=ServiceStatusReuseCode.OLD_PROTOCOL,
            identity=identity,
            desired_service_class=desired_class,
        )
    if identity.service_class != desired_class:
        return ServiceStatusReuseEvaluation(
            code=ServiceStatusReuseCode.SERVICE_CLASS_MISMATCH,
            identity=identity,
            desired_service_class=desired_class,
        )
    return ServiceStatusReuseEvaluation(
        code=ServiceStatusReuseCode.REUSABLE,
        identity=identity,
        desired_service_class=desired_class,
    )

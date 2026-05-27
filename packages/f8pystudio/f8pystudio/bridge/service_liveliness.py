from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

ZENOH_SERVICE_LIVELINESS_PREFIX = "f8/live/svc/"


@dataclass(frozen=True)
class ServiceLivelinessIdentity:
    service_id: str
    runtime_instance_id: str


@dataclass(frozen=True)
class ServiceLivelinessQueryResult:
    instances: set[str]
    query_ok: bool
    error: BaseException | None = None


def is_zenoh_liveliness_reply_channel_drained(exc: BaseException) -> bool:
    return "channel is empty and closed" in str(exc).strip().lower()


def service_liveliness_identity_from_zenoh_key(key: str) -> ServiceLivelinessIdentity | None:
    parts = [part for part in str(key or "").strip("/").split("/") if part]
    if len(parts) != 6:
        return None
    if parts[0] != "f8" or parts[1] != "live" or parts[2] != "svc" or parts[4] != "instances":
        return None
    service_id = parts[3]
    runtime_instance_id = parts[5]
    if not service_id or not runtime_instance_id:
        return None
    return ServiceLivelinessIdentity(service_id=service_id, runtime_instance_id=runtime_instance_id)


def service_id_from_zenoh_liveliness_key(key: str) -> str | None:
    identity = service_liveliness_identity_from_zenoh_key(key)
    if identity is None:
        return None
    return identity.service_id


def format_runtime_instances(instances: set[str] | None) -> str:
    if instances is None:
        return "<unknown>"
    if not instances:
        return "<none>"
    return ",".join(sorted(instances))


def query_service_liveliness_instances_sync(
    *,
    zenoh_module: Any,
    session: Any,
    service_id: str,
    timeout_s: float,
) -> ServiceLivelinessQueryResult:
    sid = str(service_id or "").strip()
    instances: set[str] = set()
    key_expr = f"{ZENOH_SERVICE_LIVELINESS_PREFIX}{sid}/instances/**"
    try:
        replies = session.liveliness().get(key_expr, timeout=float(timeout_s))
        deadline = time.monotonic() + max(0.02, float(timeout_s)) + 0.05
        while time.monotonic() < deadline:
            try:
                reply = replies.try_recv()
            except zenoh_module.ZError as exc:
                if is_zenoh_liveliness_reply_channel_drained(exc):
                    break
                raise
            if reply is None:
                time.sleep(0.01)
                continue
            sample = reply.ok
            if sample is None:
                continue
            identity = service_liveliness_identity_from_zenoh_key(str(sample.key_expr))
            if identity is not None and identity.service_id == sid:
                instances.add(identity.runtime_instance_id)
        return ServiceLivelinessQueryResult(instances=instances, query_ok=True)
    except Exception as exc:
        return ServiceLivelinessQueryResult(instances=set(), query_ok=False, error=exc)


__all__ = [
    "ServiceLivelinessIdentity",
    "ServiceLivelinessQueryResult",
    "ZENOH_SERVICE_LIVELINESS_PREFIX",
    "format_runtime_instances",
    "is_zenoh_liveliness_reply_channel_drained",
    "query_service_liveliness_instances_sync",
    "service_id_from_zenoh_liveliness_key",
    "service_liveliness_identity_from_zenoh_key",
]

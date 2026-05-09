from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from f8pysdk.f8_naming import ensure_token


@dataclass(frozen=True)
class ManagedServiceInventory:
    service_ids: set[str] = field(default_factory=set)
    service_classes: dict[str, str] = field(default_factory=dict)
    start_order: list[tuple[str, str]] = field(default_factory=list)


def collect_managed_service_inventory(
    *,
    services: list[Any],
    studio_service_id: str,
    studio_service_class: str,
    on_collect_error: Callable[[BaseException], None],
) -> ManagedServiceInventory:
    service_ids: set[str] = set()
    service_classes: dict[str, str] = {}
    start_order: list[tuple[str, str]] = []
    for service in list(services or []):
        try:
            service_id = ensure_token(str(service.serviceId), label="service_id")
            service_class = str(service.serviceClass)
            if service_id == str(studio_service_id) or service_class == str(studio_service_class):
                continue
            service_ids.add(service_id)
            service_classes[service_id] = service_class
            start_order.append((service_id, service_class))
        except Exception as exc:
            on_collect_error(exc)
    return ManagedServiceInventory(
        service_ids=service_ids,
        service_classes=service_classes,
        start_order=start_order,
    )

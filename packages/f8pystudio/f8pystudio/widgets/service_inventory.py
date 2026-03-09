from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable

from f8pysdk import F8ServiceSpec

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeclaredService:
    service_id: str
    service_class: str


def collect_declared_services(*, nodes: Iterable[Any], studio_service_class: str) -> dict[str, str]:
    rows: dict[str, str] = {}
    for row in iter_declared_services(nodes=nodes, studio_service_class=studio_service_class):
        rows[row.service_id] = row.service_class
    return rows


def collect_declared_service_ids(*, nodes: Iterable[Any], studio_service_class: str) -> set[str]:
    service_ids: set[str] = set()
    for row in iter_declared_services(nodes=nodes, studio_service_class=studio_service_class):
        service_ids.add(row.service_id)
    return service_ids


def iter_declared_services(*, nodes: Iterable[Any], studio_service_class: str) -> Iterable[DeclaredService]:
    for node in nodes:
        row = _extract_declared_service(node=node, studio_service_class=studio_service_class)
        if row is None:
            continue
        yield row


def _extract_declared_service(*, node: Any, studio_service_class: str) -> DeclaredService | None:
    try:
        spec = node.spec
    except Exception as exc:
        logger.debug("Failed to read node.spec for declared-service scan: %s", exc, exc_info=True)
        return None

    if not isinstance(spec, F8ServiceSpec):
        return None

    service_class = str(spec.serviceClass or "").strip()
    if not service_class or service_class == studio_service_class:
        return None

    try:
        service_id = str(node.id or "").strip()
    except Exception as exc:
        logger.debug("Failed to read node.id for declared-service scan: %s", exc, exc_info=True)
        return None
    if not service_id:
        return None

    return DeclaredService(service_id=service_id, service_class=service_class)

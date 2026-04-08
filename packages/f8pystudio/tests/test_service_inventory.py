from __future__ import annotations

from f8pysdk.specs import F8ServiceSpec

from f8pystudio.ui.support.service_inventory import collect_declared_service_ids, collect_declared_services

_STUDIO_SERVICE_CLASS = "f8.pystudio"


class _Node:
    def __init__(self, *, node_id: str, service_class: str) -> None:
        self.id = node_id
        self.spec = F8ServiceSpec(serviceClass=service_class, label="Test Service")


class _NonServiceNode:
    def __init__(self) -> None:
        self.id = "op.1"
        self.spec = object()


class _BrokenSpecNode:
    id = "svc.broken"

    @property
    def spec(self) -> object:
        raise RuntimeError("spec unavailable")


class _BrokenIdNode:
    spec = F8ServiceSpec(serviceClass="f8.test.brokenid", label="Broken Id")

    @property
    def id(self) -> str:
        raise RuntimeError("id unavailable")


def test_collect_declared_services_filters_non_runtime_service_nodes() -> None:
    rows = collect_declared_services(
        nodes=[
            _Node(node_id="svc.1", service_class="f8.test.alpha"),
            _Node(node_id="svc.2", service_class=_STUDIO_SERVICE_CLASS),
            _Node(node_id="", service_class="f8.test.empty"),
            _NonServiceNode(),
        ],
        studio_service_class=_STUDIO_SERVICE_CLASS,
    )

    assert rows == {"svc.1": "f8.test.alpha"}


def test_collect_declared_service_ids_tolerates_broken_nodes() -> None:
    service_ids = collect_declared_service_ids(
        nodes=[
            _Node(node_id="svc.good", service_class="f8.test.good"),
            _BrokenSpecNode(),
            _BrokenIdNode(),
        ],
        studio_service_class=_STUDIO_SERVICE_CLASS,
    )

    assert service_ids == {"svc.good"}

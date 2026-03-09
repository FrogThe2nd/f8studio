from __future__ import annotations

from types import SimpleNamespace

from f8pystudio.bridge.managed_service_inventory import collect_managed_service_inventory


def test_collect_managed_service_inventory_skips_studio_and_collects_start_order() -> None:
    errors: list[str] = []
    services = [
        SimpleNamespace(serviceId="studio", serviceClass="f8.pystudio"),
        SimpleNamespace(serviceId="svc_a", serviceClass="f8.test.a"),
        SimpleNamespace(serviceId="svc_b", serviceClass="f8.test.b"),
        SimpleNamespace(serviceId="svc_studio_like", serviceClass="f8.studio.service"),
    ]

    inventory = collect_managed_service_inventory(
        services=services,
        studio_service_id="studio",
        studio_service_class="f8.studio.service",
        on_collect_error=lambda exc: errors.append(type(exc).__name__),
    )

    assert inventory.service_ids == {"svc_a", "svc_b"}
    assert inventory.service_classes == {"svc_a": "f8.test.a", "svc_b": "f8.test.b"}
    assert inventory.start_order == [("svc_a", "f8.test.a"), ("svc_b", "f8.test.b")]
    assert errors == []


def test_collect_managed_service_inventory_reports_invalid_service_id() -> None:
    errors: list[str] = []
    services = [
        SimpleNamespace(serviceId="svc.ok", serviceClass="f8.test.invalid"),
        SimpleNamespace(serviceId="svc_ok", serviceClass="f8.test.ok"),
    ]

    inventory = collect_managed_service_inventory(
        services=services,
        studio_service_id="studio",
        studio_service_class="f8.studio.service",
        on_collect_error=lambda exc: errors.append(type(exc).__name__),
    )

    assert inventory.service_ids == {"svc_ok"}
    assert inventory.service_classes == {"svc_ok": "f8.test.ok"}
    assert inventory.start_order == [("svc_ok", "f8.test.ok")]
    assert errors == ["ValueError"]

from __future__ import annotations

from f8pystudio.bridge.service_availability import (
    ServiceStatusReuseCode,
    evaluate_service_status_reuse,
    service_status_identity_from_payload,
    service_status_identity_valid,
)


def test_service_status_identity_from_payload_extracts_explicit_fields() -> None:
    identity = service_status_identity_from_payload(
        {
            "active": True,
            "identityValid": True,
            "serviceClass": " f8.tests.alpha ",
            "runtimeInstanceId": " inst_alpha ",
        }
    )

    assert identity.active is True
    assert identity.identity_valid is True
    assert identity.service_class == "f8.tests.alpha"
    assert identity.runtime_instance_id == "inst_alpha"
    assert identity.protocol_complete is True


def test_service_status_identity_valid_requires_protocol_fields() -> None:
    assert service_status_identity_valid(
        {
            "identityValid": True,
            "serviceClass": "f8.tests.alpha",
            "runtimeInstanceId": "inst_alpha",
        }
    ) is True
    assert service_status_identity_valid({"identityValid": True, "serviceClass": "f8.tests.alpha"}) is False
    assert service_status_identity_valid({"serviceClass": "f8.tests.alpha", "runtimeInstanceId": "inst"}) is False


def test_evaluate_service_status_reuse_reports_unreachable() -> None:
    evaluation = evaluate_service_status_reuse(None, desired_service_class="f8.tests.alpha")

    assert evaluation.code is ServiceStatusReuseCode.UNREACHABLE
    assert evaluation.reusable is False
    assert evaluation.identity is None
    assert evaluation.desired_service_class == "f8.tests.alpha"


def test_evaluate_service_status_reuse_reports_old_protocol() -> None:
    evaluation = evaluate_service_status_reuse(
        {"identityValid": False, "serviceClass": "f8.tests.alpha", "runtimeInstanceId": "inst"},
        desired_service_class="f8.tests.alpha",
    )

    assert evaluation.code is ServiceStatusReuseCode.OLD_PROTOCOL
    assert evaluation.reusable is False
    assert evaluation.running_service_class == "f8.tests.alpha"


def test_evaluate_service_status_reuse_reports_service_class_mismatch() -> None:
    evaluation = evaluate_service_status_reuse(
        {
            "identityValid": True,
            "serviceClass": "f8.tests.actual",
            "runtimeInstanceId": "inst",
        },
        desired_service_class="f8.tests.desired",
    )

    assert evaluation.code is ServiceStatusReuseCode.SERVICE_CLASS_MISMATCH
    assert evaluation.reusable is False
    assert evaluation.running_service_class == "f8.tests.actual"


def test_evaluate_service_status_reuse_accepts_matching_identity() -> None:
    evaluation = evaluate_service_status_reuse(
        {
            "identityValid": True,
            "serviceClass": "f8.tests.alpha",
            "runtimeInstanceId": "inst",
        },
        desired_service_class=" f8.tests.alpha ",
    )

    assert evaluation.code is ServiceStatusReuseCode.REUSABLE
    assert evaluation.reusable is True
    assert evaluation.running_service_class == "f8.tests.alpha"

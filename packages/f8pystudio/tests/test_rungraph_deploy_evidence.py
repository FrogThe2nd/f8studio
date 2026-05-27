from __future__ import annotations

from f8pysdk.codec import encode_obj
from f8pysdk.rungraph_fingerprint import build_rungraph_deploy_fingerprint
from f8pysdk.service_runtime_tools.deploy.readiness import rungraph_deploy_request_status_key
from f8pysdk.specs import F8RuntimeGraph, F8RuntimeNode

from f8pystudio.bridge.rungraph_deploy_evidence import (
    RungraphApplyEvidenceTracker,
    decode_retained_rungraph_fingerprint,
    is_rungraph_config_key,
    rungraph_config_key,
)


def _graph_payload() -> dict[str, object]:
    graph = F8RuntimeGraph(
        graphId="g1",
        revision="r1",
        nodes=[F8RuntimeNode(nodeId="svc1", serviceId="svc1", serviceClass="svc.a", operatorClass=None)],
        edges=[],
    )
    return {
        "graphId": graph.graphId,
        "revision": graph.revision,
        "nodes": [
            {
                "nodeId": "svc1",
                "serviceId": "svc1",
                "serviceClass": "svc.a",
                "operatorClass": None,
            }
        ],
        "edges": [],
    }


def _status_payload(
    *,
    req_id: str = "req1",
    phase: str = "applied",
    target_fingerprint: str,
    applied_fingerprint: str = "",
    runtime_instance_id: str = "inst1",
    error_message: str = "",
) -> dict[str, object]:
    return {
        "schemaVersion": "f8.rungraphDeployStatus/2",
        "serviceId": "svc1",
        "reqId": str(req_id),
        "graphId": "g1",
        "revision": "r1",
        "phase": str(phase),
        "ok": phase == "applied",
        "source": "test",
        "errorMessage": str(error_message),
        "ts": 1,
        "targetFingerprint": str(target_fingerprint),
        "appliedFingerprint": str(applied_fingerprint),
        "runtimeInstanceId": str(runtime_instance_id),
    }


def test_rungraph_config_key_helpers() -> None:
    assert rungraph_config_key("svc1") == "f8/svc/svc1/config/rungraph"
    assert is_rungraph_config_key("f8/svc/svc1/config/rungraph") is True
    assert is_rungraph_config_key("f8/svc/svc1/status/rungraph") is False


def test_decode_retained_rungraph_fingerprint_accepts_retained_graph_payload() -> None:
    payload = _graph_payload()
    expected = build_rungraph_deploy_fingerprint(payload)

    assert decode_retained_rungraph_fingerprint(encode_obj(payload)) == expected
    assert decode_retained_rungraph_fingerprint(b"not msgpack") == ""


def test_evidence_tracker_accepts_matching_applied_status() -> None:
    payload = _graph_payload()
    fingerprint = build_rungraph_deploy_fingerprint(payload)
    tracker = RungraphApplyEvidenceTracker(
        service_id="svc1",
        req_id="req1",
        target_fingerprint=fingerprint,
        expected_runtime_instance_id="inst1",
        apply_timeout_s=0.25,
    )

    decision = tracker.observe_status_payload(
        _status_payload(
            target_fingerprint=fingerprint,
            applied_fingerprint=fingerprint,
            runtime_instance_id="inst1",
        )
    )

    assert decision.success is True
    assert decision.error_message == ""


def test_evidence_tracker_records_failed_status_message() -> None:
    payload = _graph_payload()
    fingerprint = build_rungraph_deploy_fingerprint(payload)
    tracker = RungraphApplyEvidenceTracker(
        service_id="svc1",
        req_id="req1",
        target_fingerprint=fingerprint,
        apply_timeout_s=0.25,
    )

    decision = tracker.observe_status_payload(
        _status_payload(
            phase="failed",
            target_fingerprint=fingerprint,
            applied_fingerprint="",
            error_message="ValueError: invalid graph",
        )
    )

    assert decision.success is False
    assert decision.error_message == "ValueError: invalid graph"
    assert tracker.timeout_error_message() == "ValueError: invalid graph"


def test_evidence_tracker_ignores_stale_request_status() -> None:
    payload = _graph_payload()
    fingerprint = build_rungraph_deploy_fingerprint(payload)
    tracker = RungraphApplyEvidenceTracker(
        service_id="svc1",
        req_id="req1",
        target_fingerprint=fingerprint,
        apply_timeout_s=0.25,
    )

    decision = tracker.observe_status_payload(
        _status_payload(
            req_id="old",
            target_fingerprint=fingerprint,
            applied_fingerprint=fingerprint,
        )
    )

    assert decision.success is False
    assert tracker.last_phase == ""
    assert "rungraph apply status not received within 0.25s" in tracker.timeout_error_message()
    assert rungraph_deploy_request_status_key("svc1", "req1") in tracker.timeout_error_message()


def test_evidence_tracker_reports_unexpected_runtime_instance() -> None:
    payload = _graph_payload()
    fingerprint = build_rungraph_deploy_fingerprint(payload)
    tracker = RungraphApplyEvidenceTracker(
        service_id="svc1",
        req_id="req1",
        target_fingerprint=fingerprint,
        expected_runtime_instance_id="new_inst",
        apply_timeout_s=0.25,
    )

    decision = tracker.observe_status_payload(
        _status_payload(
            target_fingerprint=fingerprint,
            applied_fingerprint=fingerprint,
            runtime_instance_id="old_inst",
        )
    )

    assert decision.success is False
    error = tracker.timeout_error_message()
    assert "unexpected runtime instance" in error
    assert "expectedRuntimeInstanceId=new_inst" in error
    assert "runtimeInstanceId=old_inst" in error


def test_evidence_tracker_reports_applied_fingerprint_mismatch() -> None:
    payload = _graph_payload()
    fingerprint = build_rungraph_deploy_fingerprint(payload)
    tracker = RungraphApplyEvidenceTracker(
        service_id="svc1",
        req_id="req1",
        target_fingerprint=fingerprint,
        apply_timeout_s=0.25,
    )

    decision = tracker.observe_status_payload(
        _status_payload(
            target_fingerprint=fingerprint,
            applied_fingerprint="different-fingerprint",
        )
    )

    assert decision.success is False
    error = tracker.timeout_error_message()
    assert "reported applied but fingerprint mismatched" in error
    assert "applied=different-finge" in error


def test_evidence_tracker_reports_last_nonfinal_phase() -> None:
    payload = _graph_payload()
    fingerprint = build_rungraph_deploy_fingerprint(payload)
    tracker = RungraphApplyEvidenceTracker(
        service_id="svc1",
        req_id="req1",
        target_fingerprint=fingerprint,
        apply_timeout_s=0.25,
    )

    decision = tracker.observe_status_payload(
        _status_payload(
            phase="applying",
            target_fingerprint=fingerprint,
            applied_fingerprint="",
        )
    )

    assert decision.success is False
    assert "last phase=applying" in tracker.timeout_error_message()

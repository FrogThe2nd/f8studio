from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from f8pysdk.codec import decode_obj
from f8pysdk.f8_naming import ensure_token
from f8pysdk.rungraph_fingerprint import build_rungraph_deploy_fingerprint
from f8pysdk.service_runtime_tools.deploy.readiness import rungraph_deploy_request_status_key

RUNGRAPH_CONFIG_KEY_SUFFIX = "/config/rungraph"


def rungraph_config_key(service_id: str) -> str:
    service_id_s = ensure_token(str(service_id), label="service_id")
    return f"f8/svc/{service_id_s}/config/rungraph"


def is_rungraph_config_key(key: str) -> bool:
    return str(key or "").endswith(RUNGRAPH_CONFIG_KEY_SUFFIX)


def decode_retained_rungraph_fingerprint(raw: bytes | None) -> str:
    if not raw:
        return ""
    try:
        payload = decode_obj(raw)
    except ValueError:
        return ""
    if isinstance(payload, dict) and "nodes" in payload and "edges" in payload:
        return build_rungraph_deploy_fingerprint(payload)
    return ""


@dataclass(frozen=True)
class RungraphApplyEvidenceDecision:
    success: bool
    error_message: str = ""


@dataclass
class RungraphApplyEvidenceTracker:
    service_id: str
    req_id: str
    target_fingerprint: str
    apply_timeout_s: float
    expected_runtime_instance_id: str = ""
    failed_message: str = ""
    last_phase: str = ""
    last_target_fingerprint: str = ""
    last_applied_fingerprint: str = ""
    last_runtime_instance_id: str = ""

    def __post_init__(self) -> None:
        self.service_id = ensure_token(str(self.service_id), label="service_id")
        self.req_id = str(self.req_id or "").strip()
        if not self.req_id:
            raise ValueError("req_id is required")
        self.target_fingerprint = str(self.target_fingerprint or "").strip()
        self.expected_runtime_instance_id = str(self.expected_runtime_instance_id or "").strip()
        self.apply_timeout_s = max(0.001, float(self.apply_timeout_s))

    @property
    def request_status_key(self) -> str:
        return rungraph_deploy_request_status_key(self.service_id, self.req_id)

    def observe_status_payload(self, payload: Any) -> RungraphApplyEvidenceDecision:
        if not isinstance(payload, dict):
            return RungraphApplyEvidenceDecision(success=False)
        payload_req_id = str(payload.get("reqId") or "").strip()
        if payload_req_id != self.req_id:
            return RungraphApplyEvidenceDecision(success=False)

        phase = str(payload.get("phase") or "").strip()
        if phase:
            self.last_phase = phase
            self.last_target_fingerprint = str(payload.get("targetFingerprint") or "").strip()
            self.last_applied_fingerprint = str(payload.get("appliedFingerprint") or "").strip()
            self.last_runtime_instance_id = str(payload.get("runtimeInstanceId") or "").strip()

        if self.expected_runtime_instance_id:
            runtime_instance_id = str(payload.get("runtimeInstanceId") or "").strip()
            if runtime_instance_id != self.expected_runtime_instance_id:
                return RungraphApplyEvidenceDecision(success=False)

        target = str(payload.get("targetFingerprint") or "").strip()
        applied = str(payload.get("appliedFingerprint") or "").strip()
        if applied and applied == self.target_fingerprint and phase == "applied":
            return RungraphApplyEvidenceDecision(success=True)
        if phase == "failed" and target == self.target_fingerprint:
            self.failed_message = str(payload.get("errorMessage") or "rungraph apply failed")
            return RungraphApplyEvidenceDecision(success=False, error_message=self.failed_message)
        return RungraphApplyEvidenceDecision(success=False)

    def timeout_error_message(self) -> str:
        if self.failed_message:
            return self.failed_message
        if (
            self.expected_runtime_instance_id
            and self.last_phase == "applied"
            and self.last_applied_fingerprint == self.target_fingerprint
            and self.last_runtime_instance_id
            and self.last_runtime_instance_id != self.expected_runtime_instance_id
        ):
            return (
                f"rungraph apply reported applied from unexpected runtime instance within "
                f"{self.apply_timeout_s:g}s "
                f"(expectedRuntimeInstanceId={self.expected_runtime_instance_id}, "
                f"runtimeInstanceId={self.last_runtime_instance_id}, key={self.request_status_key})"
            )
        if self.last_phase == "applied" and self.last_applied_fingerprint:
            target_short = (self.last_target_fingerprint or self.target_fingerprint)[:16]
            applied_short = self.last_applied_fingerprint[:16]
            return (
                f"rungraph apply reported applied but fingerprint mismatched within "
                f"{self.apply_timeout_s:g}s "
                f"(target={target_short}, applied={applied_short}, key={self.request_status_key})"
            )
        if self.last_phase:
            return (
                f"rungraph apply status not final within {self.apply_timeout_s:g}s "
                f"(last phase={self.last_phase}, key={self.request_status_key})"
            )
        expected_suffix = (
            f", expectedRuntimeInstanceId={self.expected_runtime_instance_id}"
            if self.expected_runtime_instance_id
            else ""
        )
        return (
            f"rungraph apply status not received within {self.apply_timeout_s:g}s "
            f"(key={self.request_status_key}, fingerprint={self.target_fingerprint[:16]}{expected_suffix})"
        )

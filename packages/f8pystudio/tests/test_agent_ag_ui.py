from __future__ import annotations

from f8pystudio.agents.ag_ui import (
    AgUiRunEnvelope,
    encode_ag_ui_events,
    graph_patch_preview_event,
    runtime_evidence_event,
)
from f8pystudio.agents.runtime import StudioAgentEvent


def test_ag_ui_envelope_maps_agent_stream_events() -> None:
    envelope = AgUiRunEnvelope(run_id="run-1", message_id="msg-1")

    events = [
        envelope.run_started(),
        envelope.text_started(),
        *envelope.agent_event(StudioAgentEvent(kind="chunk", text="hello")),
        *envelope.agent_event(StudioAgentEvent(kind="done")),
    ]

    assert encode_ag_ui_events(events) == [
        {"type": "RUN_STARTED", "runId": "run-1"},
        {"type": "TEXT_MESSAGE_START", "runId": "run-1", "messageId": "msg-1"},
        {"type": "TEXT_MESSAGE_CONTENT", "runId": "run-1", "messageId": "msg-1", "delta": "hello"},
        {"type": "TEXT_MESSAGE_END", "runId": "run-1", "messageId": "msg-1"},
        {"type": "RUN_FINISHED", "runId": "run-1"},
    ]


def test_ag_ui_envelope_maps_agent_error_event() -> None:
    envelope = AgUiRunEnvelope(run_id="run-1", message_id="msg-1")

    events = envelope.agent_event(StudioAgentEvent(kind="error", error="boom"))

    assert encode_ag_ui_events(events) == [{"type": "RUN_ERROR", "runId": "run-1", "error": "boom"}]


def test_ag_ui_custom_events_carry_graph_and_runtime_payloads() -> None:
    envelope = AgUiRunEnvelope(run_id="run-1", message_id="msg-1")

    preview_event = graph_patch_preview_event(envelope, {"valid": True})
    evidence_event = runtime_evidence_event(envelope, {"serviceId": "svc-a"})

    assert preview_event.to_dict() == {
        "type": "CUSTOM",
        "runId": "run-1",
        "name": "f8studio.graph.patch.preview",
        "value": {"preview": {"valid": True}},
    }
    assert evidence_event.to_dict() == {
        "type": "CUSTOM",
        "runId": "run-1",
        "name": "f8studio.runtime.evidence",
        "value": {"evidence": {"serviceId": "svc-a"}},
    }

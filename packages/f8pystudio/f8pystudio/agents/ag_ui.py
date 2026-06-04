from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .runtime import StudioAgentEvent

AgUiEventType = Literal[
    "RUN_STARTED",
    "RUN_FINISHED",
    "RUN_ERROR",
    "TEXT_MESSAGE_START",
    "TEXT_MESSAGE_CONTENT",
    "TEXT_MESSAGE_END",
    "CUSTOM",
]


@dataclass(frozen=True)
class AgUiEvent:
    type: AgUiEventType
    run_id: str
    message_id: str = ""
    delta: str = ""
    error: str = ""
    name: str = ""
    value: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": self.type, "runId": self.run_id}
        if self.message_id:
            payload["messageId"] = self.message_id
        if self.delta:
            payload["delta"] = self.delta
        if self.error:
            payload["error"] = self.error
        if self.name:
            payload["name"] = self.name
        if self.value:
            payload["value"] = dict(self.value)
        return payload


@dataclass(frozen=True)
class AgUiRunEnvelope:
    run_id: str
    message_id: str

    def run_started(self) -> AgUiEvent:
        return AgUiEvent(type="RUN_STARTED", run_id=self.run_id)

    def text_started(self) -> AgUiEvent:
        return AgUiEvent(type="TEXT_MESSAGE_START", run_id=self.run_id, message_id=self.message_id)

    def text_finished(self) -> AgUiEvent:
        return AgUiEvent(type="TEXT_MESSAGE_END", run_id=self.run_id, message_id=self.message_id)

    def run_finished(self) -> AgUiEvent:
        return AgUiEvent(type="RUN_FINISHED", run_id=self.run_id)

    def run_error(self, error: str) -> AgUiEvent:
        return AgUiEvent(type="RUN_ERROR", run_id=self.run_id, error=str(error or ""))

    def custom(self, name: str, value: dict[str, Any]) -> AgUiEvent:
        return AgUiEvent(type="CUSTOM", run_id=self.run_id, name=str(name or ""), value=dict(value))

    def agent_event(self, event: StudioAgentEvent) -> list[AgUiEvent]:
        if event.kind == "chunk":
            return [
                AgUiEvent(
                    type="TEXT_MESSAGE_CONTENT",
                    run_id=self.run_id,
                    message_id=self.message_id,
                    delta=event.text,
                )
            ]
        if event.kind == "done":
            return [self.text_finished(), self.run_finished()]
        if event.kind == "error":
            return [self.run_error(event.error)]
        return []


def encode_ag_ui_events(events: list[AgUiEvent]) -> list[dict[str, Any]]:
    return [event.to_dict() for event in events]


def graph_patch_preview_event(envelope: AgUiRunEnvelope, preview: dict[str, Any]) -> AgUiEvent:
    return envelope.custom(
        "f8studio.graph.patch.preview",
        {
            "preview": dict(preview),
        },
    )


def runtime_evidence_event(envelope: AgUiRunEnvelope, evidence: dict[str, Any]) -> AgUiEvent:
    return envelope.custom(
        "f8studio.runtime.evidence",
        {
            "evidence": dict(evidence),
        },
    )

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = "f8studio-ai-conversations/1"
_DEFAULT_TITLE = "New conversation"
_SHARED_CONVERSATION_STORE: StudioConversationStore | None = None


@dataclass(frozen=True)
class StudioConversationMessage:
    role: str
    content: str
    attachments: tuple[dict[str, str], ...] = ()
    created_at_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "role": self.role,
            "content": self.content,
            "createdAtMs": self.created_at_ms,
        }
        if self.attachments:
            payload["attachments"] = [dict(item) for item in self.attachments]
        return payload


@dataclass(frozen=True)
class StudioConversationRecord:
    conversation_id: str
    title: str
    scope: str
    created_at_ms: int
    updated_at_ms: int
    messages: tuple[StudioConversationMessage, ...] = ()
    agent_session: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "conversationId": self.conversation_id,
            "title": self.title,
            "scope": self.scope,
            "createdAtMs": self.created_at_ms,
            "updatedAtMs": self.updated_at_ms,
            "messages": [message.to_dict() for message in self.messages],
        }
        if self.agent_session is not None:
            payload["agentSession"] = dict(self.agent_session)
        return payload


@dataclass(frozen=True)
class StudioConversationSummary:
    conversation_id: str
    title: str
    scope: str
    created_at_ms: int
    updated_at_ms: int
    message_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversationId": self.conversation_id,
            "title": self.title,
            "scope": self.scope,
            "createdAtMs": self.created_at_ms,
            "updatedAtMs": self.updated_at_ms,
            "messageCount": self.message_count,
        }


class StudioConversationStore:
    def __init__(self, *, storage_path: Path | None = None) -> None:
        self._storage_path = storage_path or self._resolve_storage_path()
        self._conversations: dict[str, StudioConversationRecord] = {}
        self._active_conversation_id = ""
        self._lock = threading.RLock()
        self._load()

    def list_conversations(self, *, scope: str = "") -> list[StudioConversationSummary]:
        scope_text = str(scope or "").strip()
        with self._lock:
            records = list(self._conversations.values())
        if scope_text:
            records = [record for record in records if record.scope == scope_text]
        records.sort(key=lambda record: record.updated_at_ms, reverse=True)
        return [
            StudioConversationSummary(
                conversation_id=record.conversation_id,
                title=record.title,
                scope=record.scope,
                created_at_ms=record.created_at_ms,
                updated_at_ms=record.updated_at_ms,
                message_count=len(record.messages),
            )
            for record in records
        ]

    def get_conversation(self, conversation_id: str) -> StudioConversationRecord | None:
        with self._lock:
            return self._conversations.get(str(conversation_id or "").strip())

    def active_conversation_id(self) -> str:
        with self._lock:
            return self._active_conversation_id

    def set_active_conversation_id(self, conversation_id: str) -> None:
        with self._lock:
            self._active_conversation_id = str(conversation_id or "").strip()

    def ensure_conversation(
        self,
        conversation_id: str = "",
        *,
        scope: str = "graph",
        title: str = "",
    ) -> StudioConversationRecord:
        with self._lock:
            normalized_id = str(conversation_id or "").strip()
            if normalized_id:
                existing = self._conversations.get(normalized_id)
                if existing is not None:
                    return existing
            now = _now_ms()
            resolved_title = _conversation_title(str(title or "").strip(), ())
            record = StudioConversationRecord(
                conversation_id=normalized_id or uuid.uuid4().hex,
                title=resolved_title,
                scope=str(scope or "graph").strip() or "graph",
                created_at_ms=now,
                updated_at_ms=now,
            )
            self._conversations[record.conversation_id] = record
            self._persist()
            return record

    def save_messages(
        self,
        conversation_id: str,
        *,
        scope: str,
        messages: tuple[StudioConversationMessage, ...],
        agent_session: dict[str, Any] | None = None,
    ) -> StudioConversationRecord:
        with self._lock:
            existing = self.ensure_conversation(conversation_id, scope=scope)
            now = _now_ms()
            title = _conversation_title(existing.title, messages)
            resolved_agent_session = agent_session if agent_session is not None else existing.agent_session
            record = StudioConversationRecord(
                conversation_id=existing.conversation_id,
                title=title,
                scope=existing.scope,
                created_at_ms=existing.created_at_ms,
                updated_at_ms=now,
                messages=tuple(messages),
                agent_session=resolved_agent_session,
            )
            self._conversations[record.conversation_id] = record
            self._persist()
            return record

    def save_agent_session(
        self,
        conversation_id: str,
        *,
        scope: str,
        agent_session: dict[str, Any],
    ) -> StudioConversationRecord:
        with self._lock:
            existing = self.ensure_conversation(conversation_id, scope=scope)
            now = _now_ms()
            record = StudioConversationRecord(
                conversation_id=existing.conversation_id,
                title=existing.title,
                scope=existing.scope,
                created_at_ms=existing.created_at_ms,
                updated_at_ms=now,
                messages=existing.messages,
                agent_session=dict(agent_session),
            )
            self._conversations[record.conversation_id] = record
            self._persist()
            return record

    def delete_conversation(self, conversation_id: str) -> bool:
        with self._lock:
            normalized_id = str(conversation_id or "").strip()
            if not normalized_id:
                return False
            removed = self._conversations.pop(normalized_id, None)
            if removed is None:
                return False
            if self._active_conversation_id == normalized_id:
                self._active_conversation_id = ""
            self._persist()
            return True

    def _load(self) -> None:
        if not self._storage_path.exists():
            return
        try:
            data = json.loads(self._storage_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.exception("Failed to load AI conversations from %s", self._storage_path)
            return
        if not isinstance(data, dict):
            logger.warning("Ignoring malformed AI conversations payload: %r", data)
            return
        records = data.get("conversations", [])
        if not isinstance(records, list):
            return
        loaded: dict[str, StudioConversationRecord] = {}
        for item in records:
            if not isinstance(item, dict):
                continue
            try:
                record = _conversation_from_dict(item)
            except (KeyError, TypeError, ValueError):
                logger.warning("Skipping malformed AI conversation record: %r", item)
                continue
            loaded[record.conversation_id] = record
        with self._lock:
            self._conversations = loaded

    def _persist(self) -> None:
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "schemaVersion": _SCHEMA_VERSION,
                "conversations": [record.to_dict() for record in self._conversations.values()],
            }
            self._storage_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            logger.exception("Failed to persist AI conversations to %s", self._storage_path)

    @staticmethod
    def _resolve_storage_path() -> Path:
        return Path.home() / ".config" / "Feel8" / "ai_conversations.json"


def shared_conversation_store() -> StudioConversationStore:
    global _SHARED_CONVERSATION_STORE
    if _SHARED_CONVERSATION_STORE is None:
        _SHARED_CONVERSATION_STORE = StudioConversationStore()
    return _SHARED_CONVERSATION_STORE


def decode_conversation_messages(raw: str) -> tuple[StudioConversationMessage, ...]:
    if not raw:
        return ()
    payload = json.loads(raw)
    if not isinstance(payload, list):
        raise ValueError("conversation messages payload must be a list")
    return tuple(_message_from_dict(dict(item)) for item in payload if isinstance(item, dict))


def _conversation_from_dict(payload: dict[str, Any]) -> StudioConversationRecord:
    messages_payload = payload.get("messages", [])
    if not isinstance(messages_payload, list):
        messages_payload = []
    agent_session_payload = payload.get("agentSession")
    agent_session = dict(agent_session_payload) if isinstance(agent_session_payload, dict) else None
    return StudioConversationRecord(
        conversation_id=str(payload["conversationId"]),
        title=str(payload.get("title") or _DEFAULT_TITLE),
        scope=str(payload.get("scope") or "graph"),
        created_at_ms=int(payload.get("createdAtMs") or 0),
        updated_at_ms=int(payload.get("updatedAtMs") or 0),
        messages=tuple(_message_from_dict(dict(item)) for item in messages_payload if isinstance(item, dict)),
        agent_session=agent_session,
    )


def _message_from_dict(payload: dict[str, Any]) -> StudioConversationMessage:
    attachments_payload = payload.get("attachments", [])
    attachments: list[dict[str, str]] = []
    if isinstance(attachments_payload, list):
        for item in attachments_payload:
            if isinstance(item, dict):
                attachments.append(
                    {
                        "name": str(item.get("name", "")),
                        "content": str(item.get("content", "")),
                        "mime": str(item.get("mime", "image/png")),
                    }
                )
    return StudioConversationMessage(
        role=str(payload.get("role") or ""),
        content=str(payload.get("content") or ""),
        attachments=tuple(attachments),
        created_at_ms=int(payload.get("createdAtMs") or 0),
    )


def _conversation_title(existing_title: str, messages: tuple[StudioConversationMessage, ...]) -> str:
    current = str(existing_title or "").strip()
    if current and current != _DEFAULT_TITLE:
        return current
    for message in messages:
        if message.role == "user" and message.content.strip():
            text = " ".join(message.content.strip().split())
            if len(text) <= 48:
                return text
            return text[:45].rstrip() + "..."
    return _DEFAULT_TITLE


def _now_ms() -> int:
    return int(time.time() * 1000.0)

from __future__ import annotations

import json
import threading
from pathlib import Path

from f8pystudio.agents.conversations import (
    StudioConversationMessage,
    StudioConversationStore,
    decode_conversation_messages,
)


def test_conversation_store_round_trips_messages_and_agent_session(tmp_path: Path) -> None:
    storage_path = tmp_path / "ai_conversations.json"
    store = StudioConversationStore(storage_path=storage_path)

    first = store.save_messages(
        "",
        scope="graph",
        messages=(
            StudioConversationMessage(
                role="user",
                content="Build a graph that emits a 1hz sine wave",
                attachments=({"name": "sketch.png", "content": "abc", "mime": "image/png"},),
                created_at_ms=10,
            ),
            StudioConversationMessage(role="assistant", content="Done.", created_at_ms=20),
        ),
    )
    store.save_agent_session(
        first.conversation_id,
        scope="graph",
        agent_session={"sessionId": "maf-session", "serviceSessionId": "provider-session"},
    )

    reloaded = StudioConversationStore(storage_path=storage_path)
    record = reloaded.get_conversation(first.conversation_id)

    assert record is not None
    assert record.title == "Build a graph that emits a 1hz sine wave"
    assert record.scope == "graph"
    assert record.agent_session == {"sessionId": "maf-session", "serviceSessionId": "provider-session"}
    assert len(record.messages) == 2
    assert record.messages[0].attachments == ({"name": "sketch.png", "content": "abc", "mime": "image/png"},)


def test_conversation_store_lists_by_scope_and_delete(tmp_path: Path) -> None:
    store = StudioConversationStore(storage_path=tmp_path / "ai_conversations.json")
    graph_record = store.save_messages(
        "",
        scope="graph",
        messages=(StudioConversationMessage(role="user", content="graph request"),),
    )
    editor_record = store.save_messages(
        "",
        scope="editor",
        messages=(StudioConversationMessage(role="user", content="editor request"),),
    )

    graph_summaries = store.list_conversations(scope="graph")
    all_summaries = store.list_conversations()

    assert [summary.conversation_id for summary in graph_summaries] == [graph_record.conversation_id]
    assert {summary.conversation_id for summary in all_summaries} == {
        graph_record.conversation_id,
        editor_record.conversation_id,
    }
    assert store.delete_conversation(graph_record.conversation_id) is True
    assert store.delete_conversation(graph_record.conversation_id) is False
    assert store.get_conversation(graph_record.conversation_id) is None


def test_decode_conversation_messages_accepts_frontend_payload() -> None:
    payload = [
        {
            "role": "user",
            "content": "inspect this image",
            "createdAtMs": 123,
            "attachments": [{"name": "input.png", "content": "abc", "mime": "image/png"}],
        }
    ]

    messages = decode_conversation_messages(json.dumps(payload))

    assert messages == (
        StudioConversationMessage(
            role="user",
            content="inspect this image",
            attachments=({"name": "input.png", "content": "abc", "mime": "image/png"},),
            created_at_ms=123,
        ),
    )


def test_conversation_title_is_truncated_from_first_user_message(tmp_path: Path) -> None:
    store = StudioConversationStore(storage_path=tmp_path / "ai_conversations.json")
    record = store.save_messages(
        "",
        scope="graph",
        messages=(
            StudioConversationMessage(
                role="user",
                content="Please build a graph with many details and a fairly long descriptive request",
            ),
        ),
    )

    assert record.title == "Please build a graph with many details and a..."
    assert len(record.title) <= 48


def test_conversation_store_preserves_messages_when_agent_session_saves_from_worker_thread(tmp_path: Path) -> None:
    store = StudioConversationStore(storage_path=tmp_path / "ai_conversations.json")
    record = store.save_messages(
        "",
        scope="graph",
        messages=(StudioConversationMessage(role="user", content="first"),),
    )

    def _save_session() -> None:
        store.save_agent_session(
            record.conversation_id,
            scope="graph",
            agent_session={"sessionId": "worker-session"},
        )

    worker = threading.Thread(target=_save_session)
    worker.start()
    store.save_messages(
        record.conversation_id,
        scope="graph",
        messages=(
            StudioConversationMessage(role="user", content="first"),
            StudioConversationMessage(role="assistant", content="second"),
        ),
    )
    worker.join(timeout=2.0)

    saved = store.get_conversation(record.conversation_id)
    assert saved is not None
    assert [message.content for message in saved.messages] == ["first", "second"]
    assert saved.agent_session == {"sessionId": "worker-session"}

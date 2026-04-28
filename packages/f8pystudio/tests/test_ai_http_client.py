from __future__ import annotations

import json

from f8pystudio.ai_assist.http_client import (
    AiHttpClient,
    _StreamState,
    _chat_payload_openai,
    _responses_payload_openai,
)
from f8pystudio.ai_assist.registry import ProviderConfig


def test_responses_payload_contains_required_fields_and_cache() -> None:
    cfg = ProviderConfig(
        provider_id="openai",
        display_name="OpenAI",
        protocol="openai",
        api_mode="responses",
        endpoint="https://api.openai.com/v1",
        reasoning_level="high",
    )
    payload = _responses_payload_openai(
        cfg,
        "gpt-4.1",
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
        ],
        system="system",
        stream=True,
        reasoning_level="high",
        max_tokens=123,
    )

    assert payload["model"] == "gpt-4.1"
    assert payload["instructions"] == "system"
    assert payload["input"] == [{"role": "user", "content": "hello"}]
    assert payload["stream"] is True
    assert payload["max_output_tokens"] == 123
    assert payload["store"] is False
    assert payload["reasoning"] == {"effort": "high"}
    assert payload["prompt_cache_retention"] == "24h"
    assert payload["prompt_cache_key"] == "f8pystudio:openai:gpt-4.1:responses"


def test_third_party_responses_payload_omits_openai_cache_params() -> None:
    cfg = ProviderConfig(
        provider_id="compatible",
        display_name="Compatible",
        protocol="custom",
        api_mode="responses",
        endpoint="https://example.test/v1",
    )
    payload = _responses_payload_openai(
        cfg,
        "model-a",
        [{"role": "user", "content": "hello"}],
        system="",
        stream=False,
        reasoning_level="",
        max_tokens=16,
    )

    assert "prompt_cache_retention" not in payload
    assert "prompt_cache_key" not in payload


def test_responses_payload_converts_multimodal_parts() -> None:
    cfg = ProviderConfig(
        provider_id="openai",
        display_name="OpenAI",
        protocol="openai",
        api_mode="responses",
        endpoint="https://api.openai.com/v1",
    )
    payload = _responses_payload_openai(
        cfg,
        "gpt-4.1",
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "look"},
                    {"type": "image", "mime_type": "image/png", "image": "abc"},
                ],
            }
        ],
        system="",
        stream=False,
        reasoning_level="",
        max_tokens=16,
    )

    assert payload["input"] == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "look"},
                {
                    "type": "input_image",
                    "image_url": "data:image/png;base64,abc",
                    "detail": "auto",
                },
            ],
        }
    ]


def test_responses_non_stream_parser_reads_output_text() -> None:
    raw = json.dumps({"output_text": "hello"})
    assert AiHttpClient._extract_text(raw, "openai", "responses") == "hello"


def test_responses_non_stream_parser_reads_output_content_parts() -> None:
    raw = json.dumps({
        "output": [
            {
                "content": [
                    {"type": "output_text", "text": "hello"},
                    {"type": "output_text", "text": " world"},
                ]
            }
        ]
    })
    assert AiHttpClient._extract_text(raw, "openai", "responses") == "hello world"


def test_responses_stream_parser_emits_output_text_delta() -> None:
    chunks: list[str] = []
    done: list[tuple[str, str | None]] = []
    state = _StreamState(
        protocol="openai",
        api_mode="responses",
        on_chunk=chunks.append,
        on_done=lambda full, err: done.append((full, err)),
    )

    state.feed(b'data: {"type":"response.output_text.delta","delta":"hel"}\n\n')
    state.feed(b'data: {"type":"response.output_text.delta","delta":"lo"}\n\n')
    state.finish(None)

    assert chunks == ["hel", "lo"]
    assert done == [("hello", None)]


def test_responses_stream_parser_returns_errors() -> None:
    done: list[tuple[str, str | None]] = []
    state = _StreamState(
        protocol="openai",
        api_mode="responses",
        on_chunk=lambda _delta: None,
        on_done=lambda full, err: done.append((full, err)),
    )

    state.feed(b'data: {"type":"error","error":{"message":"bad request"}}\n\n')
    state.finish(None)

    assert done == [("", "bad request")]


def test_chat_completions_payload_and_parser_stay_unchanged() -> None:
    payload = _chat_payload_openai(
        "gpt-4o",
        [{"role": "user", "content": "hello"}],
        stream=True,
        reasoning_level="",
        max_tokens=32,
    )
    assert payload == {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
        "max_tokens": 32,
    }

    raw = json.dumps({"choices": [{"message": {"content": "ok"}}]})
    assert AiHttpClient._extract_text(raw, "openai", "chat_completions") == "ok"

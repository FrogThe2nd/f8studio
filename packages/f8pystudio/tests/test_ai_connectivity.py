from __future__ import annotations

import asyncio
import sys
import types

from f8pystudio.agents.connectivity import check_model_connectivity
from f8pystudio.agents.registry import ProviderConfig


class _FakeMessage:
    def __init__(self, role: str, contents: list[str]) -> None:
        self.role = role
        self.contents = contents


class _FakeClient:
    def __init__(self, calls: list[dict[str, object]]) -> None:
        self._calls = calls

    async def _response(self) -> object:
        return object()

    def get_response(self, messages: object, *, options: object = None) -> object:
        self._calls.append({"messages": messages, "options": options})
        return self._response()


class _SlowClient:
    async def _response(self) -> object:
        await asyncio.sleep(10)
        return object()

    def get_response(self, messages: object, *, options: object = None) -> object:
        return self._response()


def test_connectivity_uses_maf_chat_client_ping(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    selections: list[tuple[str, str]] = []
    monkeypatch.setitem(sys.modules, "agent_framework", types.SimpleNamespace(Message=_FakeMessage))

    def fake_build_chat_client(selection: object) -> _FakeClient:
        selections.append((selection.provider.provider_id, selection.model_id))
        return _FakeClient(calls)

    monkeypatch.setattr("f8pystudio.agents.connectivity.build_chat_client", fake_build_chat_client)
    provider = ProviderConfig(
        provider_id="openai",
        display_name="OpenAI",
        inference_service="openai_responses",
        api_key="sk-test",
        endpoint="https://api.openai.com/v1",
    )

    result = check_model_connectivity(provider, "gpt-4.1")

    assert result.success
    assert selections == [("openai", "gpt-4.1")]
    assert len(calls) == 1
    assert calls[0]["options"] == {"max_tokens": 8, "store": False}
    message = calls[0]["messages"][0]
    assert message.role == "user"
    assert message.contents == ["Reply with only: ok"]


def test_connectivity_reports_missing_model() -> None:
    provider = ProviderConfig(provider_id="openai", display_name="OpenAI", inference_service="openai_responses")

    result = check_model_connectivity(provider, "")

    assert not result.success
    assert result.error == "Model ID is required."


def test_connectivity_reports_maf_timeout(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "agent_framework", types.SimpleNamespace(Message=_FakeMessage))
    monkeypatch.setattr("f8pystudio.agents.connectivity.build_chat_client", lambda _selection: _SlowClient())
    provider = ProviderConfig(provider_id="openai", display_name="OpenAI", inference_service="openai_responses")

    result = check_model_connectivity(provider, "gpt-4.1", timeout_s=0.01)

    assert not result.success
    assert result.error == "Timed out while testing model connectivity."

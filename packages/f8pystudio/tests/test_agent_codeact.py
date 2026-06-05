from __future__ import annotations

import types
from f8pystudio.agents.codeact import (
    StudioCodeActConfig,
    build_codeact_context_provider,
    codeact_availability,
    codeact_skill_status,
)


def test_codeact_reports_missing_hyperlight_package(monkeypatch) -> None:
    def fake_import_module(name: str) -> object:
        if name == "agent_framework_hyperlight":
            raise ModuleNotFoundError("No module named 'agent_framework_hyperlight'")
        return object()

    monkeypatch.setattr("f8pystudio.agents.codeact.importlib.import_module", fake_import_module)

    availability = codeact_availability()

    assert availability.available is False
    assert availability.status == "unavailable"
    assert "agent_framework_hyperlight" in availability.reason


def test_codeact_reports_missing_sandbox_backend(monkeypatch) -> None:
    def fake_import_module(name: str) -> object:
        if name == "agent_framework_hyperlight":
            return types.SimpleNamespace(HyperlightCodeActProvider=object)
        return object()

    def fake_find_spec(name: str) -> object | None:
        if name == "python_guest":
            return object()
        if name == "hyperlight_sandbox_backend_wasm":
            return None
        return object()

    monkeypatch.setattr("f8pystudio.agents.codeact.importlib.import_module", fake_import_module)
    monkeypatch.setattr("f8pystudio.agents.codeact.importlib.util.find_spec", fake_find_spec)

    availability = codeact_availability()

    assert availability.available is False
    assert availability.status == "unavailable"
    assert "Wasm backend package is unavailable" in availability.reason

    status = codeact_skill_status()
    assert status.name == "codeact_diagnostics"
    assert status.available is False
    assert status.enabled is False
    assert status.label() == "CodeAct diagnostics: unavailable"


def test_build_codeact_context_provider_uses_hyperlight_when_available(monkeypatch) -> None:
    created: list[dict[str, object]] = []

    class FakeProvider:
        def __init__(
            self,
            *,
            tools: tuple[object, ...],
            approval_mode: object,
            workspace_root: str | None,
        ) -> None:
            created.append(
                {
                    "tools": tools,
                    "approval_mode": approval_mode,
                    "workspace_root": workspace_root,
                }
            )

    fake_module = types.SimpleNamespace(HyperlightCodeActProvider=FakeProvider)

    def fake_import_module(name: str) -> object:
        if name == "agent_framework_hyperlight":
            return fake_module
        raise ModuleNotFoundError(name)

    def fake_find_spec(name: str) -> object | None:
        if name in {"python_guest", "hyperlight_sandbox_backend_wasm"}:
            return object()
        return None

    monkeypatch.setattr("f8pystudio.agents.codeact.importlib.import_module", fake_import_module)
    monkeypatch.setattr("f8pystudio.agents.codeact.importlib.util.find_spec", fake_find_spec)

    def fake_tool() -> dict[str, object]:
        return {"ok": True}

    provider = build_codeact_context_provider(
        tools=(fake_tool,),
        config=StudioCodeActConfig(enabled=True, workspace_root="/workspace"),
    )

    assert isinstance(provider, FakeProvider)
    assert created == [
        {
            "tools": (fake_tool,),
            "approval_mode": "never_require",
            "workspace_root": "/workspace",
        }
    ]


def test_build_codeact_context_provider_skips_when_disabled() -> None:
    def fake_tool() -> dict[str, object]:
        return {"ok": True}

    provider = build_codeact_context_provider(
        tools=(fake_tool,),
        config=StudioCodeActConfig(enabled=False),
    )

    assert provider is None

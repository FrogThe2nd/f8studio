from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from f8pystudio.plugins.api import (
    PluginOperatorRegistration,
    PluginRendererRegistration,
    StudioPlugin,
    StudioPluginManifest,
)
from f8pystudio.plugins.loader import load_entrypoint_plugins


class _FakeEntryPoint:
    def __init__(self, *, name: str, value: str, loaded_obj: Any = None, error: BaseException | None = None) -> None:
        self.name = str(name)
        self.value = str(value)
        self._loaded_obj = loaded_obj
        self._error = error

    def load(self) -> Any:
        if self._error is not None:
            raise self._error
        return self._loaded_obj


class _FakeEntryPoints:
    def __init__(self, items: list[_FakeEntryPoint]) -> None:
        self._items = list(items)

    def select(self, *, group: str) -> list[_FakeEntryPoint]:
        _ = group
        return list(self._items)


@dataclass(frozen=True)
class _Plugin(StudioPlugin):
    manifest_obj: StudioPluginManifest

    def manifest(self) -> StudioPluginManifest:
        return self.manifest_obj


def _manifest(plugin_id: str) -> StudioPluginManifest:
    def _register(registry: object) -> object:
        return registry

    return StudioPluginManifest(
        plugin_id=plugin_id,
        plugin_name=f"{plugin_id} name",
        plugin_version="1.0.0",
        renderers=(PluginRendererRegistration(renderer_class=f"{plugin_id}.renderer", node_class=dict),),
        operators=(PluginOperatorRegistration(register=_register),),
    )


def test_plugin_loader_loads_entrypoints(monkeypatch) -> None:
    eps = _FakeEntryPoints(
        [
            _FakeEntryPoint(name="b", value="pkg.b:plugin", loaded_obj=_Plugin(_manifest("plugin_b"))),
            _FakeEntryPoint(name="a", value="pkg.a:manifest", loaded_obj=_manifest("plugin_a")),
        ]
    )
    monkeypatch.setattr("f8pystudio.plugins.loader.importlib.metadata.entry_points", lambda: eps)

    manifests = load_entrypoint_plugins()

    assert [m.plugin_id for m in manifests] == ["plugin_a", "plugin_b"]
    assert len(manifests[0].operators) == 1


def test_plugin_loader_rejects_invalid_manifest(monkeypatch, caplog) -> None:
    caplog.set_level(logging.ERROR)
    eps = _FakeEntryPoints([_FakeEntryPoint(name="bad", value="pkg.bad:plugin", loaded_obj=object())])
    monkeypatch.setattr("f8pystudio.plugins.loader.importlib.metadata.entry_points", lambda: eps)

    manifests = load_entrypoint_plugins()

    assert manifests == []
    assert "Plugin load failed" in caplog.text


def test_plugin_loader_isolation_on_plugin_failure(monkeypatch, caplog) -> None:
    caplog.set_level(logging.ERROR)
    eps = _FakeEntryPoints(
        [
            _FakeEntryPoint(name="bad", value="pkg.bad:plugin", error=RuntimeError("boom")),
            _FakeEntryPoint(name="good", value="pkg.good:plugin", loaded_obj=_manifest("plugin_good")),
        ]
    )
    monkeypatch.setattr("f8pystudio.plugins.loader.importlib.metadata.entry_points", lambda: eps)

    manifests = load_entrypoint_plugins()

    assert [m.plugin_id for m in manifests] == ["plugin_good"]
    assert "Plugin load failed" in caplog.text

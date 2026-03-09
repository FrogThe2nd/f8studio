from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from NodeGraphQt import BaseNode
from f8pysdk.runtime_node_registry import RuntimeNodeRegistry

from f8pystudio.plugin_api import PluginOperatorRegistration, PluginRendererRegistration, StudioPluginManifest
from f8pystudio.pystudio_program import PyStudioProgram
from f8pystudio.render_nodes.registry import RenderNodeRegistry


@dataclass
class _FakeRegistry:
    registered: list[tuple[str, type[Any]]]

    def register(self, renderer_key: str, renderer: type[Any]) -> None:
        self.registered.append((str(renderer_key), renderer))


def _manifest(
    plugin_id: str,
    renderer_key: str,
    node_class: type[Any] = dict,
    operator_reg: PluginOperatorRegistration | None = None,
) -> StudioPluginManifest:
    return StudioPluginManifest(
        plugin_id=plugin_id,
        plugin_name=plugin_id,
        plugin_version="1.0.0",
        renderers=(PluginRendererRegistration(renderer_class=renderer_key, node_class=node_class),),
        operators=((operator_reg,) if operator_reg is not None else ()),
    )


def test_program_applies_plugin_renderers(monkeypatch) -> None:
    fake_registry = _FakeRegistry(registered=[])
    monkeypatch.setattr(
        "f8pystudio.render_nodes.RenderNodeRegistry.instance",
        lambda: fake_registry,
    )
    manifests = [_manifest("plugin_a", "renderer.a"), _manifest("plugin_b", "renderer.b")]

    PyStudioProgram._apply_plugin_manifests_to_renderers(manifests)

    assert ("renderer.a", dict) in fake_registry.registered
    assert ("renderer.b", dict) in fake_registry.registered


def test_program_applies_no_manifest_without_side_effect(monkeypatch) -> None:
    called = {"count": 0}

    def _unexpected_instance() -> _FakeRegistry:
        called["count"] += 1
        return _FakeRegistry(registered=[])

    monkeypatch.setattr("f8pystudio.render_nodes.RenderNodeRegistry.instance", _unexpected_instance)

    PyStudioProgram._apply_plugin_manifests_to_renderers([])

    assert called["count"] == 0


def test_program_manifest_application_enables_renderer_key_in_registry() -> None:
    class _DummyNode(BaseNode):
        pass

    previous_instance = RenderNodeRegistry._instance
    try:
        RenderNodeRegistry._instance = None
        registry = RenderNodeRegistry.instance()
        PyStudioProgram._apply_plugin_manifests_to_renderers(
            [_manifest("plugin_tm", "template_match_capture", _DummyNode)]
        )
        assert registry.get("template_match_capture") is _DummyNode
    finally:
        RenderNodeRegistry._instance = previous_instance


def test_program_applies_plugin_operator_registration() -> None:
    called = {"count": 0}

    def _register(registry: RuntimeNodeRegistry | None) -> RuntimeNodeRegistry:
        assert isinstance(registry, RuntimeNodeRegistry)
        called["count"] += 1
        return registry

    manifest = _manifest("plugin_ops", "renderer.ops", operator_reg=PluginOperatorRegistration(register=_register))
    registry = RuntimeNodeRegistry.instance()
    PyStudioProgram._apply_plugin_manifests_to_runtime_registry([manifest], registry=registry)
    assert called["count"] == 1

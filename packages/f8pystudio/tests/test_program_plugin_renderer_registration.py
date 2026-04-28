from __future__ import annotations

from NodeGraphQt import BaseNode

from f8pystudio.plugins.api import PluginRendererRegistration, StudioPluginManifest
from f8pystudio.app.program import PyStudioProgram
from f8pystudio.render_nodes.registry import RenderNodeRegistry


def test_program_plugin_renderer_registration_enables_renderer_key() -> None:
    class _DummyRenderer(BaseNode):
        pass

    manifest = StudioPluginManifest(
        plugin_id="plugin_renderer",
        plugin_name="Plugin Renderer",
        plugin_version="1.0.0",
        renderers=(PluginRendererRegistration(renderer_class="viz_tcode", node_class=_DummyRenderer),),
    )

    previous_instance = RenderNodeRegistry._instance
    try:
        RenderNodeRegistry._instance = None
        registry = RenderNodeRegistry.instance()
        PyStudioProgram._apply_plugin_manifests_to_renderers([manifest])
        assert registry.get("viz_tcode") is _DummyRenderer
    finally:
        RenderNodeRegistry._instance = previous_instance

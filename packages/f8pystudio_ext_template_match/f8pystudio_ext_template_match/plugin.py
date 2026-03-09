from __future__ import annotations

from dataclasses import dataclass

from f8pystudio.plugin_api import PluginRendererRegistration, StudioPlugin, StudioPluginManifest

from .template_match_capture_render_node import TemplateMatchCaptureRenderNode


@dataclass(frozen=True)
class _TemplateMatchPlugin(StudioPlugin):
    def manifest(self) -> StudioPluginManifest:
        return StudioPluginManifest(
            plugin_id="template_match_capture",
            plugin_name="Template Match Capture",
            plugin_version="0.1.0",
            renderers=(
                PluginRendererRegistration(
                    renderer_class="template_match_capture",
                    node_class=TemplateMatchCaptureRenderNode,
                ),
            ),
        )


plugin = _TemplateMatchPlugin()


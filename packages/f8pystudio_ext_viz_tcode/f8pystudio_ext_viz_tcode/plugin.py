from __future__ import annotations

from dataclasses import dataclass

from f8pystudio.plugins.api import (
    PluginOperatorRegistration,
    PluginRendererRegistration,
    StudioPlugin,
    StudioPluginManifest,
)

from .operators.viz_tcode import register_operator as register_viz_tcode_operator
from .render_nodes.viz_tcode import VizTCodeRenderNode


@dataclass(frozen=True)
class _VizTCodePlugin(StudioPlugin):
    def manifest(self) -> StudioPluginManifest:
        return StudioPluginManifest(
            plugin_id="viz_tcode",
            plugin_name="Viz TCode",
            plugin_version="0.1.0",
            renderers=(
                PluginRendererRegistration(
                    renderer_class="viz_tcode",
                    node_class=VizTCodeRenderNode,
                ),
            ),
            operators=(
                PluginOperatorRegistration(
                    register=register_viz_tcode_operator,
                ),
            ),
        )


plugin = _VizTCodePlugin()


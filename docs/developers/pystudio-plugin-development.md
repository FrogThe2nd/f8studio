# PyStudio Plugin Development

This page is the entry point for developers who want to extend `f8pystudio` with plugin-provided renderers or operators.

## Mental Model

PyStudio plugins contribute capabilities through the `f8studio.pystudio.plugins` entrypoint group.

A plugin typically provides one or both of these:

- renderer registrations for Studio-side node rendering/UI behavior
- operator registrations that extend the runtime node registry used by PyStudio

## Key Pieces

- `StudioPluginManifest` declares plugin metadata plus renderer/operator registrations
- `PluginRendererRegistration` maps a `renderer_class` to a Studio node class
- `PluginOperatorRegistration` provides a registration function that writes into `RuntimeNodeRegistry`

## Typical Plugin Flow

1. Create a Python package for the plugin
2. Add a `pyproject.toml` entry under `project.entry-points."f8studio.pystudio.plugins"`
3. Return a `StudioPluginManifest` from the plugin entry object
4. Register renderers and/or operators explicitly
5. Install the package in the Studio environment and verify discovery/loading

## Existing Repo Examples

- `f8pystudio_ext_viz_tcode`: full plugin with renderer + operator registration
- `f8pystudio_ext_template_match`: renderer-focused workflow plugin

## Behavior To Keep In Mind

- Missing plugins can leave sessions with missing-node placeholders or degraded recovery paths
- Plugin-provided nodes should still have explicit, discoverable contracts
- Renderer logic belongs in the Studio/plugin layer; runtime logic belongs in runtime nodes/operators

## Validation Checklist

- Plugin package is installed in the active Studio environment
- Entry-point name resolves correctly
- Manifest metadata is stable
- Renderer class names match the runtime spec that refers to them
- Sessions fail gracefully if the plugin is absent

## Related Pages

- [Build from Source](build-from-source.md)
- [Plugin Workflows](../node-atlas/plugin-workflows.md)
- [TCodeViz Plugin Node](../node-atlas/f8-viz-tcode.md)


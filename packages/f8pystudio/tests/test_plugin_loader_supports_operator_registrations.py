from __future__ import annotations

from f8pysdk.runtime_node_registry import RuntimeNodeRegistry

from f8pystudio.plugins.api import PluginOperatorRegistration, StudioPluginManifest


def test_manifest_can_carry_operator_registration_callable() -> None:
    def _register(registry: RuntimeNodeRegistry | None) -> RuntimeNodeRegistry:
        return registry or RuntimeNodeRegistry.instance()

    manifest = StudioPluginManifest(
        plugin_id="plugin_ops",
        plugin_name="Plugin Ops",
        plugin_version="1.0.0",
        operators=(PluginOperatorRegistration(register=_register),),
    )

    assert len(manifest.operators) == 1
    out_registry = manifest.operators[0].register(None)
    assert isinstance(out_registry, RuntimeNodeRegistry)

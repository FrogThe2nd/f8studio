from __future__ import annotations

from f8pysdk.registry import create_runtime_node_registry

from f8pystudio.plugins.api import PluginOperatorRegistration, StudioPluginManifest


def test_manifest_can_carry_operator_registration_callable() -> None:
    def _register(registry: object) -> object:
        return registry

    manifest = StudioPluginManifest(
        plugin_id="plugin_ops",
        plugin_name="Plugin Ops",
        plugin_version="1.0.0",
        operators=(PluginOperatorRegistration(register=_register),),
    )

    assert len(manifest.operators) == 1
    registry = create_runtime_node_registry()
    out_registry = manifest.operators[0].register(registry)
    assert out_registry is registry

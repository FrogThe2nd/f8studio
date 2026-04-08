from __future__ import annotations

import logging

from f8pysdk.registry import create_runtime_node_registry

from f8pystudio.plugins.api import PluginOperatorRegistration, StudioPluginManifest
from f8pystudio.app.program import PyStudioProgram


def test_program_plugin_runtime_registration_is_applied() -> None:
    called = {"count": 0}
    registry = create_runtime_node_registry()

    def _register(received_registry: object) -> object:
        assert received_registry is registry
        called["count"] += 1
        return received_registry

    manifest = StudioPluginManifest(
        plugin_id="plugin_runtime",
        plugin_name="Plugin Runtime",
        plugin_version="1.0.0",
        operators=(PluginOperatorRegistration(register=_register),),
    )

    PyStudioProgram._apply_plugin_manifests_to_runtime_registry([manifest], registry=registry)
    assert called["count"] == 1


def test_program_plugin_runtime_registration_failure_is_isolated(caplog) -> None:
    caplog.set_level(logging.ERROR)

    def _raise(_registry: object) -> object:
        raise RuntimeError("boom")

    manifest = StudioPluginManifest(
        plugin_id="plugin_bad",
        plugin_name="Plugin Bad",
        plugin_version="1.0.0",
        operators=(PluginOperatorRegistration(register=_raise),),
    )

    registry = create_runtime_node_registry()
    PyStudioProgram._apply_plugin_manifests_to_runtime_registry([manifest], registry=registry)
    assert "Operator registration failed in plugin" in caplog.text

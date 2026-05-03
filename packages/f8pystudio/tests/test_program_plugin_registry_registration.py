from __future__ import annotations

import logging

from f8pysdk.registry import Registry

from f8pystudio.plugins.api import PluginOperatorRegistration, StudioPluginManifest
from f8pystudio.app.program import PyStudioProgram
from f8pystudio.studio_specs.identifiers import SERVICE_CLASS
from f8pystudio_ext_viz_tcode.operators.viz_tcode import OPERATOR_CLASS, register_operator as register_viz_tcode_operator

PALETTE_CATEGORY_VIZ = "f8.pystudio.viz"


def test_program_plugin_registry_registration_is_applied() -> None:
    called = {"count": 0}
    registry = Registry()

    def _register(received_registry: Registry) -> Registry:
        assert received_registry is registry
        called["count"] += 1
        return received_registry

    manifest = StudioPluginManifest(
        plugin_id="plugin_registry",
        plugin_name="Plugin Registry",
        plugin_version="1.0.0",
        operators=(PluginOperatorRegistration(register=_register),),
    )

    PyStudioProgram._apply_plugin_manifests_to_registry([manifest], registry=registry)
    assert called["count"] == 1


def test_program_plugin_registry_registration_failure_is_isolated(caplog) -> None:
    caplog.set_level(logging.ERROR)

    def _raise(_registry: Registry) -> Registry:
        raise RuntimeError("boom")

    manifest = StudioPluginManifest(
        plugin_id="plugin_bad",
        plugin_name="Plugin Bad",
        plugin_version="1.0.0",
        operators=(PluginOperatorRegistration(register=_raise),),
    )

    registry = Registry()
    PyStudioProgram._apply_plugin_manifests_to_registry([manifest], registry=registry)
    assert "Operator registration failed in plugin" in caplog.text


def test_program_plugin_registry_registration_preserves_pystudio_viz_category() -> None:
    manifest = StudioPluginManifest(
        plugin_id="plugin_tcode_viz",
        plugin_name="Plugin TCode Viz",
        plugin_version="1.0.0",
        operators=(PluginOperatorRegistration(register=register_viz_tcode_operator),),
    )

    registry = Registry()

    PyStudioProgram._apply_plugin_manifests_to_registry([manifest], registry=registry)

    spec = next(
        (
            operator_spec
            for operator_spec in registry.operator_specs(SERVICE_CLASS)
            if str(operator_spec.operatorClass or "") == OPERATOR_CLASS
        ),
        None,
    )

    assert spec is not None
    assert spec.paletteCategory == PALETTE_CATEGORY_VIZ

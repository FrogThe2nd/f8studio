from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from NodeGraphQt import BaseNode
from f8pysdk.nodes import OperatorNode
from f8pysdk.registry import Registry
from f8pysdk.service_runtime_tools.inventory.catalog import ServiceCatalog
from f8pysdk.specs import F8OperatorSchemaVersion, F8OperatorSpec, F8RuntimeNode, F8ServiceSchemaVersion, F8ServiceSpec

from f8pystudio.plugins.api import PluginOperatorRegistration, PluginRendererRegistration, StudioPluginManifest
from f8pystudio.app.program import PyStudioProgram
from f8pystudio.studio_specs.registry import SERVICE_CLASS, create_pystudio_registry
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
    registry = Registry()

    def _register(received_registry: Registry) -> Registry:
        assert received_registry is registry
        called["count"] += 1
        return received_registry

    manifest = _manifest("plugin_ops", "renderer.ops", operator_reg=PluginOperatorRegistration(register=_register))
    PyStudioProgram._apply_plugin_manifests_to_registry([manifest], registry=registry)
    assert called["count"] == 1


def test_program_injects_plugin_operator_specs_into_catalog() -> None:
    catalog = ServiceCatalog.instance()
    catalog.clear()
    registry = Registry.wrap(create_pystudio_registry())

    class _PluginTestRuntimeNode(OperatorNode):
        def __init__(
            self,
            *,
            node_id: str,
            node: F8RuntimeNode,
            initial_state: dict[str, Any] | None = None,
        ) -> None:
            del node
            del initial_state
            super().__init__(node_id=node_id)

    def _register(received_registry: Registry) -> Registry:
        assert received_registry is registry
        registry.register_operator(
            F8OperatorSpec(
                schemaVersion=F8OperatorSchemaVersion.f8operator_1,
                serviceClass=SERVICE_CLASS,
                operatorClass="f8.viz.plugin_test",
                version="1.0.0",
                label="Plugin Test Viz",
                rendererClass="plugin_test_renderer",
            ),
            _PluginTestRuntimeNode,
            overwrite=True,
        )
        return received_registry

    manifest = _manifest(
        "plugin_ops_catalog",
        "plugin_test_renderer",
        operator_reg=PluginOperatorRegistration(register=_register),
    )

    try:
        PyStudioProgram._apply_plugin_manifests_to_registry([manifest], registry=registry)
        injected_service_class = PyStudioProgram._inject_pystudio_specs_from_registry(catalog, registry=registry)
        operator_classes = {str(op.operatorClass) for op in catalog.operators.all() if op.serviceClass == SERVICE_CLASS}

        assert injected_service_class == SERVICE_CLASS
        assert "f8.viz.plugin_test" in operator_classes
    finally:
        catalog.clear()


def test_build_node_classes_use_canonical_type_ids_not_palette_categories() -> None:
    catalog = ServiceCatalog.instance()
    catalog.clear()

    try:
        catalog.register_service(
            F8ServiceSpec(
                schemaVersion=F8ServiceSchemaVersion.f8service_1,
                serviceClass="f8.pyengine",
                version="1.0.0",
                label="PyEngine",
                paletteCategory="f8.pyengine.services",
            )
        )
        catalog.register_operator(
            F8OperatorSpec(
                schemaVersion=F8OperatorSchemaVersion.f8operator_1,
                serviceClass="f8.pyengine",
                operatorClass="f8.tick",
                version="1.0.0",
                label="Tick",
                paletteCategory="f8.pyengine.execution",
            )
        )

        node_types = {str(node_cls.type_) for node_cls in PyStudioProgram.build_node_classes()}

        assert "svc.f8.pyengine" in node_types
        assert "f8.pyengine.f8.tick" in node_types
        assert "f8.pyengine.services.f8.pyengine" not in node_types
        assert "f8.pyengine.execution.f8.tick" not in node_types
    finally:
        catalog.clear()

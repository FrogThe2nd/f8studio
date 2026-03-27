from __future__ import annotations

from types import SimpleNamespace

from f8pystudio.nodegraph.node_model import F8StudioNodeModel
from f8pystudio.widgets.node_property_panel.editor import F8StudioSingleNodePropertiesWidget


def test_node_model_node_purpose_round_trips_through_f8_sys() -> None:
    model = F8StudioNodeModel()

    model.nodePurpose = "  Map the unpacked payload into canonical fields.  "

    assert model.nodePurpose == "Map the unpacked payload into canonical fields."
    assert model.f8_sys["nodePurpose"] == "Map the unpacked payload into canonical fields."


def test_node_model_set_property_normalizes_node_purpose() -> None:
    model = F8StudioNodeModel()

    model.set_property("nodePurpose", "  Extract skeleton joints  ")

    assert model.nodePurpose == "Extract skeleton joints"


def test_node_model_emits_graph_property_changed_for_f8_ui() -> None:
    seen: list[tuple[object, str, object]] = []
    model = F8StudioNodeModel()
    owner = SimpleNamespace()
    owner.graph = SimpleNamespace(
        property_changed=SimpleNamespace(emit=lambda node, name, value: seen.append((node, name, value)))
    )
    model._owner_node = owner

    model.set_property("f8_ui", {"stateFields": {"trigger": {"globalHotkey": "F10"}}})

    assert seen == [(owner, "f8_ui", {"stateFields": {"trigger": {"globalHotkey": "F10"}}})]


def test_node_model_skips_system_property_emit_when_graph_is_none() -> None:
    model = F8StudioNodeModel()
    model._owner_node = SimpleNamespace(graph=None)

    model.set_property("f8_ui", {"stateFields": {"trigger": {"globalHotkey": "F10"}}})


def test_property_panel_reloads_when_system_spec_changes() -> None:
    seen: list[str] = []
    fake_self = SimpleNamespace(
        _editor=SimpleNamespace(reload=lambda: seen.append("reload")),
        _node_id="nodeA",
    )
    node = SimpleNamespace(id="nodeA")

    F8StudioSingleNodePropertiesWidget._on_graph_property_changed(fake_self, node, "f8_spec", {"stateFields": []})

    assert seen == ["reload"]

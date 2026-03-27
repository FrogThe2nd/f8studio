from __future__ import annotations

from types import SimpleNamespace

from f8pystudio.nodegraph.node_model import F8StudioNodeModel


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

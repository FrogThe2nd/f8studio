from __future__ import annotations

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

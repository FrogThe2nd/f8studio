from __future__ import annotations

from f8pystudio.nodegraph.items import service_node_painting as painting


class _NodeItemStub:
    def __init__(self) -> None:
        self.name = "Example Node"
        self.type_ = "f8.example"
        self.tooltip = ""

    def setToolTip(self, tooltip: str) -> None:
        self.tooltip = tooltip


def test_tooltip_disable_marks_disabled_nodes() -> None:
    node_item = _NodeItemStub()

    painting.tooltip_disable(node_item, True)

    assert "(DISABLED)" in node_item.tooltip
    assert "Example Node" in node_item.tooltip
    assert "f8.example" in node_item.tooltip


def test_tooltip_disable_leaves_enabled_nodes_clean() -> None:
    node_item = _NodeItemStub()

    painting.tooltip_disable(node_item, False)

    assert "(DISABLED)" not in node_item.tooltip
    assert "Example Node" in node_item.tooltip

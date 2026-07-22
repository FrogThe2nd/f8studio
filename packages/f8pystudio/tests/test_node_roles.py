from __future__ import annotations

import pytest

from f8pystudio.nodegraph.node_roles import NODE_ROLE_LABELS, NodeRole, node_role_for_palette_category


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("f8.pyengine.input", NodeRole.SOURCE),
        ("f8.pyengine.analysis", NodeRole.DETECT),
        ("f8.pyengine.signal", NodeRole.SHAPE),
        ("f8.pyengine.motion", NodeRole.SHAPE),
        ("f8.pyengine.expr", NodeRole.SHAPE),
        ("f8.pyengine.output", NodeRole.OUTPUT),
        ("f8.pyengine.debug", NodeRole.VIEW),
        ("f8.pystudio.viz", NodeRole.VIEW),
        ("f8.pyengine.execution", NodeRole.ADVANCED),
        ("custom.category", NodeRole.ADVANCED),
        ("", NodeRole.ADVANCED),
    ],
)
def test_node_role_for_palette_category(category: str, expected: NodeRole) -> None:
    assert node_role_for_palette_category(category) == expected


def test_every_node_role_has_a_display_label() -> None:
    assert set(NODE_ROLE_LABELS) == set(NodeRole)

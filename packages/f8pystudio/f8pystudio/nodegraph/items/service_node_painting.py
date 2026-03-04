from __future__ import annotations

from typing import Any


def tooltip_disable(node_item: Any, state: bool) -> None:
    """
    Updates the node tooltip when the node is enabled/disabled.
    """
    tooltip = "<b>{}</b>".format(node_item.name)
    if state:
        tooltip += ' <font color="red"><b>(DISABLED)</b></font>'
    tooltip += "<br/>{}<br/>".format(node_item.type_)
    node_item.setToolTip(tooltip)

from __future__ import annotations

from f8pystudio.nodegraph.service_basenode import F8StudioServiceNodeItem
from f8pystudio.nodegraph.service_node_graph_mixin import ServiceNodeGraphMixin
from f8pystudio.nodegraph.service_node_layout_mixin import ServiceNodeLayoutMixin
from f8pystudio.nodegraph.service_node_ports_mixin import ServiceNodePortsMixin
from f8pystudio.nodegraph.service_node_toolbar_mixin import ServiceNodeToolbarMixin


def test_service_node_item_uses_layout_mixin_methods_directly() -> None:
    assert F8StudioServiceNodeItem._content_rect_for_widgets is ServiceNodeLayoutMixin._content_rect_for_widgets
    assert F8StudioServiceNodeItem._apply_widget_resize_policy is ServiceNodeLayoutMixin._apply_widget_resize_policy
    assert F8StudioServiceNodeItem._refresh_pipe_visual_state is ServiceNodeLayoutMixin._refresh_pipe_visual_state


def test_service_node_item_uses_graph_and_toolbar_mixins_directly() -> None:
    assert F8StudioServiceNodeItem._backend_node is ServiceNodeGraphMixin._backend_node
    assert F8StudioServiceNodeItem._graph is ServiceNodeGraphMixin._graph
    assert F8StudioServiceNodeItem._ensure_service_toolbar is ServiceNodeToolbarMixin._ensure_service_toolbar
    assert (
        F8StudioServiceNodeItem.refresh_service_identity_bindings
        is ServiceNodeToolbarMixin.refresh_service_identity_bindings
    )


def test_service_node_item_keeps_port_construction_local_and_port_operations_in_mixin() -> None:
    assert "_create_input_port_item" in F8StudioServiceNodeItem.__dict__
    assert "_create_output_port_item" in F8StudioServiceNodeItem.__dict__
    assert F8StudioServiceNodeItem._add_port is ServiceNodePortsMixin._add_port
    assert F8StudioServiceNodeItem.add_input is ServiceNodePortsMixin.add_input
    assert F8StudioServiceNodeItem.add_output is ServiceNodePortsMixin.add_output
    assert F8StudioServiceNodeItem.from_dict is ServiceNodePortsMixin.from_dict

from __future__ import annotations

import logging
from typing import Any

from NodeGraphQt.base.commands import NodeAddedCmd
from NodeGraphQt.errors import NodeCreationError
from f8pysdk.codec import dump_json

from f8pysdk.specs import F8OperatorSpec, F8ServiceSpec, F8StateAccess

from f8pystudio.studio_specs.identifiers import SERVICE_CLASS as _CANVAS_SERVICE_CLASS_
from f8pystudio.studio_specs.identifiers import STUDIO_SERVICE_ID
from ..ui.support.ui_notifications import show_warning
from ..assets.variants.variant_ids import parse_variant_node_type
from .node_base import F8StudioBaseNode

logger = logging.getLogger(__name__)


class GraphFactoryFlowMixin:
    @staticmethod
    def _node_debug_identity(node: Any) -> tuple[str, str]:
        node_id = ""
        node_type = ""
        try:
            node_id = str(node.id or "")
        except (AttributeError, RuntimeError, TypeError):
            node_id = ""
        try:
            node_type = str(node.type_ or "")
        except (AttributeError, RuntimeError, TypeError):
            node_type = ""
        return node_id, node_type

    def _teardown_node(self, node: Any) -> None:
        if node is None:
            return
        try:
            node.on_graph_teardown()
        except AttributeError:
            return
        except Exception:
            node_id, node_type = self._node_debug_identity(node)
            logger.exception("node graph teardown failed nodeId=%s type=%s", node_id, node_type)

    def _teardown_nodes(self, nodes: list[Any]) -> None:
        seen_refs: set[int] = set()
        for node in list(nodes or []):
            marker = id(node)
            if marker in seen_refs:
                continue
            seen_refs.add(marker)
            self._teardown_node(node)

    def _assign_node_id(self, node: F8StudioBaseNode) -> F8StudioBaseNode:
        new_nid = self.new_unique_node_id()
        node.model.id = new_nid
        node.view.id = new_nid
        # Seed identity state into UI properties early (these are runtime-owned readonly
        # fields; the runtime compiler skips ro values so they won't be deployed).
        spec = node.spec
        if isinstance(spec, F8OperatorSpec):
            if "operatorId" in node.model.properties or "operatorId" in node.model.custom_properties:
                node.set_property("operatorId", str(new_nid), push_undo=False)
        elif isinstance(spec, F8ServiceSpec):
            if "svcId" in node.model.properties or "svcId" in node.model.custom_properties:
                node.set_property("svcId", str(new_nid), push_undo=False)
        return node

    def create_node(
        self,
        node_type,
        name=None,
        selected=True,
        color=None,
        text_color=None,
        pos=None,
        push_undo=True,
        *,
        begin_undo_macro: bool = True,
    ):
        """
        Create a new node in the node graph.

        See Also:
            To list all node types :meth:`NodeGraph.registered_nodes`

        Args:
            node_type (str): node instance type.
            name (str): set name of the node.


            selected (bool): set created node to be selected.
            color (tuple or str): node color ``(255, 255, 255)`` or ``"#FFFFFF"``.
            text_color (tuple or str): text color ``(255, 255, 255)`` or ``"#FFFFFF"``.
            pos (list[int, int]): initial x, y position for the node (default: ``(0, 0)``).
            push_undo (bool): register the command to the undo stack. (default: True)

        Returns:
            BaseNode: the created instance of the node.
        """
        variant_id = parse_variant_node_type(str(node_type))
        if variant_id:
            record = self._variant_record(variant_id)
            if record is None:
                raise NodeCreationError(f'Can\'t find variant: "{variant_id}"')
            base_node_type = str(record.baseNodeType or "").strip()
            if not base_node_type:
                raise NodeCreationError(f'Variant "{variant_id}" has empty baseNodeType')
            variant_name = str(record.name or "").strip()
            variant_spec_json = dump_json(record.spec, mode="json") if not isinstance(record.spec, dict) else record.spec
            if not isinstance(variant_spec_json, dict):
                raise NodeCreationError(f'Variant "{variant_id}" has invalid spec')
            node = self.create_node(
                base_node_type,
                name=name or variant_name or None,
                selected=selected,
                color=color,
                text_color=text_color,
                pos=pos,
                push_undo=push_undo,
                begin_undo_macro=begin_undo_macro,
            )
            if node is None:
                return None
            self._apply_variant_to_node(
                node=node,
                variant_record=record,
                variant_spec_json=variant_spec_json,
            )
            return node

        node = self._node_factory.create_node_instance(node_type)
        if node:
            if not isinstance(node, F8StudioBaseNode):
                raise NodeCreationError(
                    f'Node "{node_type}" must inherit from F8StudioBaseNode, got {type(node).__name__}.'
                )
            node = self._assign_node_id(node)

            node._graph = self
            node.model._graph_model = self.model

            wid_types = node.model.__dict__.pop("_TEMP_property_widget_types")
            prop_attrs = node.model.__dict__.pop("_TEMP_property_attrs")

            if self.model.get_node_common_properties(node.type_) is None:
                node_attrs = {node.type_: {n: {"widget_type": wt} for n, wt in wid_types.items()}}
                for pname, pattrs in prop_attrs.items():
                    node_attrs[node.type_][pname].update(pattrs)
                self.model.set_node_common_properties(node_attrs)

            accept_types = node.model.__dict__.pop("_TEMP_accept_connection_types")
            for ptype, pdata in accept_types.get(node.type_, {}).items():
                for pname, accept_data in pdata.items():
                    for accept_ntype, accept_ndata in accept_data.items():
                        for accept_ptype, accept_pnames in accept_ndata.items():
                            for accept_pname in accept_pnames:
                                self._model.add_port_accept_connection_type(
                                    port_name=pname,
                                    port_type=ptype,
                                    node_type=node.type_,
                                    accept_pname=accept_pname,
                                    accept_ptype=accept_ptype,
                                    accept_ntype=accept_ntype,
                                )
            reject_types = node.model.__dict__.pop("_TEMP_reject_connection_types")
            for ptype, pdata in reject_types.get(node.type_, {}).items():
                for pname, reject_data in pdata.items():
                    for reject_ntype, reject_ndata in reject_data.items():
                        for reject_ptype, reject_pnames in reject_ndata.items():
                            for reject_pname in reject_pnames:
                                self._model.add_port_reject_connection_type(
                                    port_name=pname,
                                    port_type=ptype,
                                    node_type=node.type_,
                                    reject_pname=reject_pname,
                                    reject_ptype=reject_ptype,
                                    reject_ntype=reject_ntype,
                                )

            node.NODE_NAME = self.get_unique_name(name or node.NODE_NAME)
            node.model.name = node.NODE_NAME
            node.model.selected = selected

            def format_color(clr):
                if isinstance(clr, str):
                    clr = clr.strip("#")
                    return tuple(int(clr[i : i + 2], 16) for i in (0, 2, 4))
                return clr

            if color:
                node.model.color = format_color(color)
            if text_color:
                node.model.text_color = format_color(text_color)
            if pos:
                node.model.pos = [float(pos[0]), float(pos[1])]

            # initial node direction layout.
            node.model.layout_direction = self.layout_direction()
            if not self._loading_session:
                node.set_property(
                    "f8_ui_state",
                    self.set_node_layer_ids_in_ui_state_for_editor(node.ui_state(), self.default_layer_ids_for_new_node()),
                    push_undo=False,
                )

            node.update()

            if not self._loading_session:
                ok, msg = self._ensure_operator_in_container(node, pos=pos)
                if not ok:
                    if msg:
                        show_warning(self._notification_parent(), "Container required", msg)
                    return None

            undo_cmd = NodeAddedCmd(self, node, pos=node.model.pos, emit_signal=True)
            if push_undo and begin_undo_macro:
                undo_label = 'create node: "{}"'.format(node.NODE_NAME)
                self._undo_stack.beginMacro(undo_label)
                for n in self.selected_nodes():
                    n.set_property("selected", False, push_undo=True)
                self._undo_stack.push(undo_cmd)
                self._undo_stack.endMacro()
            elif push_undo:
                for n in self.selected_nodes():
                    n.set_property("selected", False, push_undo=True)
                self._undo_stack.push(undo_cmd)
            else:
                for n in self.selected_nodes():
                    n.set_property("selected", False, push_undo=False)
                undo_cmd.redo()

            self.refresh_layer_visibility()
            return node

        raise NodeCreationError('Can\'t find node: "{}"'.format(node_type))

    def add_node(self, node, pos=None, selected=True, push_undo=True, inherite_graph_style=True):
        """Add an existing node to the graph.
        Args:
            node (BaseNode): node instance to add.
            pos (list[int, int]): initial x, y position for the node (default: ``(0, 0)``).
            selected (bool): set created node to be selected.
            push_undo (bool): register the command to the undo stack. (default: True)
            inherite_graph_style (bool): whether to inherite the graph style settings.
        """

        if not self._loading_session:
            node = self._assign_node_id(node)
        if pos:
            node.model.pos = [float(pos[0]), float(pos[1])]
            node.view.xy_pos = [float(pos[0]), float(pos[1])]

        if not self._loading_session:
            ok, msg = self._ensure_operator_in_container(node, pos=pos)
            if not ok:
                if msg:
                    show_warning(self._notification_parent(), "Container required", msg)
                return

        super().add_node(
            node, pos=pos, selected=selected, push_undo=push_undo, inherite_graph_style=inherite_graph_style
        )
        self.refresh_layer_visibility()

    def delete_node(self, node, push_undo=True):
        """
        Delete a node from the graph.

        Note: deleting a service container also deletes its bound operators.
        """
        nodes = self._expand_delete_nodes([node])
        return self._delete_nodes_expanded(nodes, push_undo=push_undo)

    def delete_nodes(self, nodes, push_undo=True):
        """
        Delete multiple nodes from the graph.

        Note: deleting any service container also deletes its bound operators.
        """
        nodes = self._expand_delete_nodes(list(nodes or []))
        return self._delete_nodes_expanded(nodes, push_undo=push_undo)

    def _delete_nodes_expanded(self, nodes: list[Any], *, push_undo: bool = True) -> Any:
        """
        Delete nodes where the list is already expanded (ie. includes container children).
        """
        if not nodes:
            return
        self.repair_stale_port_connection_refs()
        self._teardown_nodes(nodes)

        service_ids: set[str] = set()
        for n in list(nodes or []):
            try:
                # Reclaim applies to *service instance nodes* (containers + standalone services).
                spec = n.spec
                if not isinstance(spec, F8ServiceSpec):
                    continue
                sid = str(n.id or "").strip()
                svc_class = str(spec.serviceClass or "")
                if sid and sid != STUDIO_SERVICE_ID and svc_class != _CANVAS_SERVICE_CLASS_:
                    service_ids.add(sid)
            except (AttributeError, TypeError):
                continue

        # NodeGraphQt's `delete_nodes([single])` calls back into `self.delete_node(...)`,
        # which we override. Avoid recursion by using `delete_node` directly for the
        # single-node case.
        if len(nodes) == 1:
            node = nodes[0]
            r = super().delete_node(node, push_undo=push_undo)
        else:
            r = super().delete_nodes(nodes, push_undo=push_undo)

        for sid in sorted(service_ids):
            self._schedule_service_reclaim(sid, delay_ms=3000)
        return r

    def new_unique_node_id(self) -> str:
        """Generate a new unique node ID."""
        uuid = self.uuid_generator.random(self.uuid_length)
        while self.get_node_by_id(uuid) is not None:
            uuid = self.uuid_generator.random(self.uuid_length)
        return uuid

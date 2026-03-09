from __future__ import annotations

from f8pysdk.msgspec_codec import dump_json
import copy
import logging
from typing import Any

from NodeGraphQt import BaseNode

from f8pysdk import F8OperatorSpec, F8ServiceSpec

logger = logging.getLogger(__name__)


class GraphDuplicateActionsMixin:
    _DUPLICATE_OFFSET_X: float = 50.0
    _DUPLICATE_OFFSET_Y: float = 50.0

    def install_duplicate_context_menu_for_nodes(self, node_classes: list[type]) -> None:
        nodes_menu = self.context_nodes_menu()
        if nodes_menu is None:
            return
        for node_cls in list(node_classes or []):
            node_type = str(node_cls.type_ or "")
            if not node_type or node_type in self._duplicate_menu_node_types:
                continue
            nodes_menu.add_command(
                self.tr("Duplicate"),
                func=self._on_duplicate_node_menu_action,
                node_type=node_type,
            )
            self._duplicate_menu_node_types.add(node_type)

    def _on_duplicate_node_menu_action(self, graph: Any, node: Any) -> None:
        _ = graph
        if not isinstance(node, BaseNode):
            return
        self._duplicate_single_node(node)

    def _duplicate_single_node(self, node: BaseNode) -> BaseNode | None:
        source_node_type = str(node.type_ or "").strip()
        if not source_node_type:
            return None

        source_x = 0.0
        source_y = 0.0
        try:
            source_pos = node.pos()
            source_x = float(source_pos[0])
            source_y = float(source_pos[1])
        except (AttributeError, RuntimeError, TypeError, ValueError, IndexError):
            source_x = 0.0
            source_y = 0.0

        source_name = ""
        try:
            source_name = str(node.name() or "").strip()
        except (AttributeError, RuntimeError, TypeError):
            source_name = ""

        duplicated_node = self.create_node(
            source_node_type,
            name=source_name or None,
            selected=True,
            pos=(source_x + self._DUPLICATE_OFFSET_X, source_y + self._DUPLICATE_OFFSET_Y),
            push_undo=True,
        )
        if not isinstance(duplicated_node, BaseNode):
            return None

        self._copy_node_spec_and_ui(source=node, target=duplicated_node)
        self._copy_node_custom_properties(source=node, target=duplicated_node)
        return duplicated_node

    @staticmethod
    def _copy_node_spec_and_ui(*, source: BaseNode, target: BaseNode) -> None:
        try:
            source_spec = source.spec  # type: ignore[attr-defined]
        except (AttributeError, RuntimeError, TypeError):
            source_spec = None

        if isinstance(source_spec, (F8OperatorSpec, F8ServiceSpec)):
            try:
                target.set_spec(dump_json(source_spec, mode="json"), rebuild=True)  # type: ignore[attr-defined]
            except (AttributeError, RuntimeError, TypeError, ValueError):
                logger.exception("Failed to copy spec for duplicate node: type=%s", str(source.type_ or ""))

        try:
            source_ui_overrides = source.ui_overrides()  # type: ignore[attr-defined]
        except (AttributeError, RuntimeError, TypeError):
            source_ui_overrides = {}
        if isinstance(source_ui_overrides, dict):
            try:
                target.set_ui_overrides(copy.deepcopy(source_ui_overrides), rebuild=True)  # type: ignore[attr-defined]
            except (AttributeError, RuntimeError, TypeError, ValueError):
                logger.exception("Failed to copy ui overrides for duplicate node: type=%s", str(source.type_ or ""))

    @staticmethod
    def _copy_node_custom_properties(*, source: BaseNode, target: BaseNode) -> None:
        try:
            source_custom = dict(source.model.custom_properties or {})
        except (AttributeError, RuntimeError, TypeError, ValueError):
            source_custom = {}

        for prop_name in sorted(source_custom.keys()):
            if prop_name in {"operatorId", "svcId"}:
                continue

            can_set_target_prop = False
            try:
                can_set_target_prop = prop_name in target.model.properties or prop_name in target.model.custom_properties
            except (AttributeError, RuntimeError, TypeError):
                can_set_target_prop = False
            if not can_set_target_prop:
                continue

            try:
                source_value = source.get_property(prop_name)
            except (AttributeError, RuntimeError, TypeError, KeyError):
                continue
            try:
                target.set_property(prop_name, copy.deepcopy(source_value), push_undo=False)
            except (AttributeError, RuntimeError, TypeError, ValueError, KeyError):
                logger.exception(
                    "Failed to copy custom property during duplicate: node_type=%s property=%s",
                    str(source.type_ or ""),
                    prop_name,
                )

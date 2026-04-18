from __future__ import annotations

from typing import Any, cast

from qtpy import QtCore, QtGui, QtWidgets

from NodeGraphQt.custom_widgets.nodes_tree import TYPE_NODE

from f8pysdk.specs import F8OperatorSpec, F8ServiceSpec, palette_category_from_spec

from ...assets.variants.variant_ids import build_variant_node_type
from ...assets.variants.variant_repository import delete_variant, list_variants_for_base
from ...nodegraph.spec_visibility import typed_spec_template_or_none
from ...ui.support.ui_notifications import show_warning
from ..dialogs.node_docs_dialog import show_node_docs_dialog


class NodeLibraryTreeInteractionMixin:
    def _build_search_blob(self, *, node_cls: Any, node_name: str, node_id: str) -> str:
        parts: list[str] = [str(node_name), str(node_id)]
        parts.append(str(node_cls.NODE_NAME))
        spec = typed_spec_template_or_none(node_cls)
        if spec is not None:
            if spec.label:
                parts.append(str(spec.label))
            if spec.description:
                parts.append(str(spec.description))
            parts.extend(str(tag) for tag in list(spec.tags or []))
            if isinstance(spec, F8OperatorSpec):
                parts.append(str(spec.serviceClass))
                parts.append(str(spec.operatorClass))
            else:
                parts.append(str(spec.serviceClass))
        return " ".join(parts).lower()

    def _matches_search(self, *, node_cls: Any, node_name: str, node_id: str) -> bool:
        host = cast(Any, self)
        query = host._search_text
        if not query:
            return True
        haystack = self._build_search_blob(node_cls=node_cls, node_name=node_name, node_id=node_id)
        for token in query.split():
            if token not in haystack:
                return False
        return True

    @staticmethod
    def _variant_search_blob(variant: Any) -> str:
        parts = [
            str(variant.name or ""),
            str(variant.description or ""),
            " ".join(str(t) for t in list(variant.tags or [])),
        ]
        spec = variant.spec if isinstance(variant.spec, dict) else {}
        if isinstance(spec, dict):
            parts.append(str(spec.get("label") or ""))
            parts.append(str(spec.get("description") or ""))
            parts.append(" ".join(str(t) for t in list(spec.get("tags") or [])))
        return " ".join(parts).lower()

    def _variant_matches_search(self, variant: Any) -> bool:
        host = cast(Any, self)
        query = host._search_text
        if not query:
            return True
        haystack = self._variant_search_blob(variant)
        for token in query.split():
            if token not in haystack:
                return False
        return True

    @staticmethod
    def _spec_description(spec: F8OperatorSpec | F8ServiceSpec) -> str:
        desc = str(spec.description or "").strip()
        if desc:
            return desc
        label = str(spec.label or "").strip()
        if label:
            return label
        return ""

    def _show_spec_dialog(self, *, node_id: str, node_name: str) -> None:
        host = cast(Any, self)
        node_cls = host._factory.nodes.get(node_id) if host._factory is not None else None
        if node_cls is None:
            return
        spec = typed_spec_template_or_none(node_cls)
        if spec is None:
            return
        show_node_docs_dialog(parent=host, spec=spec, node_id=node_id, node_name=node_name)

    def _open_variant_catalog(self, *, base_node_type: str, base_node_name: str) -> None:
        host = cast(Any, self)
        callback = host._on_open_variant_catalog
        if callable(callback):
            callback(base_node_type=base_node_type, base_node_name=base_node_name)
            return
        try:
            from ...assets.ui.variant_catalog_dialog import VariantCatalogDialog

            dlg = VariantCatalogDialog(
                parent=host.window(),
                base_node_type=base_node_type,
                base_node_name=base_node_name,
                node_graph=host._node_graph,
            )
            dlg.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
            dlg.destroyed.connect(host._on_variant_catalog_destroyed)  # type: ignore[attr-defined]
            host._variant_catalog_dialogs.append(dlg)
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
        except Exception as exc:
            show_warning(host, "Open Variant Catalog Failed", str(exc))

    def _on_variant_catalog_destroyed(self, obj: Any) -> None:
        host = cast(Any, self)
        host._variant_catalog_dialogs = [dialog for dialog in host._variant_catalog_dialogs if dialog is not obj]

    def _on_item_clicked(self, item: QtWidgets.QTreeWidgetItem, column: int) -> None:
        host = cast(Any, self)
        if item.type() != TYPE_NODE:
            return
        _ = column
        graph = host._node_graph
        if graph is None:
            return
        node_id = str(item.data(0, host._ROLE_NODE_ID) or "")
        if not node_id:
            return
        node_name = str(item.data(0, host._ROLE_NODE_NAME) or item.text(0))
        is_variant = bool(item.data(0, host._ROLE_IS_VARIANT))
        if is_variant:
            variant_name = str(item.data(0, host._ROLE_VARIANT_NAME) or "").strip()
            if variant_name:
                node_name = f"{node_name}\n - {variant_name}"
        graph.begin_node_placement(node_id, node_name)

    def _on_context_menu_requested(self, pos: QtCore.QPoint) -> None:
        host = cast(Any, self)
        item = host.itemAt(pos)
        if item is None or item.type() != TYPE_NODE:
            return
        menu = QtWidgets.QMenu(host)
        graph = host._node_graph
        if graph is None:
            return
        base_node_id = str(item.data(0, host._ROLE_BASE_NODE_ID) or item.data(0, host._ROLE_NODE_ID) or "")
        base_node_name = str(item.data(0, host._ROLE_NODE_NAME) or item.text(0))
        if not base_node_id:
            return
        is_variant_item = bool(item.data(0, host._ROLE_IS_VARIANT))
        variant_id = str(item.data(0, host._ROLE_VARIANT_ID) or "").strip()
        variant_name = str(item.data(0, host._ROLE_VARIANT_NAME) or "").strip()

        action_info = menu.addAction("Show Details")
        action_manage = menu.addAction("Open Variant Catalog...")
        action_delete_variant = None
        if is_variant_item and variant_id:
            menu.addSeparator()
            action_delete_variant = menu.addAction("Delete Variant...")

        variants = list_variants_for_base(base_node_id)
        if variants:
            variants_menu = menu.addMenu("Variants")
            variant_actions: dict[QtGui.QAction, tuple[str, str]] = {}
            for v in variants:
                act = variants_menu.addAction(str(v.name or v.variantId))
                variant_actions[act] = (str(v.variantId), str(v.name or ""))
        else:
            variant_actions = {}

        chosen = menu.exec(host.viewport().mapToGlobal(pos))
        if chosen == action_info:
            host._show_spec_dialog(node_id=base_node_id, node_name=base_node_name)
            return
        if chosen == action_manage:
            host._open_variant_catalog(base_node_type=base_node_id, base_node_name=base_node_name)
            return
        if action_delete_variant is not None and chosen == action_delete_variant:
            reply = QtWidgets.QMessageBox.question(host, "Delete variant", f"Delete variant '{variant_name}'?")
            if reply != QtWidgets.QMessageBox.StandardButton.Yes:
                return
            removed = delete_variant(variant_id)
            if not removed:
                show_warning(host, "Delete variant failed", f"Variant not found: {variant_id}")
            return
        if chosen in variant_actions:
            variant_id, variant_name = variant_actions[chosen]
            variant_node_type = build_variant_node_type(variant_id)
            placement_label = str(base_node_name)
            if variant_name:
                placement_label = f"{base_node_name}\n - {variant_name}"
            graph.begin_node_placement(variant_node_type, placement_label)

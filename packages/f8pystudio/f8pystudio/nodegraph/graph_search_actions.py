# pyright: reportPrivateUsage=false
from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol, cast

from qtpy import QtWidgets

from f8pysdk.specs import F8VariantRecord
from f8pysdk.specs import palette_category_from_spec

from ..assets.common import JsonObject
from ..assets.components.component_repository import component_entry, list_component_entries
from ..assets.projects.project_storage import ProjectStorageService
from ..ui.support.node_category_labels import display_node_category_label
from ..ui.support.ui_notifications import show_warning
from ..assets.variants.variant_ids import build_variant_node_type
from ..assets.variants.variant_repository import list_variants_grouped_by_base
from .service_basenode import F8StudioServiceNodeItem
from .spec_visibility import is_hidden_spec_node_class, typed_spec_template_or_none


class _NodeFactoryProtocol(Protocol):
    names: dict[str, list[str]]
    nodes: dict[str, type[object]]


class _TabSearchViewerProtocol(Protocol):
    def tab_search_set_nodes(self, nodes: dict[str, list[str]]) -> None: ...

    def tab_search_toggle(self) -> None: ...


class _SearchNodeProtocol(Protocol):
    id: object
    view: object


class _GraphSearchHost(Protocol):
    @property
    def node_factory(self) -> _NodeFactoryProtocol: ...

    def viewer(self) -> _TabSearchViewerProtocol | None: ...

    def create_node(self, node_type_id: str, *, pos: tuple[float, float] | None = None) -> object: ...

    def prepare_insert_graph_from_component(self, component_payload: JsonObject, *, component_name: str) -> object: ...

    def apply_insert_graph(self, request: object, *, anchor_x: float, anchor_y: float) -> object: ...

    def serialize_session(self) -> JsonObject: ...

    def serialize_publish_session(self) -> JsonObject: ...

    def load_session_payload(self, payload: JsonObject) -> None: ...

    def all_nodes(self) -> list[_SearchNodeProtocol]: ...

    def _notification_parent(self) -> QtWidgets.QWidget | None: ...


class _NodeClassNameProtocol(Protocol):
    NODE_NAME: str


class GraphSearchActionsMixin:
    _tab_search_node_type_aliases: dict[str, str] | None = None
    _tab_search_component_ids: dict[str, str] | None = None

    def _node_type_aliases(self) -> dict[str, str]:
        aliases = self._tab_search_node_type_aliases
        if aliases is None:
            aliases = {}
            self._tab_search_node_type_aliases = aliases
        return aliases

    def _component_search_ids(self) -> dict[str, str]:
        component_ids = self._tab_search_component_ids
        if component_ids is None:
            component_ids = {}
            self._tab_search_component_ids = component_ids
        return component_ids

    def toggle_node_search(self) -> None:
        """
        Open node search (tab search menu).

        NodeGraphQt's default implementation only opens when the viewer is
        under the mouse; for keyboard shortcuts we want it to open when the
        viewer has focus.
        """
        host = cast(_GraphSearchHost, cast(object, self))
        viewer = host.viewer()
        if viewer is None:
            return
        factory = host.node_factory
        names = factory.names
        nodes = factory.nodes

        node_type_aliases = self._node_type_aliases()
        node_type_aliases.clear()
        component_ids = self._component_search_ids()
        component_ids.clear()
        alias_counts: dict[str, int] = {}
        filtered_names: dict[str, list[str]] = {}
        base_node_names: dict[str, str] = {}
        base_node_categories: dict[str, str] = {}
        for node_name, node_types in dict(names or {}).items():
            kept_types: list[str] = []
            for node_type in list(node_types or []):
                node_type_id = str(node_type)
                node_cls = nodes.get(node_type_id)
                if node_cls is not None and self._is_hidden_node_class(node_cls):
                    continue
                category = self._tab_search_category_for_node(node_cls=node_cls, node_type_id=node_type_id)
                base_node_names[node_type_id] = str(node_name)
                base_node_categories[node_type_id] = category
                node_leaf = node_type_id.split(".")[-1] if "." in node_type_id else node_type_id
                alias_base = f"{category}.{node_leaf}"
                count = int(alias_counts.get(alias_base, 0)) + 1
                alias_counts[alias_base] = count
                alias_id = alias_base if count == 1 else f"{alias_base}_{count}"
                node_type_aliases[alias_id] = node_type_id
                kept_types.append(alias_id)
            if kept_types:
                filtered_names[str(node_name)] = kept_types
        variants_by_base = list_variants_grouped_by_base(include_uninstalled=False)
        self._append_variant_search_entries(
            filtered_names=filtered_names,
            alias_counts=alias_counts,
            nodes=nodes,
            base_node_names=base_node_names,
            base_node_categories=base_node_categories,
            variants_by_base=variants_by_base,
        )
        self._append_component_search_entries(filtered_names=filtered_names, alias_counts=alias_counts)

        viewer.tab_search_set_nodes(filtered_names)
        viewer.tab_search_toggle()

    def _append_variant_search_entries(
        self,
        *,
        filtered_names: dict[str, list[str]],
        alias_counts: dict[str, int],
        nodes: dict[str, type[object]],
        base_node_names: dict[str, str],
        base_node_categories: dict[str, str],
        variants_by_base: dict[str, list[F8VariantRecord]],
    ) -> None:
        """
        Add saved variants to tab-search without requiring dynamic node-class registration.
        Variants are grouped up front so tab-search does not re-scan the full
        catalog once per base node.
        """
        node_type_aliases = self._node_type_aliases()
        seen_variant_ids: set[str] = set()
        for base_node_type in list(base_node_names.keys()):
            for variant in list(variants_by_base.get(base_node_type, [])):
                variant_id = str(variant.variantId or "").strip()
                if not variant_id or variant_id in seen_variant_ids:
                    continue
                seen_variant_ids.add(variant_id)
                base_node_type = str(variant.baseNodeType or "").strip()
                if not base_node_type:
                    continue
                base_node_cls = nodes.get(base_node_type)
                if base_node_cls is not None and self._is_hidden_node_class(base_node_cls):
                    continue
                category = str(base_node_categories.get(base_node_type) or "").strip()
                if not category:
                    category = self._tab_search_category_for_node(node_cls=base_node_cls, node_type_id=base_node_type)
                if not category:
                    category = "uncategorized"

                base_node_name = str(base_node_names.get(base_node_type) or "").strip()
                if not base_node_name and base_node_cls is not None:
                    named_node_cls = cast(type[_NodeClassNameProtocol], base_node_cls)
                    base_node_name = str(named_node_cls.NODE_NAME or "").strip()
                if not base_node_name:
                    base_node_name = base_node_type.split(".")[-1] if "." in base_node_type else base_node_type

                variant_name = str(variant.name or "").strip() or variant_id
                base_leaf = base_node_type.split(".")[-1] if "." in base_node_type else base_node_type
                variant_leaf = self._tab_search_leaf_token(variant_name)
                alias_base = f"{category}.{base_leaf}_variant_{variant_leaf}"
                count = int(alias_counts.get(alias_base, 0)) + 1
                alias_counts[alias_base] = count
                alias_id = alias_base if count == 1 else f"{alias_base}_{count}"
                node_type_aliases[alias_id] = build_variant_node_type(variant_id)

                display_name = f"{base_node_name} | {variant_name}"
                existing = filtered_names.get(display_name)
                if existing is None:
                    filtered_names[display_name] = [alias_id]
                else:
                    existing.append(alias_id)

    def _append_component_search_entries(
        self,
        *,
        filtered_names: dict[str, list[str]],
        alias_counts: dict[str, int],
    ) -> None:
        component_ids = self._component_search_ids()
        for entry in list_component_entries(include_uninstalled=False):
            component_id = str(entry.record.componentId or "").strip()
            if not component_id:
                continue
            component_name = str(entry.record.name or "").strip() or component_id
            component_leaf = self._tab_search_leaf_token(component_name)
            alias_base = f"components.component_{component_leaf}"
            count = int(alias_counts.get(alias_base, 0)) + 1
            alias_counts[alias_base] = count
            alias_id = alias_base if count == 1 else f"{alias_base}_{count}"
            component_ids[alias_id] = component_id

            display_name = f"Component | {component_name}"
            existing = filtered_names.get(display_name)
            if existing is None:
                filtered_names[display_name] = [alias_id]
            else:
                existing.append(alias_id)

    @staticmethod
    def _tab_search_leaf_token(value: str) -> str:
        """
        Normalize free-form names to a deterministic token safe for tab-search alias ids.
        """
        token = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value or ""))
        token = token.strip("_")
        return token or "variant"

    @staticmethod
    def _tab_search_category_for_node(*, node_cls: type[object] | None, node_type_id: str) -> str:
        _ = node_type_id
        if node_cls is None:
            return "uncategorized"
        spec = typed_spec_template_or_none(node_cls)
        if spec is None:
            return "uncategorized"
        return display_node_category_label(palette_category_from_spec(spec))

    def _on_search_triggered(self, node_type: str, pos: tuple[float, float]) -> None:
        """
        Resolve tab-search aliases to real node types before creating nodes.
        """
        host = cast(_GraphSearchHost, cast(object, self))
        component_id = self._component_search_ids().get(str(node_type))
        if component_id is not None:
            self._insert_component_from_search(component_id=component_id, pos=pos)
            return
        node_type_id = self._node_type_aliases().get(str(node_type), str(node_type))
        _ = host.create_node(node_type_id, pos=pos)

    def _insert_component_from_search(self, *, component_id: str, pos: tuple[float, float]) -> None:
        host = cast(_GraphSearchHost, cast(object, self))
        entry = component_entry(component_id, include_uninstalled=False)
        if entry is None:
            show_warning(host._notification_parent(), "Insert component failed", f"Component is unavailable: {component_id}")
            return
        try:
            request = host.prepare_insert_graph_from_component(
                entry.record.content,
                component_name=entry.record.name,
            )
            _ = host.apply_insert_graph(request, anchor_x=float(pos[0]), anchor_y=float(pos[1]))
        except Exception as exc:
            show_warning(host._notification_parent(), "Insert component failed", str(exc))

    @staticmethod
    def _is_hidden_node_class(node_cls: type[object]) -> bool:
        """
        Hide nodes explicitly marked `hiddenInPalette` from tab search while keeping them registered.
        """
        return is_hidden_spec_node_class(node_cls)

    def save_last_project(self) -> str:
        """
        Save the current session to the local project store.
        """
        host = cast(_GraphSearchHost, cast(object, self))
        record = ProjectStorageService().save_last_project(content=host.serialize_session())
        return str(record.projectId)

    def save_publish_session(self, file_path: str) -> str:
        path = Path(str(file_path or "").strip())
        if not str(path):
            raise ValueError("publish session path cannot be empty")
        path.parent.mkdir(parents=True, exist_ok=True)
        host = cast(_GraphSearchHost, cast(object, self))
        payload = host.serialize_publish_session()
        _ = path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return str(path)

    def load_last_project(self) -> str | None:
        """
        Load the last session from the local project store if it exists.
        """
        host = cast(_GraphSearchHost, cast(object, self))
        record = ProjectStorageService().load_last_project()
        if record is None:
            return None
        host.load_session_payload(record.content)
        return str(record.projectId)

    def _refresh_all_inline_state_read_only(self) -> None:
        """
        Apply inline readonly state for all nodes (best-effort).

        Needed after session load because NodeGraphQt can restore connections
        without triggering interactive port connect signals in our UI layer.
        """
        host = cast(_GraphSearchHost, cast(object, self))
        nodes = list(host.all_nodes() or [])
        for n in nodes:
            view = n.view
            if not isinstance(view, F8StudioServiceNodeItem):
                continue
            view.refresh_state_inline_control_read_only()
            view.update()

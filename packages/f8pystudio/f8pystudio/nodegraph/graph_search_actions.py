from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .service_basenode import F8StudioServiceNodeItem
from .session import last_session_path
from .spec_visibility import is_hidden_spec_node_class
from ..variants.variant_ids import build_variant_node_type
from f8pysdk.spec_metadata import palette_category_from_spec
from ..variants.variant_repository import load_library


class GraphSearchActionsMixin:
    def toggle_node_search(self):
        """
        Open node search (tab search menu).

        NodeGraphQt's default implementation only opens when the viewer is
        under the mouse; for keyboard shortcuts we want it to open when the
        viewer has focus.
        """
        names = self._node_factory.names
        nodes = self._node_factory.nodes

        self._tab_search_node_type_aliases = {}
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
                self._tab_search_node_type_aliases[alias_id] = node_type_id
                kept_types.append(alias_id)
            if kept_types:
                filtered_names[str(node_name)] = kept_types
        self._append_variant_search_entries(
            filtered_names=filtered_names,
            alias_counts=alias_counts,
            nodes=nodes,
            base_node_names=base_node_names,
            base_node_categories=base_node_categories,
        )

        self._viewer.tab_search_set_nodes(filtered_names)
        self._viewer.tab_search_toggle()

    def _append_variant_search_entries(
        self,
        *,
        filtered_names: dict[str, list[str]],
        alias_counts: dict[str, int],
        nodes: dict[str, Any],
        base_node_names: dict[str, str],
        base_node_categories: dict[str, str],
    ) -> None:
        """
        Add saved variants to tab-search without requiring dynamic node-class registration.
        """
        lib = load_library()
        for variant in list(lib.variants or []):
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
            if not base_node_name:
                if base_node_cls is not None:
                    try:
                        base_node_name = str(base_node_cls.NODE_NAME or "").strip()
                    except (AttributeError, RuntimeError, TypeError):
                        base_node_name = ""
            if not base_node_name:
                base_node_name = base_node_type.split(".")[-1] if "." in base_node_type else base_node_type

            variant_id = str(variant.variantId or "").strip()
            if not variant_id:
                continue
            variant_name = str(variant.name or "").strip() or variant_id

            base_leaf = base_node_type.split(".")[-1] if "." in base_node_type else base_node_type
            variant_leaf = self._tab_search_leaf_token(variant_name)
            alias_base = f"{category}.{base_leaf}_variant_{variant_leaf}"
            count = int(alias_counts.get(alias_base, 0)) + 1
            alias_counts[alias_base] = count
            alias_id = alias_base if count == 1 else f"{alias_base}_{count}"
            self._tab_search_node_type_aliases[alias_id] = build_variant_node_type(variant_id)

            display_name = f"{base_node_name} | {variant_name}"
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
    def _tab_search_category_for_node(*, node_cls: Any | None, node_type_id: str) -> str:
        if node_cls is None:
            return "uncategorized"
        spec = typed_spec_template_or_none(node_cls)
        if spec is None:
            return "uncategorized"
        return palette_category_from_spec(spec)

    def _on_search_triggered(self, node_type: str, pos: tuple[float, float]) -> None:
        """
        Resolve tab-search aliases to real node types before creating nodes.
        """
        node_type_id = self._tab_search_node_type_aliases.get(str(node_type), str(node_type))
        self.create_node(node_type_id, pos=pos)

    @staticmethod
    def _is_hidden_node_class(node_cls: Any) -> bool:
        """
        Hide nodes explicitly marked `hiddenInPalette` from tab search while keeping them registered.
        """
        return is_hidden_spec_node_class(node_cls)

    def save_last_session(self) -> str:
        """
        Save the current session to `~/.f8/studio/lastSession.json`.
        """
        path = last_session_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.save_session(str(path))
        return str(path)

    def save_publish_session(self, file_path: str) -> str:
        path = Path(str(file_path or "").strip())
        if not str(path):
            raise ValueError("publish session path cannot be empty")
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.serialize_publish_session()
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return str(path)

    def load_last_session(self) -> str | None:
        """
        Load `~/.f8/studio/lastSession.json` if it exists.
        """
        path = last_session_path()
        if not path.is_file():
            return None
        self.load_session(str(path))
        return str(path)

    def _refresh_all_inline_state_read_only(self) -> None:
        """
        Apply inline readonly state for all nodes (best-effort).

        Needed after session load because NodeGraphQt can restore connections
        without triggering interactive port connect signals in our UI layer.
        """
        nodes = list(self.all_nodes() or [])
        for n in nodes:
            view = n.view
            if not isinstance(view, F8StudioServiceNodeItem):
                continue
            view.refresh_state_inline_control_read_only()
            view.update()

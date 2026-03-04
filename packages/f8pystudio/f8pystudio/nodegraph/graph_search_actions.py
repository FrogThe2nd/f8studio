from __future__ import annotations

from typing import Any

from .service_basenode import F8StudioServiceNodeItem
from .session import last_session_path
from .spec_visibility import is_hidden_spec_node_class


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
        for node_name, node_types in dict(names or {}).items():
            kept_types: list[str] = []
            for node_type in list(node_types or []):
                node_type_id = str(node_type)
                node_cls = nodes.get(node_type_id)
                if node_cls is not None and self._is_hidden_node_class(node_cls):
                    continue
                category = self._tab_search_category_for_node(node_cls=node_cls, node_type_id=node_type_id)
                node_leaf = node_type_id.split(".")[-1] if "." in node_type_id else node_type_id
                alias_base = f"{category}.{node_leaf}"
                count = int(alias_counts.get(alias_base, 0)) + 1
                alias_counts[alias_base] = count
                alias_id = alias_base if count == 1 else f"{alias_base}_{count}"
                self._tab_search_node_type_aliases[alias_id] = node_type_id
                kept_types.append(alias_id)
            if kept_types:
                filtered_names[str(node_name)] = kept_types

        self._viewer.tab_search_set_nodes(filtered_names)
        self._viewer.tab_search_toggle()

    @staticmethod
    def _tab_search_category_for_node(*, node_cls: Any | None, node_type_id: str) -> str:
        if node_cls is not None:
            identifier = str(node_cls.__identifier__ or "").strip()
            if identifier:
                return identifier

        if "." in node_type_id:
            return ".".join(node_type_id.split(".")[:-1])
        return "uncategorized"

    def _on_search_triggered(self, node_type: str, pos: tuple[float, float]) -> None:
        """
        Resolve tab-search aliases to real node types before creating nodes.
        """
        node_type_id = self._tab_search_node_type_aliases.get(str(node_type), str(node_type))
        self.create_node(node_type_id, pos=pos)

    @staticmethod
    def _is_hidden_node_class(node_cls: Any) -> bool:
        """
        Hide nodes tagged with `__hidden__` from tab search while keeping them registered.
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
            view.refresh_inline_state_read_only()
            view.update()


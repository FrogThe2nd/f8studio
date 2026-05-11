from __future__ import annotations

from collections.abc import Iterable

from Qt import QtCore, QtWidgets
from NodeGraphQt.widgets.tab_search import TabSearchMenuWidget


def _normalize_search_text(value: str) -> str:
    return " ".join(str(value or "").casefold().replace("_", " ").replace(".", " ").split())


def _compact_search_text(value: str) -> str:
    return "".join(ch for ch in _normalize_search_text(value) if ch.isalnum())


def _subsequence_score(query: str, candidate: str) -> int | None:
    if not query:
        return 0
    search_from = 0
    first_index = -1
    previous_index = -1
    gap_score = 0
    for ch in query:
        index = candidate.find(ch, search_from)
        if index < 0:
            return None
        if first_index < 0:
            first_index = index
        if previous_index >= 0:
            gap_score += max(0, index - previous_index - 1)
        previous_index = index
        search_from = index + 1
    return first_index * 8 + gap_score


class FastTabSearchMenuWidget(TabSearchMenuWidget):
    """
    Faster replacement for NodeGraphQt's tab search menu.

    The upstream widget regex-scans every QAction and adds every match back to a
    QMenu on every keypress. With many service/operator classes this makes typing
    visibly stall. This widget keeps the public contract but caches normalized
    labels, uses a cheap scorer, and only renders the top results.
    """

    MAX_SEARCH_RESULTS = 64

    def __init__(self, node_dict: dict[str, list[str]] | None = None):
        super().__init__()
        self._search_index: list[tuple[str, str, str]] = []
        if node_dict:
            self.rebuild = True
            self.set_nodes(node_dict)

    def _clear_actions(self):
        for action in self._searched_actions:
            self.removeAction(action)
        del self._searched_actions[:]

    def _on_text_changed(self, text):
        self._clear_actions()

        query = _compact_search_text(str(text or ""))
        if not query:
            self._set_menu_visible(True)
            return

        self._set_menu_visible(False)
        ranked: list[tuple[int, str]] = []
        for action_name, normalized_name, compact_name in self._search_index:
            score = self._score_search_match(query, normalized_name, compact_name)
            if score is not None:
                ranked.append((score, action_name))

        ranked.sort(key=lambda item: (item[0], item[1].casefold()))
        action_names = [name for _, name in ranked[: self.MAX_SEARCH_RESULTS]]
        self._searched_actions = [self._actions[name] for name in action_names]
        self.addActions(self._searched_actions)

        if self._searched_actions:
            self.setActiveAction(self._searched_actions[0])

    @staticmethod
    def _score_search_match(query: str, normalized_name: str, compact_name: str) -> int | None:
        if compact_name.startswith(query):
            return 0
        if query in compact_name:
            return 100 + compact_name.find(query)
        if normalized_name.startswith(query):
            return 200
        if query in normalized_name:
            return 300 + normalized_name.find(query)
        fuzzy_score = _subsequence_score(query, compact_name)
        if fuzzy_score is None:
            return None
        return 1000 + fuzzy_score

    def set_nodes(self, node_dict=None):
        if not self._node_dict or self.rebuild:
            self._node_dict.clear()
            self._clear_actions()
            self._set_menu_visible(False)
            for menu in self._menus.values():
                self.removeAction(menu.menuAction())
            self._actions.clear()
            self._menus.clear()
            self._search_index.clear()
            for name, node_types in (node_dict or {}).items():
                if len(node_types) == 1:
                    self._node_dict[str(name)] = str(node_types[0])
                    continue
                for node_id in node_types:
                    self._node_dict[f"{name} ({node_id})"] = str(node_id)
            self.build_menu_tree()
            self._rebuild_search_index()
            self.rebuild = False

        self._show()

    def _rebuild_search_index(self) -> None:
        index: list[tuple[str, str, str]] = []
        for action_name in self._actions.keys():
            node_type = str(self._node_dict.get(action_name, ""))
            searchable = f"{action_name} {node_type}"
            normalized = _normalize_search_text(searchable)
            compact = _compact_search_text(searchable)
            index.append((action_name, normalized, compact))
        self._search_index = index

    @staticmethod
    def search_result_names_for_test(actions: Iterable[str], query: str, *, limit: int = MAX_SEARCH_RESULTS) -> list[str]:
        indexed = [
            (str(action), _normalize_search_text(str(action)), _compact_search_text(str(action)))
            for action in actions
        ]
        compact_query = _compact_search_text(query)
        ranked: list[tuple[int, str]] = []
        for action_name, normalized_name, compact_name in indexed:
            score = FastTabSearchMenuWidget._score_search_match(compact_query, normalized_name, compact_name)
            if score is not None:
                ranked.append((score, action_name))
        ranked.sort(key=lambda item: (item[0], item[1].casefold()))
        return [name for _, name in ranked[:limit]]

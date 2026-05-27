from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

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


@dataclass(frozen=True, slots=True)
class _SearchIndexEntry:
    action_name: str
    normalized_name: str
    compact_name: str
    compact_chars: frozenset[str]


class FastTabSearchMenuWidget(TabSearchMenuWidget):
    """
    Faster replacement for NodeGraphQt's tab search menu.

    The upstream widget regex-scans every QAction and adds every match back to a
    QMenu on every keypress. With many service/operator classes this makes typing
    visibly stall. This widget keeps the public contract but caches normalized
    labels, uses a cheap scorer, and only renders the top results.
    """

    MAX_SEARCH_RESULTS = 64
    SEARCH_DEBOUNCE_MS = 70

    def __init__(self, node_dict: dict[str, list[str]] | None = None):
        super().__init__()
        self._search_index: list[_SearchIndexEntry] = []
        self._search_indices_by_char: dict[str, set[int]] = {}
        self._all_search_indices: set[int] = set()
        self._pending_search_text = ""
        self._search_result_action_names: tuple[str, ...] = ()
        self._search_debounce_timer = QtCore.QTimer(self)
        self._search_debounce_timer.setSingleShot(True)
        self._search_debounce_timer.setInterval(self.SEARCH_DEBOUNCE_MS)
        self._search_debounce_timer.timeout.connect(self._apply_pending_search_text)  # type: ignore[attr-defined]
        if node_dict:
            self.rebuild = True
            self.set_nodes(node_dict)

    def _clear_actions(self):
        for action in self._searched_actions:
            self.removeAction(action)
            action.triggered.connect(self._on_search_submitted)
        del self._searched_actions[:]
        self._search_result_action_names = ()

    def _on_text_changed(self, text):
        self._pending_search_text = str(text or "")
        query = _compact_search_text(self._pending_search_text)
        if not query:
            self._search_debounce_timer.stop()
            self._apply_search_text(self._pending_search_text)
            return

        self._search_debounce_timer.start()

    def _on_search_submitted(self):
        self.flush_pending_search()
        if self._block_submit:
            self._close()
            return

        sender = self.sender()
        if isinstance(sender, QtWidgets.QAction):
            action = sender
        else:
            action = self._selected_search_action()
            if action is None:
                self._close()
                return

        node_type = self._node_dict.get(action.text())
        if node_type:
            self.search_submitted.emit(node_type)

        self._close()

    def _selected_search_action(self) -> QtWidgets.QAction | None:
        active_action = self.activeAction()
        if active_action in self._searched_actions:
            return active_action
        if self._searched_actions:
            return self._searched_actions[0]
        return None

    def _apply_pending_search_text(self) -> None:
        self._apply_search_text(self._pending_search_text)

    def _apply_search_text(self, text: str) -> None:
        query = _compact_search_text(str(text or ""))
        if not query:
            if self._searched_actions:
                self._clear_actions()
            self._set_menu_visible(True)
            return

        self._set_menu_visible(False)
        ranked: list[tuple[int, str]] = []
        for index in self._candidate_indices_for_query(query):
            entry = self._search_index[index]
            score = self._score_search_match(query, entry.normalized_name, entry.compact_name)
            if score is not None:
                ranked.append((score, entry.action_name))

        ranked.sort(key=lambda item: (item[0], item[1].casefold()))
        action_names = [name for _, name in ranked[: self.MAX_SEARCH_RESULTS]]
        result_signature = tuple(action_names)
        if result_signature == self._search_result_action_names:
            return

        self._clear_actions()
        self._search_result_action_names = result_signature
        self._searched_actions = [self._actions[name] for name in action_names]
        self.addActions(self._searched_actions)

        if self._searched_actions:
            self.setActiveAction(self._searched_actions[0])

    def _candidate_indices_for_query(self, query: str) -> set[int]:
        query_chars = frozenset(ch for ch in str(query or "") if ch.isalnum())
        if not query_chars:
            return set(self._all_search_indices)

        candidate_sets: list[set[int]] = []
        for ch in query_chars:
            indices = self._search_indices_by_char.get(ch)
            if not indices:
                return set()
            candidate_sets.append(indices)

        candidate_sets.sort(key=len)
        candidates = set(candidate_sets[0])
        for indices in candidate_sets[1:]:
            candidates.intersection_update(indices)
            if not candidates:
                break
        return candidates

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
            self._search_indices_by_char.clear()
            self._all_search_indices.clear()
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
        index: list[_SearchIndexEntry] = []
        indices_by_char: dict[str, set[int]] = {}
        for action_name in self._actions.keys():
            node_type = str(self._node_dict.get(action_name, ""))
            searchable = f"{action_name} {node_type}"
            normalized = _normalize_search_text(searchable)
            compact = _compact_search_text(searchable)
            entry = _SearchIndexEntry(
                action_name=action_name,
                normalized_name=normalized,
                compact_name=compact,
                compact_chars=frozenset(compact),
            )
            entry_index = len(index)
            index.append(entry)
            for ch in entry.compact_chars:
                if ch not in indices_by_char:
                    indices_by_char[ch] = set()
                indices_by_char[ch].add(entry_index)
        self._search_index = index
        self._search_indices_by_char = indices_by_char
        self._all_search_indices = set(range(len(index)))

    def flush_pending_search(self) -> None:
        self._search_debounce_timer.stop()
        self._apply_pending_search_text()

    def flush_pending_search_for_test(self) -> None:
        self.flush_pending_search()

    @staticmethod
    def search_result_names_for_test(actions: Iterable[str], query: str, *, limit: int = MAX_SEARCH_RESULTS) -> list[str]:
        indexed = []
        indices_by_char: dict[str, set[int]] = {}
        for action in actions:
            action_name = str(action)
            compact_name = _compact_search_text(action_name)
            entry = _SearchIndexEntry(
                action_name=action_name,
                normalized_name=_normalize_search_text(action_name),
                compact_name=compact_name,
                compact_chars=frozenset(compact_name),
            )
            entry_index = len(indexed)
            indexed.append(entry)
            for ch in entry.compact_chars:
                if ch not in indices_by_char:
                    indices_by_char[ch] = set()
                indices_by_char[ch].add(entry_index)

        compact_query = _compact_search_text(query)
        query_chars = frozenset(ch for ch in compact_query if ch.isalnum())
        if query_chars:
            candidate_sets: list[set[int]] = []
            for ch in query_chars:
                indices = indices_by_char.get(ch)
                if not indices:
                    return []
                candidate_sets.append(indices)
            candidate_sets.sort(key=len)
            candidate_indices = set(candidate_sets[0])
            for indices in candidate_sets[1:]:
                candidate_indices.intersection_update(indices)
                if not candidate_indices:
                    return []
        else:
            candidate_indices = set(range(len(indexed)))

        ranked: list[tuple[int, str]] = []
        for index in candidate_indices:
            entry = indexed[index]
            score = FastTabSearchMenuWidget._score_search_match(compact_query, entry.normalized_name, entry.compact_name)
            if score is not None:
                ranked.append((score, entry.action_name))
        ranked.sort(key=lambda item: (item[0], item[1].casefold()))
        return [name for _, name in ranked[:limit]]

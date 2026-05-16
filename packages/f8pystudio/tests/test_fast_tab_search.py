from __future__ import annotations

from qtpy import QtWidgets

from f8pystudio.nodegraph.fast_tab_search import FastTabSearchMenuWidget
from f8pystudio.nodegraph.viewer import F8StudioNodeViewer


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if isinstance(app, QtWidgets.QApplication):
        return app
    return QtWidgets.QApplication([])


def test_fast_tab_search_scores_direct_matches_before_fuzzy_matches() -> None:
    actions = [
        "Lowpass Filter",
        "Lua Script",
        "Range Map",
        "Lovense Out",
        "CPython Script",
    ]

    results = FastTabSearchMenuWidget.search_result_names_for_test(actions, "lua")

    assert results[0] == "Lua Script"
    assert "Lowpass Filter" not in results


def test_fast_tab_search_limits_rendered_search_results() -> None:
    actions = [f"Node {index:03d}" for index in range(200)]

    results = FastTabSearchMenuWidget.search_result_names_for_test(actions, "node")

    assert len(results) == FastTabSearchMenuWidget.MAX_SEARCH_RESULTS
    assert results[0] == "Node 000"


def test_fast_tab_search_menu_builds_cached_index_and_reuses_actions() -> None:
    _ensure_app()
    widget = FastTabSearchMenuWidget()
    widget._show = lambda: None  # type: ignore[method-assign]

    widget.rebuild = True
    widget.set_nodes({f"Node {index:03d}": [f"svc.category.node_{index:03d}"] for index in range(90)})

    assert len(widget._search_index) == 90
    widget.line_edit.setText("node")
    widget.flush_pending_search_for_test()

    assert len(widget._searched_actions) == FastTabSearchMenuWidget.MAX_SEARCH_RESULTS
    for action in widget._searched_actions:
        assert action.text().startswith("Node ")

    widget.close()
    widget.deleteLater()


def test_fast_tab_search_debounces_non_empty_queries() -> None:
    _ensure_app()
    widget = FastTabSearchMenuWidget()
    widget._show = lambda: None  # type: ignore[method-assign]

    widget.rebuild = True
    widget.set_nodes({f"Node {index:03d}": [f"svc.category.node_{index:03d}"] for index in range(90)})

    widget.line_edit.setText("n")
    QtWidgets.QApplication.processEvents()
    assert widget._searched_actions == []

    widget.line_edit.setText("node")
    widget.flush_pending_search_for_test()

    assert len(widget._searched_actions) == FastTabSearchMenuWidget.MAX_SEARCH_RESULTS
    assert widget._searched_actions[0].text() == "Node 000"

    widget.close()
    widget.deleteLater()


def test_fast_tab_search_uses_candidate_index_to_reject_missing_chars() -> None:
    actions = [
        "Lowpass Filter",
        "Lua Script",
        "Range Map",
        "Lovense Out",
    ]

    results = FastTabSearchMenuWidget.search_result_names_for_test(actions, "zz")

    assert results == []


def test_fast_tab_search_keeps_existing_render_when_result_names_do_not_change(monkeypatch) -> None:
    _ensure_app()
    widget = FastTabSearchMenuWidget()
    widget._show = lambda: None  # type: ignore[method-assign]

    widget.rebuild = True
    widget.set_nodes(
        {
            "Alpha Mixer": ["svc.category.alpha_mixer"],
            "Alpha Mapper": ["svc.category.alpha_mapper"],
            "Range Map": ["svc.category.range_map"],
        }
    )

    add_calls: list[tuple[str, ...]] = []
    remove_calls: list[str] = []
    original_add_actions = widget.addActions
    original_remove_action = widget.removeAction

    def record_add_actions(actions) -> None:
        action_names = tuple(action.text() for action in actions)
        add_calls.append(action_names)
        original_add_actions(actions)

    def record_remove_action(action) -> None:
        remove_calls.append(action.text())
        original_remove_action(action)

    monkeypatch.setattr(widget, "addActions", record_add_actions)
    monkeypatch.setattr(widget, "removeAction", record_remove_action)

    widget.line_edit.setText("alpha")
    widget.flush_pending_search_for_test()
    assert tuple(action.text() for action in widget._searched_actions) == ("Alpha Mapper", "Alpha Mixer")

    widget.line_edit.setText("alpha m")
    widget.flush_pending_search_for_test()

    assert add_calls == [("Alpha Mapper", "Alpha Mixer")]
    assert remove_calls == []

    widget.close()
    widget.deleteLater()


def test_viewer_uses_fast_tab_search_widget() -> None:
    _ensure_app()
    viewer = F8StudioNodeViewer()

    assert isinstance(viewer._search_widget, FastTabSearchMenuWidget)

    viewer.close()
    viewer.deleteLater()

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
    QtWidgets.QApplication.processEvents()

    assert len(widget._searched_actions) == FastTabSearchMenuWidget.MAX_SEARCH_RESULTS
    for action in widget._searched_actions:
        assert action.text().startswith("Node ")

    widget.close()
    widget.deleteLater()


def test_viewer_uses_fast_tab_search_widget() -> None:
    _ensure_app()
    viewer = F8StudioNodeViewer()

    assert isinstance(viewer._search_widget, FastTabSearchMenuWidget)

    viewer.close()
    viewer.deleteLater()

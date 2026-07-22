from __future__ import annotations

from qtpy import QtWidgets

from f8pystudio.assets.components.component_taxonomy import ComponentRole
from f8pystudio.assets.ui.component_catalog_dialog import ComponentCatalogDialog


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def test_component_catalog_role_combo_updates_role_filter(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr(ComponentCatalogDialog, "_render_browser_from_state", lambda self, *_args, **_kwargs: None)
    dialog = ComponentCatalogDialog(parent=None, node_graph=None)

    output_index = dialog._role_filter_combo.findData(ComponentRole.OUTPUT.value)
    assert output_index >= 0
    dialog._role_filter_combo.setCurrentIndex(output_index)
    assert dialog._current_component_role_filter() == ComponentRole.OUTPUT

    dialog._role_filter_combo.setCurrentIndex(0)
    assert dialog._current_component_role_filter() is None
    dialog.deleteLater()

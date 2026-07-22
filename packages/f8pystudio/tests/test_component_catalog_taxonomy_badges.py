from __future__ import annotations

from types import SimpleNamespace

from qtpy import QtWidgets

from f8pysdk.specs import F8ComponentRecord
from f8pystudio.assets.components.component_models import F8ComponentEntry, F8ComponentSourceKind
from f8pystudio.assets.ui.component_catalog_ui import ComponentCatalogUiMixin


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def test_component_catalog_builds_compact_taxonomy_badges() -> None:
    _ensure_app()
    parent = QtWidgets.QWidget()
    entry = F8ComponentEntry(
        record=F8ComponentRecord(
            componentId="component-output",
            name="Output",
            tags=[
                "role:output",
                "signal:vibrate",
                "signal:position",
                "protocol:lovense",
            ],
            content={},
        ),
        source=F8ComponentSourceKind.local,
    )
    host = SimpleNamespace(
        _build_text_badge=lambda badge_parent, text: ComponentCatalogUiMixin._build_text_badge(badge_parent, text),
        _build_taxonomy_values_badge=lambda **kwargs: ComponentCatalogUiMixin._build_taxonomy_values_badge(
            host, **kwargs
        ),
    )

    badges = ComponentCatalogUiMixin._build_taxonomy_badges(host, parent, entry)  # type: ignore[arg-type]

    assert [badge.objectName() for badge in badges] == [
        "component-role-badge",
        "component-signal-badge",
        "component-protocol-badge",
    ]
    assert [badge.text() for badge in badges] == ["Output", "Position +1", "Lovense"]
    assert badges[1].toolTip() == "Signals: position, vibrate"
    parent.deleteLater()

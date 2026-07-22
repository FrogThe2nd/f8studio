from __future__ import annotations

from qtpy import QtWidgets

from f8pystudio.assets.ui.component_metadata_dialogs import (
    ComponentMetadataDialog,
    ComponentOverwriteMetadataDialog,
)
from f8pystudio.assets.ui.project_asset_dialogs import AssetOverwriteChoice


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def test_component_metadata_dialog_round_trips_structured_and_free_tags() -> None:
    _ensure_app()
    dialog = ComponentMetadataDialog(
        parent=None,
        title="Edit Component",
        name="Video Output",
        description="Maps video motion to a device.",
        tags=[
            "role:output",
            "workflow:video",
            "signal:position",
            "protocol:buttplug",
            "level:starter",
            "author:example",
        ],
    )

    name, description, tags = dialog.values()

    assert name == "Video Output"
    assert description == "Maps video motion to a device."
    assert tags == [
        "role:output",
        "workflow:video",
        "signal:position",
        "protocol:buttplug",
        "level:starter",
        "author:example",
    ]
    assert dialog.findChild(QtWidgets.QComboBox, "component-role").currentData() == "output"
    dialog.deleteLater()


def test_component_overwrite_dialog_loads_selected_component_taxonomy() -> None:
    _ensure_app()
    dialog = ComponentOverwriteMetadataDialog(
        parent=None,
        title="Save As Component",
        name="New Component",
        description="",
        tags=["role:complete"],
        overwrite_choices=[
            AssetOverwriteChoice(
                asset_id="component-1",
                label="Existing Output",
                description="Existing description",
                tags=["role:output", "signal:vibrate", "protocol:lovense", "custom-tag"],
            )
        ],
    )
    overwrite_combo = dialog.findChild(QtWidgets.QComboBox, "component-overwrite-target")
    overwrite_combo.setCurrentIndex(overwrite_combo.findData("component-1"))

    name, description, tags, component_id = dialog.values()

    assert component_id == "component-1"
    assert name == "Existing Output"
    assert description == "Existing description"
    assert tags == ["role:output", "signal:vibrate", "protocol:lovense", "custom-tag"]
    dialog.deleteLater()

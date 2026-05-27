from __future__ import annotations

from datetime import timedelta, timezone

from qtpy import QtWidgets

from f8pystudio.assets.common import format_timestamp_for_local_display, format_timestamp_tooltip
from f8pystudio.assets.ui import project_asset_dialogs
from f8pystudio.assets.ui.project_asset_dialogs import (
    AssetVersionBrowserAction,
    AssetVersionBrowserDialog,
    AssetVersionBrowserItem,
    ProjectPickerDialog,
)
from f8pystudio.assets.projects.project_models import F8ProjectSummary


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def test_format_timestamp_for_local_display_converts_utc_to_target_timezone() -> None:
    eastern_daylight = timezone(timedelta(hours=-4), name="EDT")

    assert (
        format_timestamp_for_local_display("2026-04-15T13:45:00+00:00", local_tz=eastern_daylight)
        == "2026-04-15 09:45:00"
    )


def test_format_timestamp_for_local_display_treats_naive_timestamp_as_utc() -> None:
    eastern_daylight = timezone(timedelta(hours=-4), name="EDT")

    assert (
        format_timestamp_for_local_display("2026-04-15T13:45:00", local_tz=eastern_daylight)
        == "2026-04-15 09:45:00"
    )


def test_format_timestamp_tooltip_includes_timezone_abbreviation() -> None:
    eastern_daylight = timezone(timedelta(hours=-4), name="EDT")

    assert (
        format_timestamp_tooltip("2026-04-15T13:45:00+00:00", local_tz=eastern_daylight)
        == "2026-04-15 09:45:00 EDT"
    )


def test_asset_version_browser_dialog_formats_history_timestamps_locally(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr(
        project_asset_dialogs,
        "format_timestamp_for_local_display",
        lambda value: f"LOCAL<{value}>",
    )
    monkeypatch.setattr(
        project_asset_dialogs,
        "format_timestamp_tooltip",
        lambda value: f"TIP<{value}>",
    )

    dialog = AssetVersionBrowserDialog(
        parent=None,
        title="History",
        items=[
            AssetVersionBrowserItem(
                version_number=3,
                created_at="2026-04-15T13:45:00+00:00",
                change_summary="Updated graph",
            )
        ],
        load_payload=lambda _version_number: {"ok": True},
    )

    assert dialog._list.item(0).text() == "v3 | LOCAL<2026-04-15T13:45:00+00:00> | Updated graph"
    assert dialog._list.item(0).toolTip() == "TIP<2026-04-15T13:45:00+00:00>"


def test_asset_version_browser_dialog_orders_action_buttons() -> None:
    _ensure_app()

    dialog = AssetVersionBrowserDialog(
        parent=None,
        title="History",
        items=[
            AssetVersionBrowserItem(
                version_number=3,
                created_at="2026-04-15T13:45:00+00:00",
            )
        ],
        load_payload=lambda _version_number: {"ok": True},
        actions=[
            AssetVersionBrowserAction(action_key="restore", label="Restore As Latest"),
            AssetVersionBrowserAction(action_key="delete", label="Delete Version"),
        ],
    )

    assert [button.text() for button in dialog.findChildren(QtWidgets.QPushButton)] == [
        "Restore As Latest",
        "Delete Version",
        "Close",
    ]


def test_project_picker_dialog_formats_updated_timestamp_locally(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr(
        project_asset_dialogs,
        "format_timestamp_for_local_display",
        lambda value: f"LOCAL<{value}>",
    )
    monkeypatch.setattr(
        project_asset_dialogs,
        "format_timestamp_tooltip",
        lambda value: f"TIP<{value}>",
    )

    dialog = ProjectPickerDialog(
        parent=None,
        projects=[
            F8ProjectSummary(
                projectId="project-1",
                name="Demo Project",
                description="Demo",
                tags=["alpha"],
                latestVersionNumber=7,
                updatedAt="2026-04-15T13:45:00+00:00",
            )
        ],
        current_project_id="project-1",
    )

    assert "Updated: LOCAL<2026-04-15T13:45:00+00:00>" in dialog._details.toPlainText()
    assert dialog._details.toolTip() == "TIP<2026-04-15T13:45:00+00:00>"


def test_project_picker_dialog_orders_management_buttons() -> None:
    _ensure_app()

    dialog = ProjectPickerDialog(
        parent=None,
        projects=[
            F8ProjectSummary(
                projectId="project-1",
                name="Demo Project",
            )
        ],
        current_project_id="project-1",
        allow_history=True,
        allow_delete=True,
    )

    assert [button.text() for button in dialog.findChildren(QtWidgets.QPushButton)] == [
        "Open",
        "History...",
        "Delete",
        "Cancel",
    ]

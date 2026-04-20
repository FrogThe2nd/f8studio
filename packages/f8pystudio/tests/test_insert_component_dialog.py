from __future__ import annotations

from qtpy import QtWidgets

from f8pystudio.assets.components.component_models import (
    F8ComponentEntry,
    F8ComponentRecord,
    F8ComponentSourceKind,
    F8ComponentVisibility,
)
from f8pystudio.assets.ui.component_insert_dialog import (
    ComponentInsertDialog,
    community_component_entries,
    component_insert_badges,
    installed_component_entries,
)


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def _entry(
    *,
    component_id: str,
    name: str,
    source: F8ComponentSourceKind,
    installed: bool,
    subscribed: bool = False,
    owner_user_id: str | None = None,
) -> F8ComponentEntry:
    visibility = None
    owner_display_name = None
    if source == F8ComponentSourceKind.remote_public:
        visibility = F8ComponentVisibility.public
        owner_display_name = "Remote User"
    record = F8ComponentRecord(
        componentId=component_id,
        name=name,
        description=f"Description for {name}",
        tags=["graph", "demo"],
        content={"schemaVersion": "f8studio-session/1", "layout": {"nodes": {}, "connections": []}},
    )
    return F8ComponentEntry(
        record=record,
        source=source,
        visibility=visibility,
        ownerUserId=owner_user_id,
        ownerDisplayName=owner_display_name,
        installed=installed,
        subscribed=subscribed,
    )


def test_component_insert_dialog_entry_helpers_split_installed_and_community() -> None:
    local_entry = _entry(
        component_id="local-1",
        name="Local Component",
        source=F8ComponentSourceKind.local,
        installed=True,
    )
    installed_remote = _entry(
        component_id="remote-1",
        name="Subscribed Community",
        source=F8ComponentSourceKind.remote_public,
        installed=True,
        subscribed=True,
    )
    uninstalled_remote = _entry(
        component_id="remote-2",
        name="Fresh Community",
        source=F8ComponentSourceKind.remote_public,
        installed=False,
    )

    installed_names = [
        entry.record.name
        for entry in installed_component_entries([local_entry, installed_remote, uninstalled_remote], query="")
    ]
    community_names = [
        entry.record.name
        for entry in community_component_entries([local_entry, installed_remote, uninstalled_remote], query="")
    ]

    assert installed_names == ["Local Component", "Subscribed Community"]
    assert community_names == ["Fresh Community", "Subscribed Community"]


def test_component_insert_dialog_helpers_apply_search_and_badges() -> None:
    community_entry = _entry(
        component_id="remote-1",
        name="Searchable Community",
        source=F8ComponentSourceKind.remote_public,
        installed=False,
        subscribed=True,
    )

    filtered = community_component_entries([community_entry], query="searchable")
    assert [entry.record.componentId for entry in filtered] == ["remote-1"]

    badges = component_insert_badges(community_entry)
    assert badges == ["cloud", "public", "install on insert", "subscribed"]


def test_component_insert_dialog_helpers_apply_tab_filters() -> None:
    local_entry = _entry(
        component_id="local-1",
        name="Local Component",
        source=F8ComponentSourceKind.local,
        installed=True,
    )
    my_remote_entry = _entry(
        component_id="remote-mine",
        name="My Remote Component",
        source=F8ComponentSourceKind.remote_public,
        installed=True,
        owner_user_id="user-1",
    )
    subscribed_entry = _entry(
        component_id="remote-sub",
        name="Subscribed Community",
        source=F8ComponentSourceKind.remote_public,
        installed=True,
        subscribed=True,
        owner_user_id="user-2",
    )

    mine_only = installed_component_entries(
        [local_entry, my_remote_entry, subscribed_entry],
        query="",
        filter_value="mine",
        current_user_id="user-1",
    )
    assert [entry.record.componentId for entry in mine_only] == ["local-1", "remote-mine"]

    subscribed_only = community_component_entries(
        [my_remote_entry, subscribed_entry],
        query="",
        filter_value="subscribed",
        current_user_id="user-1",
    )
    assert [entry.record.componentId for entry in subscribed_only] == ["remote-sub"]


def test_component_insert_dialog_ignores_deleted_selection_wrapper(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr(
        "f8pystudio.assets.ui.component_insert_dialog.subscribe_components_changed",
        lambda _cb: (lambda: None),
    )
    dialog = ComponentInsertDialog(parent=None, node_graph=None)

    class _DeletedListWidget:
        def currentItem(self) -> None:
            raise RuntimeError("Internal C++ object (PySide6.QtWidgets.QListWidget) already deleted.")

    dialog._list = _DeletedListWidget()  # type: ignore[assignment]

    assert dialog._selected_entry() is None
    dialog._on_selection_changed()

    dialog.close()

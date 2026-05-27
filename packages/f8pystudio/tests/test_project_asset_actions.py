from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from qtpy import QtCore, QtWidgets

from f8pystudio.assets.components.component_catalog import ComponentCatalogService
from f8pystudio.assets.components.component_drafts import ComponentDraftService
from f8pystudio.assets.components.component_models import (
    F8ComponentEntry,
    F8ComponentRecord,
    F8ComponentSourceKind,
    F8ComponentVisibility,
    component_now_iso,
)
from f8pystudio.assets.projects.project_storage import ProjectStorageService
from f8pystudio.assets.ui.project_asset_dialogs import AssetOverwriteChoice, AssetOverwriteMetaDialog
from f8pystudio.ui.support.ui_notifications import _ACTIVE_TOASTS
from f8pystudio.ui.mainwin import project_asset_actions


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def _session_payload(node_id: str) -> dict[str, object]:
    return {
        "schemaVersion": "f8studio-session/1",
        "layout": {
            "nodes": {
                node_id: {
                    "id": node_id,
                    "name": node_id,
                    "pos": [10, 20],
                }
            },
            "connections": [],
        },
    }


class _FakeGraph:
    def __init__(self) -> None:
        self.loaded_payloads: list[object] = []

    def load_session_payload(self, payload: object) -> None:
        self.loaded_payloads.append(payload)

    def serialize_session(self) -> dict[str, object]:
        return _session_payload("unused")

    def serialize_publish_session(self) -> dict[str, object]:
        return _session_payload("unused")

    def prepare_insert_graph_from_file(self, path: str) -> object:
        raise AssertionError(f"Unexpected insert request: {path}")

    def begin_graph_placement(self, request: object, *, label: str = "") -> None:
        raise AssertionError(f"Unexpected graph placement: {request} {label}")


class _FakeLogDock:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []
        self.exceptions: list[tuple[str, str, str]] = []

    def append(self, channel: str, line: str) -> None:
        self.lines.append((str(channel), str(line)))

    def report_exception(self, channel: str, context: str, exc: Exception) -> None:
        self.exceptions.append((str(channel), str(context), str(exc)))


class _FakeAssetOverwriteMetaDialog:
    init_kwargs: dict[str, object] | None = None

    def __init__(
        self,
        *,
        parent: QtWidgets.QWidget | None,
        title: str,
        name: str,
        description: str,
        tags: list[str],
        overwrite_choices: list[object],
        overwrite_label: str,
        name_validator: object | None = None,
    ) -> None:
        del parent, overwrite_choices, overwrite_label, name_validator
        type(self).init_kwargs = {
            "title": str(title),
            "name": str(name),
            "description": str(description),
            "tags": list(tags),
        }

    def exec(self) -> int:
        return QtWidgets.QDialog.Accepted

    def values(self) -> tuple[str, str, list[str], str | None]:
        init_kwargs = type(self).init_kwargs
        assert init_kwargs is not None
        return (
            str(init_kwargs["name"]),
            str(init_kwargs["description"]),
            list(init_kwargs["tags"]),
            None,
        )


class _FakeComponentCatalogDialog:
    last_instance: _FakeComponentCatalogDialog | None = None

    def __init__(self, *, parent: QtWidgets.QWidget | None, node_graph: object) -> None:
        self.parent = parent
        self.node_graph = node_graph
        self.delete_on_close = False
        self.modal = True
        self.shown = False
        self.raised = False
        self.activated = False
        type(self).last_instance = self

    def setAttribute(self, attribute: QtCore.Qt.WidgetAttribute, on: bool = True) -> None:
        if attribute == QtCore.Qt.WA_DeleteOnClose:
            self.delete_on_close = bool(on)

    def setModal(self, modal: bool) -> None:
        self.modal = bool(modal)

    def show(self) -> None:
        self.shown = True

    def raise_(self) -> None:
        self.raised = True

    def activateWindow(self) -> None:
        self.activated = True


@dataclass
class _DialogPlan:
    result: int
    version_number: int | None = None
    action_key: str | None = None


class _FakeHistoryDialog:
    plans: list[_DialogPlan] = []
    seen_item_counts: list[int] = []

    def __init__(self, *args: object, items: list[object], **kwargs: object) -> None:
        del args, kwargs
        self._plan = self.plans.pop(0)
        self._version_number = self._plan.version_number
        self._action_key = self._plan.action_key
        self.seen_item_counts.append(len(items))

    def exec(self) -> int:
        return int(self._plan.result)

    def selected_version_number(self) -> int | None:
        return self._version_number

    def selected_action_key(self) -> str | None:
        return self._action_key


class _FakeProjectPickerDialog:
    delete_id: str = ""
    history_id: str = ""
    open_id: str = ""
    result: int = QtWidgets.QDialog.Accepted
    accepted: bool = False
    init_kwargs: dict[str, object] | None = None
    project_counts: list[int] = []

    def __init__(
        self,
        *,
        parent: QtWidgets.QWidget | None,
        projects: list[object],
        current_project_id: str,
        title: str = "Projects",
        accept_text: str = "Open",
        allow_history: bool = False,
        allow_delete: bool = False,
    ) -> None:
        del parent
        type(self).init_kwargs = {
            "project_count": len(projects),
            "current_project_id": str(current_project_id),
            "title": str(title),
            "accept_text": str(accept_text),
            "allow_history": bool(allow_history),
            "allow_delete": bool(allow_delete),
        }
        type(self).project_counts = [len(projects)]
        self.history_requested = _FakeSignal()
        self.delete_requested = _FakeSignal()

    def exec(self) -> int:
        type(self).accepted = False
        if type(self).history_id:
            self.history_requested.emit(type(self).history_id)
        if type(self).accepted:
            return QtWidgets.QDialog.Rejected
        if type(self).delete_id:
            self.delete_requested.emit(type(self).delete_id)
        return int(type(self).result)

    def selected_project_id(self) -> str:
        return type(self).open_id

    def replace_projects(self, *, projects: list[object], current_project_id: str) -> None:
        del current_project_id
        type(self).project_counts.append(len(projects))

    def accept(self) -> None:
        type(self).accepted = True


class _FakeSignal:
    def __init__(self) -> None:
        self._callbacks: list[Callable[[str], None]] = []

    def connect(self, callback: Callable[[str], None]) -> None:
        self._callbacks.append(callback)

    def emit(self, project_id: str) -> None:
        for callback in list(self._callbacks):
            callback(project_id)


def test_show_project_history_dialog_deletes_selected_version_and_refreshes(monkeypatch, tmp_path: Path) -> None:
    _ensure_app()
    settings = QtCore.QSettings(str(tmp_path / "project-history-actions.ini"), QtCore.QSettings.IniFormat)
    service = ProjectStorageService(db_path=tmp_path / "assets.db", settings=settings)
    saved = service.save_project(
        content=_session_payload("first"),
        name="History Action Demo",
        description="",
        tags=["history"],
        set_current=True,
    )
    _ = service.save_project(
        content=_session_payload("second"),
        project_id=saved.projectId,
        name=saved.name,
        description=saved.description,
        tags=list(saved.tags),
        set_current=True,
    )

    _FakeHistoryDialog.plans = [
        _DialogPlan(result=QtWidgets.QDialog.Accepted, version_number=1, action_key="delete"),
        _DialogPlan(result=QtWidgets.QDialog.Rejected),
    ]
    _FakeHistoryDialog.seen_item_counts = []

    info_messages: list[tuple[str, str]] = []
    warning_messages: list[tuple[str, str]] = []
    graph = _FakeGraph()
    log_dock = _FakeLogDock()
    parent = QtWidgets.QWidget()

    monkeypatch.setattr(project_asset_actions, "ProjectStorageService", lambda: service)
    monkeypatch.setattr(project_asset_actions, "AssetVersionBrowserDialog", _FakeHistoryDialog)
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        lambda *args, **kwargs: QtWidgets.QMessageBox.Yes,
    )

    restored = project_asset_actions.show_project_history_dialog(
        parent=parent,
        studio_graph=graph,
        log_dock=log_dock,
        show_warning=lambda _parent, title, message: warning_messages.append((str(title), str(message))),
        show_info_message=lambda _parent, title, message: info_messages.append((str(title), str(message))),
    )

    assert restored is False
    assert _FakeHistoryDialog.seen_item_counts == [2, 1]
    assert service.project_version(saved.projectId, 1) is None
    assert [version.versionNumber for version in service.list_project_versions(saved.projectId)] == [2]
    assert graph.loaded_payloads == []
    assert warning_messages == []
    assert ("Project version deleted", "Deleted project version v1.") in info_messages
    assert any("deleted version v1" in line for _channel, line in log_dock.lines)


def test_open_project_dialog_can_delete_project_and_continue_opening(monkeypatch, tmp_path: Path) -> None:
    _ensure_app()
    settings = QtCore.QSettings(str(tmp_path / "project-delete-dialog.ini"), QtCore.QSettings.IniFormat)
    service = ProjectStorageService(db_path=tmp_path / "assets.db", settings=settings)
    keep = service.save_project(
        content=_session_payload("keep-project"),
        name="Keep Project",
        description="",
        tags=[],
        set_current=True,
    )
    delete_me = service.save_project(
        content=_session_payload("delete-project"),
        name="Delete Project",
        description="",
        tags=[],
        set_current=True,
    )
    _FakeProjectPickerDialog.delete_id = delete_me.projectId
    _FakeProjectPickerDialog.history_id = ""
    _FakeProjectPickerDialog.open_id = keep.projectId
    _FakeProjectPickerDialog.result = QtWidgets.QDialog.Accepted
    _FakeProjectPickerDialog.accepted = False
    _FakeProjectPickerDialog.init_kwargs = None
    _FakeProjectPickerDialog.project_counts = []

    info_messages: list[tuple[str, str]] = []
    warning_messages: list[tuple[str, str]] = []
    graph = _FakeGraph()
    log_dock = _FakeLogDock()
    parent = QtWidgets.QWidget()

    monkeypatch.setattr(project_asset_actions, "ProjectStorageService", lambda: service)
    monkeypatch.setattr(project_asset_actions, "ProjectPickerDialog", _FakeProjectPickerDialog)
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        lambda *args, **kwargs: QtWidgets.QMessageBox.Yes,
    )

    session_dir, loaded = project_asset_actions.open_project_dialog(
        parent=parent,
        studio_graph=graph,
        log_dock=log_dock,
        start_dir="/tmp/projects",
        show_warning=lambda _parent, title, message: warning_messages.append((str(title), str(message))),
        show_info_message=lambda _parent, title, message: info_messages.append((str(title), str(message))),
    )

    assert session_dir == "/tmp/projects"
    assert loaded is True
    assert service.project(delete_me.projectId) is None
    assert service.project(keep.projectId) is not None
    assert service.current_project_id() == keep.projectId
    assert _FakeProjectPickerDialog.init_kwargs == {
        "project_count": 2,
        "current_project_id": delete_me.projectId,
        "title": "Projects",
        "accept_text": "Open",
        "allow_history": True,
        "allow_delete": True,
    }
    assert _FakeProjectPickerDialog.project_counts == [2, 1]
    assert graph.loaded_payloads == [keep.content]
    assert warning_messages == []
    assert info_messages == [("Project deleted", "Deleted project:\nDelete Project")]
    assert any("deleted: Delete Project" in line for _channel, line in log_dock.lines)
    assert any("loaded: Keep Project" in line for _channel, line in log_dock.lines)


def test_open_project_dialog_can_restore_selected_project_history(monkeypatch, tmp_path: Path) -> None:
    _ensure_app()
    settings = QtCore.QSettings(str(tmp_path / "project-picker-history.ini"), QtCore.QSettings.IniFormat)
    service = ProjectStorageService(db_path=tmp_path / "assets.db", settings=settings)
    project = service.save_project(
        content=_session_payload("history-first"),
        name="History Project",
        description="",
        tags=[],
        set_current=True,
    )
    _ = service.save_project(
        content=_session_payload("history-second"),
        project_id=project.projectId,
        name=project.name,
        description=project.description,
        tags=list(project.tags),
        set_current=True,
    )
    _FakeHistoryDialog.plans = [
        _DialogPlan(result=QtWidgets.QDialog.Accepted, version_number=1, action_key="restore"),
    ]
    _FakeHistoryDialog.seen_item_counts = []
    _FakeProjectPickerDialog.delete_id = ""
    _FakeProjectPickerDialog.history_id = project.projectId
    _FakeProjectPickerDialog.open_id = ""
    _FakeProjectPickerDialog.result = QtWidgets.QDialog.Rejected
    _FakeProjectPickerDialog.accepted = False
    _FakeProjectPickerDialog.init_kwargs = None
    _FakeProjectPickerDialog.project_counts = []

    info_messages: list[tuple[str, str]] = []
    warning_messages: list[tuple[str, str]] = []
    graph = _FakeGraph()
    log_dock = _FakeLogDock()
    parent = QtWidgets.QWidget()

    monkeypatch.setattr(project_asset_actions, "ProjectStorageService", lambda: service)
    monkeypatch.setattr(project_asset_actions, "ProjectPickerDialog", _FakeProjectPickerDialog)
    monkeypatch.setattr(project_asset_actions, "AssetVersionBrowserDialog", _FakeHistoryDialog)

    session_dir, loaded = project_asset_actions.open_project_dialog(
        parent=parent,
        studio_graph=graph,
        log_dock=log_dock,
        start_dir="/tmp/projects",
        show_warning=lambda _parent, title, message: warning_messages.append((str(title), str(message))),
        show_info_message=lambda _parent, title, message: info_messages.append((str(title), str(message))),
    )

    assert session_dir == "/tmp/projects"
    assert loaded is True
    assert _FakeHistoryDialog.seen_item_counts == [2]
    assert _FakeProjectPickerDialog.accepted is True
    assert _FakeProjectPickerDialog.project_counts == [1, 1]
    assert graph.loaded_payloads == [service.project(project.projectId).content]
    assert "history-first" in graph.loaded_payloads[0]["layout"]["nodes"]
    assert warning_messages == []
    assert ("Project restored", "Restored project version v1 as the latest version.") in info_messages
    assert any("loaded restored history: History Project" in line for _channel, line in log_dock.lines)


def test_save_component_as_dialog_seeds_metadata_from_current_project(monkeypatch, tmp_path: Path) -> None:
    _ensure_app()
    settings = QtCore.QSettings(str(tmp_path / "component-seed.ini"), QtCore.QSettings.IniFormat)
    service = ProjectStorageService(db_path=tmp_path / "assets.db", settings=settings)
    project = service.save_project(
        content=_session_payload("seeded"),
        name="Seed Project",
        description="Seed description",
        tags=["alpha", "beta"],
        set_current=True,
    )
    graph = _FakeGraph()
    log_dock = _FakeLogDock()
    parent = QtWidgets.QWidget()
    saved_records: list[object] = []
    info_messages: list[tuple[str, str]] = []
    _FakeAssetOverwriteMetaDialog.init_kwargs = None

    monkeypatch.setattr(project_asset_actions, "ProjectStorageService", lambda: service)
    monkeypatch.setattr(project_asset_actions, "AssetOverwriteMetaDialog", _FakeAssetOverwriteMetaDialog)
    monkeypatch.setattr(
        "f8pystudio.assets.components.component_repository.upsert_component",
        lambda record: saved_records.append(record),
    )
    monkeypatch.setattr(
        "f8pystudio.ui.support.ui_notifications.show_info",
        lambda _parent, title, message: info_messages.append((str(title), str(message))),
    )

    saved = project_asset_actions.save_component_as_dialog(
        parent=parent,
        studio_graph=graph,
        log_dock=log_dock,
        show_warning=lambda *_args: None,
    )

    assert project.projectId == service.current_project_id()
    assert saved is True
    assert _FakeAssetOverwriteMetaDialog.init_kwargs == {
        "title": "Export to Component",
        "name": "Seed Project",
        "description": "Seed description",
        "tags": ["alpha", "beta"],
    }
    assert len(saved_records) == 1
    saved_record = saved_records[0]
    assert saved_record.name == "Seed Project"
    assert saved_record.description == "Seed description"
    assert saved_record.tags == ["alpha", "beta"]
    assert saved_record.content == _session_payload("unused")
    assert info_messages == [("Component Saved", "Saved component:\nSeed Project")]


def test_save_component_as_dialog_overwrites_only_local_drafts(monkeypatch, tmp_path: Path) -> None:
    _ensure_app()
    settings = QtCore.QSettings(str(tmp_path / "component-overwrite-drafts.ini"), QtCore.QSettings.IniFormat)
    service = ProjectStorageService(db_path=tmp_path / "assets.db", settings=settings)
    _ = service.save_project(
        content=_session_payload("seeded"),
        name="Video AI Tracking",
        description="Seed description",
        tags=["tracking"],
        set_current=True,
    )
    timestamp = component_now_iso()
    draft_record = F8ComponentRecord(
        componentId="draft-video-ai-tracking",
        name="Video AI Tracking",
        description="Draft description",
        tags=["draft"],
        content=_session_payload("draft"),
        createdAt=timestamp,
        updatedAt=timestamp,
    )
    _ = ComponentDraftService().create_draft_from_record(
        draft_record,
        origin_kind=None,
        publish_target_asset_id=None,
        publish_base_remote_version_number=None,
        draft_id="draft-video-ai-tracking",
    )
    ComponentCatalogService().replace_remote_entries(
        [
            F8ComponentEntry(
                record=F8ComponentRecord(
                    componentId="remote-video-ai-tracking",
                    name="Video AI Tracking",
                    description="Remote description",
                    tags=["remote"],
                    content=_session_payload("remote"),
                    createdAt=timestamp,
                    updatedAt=timestamp,
                ),
                source=F8ComponentSourceKind.remote_private,
                visibility=F8ComponentVisibility.private,
                ownerUserId="user-1",
                ownerDisplayName="Author One",
                installed=True,
                hasCachedContent=True,
            )
        ]
    )

    captured_choice_ids: list[str] = []
    captured_choice_labels: list[str] = []
    validation_messages: dict[str, str | None] = {}
    saved_records: list[F8ComponentRecord] = []
    info_messages: list[tuple[str, str]] = []
    graph = _FakeGraph()
    log_dock = _FakeLogDock()
    parent = QtWidgets.QWidget()

    class _DraftOverwriteDialog:
        def __init__(
            self,
            *,
            parent: QtWidgets.QWidget | None,
            title: str,
            name: str,
            description: str,
            tags: list[str],
            overwrite_choices: list[AssetOverwriteChoice],
            overwrite_label: str,
            name_validator: object | None = None,
        ) -> None:
            del parent, title, description, tags
            assert overwrite_label == "Overwrite Local Draft"
            captured_choice_ids.extend([str(choice.asset_id) for choice in overwrite_choices])
            captured_choice_labels.extend([str(choice.display_label) for choice in overwrite_choices])
            assert callable(name_validator)
            validator = cast(Callable[[str, str | None], str | None], name_validator)
            validation_messages["selected_draft"] = validator(name, "draft-video-ai-tracking")
            validation_messages["create_new"] = validator(name, None)

        def exec(self) -> int:
            return QtWidgets.QDialog.Accepted

        def values(self) -> tuple[str, str, list[str], str | None]:
            return ("Video AI Tracking", "Updated description", ["tracking"], "draft-video-ai-tracking")

    monkeypatch.setattr(project_asset_actions, "ProjectStorageService", lambda: service)
    monkeypatch.setattr(project_asset_actions, "AssetOverwriteMetaDialog", _DraftOverwriteDialog)
    monkeypatch.setattr(
        "f8pystudio.assets.components.component_repository.upsert_component",
        lambda record: saved_records.append(record),
    )
    monkeypatch.setattr(
        "f8pystudio.ui.support.ui_notifications.show_info",
        lambda _parent, title, message: info_messages.append((str(title), str(message))),
    )

    saved = project_asset_actions.save_component_as_dialog(
        parent=parent,
        studio_graph=graph,
        log_dock=log_dock,
        show_warning=lambda *_args: None,
    )

    assert saved is True
    assert captured_choice_ids == ["draft-video-ai-tracking"]
    assert captured_choice_labels == ["Video AI Tracking (Local Draft)"]
    assert validation_messages["selected_draft"] is None
    assert "Select that local draft" in str(validation_messages["create_new"])
    assert len(saved_records) == 1
    saved_record = saved_records[0]
    assert saved_record.componentId == "draft-video-ai-tracking"
    assert saved_record.name == "Video AI Tracking"
    assert saved_record.description == "Updated description"
    assert info_messages == [("Component Updated", "Updated component:\nVideo AI Tracking")]


def test_asset_overwrite_dialog_validation_error_is_inline_not_toast() -> None:
    _ensure_app()
    for toast in list(_ACTIVE_TOASTS):
        toast.close()
    QtWidgets.QApplication.processEvents()

    dialog = AssetOverwriteMetaDialog(
        parent=None,
        title="Export to Component",
        name="Video AI Tracking",
        description="",
        tags=[],
        overwrite_choices=[],
        overwrite_label="Overwrite Local Draft",
        name_validator=lambda _candidate, _selected_id: "Component draft named 'Video AI Tracking' already exists.",
    )

    dialog._on_accept_clicked()
    validation_label = dialog.findChild(QtWidgets.QLabel, "asset-overwrite-validation-error")

    assert validation_label is not None
    assert validation_label.text() == "Component draft named 'Video AI Tracking' already exists."
    assert validation_label.isHidden() is False
    assert _ACTIVE_TOASTS == []

    dialog.close()
    QtWidgets.QApplication.processEvents()


def test_asset_overwrite_dialog_uses_display_label_without_changing_metadata_name() -> None:
    _ensure_app()
    choice = AssetOverwriteChoice(
        asset_id="draft-video",
        label="Video AI Tracking",
        description="Draft description",
        tags=["tracking"],
        display_label="Video AI Tracking (Linked Local Draft)",
        tooltip="Linked Local Draft\nDraft ID: draft-video",
    )
    dialog = AssetOverwriteMetaDialog(
        parent=None,
        title="Export to Component",
        name="New Component",
        description="New description",
        tags=[],
        overwrite_choices=[choice],
        overwrite_label="Overwrite Local Draft",
    )

    assert dialog._overwrite_combo.itemText(1) == "Video AI Tracking (Linked Local Draft)"

    dialog._overwrite_combo.setCurrentIndex(1)
    name, description, tags, selected_asset_id = dialog.values()

    assert name == "Video AI Tracking"
    assert description == "Draft description"
    assert tags == ["tracking"]
    assert selected_asset_id == "draft-video"

    dialog.close()
    QtWidgets.QApplication.processEvents()


def test_open_component_catalog_dialog_is_modeless(monkeypatch) -> None:
    _ensure_app()
    parent = QtWidgets.QWidget()
    graph = _FakeGraph()
    _FakeComponentCatalogDialog.last_instance = None

    monkeypatch.setattr(project_asset_actions, "ComponentCatalogDialog", _FakeComponentCatalogDialog)

    project_asset_actions.open_component_catalog_dialog(parent=parent, studio_graph=graph)

    dialog = _FakeComponentCatalogDialog.last_instance
    assert dialog is not None
    assert dialog.parent is parent
    assert dialog.node_graph is graph
    assert dialog.delete_on_close is True
    assert dialog.modal is False
    assert dialog.shown is True
    assert dialog.raised is True
    assert dialog.activated is True

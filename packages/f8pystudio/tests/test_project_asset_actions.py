from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from qtpy import QtCore, QtWidgets

from f8pystudio.assets.projects.project_storage import ProjectStorageService
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


class _FakeProjectAssetMetaDialog:
    init_kwargs: dict[str, object] | None = None

    def __init__(
        self,
        *,
        parent: QtWidgets.QWidget | None,
        title: str,
        name: str,
        description: str,
        tags: list[str],
        name_validator: object | None = None,
    ) -> None:
        del parent, name_validator
        type(self).init_kwargs = {
            "title": str(title),
            "name": str(name),
            "description": str(description),
            "tags": list(tags),
        }

    def exec(self) -> int:
        return QtWidgets.QDialog.Accepted

    def values(self) -> tuple[str, str, list[str]]:
        init_kwargs = type(self).init_kwargs
        assert init_kwargs is not None
        return (
            str(init_kwargs["name"]),
            str(init_kwargs["description"]),
            list(init_kwargs["tags"]),
        )


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
    _FakeProjectAssetMetaDialog.init_kwargs = None

    monkeypatch.setattr(project_asset_actions, "ProjectStorageService", lambda: service)
    monkeypatch.setattr(project_asset_actions, "ProjectAssetMetaDialog", _FakeProjectAssetMetaDialog)
    monkeypatch.setattr(project_asset_actions, "upsert_component", lambda record: saved_records.append(record))
    monkeypatch.setattr(
        project_asset_actions,
        "show_info",
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
    assert _FakeProjectAssetMetaDialog.init_kwargs == {
        "title": "Save As Component",
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
    assert info_messages == [("Component saved", "Saved component:\nSeed Project")]

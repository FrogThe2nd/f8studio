from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Protocol

from qtpy import QtWidgets

from f8pysdk.codec import dump_json

from ...assets.common import JsonObject, new_asset_id
from ...assets.ui.component_catalog_dialog import ComponentCatalogDialog
from ...assets.ui.component_insert_dialog import ComponentInsertDialog
from ...assets.ui.project_asset_dialogs import (
    AssetVersionBrowserAction,
    AssetVersionBrowserDialog,
    AssetVersionBrowserItem,
    ProjectAssetMetaDialog,
    ProjectPickerDialog,
)
from ...assets.components.component_models import F8ComponentRecord
from ...assets.components.component_repository import upsert_component
from ...assets.projects.project_models import F8ProjectRecord
from ...assets.projects.project_storage import ProjectStorageService
from ...nodegraph.graph_insert_flow import GraphInsertRequest
from ..support.ui_notifications import show_info

logger = logging.getLogger(__name__)

MessageDialogFn = Callable[[QtWidgets.QWidget, str, str], None]


class ProjectAssetLogDockLike(Protocol):
    def append(self, channel: str, line: str) -> None: ...

    def report_exception(self, channel: str, context: str, exc: Exception) -> None: ...


class ProjectAssetGraphLike(Protocol):
    def load_session_payload(self, payload: JsonObject) -> None: ...

    def serialize_session(self) -> JsonObject: ...

    def serialize_publish_session(self) -> JsonObject: ...

    def prepare_insert_graph_from_file(self, path: str) -> GraphInsertRequest: ...

    def begin_graph_placement(self, request: GraphInsertRequest, *, label: str = "") -> None: ...


def _current_project_record(service: ProjectStorageService) -> F8ProjectRecord | None:
    current_project = service.project(service.current_project_id())
    if current_project is not None:
        return current_project
    return service.load_last_project()


def _component_seed_from_current_project() -> tuple[str, str, list[str]]:
    current_project = _current_project_record(ProjectStorageService())
    if current_project is None:
        return "Untitled Component", "", []
    component_name = str(current_project.name or "").strip() or "Untitled Component"
    component_description = str(current_project.description or "")
    component_tags = [str(tag).strip() for tag in list(current_project.tags or []) if str(tag).strip()]
    return component_name, component_description, component_tags


def auto_load_project(*, studio_graph: ProjectAssetGraphLike, log_dock: ProjectAssetLogDockLike) -> None:
    try:
        project = ProjectStorageService().load_last_project()
        if project is None:
            return
        studio_graph.load_session_payload(project.content)
        logger.info("Loaded project from %s", project.projectId)
    except Exception as exc:
        log_dock.report_exception("studio", "session auto-load failed", exc)
        logger.exception("Auto-load session failed")


def auto_save_project(
    *,
    studio_graph: ProjectAssetGraphLike,
    log_dock: ProjectAssetLogDockLike,
    already_saved: bool,
) -> bool:
    if bool(already_saved):
        return True

    try:
        saved = ProjectStorageService().save_last_project(content=studio_graph.serialize_session())
        logger.info("Saved project to %s", saved.projectId)
        return True
    except Exception as exc:
        log_dock.report_exception("studio", "session auto-save failed", exc)
        logger.exception("Auto-save session failed")
        return False


def save_project(
    *,
    parent: QtWidgets.QWidget,
    studio_graph: ProjectAssetGraphLike,
    show_info: MessageDialogFn,
) -> None:
    record = ProjectStorageService().save_last_project(content=studio_graph.serialize_session())
    show_info(parent, "Project saved", f"Saved project:\n{record.name}")


def load_last_project(
    *,
    parent: QtWidgets.QWidget,
    studio_graph: ProjectAssetGraphLike,
    session_file: Path,
    show_info: MessageDialogFn,
) -> bool:
    _ = session_file
    record = ProjectStorageService().load_last_project()
    if record is None:
        show_info(parent, "No project", "No local project was found.")
        return False
    studio_graph.load_session_payload(record.content)
    show_info(parent, "Project loaded", f"Loaded project:\n{record.name}")
    return True


def open_project_dialog(
    *,
    parent: QtWidgets.QWidget,
    studio_graph: ProjectAssetGraphLike,
    log_dock: ProjectAssetLogDockLike,
    start_dir: str,
    show_warning: MessageDialogFn,
) -> tuple[str, bool]:
    service = ProjectStorageService()
    dialog = ProjectPickerDialog(
        parent=parent,
        projects=service.list_projects(),
        current_project_id=service.current_project_id(),
    )
    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return str(start_dir or ""), False

    try:
        project_id = dialog.selected_project_id()
        record = service.project(project_id)
        if record is None:
            raise FileNotFoundError(f"Project not found: {project_id}")
        studio_graph.load_session_payload(record.content)
        service.set_current_project_id(record.projectId)
        log_dock.append("studio", f"[project] loaded: {record.name} ({record.projectId})\n")
        return str(start_dir or ""), True
    except Exception as exc:
        log_dock.append("studio", f"[project] load failed: {exc}\n")
        log_dock.report_exception("studio", "project load failed", exc)
        show_warning(parent, "Load failed", f"Failed to load project.\n\n{exc}")
        return str(start_dir or ""), False


def import_project_json_as_dialog(
    *,
    parent: QtWidgets.QWidget,
    studio_graph: ProjectAssetGraphLike,
    log_dock: ProjectAssetLogDockLike,
    start_dir: str,
    show_warning: MessageDialogFn,
) -> tuple[str, bool]:
    path, _ = QtWidgets.QFileDialog.getOpenFileName(
        parent,
        "Import Project JSON",
        str(start_dir or ""),
        "F8 Studio Session (*.json);;JSON (*.json);;All Files (*)",
    )
    selected_path = str(path or "").strip()
    if not selected_path:
        return str(start_dir or ""), False

    dialog = ProjectAssetMetaDialog(
        parent=parent,
        title="Import Project JSON",
        name=Path(selected_path).stem or "Imported Project",
        description="",
        tags=[],
    )
    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return str(start_dir or ""), False

    try:
        name, description, tags = dialog.values()
        record = ProjectStorageService().import_project_from_json(
            path=selected_path,
            name=name,
            description=description,
            tags=tags,
            set_current=True,
        )
        studio_graph.load_session_payload(record.content)
        resolved_dir = str(Path(selected_path).resolve().parent)
        log_dock.append("studio", f"[project][import] imported: {record.name} ({record.projectId})\n")
        return resolved_dir, True
    except Exception as exc:
        log_dock.append("studio", f"[project][import] failed: {exc}\n")
        log_dock.report_exception("studio", f"project import failed ({selected_path})", exc)
        show_warning(parent, "Import failed", f"Failed to import project JSON.\n\n{exc}")
        return str(start_dir or ""), False


def save_project_as_dialog(
    *,
    parent: QtWidgets.QWidget,
    studio_graph: ProjectAssetGraphLike,
    log_dock: ProjectAssetLogDockLike,
    start_dir: str,
    show_warning: MessageDialogFn,
) -> tuple[str, bool]:
    dialog = ProjectAssetMetaDialog(
        parent=parent,
        title="Save Project As",
        name="Untitled Project",
        description="",
        tags=[],
    )
    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return str(start_dir or ""), False

    try:
        name, description, tags = dialog.values()
        record = ProjectStorageService().save_project(
            content=studio_graph.serialize_session(),
            name=name,
            description=description,
            tags=tags,
            set_current=True,
        )
        log_dock.append("studio", f"[project] saved: {record.name} ({record.projectId})\n")
        return str(start_dir or ""), True
    except Exception as exc:
        log_dock.append("studio", f"[project] save failed: {exc}\n")
        log_dock.report_exception("studio", "project save failed", exc)
        show_warning(parent, "Save failed", f"Failed to save project.\n\n{exc}")
        return str(start_dir or ""), False


def export_project_json_as_dialog(
    *,
    parent: QtWidgets.QWidget,
    studio_graph: ProjectAssetGraphLike,
    log_dock: ProjectAssetLogDockLike,
    start_dir: str,
    show_warning: MessageDialogFn,
) -> tuple[str, str | None]:
    path, _ = QtWidgets.QFileDialog.getSaveFileName(
        parent,
        "Export Project JSON",
        str(start_dir or ""),
        "F8 Studio Session (*.json);;JSON (*.json);;All Files (*)",
    )
    selected_path = str(path or "").strip()
    if not selected_path:
        return str(start_dir or ""), None

    save_path = selected_path if selected_path.lower().endswith(".json") else selected_path + ".json"
    try:
        payload = studio_graph.serialize_session()
        Path(save_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        resolved_dir = str(Path(save_path).resolve().parent)
        log_dock.append("studio", f"[project][export] exported: {save_path}\n")
        return resolved_dir, str(save_path)
    except Exception as exc:
        log_dock.append("studio", f"[project][export] export failed: {exc}\n")
        log_dock.report_exception("studio", f"project export failed ({save_path})", exc)
        show_warning(parent, "Export JSON failed", f"Failed to export project JSON:\n{save_path}\n\n{exc}")
        return str(start_dir or ""), None


def export_publish_json_dialog(
    *,
    parent: QtWidgets.QWidget,
    studio_graph: ProjectAssetGraphLike,
    log_dock: ProjectAssetLogDockLike,
    start_dir: str,
    show_warning: MessageDialogFn,
) -> tuple[str, str | None]:
    path, _ = QtWidgets.QFileDialog.getSaveFileName(
        parent,
        "Export Publish JSON",
        str(start_dir or ""),
        "F8 Studio Session (*.json);;JSON (*.json);;All Files (*)",
    )
    selected_path = str(path or "").strip()
    if not selected_path:
        return str(start_dir or ""), None

    save_path = selected_path if selected_path.lower().endswith(".json") else selected_path + ".json"
    try:
        payload = studio_graph.serialize_publish_session()
        Path(save_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        resolved_dir = str(Path(save_path).resolve().parent)
        log_dock.append("studio", f"[session][publish] exported: {save_path}\n")
        return resolved_dir, str(save_path)
    except Exception as exc:
        log_dock.append("studio", f"[session][publish] export failed: {exc}\n")
        log_dock.report_exception("studio", f"publish session failed ({save_path})", exc)
        show_warning(parent, "Publish JSON failed", f"Failed to export publish JSON:\n{save_path}\n\n{exc}")
        return str(start_dir or ""), None


def save_component_as_dialog(
    *,
    parent: QtWidgets.QWidget,
    studio_graph: ProjectAssetGraphLike,
    log_dock: ProjectAssetLogDockLike,
    show_warning: MessageDialogFn,
) -> bool:
    seed_name, seed_description, seed_tags = _component_seed_from_current_project()
    dialog = ProjectAssetMetaDialog(
        parent=parent,
        title="Save As Component",
        name=seed_name,
        description=seed_description,
        tags=seed_tags,
    )
    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return False

    try:
        name, description, tags = dialog.values()
        record = F8ComponentRecord(
            componentId=new_asset_id(),
            name=name,
            description=description,
            tags=tags,
            content=studio_graph.serialize_publish_session(),
        )
        upsert_component(record)
        log_dock.append("studio", f"[component] saved: {record.name} ({record.componentId})\n")
        show_info(parent, "Component saved", f"Saved component:\n{record.name}")
        return True
    except Exception as exc:
        log_dock.append("studio", f"[component] save failed: {exc}\n")
        log_dock.report_exception("studio", "component save failed", exc)
        show_warning(parent, "Save component failed", f"Failed to save component.\n\n{exc}")
        return False


def open_component_catalog_dialog(*, parent: QtWidgets.QWidget, studio_graph: ProjectAssetGraphLike) -> None:
    dialog = ComponentCatalogDialog(parent=parent, node_graph=studio_graph)
    dialog.exec()


def open_component_insert_dialog(
    *,
    parent: QtWidgets.QWidget,
    studio_graph: ProjectAssetGraphLike,
    insert_scene_pos: tuple[float, float] | None = None,
) -> None:
    dialog = ComponentInsertDialog(parent=parent, node_graph=studio_graph, insert_scene_pos=insert_scene_pos)
    dialog.exec()


def show_project_history_dialog(
    *,
    parent: QtWidgets.QWidget,
    studio_graph: ProjectAssetGraphLike,
    log_dock: ProjectAssetLogDockLike,
    show_warning: MessageDialogFn,
    show_info_message: MessageDialogFn,
) -> bool:
    service = ProjectStorageService()
    current_project = _current_project_record(service)
    if current_project is None:
        show_info_message(parent, "No project", "No local project was found.")
        return False
    project_id = current_project.projectId
    while True:
        current_project = service.project(project_id)
        if current_project is None:
            show_info_message(parent, "No project", "No local project was found.")
            return False
        versions = service.list_project_versions(current_project.projectId)
        if not versions:
            show_info_message(parent, "Project History", "No project history found.")
            return False

        dialog = AssetVersionBrowserDialog(
            parent=parent,
            title=f"Project History - {current_project.name}",
            items=[
                AssetVersionBrowserItem(version_number=int(version.versionNumber), created_at=str(version.createdAt))
                for version in versions
            ],
            load_payload=lambda version_number: dump_json(
                _require_project_version_payload(
                    service=service,
                    project_id=current_project.projectId,
                    version_number=version_number,
                ),
                mode="json",
            ),
            actions=[
                AssetVersionBrowserAction(action_key="restore", label="Restore As Latest"),
                AssetVersionBrowserAction(action_key="delete", label="Delete Version"),
            ],
        )
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return False

        selected_version_number = dialog.selected_version_number()
        if selected_version_number is None:
            return False
        selected_action_key = str(dialog.selected_action_key() or "restore")
        if selected_action_key == "delete":
            answer = QtWidgets.QMessageBox.question(
                parent,
                "Delete project version",
                f"Delete project history version v{selected_version_number}?\nThis cannot be undone.",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if answer != QtWidgets.QMessageBox.Yes:
                continue
            try:
                service.delete_project_version(
                    project_id=current_project.projectId,
                    version_number=int(selected_version_number),
                )
                log_dock.append(
                    "studio",
                    f"[project] deleted version v{selected_version_number} ({current_project.projectId})\n",
                )
                show_info_message(
                    parent,
                    "Project version deleted",
                    f"Deleted project version v{selected_version_number}.",
                )
            except Exception as exc:
                log_dock.append("studio", f"[project] delete failed: {exc}\n")
                log_dock.report_exception("studio", "project delete failed", exc)
                show_warning(parent, "Delete failed", f"Failed to delete project version.\n\n{exc}")
            continue
        try:
            restored = service.restore_project_version(
                project_id=current_project.projectId,
                version_number=int(selected_version_number),
            )
            studio_graph.load_session_payload(restored.content)
            log_dock.append(
                "studio",
                f"[project] restored version v{selected_version_number} as latest ({restored.projectId})\n",
            )
            show_info_message(
                parent,
                "Project restored",
                f"Restored project version v{selected_version_number} as the latest version.",
            )
            return True
        except Exception as exc:
            log_dock.append("studio", f"[project] restore failed: {exc}\n")
            log_dock.report_exception("studio", "project restore failed", exc)
            show_warning(parent, "Restore failed", f"Failed to restore project version.\n\n{exc}")
            return False


def insert_graph_json_dialog(
    *,
    parent: QtWidgets.QWidget,
    studio_graph: ProjectAssetGraphLike,
    log_dock: ProjectAssetLogDockLike,
    start_dir: str,
    show_warning: MessageDialogFn,
) -> str:
    path, _ = QtWidgets.QFileDialog.getOpenFileName(
        parent,
        "Insert Graph",
        str(start_dir or ""),
        "F8 Studio Session (*.json);;JSON (*.json);;All Files (*)",
    )
    selected_path = str(path or "").strip()
    if not selected_path:
        return str(start_dir or "")

    try:
        request = studio_graph.prepare_insert_graph_from_file(selected_path)
    except Exception as exc:
        log_dock.append("studio", f"[insert] prepare failed: {exc}\n")
        log_dock.report_exception("studio", f"insert graph prepare failed ({selected_path})", exc)
        show_warning(parent, "Insert failed", f"Failed to prepare insert:\n{selected_path}\n\n{exc}")
        return str(start_dir or "")

    resolved_dir = str(Path(selected_path).resolve().parent)
    if request.node_count <= 0:
        show_warning(parent, "Insert blocked", f"Graph has no nodes:\n{selected_path}")
        return resolved_dir

    graph_name = Path(selected_path).name
    placement_label = f"Insert: {graph_name}\n{request.node_count} nodes"
    studio_graph.begin_graph_placement(request, label=placement_label)
    log_dock.append("studio", f"[insert] click canvas to place: {graph_name} ({request.node_count} nodes)\n")
    return resolved_dir


def _require_project_version_payload(
    *,
    service: ProjectStorageService,
    project_id: str,
    version_number: int,
) -> JsonObject:
    record = service.project_version(project_id, int(version_number))
    if record is None:
        raise FileNotFoundError(f"Project version not found: {project_id} v{version_number}")
    return dump_json(record, mode="json")

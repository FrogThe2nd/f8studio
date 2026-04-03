from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from qtpy import QtWidgets

from f8pysdk.msgspec_codec import dump_json

from ..graph_assets.common import new_asset_id
from ..graph_assets.component_models import F8ComponentRecord
from ..graph_assets.component_repository import upsert_component
from ..graph_assets.project_storage import ProjectStorageService
from ..ui_notifications import show_info
from .component_manager_dialog import ComponentManagerDialog
from .graph_asset_dialogs import GraphAssetMetaDialog, JsonVersionBrowserDialog, JsonVersionBrowserItem, ProjectPickerDialog
from .insert_component_dialog import InsertComponentDialog

logger = logging.getLogger(__name__)


def auto_load_session(*, studio_graph: Any, log_dock: Any) -> None:
    try:
        project = ProjectStorageService().load_last_session()
        if project is None:
            return
        studio_graph.load_session_payload(project.content)
        logger.info("Loaded project from %s", project.projectId)
    except Exception as exc:
        log_dock.report_exception("studio", "session auto-load failed", exc)
        logger.exception("Auto-load session failed")


def auto_save_session(*, studio_graph: Any, log_dock: Any, already_saved: bool) -> bool:
    if bool(already_saved):
        return True

    try:
        saved = ProjectStorageService().save_last_session(content=studio_graph.serialize_session())
        logger.info("Saved project to %s", saved.projectId)
        return True
    except Exception:
        try:
            log_dock.append("studio", "[session] auto-save failed\n")
        except (AttributeError, RuntimeError, TypeError):
            pass
        logger.exception("Auto-save session failed")
        return False


def save_session(*, parent: QtWidgets.QWidget, studio_graph: Any, show_info: Any) -> None:
    record = ProjectStorageService().save_last_session(content=studio_graph.serialize_session())
    show_info(parent, "Project saved", f"Saved project:\n{record.name}")


def load_last_session(*, parent: QtWidgets.QWidget, studio_graph: Any, session_file: Path, show_info: Any) -> bool:
    _ = session_file
    record = ProjectStorageService().load_last_session()
    if record is None:
        show_info(parent, "No project", "No local project was found.")
        return False
    studio_graph.load_session_payload(record.content)
    show_info(parent, "Project loaded", f"Loaded project:\n{record.name}")
    return True


def load_session_from_dialog(
    *,
    parent: QtWidgets.QWidget,
    studio_graph: Any,
    log_dock: Any,
    start_dir: str,
    show_warning: Any,
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
    studio_graph: Any,
    log_dock: Any,
    start_dir: str,
    show_warning: Any,
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

    dialog = GraphAssetMetaDialog(
        parent=parent,
        title="Import Project JSON",
        name=Path(selected_path).stem or "Imported Project",
        description="",
        tags=[],
        include_usage_notes=False,
    )
    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return str(start_dir or ""), False

    try:
        name, description, tags, _usage_notes = dialog.values()
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


def save_session_as_dialog(
    *,
    parent: QtWidgets.QWidget,
    studio_graph: Any,
    log_dock: Any,
    start_dir: str,
    show_warning: Any,
) -> tuple[str, bool]:
    dialog = GraphAssetMetaDialog(
        parent=parent,
        title="Save Project As",
        name="Untitled Project",
        description="",
        tags=[],
        include_usage_notes=False,
    )
    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return str(start_dir or ""), False

    try:
        name, description, tags, _usage_notes = dialog.values()
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
    studio_graph: Any,
    log_dock: Any,
    start_dir: str,
    show_warning: Any,
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


def publish_session_as_dialog(
    *,
    parent: QtWidgets.QWidget,
    studio_graph: Any,
    log_dock: Any,
    start_dir: str,
    show_warning: Any,
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
    studio_graph: Any,
    log_dock: Any,
    show_warning: Any,
) -> bool:
    dialog = GraphAssetMetaDialog(
        parent=parent,
        title="Save As Component",
        name="Untitled Component",
        description="",
        tags=[],
        usage_notes="",
        include_usage_notes=True,
    )
    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return False

    try:
        name, description, tags, usage_notes = dialog.values()
        record = F8ComponentRecord(
            componentId=new_asset_id(),
            name=name,
            description=description,
            usageNotes=usage_notes,
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


def manage_components_dialog(*, parent: QtWidgets.QWidget, studio_graph: Any) -> None:
    dialog = ComponentManagerDialog(parent=parent, node_graph=studio_graph)
    dialog.exec()


def insert_component_dialog(
    *,
    parent: QtWidgets.QWidget,
    studio_graph: Any,
    insert_scene_pos: tuple[float, float] | None = None,
) -> None:
    dialog = InsertComponentDialog(parent=parent, node_graph=studio_graph, insert_scene_pos=insert_scene_pos)
    dialog.exec()


def show_project_history_dialog(
    *,
    parent: QtWidgets.QWidget,
    studio_graph: Any,
    log_dock: Any,
    show_warning: Any,
    show_info_message: Any,
) -> bool:
    service = ProjectStorageService()
    current_project = service.project(service.current_project_id())
    if current_project is None:
        current_project = service.load_last_session()
    if current_project is None:
        show_info_message(parent, "No project", "No local project was found.")
        return False

    versions = service.list_project_versions(current_project.projectId)
    if not versions:
        show_info_message(parent, "Project History", "No project history found.")
        return False

    dialog = JsonVersionBrowserDialog(
        parent=parent,
        title=f"Project History - {current_project.name}",
        items=[
            JsonVersionBrowserItem(version_number=int(version.versionNumber), created_at=str(version.createdAt))
            for version in versions
        ],
        load_payload=lambda version_number: dump_json(
            _require_project_version_payload(service=service, project_id=current_project.projectId, version_number=version_number),
            mode="json",
        ),
        primary_action_label="Restore As Latest",
    )
    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return False

    selected_version_number = dialog.selected_version_number()
    if selected_version_number is None:
        return False
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


def insert_graph_from_dialog(
    *,
    parent: QtWidgets.QWidget,
    studio_graph: Any,
    log_dock: Any,
    start_dir: str,
    show_warning: Any,
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
) -> dict[str, Any]:
    record = service.project_version(project_id, int(version_number))
    if record is None:
        raise FileNotFoundError(f"Project version not found: {project_id} v{version_number}")
    return dump_json(record, mode="json")

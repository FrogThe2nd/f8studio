from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from qtpy import QtWidgets

logger = logging.getLogger(__name__)


def auto_load_session(*, studio_graph: Any, log_dock: Any) -> None:
    try:
        loaded = studio_graph.load_last_session()
        if loaded:
            logger.info("Loaded session from %s", loaded)
    except Exception as exc:
        log_dock.report_exception("studio", "session auto-load failed", exc)
        logger.exception("Auto-load session failed")


def auto_save_session(*, studio_graph: Any, log_dock: Any, already_saved: bool) -> bool:
    if bool(already_saved):
        return True

    try:
        saved = studio_graph.save_last_session()
        logger.info("Saved session to %s", saved)
        return True
    except Exception:
        try:
            log_dock.append("studio", "[session] auto-save failed\n")
        except (AttributeError, RuntimeError, TypeError):
            pass
        logger.exception("Auto-save session failed")
        return False


def save_session(*, parent: QtWidgets.QWidget, studio_graph: Any, show_info: Any) -> None:
    path = studio_graph.save_last_session()
    show_info(parent, "Session saved", f"Saved to:\n{path}")


def load_last_session(*, parent: QtWidgets.QWidget, studio_graph: Any, session_file: Path, show_info: Any) -> bool:
    path = studio_graph.load_last_session()
    if not path:
        show_info(parent, "No session", f"No session file found at:\n{session_file}")
        return False
    show_info(parent, "Session loaded", f"Loaded:\n{path}")
    return True


def load_session_from_dialog(
    *,
    parent: QtWidgets.QWidget,
    studio_graph: Any,
    log_dock: Any,
    start_dir: str,
    show_warning: Any,
) -> tuple[str, bool]:
    path, _ = QtWidgets.QFileDialog.getOpenFileName(
        parent,
        "Load Session",
        str(start_dir or ""),
        "F8 Studio Session (*.json);;JSON (*.json);;All Files (*)",
    )
    selected_path = str(path or "").strip()
    if not selected_path:
        return str(start_dir or ""), False

    try:
        studio_graph.load_session(selected_path)
        resolved_dir = str(Path(selected_path).resolve().parent)
        log_dock.append("studio", f"[session] loaded: {selected_path}\n")
        return resolved_dir, True
    except Exception as exc:
        log_dock.append("studio", f"[session] load failed: {exc}\n")
        log_dock.report_exception("studio", f"session load failed ({selected_path})", exc)
        show_warning(parent, "Load failed", f"Failed to load:\n{selected_path}\n\n{exc}")
        return str(start_dir or ""), False


def save_session_as_dialog(
    *,
    parent: QtWidgets.QWidget,
    studio_graph: Any,
    log_dock: Any,
    start_dir: str,
    show_warning: Any,
) -> tuple[str, bool]:
    path, _ = QtWidgets.QFileDialog.getSaveFileName(
        parent,
        "Save Session As",
        str(start_dir or ""),
        "F8 Studio Session (*.json);;JSON (*.json);;All Files (*)",
    )
    selected_path = str(path or "").strip()
    if not selected_path:
        return str(start_dir or ""), False

    save_path = selected_path if selected_path.lower().endswith(".json") else selected_path + ".json"
    try:
        studio_graph.save_session(save_path)
        resolved_dir = str(Path(save_path).resolve().parent)
        log_dock.append("studio", f"[session] saved: {save_path}\n")
        return resolved_dir, True
    except Exception as exc:
        log_dock.append("studio", f"[session] save failed: {exc}\n")
        log_dock.report_exception("studio", f"session save failed ({save_path})", exc)
        show_warning(parent, "Save failed", f"Failed to save:\n{save_path}\n\n{exc}")
        return str(start_dir or ""), False


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
        "Publish JSON",
        str(start_dir or ""),
        "F8 Studio Session (*.json);;JSON (*.json);;All Files (*)",
    )
    selected_path = str(path or "").strip()
    if not selected_path:
        return str(start_dir or ""), None

    save_path = selected_path if selected_path.lower().endswith(".json") else selected_path + ".json"
    try:
        published_path = studio_graph.save_publish_session(save_path)
        resolved_dir = str(Path(save_path).resolve().parent)
        log_dock.append("studio", f"[session][publish] exported: {published_path}\n")
        return resolved_dir, str(published_path)
    except Exception as exc:
        log_dock.append("studio", f"[session][publish] export failed: {exc}\n")
        log_dock.report_exception("studio", f"publish session failed ({save_path})", exc)
        show_warning(parent, "Publish JSON failed", f"Failed to export publish JSON:\n{save_path}\n\n{exc}")
        return str(start_dir or ""), None


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

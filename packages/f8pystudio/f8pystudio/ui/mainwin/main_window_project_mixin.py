from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from qtpy import QtCore, QtWidgets

from ...assets.projects.project_models import F8ProjectRecord
from ...assets.projects.project_storage import ProjectStorageService
from ...ui.support.ui_notifications import show_info, show_warning
from ..dialogs.global_hotkey_registry_dialog import GlobalHotkeyRegistryDialog
from ..dialogs.node_docs_dialog import SpecTemplate, show_node_docs_dialog
from .project_asset_actions import (
    auto_save_project as save_project_automatically,
    export_project_json_as_dialog,
    export_publish_json_dialog,
    import_project_json_as_dialog,
    insert_graph_json_dialog,
    load_last_project,
    open_component_catalog_dialog,
    open_project_dialog,
    save_component_as_dialog,
    save_project,
    save_project_as_dialog,
    show_project_history_dialog,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ...global_hotkeys.controller import ControlPanelGlobalHotkeyController
    from ...nodegraph.node_graph import F8StudioGraph
    from ..widgets.service_log_widget import ServiceLogDock

logger = logging.getLogger(__name__)


class _ProjectAutoLoadWorker(QtCore.QObject):
    loaded = QtCore.Signal(object)
    failed = QtCore.Signal(str, object)

    def __init__(self) -> None:
        super().__init__(None)
        self._thread: threading.Thread | None = None
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        worker_thread = threading.Thread(
            target=self._run,
            name="f8pystudio-project-auto-load",
            daemon=True,
        )
        self._thread = worker_thread
        worker_thread.start()

    def _run(self) -> None:
        try:
            project = ProjectStorageService().load_last_project()
        except Exception as exc:
            self.failed.emit("session auto-load failed", exc)
            return
        self.loaded.emit(project)


class MainWindowProjectMixin:
    if TYPE_CHECKING:
        studio_graph: F8StudioGraph
        _log_dock: ServiceLogDock
        _session_file: Path
        _session_dialog_dir: str
        _closing: bool
        _auto_save_enabled: bool
        _exit_autosaved: bool
        _auto_load_worker: _ProjectAutoLoadWorker | None
        _last_auto_deploy_fingerprint: str
        _global_hotkey_controller: ControlPanelGlobalHotkeyController

        def _mark_session_saved(self) -> None: ...
        def _mark_auto_deploy_observed(self) -> None: ...
        def _schedule_deferred_auto_deploy_fingerprint_refresh(self) -> None: ...
        def _schedule_studio_runtime_sync(self) -> None: ...
        def _graph_has_unsaved_changes(self) -> bool: ...
        def _mark_auto_deploy_synced(self, *, compiled: object | None = None) -> None: ...
        def _focus_node_by_id(self, node_id: str) -> None: ...

    @QtCore.Slot()
    def _auto_load_project(self) -> None:
        if self._auto_load_worker is not None:
            return
        worker = _ProjectAutoLoadWorker()
        worker.loaded.connect(self._on_auto_load_project_loaded)  # type: ignore[attr-defined]
        worker.failed.connect(self._on_auto_load_project_failed)  # type: ignore[attr-defined]
        self._auto_load_worker = worker
        worker.start()

    def _finalize_auto_load_project(self) -> None:
        self._mark_session_saved()
        self._mark_auto_deploy_observed()
        self._last_auto_deploy_fingerprint = ""
        self._schedule_deferred_auto_deploy_fingerprint_refresh()
        self._schedule_studio_runtime_sync()
        self._global_hotkey_controller.refresh_bindings()

    @QtCore.Slot(object)
    def _on_auto_load_project_loaded(self, project: object) -> None:
        self._auto_load_worker = None
        if self._closing:
            return
        if project is not None and not isinstance(project, F8ProjectRecord):
            logger.error("Unexpected auto-load project type: %s", type(project).__name__)
            self._finalize_auto_load_project()
            return

        loaded_project = project
        if isinstance(loaded_project, F8ProjectRecord):
            try:
                self.studio_graph.load_session_payload(loaded_project.content)
                logger.info("Loaded project from %s", loaded_project.projectId)
            except Exception as exc:
                self._log_dock.report_exception("studio", "session auto-load failed", exc)
                logger.error("Auto-load session failed", exc_info=exc)
        self._finalize_auto_load_project()

    @QtCore.Slot(str, object)
    def _on_auto_load_project_failed(self, context: str, exc: object) -> None:
        self._auto_load_worker = None
        if self._closing:
            return
        normalized_context = str(context or "").strip() or "session auto-load failed"
        if isinstance(exc, Exception):
            self._log_dock.report_exception("studio", normalized_context, exc)
            logger.error("%s", normalized_context, exc_info=exc)
        else:
            self._log_dock.append("studio", f"[project][auto-load] {normalized_context}\n")
        self._finalize_auto_load_project()

    @QtCore.Slot()
    def _auto_save_project(self) -> None:
        if not self._auto_save_enabled:
            return
        if not self._graph_has_unsaved_changes():
            self._exit_autosaved = True
            return
        self._exit_autosaved = save_project_automatically(
            studio_graph=self.studio_graph,
            log_dock=self._log_dock,
            already_saved=self._exit_autosaved,
        )
        if self._exit_autosaved:
            self._mark_session_saved()

    @QtCore.Slot()
    def _on_quicksave_project_action(self) -> None:
        save_project(parent=self, studio_graph=self.studio_graph, show_info=show_info)
        self._mark_session_saved()

    @QtCore.Slot()
    def _on_quickload_project_action(self) -> None:
        loaded = load_last_project(
            parent=self,
            studio_graph=self.studio_graph,
            session_file=self._session_file,
            show_info=show_info,
        )
        if loaded:
            self._mark_session_saved()
            self._mark_auto_deploy_synced()
            self._schedule_studio_runtime_sync()
            self._global_hotkey_controller.refresh_bindings()

    @QtCore.Slot()
    def _on_open_project_action(self) -> None:
        session_dir, loaded = open_project_dialog(
            parent=self,
            studio_graph=self.studio_graph,
            log_dock=self._log_dock,
            start_dir=str(self._session_dialog_dir or ""),
            show_warning=show_warning,
            show_info_message=show_info,
        )
        self._session_dialog_dir = str(session_dir)
        if loaded:
            self._mark_session_saved()
            self._mark_auto_deploy_synced()
            self._schedule_studio_runtime_sync()
            self._global_hotkey_controller.refresh_bindings()

    @QtCore.Slot()
    def _on_import_project_json_action(self) -> None:
        session_dir, loaded = import_project_json_as_dialog(
            parent=self,
            studio_graph=self.studio_graph,
            log_dock=self._log_dock,
            start_dir=str(self._session_dialog_dir or ""),
            show_warning=show_warning,
        )
        self._session_dialog_dir = str(session_dir)
        if loaded:
            self._mark_session_saved()
            self._mark_auto_deploy_synced()
            self._schedule_studio_runtime_sync()
            self._global_hotkey_controller.refresh_bindings()

    @QtCore.Slot()
    def _on_save_project_as_action(self) -> None:
        session_dir, saved = save_project_as_dialog(
            parent=self,
            studio_graph=self.studio_graph,
            log_dock=self._log_dock,
            start_dir=str(self._session_dialog_dir or ""),
            show_warning=show_warning,
        )
        self._session_dialog_dir = str(session_dir)
        if saved:
            self._mark_session_saved()

    @QtCore.Slot()
    def _on_export_project_json_action(self) -> None:
        session_dir, exported_path = export_project_json_as_dialog(
            parent=self,
            studio_graph=self.studio_graph,
            log_dock=self._log_dock,
            start_dir=str(self._session_dialog_dir or ""),
            show_warning=show_warning,
        )
        self._session_dialog_dir = str(session_dir)
        if exported_path:
            show_info(self, "Project JSON exported", f"Exported project JSON to:\n{exported_path}")

    @QtCore.Slot()
    def _on_project_history_action(self) -> None:
        restored = show_project_history_dialog(
            parent=self,
            studio_graph=self.studio_graph,
            log_dock=self._log_dock,
            show_warning=show_warning,
            show_info_message=show_info,
        )
        if restored:
            self._mark_session_saved()
            self._mark_auto_deploy_synced()
            self._schedule_studio_runtime_sync()
            self._global_hotkey_controller.refresh_bindings()

    @QtCore.Slot()
    def _on_save_component_action(self) -> None:
        save_component_as_dialog(
            parent=self,
            studio_graph=self.studio_graph,
            log_dock=self._log_dock,
            show_warning=show_warning,
        )

    @QtCore.Slot()
    def _on_manage_components_action(self) -> None:
        open_component_catalog_dialog(parent=self, studio_graph=self.studio_graph)

    def _open_node_docs_dialog_for_graph(self, spec: SpecTemplate, node_id: str, node_name: str) -> None:
        show_node_docs_dialog(parent=self, spec=spec, node_id=node_id, node_name=node_name)

    @QtCore.Slot()
    def _on_export_published_session_action(self) -> None:
        session_dir, published_path = export_publish_json_dialog(
            parent=self,
            studio_graph=self.studio_graph,
            log_dock=self._log_dock,
            start_dir=str(self._session_dialog_dir or ""),
            show_warning=show_warning,
        )
        self._session_dialog_dir = str(session_dir)
        if published_path:
            show_info(self, "Publish JSON exported", f"Exported publish-safe JSON to:\n{published_path}")

    @QtCore.Slot()
    def _on_import_graph_action(self) -> None:
        self._session_dialog_dir = str(
            insert_graph_json_dialog(
                parent=self,
                studio_graph=self.studio_graph,
                log_dock=self._log_dock,
                start_dir=str(self._session_dialog_dir or ""),
                show_warning=show_warning,
            )
        )

    @QtCore.Slot()
    def _on_global_hotkeys_action(self) -> None:
        dialog = GlobalHotkeyRegistryDialog(
            self,
            entries_provider=self._global_hotkey_controller.registry_entries,
        )
        dialog.node_requested.connect(self._focus_node_by_id)  # type: ignore[attr-defined]
        self._global_hotkey_controller.registry_changed.connect(dialog.refresh_entries)  # type: ignore[attr-defined]
        dialog.exec()

    @QtCore.Slot()
    def _on_variant_catalog_action(self) -> None:
        from ...assets.ui.variant_catalog_dialog import VariantCatalogDialog

        dialog = VariantCatalogDialog(
            parent=self,
            base_node_type=None,
            base_node_name=None,
            node_graph=self.studio_graph,
        )
        dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    @QtCore.Slot()
    def _on_game_modding_action(self) -> None:
        from ...ui.dialogs.game_modding_dialog import GameModdingDialog

        dialog = GameModdingDialog(
            parent=self,
            studio_graph=self.studio_graph,
            on_graph_applied=self._schedule_studio_runtime_sync,
        )
        dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

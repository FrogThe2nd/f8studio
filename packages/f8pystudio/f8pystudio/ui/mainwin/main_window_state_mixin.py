from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from qtpy import QtCore, QtGui

from f8pysdk.codec import coerce_bool

from ...bridge.deploy_fingerprint import build_compiled_deploy_fingerprint
from ...diagnostics.logging import apply_root_log_level
from ...nodegraph.runtime_compiler import CompiledRuntimeGraphs, compile_runtime_graphs_from_studio
from ...nodegraph.viewer import F8StudioNodeViewer

if TYPE_CHECKING:
    from ...nodegraph.node_graph import F8StudioGraph
    from ..widgets.service_log_widget import ServiceLogDock

logger = logging.getLogger(__name__)


class MainWindowStateMixin:
    if TYPE_CHECKING:
        studio_graph: F8StudioGraph
        _log_dock: ServiceLogDock
        _default_dock_layout_state: QtCore.QByteArray
        _auto_save_enabled: bool
        _auto_deploy_enabled: bool
        _auto_proxy_enabled: bool
        _performance_overlay_enabled: bool
        _closing: bool
        _log_level_actions: dict[int, QtGui.QAction]
        _last_saved_undo_index: int
        _last_auto_deploy_observed_undo_index: int
        _last_auto_deploy_fingerprint: str
        _deferred_auto_deploy_fingerprint_timer: QtCore.QTimer
        _auto_deploy_timer: QtCore.QTimer
        _studio_runtime_sync_timer: QtCore.QTimer
        _WINDOW_LAYOUT_SETTINGS_GROUP: str
        _WINDOW_LAYOUT_STATE_KEY: str
        _WINDOW_LAYOUT_GEOMETRY_KEY: str
        _WINDOW_LAYOUT_STATE_VERSION: int
        _LOG_LEVEL_SETTINGS_GROUP: str
        _LOG_LEVEL_SETTINGS_KEY: str
        _LOG_LEVEL_CHOICES: tuple[tuple[str, int], ...]
        _AUTOMATION_SETTINGS_GROUP: str
        _AUTO_SAVE_ENABLED_SETTINGS_KEY: str
        _AUTO_DEPLOY_ENABLED_SETTINGS_KEY: str
        _VIEW_SETTINGS_GROUP: str
        _AUTO_PROXY_ENABLED_SETTINGS_KEY: str
        _PERFORMANCE_OVERLAY_ENABLED_SETTINGS_KEY: str

        def saveState(self, version: int = 0) -> QtCore.QByteArray: ...
        def restoreState(self, state: QtCore.QByteArray, version: int = 0) -> bool: ...
        def saveGeometry(self) -> QtCore.QByteArray: ...
        def restoreGeometry(self, geometry: QtCore.QByteArray) -> bool: ...
        def _apply_auto_deploy(
            self,
            *,
            compiled: CompiledRuntimeGraphs,
            current_undo_index: int,
            last_auto_deploy_observed_undo_index: int,
            last_auto_deploy_fingerprint: str,
            declared_service_ids: object,
            fingerprint: str,
        ) -> tuple[int, str]: ...
        def _declared_graph_services(self) -> dict[str, str]: ...
        def _schedule_studio_runtime_sync(self) -> None: ...

    def _layout_settings(self) -> QtCore.QSettings:
        return QtCore.QSettings()

    @staticmethod
    def _as_qbytearray(value: object) -> QtCore.QByteArray | None:
        if isinstance(value, QtCore.QByteArray):
            return value
        if isinstance(value, (bytes, bytearray)):
            return QtCore.QByteArray(bytes(value))
        return None

    def _read_layout_bytes(self, *, key: str) -> QtCore.QByteArray | None:
        settings = self._layout_settings()
        settings.beginGroup(self._WINDOW_LAYOUT_SETTINGS_GROUP)
        try:
            raw = settings.value(key)
        finally:
            settings.endGroup()
        return self._as_qbytearray(raw)

    def _write_layout_bytes(self, *, key: str, value: QtCore.QByteArray) -> None:
        settings = self._layout_settings()
        settings.beginGroup(self._WINDOW_LAYOUT_SETTINGS_GROUP)
        try:
            settings.setValue(key, value)
            settings.sync()
        finally:
            settings.endGroup()

    @classmethod
    def _normalize_supported_log_level(cls, level: int) -> int:
        normalized = int(level)
        if normalized <= logging.DEBUG:
            return logging.DEBUG
        if normalized <= logging.INFO:
            return logging.INFO
        if normalized <= logging.WARNING:
            return logging.WARNING
        if normalized <= logging.ERROR:
            return logging.ERROR
        return logging.CRITICAL

    @classmethod
    def _log_level_name_for_value(cls, level: int) -> str:
        normalized_level = cls._normalize_supported_log_level(level)
        for name, value in cls._LOG_LEVEL_CHOICES:
            if value == normalized_level:
                return str(name)
        return "WARNING"

    @classmethod
    def _log_level_value_from_name(cls, level_name: str) -> int | None:
        normalized_name = str(level_name or "").strip().upper()
        for candidate_name, candidate_value in cls._LOG_LEVEL_CHOICES:
            if candidate_name == normalized_name:
                return candidate_value
        return None

    def _read_saved_log_level_name(self) -> str:
        settings = self._layout_settings()
        settings.beginGroup(self._LOG_LEVEL_SETTINGS_GROUP)
        try:
            raw = settings.value(self._LOG_LEVEL_SETTINGS_KEY, "")
        finally:
            settings.endGroup()
        return str(raw or "").strip().upper()

    def _write_saved_log_level_name(self, *, level_name: str) -> None:
        settings = self._layout_settings()
        settings.beginGroup(self._LOG_LEVEL_SETTINGS_GROUP)
        try:
            settings.setValue(self._LOG_LEVEL_SETTINGS_KEY, str(level_name or "").strip().upper())
            settings.sync()
        finally:
            settings.endGroup()

    def _sync_log_level_actions(self, *, level: int) -> None:
        normalized_level = self._normalize_supported_log_level(level)
        for candidate_level, action in self._log_level_actions.items():
            previous = action.blockSignals(True)
            try:
                action.setChecked(candidate_level == normalized_level)
            finally:
                action.blockSignals(previous)

    def _apply_log_level(self, *, level: int, persist: bool) -> None:
        normalized_level = self._normalize_supported_log_level(level)
        apply_root_log_level(normalized_level)
        self._log_dock.set_minimum_level(normalized_level)
        self._sync_log_level_actions(level=normalized_level)
        if persist:
            self._write_saved_log_level_name(level_name=self._log_level_name_for_value(normalized_level))

    def _restore_saved_log_level(self) -> None:
        saved_level_name = self._read_saved_log_level_name()
        if not saved_level_name:
            return
        saved_level_value = self._log_level_value_from_name(saved_level_name)
        if saved_level_value is None:
            logger.warning("Invalid saved log level ignored: %s", saved_level_name)
            return
        self._apply_log_level(level=saved_level_value, persist=False)

    def _on_log_level_toggled(self, checked: bool, level: int) -> None:
        if not bool(checked):
            return
        self._apply_log_level(level=level, persist=True)

    def _capture_default_dock_layout_state(self) -> None:
        self._default_dock_layout_state = self.saveState(self._WINDOW_LAYOUT_STATE_VERSION)

    def _restore_saved_window_layout(self) -> None:
        geometry_state = self._read_layout_bytes(key=self._WINDOW_LAYOUT_GEOMETRY_KEY)
        if geometry_state is not None and not geometry_state.isEmpty():
            self.restoreGeometry(geometry_state)
        dock_state = self._read_layout_bytes(key=self._WINDOW_LAYOUT_STATE_KEY)
        if dock_state is None or dock_state.isEmpty():
            return
        restored = self.restoreState(dock_state, self._WINDOW_LAYOUT_STATE_VERSION)
        if not restored:
            logger.warning("Failed to restore dock layout from QSettings")

    def _save_window_layout(self) -> None:
        self._write_layout_bytes(
            key=self._WINDOW_LAYOUT_STATE_KEY,
            value=self.saveState(self._WINDOW_LAYOUT_STATE_VERSION),
        )
        self._write_layout_bytes(
            key=self._WINDOW_LAYOUT_GEOMETRY_KEY,
            value=self.saveGeometry(),
        )

    def _read_saved_auto_save_enabled(self) -> bool:
        settings = self._layout_settings()
        settings.beginGroup(self._AUTOMATION_SETTINGS_GROUP)
        try:
            raw = settings.value(self._AUTO_SAVE_ENABLED_SETTINGS_KEY, False)
        finally:
            settings.endGroup()
        return coerce_bool(raw, default=False)

    def _write_saved_auto_save_enabled(self, *, enabled: bool) -> None:
        settings = self._layout_settings()
        settings.beginGroup(self._AUTOMATION_SETTINGS_GROUP)
        try:
            settings.setValue(self._AUTO_SAVE_ENABLED_SETTINGS_KEY, bool(enabled))
            settings.sync()
        finally:
            settings.endGroup()

    def _read_saved_auto_deploy_enabled(self) -> bool:
        settings = self._layout_settings()
        settings.beginGroup(self._AUTOMATION_SETTINGS_GROUP)
        try:
            raw = settings.value(self._AUTO_DEPLOY_ENABLED_SETTINGS_KEY, False)
        finally:
            settings.endGroup()
        return coerce_bool(raw, default=False)

    def _write_saved_auto_deploy_enabled(self, *, enabled: bool) -> None:
        settings = self._layout_settings()
        settings.beginGroup(self._AUTOMATION_SETTINGS_GROUP)
        try:
            settings.setValue(self._AUTO_DEPLOY_ENABLED_SETTINGS_KEY, bool(enabled))
            settings.sync()
        finally:
            settings.endGroup()

    def _read_saved_kill_managed_services_on_exit_enabled(self) -> bool:
        settings = self._layout_settings()
        settings.beginGroup(self._AUTOMATION_SETTINGS_GROUP)
        try:
            raw = settings.value(self._KILL_MANAGED_SERVICES_ON_EXIT_SETTINGS_KEY, True)
        finally:
            settings.endGroup()
        return coerce_bool(raw, default=True)

    def _write_saved_kill_managed_services_on_exit_enabled(self, *, enabled: bool) -> None:
        settings = self._layout_settings()
        settings.beginGroup(self._AUTOMATION_SETTINGS_GROUP)
        try:
            settings.setValue(self._KILL_MANAGED_SERVICES_ON_EXIT_SETTINGS_KEY, bool(enabled))
            settings.sync()
        finally:
            settings.endGroup()

    def _apply_kill_managed_services_on_exit_enabled(self, *, enabled: bool, persist: bool) -> None:
        self._kill_managed_services_on_exit_enabled = bool(enabled)
        self._bridge.set_kill_managed_services_on_exit(self._kill_managed_services_on_exit_enabled)
        if persist:
            self._write_saved_kill_managed_services_on_exit_enabled(
                enabled=self._kill_managed_services_on_exit_enabled
            )

    def _read_saved_performance_overlay_enabled(self) -> bool:
        settings = self._layout_settings()
        settings.beginGroup(self._VIEW_SETTINGS_GROUP)
        try:
            raw = settings.value(self._PERFORMANCE_OVERLAY_ENABLED_SETTINGS_KEY, False)
        finally:
            settings.endGroup()
        return coerce_bool(raw, default=False)

    def _write_saved_performance_overlay_enabled(self, *, enabled: bool) -> None:
        settings = self._layout_settings()
        settings.beginGroup(self._VIEW_SETTINGS_GROUP)
        try:
            settings.setValue(self._PERFORMANCE_OVERLAY_ENABLED_SETTINGS_KEY, bool(enabled))
            settings.sync()
        finally:
            settings.endGroup()

    def _apply_performance_overlay_enabled(self, *, enabled: bool, persist: bool) -> None:
        self._performance_overlay_enabled = bool(enabled)
        viewer = self.studio_graph.viewer()
        if isinstance(viewer, F8StudioNodeViewer):
            viewer.set_performance_overlay_enabled(self._performance_overlay_enabled)
        if persist:
            self._write_saved_performance_overlay_enabled(enabled=self._performance_overlay_enabled)

    def _read_saved_auto_proxy_enabled(self) -> bool:
        settings = self._layout_settings()
        settings.beginGroup(self._VIEW_SETTINGS_GROUP)
        try:
            raw = settings.value(self._AUTO_PROXY_ENABLED_SETTINGS_KEY, False)
        finally:
            settings.endGroup()
        return coerce_bool(raw, default=False)

    def _write_saved_auto_proxy_enabled(self, *, enabled: bool) -> None:
        settings = self._layout_settings()
        settings.beginGroup(self._VIEW_SETTINGS_GROUP)
        try:
            settings.setValue(self._AUTO_PROXY_ENABLED_SETTINGS_KEY, bool(enabled))
            settings.sync()
        finally:
            settings.endGroup()

    def _apply_auto_proxy_enabled(self, *, enabled: bool, persist: bool) -> None:
        self._auto_proxy_enabled = bool(enabled)
        viewer = self.studio_graph.viewer()
        if isinstance(viewer, F8StudioNodeViewer):
            viewer.set_auto_proxy_enabled(self._auto_proxy_enabled)
        if persist:
            self._write_saved_auto_proxy_enabled(enabled=self._auto_proxy_enabled)

    def _current_undo_index(self) -> int:
        return int(self.studio_graph._undo_stack.index())  # type: ignore[attr-defined]

    def _graph_has_unsaved_changes(self) -> bool:
        return self._current_undo_index() != self._last_saved_undo_index

    def _mark_session_saved(self) -> None:
        self._last_saved_undo_index = self._current_undo_index()

    def _mark_auto_deploy_observed(self) -> None:
        self._last_auto_deploy_observed_undo_index = self._current_undo_index()

    def _deploy_fingerprint_from_compiled(self, compiled: CompiledRuntimeGraphs) -> str:
        return build_compiled_deploy_fingerprint(compiled)

    def _refresh_auto_deploy_fingerprint(self, *, compiled: CompiledRuntimeGraphs | None = None) -> None:
        resolved_compiled = compiled
        if resolved_compiled is None:
            try:
                resolved_compiled = compile_runtime_graphs_from_studio(self.studio_graph)
            except Exception:
                logger.exception("Failed to refresh auto-deploy fingerprint")
                self._last_auto_deploy_fingerprint = ""
                return
        self._last_auto_deploy_fingerprint = self._deploy_fingerprint_from_compiled(resolved_compiled)

    def _mark_auto_deploy_synced(self, *, compiled: CompiledRuntimeGraphs | None = None) -> None:
        self._mark_auto_deploy_observed()
        self._refresh_auto_deploy_fingerprint(compiled=compiled)

    def _schedule_deferred_auto_deploy_fingerprint_refresh(self) -> None:
        self._deferred_auto_deploy_fingerprint_timer.start()

    def _schedule_studio_runtime_sync(self) -> None:
        if self._closing:
            return
        self._studio_runtime_sync_timer.start()

    def _on_auto_save_toggled(self, checked: bool) -> None:
        self._auto_save_enabled = bool(checked)
        self._write_saved_auto_save_enabled(enabled=self._auto_save_enabled)

    def _on_auto_deploy_toggled(self, checked: bool) -> None:
        self._auto_deploy_enabled = bool(checked)
        if not self._auto_deploy_enabled:
            self._auto_deploy_timer.stop()
        elif self._current_undo_index() != self._last_auto_deploy_observed_undo_index:
            self._auto_deploy_timer.start()
        self._write_saved_auto_deploy_enabled(enabled=self._auto_deploy_enabled)

    def _on_kill_managed_services_on_exit_toggled(self, checked: bool) -> None:
        self._apply_kill_managed_services_on_exit_enabled(enabled=bool(checked), persist=True)

    def _on_performance_overlay_toggled(self, checked: bool) -> None:
        self._apply_performance_overlay_enabled(enabled=bool(checked), persist=True)

    def _on_auto_proxy_toggled(self, checked: bool) -> None:
        self._apply_auto_proxy_enabled(enabled=bool(checked), persist=True)

    def _on_graph_undo_index_changed(self, index: int) -> None:
        _ = index
        self._exit_autosaved = False
        if bool(self.studio_graph._loading_session):  # type: ignore[attr-defined]
            return
        self._schedule_studio_runtime_sync()
        if self._auto_deploy_enabled:
            self._auto_deploy_timer.start()

    @QtCore.Slot()
    def _on_graph_inserted(self) -> None:
        self._exit_autosaved = False
        self._schedule_studio_runtime_sync()
        if self._auto_deploy_enabled:
            self._auto_deploy_timer.start()

    @QtCore.Slot()
    def _on_graph_session_loaded(self) -> None:
        self._schedule_studio_runtime_sync()

    @QtCore.Slot()
    def _on_deferred_auto_deploy_fingerprint_timeout(self) -> None:
        if self._closing:
            return
        self._refresh_auto_deploy_fingerprint()

    @QtCore.Slot()
    def _on_periodic_auto_save_timeout(self) -> None:
        if not self._auto_save_enabled or not self._graph_has_unsaved_changes():
            return
        try:
            self.studio_graph.save_last_project()
        except Exception as exc:
            self._log_dock.report_exception("studio", "periodic auto-save failed", exc)
            return
        self._mark_session_saved()

    @QtCore.Slot()
    def _on_auto_deploy_timeout(self) -> None:
        if not self._auto_deploy_enabled:
            return
        current_undo_index = self._current_undo_index()
        if current_undo_index == self._last_auto_deploy_observed_undo_index:
            return

        try:
            compiled = compile_runtime_graphs_from_studio(self.studio_graph)
        except ValueError as exc:
            msg = str(exc or "").strip() or "auto deploy blocked by invalid graph"
            self._log_dock.append("studio", f"[deploy][auto][blocked] {msg}\n")
            self._mark_auto_deploy_observed()
            return
        except Exception as exc:
            self._log_dock.append("studio", f"[deploy][auto][error] {exc}\n")
            self._log_dock.report_exception("studio", "auto deploy compile failed", exc)
            self._mark_auto_deploy_observed()
            return

        self._last_auto_deploy_observed_undo_index, self._last_auto_deploy_fingerprint = self._apply_auto_deploy(
            compiled=compiled,
            current_undo_index=current_undo_index,
            last_auto_deploy_observed_undo_index=self._last_auto_deploy_observed_undo_index,
            last_auto_deploy_fingerprint=self._last_auto_deploy_fingerprint,
            declared_service_ids=self._declared_graph_services().keys(),
            fingerprint=self._deploy_fingerprint_from_compiled(compiled),
        )

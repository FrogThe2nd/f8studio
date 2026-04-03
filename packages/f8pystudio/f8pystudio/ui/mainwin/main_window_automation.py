from __future__ import annotations

from typing import Callable, Protocol

from qtpy import QtCore

from ...bridge.deploy_fingerprint import build_compiled_deploy_fingerprint
from ...nodegraph.runtime_compiler import CompiledRuntimeGraphs


class LogDockLike(Protocol):
    def report_exception(self, channel: str, context: str, exc: Exception) -> None: ...


def current_undo_index(undo_stack: object) -> int:
    return int(undo_stack.index())


def graph_has_unsaved_changes(*, current_undo_index: int, last_saved_undo_index: int) -> bool:
    return current_undo_index != last_saved_undo_index


def mark_session_saved(*, current_undo_index: int) -> int:
    return current_undo_index


def mark_auto_deploy_observed(*, current_undo_index: int) -> int:
    return current_undo_index


def deploy_fingerprint_from_compiled(compiled: CompiledRuntimeGraphs) -> str:
    return build_compiled_deploy_fingerprint(compiled)


def refresh_auto_deploy_fingerprint(
    *,
    current_fingerprint: str,
    compile_compiled: Callable[[], CompiledRuntimeGraphs],
    compiled: CompiledRuntimeGraphs | None = None,
) -> str:
    resolved_compiled = compiled
    if resolved_compiled is None:
        try:
            resolved_compiled = compile_compiled()
        except Exception:
            return ""
    _ = current_fingerprint
    return deploy_fingerprint_from_compiled(resolved_compiled)


def mark_auto_deploy_synced(
    *,
    current_undo_index: int,
    compile_compiled: Callable[[], CompiledRuntimeGraphs],
    current_fingerprint: str,
    compiled: CompiledRuntimeGraphs | None = None,
) -> tuple[int, str]:
    return (
        mark_auto_deploy_observed(current_undo_index=current_undo_index),
        refresh_auto_deploy_fingerprint(
            current_fingerprint=current_fingerprint,
            compile_compiled=compile_compiled,
            compiled=compiled,
        ),
    )


def on_auto_deploy_toggled(
    *,
    checked: bool,
    current_undo_index: int,
    last_auto_deploy_observed_undo_index: int,
    auto_deploy_timer: QtCore.QTimer,
) -> bool:
    enabled = bool(checked)
    if not enabled:
        auto_deploy_timer.stop()
        return enabled
    if current_undo_index != last_auto_deploy_observed_undo_index:
        auto_deploy_timer.start()
    return enabled


def on_graph_undo_index_changed(
    *,
    loading_session: bool,
    studio_runtime_sync_timer: QtCore.QTimer,
    auto_deploy_enabled: bool,
    auto_deploy_timer: QtCore.QTimer,
) -> None:
    if loading_session:
        return
    studio_runtime_sync_timer.start()
    if auto_deploy_enabled:
        auto_deploy_timer.start()


def periodic_auto_save_timeout(
    *,
    auto_save_enabled: bool,
    has_unsaved_changes: bool,
    save_last_project: Callable[[], None],
    log_dock: LogDockLike,
) -> bool:
    if not auto_save_enabled or not has_unsaved_changes:
        return False
    try:
        save_last_project()
    except Exception as exc:
        log_dock.report_exception("studio", "periodic auto-save failed", exc)
        return False
    return True

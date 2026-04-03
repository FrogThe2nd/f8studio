from __future__ import annotations

from qtpy import QtWidgets

from .project_asset_actions import (
    MessageDialogFn,
    ProjectAssetGraphLike,
    ProjectAssetLogDockLike,
    auto_load_project as project_auto_load,
    auto_save_project as project_auto_save,
    export_project_json_as_dialog as project_export_json_dialog,
    export_publish_json_dialog as project_export_publish_json_dialog,
    import_project_json_as_dialog as project_import_json_dialog,
    insert_graph_json_dialog as graph_insert_json_dialog,
    load_last_project as project_load_last,
    open_component_catalog_dialog as component_catalog_dialog,
    open_component_insert_dialog,
    open_project_dialog as project_open_dialog,
    save_component_as_dialog as component_save_as_dialog,
    save_project as project_save,
    save_project_as_dialog as project_save_as_dialog,
    show_project_history_dialog as project_history_dialog,
)
from ..dialogs.node_docs_dialog import SpecTemplate, show_node_docs_dialog


def auto_load_project(*, studio_graph: ProjectAssetGraphLike, log_dock: ProjectAssetLogDockLike) -> None:
    project_auto_load(studio_graph=studio_graph, log_dock=log_dock)


def auto_save_project(
    *,
    studio_graph: ProjectAssetGraphLike,
    log_dock: ProjectAssetLogDockLike,
    already_saved: bool,
) -> bool:
    return bool(
        project_auto_save(
            studio_graph=studio_graph,
            log_dock=log_dock,
            already_saved=already_saved,
        )
    )


def save_project(*, parent: QtWidgets.QWidget, studio_graph: ProjectAssetGraphLike, show_info: MessageDialogFn) -> None:
    project_save(parent=parent, studio_graph=studio_graph, show_info=show_info)


def load_last_project(
    *,
    parent: QtWidgets.QWidget,
    studio_graph: ProjectAssetGraphLike,
    session_file: object,
    show_info: MessageDialogFn,
) -> bool:
    return bool(
        project_load_last(
            parent=parent,
            studio_graph=studio_graph,
            session_file=session_file,
            show_info=show_info,
        )
    )


def open_project(
    *,
    parent: QtWidgets.QWidget,
    studio_graph: ProjectAssetGraphLike,
    log_dock: ProjectAssetLogDockLike,
    start_dir: str,
    show_warning: MessageDialogFn,
) -> tuple[str, bool]:
    session_dir, loaded = project_open_dialog(
        parent=parent,
        studio_graph=studio_graph,
        log_dock=log_dock,
        start_dir=start_dir,
        show_warning=show_warning,
    )
    return str(session_dir), bool(loaded)


def import_project_json(
    *,
    parent: QtWidgets.QWidget,
    studio_graph: ProjectAssetGraphLike,
    log_dock: ProjectAssetLogDockLike,
    start_dir: str,
    show_warning: MessageDialogFn,
) -> tuple[str, bool]:
    session_dir, loaded = project_import_json_dialog(
        parent=parent,
        studio_graph=studio_graph,
        log_dock=log_dock,
        start_dir=start_dir,
        show_warning=show_warning,
    )
    return str(session_dir), bool(loaded)


def save_project_as(
    *,
    parent: QtWidgets.QWidget,
    studio_graph: ProjectAssetGraphLike,
    log_dock: ProjectAssetLogDockLike,
    start_dir: str,
    show_warning: MessageDialogFn,
) -> tuple[str, bool]:
    session_dir, saved = project_save_as_dialog(
        parent=parent,
        studio_graph=studio_graph,
        log_dock=log_dock,
        start_dir=start_dir,
        show_warning=show_warning,
    )
    return str(session_dir), bool(saved)


def export_project_json(
    *,
    parent: QtWidgets.QWidget,
    studio_graph: ProjectAssetGraphLike,
    log_dock: ProjectAssetLogDockLike,
    start_dir: str,
    show_warning: MessageDialogFn,
) -> tuple[str, str]:
    session_dir, exported_path = project_export_json_dialog(
        parent=parent,
        studio_graph=studio_graph,
        log_dock=log_dock,
        start_dir=start_dir,
        show_warning=show_warning,
    )
    return str(session_dir), str(exported_path or "")


def restore_project_history(
    *,
    parent: QtWidgets.QWidget,
    studio_graph: ProjectAssetGraphLike,
    log_dock: ProjectAssetLogDockLike,
    show_warning: MessageDialogFn,
    show_info_message: MessageDialogFn,
) -> bool:
    return bool(
        project_history_dialog(
            parent=parent,
            studio_graph=studio_graph,
            log_dock=log_dock,
            show_warning=show_warning,
            show_info_message=show_info_message,
        )
    )


def save_component(
    *,
    parent: QtWidgets.QWidget,
    studio_graph: ProjectAssetGraphLike,
    log_dock: ProjectAssetLogDockLike,
    show_warning: MessageDialogFn,
) -> None:
    component_save_as_dialog(
        parent=parent,
        studio_graph=studio_graph,
        log_dock=log_dock,
        show_warning=show_warning,
    )


def manage_components(*, parent: QtWidgets.QWidget, studio_graph: ProjectAssetGraphLike) -> None:
    component_catalog_dialog(parent=parent, studio_graph=studio_graph)


def insert_component(
    *,
    parent: QtWidgets.QWidget,
    studio_graph: ProjectAssetGraphLike,
    scene_pos: tuple[float, float] | None = None,
) -> None:
    open_component_insert_dialog(
        parent=parent,
        studio_graph=studio_graph,
        insert_scene_pos=scene_pos,
    )


def show_node_docs(*, parent: QtWidgets.QWidget, spec: SpecTemplate, node_id: str, node_name: str) -> None:
    show_node_docs_dialog(parent=parent, spec=spec, node_id=node_id, node_name=node_name)


def export_publish_json(
    *,
    parent: QtWidgets.QWidget,
    studio_graph: ProjectAssetGraphLike,
    log_dock: ProjectAssetLogDockLike,
    start_dir: str,
    show_warning: MessageDialogFn,
) -> tuple[str, str]:
    session_dir, published_path = project_export_publish_json_dialog(
        parent=parent,
        studio_graph=studio_graph,
        log_dock=log_dock,
        start_dir=start_dir,
        show_warning=show_warning,
    )
    return str(session_dir), str(published_path or "")


def import_graph_json(
    *,
    parent: QtWidgets.QWidget,
    studio_graph: ProjectAssetGraphLike,
    log_dock: ProjectAssetLogDockLike,
    start_dir: str,
    show_warning: MessageDialogFn,
) -> str:
    return str(
        graph_insert_json_dialog(
            parent=parent,
            studio_graph=studio_graph,
            log_dock=log_dock,
            start_dir=start_dir,
            show_warning=show_warning,
        )
    )

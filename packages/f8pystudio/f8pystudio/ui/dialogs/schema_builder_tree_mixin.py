from __future__ import annotations

from typing import Any, cast

from qtpy import QtCore, QtGui

from .schema_builder_common import _PATH_ROLE, _decode_path, _encode_path


class SchemaBuilderTreeMixin:
    def _schema_type(self, schema_obj: dict[str, Any]) -> str:
        return str(schema_obj.get("type") or "any").strip().lower()

    def _current_path(self) -> tuple[str, ...]:
        host = cast(Any, self)
        current = host._tree.currentIndex()
        if not current.isValid():
            return ()
        path_index = current.siblingAtColumn(0)
        if not path_index.isValid():
            return ()
        return _decode_path(path_index.data(_PATH_ROLE))

    def _schema_at_path(self, path: tuple[str, ...]) -> dict[str, Any] | None:
        host = cast(Any, self)
        node: Any = host._schema_obj
        idx = 0
        while idx < len(path):
            token = path[idx]
            if not isinstance(node, dict):
                return None
            if token == "properties":
                props = node.get("properties")
                if not isinstance(props, dict):
                    return None
                idx += 1
                if idx >= len(path):
                    return None
                prop_name = path[idx]
                node = props.get(prop_name)
            elif token == "items":
                node = node.get("items")
            else:
                return None
            idx += 1
        if isinstance(node, dict):
            return node
        return None

    def _rebuild_tree(self, *, select_path: tuple[str, ...] | None = None) -> None:
        host = cast(Any, self)
        if select_path is None:
            select_path = ()

        selected_path: tuple[str, ...] = ()
        with QtCore.QSignalBlocker(host._tree):
            host._tree_model.removeRows(0, host._tree_model.rowCount())
            path_to_index: dict[tuple[str, ...], QtCore.QModelIndex] = {}

            root_path_item = QtGui.QStandardItem("$")
            root_type_item = QtGui.QStandardItem(self._schema_type(host._schema_obj))
            root_path_item.setEditable(False)
            root_type_item.setEditable(False)
            root_path_item.setData(_encode_path(()), _PATH_ROLE)
            host._tree_model.appendRow([root_path_item, root_type_item])
            path_to_index[()] = root_path_item.index()

            def _add_children(parent_item: QtGui.QStandardItem, path: tuple[str, ...], node: dict[str, Any]) -> None:
                node_type = self._schema_type(node)
                if node_type == "object":
                    properties = node.get("properties")
                    if not isinstance(properties, dict):
                        return
                    for prop_name in sorted(properties.keys()):
                        child_schema = properties.get(prop_name)
                        if not isinstance(child_schema, dict):
                            continue
                        child_path = path + ("properties", str(prop_name))
                        child_path_item = QtGui.QStandardItem(f".{prop_name}")
                        child_type_item = QtGui.QStandardItem(self._schema_type(child_schema))
                        child_path_item.setEditable(False)
                        child_type_item.setEditable(False)
                        child_path_item.setData(_encode_path(child_path), _PATH_ROLE)
                        parent_item.appendRow([child_path_item, child_type_item])
                        path_to_index[child_path] = child_path_item.index()
                        _add_children(child_path_item, child_path, child_schema)
                    return

                if node_type == "array":
                    items = node.get("items")
                    if not isinstance(items, dict):
                        return
                    child_path = path + ("items",)
                    child_path_item = QtGui.QStandardItem("[items]")
                    child_type_item = QtGui.QStandardItem(self._schema_type(items))
                    child_path_item.setEditable(False)
                    child_type_item.setEditable(False)
                    child_path_item.setData(_encode_path(child_path), _PATH_ROLE)
                    parent_item.appendRow([child_path_item, child_type_item])
                    path_to_index[child_path] = child_path_item.index()
                    _add_children(child_path_item, child_path, items)

            _add_children(root_path_item, (), host._schema_obj)
            host._tree.expandAll()
            host._tree.resizeColumnToContents(0)

            selected_index = path_to_index.get(select_path)
            if selected_index is None:
                selected_index = path_to_index.get((), QtCore.QModelIndex())
            if selected_index.isValid():
                host._tree.setCurrentIndex(selected_index)
                selected_path = _decode_path(selected_index.data(_PATH_ROLE))
            else:
                selected_path = ()

        host._render_form(selected_path)

    def _find_tree_item_for_path(self, path: tuple[str, ...]) -> QtCore.QModelIndex:
        host = cast(Any, self)

        def _walk(parent_index: QtCore.QModelIndex) -> QtCore.QModelIndex:
            rows = host._tree_model.rowCount(parent_index)
            for row in range(rows):
                index = host._tree_model.index(row, 0, parent_index)
                if not index.isValid():
                    continue
                if _decode_path(index.data(_PATH_ROLE)) == path:
                    return index
                child_match = _walk(index)
                if child_match.isValid():
                    return child_match
            return QtCore.QModelIndex()

        return _walk(QtCore.QModelIndex())

    def _on_tree_selection_changed(self, current: QtCore.QModelIndex, _previous: QtCore.QModelIndex) -> None:
        host = cast(Any, self)
        del _previous
        if not current.isValid():
            return
        path_index = current.siblingAtColumn(0)
        if not path_index.isValid():
            return
        host._render_form(_decode_path(path_index.data(_PATH_ROLE)))

    def _schedule_rebuild_tree(self, preferred_path: tuple[str, ...]) -> None:
        host = cast(Any, self)
        host._pending_rebuild_path = tuple(preferred_path)
        host._rebuild_timer.start()

    def _on_rebuild_timeout(self) -> None:
        host = cast(Any, self)
        host._rebuild_tree(select_path=host._pending_rebuild_path)

from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable, Protocol

from qtpy import QtWidgets

from .layers import (
    BASE_LAYER_ID,
    F8LayerDef,
    base_layer_def,
    extract_node_layer_ids_from_ui_state,
    merge_layer_defs,
    normalize_layer_defs,
    normalize_layer_ids,
    set_node_layer_ids_in_ui_state,
)
from .viewer import F8StudioNodeViewer


class _LayerNodeLike(Protocol):
    id: str
    view: Any

    def ui_state(self) -> dict[str, object]: ...

    def set_property(self, name: str, value: object, push_undo: bool = True) -> None: ...


class _LayerSignal0(Protocol):
    def emit(self) -> None: ...


class _LayerSignalTuple(Protocol):
    def emit(self, value: tuple[str, ...]) -> None: ...


class GraphLayeringMixin:
    _session_layer_defs: tuple[F8LayerDef, ...] = ()
    _active_layer_ids: tuple[str, ...] = ()
    layers_changed: _LayerSignal0
    active_layers_changed: _LayerSignalTuple

    def session_layer_defs(self) -> tuple[F8LayerDef, ...]:
        if self._session_layer_defs:
            return self._session_layer_defs
        self._session_layer_defs = normalize_layer_defs(())
        return self._session_layer_defs

    def active_layer_ids(self) -> tuple[str, ...]:
        return self._active_layer_ids

    def default_visible_layer_ids(self) -> tuple[str, ...]:
        defaults = [layer.id for layer in self.session_layer_defs() if bool(layer.default_visible)]
        return normalize_layer_ids(defaults, known_layer_ids=self.known_layer_ids(), allow_empty=True)

    def _normalize_active_layer_ids(self, layer_ids: Iterable[str]) -> tuple[str, ...]:
        return normalize_layer_ids(
            list(layer_ids),
            known_layer_ids=self.known_layer_ids(),
            allow_empty=True,
        )

    def layer_def_by_id(self, layer_id: str) -> F8LayerDef | None:
        target = str(layer_id or "").strip()
        for layer in self.session_layer_defs():
            if layer.id == target:
                return layer
        return None

    def known_layer_ids(self) -> tuple[str, ...]:
        return tuple(layer.id for layer in self.session_layer_defs())

    def node_layer_ids(self, node: _LayerNodeLike) -> tuple[str, ...]:
        ui_state = node.ui_state()
        return extract_node_layer_ids_from_ui_state(ui_state, known_layer_ids=self.known_layer_ids())

    def visible_layer_ids_for_node(self, node: _LayerNodeLike) -> tuple[str, ...]:
        active_ids = set(self.active_layer_ids())
        return tuple(layer_id for layer_id in self.node_layer_ids(node) if layer_id in active_ids)

    def node_visible_in_active_layers(self, node: _LayerNodeLike) -> bool:
        return bool(self.visible_layer_ids_for_node(node))

    def node_ids_share_active_layer(self, left_node: _LayerNodeLike, right_node: _LayerNodeLike) -> bool:
        left_layers = set(self.visible_layer_ids_for_node(left_node))
        if not left_layers:
            return False
        right_layers = set(self.visible_layer_ids_for_node(right_node))
        if not right_layers:
            return False
        return bool(left_layers.intersection(right_layers))

    def edge_visible_for_nodes(self, out_node: _LayerNodeLike, in_node: _LayerNodeLike) -> bool:
        if not self.node_visible_in_active_layers(out_node):
            return False
        if not self.node_visible_in_active_layers(in_node):
            return False
        return self.node_ids_share_active_layer(out_node, in_node)

    def connection_hidden_by_layer(self, out_node: _LayerNodeLike, in_node: _LayerNodeLike) -> bool:
        return not self.edge_visible_for_nodes(out_node, in_node)

    def set_session_layer_defs(
        self,
        layer_defs: Iterable[F8LayerDef | dict[str, Any]],
        *,
        preserve_active: bool,
        activate_layer_ids: Iterable[str] | None = None,
    ) -> None:
        normalized = normalize_layer_defs(list(layer_defs))
        previous_defs = self.session_layer_defs()
        previous_active = self.active_layer_ids()
        self._session_layer_defs = normalized

        known_ids = tuple(layer.id for layer in normalized)
        requested_active = list(activate_layer_ids or ())
        if preserve_active:
            next_active = list(
                normalize_layer_ids(previous_active, known_layer_ids=known_ids, allow_empty=True)
            )
            if requested_active:
                requested_visible = normalize_layer_ids(
                    requested_active,
                    known_layer_ids=known_ids,
                    allow_empty=True,
                )
            else:
                requested_visible = ()
            for layer_id in requested_visible:
                if layer_id not in next_active:
                    next_active.append(layer_id)
            if not next_active:
                next_active = list(self.default_visible_layer_ids())
        else:
            if requested_active:
                next_active = list(
                    normalize_layer_ids(
                        requested_active,
                        known_layer_ids=known_ids,
                        allow_empty=True,
                    )
                )
            else:
                next_active = []
            if not next_active:
                next_active = list(self.default_visible_layer_ids())
        self._active_layer_ids = tuple(next_active)

        if tuple(previous_defs) != tuple(normalized):
            self.layers_changed.emit()
        if tuple(previous_active) != tuple(self._active_layer_ids):
            self.active_layers_changed.emit(tuple(self._active_layer_ids))
        self.refresh_layer_visibility()

    def set_active_layer_ids(self, layer_ids: Iterable[str]) -> None:
        next_active = self._normalize_active_layer_ids(layer_ids)
        if next_active == self.active_layer_ids():
            return
        self._active_layer_ids = next_active
        self.active_layers_changed.emit(tuple(next_active))
        self.refresh_layer_visibility()

    def show_all_layers(self) -> None:
        self.set_active_layer_ids(self.known_layer_ids())

    def reset_active_layers_to_defaults(self) -> None:
        self.set_active_layer_ids(self.default_visible_layer_ids())

    def solo_layer(self, layer_id: str) -> None:
        target = str(layer_id or "").strip()
        if not target:
            return
        self.set_active_layer_ids((target,))

    def set_layer_visible(self, layer_id: str, visible: bool) -> None:
        target = str(layer_id or "").strip()
        if not target:
            return
        current = list(self.active_layer_ids())
        if bool(visible):
            if target not in current:
                current.append(target)
        else:
            current = [layer for layer in current if layer != target]
        self.set_active_layer_ids(current)

    def update_layer_definition(
        self,
        *,
        layer_id: str,
        label: str,
        description: str,
        color: str,
        default_visible: bool,
    ) -> None:
        target = str(layer_id or "").strip()
        updated: list[F8LayerDef] = []
        for layer in self.session_layer_defs():
            if layer.id != target:
                updated.append(layer)
                continue
            if layer.is_base:
                updated.append(
                    base_layer_def(
                        label=layer.label,
                        description=str(description or "").strip() or layer.description,
                        color=str(color or "").strip() or layer.color,
                        default_visible=bool(default_visible),
                    )
                )
                continue
            updated.append(
                replace(
                    layer,
                    label=str(label or "").strip() or layer.id,
                    description=str(description or "").strip(),
                    color=str(color or "").strip() or layer.color,
                    default_visible=bool(default_visible),
                )
            )
        self.set_session_layer_defs(updated, preserve_active=True)

    def add_layer(
        self,
        *,
        label: str,
        description: str,
        color: str,
        default_visible: bool,
    ) -> F8LayerDef:
        existing = list(self.session_layer_defs())
        base_label = str(label or "").strip() or "Layer"
        existing_ids = {layer.id for layer in existing}
        from .layers import next_unique_layer_id, normalize_layer_id

        layer_id = next_unique_layer_id(normalize_layer_id(base_label), existing_ids)
        new_layer = F8LayerDef(
            id=layer_id,
            label=base_label,
            description=str(description or "").strip(),
            color=str(color or "").strip() or "#64748B",
            default_visible=bool(default_visible),
            is_base=False,
        )
        existing.append(new_layer)
        self.set_session_layer_defs(existing, preserve_active=True)
        return new_layer

    def move_layer(self, layer_id: str, *, delta: int) -> None:
        target = str(layer_id or "").strip()
        if not target or target == BASE_LAYER_ID or delta == 0:
            return
        layers = list(self.session_layer_defs())
        index = -1
        for idx, layer in enumerate(layers):
            if layer.id == target:
                index = idx
                break
        if index <= 0:
            return
        new_index = max(1, min(len(layers) - 1, index + int(delta)))
        if new_index == index:
            return
        layer = layers.pop(index)
        layers.insert(new_index, layer)
        self.set_session_layer_defs(layers, preserve_active=True)

    def delete_layer(self, layer_id: str) -> None:
        target = str(layer_id or "").strip()
        if not target or target == BASE_LAYER_ID:
            return
        layers = [layer for layer in self.session_layer_defs() if layer.id != target]
        for node in list(self.all_nodes() or []):
            current = [layer_id_item for layer_id_item in self.node_layer_ids(node) if layer_id_item != target]
            if not current:
                current = [BASE_LAYER_ID]
            self.set_node_layer_ids(node, current, push_undo=False, refresh=False)
        self.set_session_layer_defs(layers, preserve_active=True)

    def set_node_layer_ids(
        self,
        node: _LayerNodeLike,
        layer_ids: Iterable[str],
        *,
        push_undo: bool,
        refresh: bool = True,
    ) -> None:
        current_ui_state = node.ui_state()
        next_ui_state = set_node_layer_ids_in_ui_state(
            current_ui_state,
            layer_ids=layer_ids,
            known_layer_ids=self.known_layer_ids(),
        )
        current_layer_ids = extract_node_layer_ids_from_ui_state(
            current_ui_state,
            known_layer_ids=self.known_layer_ids(),
        )
        next_layer_ids = extract_node_layer_ids_from_ui_state(next_ui_state, known_layer_ids=self.known_layer_ids())
        if current_layer_ids == next_layer_ids:
            return
        node.set_property("f8_ui_state", next_ui_state, push_undo=push_undo)
        if refresh:
            self.refresh_layer_visibility()

    def set_node_layer_ids_in_ui_state_for_editor(
        self,
        ui_state: object,
        layer_ids: Iterable[str],
    ) -> dict[str, Any]:
        return set_node_layer_ids_in_ui_state(
            ui_state,
            layer_ids=layer_ids,
            known_layer_ids=self.known_layer_ids(),
        )

    def default_layer_ids_for_new_node(self) -> tuple[str, ...]:
        active = self.active_layer_ids()
        if len(active) == 1:
            return active
        return (BASE_LAYER_ID,)

    def merge_imported_layer_defs(
        self,
        imported_defs: Iterable[F8LayerDef],
    ) -> tuple[tuple[F8LayerDef, ...], dict[str, str]]:
        return merge_layer_defs(self.session_layer_defs(), imported_defs)

    def refresh_layer_visibility(self) -> None:
        nodes = list(self.all_nodes() or [])

        for node in nodes:
            is_visible = self.node_visible_in_active_layers(node)
            try:
                node.view.setVisible(bool(is_visible))
            except (AttributeError, RuntimeError, TypeError):
                pass
            if not is_visible:
                try:
                    node.set_property("selected", False, push_undo=False)
                except (AttributeError, RuntimeError, TypeError):
                    pass

        viewer = self.viewer()
        if isinstance(viewer, F8StudioNodeViewer):
            viewer.refresh_edge_visibility()
            try:
                viewer.viewport().update()
            except (AttributeError, RuntimeError, TypeError):
                pass

    def on_layering_nodes_deleted(self, _node_ids: list[str]) -> None:
        self.refresh_layer_visibility()

    def active_layer_label_summary(self) -> str:
        labels: list[str] = []
        defs_by_id = {layer.id: layer for layer in self.session_layer_defs()}
        for layer_id in self.active_layer_ids():
            layer = defs_by_id.get(layer_id)
            if layer is None:
                continue
            labels.append(str(layer.label or layer.id))
        return ", ".join(labels)

    def selected_node(self) -> Any | None:
        selected = list(self.selected_nodes() or [])
        if not selected:
            return None
        return selected[0]

    def _notification_parent_for_layers(self) -> QtWidgets.QWidget | None:
        return self._notification_parent()

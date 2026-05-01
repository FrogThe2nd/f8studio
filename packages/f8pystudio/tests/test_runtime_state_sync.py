from __future__ import annotations

from types import SimpleNamespace

from f8pystudio.ui.support.runtime_state_sync import RuntimeStateSyncController
from f8pystudio.ui.widgets.node_property_panel.editor import F8StudioSingleNodePropertiesWidget


class _WidgetStub:
    def __init__(self) -> None:
        self.value: object = None
        self.blocked: list[bool] = []

    def blockSignals(self, blocked: bool) -> None:
        self.blocked.append(bool(blocked))

    def set_value(self, value: object) -> None:
        self.value = value


class _EditorStub:
    def __init__(self, widget: _WidgetStub) -> None:
        self._widget = widget

    def get_widget(self, name: str) -> _WidgetStub | None:
        if name == "ortActiveProviders":
            return self._widget
        return None


class _ModelStub:
    def __init__(self) -> None:
        self.properties: dict[str, object] = {}
        self.custom_properties: dict[str, object] = {"ortActiveProviders": ""}


class _NodeStub:
    def __init__(self) -> None:
        self.id = "ftdW"
        self.model = _ModelStub()

    def set_property(self, name: str, value: object, push_undo: bool = True) -> None:
        assert push_undo is False
        if name in self.model.properties:
            self.model.properties[name] = value
            return
        self.model.custom_properties[name] = value


class _GraphStub:
    def __init__(self, node: _NodeStub) -> None:
        self._node = node

    def get_node_by_id(self, node_id: str) -> _NodeStub | None:
        if node_id == self._node.id:
            return self._node
        return None


class _PropertyPanelStub:
    def __init__(self, editor: _EditorStub) -> None:
        self._editor = editor

    def property_editor_for_node_id(self, node_id: str) -> _EditorStub:
        assert node_id == "ftdW"
        return self._editor


class _BridgeStub:
    pass


def test_runtime_state_update_refreshes_active_property_widget() -> None:
    node = _NodeStub()
    widget = _WidgetStub()
    controller = RuntimeStateSyncController(
        studio_graph=_GraphStub(node),
        property_editor=_PropertyPanelStub(_EditorStub(widget)),
        bridge=_BridgeStub(),
        studio_service_class="f8.pystudio",
    )

    value = '["CUDAExecutionProvider", "CPUExecutionProvider"]'
    controller.on_runtime_state_updated("ftdW", "ftdW", "ortActiveProviders", value, 123)

    assert node.model.custom_properties["ortActiveProviders"] == value
    assert widget.value == value
    assert widget.blocked == [True, False]


def test_single_node_property_panel_exposes_current_editor_for_runtime_sync() -> None:
    panel = SimpleNamespace(_node_id="ftdW", _editor=object())

    assert F8StudioSingleNodePropertiesWidget.property_editor_for_node_id(panel, "ftdW") is panel._editor
    assert F8StudioSingleNodePropertiesWidget.property_editor_for_node_id(panel, "other") is None

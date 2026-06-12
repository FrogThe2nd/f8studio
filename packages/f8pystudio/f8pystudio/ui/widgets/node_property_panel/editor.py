from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from NodeGraphQt import PropertiesBinWidget
from NodeGraphQt.constants import NodeEnum
from NodeGraphQt.custom_widgets.properties_bin.node_property_widgets import PropLineEdit

from qtpy import QtCore, QtGui, QtWidgets

from ....agents.graph_context import GraphContextSnapshot, build_graph_context_snapshot
from ....agents.tools import LocalStudioGraphToolExecutor, LocalStudioGraphTools
from ....editor_assist.agent_context import EditorAgentContext
from ....nodegraph.node_graph import F8StudioGraph
from ....nodegraph.node_base import F8StudioBaseNode
from ...dialogs.node_spec_edit_dialogs import _F8EditStateFieldDialog
from ...support.node_property_support import resolve_node
from ...support.node_property_support import node_missing_lock_info
from ...support.studio_theme import label_qss, qss_rgba, studio_dark_theme, transparent_header_qss
from .common import _PROPERTY_PANEL_MIN_WIDTH, _TAB_HEADER_STYLE, _set_read_only_widget
from .editor_build_mixin import NodePropertyEditorBuildMixin
from .editor_view_state_mixin import NodePropertyEditorViewStateMixin
from .graph_sync_mixin import NodePropertyPanelGraphSyncMixin
from .selection_mixin import NodePropertyPanelSelectionMixin
from .state_fields_mixin import NodePropertyStateFieldsMixin


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _NodeEditorGraphUiContextSource:
    graph: Any | None
    node_id: str
    node_name: str

    def graph_ui_context(self) -> dict[str, Any]:
        node_id = str(self.node_id or "").strip()
        return {
            "graphRevision": _graph_revision(self.graph),
            "selectedNodeIds": [node_id] if node_id else [],
            "selectionLabel": str(self.node_name or "").strip() or node_id,
            "selectionCount": 1 if node_id else 0,
            "propertyPanelNodeId": node_id,
            "primaryNodeId": node_id,
            "primaryNodeSource": "nodeEditor",
        }


@dataclass(frozen=True)
class _NodeEditorAgentToolBundle:
    tools: tuple[object, ...] = ()
    retained_dependencies: tuple[object, ...] = ()


@dataclass(frozen=True)
class _NodePropEditorViewState:
    current_tab: str | None = None
    tab_scroll_positions: dict[str, int] = field(default_factory=dict)


class F8StudioNodePropEditorWidget(
    NodePropertyEditorBuildMixin,
    NodePropertyEditorViewStateMixin,
    NodePropertyStateFieldsMixin,
    QtWidgets.QWidget,
):
    """
    Node properties editor widget for display a Node object.

    Args:
        parent (QtWidgets.QWidget): parent object.
        node (NodeGraphQt.NodeObject): node.
    """

    #: signal (node_id, prop_name, prop_value)
    property_changed = QtCore.Signal(str, str, object)
    property_changing = QtCore.Signal(str, str, object)
    property_closed = QtCore.Signal(str)
    _STATE_FIELD_DIALOG_CLS = _F8EditStateFieldDialog
    _VIEW_STATE_CLS = _NodePropEditorViewState

    def __init__(
        self,
        parent=None,
        node=None,
        *,
        inspect_mode: bool = False,
        outer_scroll_getter: Callable[[], int] | None = None,
        outer_scroll_restorer: Callable[[int], None] | None = None,
        agent_runtime_bridge: Any | None = None,
        agent_log_source: Any | None = None,
        agent_observation_source: Any | None = None,
        on_graph_patch_applied: Callable[[], None] | None = None,
        agent_sidebar_launcher: Callable[[EditorAgentContext], None] | None = None,
    ):
        super(F8StudioNodePropEditorWidget, self).__init__(parent)
        self._node = node
        self._inspect_mode = bool(inspect_mode)
        self._outer_scroll_getter = outer_scroll_getter
        self._outer_scroll_restorer = outer_scroll_restorer
        self._agent_runtime_bridge = agent_runtime_bridge
        self._agent_log_source = agent_log_source
        self._agent_observation_source = agent_observation_source
        self._agent_graph_patch_callback = on_graph_patch_applied
        self._agent_sidebar_launcher = agent_sidebar_launcher
        self.__node_id = str(node.id or "")
        self._node_agent_tool_bundle = self._build_node_agent_tool_bundle()
        self._node_agent_context_providers: tuple[object, ...] = ()
        self.__tab_windows = {}
        self.__tab = QtWidgets.QTabWidget(self)
        self.__tab.setObjectName("f8NodePropTabs")
        self.__tab.setDocumentMode(False)
        self.__tab.setStyleSheet(_TAB_HEADER_STYLE)
        self.__tab.setUsesScrollButtons(False)
        self.__tab.tabBar().setExpanding(True)
        self.__tab.tabBar().setElideMode(QtCore.Qt.TextElideMode.ElideRight)
        self.__tab.setMinimumWidth(_PROPERTY_PANEL_MIN_WIDTH)
        self._option_pool_dependents: dict[str, list[Any]] = {}
        self._reload_pending = False
        self._reload_debounce_ms = 50
        self._reload_timer = QtCore.QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.timeout.connect(self._reload_now)

        close_btn = QtWidgets.QPushButton(self)
        close_btn.setIcon(
            QtGui.QIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DialogCloseButton))
        )
        close_btn.setFixedSize(24, 24)
        close_btn.setToolTip("close property")
        close_btn.clicked.connect(self._on_close)
        theme_palette = studio_dark_theme().palette
        close_btn.setStyleSheet(
            f"QPushButton {{ border: 0; border-radius: 4px; padding: 0; background: {qss_rgba(theme_palette.text_primary, 10)}; }}"
            f"QPushButton:hover {{ background: {qss_rgba(theme_palette.text_primary, 22)}; }}"
        )
        close_btn.setVisible(not self._inspect_mode)

        pixmap = QtGui.QPixmap()
        if node.icon():
            pixmap = QtGui.QPixmap(node.icon())

            if pixmap.size().height() > NodeEnum.ICON_SIZE.value:
                pixmap = pixmap.scaledToHeight(
                    NodeEnum.ICON_SIZE.value,
                    QtCore.Qt.TransformationMode.SmoothTransformation,
                )
            if pixmap.size().width() > NodeEnum.ICON_SIZE.value:
                pixmap = pixmap.scaledToWidth(
                    NodeEnum.ICON_SIZE.value,
                    QtCore.Qt.TransformationMode.SmoothTransformation,
                )

        self.icon_label = QtWidgets.QLabel(self)
        self.icon_label.setPixmap(pixmap)
        self.icon_label.setStyleSheet(transparent_header_qss())

        self._name_label = QtWidgets.QLabel("name", self)
        self._name_label.setStyleSheet(label_qss(color=qss_rgba(theme_palette.text_primary, 150), font_size_px=11))

        self.name_wgt = PropLineEdit(self)
        self.name_wgt.set_name("name")
        self.name_wgt.setToolTip("name\nSet the node name.")
        self.name_wgt.set_value(node.name())
        self.name_wgt.value_changed.connect(self._on_property_changed)
        self.name_wgt.setMinimumHeight(26)

        self.type_wgt = QtWidgets.QLabel(node.type_, self)
        self.type_wgt.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        self.type_wgt.setToolTip("type_\nNode type identifier followed by the class name.")
        font = self.type_wgt.font()
        font.setPointSize(9)
        self.type_wgt.setFont(font)
        self.type_wgt.setStyleSheet(
            f"{label_qss(color=qss_rgba(theme_palette.text_primary, 130))}; padding: 0 2px;"
        )

        name_layout = QtWidgets.QHBoxLayout()
        name_layout.setContentsMargins(0, 0, 0, 0)
        name_layout.setSpacing(4)
        name_layout.addWidget(self.icon_label)
        name_layout.addWidget(self._name_label)
        name_layout.addWidget(self.name_wgt)
        name_layout.addWidget(close_btn)
        missing_locked, missing_type = node_missing_lock_info(node)
        self._missing_banner = QtWidgets.QLabel(self)
        self._missing_banner.setStyleSheet(
            f"color: {theme_palette.warning}; background: {qss_rgba(theme_palette.warning, 48)}; "
            "border-radius: 4px; padding: 2px 6px;"
        )
        self._missing_banner.setVisible(bool(missing_locked))
        if missing_locked:
            self._missing_banner.setText(f"Missing dependency: {missing_type or 'unknown type'}")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        layout.addLayout(name_layout)
        layout.addWidget(self._missing_banner)
        layout.addWidget(self.__tab)
        layout.addWidget(self.type_wgt)

        self._port_connections = self._read_node(node)
        if missing_locked:
            self._apply_missing_lock_read_only()

    def __repr__(self):
        return "<{} object at {}>".format(self.__class__.__name__, hex(id(self)))

    def _apply_missing_lock_read_only(self) -> None:
        try:
            self.name_wgt.setDisabled(True)
            self.name_wgt.setToolTip("Locked: missing dependency")
        except (AttributeError, RuntimeError, TypeError):
            logger.exception("Failed to set name widget readonly on missing-locked node")
        for widget in self.get_all_property_widgets():
            if widget is self.name_wgt:
                continue
            try:
                _set_read_only_widget(widget, read_only=True)
            except Exception:
                try:
                    widget.setEnabled(False)
                except Exception:
                    logger.exception("Failed to lock property widget for missing-locked node")

    def _on_close(self):
        """
        called by the close button.
        """
        self.property_closed.emit(self.__node_id)

    def snapshot_outer_scroll_position(self) -> int | None:
        if self._outer_scroll_getter is None:
            return None
        try:
            return int(self._outer_scroll_getter())
        except (RuntimeError, TypeError, ValueError):
            logger.exception("Failed to snapshot property panel outer scroll position")
            return None

    def restore_outer_scroll_position_later(self, value: int | None) -> None:
        if value is None or self._outer_scroll_restorer is None:
            return
        target_value = int(value)

        def _restore() -> None:
            if self._outer_scroll_restorer is None:
                return
            try:
                self._outer_scroll_restorer(target_value)
            except (RuntimeError, TypeError, ValueError):
                logger.exception("Failed to restore property panel outer scroll position")

        QtCore.QTimer.singleShot(0, _restore)
        QtCore.QTimer.singleShot(0, lambda: QtCore.QTimer.singleShot(0, _restore))
        QtCore.QTimer.singleShot(50, _restore)

    def _on_property_changed(self, name, value):
        """
        slot function called when a property widget has changed.

        Args:
            name (str): property name.
            value (object): new value.
        """
        if self._inspect_mode:
            return
        self.property_changed.emit(self.__node_id, name, value)
        self.refresh_option_pool(str(name or ""))

    def _on_property_changing(self, name, value):
        """
        slot function called when a property widget is being scrubbed/previewed.

        Args:
            name (str): property name.
            value (object): new value (preview).
        """
        if self._inspect_mode:
            return
        self.property_changing.emit(self.__node_id, name, value)
        self.refresh_option_pool(str(name or ""))

    def has_option_pool_dependents(self, pool_name: str) -> bool:
        pool = str(pool_name or "").strip()
        if not pool:
            return False
        return bool(self._option_pool_dependents.get(pool))

    def refresh_option_pool(self, pool_name: str) -> bool:
        """
        Refresh option widgets that depend on a given pool state field.
        """
        pool = str(pool_name or "").strip()
        if not pool:
            return False
        dependents = list(self._option_pool_dependents.get(pool) or [])
        if not dependents:
            return False
        for w in dependents:
            try:
                w.refresh_options()
            except (AttributeError, RuntimeError, TypeError):
                continue
        return True

    def node_id(self):
        """
        Returns the node id linked to the widget.

        Returns:
            str: node id
        """
        return self.__node_id

    def graph_ui_context(self) -> dict[str, Any]:
        node_id = str(self.__node_id or "").strip()
        return {
            "graphRevision": self._graph_revision(),
            "selectedNodeIds": [node_id] if node_id else [],
            "selectionLabel": self._node_display_name(),
            "selectionCount": 1 if node_id else 0,
            "propertyPanelNodeId": node_id,
            "primaryNodeId": node_id,
            "primaryNodeSource": "nodeEditor",
        }

    def node_agent_tools(self) -> tuple[object, ...]:
        return self._node_agent_tool_bundle.tools

    def node_agent_retained_dependencies(self) -> tuple[object, ...]:
        return self._node_agent_tool_bundle.retained_dependencies

    def node_agent_context_providers(self) -> tuple[object, ...]:
        return self._node_agent_context_providers

    def node_agent_sidebar_launcher(self) -> Callable[[EditorAgentContext], None] | None:
        return self._agent_sidebar_launcher

    def node_graph_context_snapshot_provider(self, node_id: str) -> Callable[[], GraphContextSnapshot | None]:
        target_node_id = str(node_id or "").strip()
        graph = self._graph()

        def _provider() -> GraphContextSnapshot | None:
            node = resolve_node(graph, target_node_id)
            if node is None:
                return None
            return build_graph_context_snapshot(graph, node)

        return _provider

    def _build_node_agent_tool_bundle(self) -> _NodeEditorAgentToolBundle:
        if self._inspect_mode:
            return _NodeEditorAgentToolBundle()
        graph = self._graph()
        if graph is None:
            return _NodeEditorAgentToolBundle()
        node_id = str(self.__node_id or "").strip()
        ui_context_source = _NodeEditorGraphUiContextSource(
            graph=graph,
            node_id=node_id,
            node_name=self._node_display_name(),
        )
        executor = LocalStudioGraphToolExecutor(
            graph,
            bridge=self._agent_runtime_bridge,
            log_source=self._agent_log_source,
            observation_source=self._agent_observation_source,
            ui_context_source=ui_context_source,
            on_graph_patch_applied=self._agent_graph_patch_callback,
        )
        tools = LocalStudioGraphTools(executor)
        return _NodeEditorAgentToolBundle(
            tools=tuple(tools.available_node_editor_tools()),
            retained_dependencies=(executor, tools, ui_context_source),
        )

    def _graph(self) -> Any | None:
        try:
            return self._node.graph
        except (AttributeError, RuntimeError, TypeError):
            return None

    def _graph_revision(self) -> int | None:
        return _graph_revision(self._graph())

    def _node_display_name(self) -> str:
        try:
            text = str(self._node.name() or "").strip()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            text = ""
        return text or str(self.__node_id or "")


class F8StudioPropertiesBinWidget(PropertiesBinWidget):
    """
    Customized Properties Bin Widget for F8PyStudio.
    """

    def __init__(self, parent=None, node_graph=None):
        super(F8StudioPropertiesBinWidget, self).__init__(parent=parent, node_graph=node_graph)

    def create_property_editor(self, node):
        return F8StudioNodePropEditorWidget(node=node)


class F8StudioSingleNodePropertiesWidget(
    NodePropertyPanelGraphSyncMixin,
    NodePropertyPanelSelectionMixin,
    QtWidgets.QWidget,
):
    """
    Single-node properties panel (no PropertiesBinWidget/QTableWidget).

    NodeGraphQt's PropertiesBinWidget hosts editors inside a QTableWidget, which
    can scroll-jump on focus/click. Since Studio only needs one active editor,
    we present a single `F8StudioNodePropEditorWidget` inside a QScrollArea.
    """

    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
        *,
        node_graph: F8StudioGraph,
        inspect_mode: bool = False,
        empty_message: str = "Select a node to view properties.",
    ) -> None:
        super().__init__(parent)
        self._node_graph = node_graph
        self._inspect_mode = bool(inspect_mode)
        self._agent_runtime_bridge: Any | None = None
        self._agent_log_source: Any | None = None
        self._agent_observation_source: Any | None = None
        self._agent_graph_patch_callback: Callable[[], None] | None = None
        self._agent_sidebar_launcher: Callable[[EditorAgentContext], None] | None = None
        self._node_id: str | None = None
        self._editor: F8StudioNodePropEditorWidget | None = None
        self._block_signal = False
        self._last_ui_overrides_reload_fingerprint = ""
        self._last_node_click_ts: float = 0.0
        self._selection_timer = QtCore.QTimer(self)
        self._selection_timer.setSingleShot(True)
        self._selection_timer.timeout.connect(self._apply_graph_selection)

        self._scroll = QtWidgets.QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumWidth(_PROPERTY_PANEL_MIN_WIDTH)

        self._container = QtWidgets.QWidget(self._scroll)
        self._container.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        self._container_layout = QtWidgets.QVBoxLayout(self._container)
        self._container_layout.setContentsMargins(0, 0, 0, 0)
        self._container_layout.setSpacing(0)

        self._empty = QtWidgets.QLabel(str(empty_message or "Select a node to view properties."), self._container)
        self._empty.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._empty.setStyleSheet(f"{label_qss(color=qss_rgba(studio_dark_theme().palette.text_primary, 150))}; padding: 14px;")
        self._container_layout.addWidget(self._empty, 1)

        self._scroll.setWidget(self._container)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._scroll, 1)

        self._wire_graph_signals()
        QtCore.QTimer.singleShot(0, self._sync_container_width)

    def set_agent_tool_dependencies(
        self,
        *,
        runtime_bridge: Any | None,
        log_source: Any | None,
        observation_source: Any | None,
        on_graph_patch_applied: Callable[[], None] | None = None,
        agent_sidebar_launcher: Callable[[EditorAgentContext], None] | None = None,
    ) -> None:
        self._agent_runtime_bridge = runtime_bridge
        self._agent_log_source = log_source
        self._agent_observation_source = observation_source
        self._agent_graph_patch_callback = on_graph_patch_applied
        self._agent_sidebar_launcher = agent_sidebar_launcher

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._sync_container_width()

    def _sync_container_width(self) -> None:
        """
        Keep the content widget width aligned to the scroll viewport width.

        This prevents QScrollArea from showing a horizontal scrollbar due to
        the container/editor having a slightly larger size hint.
        """
        try:
            vp_w = int(self._scroll.viewport().width())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return
        if vp_w <= 0:
            return
        try:
            self._container.setMinimumWidth(vp_w)
        except (AttributeError, RuntimeError, TypeError):
            logger.exception("Failed to sync property panel container width")

    @staticmethod
    def _log_exception(message: str, *args: Any) -> None:
        logger.exception(message, *args)

    def _outer_scroll_position(self) -> int:
        try:
            return int(self._scroll.verticalScrollBar().value())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            self._log_exception("Failed to read property panel scroll position")
            return 0

    def current_node_id(self) -> str:
        return str(self._node_id or "")

    def _build_property_editor(self, *, node: F8StudioBaseNode) -> F8StudioNodePropEditorWidget:
        return F8StudioNodePropEditorWidget(
            self._container,
            node=node,
            inspect_mode=self._inspect_mode,
            outer_scroll_getter=self._outer_scroll_position,
            outer_scroll_restorer=self._restore_outer_scroll_position,
            agent_runtime_bridge=self._agent_runtime_bridge,
            agent_log_source=self._agent_log_source,
            agent_observation_source=self._agent_observation_source,
            on_graph_patch_applied=self._agent_graph_patch_callback,
            agent_sidebar_launcher=self._agent_sidebar_launcher,
        )

    def property_editor_for_node_id(self, node_id: str) -> F8StudioNodePropEditorWidget | None:
        node_id = str(node_id or "")
        if not node_id or node_id != self._node_id:
            return None
        return self._editor


def _graph_revision(graph: Any | None) -> int | None:
    if graph is None:
        return None
    try:
        undo_stack = graph._undo_stack
        return int(undo_stack.index())
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None

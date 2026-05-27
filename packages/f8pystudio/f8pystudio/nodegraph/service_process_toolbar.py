from __future__ import annotations

import logging
from typing import Any, Callable

from qtpy import QtCore, QtWidgets

from f8pystudio.studio_specs.identifiers import STUDIO_SERVICE_ID
from ..ui.support.studio_theme import service_process_toolbar_qss
from ..ui.support.ui_icons import StudioIcon, icon_for

from .service_bridge_protocol import ServiceBridge


logger = logging.getLogger(__name__)
_QT_WIDGET_ERRORS = (AttributeError, RuntimeError, TypeError)


class ServiceProcessToolbar(QtWidgets.QWidget):
    """
    Small toolbar widget (Start/Pause + Stop + Restart) for service process control.

    This controls the local `ServiceProcessManager` via `PyStudioServiceBridge`.
    """

    def __init__(
        self,
        parent=None,
        *,
        service_id: str,
        get_bridge: Callable[[], ServiceBridge | None],
        get_node: Callable[[], Any | None] | None = None,
        get_service_class: Callable[[], str] | None = None,
        get_compiled_graphs: Callable[[], Any | None] | None = None,
    ):
        super().__init__(parent)
        self._service_id = str(service_id or "")
        self._get_bridge = get_bridge
        self._get_node = get_node
        self._get_service_class = get_service_class
        self._get_compiled_graphs = get_compiled_graphs
        self._debug_once_keys: set[str] = set()

        self._btn_disable = QtWidgets.QToolButton(self)
        self._btn_toggle = QtWidgets.QToolButton(self)  # start/pause (active)
        self._btn_stop = QtWidgets.QToolButton(self)  # quit process
        self._btn_sync = QtWidgets.QToolButton(self)  # deploy
        self._btn_restart = QtWidgets.QToolButton(self)

        self._disable_icon = icon_for(self, StudioIcon.TOGGLE_ON)
        self._enable_icon = icon_for(self, StudioIcon.TOGGLE_OFF)
        self._play_icon = icon_for(self, StudioIcon.PLAY)
        self._pause_icon = icon_for(self, StudioIcon.PAUSE)
        self._stop_icon = icon_for(self, StudioIcon.STOP)
        self._sync_icon = icon_for(self, StudioIcon.TRANSFER)
        self._restart_icon = icon_for(self, StudioIcon.RESTART)

        self._btn_disable.setAutoRaise(True)
        self._btn_toggle.setAutoRaise(True)
        self._btn_stop.setAutoRaise(True)
        self._btn_sync.setAutoRaise(True)
        self._btn_restart.setAutoRaise(True)
        self._btn_disable.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self._btn_toggle.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self._btn_stop.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self._btn_sync.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self._btn_restart.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)

        # Disable is a local-only studio feature: do NOT depend on service_bridge.
        # Use a plain click handler (not checkable) to avoid QToolButton check-state
        # weirdness inside QGraphicsProxyWidget.
        self._btn_disable.setCheckable(False)
        self._btn_disable.setIcon(self._disable_icon)
        self._btn_disable.setToolTip("Disable node (skip in rungraph + do not auto-start)")
        self._btn_disable.clicked.connect(self._on_disable_clicked)  # type: ignore[attr-defined]

        # Default icons even before first successful refresh (eg. bridge not ready yet).
        self._btn_toggle.setIcon(self._play_icon)
        self._btn_stop.setIcon(self._stop_icon)
        self._btn_sync.setIcon(self._sync_icon)
        self._btn_restart.setIcon(self._restart_icon)

        self._btn_toggle.setToolTip("Start service (deploy + activate)")
        self._btn_stop.setToolTip("Terminate service process")
        self._btn_sync.setToolTip("Deploy current rungraph to service")
        self._btn_restart.setToolTip("Restart service (terminate + deploy + activate)")

        self._btn_toggle.clicked.connect(self._on_toggle_clicked)  # type: ignore[attr-defined]
        self._btn_stop.clicked.connect(self._on_stop_clicked)  # type: ignore[attr-defined]
        self._btn_sync.clicked.connect(self._on_sync_clicked)  # type: ignore[attr-defined]
        self._btn_restart.clicked.connect(self._on_restart_clicked)  # type: ignore[attr-defined]

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lay.addWidget(self._btn_disable)
        lay.addWidget(self._btn_toggle)
        lay.addWidget(self._btn_stop)
        lay.addWidget(self._btn_sync)
        lay.addWidget(self._btn_restart)

        # Match NodeGraphQt's dark UI: a subtle "badge" container with hover feedback.
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.setStyleSheet(service_process_toolbar_qss())

        # Poll state so crashes/external stops are reflected.
        self._timer = QtCore.QTimer(self)
        # Keep UI responsive when services are started/stopped outside this process.
        self._timer.setInterval(400)
        self._timer.timeout.connect(self.refresh)  # type: ignore[attr-defined]
        self._timer.start()

        self.refresh()

    def _debug_once(self, key: str, message: str, *args: object, exc: BaseException) -> None:
        if key in self._debug_once_keys:
            return
        self._debug_once_keys.add(key)
        logger.debug(message, *args, exc_info=exc)

    def _set_process_buttons_enabled(
        self,
        *,
        disable: bool,
        toggle: bool,
        stop: bool,
        sync: bool,
        restart: bool,
        toggle_tooltip: str | None = None,
    ) -> None:
        try:
            self._btn_disable.setEnabled(bool(disable))
            self._btn_toggle.setEnabled(bool(toggle))
            self._btn_stop.setEnabled(bool(stop))
            self._btn_sync.setEnabled(bool(sync))
            self._btn_restart.setEnabled(bool(restart))
            if toggle_tooltip is not None:
                self._btn_toggle.setToolTip(str(toggle_tooltip))
        except _QT_WIDGET_ERRORS as exc:
            self._debug_once(
                "set_button_enabled_failed",
                "Service toolbar failed to update button enabled states service_id=%s",
                self._service_id,
                exc=exc,
            )

    def _set_disabled_button_state(self, disabled: bool) -> None:
        try:
            self._btn_disable.setEnabled(True)
            self._btn_disable.setIcon(self._enable_icon if disabled else self._disable_icon)
            self._btn_disable.setToolTip("Enable node" if disabled else "Disable node (skip in rungraph + do not auto-start)")
        except _QT_WIDGET_ERRORS as exc:
            self._debug_once(
                "set_disable_button_failed",
                "Service toolbar failed to update disable button state service_id=%s",
                self._service_id,
                exc=exc,
            )

    def _set_disabled_process_tooltips(self) -> None:
        try:
            self._btn_toggle.setToolTip("Disabled")
            self._btn_stop.setToolTip("Disabled")
            self._btn_sync.setToolTip("Disabled")
            self._btn_restart.setToolTip("Disabled")
        except _QT_WIDGET_ERRORS as exc:
            self._debug_once(
                "set_disabled_tooltips_failed",
                "Service toolbar failed to update disabled process tooltips service_id=%s",
                self._service_id,
                exc=exc,
            )

    def set_service_id(self, service_id: str) -> None:
        self._service_id = str(service_id or "").strip()
        self.refresh()

    def _bridge(self) -> ServiceBridge | None:
        try:
            b = self._get_bridge()
            return b if b is not None else None
        except Exception as exc:
            self._debug_once("get_bridge_failed", "Service toolbar bridge provider failed service_id=%s", self._service_id, exc=exc)
            return None

    def _node(self) -> Any | None:
        try:
            return self._get_node() if self._get_node is not None else None
        except Exception as exc:
            self._debug_once("get_node_failed", "Service toolbar node provider failed service_id=%s", self._service_id, exc=exc)
            return None

    def _node_item(self) -> Any | None:
        """
        Best-effort access to the QGraphicsItem node view item that owns this toolbar.

        This allows disabling the node locally even when the backend node/bridge isn't available yet.
        """
        try:
            proxy = self.graphicsProxyWidget()
        except _QT_WIDGET_ERRORS as exc:
            self._debug_once("graphics_proxy_failed", "Service toolbar graphics proxy lookup failed service_id=%s", self._service_id, exc=exc)
            proxy = None
        if proxy is None:
            return None
        try:
            return proxy.parentItem()
        except _QT_WIDGET_ERRORS as exc:
            self._debug_once("proxy_parent_failed", "Service toolbar proxy parent lookup failed service_id=%s", self._service_id, exc=exc)
            return None

    def _is_node_disabled(self) -> bool:
        n = self._node()
        if n is not None:
            try:
                return bool(n.disabled())
            except _QT_WIDGET_ERRORS as exc:
                self._debug_once("node_disabled_method_failed", "Service toolbar failed to read node.disabled() service_id=%s", self._service_id, exc=exc)
            try:
                return bool(n.view.disabled)
            except _QT_WIDGET_ERRORS as exc:
                self._debug_once("node_view_disabled_failed", "Service toolbar failed to read node view disabled state service_id=%s", self._service_id, exc=exc)
        # Fallback: use the view item directly.
        item = self._node_item()
        if item is not None:
            try:
                return bool(item.disabled)
            except _QT_WIDGET_ERRORS as exc:
                self._debug_once("item_disabled_failed", "Service toolbar failed to read item disabled state service_id=%s", self._service_id, exc=exc)
        return False

    def _set_node_disabled(self, disabled: bool) -> None:
        n = self._node()
        if n is not None:
            try:
                n.set_disabled(bool(disabled))
                return
            except _QT_WIDGET_ERRORS as exc:
                self._debug_once("node_set_disabled_failed", "Service toolbar failed to call node.set_disabled service_id=%s", self._service_id, exc=exc)
            # Prefer setting backend node state (persists in session); also try the view.
            try:
                n.view.disabled = bool(disabled)
            except _QT_WIDGET_ERRORS as exc:
                self._debug_once("node_view_set_disabled_failed", "Service toolbar failed to set node view disabled state service_id=%s", self._service_id, exc=exc)
        # Fallback: disable the view item directly (local-only).
        item = self._node_item()
        if item is None:
            return
        try:
            item.disabled = bool(disabled)
        except _QT_WIDGET_ERRORS as exc:
            self._debug_once("item_set_disabled_failed", "Service toolbar failed to set item disabled state service_id=%s", self._service_id, exc=exc)
            return

    def _is_running(self) -> bool:
        bridge = self._bridge()
        if bridge is None:
            return False
        try:
            return bool(bridge.is_service_running(self._service_id))
        except Exception as exc:
            self._debug_once("is_running_failed", "Service toolbar failed to read running state service_id=%s", self._service_id, exc=exc)
            return False

    def _service_class(self) -> str:
        try:
            return str(self._get_service_class() or "") if self._get_service_class is not None else ""
        except Exception as exc:
            self._debug_once("get_service_class_failed", "Service toolbar service-class provider failed service_id=%s", self._service_id, exc=exc)
            return ""

    def _compiled_graphs(self) -> Any | None:
        try:
            return self._get_compiled_graphs() if self._get_compiled_graphs is not None else None
        except Exception as exc:
            self._debug_once("get_compiled_graphs_failed", "Service toolbar compiled-graphs provider failed service_id=%s", self._service_id, exc=exc)
            return None

    @QtCore.Slot()
    def refresh(self) -> None:
        sid = str(self._service_id or "").strip()
        enabled = bool(sid) and sid != STUDIO_SERVICE_ID
        if not enabled:
            self._set_process_buttons_enabled(disable=False, toggle=False, stop=False, sync=False, restart=False)
            return

        # During node creation / graph reload, the toolbar widget can exist briefly
        # before the proxy is in a scene or the backend node is resolvable. This
        # is a transient state; do not stop polling or permanently disable the UI.
        item = self._node_item()
        if item is not None:
            try:
                if item.scene() is None:
                    # Not in scene yet (or being removed). Keep polling.
                    self._set_process_buttons_enabled(
                        disable=True,
                        toggle=False,
                        stop=False,
                        sync=False,
                        restart=False,
                        toggle_tooltip="Start service (initializing)",
                    )
                    return
            except _QT_WIDGET_ERRORS as exc:
                self._debug_once("node_scene_check_failed", "Service toolbar scene check failed service_id=%s", sid, exc=exc)
        if self._node() is None and item is None:
            # Backend graph/node not ready yet (or node was deleted). Keep polling;
            # if the widget is truly orphaned it will be deleted with its proxy.
            self._set_process_buttons_enabled(
                disable=False,
                toggle=False,
                stop=False,
                sync=False,
                restart=False,
                toggle_tooltip="Start service (node not ready)",
            )
            return

        # Disable button works even without a bridge connection.
        disabled = self._is_node_disabled()
        # Show current state: when disabled -> show "enable" check icon; else show "ban".
        self._set_disabled_button_state(disabled)

        # When disabled, lock out process controls regardless of bridge availability.
        if disabled:
            self._set_process_buttons_enabled(disable=True, toggle=False, stop=False, sync=False, restart=False)
            self._set_disabled_process_tooltips()
            return

        bridge = self._bridge()

        # If bridge isn't available yet, keep the process buttons visible but disabled.
        if bridge is None:
            self._set_process_buttons_enabled(
                disable=True,
                toggle=False,
                stop=False,
                sync=False,
                restart=False,
                toggle_tooltip="Start service (bridge not ready)",
            )
            return

        try:
            bridge.request_service_status(sid)
        except _QT_WIDGET_ERRORS as exc:
            self._debug_once("request_status_failed", "Service toolbar status request failed service_id=%s", sid, exc=exc)
        try:
            running = bool(bridge.is_service_running(self._service_id))
        except Exception as exc:
            self._debug_once("refresh_running_failed", "Service toolbar failed to refresh running state service_id=%s", sid, exc=exc)
            running = False
        active = None
        if running:
            try:
                active = bridge.get_cached_service_active(sid)
            except Exception as exc:
                self._debug_once("refresh_active_failed", "Service toolbar failed to refresh active state service_id=%s", sid, exc=exc)
                active = None

        if not running:
            self._btn_toggle.setIcon(self._play_icon)
            self._btn_toggle.setToolTip("Start service (deploy + activate)")
        else:
            if active is False:
                self._btn_toggle.setIcon(self._play_icon)
                self._btn_toggle.setToolTip("Activate service")
            else:
                self._btn_toggle.setIcon(self._pause_icon)
                self._btn_toggle.setToolTip("Deactivate service")

        self._btn_stop.setIcon(self._stop_icon)
        self._btn_stop.setToolTip("Terminate service process")

        self._btn_sync.setIcon(self._sync_icon)
        self._btn_sync.setToolTip("Deploy current rungraph to service")

        self._btn_restart.setIcon(self._restart_icon)
        self._btn_restart.setToolTip("Restart service (terminate + deploy + activate)")

        # Button availability.
        self._btn_stop.setEnabled(bool(running))
        self._btn_sync.setEnabled(bool(running))
        self._btn_restart.setEnabled(bool(running))
        self._btn_toggle.setEnabled(True)

    @QtCore.Slot()
    def _on_disable_clicked(self) -> None:
        cur = self._is_node_disabled()
        nxt = not bool(cur)
        self._set_node_disabled(bool(nxt))
        try:
            self.refresh()
        except _QT_WIDGET_ERRORS as exc:
            self._debug_once("refresh_after_disable_failed", "Service toolbar refresh after disable failed service_id=%s", self._service_id, exc=exc)

    def _on_toggle_clicked(self) -> None:
        bridge = self._bridge()
        if bridge is None:
            return
        try:
            sid = str(self._service_id or "").strip()
            if not sid or sid == STUDIO_SERVICE_ID:
                return
            if not self._is_running():
                compiled = self._compiled_graphs()
                if compiled is not None:
                    bridge.start_service_and_deploy(sid, service_class=self._service_class(), compiled=compiled)
                else:
                    bridge.start_service_and_deploy(sid, service_class=self._service_class())
                return

            active = None
            try:
                active = bridge.get_cached_service_active(sid)
            except _QT_WIDGET_ERRORS as exc:
                self._debug_once("toggle_active_failed", "Service toolbar failed to read active state during toggle service_id=%s", sid, exc=exc)
                active = None
            if active is False:
                bridge.set_service_active(sid, True)
            else:
                bridge.set_service_active(sid, False)
        finally:
            self.refresh()

    def _on_stop_clicked(self) -> None:
        bridge = self._bridge()
        if bridge is None:
            return
        try:
            sid = str(self._service_id or "").strip()
            if not sid or sid == STUDIO_SERVICE_ID:
                return
            bridge.stop_service(sid)
        finally:
            self.refresh()

    def _on_restart_clicked(self) -> None:
        bridge = self._bridge()
        if bridge is None:
            return
        try:
            sid = str(self._service_id or "").strip()
            if not sid or sid == STUDIO_SERVICE_ID:
                return
            compiled = self._compiled_graphs()
            if compiled is not None:
                bridge.restart_service_and_deploy(sid, service_class=self._service_class(), compiled=compiled)
            else:
                bridge.restart_service_and_deploy(sid, service_class=self._service_class())
        finally:
            self.refresh()

    def _on_sync_clicked(self) -> None:
        bridge = self._bridge()
        if bridge is None:
            return
        try:
            sid = str(self._service_id or "").strip()
            if not sid or sid == STUDIO_SERVICE_ID:
                return
            if not self._is_running():
                return
            compiled = self._compiled_graphs()
            if compiled is None:
                return
            bridge.deploy_service_rungraph(sid, compiled=compiled)
        finally:
            self.refresh()

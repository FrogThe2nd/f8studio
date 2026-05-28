from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from f8pysdk.codec import dump_json
from f8pysdk.registry import Registry
from f8pysdk.service_runtime_tools.inventory.catalog import ServiceCatalog
from f8pysdk.service_runtime_tools.inventory.describe import (
    last_discovery_error_lines,
    last_discovery_timing_lines,
)
from f8pysdk.service_runtime_tools.inventory.discovery import (
    load_discovery_into_catalog,
)
from f8pysdk.service_runtime_tools.inventory.policy import (
    load_default_service_discovery_policy,
    merge_disabled_service_classes,
)

from f8pystudio.plugins.api import StudioPluginManifest
from f8pystudio.plugins.loader import load_entrypoint_plugins
from f8pystudio.bridge.runtime_lifecycle import SINGLETON_GUARD_DIALOG_TITLE
from f8pystudio.studio_specs.registry import (
    create_pystudio_registry,
    SERVICE_CLASS,
    shared_pystudio_registry,
)
from f8pystudio.nodegraph.node_type_ids import SERVICE_NODE_IDENTIFIER
from f8pystudio.bridge.runtime_config import PyStudioServiceBridgeConfig
from f8pystudio.bridge.studio_bridge import STARTUP_GATE_TIMEOUT_S, PyStudioServiceBridge
from f8pystudio.ui.support.ui_resources import studio_logo_path

logger = logging.getLogger(__name__)
MISSING_SERVICE_NODE_TYPE = "svc.f8.missing.service"
MISSING_OPERATOR_NODE_TYPE = "svc.f8.missing.operator"
LAUNCH_READY_FILE_ENV = "F8STUDIO_LAUNCH_READY_FILE"
LAUNCH_DISMISS_FILE_ENV = "F8STUDIO_LAUNCH_DISMISS_FILE"
_LAUNCHER_SIGNAL_ERRORS = (OSError, RuntimeError, TypeError, ValueError)
_PLUGIN_REGISTRATION_ERRORS = (Exception,)
_QT_APP_SETUP_ERRORS = (AttributeError, RuntimeError, TypeError, ValueError)
_QT_SHUTDOWN_ERRORS = (AttributeError, RuntimeError, TypeError, ValueError)


class PyStudioProgram:
    def __init__(self, bridge_config: PyStudioServiceBridgeConfig | None = None) -> None:
        self._bridge_config = bridge_config if bridge_config is not None else PyStudioServiceBridgeConfig()

    @staticmethod
    def _write_launcher_signal(*, env_name: str, content: str) -> None:
        signal_file_raw = (os.environ.get(env_name) or "").strip()
        if not signal_file_raw:
            return
        signal_file = Path(signal_file_raw).expanduser()
        try:
            signal_file.parent.mkdir(parents=True, exist_ok=True)
            signal_file.write_text(str(content), encoding="utf-8")
        except _LAUNCHER_SIGNAL_ERRORS:
            logger.exception("Failed to write launcher signal: env=%s path=%s", env_name, signal_file)

    @classmethod
    def _notify_launcher_ready(cls) -> None:
        cls._write_launcher_signal(env_name=LAUNCH_READY_FILE_ENV, content="ready\n")

    @classmethod
    def _dismiss_launcher_for_dialog(cls) -> None:
        cls._write_launcher_signal(env_name=LAUNCH_DISMISS_FILE_ENV, content="dismiss\n")

    @staticmethod
    def _studio_icon_path() -> Path | None:
        env_icon = (os.environ.get("F8_STUDIO_ICON_PATH") or "").strip()
        if env_icon:
            candidate = Path(env_icon).expanduser()
            if candidate.exists():
                return candidate

        package_icon = studio_logo_path()
        if package_icon.exists():
            return package_icon

        return None

    def describe_json(self) -> dict[str, Any]:
        registry = Registry.wrap(create_pystudio_registry())
        manifests = self._load_plugin_manifests()
        self._apply_plugin_manifests_to_registry(manifests, registry=registry)
        return dump_json(registry.describe(SERVICE_CLASS), mode="json")

    @staticmethod
    def _inject_pystudio_specs_from_registry(
        catalog: ServiceCatalog,
        *,
        registry: Registry,
    ) -> str | None:
        service_spec = registry.service_spec(SERVICE_CLASS)
        if service_spec is None:
            return None
        catalog.register_service(service_spec)
        for operator_spec in registry.operator_specs(SERVICE_CLASS):
            catalog.register_operator(operator_spec)
        return str(service_spec.serviceClass)

    @staticmethod
    def _inject_builtin_pystudio_specs(catalog: ServiceCatalog) -> str | None:
        return PyStudioProgram._inject_pystudio_specs_from_registry(
            catalog,
            registry=Registry.wrap(create_pystudio_registry()),
        )

    @staticmethod
    def _load_plugin_manifests() -> list[StudioPluginManifest]:
        manifests = load_entrypoint_plugins()
        for manifest in manifests:
            logger.debug(
                "Loaded plugin manifest: id=%s name=%s version=%s",
                manifest.plugin_id,
                manifest.plugin_name,
                manifest.plugin_version,
            )
        return manifests

    @staticmethod
    def _apply_plugin_manifests_to_registry(
        manifests: list[StudioPluginManifest], *, registry: Registry
    ) -> None:
        if not manifests:
            return
        for manifest in manifests:
            for op_reg in manifest.operators:
                try:
                    out_reg = op_reg.register(registry)
                except _PLUGIN_REGISTRATION_ERRORS:
                    logger.exception("Operator registration failed in plugin '%s'", manifest.plugin_id)
                    continue
                if out_reg is not registry:
                    logger.warning(
                        "Plugin '%s' returned a different registry instance; ignoring replacement.",
                        manifest.plugin_id,
                    )

    @staticmethod
    def _apply_plugin_manifests_to_renderers(manifests: list[StudioPluginManifest]) -> None:
        if not manifests:
            return
        from f8pystudio.render_nodes import RenderNodeRegistry

        render_registry = RenderNodeRegistry.instance()
        for manifest in manifests:
            for renderer in manifest.renderers:
                key = str(renderer.renderer_class).strip()
                if not key:
                    logger.warning("Skip empty renderer key in plugin '%s'", manifest.plugin_id)
                    continue
                try:
                    render_registry.register(key, renderer.node_class)
                except ValueError:
                    logger.warning(
                        "Renderer already registered (skip): key=%s plugin_id=%s",
                        key,
                        manifest.plugin_id,
                    )
                except TypeError:
                    logger.exception(
                        "Invalid renderer class in plugin '%s' for key '%s'",
                        manifest.plugin_id,
                        key,
                    )

    @staticmethod
    def build_node_classes() -> list[type]:
        from f8pystudio.render_nodes import RenderNodeRegistry
        from f8pystudio.nodegraph.missing_operator_basenode import F8StudioOperatorMissingNode
        from f8pystudio.nodegraph.missing_service_basenode import F8StudioServiceMissingNode

        render_node_reg = RenderNodeRegistry.instance()
        service_catalog = ServiceCatalog.instance()

        generated_node_cls: list[type] = []
        for svc in service_catalog.services.all():
            base_cls = render_node_reg.get(svc.rendererClass, node_kind="service")
            node_cls = type(
                svc.serviceClass,
                (base_cls,),
                {
                    "__identifier__": SERVICE_NODE_IDENTIFIER,
                    "NODE_NAME": svc.label,
                    "SPEC_TEMPLATE": svc,
                },
            )
            generated_node_cls.append(node_cls)

        for op in service_catalog.operators.all():
            base_cls = render_node_reg.get(op.rendererClass, node_kind="operator")
            node_cls = type(
                op.operatorClass,
                (base_cls,),
                {
                    "__identifier__": str(op.serviceClass),
                    "NODE_NAME": op.label,
                    "SPEC_TEMPLATE": op,
                },
            )
            generated_node_cls.append(node_cls)

        missing_service_cls = type(
            "service",
            (F8StudioServiceMissingNode,),
            {"__identifier__": "svc.f8.missing", "NODE_NAME": "Missing Service"},
        )
        missing_operator_cls = type(
            "operator",
            (F8StudioOperatorMissingNode,),
            {"__identifier__": "svc.f8.missing", "NODE_NAME": "Missing Operator"},
        )
        assert str(missing_service_cls.type_) == MISSING_SERVICE_NODE_TYPE
        assert str(missing_operator_cls.type_) == MISSING_OPERATOR_NODE_TYPE
        generated_node_cls.append(missing_service_cls)
        generated_node_cls.append(missing_operator_cls)

        return generated_node_cls

    def run(self) -> int:
        # Local import: keep `--describe` fast and avoid importing Qt at module import time.
        from qtpy import QtCore, QtGui, QtWidgets

        from f8pystudio.ui.support.qt_font_utils import normalize_application_font
        from f8pystudio.ui.support.studio_theme import apply_studio_theme, studio_dark_theme
        from f8pystudio.ui.support.webengine_utils import (
            flush_qt_deferred_deletes,
            prewarm_webengine_view,
            release_prewarmed_webengine_view,
        )
        from f8pystudio.ui.mainwin.main_window import F8StudioMainWin

        manifests = self._load_plugin_manifests()
        studio_registry = Registry.wrap(shared_pystudio_registry())
        self._apply_plugin_manifests_to_registry(manifests, registry=studio_registry)
        discovery_policy = load_default_service_discovery_policy()
        disabled_service_classes = merge_disabled_service_classes(policy=discovery_policy)

        load_discovery_into_catalog(
            catalog=ServiceCatalog.instance(),
            builtin_injectors=(
                lambda catalog: self._inject_pystudio_specs_from_registry(catalog, registry=studio_registry),
            ),
            disabled_service_classes=disabled_service_classes,
        )
        self._apply_plugin_manifests_to_renderers(manifests)

        node_classes = self.build_node_classes()
        icon_path = self._studio_icon_path()

        try:
            QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_ShareOpenGLContexts, True)  # type: ignore[attr-defined]
        except _QT_APP_SETUP_ERRORS:
            logger.exception("Failed to set Qt shared OpenGL context attribute")

        app = QtWidgets.QApplication([])
        app.setOrganizationName("Feel8")
        app.setApplicationName("F8PyStudio")
        normalize_application_font(app)
        apply_studio_theme(app, studio_dark_theme())
        if icon_path is not None:
            app_icon = QtGui.QIcon(str(icon_path))
            if not app_icon.isNull():
                app.setWindowIcon(app_icon)
        bridge = PyStudioServiceBridge(self._bridge_config)
        startup_blocked_message = bridge.wait_for_startup_preflight(timeout_s=STARTUP_GATE_TIMEOUT_S)
        if startup_blocked_message is not None:
            self._dismiss_launcher_for_dialog()
            QtWidgets.QMessageBox.warning(None, SINGLETON_GUARD_DIALOG_TITLE, str(startup_blocked_message))
            bridge.stop()
            return 0

        mainwin = F8StudioMainWin(node_classes, bridge=bridge)
        if icon_path is not None:
            mainwin_icon = QtGui.QIcon(str(icon_path))
            if not mainwin_icon.isNull():
                mainwin.setWindowIcon(mainwin_icon)

        startup_blocked_message = mainwin.start_bridge_and_wait_for_startup(timeout_s=STARTUP_GATE_TIMEOUT_S)
        if startup_blocked_message is not None:
            self._dismiss_launcher_for_dialog()
            QtWidgets.QMessageBox.warning(None, SINGLETON_GUARD_DIALOG_TITLE, str(startup_blocked_message))
            mainwin.stop_bridge()
            mainwin.close()
            return 0

        prewarm_webengine_view()
        mainwin.prepare_before_show()
        mainwin.show()
        self._notify_launcher_ready()
        mainwin.schedule_deferred_startup()
        app.processEvents()

        mainwin.append_discovery_logs(
            timing_lines=last_discovery_timing_lines(),
            error_lines=last_discovery_error_lines(),
        )
        try:
            return int(app.exec_() or 0)
        finally:
            try:
                mainwin.shutdown_for_app_exit()
            except _QT_SHUTDOWN_ERRORS:
                logger.exception("failed to shutdown main window after Qt loop exit")
            try:
                mainwin.deleteLater()
            except (AttributeError, RuntimeError, TypeError):
                logger.debug("failed to deleteLater main window after Qt loop exit", exc_info=True)
            release_prewarmed_webengine_view()
            flush_qt_deferred_deletes()

    def describe_json_text(self) -> str:
        return json.dumps(self.describe_json(), ensure_ascii=False)

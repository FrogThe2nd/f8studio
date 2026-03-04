from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from f8pysdk.runtime_node_registry import RuntimeNodeRegistry
from f8pysdk.service_runtime_tools.catalog import ServiceCatalog
from f8pysdk.service_runtime_tools.discovery import (
    last_discovery_error_lines,
    last_discovery_timing_lines,
    load_discovery_into_catalog,
)

from .pystudio_node_registry import SERVICE_CLASS, register_pystudio_specs

logger = logging.getLogger(__name__)
MISSING_SERVICE_NODE_TYPE = "svc.f8.missing.service"
MISSING_OPERATOR_NODE_TYPE = "svc.f8.missing.operator"


class PyStudioProgram:
    @staticmethod
    def _studio_icon_path() -> Path | None:
        env_icon = (os.environ.get("F8_STUDIO_ICON_PATH") or "").strip()
        if env_icon:
            candidate = Path(env_icon).expanduser()
            if candidate.exists():
                return candidate

        package_icon = Path(__file__).resolve().parent / "assets" / "logo.png"
        if package_icon.exists():
            return package_icon

        return None

    def describe_json(self) -> dict[str, Any]:
        register_pystudio_specs()
        return RuntimeNodeRegistry.instance().describe(SERVICE_CLASS).model_dump(mode="json")

    @staticmethod
    def _inject_builtin_pystudio_specs(catalog: ServiceCatalog) -> str | None:
        registry = register_pystudio_specs()
        service_spec = registry.service_spec(SERVICE_CLASS)
        if service_spec is None:
            return None
        catalog.register_service(service_spec)
        for operator_spec in registry.operator_specs(SERVICE_CLASS):
            catalog.register_operator(operator_spec)
        return str(service_spec.serviceClass)

    @staticmethod
    def build_node_classes() -> list[type]:
        from .render_nodes import RenderNodeRegistry
        from .nodegraph.missing_operator_basenode import F8StudioOperatorMissingNode
        from .nodegraph.missing_service_basenode import F8StudioServiceMissingNode

        render_node_reg = RenderNodeRegistry.instance()
        service_catalog = ServiceCatalog.instance()

        generated_node_cls: list[type] = []
        for svc in service_catalog.services.all():
            base_cls = render_node_reg.get(svc.rendererClass, node_kind="service")
            node_cls = type(
                svc.serviceClass,
                (base_cls,),
                {"__identifier__": "svc", "NODE_NAME": svc.label, "SPEC_TEMPLATE": svc},
            )
            generated_node_cls.append(node_cls)

        for op in service_catalog.operators.all():
            base_cls = render_node_reg.get(op.rendererClass, node_kind="operator")
            node_cls = type(
                op.operatorClass,
                (base_cls,),
                {"__identifier__": op.serviceClass, "NODE_NAME": op.label, "SPEC_TEMPLATE": op},
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
        from qtpy import QtGui, QtWidgets

        from .widgets.main_window import F8StudioMainWin

        load_discovery_into_catalog(
            catalog=ServiceCatalog.instance(),
            builtin_injectors=(self._inject_builtin_pystudio_specs,),
        )

        node_classes = self.build_node_classes()

        app = QtWidgets.QApplication([])
        icon_path = self._studio_icon_path()
        if icon_path is not None:
            app_icon = QtGui.QIcon(str(icon_path))
            if not app_icon.isNull():
                app.setWindowIcon(app_icon)
        mainwin = F8StudioMainWin(node_classes)
        if icon_path is not None:
            mainwin_icon = QtGui.QIcon(str(icon_path))
            if not mainwin_icon.isNull():
                mainwin.setWindowIcon(mainwin_icon)
        mainwin.show()

        try:
            timing_lines = last_discovery_timing_lines()
            for line in timing_lines:
                mainwin._bridge.log.emit(str(line))  # type: ignore[attr-defined]
            # Avoid double-printing errors: timings output can already include them.
            if not any("discovery errors:" in str(x) for x in timing_lines):
                for line in last_discovery_error_lines():
                    mainwin._bridge.log.emit(str(line))  # type: ignore[attr-defined]
        except Exception:
            logger.exception("Failed to emit discovery logs to studio log dock")
        return int(app.exec_() or 0)

    def describe_json_text(self) -> str:
        return json.dumps(self.describe_json(), ensure_ascii=False)

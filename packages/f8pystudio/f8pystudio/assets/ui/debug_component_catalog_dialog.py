from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from typing import Any

from qtpy import QtWidgets

from f8pystudio.diagnostics.logging import configure_root_logging_from_env
from .component_catalog_dialog import ComponentCatalogDialog

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _StubGraphPlacementRequest:
    node_count: int


class _StubGraph:
    """Minimal graph shim for exercising component dialog actions in isolation."""

    def serialize_publish_session(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "nodes": [],
            "edges": [],
        }

    def prepare_insert_graph_from_component(self, content: Any, *, component_name: str) -> _StubGraphPlacementRequest:
        logger.debug(
            "Stub graph prepare_insert_graph_from_component called component_name=%s content_type=%s",
            component_name,
            type(content).__name__,
        )
        return _StubGraphPlacementRequest(node_count=0)

    def begin_graph_placement(self, request: _StubGraphPlacementRequest, *, label: str) -> None:
        logger.debug(
            "Stub graph begin_graph_placement called node_count=%d label=%s",
            int(request.node_count),
            label,
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone Component Catalog dialog launcher")
    parser.add_argument(
        "--no-graph",
        action="store_true",
        help="Do not provide a stub graph object (Save/Insert actions will be disabled or no-op).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_root_logging_from_env()
    parser = _build_parser()
    args = parser.parse_args(argv)

    graph = None if bool(args.no_graph) else _StubGraph()

    app = QtWidgets.QApplication.instance()
    owns_app = app is None
    if app is None:
        app = QtWidgets.QApplication([])
        app.setOrganizationName("Feel8")
        app.setApplicationName("F8PyStudio")

    dialog = ComponentCatalogDialog(parent=None, node_graph=graph)

    if owns_app:
        dialog.show()
        return app.exec()
    return int(dialog.exec())


if __name__ == "__main__":
    raise SystemExit(main())

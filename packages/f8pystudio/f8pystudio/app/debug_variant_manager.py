from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass

from qtpy import QtWidgets

from f8pystudio.assets.ui.variant_manager_dialog import VariantManagerDialog
from f8pystudio.diagnostics.logging import configure_root_logging_from_env

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _StubNode:
    type_: str


class _StubGraph:
    """Minimal graph shim for exercising dialog actions in isolation."""

    def __init__(self, base_node_type: str) -> None:
        self._selected_nodes: list[_StubNode] = [_StubNode(type_=base_node_type)]

    def selected_nodes(self) -> list[_StubNode]:
        return list(self._selected_nodes)

    def begin_node_placement(self, node_type: str, placement_label: str) -> None:
        logger.debug(
            "Stub graph begin_node_placement called node_type=%s label=%s",
            node_type,
            placement_label,
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone Variant Manager dialog launcher")
    parser.add_argument(
        "--base-node-type",
        default="f8.python_script",
        help="Base node type used to filter variants.",
    )
    parser.add_argument(
        "--base-node-name",
        default="Python Script",
        help="Display name shown in dialog title and placement label.",
    )
    parser.add_argument(
        "--no-graph",
        action="store_true",
        help="Do not provide a stub graph object (Create/Add actions will be disabled or no-op).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_root_logging_from_env()
    parser = _build_parser()
    args = parser.parse_args(argv)

    base_node_type = str(args.base_node_type or "").strip()
    if not base_node_type:
        raise ValueError("--base-node-type cannot be empty")
    base_node_name = str(args.base_node_name or "").strip() or base_node_type

    graph = None if bool(args.no_graph) else _StubGraph(base_node_type)

    app = QtWidgets.QApplication.instance()
    owns_app = app is None
    if app is None:
        app = QtWidgets.QApplication([])
        app.setOrganizationName("Feel8")
        app.setApplicationName("F8PyStudio")

    dialog = VariantManagerDialog(
        parent=None,
        base_node_type=base_node_type,
        base_node_name=base_node_name,
        node_graph=graph,
    )

    if owns_app:
        dialog.show()
        return app.exec()
    return int(dialog.exec())


if __name__ == "__main__":
    raise SystemExit(main())

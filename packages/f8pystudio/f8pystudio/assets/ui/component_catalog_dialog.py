from __future__ import annotations

from typing import Any

from qtpy import QtWidgets

from ..components.component_models import F8ComponentEntry
from ..components.component_sync import ComponentSyncClient
from .catalog_status import AssetCatalogRowState
from .component_catalog_browser import ComponentCatalogBrowserMixin
from .component_catalog_entries import ComponentCatalogEntriesMixin
from .component_catalog_ui import ComponentCatalogUiMixin
from .component_catalog_actions_mixin import ComponentCatalogActionsMixin
from .component_catalog_selection import ComponentCatalogSelectionMixin
from .component_catalog_sync_flows import ComponentCatalogSyncFlowsMixin
from .component_catalog_version_flows import ComponentCatalogVersionFlowsMixin


class ComponentCatalogDialog(
    ComponentCatalogActionsMixin,
    ComponentCatalogSelectionMixin,
    ComponentCatalogBrowserMixin,
    ComponentCatalogEntriesMixin,
    ComponentCatalogVersionFlowsMixin,
    ComponentCatalogSyncFlowsMixin,
    ComponentCatalogUiMixin,
    QtWidgets.QDialog,
):
    _TAB_MINE, _TAB_COMMUNITY, _TAB_INSTALLED = 0, 1, 2
    LOCAL_DRAFT_LABEL, LOCAL_DRAFT_LOAD_TOOLTIP = "Local Draft", "Not available for Local Draft"

    def __init__(self, *, parent: QtWidgets.QWidget | None, node_graph: Any) -> None:
        super().__init__(parent)
        self._graph = node_graph
        self._entries: list[F8ComponentEntry] = []
        self._row_states_by_component_id: dict[str, AssetCatalogRowState] = {}
        self._sync_client = ComponentSyncClient()
        self._initialize_browser_state()
        self._initialize_ui(node_graph=node_graph)
        self._reload()

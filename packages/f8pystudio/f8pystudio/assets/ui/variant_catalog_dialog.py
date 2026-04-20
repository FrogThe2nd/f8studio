from __future__ import annotations

from typing import Any

from qtpy import QtWidgets

from ..variants.variant_models import F8VariantEntry
from ..variants.variant_sync import VariantSyncClient
from .catalog_status import AssetCatalogRowState
from .variant_catalog_actions_mixin import VariantCatalogActionsMixin
from .variant_catalog_browser import VariantCatalogBrowserMixin
from .variant_catalog_entries import VariantCatalogEntriesMixin, variant_row_state_for_entries
from .variant_catalog_selection import VariantCatalogSelectionMixin
from .variant_catalog_sync_flows import VariantCatalogSyncFlowsMixin
from .variant_catalog_ui import VariantCatalogUiMixin
from .variant_catalog_version_flows import VariantCatalogVersionFlowsMixin


class VariantCatalogDialog(
    VariantCatalogActionsMixin,
    VariantCatalogSelectionMixin,
    VariantCatalogBrowserMixin,
    VariantCatalogEntriesMixin,
    VariantCatalogVersionFlowsMixin,
    VariantCatalogSyncFlowsMixin,
    VariantCatalogUiMixin,
    QtWidgets.QDialog,
):
    _TAB_MINE = 0
    _TAB_COMMUNITY = 1
    _TAB_INSTALLED = 2
    LOCAL_DRAFT_LABEL = "Local Draft"
    LOCAL_DRAFT_LOAD_TOOLTIP = "Not available for Local Draft"

    def __init__(
        self,
        *,
        parent: QtWidgets.QWidget | None,
        base_node_type: str | None = None,
        base_node_name: str | None = None,
        node_graph: Any,
    ) -> None:
        super().__init__(parent)
        self._base_node_type = str(base_node_type or "").strip()
        self._is_global_mode = not self._base_node_type
        self._base_node_name = str(base_node_name or "").strip() or self._base_node_type or "All Variants"
        self._graph = node_graph
        self._entries: list[F8VariantEntry] = []
        self._row_states_by_variant_id: dict[str, AssetCatalogRowState] = {}
        self._sync_client = VariantSyncClient()
        self._initialize_browser_state()
        self._initialize_selection_state()
        self._initialize_ui(node_graph=node_graph)
        if self._is_global_mode:
            self._populate_node_type_combo()
        self._reload()

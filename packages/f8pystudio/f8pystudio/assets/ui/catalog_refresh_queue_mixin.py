from __future__ import annotations

from typing import TYPE_CHECKING, Generic, TypeVar

from .catalog_browser_state import QueuedCatalogRefresh
from .catalog_auth_state_mixin import CatalogAuthStateMixin
from .catalog_hosts import _ComponentCatalogDialogHost, _VariantCatalogDialogHost
from .catalog_remote_refresh_worker_mixin import CatalogRemoteRefreshWorkerMixin

RemoteScopeRefreshRequestT = TypeVar("RemoteScopeRefreshRequestT")

if TYPE_CHECKING:
    _CatalogRefreshQueueMixinBase = _ComponentCatalogDialogHost | _VariantCatalogDialogHost
else:
    _CatalogRefreshQueueMixinBase = object


class CatalogRefreshQueueMixin(
    CatalogRemoteRefreshWorkerMixin[RemoteScopeRefreshRequestT],
    CatalogAuthStateMixin[RemoteScopeRefreshRequestT, object],
    Generic[RemoteScopeRefreshRequestT],
    _CatalogRefreshQueueMixinBase,
):
    _remote_refresh_worker: object | None
    _queued_remote_refresh: QueuedCatalogRefresh[RemoteScopeRefreshRequestT] | None

    def _request_catalog_refresh(
        self,
        *,
        requests: list[RemoteScopeRefreshRequestT],
        error_title: str,
        log_label: str,
    ) -> None:
        if not requests:
            return
        if self._remote_refresh_worker is not None:
            self._queued_remote_refresh = QueuedCatalogRefresh(
                requests=requests,
                error_title=error_title,
                log_label=log_label,
            )
            return
        self._start_catalog_refresh(requests=requests, error_title=error_title, log_label=log_label)

    def _start_queued_catalog_refresh_if_any(self) -> None:
        queued_refresh = self._queued_remote_refresh
        if queued_refresh is None:
            return
        self._queued_remote_refresh = None
        self._start_catalog_refresh(
            requests=queued_refresh.requests,
            error_title=queued_refresh.error_title,
            log_label=queued_refresh.log_label,
        )

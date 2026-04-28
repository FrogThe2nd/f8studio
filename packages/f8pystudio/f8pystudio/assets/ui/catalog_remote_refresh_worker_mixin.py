from __future__ import annotations

from collections.abc import Callable
import time
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar

from .background_tasks import BackgroundCallWorker
from .catalog_browser_state import CatalogRefreshLogFields
from .catalog_hosts import _ComponentCatalogDialogHost, _VariantCatalogDialogHost

RemoteScopeRefreshRequestT = TypeVar("RemoteScopeRefreshRequestT")


class CatalogRemoteRefreshWorkerHooks(Protocol[RemoteScopeRefreshRequestT]):
    _active_remote_refresh_request_id: int
    _active_remote_refresh_error_title: str
    _active_remote_refresh_log_label: str
    _active_remote_refresh_started_at: float
    _is_loading_remote_scope: bool
    _remote_refresh_worker: BackgroundCallWorker | None

    def _catalog_refresh_task(
        self,
        *,
        requests: list[RemoteScopeRefreshRequestT],
    ) -> Callable[[], object]: ...

    def _catalog_refresh_log_fields(
        self,
        *,
        request_id: int,
        log_label: str,
        requests: list[RemoteScopeRefreshRequestT],
    ) -> CatalogRefreshLogFields: ...

    def _log_catalog_refresh_queued(self, *, log_fields: CatalogRefreshLogFields) -> None: ...

    def _sync_auth_controls_ui(self) -> None: ...

    def _on_catalog_refresh_succeeded(
        self,
        *,
        finished_request_id: int,
        result: object,
        elapsed_seconds: float,
        error_title: str,
        log_label: str,
        started_at: float,
    ) -> None: ...

    def _on_catalog_refresh_failed(
        self,
        *,
        finished_request_id: int,
        exc: object,
        elapsed_seconds: float,
        error_title: str,
        log_label: str,
    ) -> None: ...


if TYPE_CHECKING:
    _CatalogRemoteRefreshWorkerMixinBase = (
        CatalogRemoteRefreshWorkerHooks[RemoteScopeRefreshRequestT]
        | _ComponentCatalogDialogHost
        | _VariantCatalogDialogHost
    )
else:
    _CatalogRemoteRefreshWorkerMixinBase = object


class CatalogRemoteRefreshWorkerMixin(Generic[RemoteScopeRefreshRequestT], _CatalogRemoteRefreshWorkerMixinBase):
    def _start_catalog_refresh(
        self,
        *,
        requests: list[RemoteScopeRefreshRequestT],
        error_title: str,
        log_label: str,
    ) -> None:
        self._active_remote_refresh_request_id += 1
        request_id = self._active_remote_refresh_request_id
        started_at = time.perf_counter()
        self._remote_refresh_worker = BackgroundCallWorker(
            request_id=request_id,
            task=self._catalog_refresh_task(requests=requests),
        )
        self._is_loading_remote_scope = True
        self._sync_auth_controls_ui()
        log_fields = self._catalog_refresh_log_fields(
            request_id=request_id,
            log_label=log_label,
            requests=requests,
        )
        self._log_catalog_refresh_queued(log_fields=log_fields)
        self._active_remote_refresh_error_title = error_title
        self._active_remote_refresh_log_label = log_label
        self._active_remote_refresh_started_at = started_at
        worker = self._remote_refresh_worker
        worker.succeeded.connect(self._handle_catalog_refresh_succeeded)  # type: ignore[attr-defined]
        worker.failed.connect(self._handle_catalog_refresh_failed)  # type: ignore[attr-defined]
        worker.start()

    def _handle_catalog_refresh_succeeded(
        self,
        finished_request_id: int,
        result: object,
        elapsed_seconds: float,
    ) -> None:
        self._on_catalog_refresh_succeeded(
            finished_request_id=int(finished_request_id),
            result=result,
            elapsed_seconds=float(elapsed_seconds),
            error_title=self._active_remote_refresh_error_title,
            log_label=self._active_remote_refresh_log_label,
            started_at=self._active_remote_refresh_started_at,
        )

    def _handle_catalog_refresh_failed(
        self,
        finished_request_id: int,
        exc: object,
        elapsed_seconds: float,
    ) -> None:
        self._on_catalog_refresh_failed(
            finished_request_id=int(finished_request_id),
            exc=exc,
            elapsed_seconds=float(elapsed_seconds),
            error_title=self._active_remote_refresh_error_title,
            log_label=self._active_remote_refresh_log_label,
        )

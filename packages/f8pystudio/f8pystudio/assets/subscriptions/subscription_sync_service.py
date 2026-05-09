from __future__ import annotations

from dataclasses import dataclass
import logging
import queue
import threading
from typing import Literal, Protocol

from qtpy import QtCore

from ..components.component_catalog import component_entry_is_installed
from ..components.component_models import (
    F8ComponentEntry,
    F8ComponentRemoteAuthError,
    F8ComponentRemoteListPage,
    F8ComponentRemoteRequestError,
    F8ComponentRemoteUser,
)
from ..components.component_sync import ComponentSyncClient
from ..variants.variant_catalog import variant_entry_is_installed
from ..variants.variant_models import (
    F8VariantEntry,
    F8VariantRemoteAuthError,
    F8VariantRemoteListPage,
    F8VariantRemoteRequestError,
    F8VariantRemoteUser,
)
from ..variants.variant_sync import VariantSyncClient

logger = logging.getLogger(__name__)

RequestKind = Literal["idle", "manual", "startup"]
AssetKind = Literal["component", "variant"]
_WORKER_IDLE_TIMEOUT_S = 0.1


class VariantSyncClientLike(Protocol):
    def clone_for_background(self) -> VariantSyncClientLike: ...
    def current_user(self) -> F8VariantRemoteUser | None: ...
    def refresh_scope_page(
        self,
        *,
        scope: str,
        kind: str = "",
        base_node_type: str = "",
        query: str = "",
        cursor: str = "",
        append: bool = False,
    ) -> F8VariantRemoteListPage: ...
    def remote_entry(self, variant_id: str) -> F8VariantEntry | None: ...
    def install_variant(self, variant_id: str) -> F8VariantEntry: ...


class ComponentSyncClientLike(Protocol):
    def clone_for_background(self) -> ComponentSyncClientLike: ...
    def current_user(self) -> F8ComponentRemoteUser | None: ...
    def refresh_scope_page(
        self,
        *,
        scope: str,
        query: str = "",
        cursor: str = "",
        append: bool = False,
    ) -> F8ComponentRemoteListPage: ...
    def remote_entry(self, component_id: str) -> F8ComponentEntry | None: ...
    def install_component(self, component_id: str) -> F8ComponentEntry: ...


@dataclass(frozen=True)
class _SyncItem:
    asset_kind: AssetKind
    asset_id: str


class SubscriptionSyncService(QtCore.QObject):
    sync_started = QtCore.Signal(int)
    sync_progress = QtCore.Signal(int, int)
    sync_item_failed = QtCore.Signal(str, str)
    sync_finished = QtCore.Signal(int, int, int)

    def __init__(
        self,
        *,
        variant_client: VariantSyncClientLike | None = None,
        component_client: ComponentSyncClientLike | None = None,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._variant_client: VariantSyncClientLike = VariantSyncClient() if variant_client is None else variant_client
        self._component_client: ComponentSyncClientLike = ComponentSyncClient() if component_client is None else component_client
        self._request_queue: queue.Queue[RequestKind | None] = queue.Queue()
        self._worker_thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._running = False
        self._active_request_kind: RequestKind = "idle"
        self._last_completed_request_kind: RequestKind = "idle"
        self._pending_request_kind: RequestKind | None = None
        self._cancel_requested = False
        self._shutdown_requested = False

    def start_initial_sync(self) -> None:
        self._enqueue_request("startup")

    def request_manual_refresh(self) -> None:
        self._enqueue_request("manual")

    def cancel(self) -> None:
        with self._state_lock:
            self._cancel_requested = True

    def shutdown(self, *, wait: bool = True, timeout_s: float = 2.0) -> None:
        with self._state_lock:
            self._shutdown_requested = True
            self._cancel_requested = True
            self._pending_request_kind = None
            worker_thread = self._worker_thread
        if worker_thread is None:
            return
        self._request_queue.put(None)
        if wait and worker_thread is not threading.current_thread():
            worker_thread.join(timeout=max(0.0, float(timeout_s)))

    def is_running(self) -> bool:
        with self._state_lock:
            return self._running

    def active_request_kind(self) -> RequestKind:
        with self._state_lock:
            return self._active_request_kind

    def last_completed_request_kind(self) -> RequestKind:
        with self._state_lock:
            return self._last_completed_request_kind

    def _enqueue_request(self, request_kind: RequestKind) -> None:
        if self._variant_client.current_user() is None:
            return
        with self._state_lock:
            if self._shutdown_requested:
                return
        self._ensure_worker_thread()
        with self._state_lock:
            if self._shutdown_requested:
                return
            if self._running:
                if self._pending_request_kind != "manual":
                    self._pending_request_kind = request_kind
                elif request_kind == "manual":
                    self._pending_request_kind = "manual"
                return
            self._running = True
            self._active_request_kind = request_kind
            self._cancel_requested = False
        self._request_queue.put(request_kind)

    def _ensure_worker_thread(self) -> None:
        worker_thread = self._worker_thread
        if worker_thread is not None and worker_thread.is_alive():
            return
        worker_thread = threading.Thread(
            target=self._worker_loop,
            name="f8pystudio-subscription-sync",
            daemon=True,
        )
        self._worker_thread = worker_thread
        worker_thread.start()

    def _worker_loop(self) -> None:
        while True:
            try:
                request_kind = self._request_queue.get(timeout=_WORKER_IDLE_TIMEOUT_S)
            except queue.Empty:
                with self._state_lock:
                    if self._running or self._pending_request_kind is not None:
                        continue
                    if self._worker_thread is threading.current_thread():
                        self._worker_thread = None
                    return
            if request_kind is None:
                with self._state_lock:
                    if self._worker_thread is threading.current_thread():
                        self._worker_thread = None
                return
            next_request_kind: RequestKind | None = request_kind
            while next_request_kind is not None:
                try:
                    self._run_sync_pass(next_request_kind)
                except Exception:
                    logger.exception(
                        "Subscription sync pass failed request_kind=%s",
                        next_request_kind,
                    )
                with self._state_lock:
                    pending_request_kind = self._pending_request_kind
                    self._pending_request_kind = None
                    if pending_request_kind is None:
                        self._running = False
                        self._active_request_kind = "idle"
                        self._cancel_requested = False
                        next_request_kind = None
                    else:
                        self._active_request_kind = pending_request_kind
                        self._cancel_requested = False
                        next_request_kind = pending_request_kind

    def _run_sync_pass(self, request_kind: RequestKind) -> None:
        variant_client = self._variant_client.clone_for_background()
        component_client = self._component_client.clone_for_background()
        if variant_client.current_user() is None:
            with self._state_lock:
                self._last_completed_request_kind = request_kind
            return

        try:
            variant_items, variant_skipped = self._collect_variant_items(variant_client)
        except F8VariantRemoteAuthError as exc:
            self._complete_after_collection_auth_failure(request_kind=request_kind, exc=exc)
            return
        except F8VariantRemoteRequestError as exc:
            self._complete_after_collection_request_failure(request_kind=request_kind, asset_kind="variant", exc=exc)
            return
        if self._cancelled():
            return
        try:
            component_items, component_skipped = self._collect_component_items(component_client)
        except F8ComponentRemoteAuthError as exc:
            self._complete_after_collection_auth_failure(request_kind=request_kind, exc=exc)
            return
        except F8ComponentRemoteRequestError as exc:
            self._complete_after_collection_request_failure(request_kind=request_kind, asset_kind="component", exc=exc)
            return
        if self._cancelled():
            return

        items = variant_items + component_items
        skipped = variant_skipped + component_skipped
        total = len(items)
        self.sync_started.emit(total)

        installed = 0
        failed = 0
        done = 0
        for item in items:
            if self._cancelled():
                skipped += total - done
                break
            try:
                if item.asset_kind == "variant":
                    variant_client.install_variant(item.asset_id)
                else:
                    component_client.install_component(item.asset_id)
                installed += 1
            except (F8VariantRemoteAuthError, F8ComponentRemoteAuthError) as exc:
                failed += 1
                done += 1
                skipped += total - done
                self.sync_item_failed.emit(item.asset_id, str(exc))
                logger.warning(
                    "Subscription sync halted after auth failure request_kind=%s asset_kind=%s asset_id=%s error=%s",
                    request_kind,
                    item.asset_kind,
                    item.asset_id,
                    str(exc),
                )
                self.sync_progress.emit(done, total)
                break
            except (F8VariantRemoteRequestError, F8ComponentRemoteRequestError) as exc:
                failed += 1
                done += 1
                skipped += total - done
                self.sync_item_failed.emit(item.asset_id, str(exc))
                logger.warning(
                    "Subscription sync halted after remote request failure request_kind=%s asset_kind=%s asset_id=%s error=%s",
                    request_kind,
                    item.asset_kind,
                    item.asset_id,
                    str(exc),
                )
                self.sync_progress.emit(done, total)
                break
            except Exception as exc:
                failed += 1
                done += 1
                self.sync_item_failed.emit(item.asset_id, str(exc))
                logger.exception(
                    "Subscription sync item failed request_kind=%s asset_kind=%s asset_id=%s",
                    request_kind,
                    item.asset_kind,
                    item.asset_id,
                )
                self.sync_progress.emit(done, total)
                continue
            done += 1
            self.sync_progress.emit(done, total)

        self.sync_finished.emit(installed, failed, skipped)
        with self._state_lock:
            self._last_completed_request_kind = request_kind

    def _complete_after_collection_request_failure(
        self,
        *,
        request_kind: RequestKind,
        asset_kind: AssetKind,
        exc: F8VariantRemoteRequestError | F8ComponentRemoteRequestError,
    ) -> None:
        logger.warning(
            "Subscription sync skipped after remote request failure request_kind=%s asset_kind=%s error=%s",
            request_kind,
            asset_kind,
            str(exc),
        )
        self.sync_finished.emit(0, 1, 0)
        with self._state_lock:
            self._last_completed_request_kind = request_kind

    def _complete_after_collection_auth_failure(
        self,
        *,
        request_kind: RequestKind,
        exc: F8VariantRemoteAuthError | F8ComponentRemoteAuthError,
    ) -> None:
        logger.warning(
            "Subscription sync skipped after auth failure request_kind=%s error=%s",
            request_kind,
            str(exc),
        )
        self.sync_finished.emit(0, 0, 0)
        with self._state_lock:
            self._last_completed_request_kind = request_kind

    def _collect_variant_items(self, client: VariantSyncClientLike) -> tuple[list[_SyncItem], int]:
        items: list[_SyncItem] = []
        queued_asset_ids: set[str] = set()
        skipped = 0
        cursor = ""
        append = False
        while True:
            if self._cancelled():
                return items, skipped
            page = client.refresh_scope_page(scope="subscribed", cursor=cursor, append=append)
            for entry in page.entries:
                asset_id = str(entry.record.variantId)
                if asset_id in queued_asset_ids:
                    continue
                cached_entry = client.remote_entry(asset_id)
                if cached_entry is None or not variant_entry_is_installed(cached_entry):
                    items.append(_SyncItem(asset_kind="variant", asset_id=asset_id))
                    queued_asset_ids.add(asset_id)
                else:
                    skipped += 1
            cursor = "" if page.nextCursor is None else str(page.nextCursor)
            if not cursor:
                break
            append = True
        return items, skipped

    def _collect_component_items(self, client: ComponentSyncClientLike) -> tuple[list[_SyncItem], int]:
        items: list[_SyncItem] = []
        queued_asset_ids: set[str] = set()
        skipped = 0
        cursor = ""
        append = False
        while True:
            if self._cancelled():
                return items, skipped
            page = client.refresh_scope_page(scope="subscribed", cursor=cursor, append=append)
            for entry in page.entries:
                asset_id = str(entry.record.componentId)
                if asset_id in queued_asset_ids:
                    continue
                cached_entry = client.remote_entry(asset_id)
                if cached_entry is None or not component_entry_is_installed(cached_entry):
                    items.append(_SyncItem(asset_kind="component", asset_id=asset_id))
                    queued_asset_ids.add(asset_id)
                else:
                    skipped += 1
            cursor = "" if page.nextCursor is None else str(page.nextCursor)
            if not cursor:
                break
            append = True
        return items, skipped

    def _cancelled(self) -> bool:
        with self._state_lock:
            return self._cancel_requested

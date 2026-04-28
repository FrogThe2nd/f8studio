from __future__ import annotations

from collections.abc import Callable

import pytest

from f8pystudio.assets.ui import catalog_remote_refresh_worker_mixin as worker_mixin_module
from f8pystudio.assets.ui.catalog_browser_state import CatalogRefreshLogFields, DEFAULT_REFRESH_ERROR_TITLE
from f8pystudio.assets.ui.catalog_remote_refresh_worker_mixin import CatalogRemoteRefreshWorkerMixin


class _FakeSignal:
    def __init__(self) -> None:
        self.connected_callbacks: list[Callable[..., None]] = []

    def connect(self, callback: Callable[..., None]) -> None:
        self.connected_callbacks.append(callback)


class _FakeWorker:
    created_workers: list[_FakeWorker] = []

    def __init__(self, *, request_id: int, task: Callable[[], object]) -> None:
        self.request_id = request_id
        self.task = task
        self.succeeded = _FakeSignal()
        self.failed = _FakeSignal()
        self.started = False
        _FakeWorker.created_workers.append(self)

    def start(self) -> None:
        self.started = True


class _FakeRemoteRefresh(CatalogRemoteRefreshWorkerMixin[str]):
    def __init__(self) -> None:
        self._active_remote_refresh_request_id = 10
        self._active_remote_refresh_error_title = ""
        self._active_remote_refresh_log_label = ""
        self._active_remote_refresh_started_at = 0.0
        self._is_loading_remote_scope = False
        self._remote_refresh_worker = None
        self.sync_calls = 0
        self.logged_fields: list[CatalogRefreshLogFields] = []
        self.succeeded_calls: list[dict[str, object]] = []
        self.failed_calls: list[dict[str, object]] = []

    def _catalog_refresh_task(self, *, requests: list[str]) -> Callable[[], object]:
        return lambda: tuple(requests)

    def _catalog_refresh_log_fields(
        self,
        *,
        request_id: int,
        log_label: str,
        requests: list[str],
    ) -> CatalogRefreshLogFields:
        return CatalogRefreshLogFields(
            request_id=request_id,
            log_label=log_label,
            scopes=list(requests),
            base_node_type="",
        )

    def _log_catalog_refresh_queued(self, *, log_fields: CatalogRefreshLogFields) -> None:
        self.logged_fields.append(log_fields)

    def _sync_auth_controls_ui(self) -> None:
        self.sync_calls += 1

    def _on_catalog_refresh_succeeded(
        self,
        *,
        finished_request_id: int,
        result: object,
        elapsed_seconds: float,
        error_title: str,
        log_label: str,
        started_at: float,
    ) -> None:
        self.succeeded_calls.append(
            {
                "finished_request_id": finished_request_id,
                "result": result,
                "elapsed_seconds": elapsed_seconds,
                "error_title": error_title,
                "log_label": log_label,
                "started_at": started_at,
            }
        )

    def _on_catalog_refresh_failed(
        self,
        *,
        finished_request_id: int,
        exc: object,
        elapsed_seconds: float,
        error_title: str,
        log_label: str,
    ) -> None:
        self.failed_calls.append(
            {
                "finished_request_id": finished_request_id,
                "exc": exc,
                "elapsed_seconds": elapsed_seconds,
                "error_title": error_title,
                "log_label": log_label,
            }
        )


@pytest.fixture(autouse=True)
def _patch_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeWorker.created_workers = []
    monkeypatch.setattr(worker_mixin_module, "BackgroundCallWorker", _FakeWorker)


def test_start_catalog_refresh_creates_worker_and_records_active_metadata() -> None:
    catalog = _FakeRemoteRefresh()

    catalog._start_catalog_refresh(
        requests=["community"],
        error_title=DEFAULT_REFRESH_ERROR_TITLE,
        log_label="manual_refresh",
    )

    assert catalog._active_remote_refresh_request_id == 11
    assert catalog._active_remote_refresh_error_title == DEFAULT_REFRESH_ERROR_TITLE
    assert catalog._active_remote_refresh_log_label == "manual_refresh"
    assert catalog._active_remote_refresh_started_at > 0.0
    assert catalog._is_loading_remote_scope
    assert catalog.sync_calls == 1
    assert len(catalog.logged_fields) == 1
    assert catalog.logged_fields[0].scopes == ["community"]
    assert len(_FakeWorker.created_workers) == 1
    assert _FakeWorker.created_workers[0].request_id == 11
    assert _FakeWorker.created_workers[0].started
    assert len(_FakeWorker.created_workers[0].succeeded.connected_callbacks) == 1
    assert len(_FakeWorker.created_workers[0].failed.connected_callbacks) == 1


def test_catalog_refresh_signal_handlers_forward_active_metadata() -> None:
    catalog = _FakeRemoteRefresh()
    catalog._active_remote_refresh_error_title = DEFAULT_REFRESH_ERROR_TITLE
    catalog._active_remote_refresh_log_label = "scope_refresh"
    catalog._active_remote_refresh_started_at = 123.0

    catalog._handle_catalog_refresh_succeeded(12, {"ok": True}, 0.5)
    catalog._handle_catalog_refresh_failed(13, ValueError("bad"), 0.7)

    assert catalog.succeeded_calls == [
        {
            "finished_request_id": 12,
            "result": {"ok": True},
            "elapsed_seconds": 0.5,
            "error_title": DEFAULT_REFRESH_ERROR_TITLE,
            "log_label": "scope_refresh",
            "started_at": 123.0,
        }
    ]
    assert len(catalog.failed_calls) == 1
    assert catalog.failed_calls[0]["finished_request_id"] == 13
    assert isinstance(catalog.failed_calls[0]["exc"], ValueError)
    assert catalog.failed_calls[0]["elapsed_seconds"] == 0.7
    assert catalog.failed_calls[0]["error_title"] == DEFAULT_REFRESH_ERROR_TITLE
    assert catalog.failed_calls[0]["log_label"] == "scope_refresh"

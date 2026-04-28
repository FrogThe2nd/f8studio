from __future__ import annotations

from f8pystudio.assets.ui.catalog_browser_state import DEFAULT_REFRESH_ERROR_TITLE
from f8pystudio.assets.ui.catalog_refresh_queue_mixin import CatalogRefreshQueueMixin


class _FakeRefreshQueue(CatalogRefreshQueueMixin[str]):
    def __init__(self) -> None:
        self._remote_refresh_worker: object | None = None
        self._queued_remote_refresh = None
        self.started_requests: list[list[str]] = []
        self.started_error_titles: list[str] = []
        self.started_log_labels: list[str] = []

    def _start_catalog_refresh(
        self,
        *,
        requests: list[str],
        error_title: str,
        log_label: str,
    ) -> None:
        self.started_requests.append(requests)
        self.started_error_titles.append(error_title)
        self.started_log_labels.append(log_label)


def test_request_catalog_refresh_starts_immediately_when_idle() -> None:
    queue = _FakeRefreshQueue()

    queue._request_catalog_refresh(
        requests=["community"],
        error_title=DEFAULT_REFRESH_ERROR_TITLE,
        log_label="manual_refresh",
    )

    assert queue._queued_remote_refresh is None
    assert queue.started_requests == [["community"]]
    assert queue.started_error_titles == [DEFAULT_REFRESH_ERROR_TITLE]
    assert queue.started_log_labels == ["manual_refresh"]


def test_request_catalog_refresh_queues_when_worker_is_active() -> None:
    queue = _FakeRefreshQueue()
    queue._remote_refresh_worker = object()

    queue._request_catalog_refresh(
        requests=["mine"],
        error_title=DEFAULT_REFRESH_ERROR_TITLE,
        log_label="scope_refresh",
    )

    assert queue.started_requests == []
    assert queue._queued_remote_refresh is not None
    assert queue._queued_remote_refresh.requests == ["mine"]
    assert queue._queued_remote_refresh.error_title == DEFAULT_REFRESH_ERROR_TITLE
    assert queue._queued_remote_refresh.log_label == "scope_refresh"


def test_start_queued_catalog_refresh_if_any_drains_pending_refresh() -> None:
    queue = _FakeRefreshQueue()
    queue._remote_refresh_worker = object()
    queue._request_catalog_refresh(
        requests=["mine"],
        error_title=DEFAULT_REFRESH_ERROR_TITLE,
        log_label="scope_refresh",
    )
    queue._remote_refresh_worker = None

    queue._start_queued_catalog_refresh_if_any()

    assert queue._queued_remote_refresh is None
    assert queue.started_requests == [["mine"]]
    assert queue.started_error_titles == [DEFAULT_REFRESH_ERROR_TITLE]
    assert queue.started_log_labels == ["scope_refresh"]


def test_start_queued_catalog_refresh_if_any_is_noop_without_pending_refresh() -> None:
    queue = _FakeRefreshQueue()

    queue._start_queued_catalog_refresh_if_any()

    assert queue.started_requests == []

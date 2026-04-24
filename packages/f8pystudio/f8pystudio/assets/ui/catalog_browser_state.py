from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from .background_tasks import BackgroundCallWorker

RemoteScopeRefreshRequestT = TypeVar("RemoteScopeRefreshRequestT")
CatalogEntryT = TypeVar("CatalogEntryT")


class CatalogRefreshLogRequest(Protocol):
    scope: str

REMOTE_SCOPE_MINE = "mine"
REMOTE_SCOPE_COMMUNITY = "community"
DEFAULT_CATALOG_FILTER = "all"
DEFAULT_REFRESH_ERROR_TITLE = "Refresh failed"
INITIAL_REFRESH_ERROR_TITLE = "Initial refresh failed"
UNKNOWN_REFRESH_LOG_LABEL = "unknown"
REFRESH_LOG_LABEL_ACCOUNT_CHANGE = "account_change"
REFRESH_LOG_LABEL_INITIAL_OPEN = "initial_open"
REFRESH_LOG_LABEL_MANUAL_REFRESH = "manual_refresh"
REFRESH_LOG_LABEL_SCOPE_REFRESH = "scope_refresh"
REFRESH_LOG_LABEL_SIGNED_OUT_REFRESH = "signed_out_refresh"
REMOTE_CATALOG_SCOPES = (REMOTE_SCOPE_MINE, REMOTE_SCOPE_COMMUNITY)
FILTER_LINKED = "linked"
FILTER_UNPUBLISHED = "unpublished"
FILTER_PRIVATE = "private"
FILTER_SHARED = "shared"
FILTER_SUBSCRIBED = "subscribed"
FILTER_NOT_SUBSCRIBED = "not_subscribed"


@dataclass(slots=True)
class CatalogBrowserState(Generic[RemoteScopeRefreshRequestT, CatalogEntryT]):
    initial_remote_refresh_done: bool
    initial_remote_refresh_scheduled: bool
    initial_cached_render_started_at: float
    tab_queries: dict[int, str]
    tab_filters: dict[int, str]
    remote_next_cursor_by_scope: dict[str, str | None]
    remote_loaded_query_by_scope: dict[str, str]
    is_loading_remote_scope: bool
    active_remote_refresh_request_id: int
    remote_refresh_worker: BackgroundCallWorker | None
    active_remote_refresh_error_title: str
    active_remote_refresh_log_label: str
    active_remote_refresh_started_at: float
    catalog_local_entries_snapshot: list[CatalogEntryT]
    catalog_remote_entries_snapshot: list[CatalogEntryT]
    last_list_render_signature: tuple[object, ...] | None


@dataclass(frozen=True, slots=True)
class CatalogRefreshPageRequest:
    cursor: str
    append: bool


@dataclass(frozen=True, slots=True)
class QueuedCatalogRefresh(Generic[RemoteScopeRefreshRequestT]):
    requests: list[RemoteScopeRefreshRequestT]
    error_title: str
    log_label: str


@dataclass(frozen=True, slots=True)
class CatalogRefreshLogFields:
    request_id: int
    log_label: str
    scopes: list[str]
    base_node_type: str


def create_catalog_browser_state(
    *,
    tabs: tuple[int, ...],
) -> CatalogBrowserState[RemoteScopeRefreshRequestT, CatalogEntryT]:
    return CatalogBrowserState(
        initial_remote_refresh_done=False,
        initial_remote_refresh_scheduled=False,
        initial_cached_render_started_at=0.0,
        tab_queries={tab: "" for tab in tabs},
        tab_filters={tab: DEFAULT_CATALOG_FILTER for tab in tabs},
        remote_next_cursor_by_scope={scope: None for scope in REMOTE_CATALOG_SCOPES},
        remote_loaded_query_by_scope={scope: "" for scope in REMOTE_CATALOG_SCOPES},
        is_loading_remote_scope=False,
        active_remote_refresh_request_id=0,
        remote_refresh_worker=None,
        active_remote_refresh_error_title=DEFAULT_REFRESH_ERROR_TITLE,
        active_remote_refresh_log_label=UNKNOWN_REFRESH_LOG_LABEL,
        active_remote_refresh_started_at=0.0,
        catalog_local_entries_snapshot=[],
        catalog_remote_entries_snapshot=[],
        last_list_render_signature=None,
    )


def current_catalog_query(*, tab_queries: dict[int, str], current_tab: int) -> str:
    return str(tab_queries.get(current_tab, "")).strip()


def current_catalog_filter(*, tab_filters: dict[int, str], current_tab: int) -> str:
    return str(tab_filters.get(current_tab, DEFAULT_CATALOG_FILTER)).strip() or DEFAULT_CATALOG_FILTER


def remote_scope_for_catalog_tab(
    *,
    current_tab: int,
    mine_tab: int,
    community_tab: int,
) -> str | None:
    if current_tab == community_tab:
        return REMOTE_SCOPE_COMMUNITY
    if current_tab == mine_tab:
        return REMOTE_SCOPE_MINE
    return None


def catalog_filter_options_for_tab(
    *,
    current_tab: int,
    drafts_tab: int,
    mine_tab: int,
    community_tab: int,
    installed_mine_label: str,
) -> list[tuple[str, str]]:
    if current_tab == drafts_tab:
        return [
            ("All Drafts", DEFAULT_CATALOG_FILTER),
            ("Linked Drafts", FILTER_LINKED),
            ("Unpublished", FILTER_UNPUBLISHED),
        ]
    if current_tab == mine_tab:
        return [
            ("All Mine", DEFAULT_CATALOG_FILTER),
            ("Private Cloud", FILTER_PRIVATE),
            ("Shared Public", FILTER_SHARED),
        ]
    if current_tab == community_tab:
        return [
            ("All Community", DEFAULT_CATALOG_FILTER),
            ("Subscribed", FILTER_SUBSCRIBED),
            ("Not Subscribed", FILTER_NOT_SUBSCRIBED),
        ]
    return [
        ("All Installed", DEFAULT_CATALOG_FILTER),
        (installed_mine_label, REMOTE_SCOPE_MINE),
        ("Subscribed", FILTER_SUBSCRIBED),
    ]


def catalog_refresh_page_request(
    *,
    reset: bool,
    remote_scope: str,
    remote_next_cursor_by_scope: dict[str, str | None],
) -> CatalogRefreshPageRequest | None:
    if reset:
        return CatalogRefreshPageRequest(cursor="", append=False)
    cursor = str(remote_next_cursor_by_scope.get(remote_scope) or "")
    if not cursor:
        return None
    return CatalogRefreshPageRequest(cursor=cursor, append=True)


def catalog_refresh_log_fields(
    *,
    request_id: int,
    log_label: str,
    requests: Sequence[CatalogRefreshLogRequest],
    base_node_type: str = "",
) -> CatalogRefreshLogFields:
    return CatalogRefreshLogFields(
        request_id=request_id,
        log_label=log_label,
        scopes=[request.scope for request in requests],
        base_node_type=str(base_node_type or ""),
    )

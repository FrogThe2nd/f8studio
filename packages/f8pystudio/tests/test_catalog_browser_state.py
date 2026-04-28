from __future__ import annotations

from f8pystudio.assets.ui.catalog_browser_state import (
    DEFAULT_CATALOG_FILTER,
    DEFAULT_REFRESH_ERROR_TITLE,
    FILTER_LINKED,
    FILTER_NOT_SUBSCRIBED,
    FILTER_PRIVATE,
    FILTER_SHARED,
    FILTER_SUBSCRIBED,
    FILTER_UNPUBLISHED,
    INITIAL_REFRESH_ERROR_TITLE,
    QueuedCatalogRefresh,
    REMOTE_SCOPE_COMMUNITY,
    REMOTE_SCOPE_MINE,
    catalog_filter_options_for_tab,
    catalog_refresh_log_fields,
    catalog_refresh_page_request,
    create_catalog_browser_state,
    current_catalog_filter,
    current_catalog_query,
    remote_scope_for_catalog_tab,
)


class _RefreshLogRequest:
    def __init__(self, *, scope: str) -> None:
        self.scope = scope


def test_create_catalog_browser_state_initializes_independent_mutable_defaults() -> None:
    first_state = create_catalog_browser_state(tabs=(0, 1, 2, 3))
    second_state = create_catalog_browser_state(tabs=(0, 1, 2, 3))

    first_state.tab_queries[1] = "mine"
    first_state.catalog_local_entries_snapshot.append(object())

    assert second_state.tab_queries[1] == ""
    assert second_state.catalog_local_entries_snapshot == []


def test_current_catalog_query_and_filter_normalize_values() -> None:
    assert current_catalog_query(tab_queries={2: "  hero  "}, current_tab=2) == "hero"
    assert current_catalog_query(tab_queries={}, current_tab=2) == ""
    assert current_catalog_filter(tab_filters={2: "  subscribed  "}, current_tab=2) == FILTER_SUBSCRIBED
    assert current_catalog_filter(tab_filters={2: "  "}, current_tab=2) == DEFAULT_CATALOG_FILTER
    assert current_catalog_filter(tab_filters={}, current_tab=2) == DEFAULT_CATALOG_FILTER


def test_refresh_error_titles_are_centralized() -> None:
    assert DEFAULT_REFRESH_ERROR_TITLE == "Refresh failed"
    assert INITIAL_REFRESH_ERROR_TITLE == "Initial refresh failed"


def test_queued_catalog_refresh_groups_request_metadata() -> None:
    queued_refresh = QueuedCatalogRefresh(
        requests=["request"],
        error_title=DEFAULT_REFRESH_ERROR_TITLE,
        log_label="label",
    )

    assert queued_refresh.requests == ["request"]
    assert queued_refresh.error_title == DEFAULT_REFRESH_ERROR_TITLE
    assert queued_refresh.log_label == "label"


def test_remote_scope_for_catalog_tab_maps_only_remote_tabs() -> None:
    assert remote_scope_for_catalog_tab(current_tab=1, mine_tab=1, community_tab=2) == REMOTE_SCOPE_MINE
    assert remote_scope_for_catalog_tab(current_tab=2, mine_tab=1, community_tab=2) == REMOTE_SCOPE_COMMUNITY
    assert remote_scope_for_catalog_tab(current_tab=0, mine_tab=1, community_tab=2) is None
    assert remote_scope_for_catalog_tab(current_tab=3, mine_tab=1, community_tab=2) is None


def test_catalog_refresh_page_request_handles_reset_and_pagination() -> None:
    reset_request = catalog_refresh_page_request(
        reset=True,
        remote_scope=REMOTE_SCOPE_COMMUNITY,
        remote_next_cursor_by_scope={REMOTE_SCOPE_COMMUNITY: "next"},
    )
    next_page_request = catalog_refresh_page_request(
        reset=False,
        remote_scope=REMOTE_SCOPE_COMMUNITY,
        remote_next_cursor_by_scope={REMOTE_SCOPE_COMMUNITY: "next"},
    )
    exhausted_request = catalog_refresh_page_request(
        reset=False,
        remote_scope=REMOTE_SCOPE_COMMUNITY,
        remote_next_cursor_by_scope={REMOTE_SCOPE_COMMUNITY: ""},
    )

    assert reset_request is not None
    assert reset_request.cursor == ""
    assert not reset_request.append
    assert next_page_request is not None
    assert next_page_request.cursor == "next"
    assert next_page_request.append
    assert exhausted_request is None


def test_catalog_refresh_log_fields_extracts_stable_context() -> None:
    fields = catalog_refresh_log_fields(
        request_id=7,
        log_label="manual_refresh",
        requests=[_RefreshLogRequest(scope=REMOTE_SCOPE_COMMUNITY), _RefreshLogRequest(scope=REMOTE_SCOPE_MINE)],
        base_node_type="viz.track",
    )

    assert fields.request_id == 7
    assert fields.log_label == "manual_refresh"
    assert fields.scopes == [REMOTE_SCOPE_COMMUNITY, REMOTE_SCOPE_MINE]
    assert fields.base_node_type == "viz.track"


def test_catalog_filter_options_are_shared_across_asset_catalogs() -> None:
    drafts = catalog_filter_options_for_tab(
        current_tab=0,
        drafts_tab=0,
        mine_tab=1,
        community_tab=2,
        installed_mine_label="My Components",
    )
    mine = catalog_filter_options_for_tab(
        current_tab=1,
        drafts_tab=0,
        mine_tab=1,
        community_tab=2,
        installed_mine_label="My Components",
    )
    community = catalog_filter_options_for_tab(
        current_tab=2,
        drafts_tab=0,
        mine_tab=1,
        community_tab=2,
        installed_mine_label="My Components",
    )
    installed = catalog_filter_options_for_tab(
        current_tab=3,
        drafts_tab=0,
        mine_tab=1,
        community_tab=2,
        installed_mine_label="My Components",
    )

    assert drafts == [
        ("All Drafts", DEFAULT_CATALOG_FILTER),
        ("Linked Drafts", FILTER_LINKED),
        ("Unpublished", FILTER_UNPUBLISHED),
    ]
    assert mine == [
        ("All Mine", DEFAULT_CATALOG_FILTER),
        ("Private Cloud", FILTER_PRIVATE),
        ("Shared Public", FILTER_SHARED),
    ]
    assert community == [
        ("All Community", DEFAULT_CATALOG_FILTER),
        ("Subscribed", FILTER_SUBSCRIBED),
        ("Not Subscribed", FILTER_NOT_SUBSCRIBED),
    ]
    assert installed == [
        ("All Installed", DEFAULT_CATALOG_FILTER),
        ("My Components", REMOTE_SCOPE_MINE),
        ("Subscribed", FILTER_SUBSCRIBED),
    ]

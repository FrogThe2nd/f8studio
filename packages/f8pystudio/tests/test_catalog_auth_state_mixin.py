from __future__ import annotations

from dataclasses import dataclass

from f8pystudio.assets.ui.catalog_auth_state_mixin import CatalogAuthStateMixin
from f8pystudio.assets.ui.catalog_browser_state import (
    DEFAULT_REFRESH_ERROR_TITLE,
    REFRESH_LOG_LABEL_ACCOUNT_CHANGE,
    REFRESH_LOG_LABEL_SIGNED_OUT_REFRESH,
)


@dataclass(frozen=True)
class _FakeUser:
    name: str
    email: str


class _FakeSyncClient:
    def __init__(self) -> None:
        self.user: _FakeUser | None = None
        self.access_token = ""

    def current_user(self) -> _FakeUser | None:
        return self.user

    def current_access_token(self) -> str:
        return self.access_token


class _FakeAuthCatalog(CatalogAuthStateMixin[str, str]):
    def __init__(self) -> None:
        self._sync_client = _FakeSyncClient()
        self._account_button = object()
        self._btn_refresh = object()
        self._is_loading_remote_scope = False
        self.signed_out_applied = False
        self.sanitized = False
        self.rebuilt_ids: list[str] = []
        self.refresh_requests: list[list[str]] = []
        self.refresh_error_titles: list[str] = []
        self.refresh_log_labels: list[str] = []

    def _apply_signed_out_auth_state(self) -> None:
        self.signed_out_applied = True

    def _sanitize_remote_entries_for_signed_out_user(self) -> None:
        self.sanitized = True

    def _selected_asset_id_for_auth_refresh(self) -> str:
        return "selected"

    def _rebuild_browser_after_auth_state_changed_for_id(self, selected_asset_id: str) -> None:
        self.rebuilt_ids.append(selected_asset_id)

    def _signed_out_catalog_refresh_requests(self) -> list[str]:
        return ["community"]

    def _full_catalog_refresh_requests(self) -> list[str]:
        return ["community", "mine"]

    def _request_catalog_refresh(
        self,
        *,
        requests: list[str],
        error_title: str,
        log_label: str,
    ) -> None:
        self.refresh_requests.append(requests)
        self.refresh_error_titles.append(error_title)
        self.refresh_log_labels.append(log_label)


def test_account_button_text_uses_current_user_identity() -> None:
    catalog = _FakeAuthCatalog()
    assert catalog._account_button_text() == "Accounts"

    catalog._sync_client.user = _FakeUser(name="Ada", email="ada@example.com")
    assert catalog._account_button_text() == "Ada"

    catalog._sync_client.user = _FakeUser(name="", email="ada@example.com")
    assert catalog._account_button_text() == "ada@example.com"


def test_account_state_changed_signed_out_applies_public_refresh() -> None:
    catalog = _FakeAuthCatalog()

    catalog._on_account_state_changed()

    assert catalog.signed_out_applied
    assert not catalog.sanitized
    assert catalog.rebuilt_ids == ["selected"]
    assert catalog.refresh_requests == [["community"]]
    assert catalog.refresh_error_titles == [DEFAULT_REFRESH_ERROR_TITLE]
    assert catalog.refresh_log_labels == [REFRESH_LOG_LABEL_SIGNED_OUT_REFRESH]


def test_account_state_changed_signed_in_refreshes_full_catalog() -> None:
    catalog = _FakeAuthCatalog()
    catalog._sync_client.user = _FakeUser(name="Ada", email="ada@example.com")
    catalog._sync_client.access_token = "token"

    catalog._on_account_state_changed()

    assert not catalog.signed_out_applied
    assert catalog.sanitized
    assert catalog.rebuilt_ids == ["selected"]
    assert catalog.refresh_requests == [["community", "mine"]]
    assert catalog.refresh_error_titles == [DEFAULT_REFRESH_ERROR_TITLE]
    assert catalog.refresh_log_labels == [REFRESH_LOG_LABEL_ACCOUNT_CHANGE]


def test_ensure_logged_in_returns_true_for_existing_session() -> None:
    catalog = _FakeAuthCatalog()
    catalog._sync_client.user = _FakeUser(name="Ada", email="ada@example.com")
    catalog._sync_client.access_token = "token"

    assert catalog._ensure_logged_in()

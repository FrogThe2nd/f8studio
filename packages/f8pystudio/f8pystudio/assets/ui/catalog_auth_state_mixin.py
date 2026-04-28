from __future__ import annotations

from typing import TYPE_CHECKING, Generic, Protocol, TypeVar

from qtpy import QtCore

from ...ui.support.ui_icons import StudioIcon, icon_for
from .asset_cloud_account_menu import build_asset_account_menu, prompt_asset_cloud_sign_in
from .catalog_browser_state import DEFAULT_REFRESH_ERROR_TITLE, REFRESH_LOG_LABEL_ACCOUNT_CHANGE, REFRESH_LOG_LABEL_SIGNED_OUT_REFRESH
from .catalog_hosts import _ComponentCatalogDialogHost, _VariantCatalogDialogHost

RemoteScopeRefreshRequestT = TypeVar("RemoteScopeRefreshRequestT")
SelectedAssetIdT = TypeVar("SelectedAssetIdT")


class CatalogAuthUser(Protocol):
    name: str
    email: str


class CatalogAuthSyncClient(Protocol):
    def current_user(self) -> CatalogAuthUser | None: ...

    def current_access_token(self) -> str: ...


class CatalogAuthHooks(Protocol[RemoteScopeRefreshRequestT, SelectedAssetIdT]):
    _account_button: object
    _btn_refresh: object
    _is_loading_remote_scope: bool
    _sync_client: CatalogAuthSyncClient

    def _apply_signed_out_auth_state(self) -> None: ...

    def _sanitize_remote_entries_for_signed_out_user(self) -> None: ...

    def _selected_asset_id_for_auth_refresh(self) -> SelectedAssetIdT: ...

    def _rebuild_browser_after_auth_state_changed_for_id(self, selected_asset_id: SelectedAssetIdT) -> None: ...

    def _signed_out_catalog_refresh_requests(self) -> list[RemoteScopeRefreshRequestT]: ...

    def _full_catalog_refresh_requests(self) -> list[RemoteScopeRefreshRequestT]: ...

    def _request_catalog_refresh(
        self,
        *,
        requests: list[RemoteScopeRefreshRequestT],
        error_title: str,
        log_label: str,
    ) -> None: ...


if TYPE_CHECKING:
    _CatalogAuthStateMixinBase = (
        CatalogAuthHooks[RemoteScopeRefreshRequestT, SelectedAssetIdT]
        | _ComponentCatalogDialogHost
        | _VariantCatalogDialogHost
    )
else:
    _CatalogAuthStateMixinBase = object


class CatalogAuthStateMixin(Generic[RemoteScopeRefreshRequestT, SelectedAssetIdT], _CatalogAuthStateMixinBase):
    def _sync_auth_controls_ui(self) -> None:
        logged_in = self._current_catalog_user() is not None
        self._account_button.setIcon(icon_for(self._account_button, StudioIcon.USER if logged_in else StudioIcon.USER_OFF))
        self._btn_refresh.setEnabled(not self._is_loading_remote_scope)

    def _on_accounts_clicked(self) -> None:
        menu = build_asset_account_menu(
            parent=self,
            sync_client=self._sync_client,
            on_changed=self._on_account_state_changed,
        )
        menu.exec(self._account_button.mapToGlobal(self._account_menu_anchor_point()))

    def _account_button_text(self) -> str:
        user = self._current_catalog_user()
        if user is None:
            return "Accounts"
        return str(user.name or user.email or "Accounts")

    def _on_login_clicked(self) -> None:
        if prompt_asset_cloud_sign_in(parent=self, sync_client=self._sync_client):
            self._on_account_state_changed()

    def _on_logout_clicked(self) -> None:
        self._on_account_state_changed()

    def _on_account_state_changed(self) -> None:
        selected_asset_id = self._selected_asset_id_for_auth_refresh()
        if not self._has_catalog_auth_session():
            self._apply_signed_out_auth_state()
            self._rebuild_browser_after_auth_state_changed_for_id(selected_asset_id)
            self._request_catalog_refresh(
                requests=self._signed_out_catalog_refresh_requests(),
                error_title=DEFAULT_REFRESH_ERROR_TITLE,
                log_label=REFRESH_LOG_LABEL_SIGNED_OUT_REFRESH,
            )
            return
        self._sanitize_remote_entries_for_signed_out_user()
        self._rebuild_browser_after_auth_state_changed_for_id(selected_asset_id)
        self._request_catalog_refresh(
            requests=self._full_catalog_refresh_requests(),
            error_title=DEFAULT_REFRESH_ERROR_TITLE,
            log_label=REFRESH_LOG_LABEL_ACCOUNT_CHANGE,
        )

    def _ensure_logged_in(self) -> bool:
        if self._has_catalog_auth_session():
            return True
        self._on_login_clicked()
        return self._has_catalog_auth_session()

    def _account_menu_anchor_point(self) -> QtCore.QPoint:
        return self._account_button.rect().bottomLeft()

    def _current_catalog_user(self) -> CatalogAuthUser | None:
        return self._sync_client.current_user()

    def _has_catalog_auth_session(self) -> bool:
        return self._current_catalog_user() is not None and bool(self._sync_client.current_access_token())

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from f8pystudio.assets.ui.component_catalog_browser import ComponentCatalogBrowserMixin
from f8pystudio.assets.ui import component_catalog_browser, variant_catalog_browser
from f8pystudio.assets.ui.variant_catalog_browser import VariantCatalogBrowserMixin
from f8pystudio.ui.support.ui_icons import StudioIcon


@dataclass
class _FakeSyncClient:
    user: object | None
    access_token: str = ""

    def current_user(self) -> object | None:
        return self.user

    def current_access_token(self) -> str:
        return self.access_token


@dataclass
class _FakeButton:
    icon_value: object | None = None
    enabled: bool | None = None

    def setIcon(self, icon: object) -> None:  # noqa: N802 - Qt-style method name
        self.icon_value = icon

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802 - Qt-style method name
        self.enabled = bool(enabled)


@dataclass
class _FakeCatalog:
    _sync_client: _FakeSyncClient
    _account_button: _FakeButton
    _btn_refresh: _FakeButton
    _is_loading_remote_scope: bool = False


def _fake_icon_for(_button: object, token: StudioIcon) -> StudioIcon:
    return token


def test_component_catalog_account_icon_uses_saved_user_without_access_token(monkeypatch: Any) -> None:
    monkeypatch.setattr(component_catalog_browser, "icon_for", _fake_icon_for)
    catalog = _FakeCatalog(
        _sync_client=_FakeSyncClient(user=object(), access_token=""),
        _account_button=_FakeButton(),
        _btn_refresh=_FakeButton(),
    )

    ComponentCatalogBrowserMixin._sync_auth_controls_ui(catalog)  # type: ignore[arg-type]

    assert catalog._account_button.icon_value is StudioIcon.USER
    assert catalog._btn_refresh.enabled is True


def test_variant_catalog_account_icon_uses_saved_user_without_access_token(monkeypatch: Any) -> None:
    monkeypatch.setattr(variant_catalog_browser, "icon_for", _fake_icon_for)
    catalog = _FakeCatalog(
        _sync_client=_FakeSyncClient(user=object(), access_token=""),
        _account_button=_FakeButton(),
        _btn_refresh=_FakeButton(),
    )

    VariantCatalogBrowserMixin._sync_auth_controls_ui(catalog)  # type: ignore[arg-type]

    assert catalog._account_button.icon_value is StudioIcon.USER
    assert catalog._btn_refresh.enabled is True


def test_asset_catalog_account_icon_shows_signed_out_when_no_user(monkeypatch: Any) -> None:
    monkeypatch.setattr(component_catalog_browser, "icon_for", _fake_icon_for)
    catalog = _FakeCatalog(
        _sync_client=_FakeSyncClient(user=None, access_token="token"),
        _account_button=_FakeButton(),
        _btn_refresh=_FakeButton(),
        _is_loading_remote_scope=True,
    )

    ComponentCatalogBrowserMixin._sync_auth_controls_ui(catalog)  # type: ignore[arg-type]

    assert catalog._account_button.icon_value is StudioIcon.USER_OFF
    assert catalog._btn_refresh.enabled is False

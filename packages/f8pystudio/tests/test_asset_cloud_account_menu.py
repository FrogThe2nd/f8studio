from __future__ import annotations

from dataclasses import dataclass

from qtpy import QtWidgets

from f8pystudio.assets.ui import asset_cloud_account_menu


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


@dataclass
class _FakeUser:
    userId: str
    name: str | None
    displayName: str
    email: str | None


@dataclass
class _FakeSession:
    accountId: str
    baseUrl: str
    sessionCookie: str
    user: _FakeUser
    lastUsedAt: str


class _FakeSyncClient:
    def __init__(self) -> None:
        self._base_url = "https://assetcloud.feel8.fun"
        self._remembered_email = "alice@example.com"
        self._current_user: _FakeUser | None = None
        self._saved_sessions: list[_FakeSession] = []
        self.login_calls: list[tuple[str, str, str, bool]] = []
        self.logout_calls = 0

    def base_url(self) -> str:
        return self._base_url

    @classmethod
    def default_base_url(cls) -> str:
        return "https://assetcloud.feel8.fun"

    def set_base_url(self, base_url: str) -> None:
        self._base_url = str(base_url)

    def remembered_email(self) -> str:
        return self._remembered_email

    def current_user(self) -> _FakeUser | None:
        return self._current_user

    def login(self, *, base_url: str, email: str, password: str, remember: bool) -> object:
        self.login_calls.append((str(base_url), str(email), str(password), bool(remember)))
        self._current_user = _FakeUser(userId="u1", name="Alice", displayName="Alice", email=email)
        return object()

    def saved_sessions(self) -> list[_FakeSession]:
        return list(self._saved_sessions)

    def current_account_id(self) -> str:
        return ""

    def switch_account(self, account_id: str) -> object:
        raise AssertionError(f"unexpected switch_account call: {account_id}")

    def clear_saved_session(self, account_id: str) -> None:
        raise AssertionError(f"unexpected clear_saved_session call: {account_id}")

    def clear_all_saved_sessions(self) -> None:
        raise AssertionError("unexpected clear_all_saved_sessions call")

    def logout(self) -> None:
        self.logout_calls += 1
        self._current_user = None


class _AcceptedSignInDialog:
    def __init__(self, *, parent: QtWidgets.QWidget | None, base_url: str, email: str) -> None:
        del parent
        self.base_url = str(base_url)
        self.email = str(email)

    def exec(self) -> int:
        return QtWidgets.QDialog.Accepted

    def values(self) -> tuple[str, str]:
        return "alice@example.com", "secret"


def test_prompt_asset_cloud_sign_in_shows_welcome_message(monkeypatch) -> None:
    _ensure_app()
    parent = QtWidgets.QWidget()
    client = _FakeSyncClient()
    info_messages: list[tuple[str, str]] = []

    def _fail_warning(_parent: QtWidgets.QWidget | None, title: str, message: str) -> None:
        raise AssertionError(f"unexpected warning: {title} {message}")

    monkeypatch.setattr(asset_cloud_account_menu, "AssetCloudSignInDialog", _AcceptedSignInDialog)
    monkeypatch.setattr(
        asset_cloud_account_menu,
        "show_info",
        lambda _parent, title, message: info_messages.append((str(title), str(message))),
    )
    monkeypatch.setattr(asset_cloud_account_menu, "show_warning", _fail_warning)

    result = asset_cloud_account_menu.prompt_asset_cloud_sign_in(parent=parent, sync_client=client)

    assert result is True
    assert client.login_calls == [("https://assetcloud.feel8.fun", "alice@example.com", "secret", True)]
    assert info_messages == [("Asset Cloud", "Hi Alice, welcome back!")]


def test_prompt_asset_cloud_sign_in_preserves_loopback_base_url(monkeypatch) -> None:
    _ensure_app()
    parent = QtWidgets.QWidget()
    client = _FakeSyncClient()
    client.set_base_url("http://127.0.0.1:8787")
    info_messages: list[tuple[str, str]] = []

    def _fail_warning(_parent: QtWidgets.QWidget | None, title: str, message: str) -> None:
        raise AssertionError(f"unexpected warning: {title} {message}")

    monkeypatch.setattr(asset_cloud_account_menu, "AssetCloudSignInDialog", _AcceptedSignInDialog)
    monkeypatch.setattr(
        asset_cloud_account_menu,
        "show_info",
        lambda _parent, title, message: info_messages.append((str(title), str(message))),
    )
    monkeypatch.setattr(asset_cloud_account_menu, "show_warning", _fail_warning)

    result = asset_cloud_account_menu.prompt_asset_cloud_sign_in(parent=parent, sync_client=client)

    assert result is True
    assert client.login_calls == [("http://127.0.0.1:8787", "alice@example.com", "secret", True)]
    assert info_messages == [("Asset Cloud", "Hi Alice, welcome back!")]


def test_logout_current_account_shows_goodbye_message(monkeypatch) -> None:
    _ensure_app()
    parent = QtWidgets.QWidget()
    client = _FakeSyncClient()
    client._current_user = _FakeUser(userId="u1", name="Alice", displayName="Alice", email="alice@example.com")
    info_messages: list[tuple[str, str]] = []
    on_changed_calls: list[str] = []

    def _fail_warning(_parent: QtWidgets.QWidget | None, title: str, message: str) -> None:
        raise AssertionError(f"unexpected warning: {title} {message}")

    monkeypatch.setattr(
        asset_cloud_account_menu,
        "show_info",
        lambda _parent, title, message: info_messages.append((str(title), str(message))),
    )
    monkeypatch.setattr(asset_cloud_account_menu, "show_warning", _fail_warning)

    asset_cloud_account_menu._logout_current_account(
        parent=parent,
        sync_client=client,
        on_changed=lambda: on_changed_calls.append("changed"),
    )

    assert client.logout_calls == 1
    assert client.current_user() is None
    assert on_changed_calls == ["changed"]
    assert info_messages == [("Asset Cloud", "Goodbye, Alice.")]


def test_build_asset_account_menu_formats_saved_session_time_locally(monkeypatch) -> None:
    _ensure_app()
    parent = QtWidgets.QWidget()
    client = _FakeSyncClient()
    client._current_user = _FakeUser(userId="u1", name="Alice", displayName="Alice", email="alice@example.com")
    client._saved_sessions = [
        _FakeSession(
            accountId="acct-1",
            baseUrl="https://assetcloud.feel8.fun",
            sessionCookie="cookie",
            user=_FakeUser(userId="u1", name="Alice", displayName="Alice", email="alice@example.com"),
            lastUsedAt="2026-04-15T13:45:00+00:00",
        )
    ]

    monkeypatch.setattr(
        asset_cloud_account_menu,
        "format_timestamp_for_local_display",
        lambda value: f"LOCAL<{value}>",
    )

    menu = asset_cloud_account_menu.build_asset_account_menu(
        parent=parent,
        sync_client=client,
        on_changed=None,
    )

    switch_menu_action = next(action for action in menu.actions() if action.text() == "Switch Account")
    clear_menu_action = next(action for action in menu.actions() if action.text() == "Clear Saved Session")
    switch_menu = switch_menu_action.menu()
    clear_menu = clear_menu_action.menu()

    expected = "Alice (alice@example.com) | Last used: LOCAL<2026-04-15T13:45:00+00:00>"
    assert switch_menu is not None
    assert clear_menu is not None
    assert switch_menu.actions()[0].text() == expected
    assert clear_menu.actions()[0].text() == expected

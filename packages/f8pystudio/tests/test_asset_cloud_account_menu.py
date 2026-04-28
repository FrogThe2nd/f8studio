from __future__ import annotations

from dataclasses import dataclass

from qtpy import QtWidgets

from f8pystudio.assets.common import AssetCloudBrowserAuthCallback, AssetCloudBrowserAuthSession
from f8pystudio.assets.ui import asset_cloud_account_menu


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


@dataclass
class _FakeUser:
    userId: str
    name: str
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
        self._current_account_id = ""
        self._saved_sessions: list[_FakeSession] = []
        self.exchange_calls: list[tuple[str, str, str, str, str, bool]] = []
        self.switch_calls: list[str] = []
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
        del base_url, email, password, remember
        raise AssertionError("unexpected legacy login call")

    def exchange_browser_auth_code(
        self,
        *,
        base_url: str,
        client_id: str,
        code: str,
        redirect_uri: str,
        code_verifier: str,
        remember: bool,
    ) -> object:
        self.exchange_calls.append(
            (str(base_url), str(client_id), str(code), str(redirect_uri), str(code_verifier), bool(remember))
        )
        self._current_user = _FakeUser(userId="u1", name="Alice", email="alice@example.com")
        return object()

    def saved_sessions(self) -> list[_FakeSession]:
        return list(self._saved_sessions)

    def current_account_id(self) -> str:
        return self._current_account_id

    def switch_account(self, account_id: str) -> object:
        normalized_account_id = str(account_id)
        self.switch_calls.append(normalized_account_id)
        for session in self._saved_sessions:
            if session.accountId != normalized_account_id:
                continue
            self._current_account_id = normalized_account_id
            self._current_user = session.user
            self._base_url = session.baseUrl
            return object()
        raise AssertionError(f"unknown switch_account call: {account_id}")

    def clear_saved_session(self, account_id: str) -> None:
        raise AssertionError(f"unexpected clear_saved_session call: {account_id}")

    def clear_all_saved_sessions(self) -> None:
        raise AssertionError("unexpected clear_all_saved_sessions call")

    def logout(self) -> None:
        self.logout_calls += 1
        self._current_user = None


def _browser_auth_session(*, base_url: str) -> AssetCloudBrowserAuthSession:
    normalized_base_url = str(base_url).rstrip("/")
    return AssetCloudBrowserAuthSession(
        base_url=normalized_base_url,
        client_id="pystudio",
        state="state-1",
        code_verifier="verifier-1",
        code_challenge="challenge-1",
        redirect_uri="http://127.0.0.1:43001/callback",
        callback_port=43001,
        authorize_url=f"{normalized_base_url}/v1/auth/desktop/authorize?state=state-1",
    )


def test_prompt_asset_cloud_sign_in_shows_welcome_message(monkeypatch) -> None:
    _ensure_app()
    parent = QtWidgets.QWidget()
    client = _FakeSyncClient()
    info_messages: list[tuple[str, str]] = []
    opened_urls: list[str] = []

    def _fail_warning(_parent: QtWidgets.QWidget | None, title: str, message: str) -> None:
        raise AssertionError(f"unexpected warning: {title} {message}")

    monkeypatch.setattr(
        asset_cloud_account_menu,
        "create_browser_auth_session_for_port",
        lambda *, base_url, callback_port, client_id="pystudio": _browser_auth_session(base_url=base_url),
    )
    monkeypatch.setattr(asset_cloud_account_menu, "_open_system_browser", lambda url: opened_urls.append(str(url)))
    monkeypatch.setattr(
        asset_cloud_account_menu,
        "_wait_for_browser_sign_in_callback",
        lambda **_kwargs: AssetCloudBrowserAuthCallback(code="code-1", state="state-1"),
    )
    monkeypatch.setattr(
        asset_cloud_account_menu,
        "show_info",
        lambda _parent, title, message: info_messages.append((str(title), str(message))),
    )
    monkeypatch.setattr(asset_cloud_account_menu, "show_warning", _fail_warning)

    result = asset_cloud_account_menu.prompt_asset_cloud_sign_in(parent=parent, sync_client=client)

    assert result is True
    assert opened_urls == ["https://assetcloud.feel8.fun/v1/auth/desktop/authorize?state=state-1"]
    assert client.exchange_calls == [
        (
            "https://assetcloud.feel8.fun",
            "pystudio",
            "code-1",
            "http://127.0.0.1:43001/callback",
            "verifier-1",
            True,
        )
    ]
    assert info_messages == [("Asset Cloud", "Hi Alice, welcome back!")]


def test_prompt_asset_cloud_sign_in_preserves_loopback_base_url(monkeypatch) -> None:
    _ensure_app()
    parent = QtWidgets.QWidget()
    client = _FakeSyncClient()
    client.set_base_url("http://127.0.0.1:8787")
    info_messages: list[tuple[str, str]] = []
    opened_urls: list[str] = []

    def _fail_warning(_parent: QtWidgets.QWidget | None, title: str, message: str) -> None:
        raise AssertionError(f"unexpected warning: {title} {message}")

    monkeypatch.setattr(
        asset_cloud_account_menu,
        "create_browser_auth_session_for_port",
        lambda *, base_url, callback_port, client_id="pystudio": _browser_auth_session(base_url=base_url),
    )
    monkeypatch.setattr(asset_cloud_account_menu, "_open_system_browser", lambda url: opened_urls.append(str(url)))
    monkeypatch.setattr(
        asset_cloud_account_menu,
        "_wait_for_browser_sign_in_callback",
        lambda **_kwargs: AssetCloudBrowserAuthCallback(code="code-1", state="state-1"),
    )
    monkeypatch.setattr(
        asset_cloud_account_menu,
        "show_info",
        lambda _parent, title, message: info_messages.append((str(title), str(message))),
    )
    monkeypatch.setattr(asset_cloud_account_menu, "show_warning", _fail_warning)

    result = asset_cloud_account_menu.prompt_asset_cloud_sign_in(parent=parent, sync_client=client)

    assert result is True
    assert opened_urls == ["http://127.0.0.1:8787/v1/auth/desktop/authorize?state=state-1"]
    assert client.exchange_calls == [
        (
            "http://127.0.0.1:8787",
            "pystudio",
            "code-1",
            "http://127.0.0.1:43001/callback",
            "verifier-1",
            True,
        )
    ]
    assert info_messages == [("Asset Cloud", "Hi Alice, welcome back!")]


def test_switch_saved_account_shows_welcome_message(monkeypatch) -> None:
    _ensure_app()
    parent = QtWidgets.QWidget()
    client = _FakeSyncClient()
    client._saved_sessions = [
        _FakeSession(
            accountId="acct-2",
            baseUrl="https://assetcloud.feel8.fun",
            sessionCookie="cookie-2",
            user=_FakeUser(userId="u2", name="Bob", email="bob@example.com"),
            lastUsedAt="2026-04-21T12:00:00+00:00",
        )
    ]
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

    asset_cloud_account_menu._switch_saved_account(
        parent=parent,
        sync_client=client,
        account_id="acct-2",
        on_changed=lambda: on_changed_calls.append("changed"),
    )

    assert client.switch_calls == ["acct-2"]
    assert client.current_user() == _FakeUser(userId="u2", name="Bob", email="bob@example.com")
    assert on_changed_calls == ["changed"]
    assert info_messages == [("Asset Cloud", "Hi Bob, welcome back!")]


def test_switch_saved_account_does_not_repeat_welcome_for_current_account(monkeypatch) -> None:
    _ensure_app()
    parent = QtWidgets.QWidget()
    client = _FakeSyncClient()
    client._current_account_id = "acct-2"
    client._current_user = _FakeUser(userId="u2", name="Bob", email="bob@example.com")
    client._saved_sessions = [
        _FakeSession(
            accountId="acct-2",
            baseUrl="https://assetcloud.feel8.fun",
            sessionCookie="cookie-2",
            user=_FakeUser(userId="u2", name="Bob", email="bob@example.com"),
            lastUsedAt="2026-04-21T12:00:00+00:00",
        )
    ]
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

    asset_cloud_account_menu._switch_saved_account(
        parent=parent,
        sync_client=client,
        account_id="acct-2",
        on_changed=lambda: on_changed_calls.append("changed"),
    )

    assert client.switch_calls == ["acct-2"]
    assert on_changed_calls == ["changed"]
    assert info_messages == []


def test_logout_current_account_shows_goodbye_message(monkeypatch) -> None:
    _ensure_app()
    parent = QtWidgets.QWidget()
    client = _FakeSyncClient()
    client._current_user = _FakeUser(userId="u1", name="Alice", email="alice@example.com")
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


def test_build_asset_account_menu_disables_current_account_switch_action(monkeypatch) -> None:
    _ensure_app()
    parent = QtWidgets.QWidget()
    client = _FakeSyncClient()
    client._current_account_id = "acct-1"
    client._current_user = _FakeUser(userId="u1", name="Alice", email="alice@example.com")
    client._saved_sessions = [
        _FakeSession(
            accountId="acct-2",
            baseUrl="https://assetcloud.feel8.fun",
            sessionCookie="cookie-2",
            user=_FakeUser(userId="u2", name="Bob", email="bob@example.com"),
            lastUsedAt="2026-04-15T13:50:00+00:00",
        ),
        _FakeSession(
            accountId="acct-1",
            baseUrl="https://assetcloud.feel8.fun",
            sessionCookie="cookie-1",
            user=_FakeUser(userId="u1", name="Alice", email="alice@example.com"),
            lastUsedAt="2026-04-15T13:45:00+00:00",
        ),
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
    switch_menu = switch_menu_action.menu()

    assert switch_menu is not None
    current_action = switch_menu.actions()[0]
    other_action = switch_menu.actions()[1]
    assert current_action.text().startswith("Alice ")
    assert other_action.text().startswith("Bob ")
    assert current_action.isChecked() is True
    assert current_action.isEnabled() is False
    assert other_action.isEnabled() is True


def test_build_asset_account_menu_formats_saved_session_time_locally(monkeypatch) -> None:
    _ensure_app()
    parent = QtWidgets.QWidget()
    client = _FakeSyncClient()
    client._current_user = _FakeUser(userId="u1", name="Alice", email="alice@example.com")
    client._saved_sessions = [
        _FakeSession(
            accountId="acct-1",
            baseUrl="https://assetcloud.feel8.fun",
            sessionCookie="cookie",
            user=_FakeUser(userId="u1", name="Alice", email="alice@example.com"),
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


def test_build_asset_account_menu_hides_sessions_from_other_base_urls(monkeypatch) -> None:
    _ensure_app()
    parent = QtWidgets.QWidget()
    client = _FakeSyncClient()
    client._base_url = "http://127.0.0.1:8787"
    client._saved_sessions = [
        _FakeSession(
            accountId="acct-local",
            baseUrl="http://127.0.0.1:8787",
            sessionCookie="cookie-local",
            user=_FakeUser(userId="u1", name="Local User", email="local@example.com"),
            lastUsedAt="2026-04-15T13:45:00+00:00",
        ),
        _FakeSession(
            accountId="acct-prod",
            baseUrl="https://assetcloud.feel8.fun",
            sessionCookie="cookie-prod",
            user=_FakeUser(userId="u2", name="Prod User", email="prod@example.com"),
            lastUsedAt="2026-04-15T13:50:00+00:00",
        ),
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

    assert switch_menu is not None
    assert clear_menu is not None
    assert [action.text() for action in switch_menu.actions()] == [
        "Local User (local@example.com) | Last used: LOCAL<2026-04-15T13:45:00+00:00>"
    ]
    assert [action.text() for action in clear_menu.actions() if not action.isSeparator()] == [
        "Local User (local@example.com) | Last used: LOCAL<2026-04-15T13:45:00+00:00>",
        "Clear All Saved Sessions",
    ]

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
    displayName: str
    username: str | None


class _FakeSyncClient:
    def __init__(self) -> None:
        self._base_url = "https://assetcloud.feel8.fun"
        self._remembered_username = "alice"
        self._current_user: _FakeUser | None = None
        self.login_calls: list[tuple[str, str, str, bool]] = []
        self.logout_calls = 0

    def base_url(self) -> str:
        return self._base_url

    @classmethod
    def default_base_url(cls) -> str:
        return "https://assetcloud.feel8.fun"

    def set_base_url(self, base_url: str) -> None:
        self._base_url = str(base_url)

    def remembered_username(self) -> str:
        return self._remembered_username

    def current_user(self) -> _FakeUser | None:
        return self._current_user

    def login(self, *, base_url: str, username: str, password: str, remember: bool) -> object:
        self.login_calls.append((str(base_url), str(username), str(password), bool(remember)))
        self._current_user = _FakeUser(userId="u1", displayName="Alice", username=username)
        return object()

    def saved_sessions(self) -> list[object]:
        return []

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
    def __init__(self, *, parent: QtWidgets.QWidget | None, base_url: str, username: str) -> None:
        del parent
        self.base_url = str(base_url)
        self.username = str(username)

    def exec(self) -> int:
        return QtWidgets.QDialog.Accepted

    def values(self) -> tuple[str, str]:
        return "alice", "secret"


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
    assert client.login_calls == [("https://assetcloud.feel8.fun", "alice", "secret", True)]
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
    assert client.login_calls == [("http://127.0.0.1:8787", "alice", "secret", True)]
    assert info_messages == [("Asset Cloud", "Hi Alice, welcome back!")]


def test_logout_current_account_shows_goodbye_message(monkeypatch) -> None:
    _ensure_app()
    parent = QtWidgets.QWidget()
    client = _FakeSyncClient()
    client._current_user = _FakeUser(userId="u1", displayName="Alice", username="alice")
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

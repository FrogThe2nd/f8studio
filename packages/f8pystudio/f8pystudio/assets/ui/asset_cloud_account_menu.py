from __future__ import annotations

from collections.abc import Callable
import time
from typing import Protocol
import webbrowser

from qtpy import QtCore, QtWidgets

from ..common import (
    AssetCloudBrowserAuthCallback,
    AssetCloudBrowserAuthError,
    AssetCloudBrowserCallbackServer,
    build_browser_callback_redirect_url,
    create_browser_auth_session,
    format_timestamp_for_local_display,
)
from ...ui.support.ui_icons import StudioIcon, icon_for
from ...ui.support.ui_notifications import show_info, show_warning


class AssetCloudUserLike(Protocol):
    userId: str
    name: str
    email: str | None


class AssetCloudSessionLike(Protocol):
    accountId: str
    baseUrl: str
    sessionCookie: str
    user: AssetCloudUserLike
    lastUsedAt: str


class AssetCloudSyncClient(Protocol):
    def base_url(self) -> str: ...

    @classmethod
    def default_base_url(cls) -> str: ...

    def set_base_url(self, base_url: str) -> None: ...

    def remembered_email(self) -> str: ...

    def current_user(self) -> AssetCloudUserLike | None: ...

    def login(self, *, base_url: str, email: str, password: str, remember: bool) -> object: ...

    def exchange_browser_auth_code(
        self,
        *,
        base_url: str,
        client_id: str,
        code: str,
        redirect_uri: str,
        code_verifier: str,
        remember: bool,
    ) -> object: ...

    def saved_sessions(self) -> list[AssetCloudSessionLike]: ...

    def current_account_id(self) -> str: ...

    def switch_account(self, account_id: str) -> object: ...

    def clear_saved_session(self, account_id: str) -> None: ...

    def clear_all_saved_sessions(self) -> None: ...

    def logout(self) -> None: ...


class AssetCloudSignInDialog(QtWidgets.QDialog):
    def __init__(self, *, parent: QtWidgets.QWidget | None, base_url: str, email: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Asset Cloud Sign In")
        self.resize(460, 180)
        self._email = QtWidgets.QLineEdit(email, self)
        self._email.setClearButtonEnabled(True)
        self._password = QtWidgets.QLineEdit(self)
        self._password.setEchoMode(QtWidgets.QLineEdit.Password)
        self._password.setClearButtonEnabled(True)

        endpoint_label = QtWidgets.QLabel(f"Cloud URL: {base_url}", self)
        endpoint_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        endpoint_label.setWordWrap(True)

        form = QtWidgets.QFormLayout()
        form.addRow("Email", self._email)
        form.addRow("Password", self._password)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self._on_accept_clicked)  # type: ignore[attr-defined]
        buttons.rejected.connect(self.reject)  # type: ignore[attr-defined]

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(endpoint_label)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _on_accept_clicked(self) -> None:
        email = str(self._email.text() or "").strip()
        password = str(self._password.text() or "")
        if not email:
            show_warning(self, "Login failed", "Email cannot be empty.")
            return
        if not password:
            show_warning(self, "Login failed", "Password cannot be empty.")
            return
        self.accept()

    def values(self) -> tuple[str, str]:
        return str(self._email.text() or "").strip(), str(self._password.text() or "")


def prompt_asset_cloud_sign_in(*, parent: QtWidgets.QWidget, sync_client: AssetCloudSyncClient) -> bool:
    base_url = sync_client.base_url()
    sync_client.set_base_url(base_url)
    auth_session = create_browser_auth_session(base_url=base_url)
    callback_server = AssetCloudBrowserCallbackServer(
        callback_port=auth_session.callback_port,
        success_redirect_url=build_browser_callback_redirect_url(base_url=base_url, success=True),
        error_redirect_url=build_browser_callback_redirect_url(base_url=base_url, success=False),
    )
    try:
        callback_server.start()
        _open_system_browser(auth_session.authorize_url)
        callback = _wait_for_browser_sign_in_callback(
            parent=parent,
            callback_server=callback_server,
            timeout_seconds=180.0,
        )
        if callback.state != auth_session.state:
            raise AssetCloudBrowserAuthError("Browser sign-in returned an invalid state token.")
        sync_client.exchange_browser_auth_code(
            base_url=base_url,
            client_id=auth_session.client_id,
            code=callback.code,
            redirect_uri=auth_session.redirect_uri,
            code_verifier=auth_session.code_verifier,
            remember=True,
        )
    except Exception as exc:
        show_warning(parent, "Login failed", f"{exc}\n\nCloud URL: {base_url}")
        return False
    finally:
        callback_server.stop()
    current_user = sync_client.current_user()
    user_name = _user_greeting_name(current_user)
    if user_name:
        show_info(parent, "Asset Cloud", f"Hi {user_name}, welcome back!")
    return True


def _open_system_browser(url: str) -> None:
    try:
        opened = webbrowser.open(str(url), new=1, autoraise=True)
    except Exception as exc:
        raise AssetCloudBrowserAuthError(f"Could not open the system browser.\n\nOpen this URL manually:\n{url}") from exc
    if not opened:
        raise AssetCloudBrowserAuthError(f"Could not open the system browser.\n\nOpen this URL manually:\n{url}")


def _wait_for_browser_sign_in_callback(
    *,
    parent: QtWidgets.QWidget,
    callback_server: AssetCloudBrowserCallbackServer,
    timeout_seconds: float,
) -> AssetCloudBrowserAuthCallback:
    deadline = time.monotonic() + float(timeout_seconds)
    event_loop = QtCore.QEventLoop(parent)
    poll_timer = QtCore.QTimer(parent)
    poll_timer.setInterval(50)
    callback_result: AssetCloudBrowserAuthCallback | None = None
    callback_error: Exception | None = None

    def _poll() -> None:
        nonlocal callback_result, callback_error
        callback = callback_server.poll_callback()
        if callback is not None:
            if callback.error:
                callback_error = AssetCloudBrowserAuthError(str(callback.error_description or callback.error))
            else:
                callback_result = callback
            event_loop.quit()
            return
        if time.monotonic() >= deadline:
            callback_error = AssetCloudBrowserAuthError("Timed out waiting for browser sign-in to finish.")
            event_loop.quit()

    poll_timer.timeout.connect(_poll)  # type: ignore[attr-defined]
    poll_timer.start()
    try:
        event_loop.exec()
    finally:
        poll_timer.stop()
    if callback_error is not None:
        raise callback_error
    if callback_result is None:
        raise AssetCloudBrowserAuthError("Browser sign-in ended without a callback result.")
    return callback_result


def build_asset_account_menu(
    *,
    parent: QtWidgets.QWidget,
    sync_client: AssetCloudSyncClient,
    on_changed: Callable[[], None] | None = None,
) -> QtWidgets.QMenu:
    menu = QtWidgets.QMenu(parent)
    current_user = sync_client.current_user()
    current_text = "Cloud: signed out"
    if current_user is not None:
        current_text = f"Cloud: {_user_greeting_name(current_user)} @ {sync_client.base_url()}"
    header = menu.addAction(current_text)
    header.setEnabled(False)
    menu.addSeparator()

    sign_in_action = menu.addAction(icon_for(parent, StudioIcon.USER_PLUS), "Sign In...")
    sign_in_action.triggered.connect(  # type: ignore[attr-defined]
        _menu_callback(
            parent=parent,
            action=lambda: prompt_asset_cloud_sign_in(parent=parent, sync_client=sync_client),
            on_changed=on_changed,
        )
    )

    saved_sessions = sync_client.saved_sessions()
    if saved_sessions:
        switch_menu = menu.addMenu("Switch Account")
        current_account_id = str(sync_client.current_account_id() or "").strip()
        ordered_sessions = _sorted_saved_sessions_for_switch_menu(
            saved_sessions=saved_sessions,
            current_account_id=current_account_id,
        )
        for session in ordered_sessions:
            label = _saved_session_label(session)
            action = switch_menu.addAction(label)
            is_current_account = str(session.accountId) == current_account_id
            action.setCheckable(True)
            action.setChecked(is_current_account)
            action.setEnabled(not is_current_account)
            action.triggered.connect(  # type: ignore[attr-defined]
                _menu_callback(
                    parent=parent,
                    action=lambda account_id=session.accountId: _switch_saved_account(
                        parent=parent,
                        sync_client=sync_client,
                        account_id=account_id,
                        on_changed=on_changed,
                    ),
                    on_changed=None,
                )
            )

        clear_menu = menu.addMenu("Clear Saved Session")
        for session in saved_sessions:
            label = _saved_session_label(session)
            action = clear_menu.addAction(label)
            action.triggered.connect(  # type: ignore[attr-defined]
                _menu_callback(
                    parent=parent,
                    action=lambda account_id=session.accountId: sync_client.clear_saved_session(account_id),
                    on_changed=on_changed,
                )
            )
        clear_all_action = clear_menu.addAction("Clear All Saved Sessions")
        clear_all_action.triggered.connect(  # type: ignore[attr-defined]
            _menu_callback(parent=parent, action=sync_client.clear_all_saved_sessions, on_changed=on_changed)
        )

    menu.addSeparator()
    logout_action = menu.addAction(icon_for(parent, StudioIcon.USER_X), "Logout Current Account")
    logout_action.setEnabled(current_user is not None)
    logout_action.triggered.connect(  # type: ignore[attr-defined]
        _menu_callback(
            parent=parent,
            action=lambda: _logout_current_account(parent=parent, sync_client=sync_client, on_changed=on_changed),
            on_changed=None,
        )
    )
    return menu


def _menu_callback(
    *,
    parent: QtWidgets.QWidget,
    action: Callable[[], object],
    on_changed: Callable[[], None] | None,
) -> Callable[[], None]:
    def _callback() -> None:
        _ = _run_and_notify(parent=parent, action=action, on_changed=on_changed)

    return _callback


def _run_and_notify(*, parent: QtWidgets.QWidget, action: Callable[[], object], on_changed: Callable[[], None] | None) -> bool:
    try:
        action()
    except Exception as exc:
        show_warning(parent, "Asset Cloud", str(exc))
        return False
    if on_changed is not None:
        on_changed()
    return True


def _switch_saved_account(
    *,
    parent: QtWidgets.QWidget,
    sync_client: AssetCloudSyncClient,
    account_id: str,
    on_changed: Callable[[], None] | None,
) -> None:
    previous_account_id = str(sync_client.current_account_id() or "").strip()
    normalized_account_id = str(account_id or "").strip()
    succeeded = _run_and_notify(
        parent=parent,
        action=lambda: sync_client.switch_account(normalized_account_id),
        on_changed=on_changed,
    )
    if not succeeded:
        return
    if normalized_account_id and normalized_account_id == previous_account_id:
        return
    current_user = sync_client.current_user()
    user_name = _user_greeting_name(current_user)
    if user_name:
        show_info(parent, "Asset Cloud", f"Hi {user_name}, welcome back!")


def _logout_current_account(
    *,
    parent: QtWidgets.QWidget,
    sync_client: AssetCloudSyncClient,
    on_changed: Callable[[], None] | None,
) -> None:
    current_user = sync_client.current_user()
    user_name = _user_greeting_name(current_user)
    succeeded = _run_and_notify(parent=parent, action=sync_client.logout, on_changed=on_changed)
    if succeeded and user_name:
        show_info(parent, "Asset Cloud", f"Goodbye, {user_name}.")


def _user_greeting_name(user: AssetCloudUserLike | None) -> str:
    if user is None:
        return ""
    name = str(user.name).strip()
    if name:
        return name
    email = str(user.email or "").strip()
    if email:
        return email
    return str(user.userId or "").strip()


def _sorted_saved_sessions_for_switch_menu(
    *,
    saved_sessions: list[AssetCloudSessionLike],
    current_account_id: str,
) -> list[AssetCloudSessionLike]:
    normalized_current_account_id = str(current_account_id or "").strip()
    current_sessions: list[AssetCloudSessionLike] = []
    other_sessions: list[AssetCloudSessionLike] = []
    for session in saved_sessions:
        if str(session.accountId) == normalized_current_account_id:
            current_sessions.append(session)
        else:
            other_sessions.append(session)
    return current_sessions + other_sessions


def _saved_session_label(session: AssetCloudSessionLike) -> str:
    label = _user_greeting_name(session.user)
    identity = f"{label} ({session.user.email or session.user.userId})"
    last_used = format_timestamp_for_local_display(session.lastUsedAt)
    if not last_used:
        return identity
    return f"{identity} | Last used: {last_used}"


__all__ = [
    "AssetCloudSignInDialog",
    "AssetCloudSessionLike",
    "AssetCloudSyncClient",
    "AssetCloudUserLike",
    "build_asset_account_menu",
    "prompt_asset_cloud_sign_in",
]

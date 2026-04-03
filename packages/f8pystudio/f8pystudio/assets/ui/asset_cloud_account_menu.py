from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from urllib.parse import urlparse

from qtpy import QtCore, QtWidgets

from ...ui_icons import StudioIcon, icon_for
from ...ui_notifications import show_warning


class AssetCloudUserLike(Protocol):
    userId: str
    displayName: str
    username: str | None


class AssetCloudSessionLike(Protocol):
    accountId: str
    baseUrl: str
    refreshToken: str
    user: AssetCloudUserLike
    lastUsedAt: str


class AssetCloudSyncClient(Protocol):
    def base_url(self) -> str: ...

    @classmethod
    def default_base_url(cls) -> str: ...

    def set_base_url(self, base_url: str) -> None: ...

    def remembered_username(self) -> str: ...

    def current_user(self) -> AssetCloudUserLike | None: ...

    def login(self, *, base_url: str, username: str, password: str, remember: bool) -> object: ...

    def saved_sessions(self) -> list[AssetCloudSessionLike]: ...

    def current_account_id(self) -> str: ...

    def switch_account(self, account_id: str) -> object: ...

    def clear_saved_session(self, account_id: str) -> None: ...

    def clear_all_saved_sessions(self) -> None: ...

    def logout(self) -> None: ...


class AssetCloudSignInDialog(QtWidgets.QDialog):
    def __init__(self, *, parent: QtWidgets.QWidget | None, base_url: str, username: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Asset Cloud Sign In")
        self.resize(460, 180)
        self._username = QtWidgets.QLineEdit(username, self)
        self._username.setClearButtonEnabled(True)
        self._password = QtWidgets.QLineEdit(self)
        self._password.setEchoMode(QtWidgets.QLineEdit.Password)
        self._password.setClearButtonEnabled(True)

        endpoint_label = QtWidgets.QLabel(f"Cloud URL: {base_url}", self)
        endpoint_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        endpoint_label.setWordWrap(True)

        form = QtWidgets.QFormLayout()
        form.addRow("Username", self._username)
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
        username = str(self._username.text() or "").strip()
        password = str(self._password.text() or "")
        if not username:
            show_warning(self, "Login failed", "Username cannot be empty.")
            return
        if not password:
            show_warning(self, "Login failed", "Password cannot be empty.")
            return
        self.accept()

    def values(self) -> tuple[str, str]:
        return str(self._username.text() or "").strip(), str(self._password.text() or "")


def prompt_asset_cloud_sign_in(*, parent: QtWidgets.QWidget, sync_client: AssetCloudSyncClient) -> bool:
    base_url = _preferred_base_url(sync_client)
    sync_client.set_base_url(base_url)
    dialog = AssetCloudSignInDialog(parent=parent, base_url=base_url, username=sync_client.remembered_username())
    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return False
    username, password = dialog.values()
    try:
        sync_client.login(base_url=base_url, username=username, password=password, remember=True)
    except Exception as exc:
        show_warning(parent, "Login failed", f"{exc}\n\nCloud URL: {base_url}")
        return False
    return True


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
        current_text = f"Cloud: {current_user.displayName} @ {sync_client.base_url()}"
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
        for session in saved_sessions:
            label = f"{session.user.displayName} ({session.user.username or session.user.userId})"
            action = switch_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(session.accountId == sync_client.current_account_id())
            action.triggered.connect(  # type: ignore[attr-defined]
                _menu_callback(
                    parent=parent,
                    action=lambda account_id=session.accountId: sync_client.switch_account(account_id),
                    on_changed=on_changed,
                )
            )

        clear_menu = menu.addMenu("Clear Saved Session")
        for session in saved_sessions:
            label = f"{session.user.displayName} ({session.user.username or session.user.userId})"
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
        _menu_callback(parent=parent, action=sync_client.logout, on_changed=on_changed)
    )
    return menu


def _menu_callback(
    *,
    parent: QtWidgets.QWidget,
    action: Callable[[], object],
    on_changed: Callable[[], None] | None,
) -> Callable[[], None]:
    def _callback() -> None:
        _run_and_notify(parent=parent, action=action, on_changed=on_changed)

    return _callback


def _run_and_notify(*, parent: QtWidgets.QWidget, action: Callable[[], object], on_changed: Callable[[], None] | None) -> None:
    try:
        action()
    except Exception as exc:
        show_warning(parent, "Asset Cloud", str(exc))
        return
    if on_changed is not None:
        on_changed()


def _preferred_base_url(sync_client: AssetCloudSyncClient) -> str:
    configured_base_url = sync_client.base_url()
    parsed = urlparse(configured_base_url)
    hostname = str(parsed.hostname or "").strip().lower()
    if hostname in {"127.0.0.1", "localhost", "0.0.0.0"}:
        return sync_client.default_base_url()
    return configured_base_url


__all__ = [
    "AssetCloudSignInDialog",
    "AssetCloudSessionLike",
    "AssetCloudSyncClient",
    "AssetCloudUserLike",
    "build_asset_account_menu",
    "prompt_asset_cloud_sign_in",
]

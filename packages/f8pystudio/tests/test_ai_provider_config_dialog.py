from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from qtpy import QtWidgets  # type: ignore[import-not-found]

from f8pystudio.ai_assist.store import AiProviderStore
from f8pystudio.ui.dialogs.ai_provider_config_dialog import AiProviderConfigDialog


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def _make_store(tmp_path: Path) -> AiProviderStore:
    store_path = tmp_path / "ai_providers.json"
    with patch.object(AiProviderStore, "_resolve_storage_path", return_value=store_path):
        return AiProviderStore()


def test_provider_dialog_exposes_api_mode_and_supported_paths(tmp_path: Path) -> None:
    _ensure_app()
    store = _make_store(tmp_path)
    dialog = AiProviderConfigDialog(store)
    try:
        openai = store.provider_by_id("openai")
        assert openai is not None
        dialog._load_form(openai)

        assert dialog._current_api_mode() == "responses"
        assert dialog._chat_path_edit.findText("/responses") >= 0
        assert dialog._chat_path_edit.findText("/v1/responses") >= 0
        assert dialog._chat_path_edit.findText("/chat/completions") >= 0
        assert dialog._chat_path_edit.findText("/v1/chat/completions") >= 0
        assert dialog._chat_path_edit.findText("/v1/completions") == -1
        assert dialog._api_mode_combo.isEnabled()
    finally:
        dialog.close()


def test_provider_dialog_disables_api_mode_for_anthropic(tmp_path: Path) -> None:
    _ensure_app()
    store = _make_store(tmp_path)
    dialog = AiProviderConfigDialog(store)
    try:
        anthropic = store.provider_by_id("anthropic")
        assert anthropic is not None
        dialog._load_form(anthropic)

        assert dialog._current_api_mode() == "chat_completions"
        assert not dialog._api_mode_combo.isEnabled()
    finally:
        dialog.close()


def test_provider_dialog_orders_footer_buttons(tmp_path: Path) -> None:
    _ensure_app()
    store = _make_store(tmp_path)
    dialog = AiProviderConfigDialog(store)
    try:
        button_texts: list[str] = []
        for index in range(dialog._footer_buttons_layout.count()):
            item = dialog._footer_buttons_layout.itemAt(index)
            if item is None:
                continue
            widget = item.widget()
            if isinstance(widget, QtWidgets.QPushButton):
                button_texts.append(widget.text())

        assert button_texts == ["Save", "Close"]
    finally:
        dialog.close()

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from qtpy import QtWidgets  # type: ignore[import-not-found]

from f8pystudio.agents.registry import ProviderConfig
from f8pystudio.agents.store import AiProviderStore
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


def test_provider_dialog_exposes_api_mode_and_manual_model_controls(tmp_path: Path) -> None:
    _ensure_app()
    store = _make_store(tmp_path)
    dialog = AiProviderConfigDialog(store)
    try:
        openai = store.provider_by_id("openai")
        assert openai is not None
        dialog._load_form(openai)

        assert dialog._current_api_mode() == "responses"
        assert dialog._api_mode_combo.isEnabled()
        assert dialog._fetch_btn.text() == "Load Defaults"
        assert dialog._model_id_edit.placeholderText() == "model ID"
        assert dialog._add_model_btn.text() == "Add Model"
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


def test_provider_dialog_adds_manual_model_id(tmp_path: Path) -> None:
    _ensure_app()
    store = _make_store(tmp_path)
    store.save_provider(ProviderConfig(provider_id="custom_lab", display_name="Custom Lab", protocol="custom"))
    dialog = AiProviderConfigDialog(store)
    try:
        custom = store.provider_by_id("custom_lab")
        assert custom is not None
        dialog._current_provider_id = "custom_lab"
        dialog._load_form(custom)
        dialog._model_id_edit.setText("local-test-model")

        dialog._on_add_model_id()

        updated = store.provider_by_id("custom_lab")
        assert updated is not None
        assert [model.model_id for model in updated.cached_models] == ["local-test-model"]
        assert dialog._model_table.rowCount() == 1
        assert dialog._fetch_status.text() == "Model added."
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

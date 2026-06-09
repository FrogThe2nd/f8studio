from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from qtpy import QtWidgets  # type: ignore[import-not-found]

from f8pystudio.agents.registry import ModelCapabilities, ModelInfo, ProviderConfig
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


def test_provider_dialog_exposes_inference_service_and_manual_model_controls(tmp_path: Path) -> None:
    _ensure_app()
    store = _make_store(tmp_path)
    dialog = AiProviderConfigDialog(store)
    try:
        openai = store.provider_by_id("openai")
        assert openai is not None
        dialog._load_form(openai)

        assert dialog._current_inference_service() == "openai_responses"
        assert "service chat history: yes" in dialog._service_status.text()
        assert not dialog._api_version_edit.isEnabled()
        assert dialog._discover_btn.text() == "Discover Endpoint"
        assert dialog._discover_btn.isEnabled()
        assert dialog._model_id_edit.placeholderText() == "model ID"
        assert dialog._add_model_btn.text() == "Add Model"
        assert not dialog._chat_model_combo.isEditable()
    finally:
        dialog.close()


def test_provider_dialog_disables_endpoint_discovery_for_anthropic(tmp_path: Path) -> None:
    _ensure_app()
    store = _make_store(tmp_path)
    dialog = AiProviderConfigDialog(store)
    try:
        anthropic = store.provider_by_id("anthropic")
        assert anthropic is not None
        dialog._load_form(anthropic)

        assert dialog._current_inference_service() == "anthropic_claude"
        assert "service chat history: no" in dialog._service_status.text()
        assert not dialog._api_version_edit.isEnabled()
        assert not dialog._discover_btn.isEnabled()
    finally:
        dialog.close()


def test_provider_dialog_enables_custom_endpoint_discovery(tmp_path: Path) -> None:
    _ensure_app()
    store = _make_store(tmp_path)
    store.save_provider(
        ProviderConfig(
            provider_id="custom_lab",
            display_name="Custom Lab",
            inference_service="custom_chat_client",
        )
    )
    dialog = AiProviderConfigDialog(store)
    try:
        custom = store.provider_by_id("custom_lab")
        assert custom is not None
        dialog._current_provider_id = "custom_lab"
        dialog._load_form(custom)

        assert dialog._discover_btn.isEnabled()
        assert "provider endpoint" in dialog._discover_btn.toolTip()
    finally:
        dialog.close()


def test_provider_dialog_enables_azure_api_version_field(tmp_path: Path) -> None:
    _ensure_app()
    store = _make_store(tmp_path)
    store.save_provider(
        ProviderConfig(
            provider_id="azure_openai",
            display_name="Azure OpenAI",
            inference_service="azure_openai_responses",
            endpoint="https://example.openai.azure.com",
            api_version="2025-04-01-preview",
        )
    )
    dialog = AiProviderConfigDialog(store)
    try:
        cfg = store.provider_by_id("azure_openai")
        assert cfg is not None
        dialog._current_provider_id = "azure_openai"
        dialog._load_form(cfg)

        assert dialog._current_inference_service() == "azure_openai_responses"
        assert dialog._api_version_edit.isEnabled()
        assert dialog._api_version_edit.text() == "2025-04-01-preview"
        assert "service chat history: yes" in dialog._service_status.text()
    finally:
        dialog.close()


def test_provider_dialog_updates_service_specific_field_labels(tmp_path: Path) -> None:
    _ensure_app()
    store = _make_store(tmp_path)
    store.save_provider(
        ProviderConfig(
            provider_id="bedrock",
            display_name="Bedrock",
            inference_service="amazon_bedrock",
        )
    )
    dialog = AiProviderConfigDialog(store)
    try:
        cfg = store.provider_by_id("bedrock")
        assert cfg is not None
        dialog._current_provider_id = "bedrock"
        dialog._load_form(cfg)

        assert dialog._endpoint_label.text() == "AWS Region:"
        assert dialog._endpoint_edit.placeholderText() == "us-east-1"
        assert dialog._key_label.text() == "AWS Access Key:"
        assert not dialog._api_version_edit.isEnabled()
    finally:
        dialog.close()


def test_provider_dialog_reports_no_models_to_test(tmp_path: Path) -> None:
    _ensure_app()
    store = _make_store(tmp_path)
    store.save_provider(
        ProviderConfig(
            provider_id="custom_lab",
            display_name="Custom Lab",
            inference_service="custom_chat_client",
        )
    )
    dialog = AiProviderConfigDialog(store)
    try:
        custom = store.provider_by_id("custom_lab")
        assert custom is not None
        dialog._current_provider_id = "custom_lab"
        dialog._load_form(custom)

        dialog._on_test_models()

        assert dialog._fetch_status.text() == "No models to test. Discover endpoint models or add a model ID first."
        assert dialog._test_btn.isEnabled()
    finally:
        dialog.close()


def test_provider_dialog_adds_manual_model_id(tmp_path: Path) -> None:
    _ensure_app()
    store = _make_store(tmp_path)
    store.save_provider(
        ProviderConfig(
            provider_id="custom_lab",
            display_name="Custom Lab",
            inference_service="custom_chat_client",
        )
    )
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


def test_provider_dialog_filters_non_agent_models_from_selectors(tmp_path: Path) -> None:
    _ensure_app()
    store = _make_store(tmp_path)
    store.save_provider(
        ProviderConfig(
            provider_id="custom_lab",
            display_name="Custom Lab",
            inference_service="custom_chat_client",
            cached_models=[
                ModelInfo(model_id="gpt-5", display_name="GPT-5"),
                ModelInfo(
                    model_id="gpt-image-2",
                    display_name="gpt-image-2",
                    capabilities=ModelCapabilities(model_kind="image", supports_agent_chat=False),
                ),
            ],
        )
    )
    dialog = AiProviderConfigDialog(store)
    try:
        custom = store.provider_by_id("custom_lab")
        assert custom is not None
        dialog._current_provider_id = "custom_lab"
        dialog._load_form(custom)

        chat_ids = [dialog._chat_model_combo.itemData(i) for i in range(dialog._chat_model_combo.count())]
        assert "gpt-5" in chat_ids
        assert "gpt-image-2" not in chat_ids
    finally:
        dialog.close()


def test_provider_dialog_does_not_test_only_non_agent_models(tmp_path: Path) -> None:
    _ensure_app()
    store = _make_store(tmp_path)
    store.save_provider(
        ProviderConfig(
            provider_id="custom_lab",
            display_name="Custom Lab",
            inference_service="custom_chat_client",
            cached_models=[
                ModelInfo(
                    model_id="gpt-image-2",
                    display_name="gpt-image-2",
                    capabilities=ModelCapabilities(model_kind="image", supports_agent_chat=False),
                ),
            ],
        )
    )
    dialog = AiProviderConfigDialog(store)
    try:
        custom = store.provider_by_id("custom_lab")
        assert custom is not None
        dialog._current_provider_id = "custom_lab"
        dialog._load_form(custom)

        dialog._on_test_models()

        assert dialog._fetch_status.text() == "No models to test. Discover endpoint models or add a model ID first."
        assert dialog._test_btn.isEnabled()
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

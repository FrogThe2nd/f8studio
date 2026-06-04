"""
AiProviderConfigDialog — full provider management UI.

Allows users to:
  - Add / edit / delete AI providers
  - Configure Agent Framework inference service, endpoint URL, API key
  - Discover endpoint model IDs or add model IDs manually
  - Select default inline and chat models
"""
from __future__ import annotations

import logging

from qtpy import QtCore, QtGui, QtWidgets  # type: ignore[import-not-found]

from ...agents.model_catalog import supports_agent_chat_model, supports_endpoint_model_discovery
from ...agents.provider_endpoints import provider_default_endpoint_for_service
from ...agents.registry import (
    ProviderConfig,
    ProviderInferenceService,
    inference_service_display_name,
    inference_service_supports_service_history,
)
from ...agents.store import AiProviderStore
from ..support.studio_theme import label_qss, studio_dark_theme

logger = logging.getLogger(__name__)

_INFERENCE_SERVICES: list[tuple[str, ProviderInferenceService]] = [
    ("Foundry Agent", "foundry_agent"),
    ("Azure OpenAI Chat Completion", "azure_openai_chat_completion"),
    ("Azure OpenAI Responses", "azure_openai_responses"),
    ("OpenAI Chat Completion", "openai_chat_completion"),
    ("OpenAI Responses", "openai_responses"),
    ("Anthropic Claude", "anthropic_claude"),
    ("Amazon Bedrock", "amazon_bedrock"),
    ("GitHub Copilot", "github_copilot"),
    ("Ollama (OpenAI-compatible)", "ollama_chat"),
    ("Any other ChatClient", "custom_chat_client"),
]


class AiProviderConfigDialog(QtWidgets.QDialog):
    """Full AI provider manager dialog."""

    def __init__(self, store: AiProviderStore, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._store.providers_changed.connect(self._on_providers_changed)  # type: ignore[attr-defined]
        self._store.models_fetched.connect(self._on_models_fetched)  # type: ignore[attr-defined]
        self._store.model_tested.connect(self._on_model_tested)  # type: ignore[attr-defined]

        self.setWindowTitle("AI Provider Configuration")
        self.resize(780, 540)
        self._build_ui()
        self._populate_list()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root_layout = QtWidgets.QVBoxLayout(self)

        self._splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        root_layout.addWidget(self._splitter, 1)

        # Left: provider list + add/delete buttons
        left_widget = QtWidgets.QWidget()
        left = QtWidgets.QVBoxLayout(left_widget)
        left.setContentsMargins(0, 0, 0, 0)

        left.addWidget(QtWidgets.QLabel("<b>Providers</b>"))
        self._list = QtWidgets.QListWidget()
        self._list.currentRowChanged.connect(self._on_list_row_changed)  # type: ignore[attr-defined]
        left.addWidget(self._list, 1)

        add_del = QtWidgets.QHBoxLayout()
        self._add_btn = QtWidgets.QPushButton("+ Add")
        self._add_btn.clicked.connect(self._on_add)  # type: ignore[attr-defined]
        self._del_btn = QtWidgets.QPushButton("Delete")
        self._del_btn.clicked.connect(self._on_delete)  # type: ignore[attr-defined]
        self._del_btn.setEnabled(False)
        add_del.addWidget(self._add_btn)
        add_del.addWidget(self._del_btn)
        left.addLayout(add_del)

        self._splitter.addWidget(left_widget)

        # Right: form
        right_widget = QtWidgets.QWidget()
        right = QtWidgets.QVBoxLayout(right_widget)
        right.setContentsMargins(0, 0, 0, 0)

        form = QtWidgets.QFormLayout()
        right.addLayout(form)

        self._name_edit = QtWidgets.QLineEdit()
        form.addRow("Display Name:", self._name_edit)

        self._inference_service_combo = QtWidgets.QComboBox()
        for label, _ in _INFERENCE_SERVICES:
            self._inference_service_combo.addItem(label)
        self._inference_service_combo.currentIndexChanged.connect(self._on_inference_service_changed)  # type: ignore[attr-defined]
        form.addRow("Inference Service:", self._inference_service_combo)

        self._service_status = QtWidgets.QLabel("")
        self._service_status.setStyleSheet(label_qss(color=studio_dark_theme().palette.text_muted, font_size_px=11))
        self._service_status.setWordWrap(True)
        form.addRow("MAF:", self._service_status)

        self._endpoint_label = QtWidgets.QLabel("Endpoint URL:")
        self._endpoint_edit = QtWidgets.QLineEdit()
        self._endpoint_edit.setPlaceholderText("Leave empty for service default")
        form.addRow(self._endpoint_label, self._endpoint_edit)

        self._api_version_label = QtWidgets.QLabel("API Version:")
        self._api_version_edit = QtWidgets.QLineEdit()
        self._api_version_edit.setPlaceholderText("Azure OpenAI api-version, optional")
        form.addRow(self._api_version_label, self._api_version_edit)

        self._key_label = QtWidgets.QLabel("API Key:")
        self._key_edit = QtWidgets.QLineEdit()
        self._key_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        form.addRow(self._key_label, self._key_edit)

        # Model cache actions
        fetch_row = QtWidgets.QHBoxLayout()
        self._discover_btn = QtWidgets.QPushButton("Discover Endpoint")
        self._discover_btn.clicked.connect(self._on_discover_models)  # type: ignore[attr-defined]
        
        self._test_btn = QtWidgets.QPushButton("Test Models")
        self._test_btn.clicked.connect(self._on_test_models)  # type: ignore[attr-defined]
        
        self._remove_model_btn = QtWidgets.QPushButton("Remove Selected")
        self._remove_model_btn.clicked.connect(self._on_remove_models)  # type: ignore[attr-defined]

        fetch_row.addWidget(self._discover_btn)
        fetch_row.addWidget(self._test_btn)
        fetch_row.addWidget(self._remove_model_btn)

        form.addRow("Models:", fetch_row)

        self._fetch_status = QtWidgets.QLabel("")
        self._fetch_status.setStyleSheet(label_qss(color=studio_dark_theme().palette.text_muted, font_size_px=11))
        self._fetch_status.setWordWrap(True)
        self._fetch_status.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("Status:", self._fetch_status)

        add_model_row = QtWidgets.QHBoxLayout()
        self._model_id_edit = QtWidgets.QLineEdit()
        self._model_id_edit.setPlaceholderText("model ID")
        self._model_id_edit.returnPressed.connect(self._on_add_model_id)  # type: ignore[attr-defined]
        self._add_model_btn = QtWidgets.QPushButton("Add Model")
        self._add_model_btn.clicked.connect(self._on_add_model_id)  # type: ignore[attr-defined]
        add_model_row.addWidget(self._model_id_edit, 1)
        add_model_row.addWidget(self._add_model_btn)
        form.addRow("Add Model:", add_model_row)

        # Model table
        self._model_table = QtWidgets.QTableWidget(0, 3)
        self._model_table.setHorizontalHeaderLabels(["S", "Model Name", "Model ID"])
        self._model_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Fixed)
        self._model_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self._model_table.setColumnWidth(0, 24)
        self._model_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self._model_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self._model_table.setMaximumHeight(150)
        form.addRow("", self._model_table)

        # Per-task model selectors
        self._inline_model_combo = QtWidgets.QComboBox()
        form.addRow("Inline Model:", self._inline_model_combo)

        self._chat_model_combo = QtWidgets.QComboBox()
        form.addRow("Chat/Edit Model:", self._chat_model_combo)

        self._setup_combo_search(self._inline_model_combo)
        self._setup_combo_search(self._chat_model_combo)

        self._reasoning_combo = QtWidgets.QComboBox()
        self._reasoning_combo.addItems(["(none)", "low", "medium", "high"])
        form.addRow("Reasoning Level:", self._reasoning_combo)

        right.addStretch()

        # Explicit ordering keeps provider actions consistent across Qt platform styles.
        self._footer_buttons_layout = QtWidgets.QHBoxLayout()
        self._footer_buttons_layout.setContentsMargins(0, 0, 0, 0)
        self._footer_buttons_layout.setSpacing(8)
        self._footer_buttons_layout.addStretch(1)

        self._save_provider_button = QtWidgets.QPushButton("Save", self)
        self._save_provider_button.setObjectName("aiProviderSaveButton")
        self._save_provider_button.clicked.connect(self._on_save_provider)  # type: ignore[attr-defined]
        self._footer_buttons_layout.addWidget(self._save_provider_button)

        self._close_button = QtWidgets.QPushButton("Close", self)
        self._close_button.setObjectName("aiProviderCloseButton")
        self._close_button.clicked.connect(self.close)  # type: ignore[attr-defined]
        self._footer_buttons_layout.addWidget(self._close_button)

        right.addLayout(self._footer_buttons_layout)

        self._splitter.addWidget(right_widget)
        # Set initial sizes: 1/3 for list, 2/3 for form
        self._splitter.setSizes([260, 520])

        self._form_widgets: list[QtWidgets.QWidget] = [
            self._name_edit, self._inference_service_combo, self._endpoint_edit,
            self._api_version_edit, self._key_edit, self._discover_btn, self._test_btn, self._remove_model_btn,
            self._model_id_edit, self._add_model_btn, self._model_table,
            self._inline_model_combo, self._chat_model_combo, self._reasoning_combo,
        ]
        self._set_form_enabled(False)
        self._current_provider_id: str = ""
        self._pending_model_tests: set[str] = set()
        self._test_success_count = 0
        self._test_error_count = 0

    # ------------------------------------------------------------------
    # List management
    # ------------------------------------------------------------------

    def _populate_list(self) -> None:
        current_id = self._current_provider_id
        self._list.blockSignals(True)
        self._list.clear()
        for cfg in self._store.providers():
            item = QtWidgets.QListWidgetItem(f"{cfg.health_icon} {cfg.display_name}")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, cfg.provider_id)
            self._list.addItem(item)
        self._list.blockSignals(False)
        # Reselect previously selected
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item and item.data(QtCore.Qt.ItemDataRole.UserRole) == current_id:
                self._list.setCurrentRow(i)
                return
        if self._list.count():
            self._list.setCurrentRow(0)

    def _on_list_row_changed(self, row: int) -> None:
        item = self._list.item(row)
        if item is None:
            self._set_form_enabled(False)
            self._del_btn.setEnabled(False)
            return
        pid = str(item.data(QtCore.Qt.ItemDataRole.UserRole) or "")
        self._current_provider_id = pid
        cfg = self._store.provider_by_id(pid)
        if cfg is None:
            self._set_form_enabled(False)
            return
        self._load_form(cfg)
        self._set_form_enabled(True)
        self._update_service_controls(cfg)
        self._update_model_catalog_controls(cfg)
        self._del_btn.setEnabled(True)

    # ------------------------------------------------------------------
    # Form load / save
    # ------------------------------------------------------------------

    def _load_form(self, cfg: ProviderConfig) -> None:
        self._name_edit.setText(cfg.display_name)
        service_idx = next(
            (i for i, (_, service) in enumerate(_INFERENCE_SERVICES) if service == cfg.inference_service),
            0,
        )
        self._inference_service_combo.blockSignals(True)
        try:
            self._inference_service_combo.setCurrentIndex(service_idx)
        finally:
            self._inference_service_combo.blockSignals(False)
        self._endpoint_edit.setText(cfg.endpoint)
        self._api_version_edit.setText(cfg.api_version)
        self._key_edit.setText(cfg.api_key)
        self._model_id_edit.clear()
            
        self._refresh_model_combos(cfg)
        self._update_service_controls(cfg)
        self._update_model_catalog_controls(cfg)
        self._fetch_status.setText("")

    def _refresh_model_combos(self, cfg: ProviderConfig) -> None:
        self._model_table.setRowCount(0)
        self._inline_model_combo.blockSignals(True)
        self._chat_model_combo.blockSignals(True)
        try:
            self._inline_model_combo.clear()
            self._chat_model_combo.clear()
            self._inline_model_combo.addItem("(none)", "")
            self._chat_model_combo.addItem("(none)", "")
            
            for m in cfg.cached_models:
                row = self._model_table.rowCount()
                self._model_table.insertRow(row)
                
                # Status
                status_map = {"ok": "🟢", "error": "🔴", "unknown": "⚪"}
                status_item = QtWidgets.QTableWidgetItem(status_map.get(m.health_status, "⚪"))
                status_item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                self._model_table.setItem(row, 0, status_item)
                
                # Name
                self._model_table.setItem(row, 1, QtWidgets.QTableWidgetItem(m.display_name_with_icons))
                
                # ID
                id_item = QtWidgets.QTableWidgetItem(m.model_id)
                id_item.setForeground(QtGui.QColor(studio_dark_theme().palette.text_muted))
                self._model_table.setItem(row, 2, id_item)
                
                if m.health_status != "error" and supports_agent_chat_model(m):
                    self._inline_model_combo.addItem(m.full_display_label, m.model_id)
                    self._chat_model_combo.addItem(m.full_display_label, m.model_id)

            # Restore selection
            for i in range(self._inline_model_combo.count()):
                if self._inline_model_combo.itemData(i) == cfg.inline_model_id:
                    self._inline_model_combo.setCurrentIndex(i)
                    break

            for i in range(self._chat_model_combo.count()):
                if self._chat_model_combo.itemData(i) == cfg.chat_model_id:
                    self._chat_model_combo.setCurrentIndex(i)
                    break
        finally:
            self._inline_model_combo.blockSignals(False)
            self._chat_model_combo.blockSignals(False)

        levels = ["(none)", "low", "medium", "high"]
        try:
            self._reasoning_combo.setCurrentIndex(levels.index(cfg.reasoning_level) if cfg.reasoning_level in levels else 0)
        except ValueError:
            self._reasoning_combo.setCurrentIndex(0)

    def _on_save_provider(self) -> None:
        pid = self._current_provider_id
        if not pid:
            return
        cfg = self._store.provider_by_id(pid)
        if cfg is None:
            return

        cfg.display_name = self._name_edit.text().strip() or cfg.display_name
        cfg.inference_service = self._current_inference_service()
        cfg.endpoint = self._endpoint_edit.text().strip()
        cfg.api_version = self._api_version_edit.text().strip()
        cfg.api_key = self._key_edit.text().strip()

        inline_idx = self._inline_model_combo.currentIndex()
        cfg.inline_model_id = str(self._inline_model_combo.itemData(inline_idx) or "") if inline_idx >= 0 else ""

        chat_idx = self._chat_model_combo.currentIndex()
        cfg.chat_model_id = str(self._chat_model_combo.itemData(chat_idx) or "") if chat_idx >= 0 else ""

        reasoning_text = self._reasoning_combo.currentText()
        cfg.reasoning_level = "" if reasoning_text == "(none)" else reasoning_text

        self._store.save_provider(cfg)
        self._fetch_status.setText("Saved.")

    # ------------------------------------------------------------------
    # Add/Delete
    # ------------------------------------------------------------------

    def _on_add(self) -> None:
        text, ok = QtWidgets.QInputDialog.getText(
            self, "New Provider", "Provider ID (unique lowercase slug):"
        )
        if not ok:
            return
        pid = str(text or "").strip().lower().replace(" ", "_")
        if not pid:
            return
        if self._store.provider_by_id(pid) is not None:
            QtWidgets.QMessageBox.warning(self, "Duplicate", f"Provider '{pid}' already exists.")
            return
        cfg = ProviderConfig(
            provider_id=pid,
            display_name=pid.replace("_", " ").title(),
            inference_service="custom_chat_client",
        )
        self._store.save_provider(cfg)
        self._current_provider_id = pid
        self._populate_list()

    def _on_delete(self) -> None:
        pid = self._current_provider_id
        if not pid:
            return
        answer = QtWidgets.QMessageBox.question(
            self, "Delete Provider",
            f"Delete provider '{pid}'?\nThis cannot be undone.",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self._current_provider_id = ""
        self._store.delete_provider(pid)
        self._populate_list()

    # ------------------------------------------------------------------
    # Model fetch
    # ------------------------------------------------------------------

    def _on_discover_models(self) -> None:
        pid = self._current_provider_id
        if not pid:
            return
        cfg = self._save_catalog_form_fields(pid)
        if cfg is None:
            return
        if not supports_endpoint_model_discovery(cfg):
            self._fetch_status.setText("Endpoint discovery is not available for this provider type.")
            self._fetch_status.setToolTip("")
            return

        self._discover_btn.setEnabled(False)
        self._fetch_status.setText("Discovering endpoint models...")
        self._fetch_status.setToolTip("")
        self._store.discover_endpoint_models_async(pid)

    def _on_models_fetched(self, pid: str, success: bool, error: str) -> None:
        if pid != self._current_provider_id:
            return
        cfg = self._store.provider_by_id(pid)
        if cfg is not None:
            self._update_model_catalog_controls(cfg)
        if success:
            self._fetch_status.setText(error or "Models updated.")
            self._fetch_status.setToolTip("")
            if cfg:
                self._refresh_model_combos(cfg)
        else:
            self._fetch_status.setText(error or "Model catalog update failed.")
            self._fetch_status.setToolTip(error)

    def _on_add_model_id(self) -> None:
        pid = self._current_provider_id
        model_id = self._model_id_edit.text().strip()
        if not pid or not model_id:
            return
        if not self._store.add_cached_model(pid, model_id):
            self._fetch_status.setText("Unable to add model.")
            return
        self._model_id_edit.clear()
        cfg = self._store.provider_by_id(pid)
        if cfg is not None:
            self._refresh_model_combos(cfg)
        self._fetch_status.setText("Model added.")

    def _on_remove_models(self) -> None:
        pid = self._current_provider_id
        if not pid:
            return
        selected_ids: list[str] = []
        indices = self._model_table.selectionModel().selectedRows()
        for idx in indices:
            item = self._model_table.item(idx.row(), 2)
            if item is not None:
                selected_ids.append(item.text())
        removed_count = self._store.remove_cached_models(pid, selected_ids)
        if removed_count <= 0:
            self._fetch_status.setText("No model selected.")
            return
        cfg = self._store.provider_by_id(pid)
        if cfg is not None:
            self._refresh_model_combos(cfg)
        self._fetch_status.setText("Model removed." if removed_count == 1 else f"{removed_count} models removed.")

    def _on_test_models(self) -> None:
        pid = self._current_provider_id
        if not pid:
            return
        cfg = self._save_catalog_form_fields(pid)
        if not cfg:
            return

        selected_ids: list[str] = []
        indices = self._model_table.selectionModel().selectedRows()
        for idx in indices:
            item = self._model_table.item(idx.row(), 2)
            if item is not None:
                selected_ids.append(item.text())

        ids_to_test = selected_ids if selected_ids else [
            model.model_id for model in cfg.cached_models if supports_agent_chat_model(model)
        ]
        if not ids_to_test:
            self._fetch_status.setText("No models to test. Discover endpoint models or add a model ID first.")
            self._fetch_status.setToolTip("")
            return

        self._pending_model_tests = set(ids_to_test)
        self._test_success_count = 0
        self._test_error_count = 0
        self._test_btn.setEnabled(False)
        self._fetch_status.setText(f"Testing connectivity for {len(ids_to_test)} model(s)...")
        self._fetch_status.setToolTip("")
        if not self._store.test_models_async(pid, ids_to_test):
            self._pending_model_tests.clear()
            self._test_btn.setEnabled(True)
            self._fetch_status.setText("No matching models to test. Refresh the table or add a model ID first.")

    def _on_model_tested(self, pid: str, model_id: str, success: bool, error: str) -> None:
        if pid != self._current_provider_id:
            return
        if model_id in self._pending_model_tests:
            self._pending_model_tests.remove(model_id)
        if success:
            self._test_success_count += 1
        else:
            self._test_error_count += 1
            
        # Update table status
        for row in range(self._model_table.rowCount()):
            if self._model_table.item(row, 2).text() == model_id:
                status_item = self._model_table.item(row, 0)
                status_item.setText("🟢" if success else "🔴")
                if not success:
                    status_item.setToolTip(error)
                break
        
        # Immediate sync for combos
        cfg = self._store.provider_by_id(pid)
        if cfg:
            self._refresh_model_combos(cfg)

        if self._pending_model_tests:
            self._fetch_status.setText(f"Testing connectivity... {len(self._pending_model_tests)} remaining.")
            if not success:
                self._fetch_status.setToolTip(error)
            return

        self._test_btn.setEnabled(True)
        if self._test_error_count:
            self._fetch_status.setText(
                f"Connectivity test complete: {self._test_success_count} ok, {self._test_error_count} failed."
            )
            self._fetch_status.setToolTip(error)
        else:
            self._fetch_status.setText(f"Connectivity test complete: {self._test_success_count} ok.")
            self._fetch_status.setToolTip("")

    def _on_inference_service_changed(self, idx: int) -> None:
        if not self._current_provider_id:
            return
        if 0 <= idx < len(_INFERENCE_SERVICES):
            _label, service = _INFERENCE_SERVICES[idx]
            if not self._endpoint_edit.text().strip():
                self._endpoint_edit.setText(provider_default_endpoint_for_service(service))
            cfg = self._store.provider_by_id(self._current_provider_id)
            if cfg is not None:
                cfg.inference_service = service
                cfg.endpoint = self._endpoint_edit.text().strip()
                cfg.api_version = self._api_version_edit.text().strip()
                self._update_service_controls(cfg)
                self._update_model_catalog_controls(cfg)

    def _on_providers_changed(self) -> None:
        self._populate_list()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _current_inference_service(self) -> ProviderInferenceService:
        idx = self._inference_service_combo.currentIndex()
        if 0 <= idx < len(_INFERENCE_SERVICES):
            return _INFERENCE_SERVICES[idx][1]
        return "custom_chat_client"

    def _update_service_controls(self, cfg: ProviderConfig) -> None:
        service_name = inference_service_display_name(cfg.inference_service)
        history = "service chat history: yes" if inference_service_supports_service_history(cfg.inference_service) else "service chat history: no"
        self._service_status.setText(f"{service_name}; {history}.")
        self._api_version_edit.setEnabled(cfg.inference_service in ("azure_openai_chat_completion", "azure_openai_responses"))
        self._api_version_label.setEnabled(self._api_version_edit.isEnabled())

        if cfg.inference_service == "foundry_agent":
            self._endpoint_label.setText("Project Endpoint:")
            self._endpoint_edit.setPlaceholderText("https://<resource>.services.ai.azure.com/api/projects/<project>")
            self._key_label.setText("Credential:")
            self._key_edit.setPlaceholderText("Use Azure credential environment or project client")
        elif cfg.inference_service == "amazon_bedrock":
            self._endpoint_label.setText("AWS Region:")
            self._endpoint_edit.setPlaceholderText("us-east-1")
            self._key_label.setText("AWS Access Key:")
            self._key_edit.setPlaceholderText("Optional; secret/session can come from AWS environment or profile")
        elif cfg.inference_service == "github_copilot":
            self._endpoint_label.setText("Endpoint URL:")
            self._endpoint_edit.setPlaceholderText("Managed by GitHub Copilot SDK")
            self._key_label.setText("Credential:")
            self._key_edit.setPlaceholderText("Managed by GitHub Copilot SDK")
        elif cfg.inference_service == "ollama_chat":
            self._endpoint_label.setText("Ollama Host:")
            self._endpoint_edit.setPlaceholderText(provider_default_endpoint_for_service(cfg.inference_service))
            self._key_label.setText("API Key:")
            self._key_edit.setPlaceholderText("Not required for local Ollama")
        elif cfg.inference_service in ("azure_openai_chat_completion", "azure_openai_responses"):
            self._endpoint_label.setText("Azure Endpoint:")
            self._endpoint_edit.setPlaceholderText("https://<resource>.openai.azure.com")
            self._key_label.setText("API Key:")
            self._key_edit.setPlaceholderText("Azure OpenAI API key")
        else:
            self._endpoint_label.setText("Endpoint URL:")
            default_endpoint = provider_default_endpoint_for_service(cfg.inference_service)
            self._endpoint_edit.setPlaceholderText(default_endpoint or "Provider endpoint URL")
            self._key_label.setText("API Key:")
            self._key_edit.setPlaceholderText("Provider API key")

    def _update_model_catalog_controls(self, cfg: ProviderConfig) -> None:
        supports_discovery = supports_endpoint_model_discovery(cfg)
        self._discover_btn.setEnabled(supports_discovery)
        if supports_discovery:
            self._discover_btn.setToolTip("Query the provider endpoint for model IDs.")
        else:
            self._discover_btn.setToolTip("Endpoint model discovery is not available for this provider type.")

    def _save_catalog_form_fields(self, provider_id: str) -> ProviderConfig | None:
        cfg = self._store.provider_by_id(provider_id)
        if cfg is None:
            return None
        cfg.api_key = self._key_edit.text().strip()
        cfg.endpoint = self._endpoint_edit.text().strip()
        cfg.api_version = self._api_version_edit.text().strip()
        cfg.inference_service = self._current_inference_service()
        self._store.save_provider(cfg)
        return cfg

    def _set_form_enabled(self, enabled: bool) -> None:
        for w in self._form_widgets:
            w.setEnabled(enabled)
        if enabled and self._current_provider_id:
            cfg = self._store.provider_by_id(self._current_provider_id)
            if cfg is not None:
                self._update_service_controls(cfg)
                self._update_model_catalog_controls(cfg)

    def _setup_combo_search(self, combo: QtWidgets.QComboBox) -> None:
        combo.setEditable(False)
        combo.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        completer = combo.completer()
        if completer:
            completer.setFilterMode(QtCore.Qt.MatchFlag.MatchContains)
            completer.setCompletionMode(QtWidgets.QCompleter.CompletionMode.PopupCompletion)


def _vline() -> QtWidgets.QFrame:
    line = QtWidgets.QFrame()
    line.setFrameShape(QtWidgets.QFrame.Shape.VLine)
    line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
    return line

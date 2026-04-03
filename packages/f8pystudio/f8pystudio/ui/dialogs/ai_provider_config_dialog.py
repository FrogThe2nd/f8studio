"""
AiProviderConfigDialog — full provider management UI.

Allows users to:
  - Add / edit / delete AI providers
  - Configure protocol, endpoint URL, API key
  - Fetch the model list from the provider's API
  - Select default inline and chat models
"""
from __future__ import annotations

import logging

from qtpy import QtCore, QtGui, QtWidgets  # type: ignore[import-not-found]

from ...ai_assist.registry import DEFAULT_PROVIDERS, ProviderConfig, ProviderProtocol
from ...ai_assist.store import AiProviderStore

logger = logging.getLogger(__name__)

_PROTOCOLS: list[tuple[str, ProviderProtocol]] = [
    ("OpenAI / compatible", "openai"),
    ("Anthropic", "anthropic"),
    ("Ollama", "ollama"),
    ("Custom", "custom"),
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

        self._protocol_combo = QtWidgets.QComboBox()
        for label, _ in _PROTOCOLS:
            self._protocol_combo.addItem(label)
        self._protocol_combo.currentIndexChanged.connect(self._on_protocol_changed)  # type: ignore[attr-defined]
        form.addRow("Protocol:", self._protocol_combo)

        self._endpoint_edit = QtWidgets.QLineEdit()
        self._endpoint_edit.setPlaceholderText("Leave empty for protocol default")
        form.addRow("Endpoint URL:", self._endpoint_edit)

        self._key_edit = QtWidgets.QLineEdit()
        self._key_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        form.addRow("API Key:", self._key_edit)

        self._models_path_edit = QtWidgets.QLineEdit()
        self._models_path_edit.setPlaceholderText("Default: /models")
        form.addRow("Models Path:", self._models_path_edit)

        self._chat_path_edit = QtWidgets.QComboBox()
        self._chat_path_edit.setEditable(True)
        self._chat_path_edit.addItems([
            "/chat/completions",
            "/v1/chat/completions",
            "/v1/messages",
            "/v1/completions",
            "/v1/responses",
        ])
        self._chat_path_edit.setPlaceholderText("Default: /chat/completions")
        form.addRow("Chat Path:", self._chat_path_edit)

        # Model fetch row
        fetch_row = QtWidgets.QHBoxLayout()
        self._fetch_btn = QtWidgets.QPushButton("⟳ Fetch Models")
        self._fetch_btn.clicked.connect(self._on_fetch_models)  # type: ignore[attr-defined]
        
        self._test_btn = QtWidgets.QPushButton("Test Models")
        self._test_btn.clicked.connect(self._on_test_models)  # type: ignore[attr-defined]
        
        fetch_row.addWidget(self._fetch_btn)
        fetch_row.addWidget(self._test_btn)

        self._fetch_status = QtWidgets.QLabel("")
        self._fetch_status.setStyleSheet("color: gray; font-size: 11px;")
        # Fix layout jumping: Use Ignored policy so long text doesn't push the layout.
        # We also enable text elision or simply let it overflow while staying compact.
        self._fetch_status.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        fetch_row.addWidget(self._fetch_status, 1)

        form.addRow("Models:", fetch_row)

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

        # Save / discard buttons
        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Close
        )
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Save).clicked.connect(self._on_save_provider)  # type: ignore[attr-defined]
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Close).clicked.connect(self.close)  # type: ignore[attr-defined]
        right.addWidget(btns)

        self._splitter.addWidget(right_widget)
        # Set initial sizes: 1/3 for list, 2/3 for form
        self._splitter.setSizes([260, 520])

        self._form_widgets: list[QtWidgets.QWidget] = [
            self._name_edit, self._protocol_combo, self._endpoint_edit,
            self._key_edit, self._models_path_edit, self._chat_path_edit,
            self._fetch_btn, self._test_btn, self._model_table,
            self._inline_model_combo, self._chat_model_combo, self._reasoning_combo,
        ]
        self._set_form_enabled(False)
        self._current_provider_id: str = ""

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
        self._del_btn.setEnabled(True)

    # ------------------------------------------------------------------
    # Form load / save
    # ------------------------------------------------------------------

    def _load_form(self, cfg: ProviderConfig) -> None:
        self._name_edit.setText(cfg.display_name)
        proto_idx = next(
            (i for i, (_, p) in enumerate(_PROTOCOLS) if p == cfg.protocol), 0
        )
        self._protocol_combo.setCurrentIndex(proto_idx)
        self._endpoint_edit.setText(cfg.endpoint)
        self._key_edit.setText(cfg.api_key)
        self._models_path_edit.setText(cfg.models_path)
        
        # Load chat path into combo (either selecting preset or setting custom text)
        path = cfg.chat_path
        idx = self._chat_path_edit.findText(path)
        if idx >= 0:
            self._chat_path_edit.setCurrentIndex(idx)
        else:
            self._chat_path_edit.setEditText(path)
            
        self._refresh_model_combos(cfg)
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
                id_item.setForeground(QtGui.QColor("#888888"))
                self._model_table.setItem(row, 2, id_item)
                
                if m.health_status != "error":
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
        proto_idx = self._protocol_combo.currentIndex()
        if 0 <= proto_idx < len(_PROTOCOLS):
            cfg.protocol = _PROTOCOLS[proto_idx][1]
        cfg.endpoint = self._endpoint_edit.text().strip()
        cfg.api_key = self._key_edit.text().strip()
        cfg.models_path = self._models_path_edit.text().strip()
        cfg.chat_path = self._chat_path_edit.currentText().strip()

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

    def _on_fetch_models(self) -> None:
        pid = self._current_provider_id
        if not pid:
            return
        # Apply current form api_key/endpoint before fetching
        cfg = self._store.provider_by_id(pid)
        if cfg is None:
            return
        cfg.api_key = self._key_edit.text().strip()
        cfg.endpoint = self._endpoint_edit.text().strip()
        cfg.models_path = self._models_path_edit.text().strip()
        cfg.chat_path = self._chat_path_edit.currentText().strip()
        self._store.save_provider(cfg)  # persist key/endpoint/paths first

        self._fetch_btn.setEnabled(False)
        self._fetch_status.setText("Fetching…")
        self._store.fetch_models_async(pid)

    def _on_models_fetched(self, pid: str, success: bool, error: str) -> None:
        if pid != self._current_provider_id:
            return
        self._fetch_btn.setEnabled(True)
        if success:
            self._fetch_status.setText("✓ Models updated")
            cfg = self._store.provider_by_id(pid)
            if cfg:
                self._refresh_model_combos(cfg)
        else:
            self._fetch_status.setText(f"Error: {error}")
            self._fetch_status.setToolTip(error)

    def _on_test_models(self) -> None:
        pid = self._current_provider_id
        if not pid:
            return
        cfg = self._store.provider_by_id(pid)
        if not cfg:
            return

        # Get selected models
        selected_ids = []
        indices = self._model_table.selectionModel().selectedRows()
        for idx in indices:
            mid = self._model_table.item(idx.row(), 2).text()
            selected_ids.append(mid)
        
        # If none selected, test all
        ids_to_test = selected_ids if selected_ids else None
        
        self._test_btn.setEnabled(False)
        self._fetch_status.setText("Testing connectivity…")
        self._store.test_models_async(pid, ids_to_test)

    def _on_model_tested(self, pid: str, model_id: str, success: bool, error: str) -> None:
        if pid != self._current_provider_id:
            return
            
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

        self._test_btn.setEnabled(True)
        self._fetch_status.setText("Test complete." if success else f"Error: {error}")
        if not success:
            self._fetch_status.setToolTip(error)
        else:
            self._fetch_status.setToolTip("")

    def _on_protocol_changed(self, idx: int) -> None:
        if not self._current_provider_id:
            return
        if 0 <= idx < len(_PROTOCOLS):
            label, proto = _PROTOCOLS[idx]
            defaults = {
                "openai": "https://api.openai.com/v1",
                "anthropic": "https://api.anthropic.com",
                "ollama": "http://localhost:11434/v1",
                "custom": "",
            }
            if not self._endpoint_edit.text().strip():
                self._endpoint_edit.setText(defaults.get(proto, ""))

    def _on_providers_changed(self) -> None:
        self._populate_list()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_form_enabled(self, enabled: bool) -> None:
        for w in self._form_widgets:
            w.setEnabled(enabled)

    def _setup_combo_search(self, combo: QtWidgets.QComboBox) -> None:
        combo.setEditable(True)
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

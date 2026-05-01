"""
AiQuickPanel — compact collapsible panel embedded in F8MonacoEditorDialog.

The panel provides:
  - Provider selector for inline (FIM) model
  - Provider selector for chat/edit/plan model
  - Model combo (filtered by provider) + small refresh button
  - Reasoning level combo (shown only when selected model supports it)
  - "Open Full Config" button that opens AiProviderConfigDialog
"""
from __future__ import annotations

import logging

from qtpy import QtCore, QtGui, QtWidgets  # type: ignore[import-not-found]

from ...ai_assist.llm_bridge import AiLlmBridge
from ...ai_assist.registry import ProviderConfig
from ...ai_assist.store import AiProviderStore
from ..support.studio_theme import ai_quick_panel_qss, flat_link_button_qss, label_qss, studio_dark_theme

logger = logging.getLogger(__name__)

_REASONING_LEVELS = ["(none)", "low", "medium", "high"]


class AiQuickPanel(QtWidgets.QWidget):
    """
    Compact panel for model selection, shown/hidden by a toolbar button.
    Lives inside the Monaco dialog layout (not a separate dialog window).
    """

    open_full_config_requested = QtCore.Signal()

    def __init__(
        self,
        store: AiProviderStore,
        bridge: AiLlmBridge,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._bridge = bridge
        self._building = False

        self._store.providers_changed.connect(self._on_providers_changed)  # type: ignore[attr-defined]
        self._store.models_fetched.connect(self._on_models_fetched)  # type: ignore[attr-defined]
        self._store.model_tested.connect(self._on_model_tested)  # type: ignore[attr-defined]

        self.setObjectName("aiQuickPanel")
        self.setStyleSheet(ai_quick_panel_qss())
        # Add drop shadow effect
        shadow = QtWidgets.QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(15)
        shadow.setXOffset(0)
        shadow.setYOffset(4)
        shadow.setColor(QtGui.QColor(0, 0, 0, 160))
        self.setGraphicsEffect(shadow)
        
        self.setMinimumWidth(260)
        self.setMaximumWidth(320)

        self._build_ui()
        self._populate_providers()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Title
        title = QtWidgets.QLabel("<b>AI Settings</b>")
        title.setStyleSheet(label_qss(color=studio_dark_theme().palette.text_primary, font_size_px=12))
        layout.addWidget(title)

        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        sep.setStyleSheet(label_qss(color=studio_dark_theme().palette.border_subtle))
        layout.addWidget(sep)

        # --- Inline task ---
        layout.addWidget(self._section_label("Inline Suggestions"))

        self._inline_provider_combo = QtWidgets.QComboBox()
        self._inline_provider_combo.currentIndexChanged.connect(lambda _: self._on_inline_provider_changed())  # type: ignore[attr-defined]
        layout.addWidget(self._labeled("Provider:", self._inline_provider_combo))

        inline_model_row = QtWidgets.QHBoxLayout()
        self._inline_model_combo = QtWidgets.QComboBox()
        self._inline_model_combo.currentIndexChanged.connect(lambda _: self._on_inline_model_changed())  # type: ignore[attr-defined]
        self._inline_refresh_btn = QtWidgets.QToolButton()
        self._inline_refresh_btn.setText("⟳")
        self._inline_refresh_btn.setToolTip("Refresh model list")
        self._inline_refresh_btn.clicked.connect(self._on_inline_refresh)  # type: ignore[attr-defined]
        inline_model_row.addWidget(self._inline_model_combo, 1)
        inline_model_row.addWidget(self._inline_refresh_btn)
        layout.addWidget(self._labeled("Model:", inline_model_row))

        # --- Chat / Edit / Plan task ---
        layout.addWidget(self._section_label("Chat / Edit / Plan"))

        self._chat_provider_combo = QtWidgets.QComboBox()
        self._chat_provider_combo.currentIndexChanged.connect(lambda _: self._on_chat_provider_changed())  # type: ignore[attr-defined]
        layout.addWidget(self._labeled("Provider:", self._chat_provider_combo))

        chat_model_row = QtWidgets.QHBoxLayout()
        self._chat_model_combo = QtWidgets.QComboBox()
        self._chat_model_combo.currentIndexChanged.connect(lambda _: self._on_chat_model_changed())  # type: ignore[attr-defined]
        self._chat_refresh_btn = QtWidgets.QToolButton()
        self._chat_refresh_btn.setText("⟳")
        self._chat_refresh_btn.setToolTip("Refresh model list")
        self._chat_refresh_btn.clicked.connect(self._on_chat_refresh)  # type: ignore[attr-defined]
        chat_model_row.addWidget(self._chat_model_combo, 1)
        chat_model_row.addWidget(self._chat_refresh_btn)
        layout.addWidget(self._labeled("Model:", chat_model_row))

        self._reasoning_label = QtWidgets.QLabel("Reasoning:")
        self._reasoning_label.setStyleSheet(label_qss(color=studio_dark_theme().palette.text_muted, font_size_px=11))
        self._reasoning_combo = QtWidgets.QComboBox()
        self._reasoning_combo.addItems(_REASONING_LEVELS)
        self._reasoning_combo.currentTextChanged.connect(self._on_reasoning_changed)  # type: ignore[attr-defined]
        self._reasoning_row = self._labeled_pair(self._reasoning_label, self._reasoning_combo)
        layout.addWidget(self._reasoning_row)
        
        # Setup search for model combos
        self._setup_combo_search(self._inline_model_combo)
        self._setup_combo_search(self._chat_model_combo)

        layout.addStretch()

        self._full_config_btn = QtWidgets.QPushButton("Open Full Config")
        self._full_config_btn.setFlat(True)
        self._full_config_btn.setStyleSheet(flat_link_button_qss())
        self._full_config_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._full_config_btn.clicked.connect(self.open_full_config_requested)  # type: ignore[attr-defined]
        layout.addWidget(self._full_config_btn)

    # ------------------------------------------------------------------
    # Provider / model population
    # ------------------------------------------------------------------

    def _populate_providers(self) -> None:
        self._building = True
        try:
            providers = self._store.providers()
            selection_state = self._bridge.selection_state()

            self._inline_provider_combo.clear()
            self._chat_provider_combo.clear()
            for p in providers:
                self._inline_provider_combo.addItem(f"{p.health_icon} {p.display_name}", p.provider_id)
                self._chat_provider_combo.addItem(f"{p.health_icon} {p.display_name}", p.provider_id)

            # Add virtual LSP provider for inline suggestions
            self._inline_provider_combo.addItem("🧩 Standard Python LSP", "lsp")

            # Select the global active provider from bridge
            self._set_combo_by_data(self._inline_provider_combo, selection_state.inline_provider_id)
            self._set_combo_by_data(self._chat_provider_combo, selection_state.chat_provider_id)

            # Populate models for the active provider
            in_p = self._current_provider(self._inline_provider_combo)
            if in_p:
                self._fill_inline_models(in_p)
                self._set_combo_by_data(self._inline_model_combo, selection_state.inline_model_id)
                
            ch_p = self._current_provider(self._chat_provider_combo)
            if ch_p:
                self._fill_chat_models(ch_p)
                self._set_combo_by_data(self._chat_model_combo, selection_state.chat_model_id)
                
                # set reasoning combo correctly
                levels = ["(none)", "low", "medium", "high"]
                if selection_state.reasoning_level in levels:
                    self._reasoning_combo.setCurrentIndex(levels.index(selection_state.reasoning_level))
                else:
                    self._reasoning_combo.setCurrentIndex(0)
        finally:
            self._building = False

        # Notify bridge of current selections
        self._on_inline_model_changed()
        self._on_chat_model_changed()

    def _fill_inline_models(self, cfg: ProviderConfig) -> None:
        current_id = self._current_id(self._inline_model_combo)
        self._inline_model_combo.blockSignals(True)
        try:
            self._inline_model_combo.clear()
            self._inline_model_combo.addItem("(none)", "")
            for m in cfg.cached_models:
                if m.health_status == "error":
                    continue
                self._inline_model_combo.addItem(m.full_display_label, m.model_id)
            if not cfg.cached_models:
                self._inline_model_combo.addItem("(no models cached — click ⟳)", "")
            
            if current_id == "lsp":
                self._inline_model_combo.addItem("Standard Python LSP", "standard")
                self._inline_model_combo.setCurrentIndex(0)
            elif current_id:
                self._set_combo_by_data(self._inline_model_combo, current_id)
        finally:
            self._inline_model_combo.blockSignals(False)

    def _fill_chat_models(self, cfg: ProviderConfig) -> None:
        current_id = self._current_id(self._chat_model_combo)
        self._chat_model_combo.blockSignals(True)
        try:
            self._chat_model_combo.clear()
            self._chat_model_combo.addItem("(none)", "")
            for m in cfg.cached_models:
                if m.health_status == "error":
                    continue
                self._chat_model_combo.addItem(m.full_display_label, m.model_id)
            if not cfg.cached_models:
                self._chat_model_combo.addItem("(no models cached — click ⟳)", "")
                
            if current_id:
                self._set_combo_by_data(self._chat_model_combo, current_id)
        finally:
            self._chat_model_combo.blockSignals(False)

    def _setup_combo_search(self, combo: QtWidgets.QComboBox) -> None:
        combo.setEditable(True)
        combo.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        completer = combo.completer()
        if completer:
            completer.setFilterMode(QtCore.Qt.MatchFlag.MatchContains)
            completer.setCompletionMode(QtWidgets.QCompleter.CompletionMode.PopupCompletion)

    # ------------------------------------------------------------------
    # Slot handlers
    # ------------------------------------------------------------------

    def _on_inline_provider_changed(self) -> None:
        if self._building:
            return
        self._building = True
        try:
            cfg = self._current_provider(self._inline_provider_combo)
            if self._current_id(self._inline_provider_combo) == "lsp":
                self._inline_model_combo.clear()
                self._inline_model_combo.addItem("Standard Python LSP", "standard")
            elif cfg:
                self._fill_inline_models(cfg)
                if cfg.inline_model_id:
                    self._set_combo_by_data(self._inline_model_combo, cfg.inline_model_id)
        finally:
            self._building = False
        self._on_inline_model_changed()

    def _on_chat_provider_changed(self) -> None:
        if self._building:
            return
        self._building = True
        try:
            cfg = self._current_provider(self._chat_provider_combo)
            if cfg:
                self._fill_chat_models(cfg)
                if cfg.chat_model_id:
                    self._set_combo_by_data(self._chat_model_combo, cfg.chat_model_id)
        finally:
            self._building = False
        self._on_chat_model_changed()

    def _on_inline_model_changed(self) -> None:
        if self._building:
            return
        pid = self._current_id(self._inline_provider_combo)
        mid = self._current_id(self._inline_model_combo)
        self._bridge.set_inline_model(pid, mid)

    def _on_chat_model_changed(self) -> None:
        if self._building:
            return
        pid = self._current_id(self._chat_provider_combo)
        mid = self._current_id(self._chat_model_combo)
        self._bridge.set_chat_model(pid, mid)

    def _on_reasoning_changed(self, text: str) -> None:
        level = "" if text == "(none)" else text
        self._bridge.set_reasoning_level(level)

    def _on_inline_refresh(self) -> None:
        pid = self._current_id(self._inline_provider_combo)
        if pid:
            self._inline_refresh_btn.setEnabled(False)
            self._store.fetch_models_async(pid)

    def _on_chat_refresh(self) -> None:
        pid = self._current_id(self._chat_provider_combo)
        if pid:
            self._chat_refresh_btn.setEnabled(False)
            self._store.fetch_models_async(pid)

    def _on_models_fetched(self, pid: str, success: bool, _error: str) -> None:
        self._inline_refresh_btn.setEnabled(True)
        self._chat_refresh_btn.setEnabled(True)
        if not success:
            return
        cfg = self._store.provider_by_id(pid)
        if cfg is None:
            return
        if self._current_id(self._inline_provider_combo) == pid:
            self._fill_inline_models(cfg)
        if self._current_id(self._chat_provider_combo) == pid:
            self._fill_chat_models(cfg)

    def _on_model_tested(self, pid: str, _mid: str, _success: bool, _error: str) -> None:
        # Refresh current dropdowns if they belong to this provider
        cfg = self._store.provider_by_id(pid)
        if cfg is None:
            return
            
        # Update provider combos (health icon might change)
        self._building = True
        try:
            for combo in [self._inline_provider_combo, self._chat_provider_combo]:
                idx = combo.findData(pid)
                if idx >= 0:
                    combo.setItemText(idx, f"{cfg.health_icon} {cfg.display_name}")
        finally:
            self._building = False

        # Update model list if visible
        if self._current_id(self._inline_provider_combo) == pid:
            self._fill_inline_models(cfg)
        if self._current_id(self._chat_provider_combo) == pid:
            self._fill_chat_models(cfg)

    def _on_providers_changed(self) -> None:
        self._populate_providers()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        """Required to support QSS backgrounds on custom QWidget subclasses."""
        opt = QtWidgets.QStyleOption()
        opt.initFrom(self)
        p = QtGui.QPainter(self)
        self.style().drawPrimitive(QtWidgets.QStyle.PrimitiveElement.PE_Widget, opt, p, self)
        super().paintEvent(event)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_combo_by_data(self, combo: QtWidgets.QComboBox, data_id: str) -> None:
        if not data_id:
            return
        idx = combo.findData(data_id)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _current_provider(self, combo: QtWidgets.QComboBox) -> ProviderConfig | None:
        pid = self._current_id(combo)
        if not pid:
            return None
        return self._store.provider_by_id(pid)

    @staticmethod
    def _current_id(combo: QtWidgets.QComboBox) -> str:
        idx = combo.currentIndex()
        if idx < 0:
            return ""
        return str(combo.itemData(idx) or "")

    @staticmethod
    def _section_label(text: str) -> QtWidgets.QLabel:
        lbl = QtWidgets.QLabel(text)
        lbl.setStyleSheet(label_qss(color=studio_dark_theme().palette.info, font_size_px=11, bold=True, margin_top_px=4))
        return lbl

    @staticmethod
    def _labeled(label: str, widget_or_layout: QtWidgets.QWidget | QtWidgets.QLayout) -> QtWidgets.QWidget:
        row = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        lbl = QtWidgets.QLabel(label)
        lbl.setStyleSheet(label_qss(color=studio_dark_theme().palette.text_muted, font_size_px=11))
        lbl.setFixedWidth(60)
        h.addWidget(lbl)
        if isinstance(widget_or_layout, QtWidgets.QWidget):
            h.addWidget(widget_or_layout, 1)
        else:
            h.addLayout(widget_or_layout, 1)
        return row

    @staticmethod
    def _labeled_pair(label: QtWidgets.QLabel, widget: QtWidgets.QWidget) -> QtWidgets.QWidget:
        row = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        label.setFixedWidth(60)
        h.addWidget(label)
        h.addWidget(widget, 1)
        return row

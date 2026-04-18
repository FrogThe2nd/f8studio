from __future__ import annotations

from qtpy import QtCore, QtWidgets

from ...ui.support.ui_icons import StudioIcon, icon_for
from ...ui.support.json_text_editor import attach_json_enhancements
from ..components.component_models import F8ComponentEntry
from .asset_graph_preview import AssetGraphPreviewPane
from .catalog_status import AssetCatalogRowState


class ComponentCatalogUiMixin:
    def _initialize_ui(self, *, node_graph: object) -> None:
        self.setWindowTitle("Components")
        self.resize(1180, 760)
        toolbar_row = self._build_toolbar()
        split = self._build_detail_split(node_graph=node_graph)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(toolbar_row)
        layout.addWidget(split, 1)

    def _build_toolbar(self) -> QtWidgets.QHBoxLayout:
        self._scope_tabs = QtWidgets.QTabBar(self)
        self._scope_tabs.addTab("Mine")
        self._scope_tabs.addTab("Community")
        self._scope_tabs.addTab("Installed")
        self._scope_tabs.currentChanged.connect(self._reload)  # type: ignore[attr-defined]

        self._account_button = QtWidgets.QToolButton(self)
        self._account_button.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self._account_button.setIcon(icon_for(self._account_button, StudioIcon.USER))
        self._account_button.setToolTip("Accounts")
        self._account_button.clicked.connect(self._on_accounts_clicked)  # type: ignore[attr-defined]

        self._search_input = QtWidgets.QLineEdit(self)
        self._search_input.setPlaceholderText("Search components")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setMinimumWidth(320)
        self._search_input.textChanged.connect(self._on_search_text_changed)  # type: ignore[attr-defined]
        self._search_input.returnPressed.connect(self._on_search_submitted)  # type: ignore[attr-defined]

        self._search_btn = QtWidgets.QPushButton(self)
        self._search_btn.setIcon(icon_for(self._search_btn, StudioIcon.CLOUD_SEARCH))
        self._search_btn.clicked.connect(self._on_search_submitted)  # type: ignore[attr-defined]
        self._search_btn.setToolTip("Search current list")
        self._search_btn.setFixedWidth(30)

        self._filter_combo = QtWidgets.QComboBox(self)
        self._filter_combo.currentIndexChanged.connect(self._on_filter_changed)  # type: ignore[attr-defined]

        self._toolbar = QtWidgets.QToolBar("Components", self)
        self._toolbar.setMovable(False)
        self._toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        self._toolbar.setIconSize(QtCore.QSize(16, 16))
        self._toolbar.addWidget(self._scope_tabs)
        self._toolbar.addSeparator()
        self._toolbar.addWidget(self._search_input)
        self._toolbar.addWidget(self._search_btn)
        self._toolbar.addWidget(self._filter_combo)
        self._toolbar.addSeparator()

        btn_add = QtWidgets.QPushButton(self)
        btn_refresh = QtWidgets.QPushButton(self)
        btn_import = QtWidgets.QPushButton(self)
        btn_export = QtWidgets.QPushButton(self)
        self._btn_install = QtWidgets.QPushButton(self)
        self._btn_upload = QtWidgets.QPushButton(self)
        self._btn_subscribe = QtWidgets.QPushButton(self)
        self._btn_copy_local = QtWidgets.QPushButton(self)
        self._btn_delete = QtWidgets.QPushButton(self)
        self._btn_edit = QtWidgets.QPushButton(self)
        self._btn_visibility = QtWidgets.QPushButton(self)
        self._btn_history = QtWidgets.QPushButton(self)
        self._btn_create = QtWidgets.QPushButton(self)

        button_specs = [
            (btn_add, StudioIcon.CIRCLE_PLUS, "Save As Component"),
            (btn_refresh, StudioIcon.REFRESH, "Refresh current list"),
            (btn_import, StudioIcon.PACKAGE_IMPORT, "Import JSON"),
            (btn_export, StudioIcon.PACKAGE_EXPORT, "Export JSON"),
        ]
        for button, icon_token, tooltip in button_specs:
            button.setIcon(icon_for(button, icon_token))
            button.setToolTip(tooltip)
            button.setText("")
            button.setFixedWidth(30)

        btn_add.clicked.connect(self._on_add_clicked)  # type: ignore[attr-defined]
        btn_refresh.clicked.connect(self._on_refresh_clicked)  # type: ignore[attr-defined]
        btn_import.clicked.connect(self._on_import_clicked)  # type: ignore[attr-defined]
        btn_export.clicked.connect(self._on_export_clicked)  # type: ignore[attr-defined]
        self._btn_install.clicked.connect(self._on_install_clicked)  # type: ignore[attr-defined]
        self._btn_upload.clicked.connect(self._on_upload_clicked)  # type: ignore[attr-defined]
        self._btn_subscribe.clicked.connect(self._on_subscribe_clicked)  # type: ignore[attr-defined]
        self._btn_copy_local.clicked.connect(self._on_copy_local_clicked)  # type: ignore[attr-defined]
        self._btn_delete.clicked.connect(self._on_delete_clicked)  # type: ignore[attr-defined]
        self._btn_edit.clicked.connect(self._on_edit_clicked)  # type: ignore[attr-defined]
        self._btn_visibility.clicked.connect(self._on_visibility_clicked)  # type: ignore[attr-defined]
        self._btn_history.clicked.connect(self._on_history_clicked)  # type: ignore[attr-defined]
        self._btn_create.clicked.connect(self._on_insert_clicked)  # type: ignore[attr-defined]

        self._configure_icon_button(self._btn_install, "Load", hidden=True)
        self._configure_icon_button(self._btn_upload, "Sync", hidden=True)
        self._configure_icon_button(self._btn_subscribe, "Subscribe", hidden=True)
        self._configure_icon_button(self._btn_copy_local, "Copy to Draft", hidden=True)
        self._configure_icon_button(self._btn_delete, "Delete", hidden=True)
        self._configure_icon_button(self._btn_edit, "Edit Metadata", hidden=True)
        self._configure_icon_button(self._btn_visibility, "Make Public", hidden=True)
        self._configure_icon_button(self._btn_history, "History", hidden=True)
        self._configure_icon_button(self._btn_create, "Create on canvas", hidden=True)

        self._toolbar.addWidget(btn_add)
        self._toolbar.addWidget(btn_import)
        self._toolbar.addWidget(btn_export)
        self._toolbar.addSeparator()
        self._toolbar.addWidget(btn_refresh)
        self._toolbar.addSeparator()
        self._toolbar.addWidget(self._btn_install)
        self._toolbar.addWidget(self._btn_upload)
        self._toolbar.addWidget(self._btn_subscribe)
        self._toolbar.addWidget(self._btn_copy_local)
        self._toolbar.addWidget(self._btn_delete)
        self._toolbar.addWidget(self._btn_edit)
        self._toolbar.addWidget(self._btn_visibility)
        self._toolbar.addWidget(self._btn_history)
        self._toolbar.addWidget(self._btn_create)

        self._btn_refresh = btn_refresh
        self._btn_add = btn_add
        self._btn_import = btn_import
        self._btn_export = btn_export

        toolbar_row = QtWidgets.QHBoxLayout()
        toolbar_row.addWidget(self._toolbar)
        toolbar_row.addStretch(1)
        toolbar_row.addWidget(self._account_button)
        return toolbar_row

    def _build_detail_split(self, *, node_graph: object) -> QtWidgets.QSplitter:
        self._list = QtWidgets.QListWidget(self)
        self._list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._list.setSpacing(3)
        self._list.setStyleSheet("QListWidget::item { border: 0; padding: 0; }")
        self._list.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.itemSelectionChanged.connect(self._on_selection_changed)  # type: ignore[attr-defined]
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)  # type: ignore[attr-defined]
        self._list.verticalScrollBar().valueChanged.connect(self._on_list_scrolled)  # type: ignore[attr-defined]
        self._list.customContextMenuRequested.connect(self._on_list_context_menu_requested)  # type: ignore[attr-defined]

        self._preview = AssetGraphPreviewPane(parent=self, host_graph=node_graph)
        self._raw = QtWidgets.QPlainTextEdit(self)
        self._raw.setReadOnly(True)
        self._raw.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        attach_json_enhancements(self._raw, read_only=True)

        self._detail_tabs = QtWidgets.QTabWidget(self)
        self._detail_tabs.addTab(self._preview, "Preview")
        self._detail_tabs.addTab(self._raw, "Raw")

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal, self)
        split.addWidget(self._list)
        split.addWidget(self._detail_tabs)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 6)
        return split

    def _configure_icon_button(self, button: QtWidgets.QPushButton, tooltip: str, *, hidden: bool = False) -> None:
        button.setToolTip(tooltip)
        button.setText("")
        button.setFixedWidth(30)
        button.setVisible(not hidden)

    def _build_list_row(self, entry: F8ComponentEntry) -> QtWidgets.QWidget:
        row_state = self._row_state_for_entry(entry)
        container = QtWidgets.QWidget(self._list)
        container.setObjectName("catalogRowCard")
        container.setStyleSheet(
            "QWidget#catalogRowCard {"
            " border: 1px solid #4b5563;"
            " border-radius: 10px;"
            " background: #20252c;"
            "}"
        )
        root = QtWidgets.QVBoxLayout(container)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        title_row = QtWidgets.QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        if row_state.subscribed:
            icon_label = QtWidgets.QLabel(container)
            icon_label.setPixmap(icon_for(container, StudioIcon.HEART_ON).pixmap(14, 14))
            icon_label.setToolTip("Subscribed")
            title_row.addWidget(icon_label)
        name_label = QtWidgets.QLabel(str(entry.record.name or ""), container)
        name_font = name_label.font()
        name_font.setBold(True)
        name_label.setFont(name_font)
        name_label.setStyleSheet("color: palette(window-text);")
        title_row.addWidget(name_label, 1)
        owner_label_text = self._owner_label_text(row_state.owner_display_name)
        if owner_label_text is not None:
            owner_label = QtWidgets.QLabel(owner_label_text, container)
            owner_label.setStyleSheet("color: palette(window-text);")
            title_row.addWidget(owner_label, 0)
        root.addLayout(title_row)

        meta_row = QtWidgets.QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(6)
        visibility_badge = self._build_visibility_badge(container, row_state)
        if visibility_badge is not None:
            meta_row.addWidget(visibility_badge, 0)
        sync_badge = self._build_sync_badge(container, row_state)
        if sync_badge is not None:
            meta_row.addWidget(sync_badge, 0)
        version_badge = self._build_version_badge(container, row_state)
        if version_badge is not None:
            meta_row.addWidget(version_badge, 0)
        meta_row.addStretch(1)
        root.addLayout(meta_row)

        if entry.record.description:
            description_label = QtWidgets.QLabel(str(entry.record.description or ""), container)
            description_label.setWordWrap(True)
            description_label.setStyleSheet("color: palette(mid);")
            root.addWidget(description_label)
        return container

    @staticmethod
    def _build_text_badge(parent: QtWidgets.QWidget, text: str) -> QtWidgets.QLabel:
        badge = QtWidgets.QLabel(str(text), parent)
        badge.setStyleSheet(
            "QLabel {"
            " border: 1px solid #596273;"
            " border-radius: 9px;"
            " padding: 1px 6px;"
            " color: #d7dde7;"
            " background: #2a3038;"
            "}"
        )
        return badge

    def _build_version_badge(
        self,
        parent: QtWidgets.QWidget,
        row_state: AssetCatalogRowState,
    ) -> QtWidgets.QLabel | None:
        version_text = row_state.compact_version_badge()
        if version_text is None:
            return None
        sync_key = row_state.sync_indicator_key()
        local_color = "#cbd5e1"
        remote_color = "#cbd5e1"
        if sync_key == "push":
            local_color = "#86efac"
            remote_color = "#94a3b8"
        elif sync_key == "pull":
            local_color = "#94a3b8"
            remote_color = "#93c5fd"
        elif sync_key == "conflict":
            local_color = "#fca5a5"
            remote_color = "#fdba74"
        badge = self._build_text_badge(parent, "")
        badge.setTextFormat(QtCore.Qt.TextFormat.RichText)
        parts: list[str] = []
        if row_state.local_version_number is not None:
            parts.append(f"<span style='color:{local_color};font-weight:600;'>L{int(row_state.local_version_number)}</span>")
        if row_state.remote_version_number is not None:
            parts.append(f"<span style='color:{remote_color};font-weight:600;'>R{int(row_state.remote_version_number)}</span>")
        badge.setText(" <span style='color:#64748b;'>|</span> ".join(parts))
        if sync_key == "push":
            badge.setToolTip("Local version is ahead of remote")
        elif sync_key == "pull":
            badge.setToolTip("Remote version is ahead of local")
        elif sync_key == "conflict":
            badge.setToolTip("Local and remote versions diverged")
        else:
            badge.setToolTip(version_text)
        return badge

    @staticmethod
    def _sync_badge_token(row_state: AssetCatalogRowState) -> StudioIcon | None:
        sync_key = row_state.sync_indicator_key()
        if sync_key == "synced":
            return StudioIcon.CHECK
        if sync_key == "push":
            return StudioIcon.CLOUD_UP
        if sync_key == "pull":
            return StudioIcon.CLOUD_DOWN
        if sync_key == "conflict":
            return StudioIcon.X
        return None

    def _build_sync_badge(
        self,
        parent: QtWidgets.QWidget,
        row_state: AssetCatalogRowState,
    ) -> QtWidgets.QLabel | None:
        token = self._sync_badge_token(row_state)
        if token is None:
            return None
        badge = self._build_text_badge(parent, "")
        badge.setPixmap(icon_for(parent, token).pixmap(12, 12))
        sync_key = row_state.sync_indicator_key()
        if sync_key == "synced":
            badge.setToolTip("Local and remote are in sync")
        elif sync_key == "push":
            badge.setToolTip("Local is ahead of remote")
        elif sync_key == "pull":
            badge.setToolTip("Remote is ahead of local")
        elif sync_key == "conflict":
            badge.setToolTip("Local and remote changed differently")
        return badge

    def _build_visibility_badge(
        self,
        parent: QtWidgets.QWidget,
        row_state: AssetCatalogRowState,
    ) -> QtWidgets.QLabel | None:
        visibility_key = row_state.visibility_icon_key()
        if visibility_key == "public":
            token = StudioIcon.PUBLIC
            tooltip = "Public"
        elif visibility_key == "private":
            token = StudioIcon.PRIVATE
            tooltip = "Private"
        else:
            return None
        badge = self._build_text_badge(parent, "")
        badge.setPixmap(icon_for(parent, token).pixmap(12, 12))
        badge.setToolTip(tooltip)
        return badge

    @classmethod
    def _owner_label_text(cls, owner_display_name: str | None) -> str | None:
        if owner_display_name is None:
            return None
        owner_text = str(owner_display_name).strip()
        if not owner_text:
            return None
        if owner_text.casefold() == cls.LOCAL_DRAFT_LABEL.casefold():
            return cls.LOCAL_DRAFT_LABEL
        return f"by {owner_text}"

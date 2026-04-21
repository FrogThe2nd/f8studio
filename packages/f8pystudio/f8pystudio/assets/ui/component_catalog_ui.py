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
        self._scope_tabs.addTab("Drafts")
        self._scope_tabs.addTab("Mine")
        self._scope_tabs.addTab("Community")
        self._scope_tabs.addTab("Installed")
        self._scope_tabs.currentChanged.connect(self._on_scope_tab_changed)  # type: ignore[attr-defined]

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

        btn_add = QtWidgets.QPushButton(self._toolbar)
        btn_refresh = QtWidgets.QPushButton(self._toolbar)
        btn_import = QtWidgets.QPushButton(self._toolbar)
        btn_export = QtWidgets.QPushButton(self._toolbar)
        self._btn_install = QtWidgets.QPushButton(self._toolbar)
        self._btn_upload = QtWidgets.QPushButton(self._toolbar)
        self._btn_subscribe = QtWidgets.QPushButton(self._toolbar)
        self._btn_copy_local = QtWidgets.QPushButton(self._toolbar)
        self._btn_delete = QtWidgets.QPushButton(self._toolbar)
        self._btn_edit = QtWidgets.QPushButton(self._toolbar)
        self._btn_visibility = QtWidgets.QPushButton(self._toolbar)
        self._btn_history = QtWidgets.QPushButton(self._toolbar)
        self._btn_create = QtWidgets.QPushButton(self._toolbar)

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
        linked_reference_text = self._linked_draft_reference_text(entry)
        linked_reference_tooltip = self._linked_draft_reference_tooltip(entry)
        linked_draft_badge_text = self._linked_draft_badge_text(entry)
        linked_draft_badge_tooltip = self._linked_draft_badge_tooltip(entry)
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
        root.setContentsMargins(10, 6, 10, 6)
        root.setSpacing(4)

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

        if linked_reference_text is not None:
            linked_label = QtWidgets.QLabel(linked_reference_text, container)
            linked_label.setStyleSheet(
                "QLabel {"
                " color: #dbeafe;"
                " font-size: 12px;"
                " font-weight: 600;"
                " background: #172033;"
                " border: 1px solid #355070;"
                " border-radius: 8px;"
                " padding: 2px 8px;"
                "}"
            )
            if linked_reference_tooltip is not None:
                linked_label.setToolTip(linked_reference_tooltip)
            root.addWidget(linked_label)

        meta_row = QtWidgets.QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(6)
        if linked_draft_badge_text is not None:
            linked_draft_badge = self._build_text_badge(container, linked_draft_badge_text)
            linked_draft_badge.setStyleSheet(
                "QLabel {"
                " border: 1px solid #1f7a5a;"
                " border-radius: 9px;"
                " padding: 1px 6px;"
                " color: #dcfce7;"
                " background: #14532d;"
                " font-weight: 600;"
                "}"
            )
            if linked_draft_badge_tooltip is not None:
                linked_draft_badge.setToolTip(linked_draft_badge_tooltip)
            meta_row.addWidget(linked_draft_badge, 0)
        visibility_badge = self._build_visibility_badge(container, row_state)
        if visibility_badge is not None:
            meta_row.addWidget(visibility_badge, 0)
        revision_badge = self._build_revision_badge(container, entry.remoteRevision)
        if revision_badge is not None:
            meta_row.addWidget(revision_badge, 0)
        meta_row.addStretch(1)
        root.addLayout(meta_row)
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

    def _build_revision_badge(self, parent: QtWidgets.QWidget, remote_revision: str | None) -> QtWidgets.QLabel | None:
        revision_text = str(remote_revision or "").strip()
        if not revision_text:
            return None
        badge = self._build_text_badge(parent, revision_text)
        badge.setToolTip(f"Remote revision: {revision_text}")
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
        if owner_text.casefold() == cls.LINKED_DRAFT_LABEL.casefold():
            return cls.LINKED_DRAFT_LABEL
        return f"by {owner_text}"

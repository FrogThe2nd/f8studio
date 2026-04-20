from __future__ import annotations

from typing import Any

from qtpy import QtCore, QtWidgets

from ...ui.support.json_text_editor import attach_json_enhancements
from ...ui.support.ui_icons import StudioIcon, icon_for
from .asset_graph_preview import AssetGraphPreviewPane


class VariantCatalogUiMixin:
    _base_node_name: str
    _is_global_mode: bool
    setWindowTitle: Any
    resize: Any
    _on_scope_tab_changed: Any
    _on_accounts_clicked: Any
    _on_search_text_changed: Any
    _on_search_submitted: Any
    _on_filter_changed: Any
    _on_node_type_filter_changed: Any
    _on_add_clicked: Any
    _on_refresh_clicked: Any
    _on_import_clicked: Any
    _on_export_clicked: Any
    _on_install_clicked: Any
    _on_upload_clicked: Any
    _on_subscribe_clicked: Any
    _on_duplicate_clicked: Any
    _on_delete_clicked: Any
    _on_edit_clicked: Any
    _on_visibility_clicked: Any
    _on_history_clicked: Any
    _on_create_clicked: Any
    _on_selection_changed: Any
    _on_item_double_clicked: Any
    _on_list_scrolled: Any
    _on_list_context_menu_requested: Any

    def _initialize_ui(self, *, node_graph: object) -> None:
        self.setWindowTitle(f"Variants - {self._base_node_name}")
        self.resize(1160, 720)
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
        self._account_button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._account_button.setIcon(icon_for(self._account_button, StudioIcon.USER))
        self._account_button.setToolTip("Accounts")
        self._account_button.clicked.connect(self._on_accounts_clicked)  # type: ignore[attr-defined]

        self._search_input = QtWidgets.QLineEdit(self)
        self._search_input.setPlaceholderText("Search variants")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setMinimumWidth(320)
        self._search_input.textChanged.connect(self._on_search_text_changed)  # type: ignore[attr-defined]
        self._search_input.returnPressed.connect(self._on_search_submitted)  # type: ignore[attr-defined]

        self._search_btn = QtWidgets.QPushButton("Search", self)
        self._search_btn.setIcon(icon_for(self._search_btn, StudioIcon.CLOUD_SEARCH))
        self._search_btn.setToolTip("Search")
        self._search_btn.setText("")
        self._search_btn.setFixedWidth(30)
        self._search_btn.clicked.connect(self._on_search_submitted)  # type: ignore[attr-defined]

        self._filter_combo = QtWidgets.QComboBox(self)
        self._filter_combo.currentIndexChanged.connect(self._on_filter_changed)  # type: ignore[attr-defined]

        self._node_type_combo = QtWidgets.QComboBox(self)
        self._node_type_combo.currentIndexChanged.connect(self._on_node_type_filter_changed)  # type: ignore[attr-defined]
        if self._is_global_mode:
            self._node_type_combo.setMinimumWidth(150)
            self._node_type_combo.setToolTip("Filter by node type")

        self._toolbar = QtWidgets.QToolBar("Variants", self)
        self._toolbar.setMovable(False)
        self._toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._toolbar.setIconSize(QtCore.QSize(16, 16))
        self._toolbar.addWidget(self._scope_tabs)
        self._toolbar.addSeparator()
        if self._is_global_mode:
            self._toolbar.addWidget(QtWidgets.QLabel("Node Type:", self))
            self._toolbar.addWidget(self._node_type_combo)
            self._toolbar.addSeparator()
        self._toolbar.addWidget(self._search_input)
        self._toolbar.addWidget(self._search_btn)
        self._toolbar.addWidget(self._filter_combo)
        self._toolbar.addSeparator()

        btn_add = QtWidgets.QPushButton("Save From Selected Node", self._toolbar)
        btn_refresh = QtWidgets.QPushButton("Refresh", self._toolbar)
        btn_import = QtWidgets.QPushButton("Import...", self._toolbar)
        btn_export = QtWidgets.QPushButton("Export...", self._toolbar)
        self._btn_install = QtWidgets.QPushButton(self._toolbar)
        self._btn_upload = QtWidgets.QPushButton(self._toolbar)
        self._btn_subscribe = QtWidgets.QPushButton(self._toolbar)
        self._btn_copy_local = QtWidgets.QPushButton(self._toolbar)
        self._btn_delete = QtWidgets.QPushButton(self._toolbar)
        self._btn_edit = QtWidgets.QPushButton(self._toolbar)
        self._btn_visibility = QtWidgets.QPushButton(self._toolbar)
        self._btn_history = QtWidgets.QPushButton(self._toolbar)
        self._btn_create = QtWidgets.QPushButton(self._toolbar)

        btn_refresh.setIcon(icon_for(btn_refresh, StudioIcon.REFRESH))
        btn_add.setIcon(icon_for(btn_add, StudioIcon.CIRCLE_PLUS))
        btn_add.setText("")
        btn_import.setIcon(icon_for(btn_import, StudioIcon.PACKAGE_IMPORT))
        btn_export.setIcon(icon_for(btn_export, StudioIcon.PACKAGE_EXPORT))

        btn_add.clicked.connect(self._on_add_clicked)  # type: ignore[attr-defined]
        btn_refresh.clicked.connect(self._on_refresh_clicked)  # type: ignore[attr-defined]
        btn_import.clicked.connect(self._on_import_clicked)  # type: ignore[attr-defined]
        btn_export.clicked.connect(self._on_export_clicked)  # type: ignore[attr-defined]
        self._btn_install.clicked.connect(self._on_install_clicked)  # type: ignore[attr-defined]
        self._btn_upload.clicked.connect(self._on_upload_clicked)  # type: ignore[attr-defined]
        self._btn_subscribe.clicked.connect(self._on_subscribe_clicked)  # type: ignore[attr-defined]
        self._btn_copy_local.clicked.connect(self._on_duplicate_clicked)  # type: ignore[attr-defined]
        self._btn_delete.clicked.connect(self._on_delete_clicked)  # type: ignore[attr-defined]
        self._btn_edit.clicked.connect(self._on_edit_clicked)  # type: ignore[attr-defined]
        self._btn_visibility.clicked.connect(self._on_visibility_clicked)  # type: ignore[attr-defined]
        self._btn_history.clicked.connect(self._on_history_clicked)  # type: ignore[attr-defined]
        self._btn_create.clicked.connect(self._on_create_clicked)  # type: ignore[attr-defined]

        self._configure_icon_button(self._btn_install, "Load", hidden=True)
        self._configure_icon_button(self._btn_upload, "Sync", hidden=True)
        self._configure_icon_button(self._btn_subscribe, "Subscribe", hidden=True)
        self._configure_icon_button(self._btn_copy_local, "Copy to Draft", hidden=True)
        self._configure_icon_button(self._btn_delete, "Delete", hidden=True)
        self._configure_icon_button(self._btn_edit, "Edit Metadata", hidden=True)
        self._configure_icon_button(self._btn_visibility, "Make Public", hidden=True)
        self._configure_icon_button(self._btn_history, "History", hidden=True)
        self._configure_icon_button(self._btn_create, "Create on canvas", hidden=True)
        self._configure_icon_button(btn_refresh, "Refresh current list")
        self._configure_icon_button(btn_import, "Import")
        self._configure_icon_button(btn_export, "Export")
        self._configure_icon_button(btn_add, "Save From Selected Node")

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
        self._list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
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
        self._raw.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        attach_json_enhancements(self._raw, read_only=True)

        self._detail_tabs = QtWidgets.QTabWidget(self)
        self._detail_tabs.addTab(self._preview, "Preview")
        self._detail_tabs.addTab(self._raw, "Raw")

        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal, self)
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

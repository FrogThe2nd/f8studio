from __future__ import annotations

import json
import logging
from typing import Any

from qtpy import QtCore, QtWidgets

from f8pystudio.modding import ModdingAutomationService

logger = logging.getLogger(__name__)
_DIALOG_ERRORS = (FileNotFoundError, OSError, RuntimeError, TypeError, ValueError)


class GameModdingDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Game Modding")
        self.resize(900, 720)
        self._service = ModdingAutomationService()
        self._detection: dict[str, Any] | None = None
        self._plan: dict[str, Any] | None = None
        self._install: dict[str, Any] | None = None
        self._verification: dict[str, Any] | None = None
        self._build_ui()
        self._refresh_action_state()

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        target_row = QtWidgets.QHBoxLayout()
        self._target_edit = QtWidgets.QLineEdit(self)
        self._target_edit.setPlaceholderText("Game .exe or root folder")
        browse_button = QtWidgets.QToolButton(self)
        browse_button.setText("...")
        browse_button.clicked.connect(self._browse_target)  # type: ignore[attr-defined]
        target_row.addWidget(self._target_edit, 1)
        target_row.addWidget(browse_button)
        root.addLayout(target_row)

        options_group = QtWidgets.QGroupBox("Options", self)
        options_layout = QtWidgets.QGridLayout(options_group)
        self._exporter_combo = QtWidgets.QComboBox(options_group)
        self._exporter_combo.addItems(["auto", "skeleton", "live2d"])
        self._port_spin = QtWidgets.QSpinBox(options_group)
        self._port_spin.setRange(1, 65535)
        self._port_spin.setValue(39540)
        self._rue_check = QtWidgets.QCheckBox("RuntimeUnityEditor", options_group)
        self._cue_check = QtWidgets.QCheckBox("CinematicUnityExplorer", options_group)
        self._config_manager_check = QtWidgets.QCheckBox("ConfigurationManager", options_group)
        self._uud_check = QtWidgets.QCheckBox("UniversalUnityDemosaics", options_group)
        self._offline_check = QtWidgets.QCheckBox("Offline/cache only", options_group)
        self._force_check = QtWidgets.QCheckBox("Force reinstall", options_group)
        options_layout.addWidget(QtWidgets.QLabel("Exporter", options_group), 0, 0)
        options_layout.addWidget(self._exporter_combo, 0, 1)
        options_layout.addWidget(QtWidgets.QLabel("UDP port", options_group), 0, 2)
        options_layout.addWidget(self._port_spin, 0, 3)
        options_layout.addWidget(self._rue_check, 1, 0)
        options_layout.addWidget(self._cue_check, 1, 1)
        options_layout.addWidget(self._config_manager_check, 1, 2)
        options_layout.addWidget(self._uud_check, 1, 3)
        options_layout.addWidget(self._offline_check, 2, 0)
        options_layout.addWidget(self._force_check, 2, 1)
        root.addWidget(options_group)

        button_row = QtWidgets.QHBoxLayout()
        self._detect_button = QtWidgets.QPushButton("Detect", self)
        self._preview_button = QtWidgets.QPushButton("Preview Install", self)
        self._apply_button = QtWidgets.QPushButton("Apply Install", self)
        self._verify_button = QtWidgets.QPushButton("Verify Stream", self)
        self._save_button = QtWidgets.QPushButton("Save Recipe", self)
        self._detect_button.clicked.connect(self._detect_target)  # type: ignore[attr-defined]
        self._preview_button.clicked.connect(self._preview_install)  # type: ignore[attr-defined]
        self._apply_button.clicked.connect(self._apply_install)  # type: ignore[attr-defined]
        self._verify_button.clicked.connect(self._verify_stream)  # type: ignore[attr-defined]
        self._save_button.clicked.connect(self._save_recipe)  # type: ignore[attr-defined]
        for button in (
            self._detect_button,
            self._preview_button,
            self._apply_button,
            self._verify_button,
            self._save_button,
        ):
            button_row.addWidget(button)
        button_row.addStretch(1)
        root.addLayout(button_row)

        self._status_label = QtWidgets.QLabel("", self)
        self._status_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self._status_label)

        self._tabs = QtWidgets.QTabWidget(self)
        self._detection_text = _readonly_text(self._tabs)
        self._preview_text = _readonly_text(self._tabs)
        self._verification_text = _readonly_text(self._tabs)
        self._recipe_text = _readonly_text(self._tabs)
        self._tabs.addTab(self._detection_text, "Detection")
        self._tabs.addTab(self._preview_text, "Install Preview")
        self._tabs.addTab(self._verification_text, "Verification")
        self._tabs.addTab(self._recipe_text, "Recipe")
        root.addWidget(self._tabs, 1)

        close_row = QtWidgets.QHBoxLayout()
        close_row.addStretch(1)
        close_button = QtWidgets.QPushButton("Close", self)
        close_button.clicked.connect(self.accept)  # type: ignore[attr-defined]
        close_row.addWidget(close_button)
        root.addLayout(close_row)

    @QtCore.Slot()
    def _browse_target(self) -> None:
        file_path, _filter = QtWidgets.QFileDialog.getOpenFileName(self, "Select Game Executable", "", "Executables (*.exe);;All Files (*)")
        if file_path:
            self._target_edit.setText(file_path)
            return
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Game Root")
        if directory:
            self._target_edit.setText(directory)

    @QtCore.Slot()
    def _detect_target(self) -> None:
        try:
            result = self._service.detect_target(target_path=self._target_path())
        except _DIALOG_ERRORS as exc:
            self._report_error("Detection failed", exc)
            return
        self._detection = dict(result)
        self._detection_text.setPlainText(_pretty_json(result))
        self._status_label.setText("Detection complete.")
        self._tabs.setCurrentWidget(self._detection_text)
        self._refresh_action_state()

    @QtCore.Slot()
    def _preview_install(self) -> None:
        try:
            result = self._service.preview_install(target_path=self._target_path(), options_payload=self._options_payload())
        except _DIALOG_ERRORS as exc:
            self._report_error("Preview failed", exc)
            return
        self._plan = dict(result.get("plan") if isinstance(result.get("plan"), dict) else result)
        self._preview_text.setPlainText(_pretty_json(result))
        self._status_label.setText("Install preview ready. Review writes before applying.")
        self._tabs.setCurrentWidget(self._preview_text)
        self._refresh_action_state()

    @QtCore.Slot()
    def _apply_install(self) -> None:
        plan = self._plan
        if plan is None:
            return
        writes = plan.get("filesToCreateOrUpdate") if isinstance(plan.get("filesToCreateOrUpdate"), list) else []
        message = "Apply the previewed install plan to the game directory?"
        if writes:
            message += "\n\nWrites:\n" + "\n".join(str(item) for item in writes[:12])
        if QtWidgets.QMessageBox.question(self, "Apply Install", message) != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            result = self._service.apply_install(plan_payload=plan, confirm=True)
        except _DIALOG_ERRORS as exc:
            self._report_error("Install failed", exc)
            return
        self._install = dict(result)
        self._preview_text.setPlainText(_pretty_json(result))
        self._status_label.setText("Install complete. Start the game, then verify the stream.")
        self._tabs.setCurrentWidget(self._preview_text)
        self._refresh_action_state()

    @QtCore.Slot()
    def _verify_stream(self) -> None:
        try:
            result = self._service.verify_stream(port=int(self._port_spin.value()), timeout_s=3.0)
        except _DIALOG_ERRORS as exc:
            self._report_error("Verification failed", exc)
            return
        self._verification = dict(result)
        self._verification_text.setPlainText(_pretty_json(result))
        self._status_label.setText("Verification finished.")
        self._tabs.setCurrentWidget(self._verification_text)
        self._refresh_action_state()

    @QtCore.Slot()
    def _save_recipe(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, "Save Recipe", "Recipe name")
        if not ok:
            return
        try:
            result = self._service.create_recipe(
                name=str(name or "").strip() or "Game Modding Recipe",
                description="",
                tags=["modding"],
                detection_payload=self._detection,
                install_payload=self._install,
                verification_payload=self._verification,
                graph_payload={},
                notes="",
                confirm=True,
            )
        except _DIALOG_ERRORS as exc:
            self._report_error("Save recipe failed", exc)
            return
        self._recipe_text.setPlainText(_pretty_json(result))
        self._status_label.setText("Recipe draft saved.")
        self._tabs.setCurrentWidget(self._recipe_text)
        self._refresh_action_state()

    def _refresh_action_state(self) -> None:
        target_ready = bool(str(self._target_edit.text() or "").strip())
        self._detect_button.setEnabled(target_ready)
        self._preview_button.setEnabled(target_ready)
        self._apply_button.setEnabled(self._plan is not None)
        self._verify_button.setEnabled(True)
        self._save_button.setEnabled(self._detection is not None or self._install is not None or self._verification is not None)

    def _target_path(self) -> str:
        target = str(self._target_edit.text() or "").strip()
        if not target:
            raise ValueError("target path is required")
        return target

    def _options_payload(self) -> dict[str, Any]:
        return {
            "exporter": str(self._exporter_combo.currentText() or "auto"),
            "udpPort": int(self._port_spin.value()),
            "installRuntimeUnityEditor": bool(self._rue_check.isChecked()),
            "installCinematicUnityExplorer": bool(self._cue_check.isChecked()),
            "installConfigurationManager": bool(self._config_manager_check.isChecked()),
            "installUniversalUnityDemosaics": bool(self._uud_check.isChecked()),
            "offline": bool(self._offline_check.isChecked()),
            "forceReinstall": bool(self._force_check.isChecked()),
        }

    def _report_error(self, title: str, exc: BaseException) -> None:
        logger.exception("%s", title)
        self._status_label.setText(f"{title}: {type(exc).__name__}: {exc}")
        QtWidgets.QMessageBox.warning(self, title, f"{type(exc).__name__}: {exc}")


def _readonly_text(parent: QtWidgets.QWidget) -> QtWidgets.QPlainTextEdit:
    widget = QtWidgets.QPlainTextEdit(parent)
    widget.setReadOnly(True)
    widget.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
    return widget


def _pretty_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


__all__ = ["GameModdingDialog"]

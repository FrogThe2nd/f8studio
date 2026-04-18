from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from qtpy import QtCore, QtGui, QtWidgets

from f8pystudio.diagnostics.error_reporting import ExceptionLogOnce, report_exception
from ...ui.support.ui_notifications import show_warning


@dataclass(frozen=True)
class _Rule:
    pattern: re.Pattern[str]
    fmt: QtGui.QTextCharFormat


@dataclass(frozen=True)
class _LogEntry:
    line: str
    level: int


_BRACKET_LEVEL_RE = re.compile(r"\[(trace|debug|info|warn|warning|error|critical|fatal)\]", re.IGNORECASE)
_PREFIX_LEVEL_RE = re.compile(r"^\s*\[?(trace|debug|info|warn|warning|error|critical|fatal)\]?\b", re.IGNORECASE)
_TRACEBACK_LEVEL_RE = re.compile(
    r"(Traceback \(most recent call last\):|^\s*File \"[^\"]+\", line \d+|\b[A-Za-z_]*Error:|\bException:)",
    re.IGNORECASE,
)


def _normalize_log_level(level: int) -> int:
    normalized = int(level)
    if normalized <= logging.DEBUG:
        return logging.DEBUG
    if normalized <= logging.INFO:
        return logging.INFO
    if normalized <= logging.WARNING:
        return logging.WARNING
    if normalized <= logging.ERROR:
        return logging.ERROR
    return logging.CRITICAL


def _level_value_from_name(level_name: str) -> int | None:
    normalized = str(level_name or "").strip().upper()
    if normalized == "TRACE":
        return logging.DEBUG
    if normalized == "DEBUG":
        return logging.DEBUG
    if normalized == "INFO":
        return logging.INFO
    if normalized in {"WARN", "WARNING"}:
        return logging.WARNING
    if normalized == "ERROR":
        return logging.ERROR
    if normalized in {"CRITICAL", "FATAL"}:
        return logging.CRITICAL
    return None


def _infer_log_level(line: str) -> int:
    text = str(line or "").strip()
    if not text:
        return logging.INFO

    bracket_match = _BRACKET_LEVEL_RE.search(text)
    if bracket_match is not None:
        resolved = _level_value_from_name(bracket_match.group(1))
        if resolved is not None:
            return resolved

    prefix_match = _PREFIX_LEVEL_RE.search(text)
    if prefix_match is not None:
        resolved = _level_value_from_name(prefix_match.group(1))
        if resolved is not None:
            return resolved

    if _TRACEBACK_LEVEL_RE.search(text) is not None:
        return logging.ERROR

    return logging.INFO


class _LogHighlighter(QtGui.QSyntaxHighlighter):
    def __init__(self, doc: QtGui.QTextDocument) -> None:
        super().__init__(doc)
        self._rules: list[_Rule] = []

        def f(color: str, *, bold: bool = False) -> QtGui.QTextCharFormat:
            fmt = QtGui.QTextCharFormat()
            fmt.setForeground(QtGui.QColor(color))
            if bold:
                fmt.setFontWeight(QtGui.QFont.Weight.Bold)
            return fmt

        # Rough VSCode-like colors.
        self._rules.extend(
            [
                _Rule(re.compile(r"\b(ERROR|FATAL)\b"), f("#f44747", bold=True)),
                _Rule(re.compile(r"\b(WARN|WARNING)\b"), f("#cca700", bold=True)),
                _Rule(re.compile(r"\b(INFO)\b"), f("#4fc1ff")),
                _Rule(re.compile(r"\b(DEBUG|TRACE)\b"), f("#b5cea8")),
                _Rule(re.compile(r"\b(Traceback \(most recent call last\):)\b"), f("#dcdcaa", bold=True)),
                _Rule(re.compile(r"\b(Exception|Error|AssertionError|ValueError|TypeError|KeyError)\b"), f("#ff7b72")),
                _Rule(re.compile(r"File \"[^\"]+\", line \d+"), f("#9cdcfe")),
            ]
        )

    def highlightBlock(self, text: str) -> None:  # noqa: N802 (Qt naming)
        for rule in self._rules:
            for m in rule.pattern.finditer(text):
                start, end = m.span()
                self.setFormat(start, end - start, rule.fmt)


class ServiceLogView(QtWidgets.QPlainTextEdit):
    """
    Read-only log view with a dark theme and basic keyword highlighting.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._entries: list[_LogEntry] = []
        self._minimum_level = _normalize_log_level(logging.getLogger().getEffectiveLevel())
        self.setReadOnly(True)
        # Terminal-like: wrap at widget width (avoid horizontal scrollbar).
        self.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
        try:
            self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        except (AttributeError, RuntimeError, TypeError):
            pass
        self.setMaximumBlockCount(5000)

        font = QtGui.QFont("Consolas")
        font.setStyleHint(QtGui.QFont.Monospace)
        font.setPointSize(10)
        self.setFont(font)

        # Dark terminal-ish palette.
        pal = self.palette()
        pal.setColor(QtGui.QPalette.Base, QtGui.QColor("#1e1e1e"))
        pal.setColor(QtGui.QPalette.Text, QtGui.QColor("#d4d4d4"))
        self.setPalette(pal)

        self._highlighter = _LogHighlighter(self.document())

    def set_minimum_level(self, level: int) -> None:
        normalized_level = _normalize_log_level(level)
        if normalized_level == self._minimum_level:
            return
        self._minimum_level = normalized_level
        self._rerender()

    def append_line(self, line: str) -> None:
        line = str(line or "")
        # Avoid double newlines.
        if line.endswith("\n"):
            line = line[:-1]
        entry = _LogEntry(line=line, level=_infer_log_level(line))
        self._entries.append(entry)
        if len(self._entries) > self.maximumBlockCount():
            self._entries = self._entries[-self.maximumBlockCount() :]
        if entry.level < self._minimum_level:
            return
        self.appendPlainText(line)

    def _rerender(self) -> None:
        visible_lines = [entry.line for entry in self._entries if entry.level >= self._minimum_level]
        self.setPlainText("\n".join(visible_lines))


class ServiceLogDock(QtWidgets.QDockWidget):
    """
    Dock widget with per-service log tabs.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__("Service Logs", parent)
        self.setObjectName("ServiceLogsDock")
        self._minimum_level = _normalize_log_level(logging.getLogger().getEffectiveLevel())

        self._tabs = QtWidgets.QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.setMovable(True)
        self._tabs.setTabsClosable(False)
        self.setWidget(self._tabs)
        tab_bar = self._tabs.tabBar()
        if tab_bar is not None:
            tab_bar.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        try:
            if tab_bar is not None:
                tab_bar.customContextMenuRequested.connect(self._on_tab_context_menu)  # type: ignore[attr-defined]
        except (AttributeError, RuntimeError, TypeError):
            pass

        self._views: dict[str, ServiceLogView] = {}
        self._service_names: dict[str, str] = {}  # serviceId -> serviceName/serviceClass
        self._exception_log_once = ExceptionLogOnce()
        self._last_save_dir = str(Path.home())

    def set_service_name(self, service_id: str, service_name: str) -> None:
        sid = str(service_id or "").strip()
        if not sid:
            return
        name = str(service_name or "").strip()
        if name:
            self._service_names[sid] = name
        else:
            self._service_names.pop(sid, None)
        view = self._views.get(sid)
        if view is None:
            return
        try:
            idx = self._tabs.indexOf(view)
        except (AttributeError, RuntimeError, TypeError):
            idx = -1
        if idx >= 0:
            try:
                self._tabs.setTabText(idx, self._tab_label(sid))
            except (AttributeError, RuntimeError, TypeError):
                pass

    def _tab_label(self, service_id: str) -> str:
        sid = str(service_id or "").strip() or "unknown"
        name = str(self._service_names.get(sid, "") or "").strip()
        if name:
            return f"{name}[{sid}]"
        return sid

    def _ensure_tab(self, service_id: str) -> ServiceLogView:
        sid = str(service_id or "").strip() or "unknown"
        view = self._views.get(sid)
        if view is not None:
            return view
        view = ServiceLogView()
        view.set_minimum_level(self._minimum_level)
        view.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        try:
            view.customContextMenuRequested.connect(lambda pos, service_id=sid: self._on_view_context_menu(service_id, pos))  # type: ignore[attr-defined]
        except (AttributeError, RuntimeError, TypeError):
            pass
        self._views[sid] = view
        self._tabs.addTab(view, self._tab_label(sid))
        return view

    def set_minimum_level(self, level: int) -> None:
        self._minimum_level = _normalize_log_level(level)
        for view in self._views.values():
            view.set_minimum_level(self._minimum_level)

    @QtCore.Slot(str, str)
    def append(self, service_id: str, line: str) -> None:
        view = self._ensure_tab(service_id)
        view.append_line(line)

    def report_exception(self, service_id: str, context: str, exc: BaseException, *, level: str = "ERROR") -> None:
        sid = str(service_id or "").strip() or "studio"

        def _emit(line: str) -> None:
            self.append(sid, line)

        report_exception(
            _emit,
            context=str(context or "").strip(),
            exc=exc,
            level=level,
            log_once=self._exception_log_once,
        )

    def close_service_tab(self, service_id: str) -> None:
        sid = str(service_id or "").strip()
        if not sid:
            return
        view = self._views.pop(sid, None)
        if view is None:
            return
        try:
            idx = self._tabs.indexOf(view)
        except (AttributeError, RuntimeError, TypeError):
            idx = -1
        if idx >= 0:
            try:
                self._tabs.removeTab(idx)
            except (AttributeError, RuntimeError, TypeError):
                pass
        try:
            view.deleteLater()
        except (AttributeError, RuntimeError, TypeError):
            pass

    def _service_id_for_view(self, view: ServiceLogView) -> str | None:
        for service_id, candidate in self._views.items():
            if candidate is view:
                return service_id
        return None

    def _on_view_context_menu(self, service_id: str, pos: QtCore.QPoint) -> None:
        sid = str(service_id or "").strip() or "unknown"
        view = self._views.get(sid)
        if view is None:
            return
        menu = QtWidgets.QMenu(view)
        act_clear = menu.addAction("Clear")
        act_save = menu.addAction("Save Log As...")
        chosen = menu.exec(view.mapToGlobal(pos))
        if chosen is act_clear:
            try:
                view.clear()
            except (AttributeError, RuntimeError, TypeError):
                pass
            return
        if chosen is act_save:
            self._save_view_to_file(view, sid)

    def _on_tab_context_menu(self, pos: QtCore.QPoint) -> None:
        tab_bar = self._tabs.tabBar()
        if tab_bar is None:
            return
        tab_index = tab_bar.tabAt(pos)
        if tab_index < 0:
            return
        w = self._tabs.widget(tab_index)
        if not isinstance(w, ServiceLogView):
            return
        sid = self._service_id_for_view(w)
        if not sid:
            return
        menu = QtWidgets.QMenu(self._tabs)
        act_close = menu.addAction("Close Tab")
        chosen = menu.exec(tab_bar.mapToGlobal(pos))
        if chosen is act_close:
            self.close_service_tab(sid)

    def _save_view_to_file(self, view: ServiceLogView, service_id: str) -> None:
        sid = str(service_id or "").strip() or "unknown"
        stamp = QtCore.QDateTime.currentDateTime().toString("yyyyMMdd-HHmmss")
        default_name = f"{sid}-{stamp}.log"
        start_dir = str(self._last_save_dir or "")
        start_path = str(Path(start_dir) / default_name) if start_dir else default_name
        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save Service Log",
            start_path,
            "Log Files (*.log *.txt);;Text Files (*.txt);;All Files (*)",
        )
        path = str(file_path or "").strip()
        if not path:
            return
        try:
            p = Path(path).expanduser().resolve()
            p.write_text(view.toPlainText(), encoding="utf-8")
            self._last_save_dir = str(p.parent)
        except Exception as exc:
            show_warning(self, "Save failed", f"Failed to save log file:\n{path}\n\n{exc}")

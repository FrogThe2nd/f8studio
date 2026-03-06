from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from qtpy import QtCore, QtGui, QtWidgets

_BRACKET_MARK_PROPERTY = int(QtGui.QTextFormat.UserProperty) + 101


@dataclass(frozen=True)
class BracketMatch:
    left: int
    right: int


def _rainbow_bracket_colors(palette: QtGui.QPalette) -> list[QtGui.QColor]:
    base = palette.color(QtGui.QPalette.ColorRole.Highlight)
    hue = int(base.hue()) if int(base.hue()) >= 0 else 210
    sat = max(90, int(base.saturation()) or 140)
    val = max(120, int(base.value()) or 200)
    colors: list[QtGui.QColor] = []
    for offset in (0, 35, 70, 105, 140, 175):
        colors.append(QtGui.QColor.fromHsv((hue + offset) % 360, sat, val, 235))
    return colors


def _token_colors(palette: QtGui.QPalette) -> dict[str, QtGui.QColor]:
    bg = palette.color(QtGui.QPalette.ColorRole.Base)
    dark_bg = int(bg.lightness()) < 128
    if dark_bg:
        return {
            "key": QtGui.QColor("#7CC7FF"),
            "string": QtGui.QColor("#95E6A1"),
            "number": QtGui.QColor("#FFCB6B"),
            "literal": QtGui.QColor("#C792EA"),
            "punct": QtGui.QColor("#9AA5B1"),
        }
    return {
        "key": QtGui.QColor("#0B5CAD"),
        "string": QtGui.QColor("#2E7D32"),
        "number": QtGui.QColor("#B45309"),
        "literal": QtGui.QColor("#7C3AED"),
        "punct": QtGui.QColor("#5B6770"),
    }


def _is_escaped(text: str, index: int) -> bool:
    backslashes = 0
    pos = int(index) - 1
    while pos >= 0 and text[pos] == "\\":
        backslashes += 1
        pos -= 1
    return (backslashes % 2) == 1


def build_bracket_pair_map(text: str) -> dict[int, int]:
    pairs: dict[int, int] = {}
    stack: list[int] = []
    in_string = False
    for index, ch in enumerate(text):
        if ch == '"' and not _is_escaped(text, index):
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            stack.append(index)
            continue
        if ch not in "}]":
            continue
        if not stack:
            continue
        opener_index = stack.pop()
        opener = text[opener_index]
        if (opener == "{" and ch == "}") or (opener == "[" and ch == "]"):
            pairs[opener_index] = index
            pairs[index] = opener_index
    return pairs


def find_bracket_match(text: str, cursor_pos: int) -> BracketMatch | None:
    if not text:
        return None
    pos = int(cursor_pos)
    candidate_indices: list[int] = []
    if 0 <= pos < len(text):
        candidate_indices.append(pos)
    if 0 <= pos - 1 < len(text):
        candidate_indices.append(pos - 1)

    pairs = build_bracket_pair_map(text)
    for idx in candidate_indices:
        if idx not in pairs:
            continue
        peer = int(pairs[idx])
        if idx <= peer:
            return BracketMatch(left=idx, right=peer)
        return BracketMatch(left=peer, right=idx)
    return None


def compute_line_end_depth(line_text: str, start_depth: int) -> int:
    depth = max(0, int(start_depth))
    in_string = False
    for index, ch in enumerate(line_text):
        if ch == '"' and not _is_escaped(line_text, index):
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            depth += 1
            continue
        if ch in "}]":
            depth = max(0, depth - 1)
    return depth


class JsonSyntaxHighlighter(QtGui.QSyntaxHighlighter):
    def __init__(self, doc: QtGui.QTextDocument, *, palette: QtGui.QPalette) -> None:
        super().__init__(doc)
        colors = _token_colors(palette)
        self._rules: list[tuple[QtCore.QRegularExpression, QtGui.QTextCharFormat]] = [
            (
                QtCore.QRegularExpression(r'"([^"\\]|\\.)*"'),
                self._fmt(fg=colors["string"]),
            ),
            (
                QtCore.QRegularExpression(r'"([^"\\]|\\.)*"(?=\s*:)'),
                self._fmt(fg=colors["key"], bold=True),
            ),
            (
                QtCore.QRegularExpression(r"\b-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?\b"),
                self._fmt(fg=colors["number"]),
            ),
            (
                QtCore.QRegularExpression(r"\b(true|false|null)\b"),
                self._fmt(fg=colors["literal"], bold=True),
            ),
            (
                QtCore.QRegularExpression(r"[:,]"),
                self._fmt(fg=colors["punct"]),
            ),
        ]
        self._bracket_formats = [self._fmt(fg=c, bold=True) for c in _rainbow_bracket_colors(palette)]

    @staticmethod
    def _fmt(*, fg: QtGui.QColor | None = None, bold: bool = False) -> QtGui.QTextCharFormat:
        fmt = QtGui.QTextCharFormat()
        if fg is not None:
            fmt.setForeground(fg)
        if bold:
            fmt.setFontWeight(QtGui.QFont.Weight.Bold)
        return fmt

    def _format_bracket(self, depth: int) -> QtGui.QTextCharFormat:
        if not self._bracket_formats:
            return QtGui.QTextCharFormat()
        idx = max(0, int(depth)) % len(self._bracket_formats)
        return self._bracket_formats[idx]

    def highlightBlock(self, text: str) -> None:
        for regex, fmt in self._rules:
            it = regex.globalMatch(text)
            while it.hasNext():
                match = it.next()
                start = int(match.capturedStart())
                length = int(match.capturedLength())
                if start >= 0 and length > 0:
                    self.setFormat(start, length, fmt)

        prev_state = int(self.previousBlockState())
        depth = prev_state if prev_state >= 0 else 0
        in_string = False
        for index, ch in enumerate(text):
            if ch == '"' and not _is_escaped(text, index):
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch in "{[":
                self.setFormat(index, 1, self._format_bracket(depth))
                depth += 1
                continue
            if ch in "}]":
                depth = max(0, depth - 1)
                self.setFormat(index, 1, self._format_bracket(depth))

        self.setCurrentBlockState(int(depth))


class BracketPairController(QtCore.QObject):
    def __init__(self, editor: QtWidgets.QPlainTextEdit | QtWidgets.QTextEdit) -> None:
        super().__init__(editor)
        self._editor = editor
        highlight = editor.palette().color(QtGui.QPalette.ColorRole.Highlight)
        self._match_background = QtGui.QColor(highlight.red(), highlight.green(), highlight.blue(), 70)
        self._match_foreground = editor.palette().color(QtGui.QPalette.ColorRole.Text)
        editor.cursorPositionChanged.connect(self._update_extra_selections)  # type: ignore[attr-defined]
        editor.textChanged.connect(self._update_extra_selections)  # type: ignore[attr-defined]
        self._update_extra_selections()

    def _make_selection(self, pos: int) -> QtWidgets.QTextEdit.ExtraSelection:
        selection = QtWidgets.QTextEdit.ExtraSelection()
        cursor = self._editor.textCursor()
        cursor.setPosition(int(pos))
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.NextCharacter, QtGui.QTextCursor.MoveMode.KeepAnchor)
        fmt = QtGui.QTextCharFormat()
        fmt.setBackground(self._match_background)
        fmt.setForeground(self._match_foreground)
        fmt.setProperty(_BRACKET_MARK_PROPERTY, True)
        selection.cursor = cursor
        selection.format = fmt
        return selection

    @QtCore.Slot()
    def _update_extra_selections(self) -> None:
        text = self._editor.toPlainText()
        cursor_pos = int(self._editor.textCursor().position())
        match = find_bracket_match(text, cursor_pos)

        preserved: list[QtWidgets.QTextEdit.ExtraSelection] = []
        for selection in list(self._editor.extraSelections()):
            if bool(selection.format.property(_BRACKET_MARK_PROPERTY)):
                continue
            preserved.append(selection)

        if match is None:
            self._editor.setExtraSelections(preserved)
            return

        preserved.append(self._make_selection(match.left))
        preserved.append(self._make_selection(match.right))
        self._editor.setExtraSelections(preserved)


def attach_json_enhancements(
    editor: QtWidgets.QPlainTextEdit | QtWidgets.QTextEdit,
    *,
    read_only: bool = False,
) -> None:
    fixed_font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
    editor.setFont(fixed_font)
    fm = QtGui.QFontMetricsF(editor.font())
    editor.setTabStopDistance(float(fm.horizontalAdvance(" ") * 4.0))
    if isinstance(editor, QtWidgets.QPlainTextEdit):
        editor.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
    else:
        editor.setLineWrapMode(QtWidgets.QTextEdit.LineWrapMode.NoWrap)
    editor.setReadOnly(bool(read_only))

    highlighter = JsonSyntaxHighlighter(editor.document(), palette=editor.palette())
    pair_controller = BracketPairController(editor)

    editor._f8_json_highlighter = highlighter  # type: ignore[attr-defined]
    editor._f8_json_bracket_pair_controller = pair_controller  # type: ignore[attr-defined]

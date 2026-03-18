from __future__ import annotations

from types import SimpleNamespace

from f8pysdk import F8ServiceSpec
from qtpy import QtGui, QtWidgets

from f8pystudio.widgets.json_text_editor import (
    BracketMatch,
    attach_json_enhancements,
    compute_line_end_depth,
    find_bracket_match,
)
from f8pystudio.widgets.state_value_controls import F8JsonValueEditor
from f8pystudio.widgets.node_library_widget import _F8StudioNodesTreeWidget
from f8pystudio.widgets.node_variant_manager_dialog import NodeVariantManagerDialog


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def test_find_bracket_match_ignores_brackets_inside_strings() -> None:
    text = '{"a": "[ignored]", "b": {"c": [1, 2]}}'
    index_open = text.index("{", text.index('"b"'))
    index_close = text.index("}", index_open)
    match = find_bracket_match(text, index_open)
    assert match == BracketMatch(left=index_open, right=index_close)


def test_find_bracket_match_supports_cursor_after_bracket() -> None:
    text = '{"k":[1,2]}'
    open_index = text.index("[")
    close_index = text.index("]")
    match = find_bracket_match(text, open_index + 1)
    assert match == BracketMatch(left=open_index, right=close_index)


def test_compute_line_end_depth_tracks_multiline_nesting() -> None:
    lines = ['{"a": {', '  "b": [1, 2]', "}}"]
    depth = 0
    for line in lines:
        depth = compute_line_end_depth(line, depth)
    assert depth == 0


def test_attach_json_enhancements_sets_runtime_helpers_on_plain_text_edit() -> None:
    _ensure_app()
    editor = QtWidgets.QPlainTextEdit()
    attach_json_enhancements(editor, read_only=False)
    assert hasattr(editor, "_f8_json_highlighter")
    assert hasattr(editor, "_f8_json_bracket_pair_controller")


def test_attach_json_enhancements_applies_syntax_colors_for_key_token() -> None:
    _ensure_app()
    editor = QtWidgets.QPlainTextEdit()
    editor.setPlainText('{"alpha": 123, "beta": true}')
    attach_json_enhancements(editor, read_only=False)
    highlighter = editor._f8_json_highlighter  # type: ignore[attr-defined]
    assert isinstance(highlighter, QtGui.QSyntaxHighlighter)
    highlighter.rehighlight()

    block = editor.document().firstBlock()
    layout = block.layout()
    formats = list(layout.formats())
    key_pos = editor.toPlainText().index("alpha")

    key_foreground: QtGui.QColor | None = None
    for fr in formats:
        start = int(fr.start)
        end = start + int(fr.length)
        if start <= key_pos < end:
            key_foreground = fr.format.foreground().color()
            break

    assert key_foreground is not None
    default_text_color = editor.palette().color(QtGui.QPalette.ColorRole.Text)
    assert key_foreground != default_text_color


def test_json_prop_text_edit_attaches_json_enhancements() -> None:
    _ensure_app()
    widget = F8JsonValueEditor()
    assert hasattr(widget, "_f8_json_highlighter")
    assert hasattr(widget, "_f8_json_bracket_pair_controller")


def test_node_info_dialog_raw_json_attaches_json_enhancements(monkeypatch) -> None:
    _ensure_app()
    tree = _F8StudioNodesTreeWidget(node_graph=None)
    fake_node_cls = type("FakeNodeCls", (), {})
    tree._factory = SimpleNamespace(nodes={"svc.test": fake_node_cls})  # type: ignore[attr-defined]
    monkeypatch.setattr(
        "f8pystudio.widgets.node_library_widget.typed_spec_template_or_none",
        lambda _cls: F8ServiceSpec(serviceClass="svc.test", label="Test"),
    )

    captured = {"attached": False}

    def _fake_exec(dialog: QtWidgets.QDialog) -> int:
        edits = dialog.findChildren(QtWidgets.QPlainTextEdit)
        if edits:
            captured["attached"] = bool(
                hasattr(edits[0], "_f8_json_highlighter")
                and hasattr(edits[0], "_f8_json_bracket_pair_controller")
            )
        return 0

    monkeypatch.setattr(QtWidgets.QDialog, "exec", _fake_exec)
    tree._show_spec_dialog(node_id="svc.test", node_name="Test")
    assert captured["attached"] is True


def test_node_variant_manager_raw_json_attaches_json_enhancements(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr("f8pystudio.widgets.node_variant_manager_dialog.list_variants_for_base", lambda _base: [])
    monkeypatch.setattr(
        "f8pystudio.widgets.node_variant_manager_dialog.subscribe_variants_changed",
        lambda _cb: (lambda: None),
    )
    dlg = NodeVariantManagerDialog(
        parent=None,
        base_node_type="svc.test.operator",
        base_node_name="Test",
        node_graph=None,
    )
    assert hasattr(dlg._raw, "_f8_json_highlighter")
    assert hasattr(dlg._raw, "_f8_json_bracket_pair_controller")
    dlg.close()

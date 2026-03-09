from __future__ import annotations

from qtpy import QtWidgets

from f8pystudio.nodegraph.items.embedded_resize_contract import clamp_content_size
from f8pystudio.nodegraph.note_nodeitem import F8StudioNoteNodeItem
from f8pystudio.render_nodes.note import _NoteWidget


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


class _FakeResizable:
    def __init__(self, minimum: tuple[int, int]) -> None:
        self._minimum = minimum

    def minimum_content_size(self) -> tuple[int, int]:
        return self._minimum

    def apply_content_rect(self, width: int, height: int) -> None:
        _ = width
        _ = height


def test_clamp_content_size_respects_minimum() -> None:
    out_w, out_h = clamp_content_size(width=64, height=32, minimum=(120, 80))
    assert out_w == 120
    assert out_h == 80


def test_note_widget_apply_content_rect_updates_group_size() -> None:
    _ensure_app()
    widget = _NoteWidget(name="__note_markdown")
    widget.apply_content_rect(460, 280)
    group = widget.widget()
    assert group is not None
    assert group.width() == 460
    assert group.height() == 280
    editor = widget.editor()
    assert editor.width() > 0
    assert editor.height() > 0


def test_note_widget_preserves_raw_markdown_text() -> None:
    _ensure_app()
    widget = _NoteWidget(name="__note_markdown")
    raw = "#aaa\n###bb"
    widget.set_value(raw)
    assert widget.get_value() == raw
    assert widget.editor().isReadOnly() is True


def test_note_widget_toggle_preview_and_edit_mode() -> None:
    _ensure_app()
    widget = _NoteWidget(name="__note_markdown")
    widget.set_value("# title\n\nbody")
    assert widget.editor().isReadOnly() is True

    widget.set_preview_enabled(False)
    assert widget.editor().isReadOnly() is False
    assert widget.editor().toPlainText() == "# title\n\nbody"

    widget.editor().setPlainText("# changed\n\ncontent")
    widget.set_preview_enabled(True)
    assert widget.editor().isReadOnly() is True
    assert widget.get_value() == "# changed\n\ncontent"


def test_note_node_item_dynamic_minimum_uses_widget_minimum() -> None:
    _ensure_app()
    item = F8StudioNoteNodeItem()
    item._widgets["__note_markdown"] = _FakeResizable(minimum=(520, 420))
    item._ports_end_y = 50.0
    item._update_dynamic_minimum_size()
    min_w, min_h = item.minimum_size
    assert min_w >= 528.0
    assert min_h >= 474.0


def test_note_node_item_resize_applies_content_rect_to_widget() -> None:
    _ensure_app()
    item = F8StudioNoteNodeItem()
    note_widget = _NoteWidget(name="__note_markdown")
    item._widgets["__note_markdown"] = note_widget
    item._width = 520.0
    item._height = 360.0
    item._ports_end_y = 40.0

    item._resize_note_content_widget()

    group = note_widget.widget()
    assert group is not None
    assert group.width() == 512
    assert group.height() == 310


def test_note_node_item_draw_node_does_not_expand_width_over_time() -> None:
    _ensure_app()
    item = F8StudioNoteNodeItem()
    note_widget = _NoteWidget(name="__note_markdown")
    item._widgets["__note_markdown"] = note_widget
    item._width = 320.0
    item._height = 220.0
    item._ports_end_y = 40.0

    item.draw_node()
    baseline = float(item._width)
    for _ in range(10):
        item.draw_node()

    assert float(item._width) == baseline

from __future__ import annotations

from typing import Any

from qtpy import QtCore, QtGui, QtWidgets

from f8pystudio.components.controls import F8OptionCombo
from f8pystudio.components.state_builders import StateControlSpec, build_inline_control_binding
from f8pystudio.components.state_editors import F8IncrementButtonEditor, F8InlineCodeEditor, F8WrapLineEditor


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def _build_inline_binding(
    *,
    spec: StateControlSpec,
    state: dict[str, Any],
    calls: list[tuple[Any, bool]],
) -> Any:
    return build_inline_control_binding(
        spec=spec,
        read_only=False,
        value_getter=lambda: state.get("value"),
        value_setter=lambda value, push_undo: _record_value(state, calls, value, push_undo),
        property_value_getter=lambda field_name: state.get(str(field_name)),
        pool_resolver=lambda field_name: list(state.get(f"pool:{field_name}", [])),
        code_title="Node - Value",
        code_value_getter=None,
        code_value_setter=None,
        assist_context=None,
        assist_context_provider=None,
        editor_session_key=None,
        style_applier=lambda widget: None,
        text_palette_applier=lambda widget: None,
        tooltip_filter_installer=None,
    )


def _record_value(state: dict[str, Any], calls: list[tuple[Any, bool]], value: Any, push_undo: bool) -> None:
    state["value"] = value
    calls.append((value, push_undo))


def test_wrapline_builder_commits_normalized_text() -> None:
    _ensure_app()
    state = {"value": "before"}
    calls: list[tuple[Any, bool]] = []
    binding = _build_inline_binding(
        spec=StateControlSpec(
            name="expr",
            label="Expr",
            ui_control="wrapline",
            ui_language="plaintext",
            schema_type="string",
            enum_items=[],
            minimum=None,
            maximum=None,
        ),
        state=state,
        calls=calls,
    )

    widget = binding.widget
    assert isinstance(widget, F8WrapLineEditor)
    widget.focusInEvent(QtGui.QFocusEvent(QtCore.QEvent.Type.FocusIn))
    widget.setPlainText("hello\n world")
    widget.keyPressEvent(QtGui.QKeyEvent(QtCore.QEvent.Type.KeyPress, QtCore.Qt.Key.Key_Return, QtCore.Qt.NoModifier))

    assert calls == [("hello world", True)]
    assert widget.toPlainText() == "hello world"


def test_code_inline_builder_commits_on_ctrl_enter() -> None:
    _ensure_app()
    state = {"value": "before"}
    calls: list[tuple[Any, bool]] = []
    binding = _build_inline_binding(
        spec=StateControlSpec(
            name="code",
            label="Code",
            ui_control="code_inline",
            ui_language="python",
            schema_type="string",
            enum_items=[],
            minimum=None,
            maximum=None,
        ),
        state=state,
        calls=calls,
    )

    widget = binding.widget
    assert isinstance(widget, F8InlineCodeEditor)
    widget.focusInEvent(QtGui.QFocusEvent(QtCore.QEvent.Type.FocusIn))
    widget.setPlainText("x = 1\ny = 2")
    widget.keyPressEvent(
        QtGui.QKeyEvent(
            QtCore.QEvent.Type.KeyPress,
            QtCore.Qt.Key.Key_Return,
            QtCore.Qt.KeyboardModifier.ControlModifier,
        )
    )

    assert calls == [("x = 1\ny = 2", True)]


def test_select_builder_refresh_options_preserves_selected_value() -> None:
    _ensure_app()
    state = {"value": "b", "pool:choices": ["a", "b"]}
    calls: list[tuple[Any, bool]] = []
    binding = _build_inline_binding(
        spec=StateControlSpec(
            name="choice",
            label="Choice",
            ui_control="select",
            ui_language="plaintext",
            schema_type="string",
            enum_items=[],
            minimum=None,
            maximum=None,
            select_pool_field="choices",
        ),
        state=state,
        calls=calls,
    )

    widget = binding.widget
    assert isinstance(widget, F8OptionCombo)
    assert widget.count() == 2
    assert widget.value() == "b"

    state["pool:choices"] = ["c", "b", "d"]
    assert binding.refresh_options is not None
    binding.refresh_options()

    assert widget.count() == 3
    assert widget.value() == "b"


def test_button_builder_marks_invalid_numeric_schema() -> None:
    _ensure_app()
    state = {"value": 0}
    calls: list[tuple[Any, bool]] = []
    binding = _build_inline_binding(
        spec=StateControlSpec(
            name="trigger",
            label="Trigger",
            ui_control="button",
            ui_language="plaintext",
            schema_type="string",
            enum_items=[],
            minimum=None,
            maximum=None,
        ),
        state=state,
        calls=calls,
    )

    widget = binding.widget
    assert isinstance(widget, F8IncrementButtonEditor)
    assert not widget.isEnabled()
    widget.click()
    assert calls == []

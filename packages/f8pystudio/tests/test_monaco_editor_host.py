from __future__ import annotations

from qtpy import QtCore, QtWidgets

from f8pystudio.editor_assist.session import EditorSessionKey
from f8pystudio.widgets import monaco_editor_dialog as monaco_dialog_module
from f8pystudio.widgets.monaco_editor_dialog import MonacoEditorHostDialog, open_code_editor_window
from f8pystudio.widgets.monaco_editor_page import MonacoEditorPageConfig, build_monaco_editor_html


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


class _FakeEditorWidget(QtWidgets.QWidget):
    code_saved = QtCore.Signal(str)
    close_requested = QtCore.Signal()
    accept_requested = QtCore.Signal()

    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._saved_close_after: list[bool] = []
        controller.code_saved.connect(self.code_saved.emit)  # type: ignore[attr-defined]

    def controller(self):
        return self._controller

    def is_dirty(self) -> bool:
        return self._controller.dirty()

    def set_close_on_save(self, close_on_save: bool) -> None:
        self._controller.set_close_on_save(close_on_save)

    def shutdown(self) -> None:
        self._controller.shutdown()

    def save_current(self, *, close_after: bool) -> str:
        self._saved_close_after.append(bool(close_after))
        self._controller.save_code(self._controller.code())
        if close_after:
            self.accept_requested.emit()
        return self._controller.code()


def test_open_code_editor_window_reuses_existing_tab_for_same_session_key(monkeypatch) -> None:
    _ensure_app()
    monaco_dialog_module._HOST_DIALOGS.clear()
    monkeypatch.setattr(MonacoEditorHostDialog, "_create_editor_widget", lambda self, controller: _FakeEditorWidget(controller, self))

    session_key = EditorSessionKey.studio_node(graph_id="graph:alpha", node_id="nodeA", field_name="code")

    host1 = open_code_editor_window(
        None,
        title="Node A",
        code="print('a')\n",
        language="python",
        on_saved=lambda _code: None,
        session_key=session_key,
    )
    host2 = open_code_editor_window(
        None,
        title="Node A Again",
        code="print('b')\n",
        language="python",
        on_saved=lambda _code: None,
        session_key=session_key,
    )

    assert host1 is host2
    assert isinstance(host1, MonacoEditorHostDialog)
    assert host1._tabs.count() == 1
    host1.close()


def test_host_keeps_sessions_isolated_across_tabs(monkeypatch) -> None:
    _ensure_app()
    monaco_dialog_module._HOST_DIALOGS.clear()
    monkeypatch.setattr(MonacoEditorHostDialog, "_create_editor_widget", lambda self, controller: _FakeEditorWidget(controller, self))
    monkeypatch.setattr(monaco_dialog_module, "_ask_save_before_close", lambda parent, title=None: QtWidgets.QMessageBox.StandardButton.No)

    key_a = EditorSessionKey.studio_node(graph_id="graph:beta", node_id="nodeA", field_name="code")
    key_b = EditorSessionKey.studio_node(graph_id="graph:beta", node_id="nodeB", field_name="code")

    host = open_code_editor_window(
        None,
        title="Node A",
        code="print('a')\n",
        language="python",
        on_saved=lambda _code: None,
        session_key=key_a,
    )
    host = open_code_editor_window(
        None,
        title="Node B",
        code="print('b')\n",
        language="python",
        on_saved=lambda _code: None,
        session_key=key_b,
    )

    editor_a = host._sessions[key_a.as_id()]
    editor_b = host._sessions[key_b.as_id()]
    editor_a.controller().set_dirty(True)

    index_a = host._tabs.indexOf(editor_a)
    index_b = host._tabs.indexOf(editor_b)

    assert host._tabs.tabText(index_a).startswith("* ")
    assert not host._tabs.tabText(index_b).startswith("* ")

    assert host._close_editor_widget(editor_a, interactive=True) is True
    assert key_a.as_id() not in host._sessions
    assert key_b.as_id() in host._sessions
    assert host._tabs.count() == 1
    host.close()


def test_monaco_editor_page_routes_ai_requests_through_request_maps() -> None:
    html = build_monaco_editor_html(
        MonacoEditorPageConfig(
            code="print('hello')\n",
            language="python",
            monaco_base_url="https://cdn.jsdelivr.net/npm/monaco-editor/min",
            python_assist_enabled=True,
        )
    )

    send_message_block = html.split("function _f8_sendMessage() {", 1)[1].split("window._f8_attachments = [];", 1)[0]
    assert "window._f8_chatRequests = Object.create(null);" in html
    assert "window._f8_editRequests = Object.create(null);" in html
    assert "window._f8_planRequests = Object.create(null);" in html
    assert html.count("chat_chunk_ready.connect") >= 1
    assert html.count("chat_done.connect") >= 1
    assert html.count("edit_result_ready.connect") >= 1
    assert html.count("plan_step_ready.connect") >= 1
    assert html.count("plan_done.connect") >= 1
    assert ".chat_chunk_ready.connect" not in send_message_block
    assert ".chat_done.connect" not in send_message_block
    assert ".edit_result_ready.connect" not in send_message_block
    assert ".plan_step_ready.connect" not in send_message_block
    assert ".plan_done.connect" not in send_message_block

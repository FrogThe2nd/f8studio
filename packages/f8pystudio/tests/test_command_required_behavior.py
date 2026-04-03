from __future__ import annotations

from qtpy import QtWidgets

from f8pysdk import F8Command, F8ServiceSpec, F8SpecEditPolicy, editable_collection_edit_policy
from f8pystudio.nodegraph.spec_mutations import delete_command
from f8pystudio.ui.widgets.node_property_panel import _F8EditCommandDialog, _F8SpecCommandEditor


class _FakeModel:
    def __init__(self) -> None:
        self.f8_sys: dict[str, object] = {}


class _FakeNode:
    def __init__(self, spec: F8ServiceSpec) -> None:
        self.spec = spec
        self.model = _FakeModel()
        self.id = "svc.test"
        self._ui_overrides: dict[str, object] = {}

    def effective_commands(self) -> list[F8Command]:
        return list(self.spec.commands or [])

    def ui_overrides(self) -> dict[str, object]:
        return dict(self._ui_overrides)

    def set_ui_overrides(self, value: dict[str, object] | None, *, rebuild: bool = True) -> None:
        _ = rebuild
        self._ui_overrides = dict(value or {})


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def _make_spec(commands: list[F8Command]) -> F8ServiceSpec:
    return F8ServiceSpec(
        serviceClass="f8.test",
        label="Test",
        editPolicy=F8SpecEditPolicy(commands=editable_collection_edit_policy()),
        commands=commands,
    )


def test_spec_delete_command_keeps_required() -> None:
    spec = _make_spec(
        [
            F8Command(name="required_cmd", required=True, params=[]),
            F8Command(name="optional_cmd", required=False, params=[]),
        ]
    )
    spec2 = delete_command(spec, name="required_cmd")
    names = [str(c.name or "") for c in list(spec2.commands or [])]
    assert names == ["required_cmd", "optional_cmd"]


def test_spec_delete_command_removes_optional() -> None:
    spec = _make_spec(
        [
            F8Command(name="required_cmd", required=True, params=[]),
            F8Command(name="optional_cmd", required=False, params=[]),
        ]
    )
    spec2 = delete_command(spec, name="optional_cmd")
    names = [str(c.name or "") for c in list(spec2.commands or [])]
    assert names == ["required_cmd"]


def test_edit_command_dialog_preserves_required_flag() -> None:
    _ensure_app()
    dialog = _F8EditCommandDialog(
        None,
        title="Edit command",
        cmd=F8Command(name="required_cmd", required=True, params=[]),
        ui_only=False,
    )
    edited = dialog.command()
    assert edited.required is True


def test_command_editor_hides_delete_for_required_command(monkeypatch) -> None:
    _ensure_app()
    spec = _make_spec(
        [
            F8Command(name="required_cmd", required=True, params=[]),
            F8Command(name="optional_cmd", required=False, params=[]),
        ]
    )
    node = _FakeNode(spec)
    editor = _F8SpecCommandEditor(None, node=node, on_apply=None)

    required_row = editor._cmd_rows["required_cmd"]
    optional_row = editor._cmd_rows["optional_cmd"]
    assert required_row._btn_del.isHidden() is True
    assert optional_row._btn_del.isHidden() is False

    asked = {"called": False}

    def _fake_question(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        asked["called"] = True
        return QtWidgets.QMessageBox.Yes

    monkeypatch.setattr(QtWidgets.QMessageBox, "question", _fake_question)

    editor._delete_command("required_cmd")
    assert asked["called"] is False
    names = [str(c.name or "") for c in list(node.spec.commands or [])]
    assert names == ["required_cmd", "optional_cmd"]

    editor._delete_command("optional_cmd")
    assert asked["called"] is True
    names = [str(c.name or "") for c in list(node.spec.commands or [])]
    assert names == ["required_cmd"]


def test_command_editor_delete_cleans_list_order_override(monkeypatch) -> None:
    _ensure_app()
    spec = _make_spec(
        [
            F8Command(name="first", required=False, params=[]),
            F8Command(name="second", required=False, params=[]),
        ]
    )
    node = _FakeNode(spec)
    node.set_ui_overrides({"listOrder": {"commands": ["second", "first"]}}, rebuild=False)
    editor = _F8SpecCommandEditor(None, node=node, on_apply=None)

    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *args, **kwargs: QtWidgets.QMessageBox.Yes)

    editor._delete_command("second")

    assert [str(command.name or "") for command in list(node.spec.commands or [])] == ["first"]
    assert node.ui_overrides() == {}

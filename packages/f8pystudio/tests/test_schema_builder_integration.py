from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from qtpy import QtWidgets

from f8pysdk import (
    F8Command,
    F8CommandParam,
    F8DataPortSpec,
    F8ServiceSpec,
    F8StateAccess,
    F8StateSpec,
)
from f8pystudio.widgets import node_property_widgets as npw


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


class _FakeSchemaDialog:
    last_read_only: bool | None = None

    def __init__(self, _parent: Any = None, *, title: str, schema: Any, read_only: bool = False):
        del title
        self._schema = schema
        _FakeSchemaDialog.last_read_only = bool(read_only)

    def exec_(self) -> int:
        return QtWidgets.QDialog.Rejected

    def schema(self) -> Any:
        return self._schema


class _FakePortNode:
    def __init__(self, spec: F8ServiceSpec, *, missing_locked: bool) -> None:
        self.spec = spec
        self.model = SimpleNamespace(f8_sys={"missingLocked": bool(missing_locked), "missingType": "svc.test"})

    def data_port_show_on_node(self, _name: str, *, is_in: bool) -> bool:
        del is_in
        return True


class _FakeGraph:
    def __init__(self) -> None:
        self.service_bridge = None


class _FakeCommandNode:
    def __init__(self, spec: F8ServiceSpec, *, missing_locked: bool) -> None:
        self.spec = spec
        self.model = SimpleNamespace(f8_sys={"missingLocked": bool(missing_locked), "missingType": "svc.test"})
        self.id = "svc.test"
        self.graph = _FakeGraph()

    def effective_commands(self) -> list[F8Command]:
        return list(self.spec.commands or [])


class _FakeStateNode:
    def __init__(self, spec: F8ServiceSpec, *, missing_locked: bool) -> None:
        self.spec = spec
        self.model = SimpleNamespace(f8_sys={"missingLocked": bool(missing_locked), "missingType": "svc.test"})

    def effective_state_fields(self) -> list[F8StateSpec]:
        return list(self.spec.stateFields or [])


def test_edit_schema_dialogs_pass_read_only_when_ui_only(monkeypatch) -> None:
    _ensure_app()
    monkeypatch.setattr(npw, "SchemaBuilderDialog", _FakeSchemaDialog)

    data_port = npw._F8EditDataPortDialog(
        None,
        title="Data",
        port=F8DataPortSpec(name="in", valueSchema=npw._schema_from_json_obj({"type": "any"})),
        ui_only=True,
    )
    data_port._edit_schema()
    assert _FakeSchemaDialog.last_read_only is True

    state_field = npw._F8EditStateFieldDialog(
        None,
        title="State",
        field=F8StateSpec(name="x", valueSchema=npw._schema_from_json_obj({"type": "number"}), access=F8StateAccess.rw),
        ui_only=True,
    )
    state_field._edit_schema()
    assert _FakeSchemaDialog.last_read_only is True

    cmd_param = npw._F8EditCommandParamDialog(
        None,
        title="Param",
        param=F8CommandParam(name="arg", valueSchema=npw._schema_from_json_obj({"type": "string"})),
        ui_only=True,
    )
    cmd_param._edit_schema()
    assert _FakeSchemaDialog.last_read_only is True


def test_open_state_field_editor_allows_missing_locked_read_only_dialog(monkeypatch) -> None:
    _ensure_app()

    captured: dict[str, Any] = {}

    class _FakeStateDialog:
        def __init__(self, _parent: Any = None, *, title: str, field: Any, ui_only: bool, read_only: bool):
            del title, field
            captured["ui_only"] = bool(ui_only)
            captured["read_only"] = bool(read_only)

        def exec_(self) -> int:
            return QtWidgets.QDialog.Rejected

    monkeypatch.setattr(npw, "_F8EditStateFieldDialog", _FakeStateDialog)

    spec = F8ServiceSpec(
        serviceClass="f8.test",
        label="Test",
        editableStateFields=True,
        stateFields=[F8StateSpec(name="x", valueSchema=npw._schema_from_json_obj({"type": "number"}), access=F8StateAccess.rw)],
    )
    node = _FakeStateNode(spec, missing_locked=True)

    class _FakeEditor:
        def __init__(self, backend_node: _FakeStateNode) -> None:
            self._node = backend_node

        def _apply_state_field_ui_override(self, _name: str, _field: F8StateSpec) -> None:
            raise AssertionError("should not be called in read-only mode")

        def _apply_state_field_spec_replace(self, _name: str, _field: F8StateSpec) -> None:
            raise AssertionError("should not be called in read-only mode")

        def _on_spec_applied(self) -> None:
            raise AssertionError("should not be called in read-only mode")

    widget = _FakeEditor(node)

    npw.F8StudioNodePropEditorWidget.open_state_field_editor(widget, "x")
    assert captured["read_only"] is True


def test_edit_data_port_allows_missing_locked_read_only_dialog(monkeypatch) -> None:
    _ensure_app()

    captured: dict[str, Any] = {}

    class _FakeDataDialog:
        def __init__(self, _parent: Any = None, *, title: str, port: Any, ui_only: bool, read_only: bool):
            del title, port
            captured["ui_only"] = bool(ui_only)
            captured["read_only"] = bool(read_only)

        def exec_(self) -> int:
            return QtWidgets.QDialog.Rejected

    monkeypatch.setattr(npw, "_F8EditDataPortDialog", _FakeDataDialog)

    spec = F8ServiceSpec(
        serviceClass="f8.test",
        label="Test",
        editableDataInPorts=True,
        dataInPorts=[F8DataPortSpec(name="in", valueSchema=npw._schema_from_json_obj({"type": "any"}))],
        dataOutPorts=[],
    )
    node = _FakePortNode(spec, missing_locked=True)
    editor = npw._F8SpecPortEditor(None, node=node, on_apply=None)

    row = editor._sec_data_in.rows()[0]
    editor._edit_data(row)

    assert captured["read_only"] is True


def test_edit_command_allows_missing_locked_read_only_dialog(monkeypatch) -> None:
    _ensure_app()

    captured: dict[str, Any] = {}

    class _FakeCommandDialog:
        def __init__(self, _parent: Any = None, *, title: str, cmd: Any, ui_only: bool, read_only: bool):
            del title, cmd
            captured["ui_only"] = bool(ui_only)
            captured["read_only"] = bool(read_only)

        def exec_(self) -> int:
            return QtWidgets.QDialog.Rejected

    monkeypatch.setattr(npw, "_F8EditCommandDialog", _FakeCommandDialog)

    spec = F8ServiceSpec(
        serviceClass="f8.test",
        label="Test",
        editableCommands=True,
        commands=[F8Command(name="run", params=[])],
    )
    node = _FakeCommandNode(spec, missing_locked=True)
    editor = npw._F8SpecCommandEditor(None, node=node, on_apply=None)

    editor._edit_command("run")

    assert captured["read_only"] is True

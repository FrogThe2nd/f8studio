from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from qtpy import QtWidgets
from NodeGraphQt.constants import NodePropWidgetEnum

from f8pysdk import (
    F8Command,
    F8CommandParam,
    F8DataPortSpec,
    F8OperatorSpec,
    F8ServiceSpec,
    F8SpecEditPolicy,
    F8StateAccess,
    F8StateSpec,
    editable_collection_edit_policy,
)
from f8pystudio.widgets import node_property_panel as npw


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
        self.model = _FakeGraphModel()


class _FakeGraphModel:
    def get_node_common_properties(self, _node_type: str) -> dict[str, Any]:
        return {}


class _FakePropertyModel:
    def __init__(self) -> None:
        self.f8_sys: dict[str, object] = {}
        self.custom_properties: dict[str, Any] = {}

    def get_tab_name(self, _prop_name: str) -> str:
        return "Properties"

    def get_widget_type(self, _prop_name: str) -> int:
        return int(NodePropWidgetEnum.QLINE_EDIT.value)

    def get_property(self, prop_name: str) -> Any:
        if prop_name == "type_":
            return "f8.test.operator"
        return None


class _FakeOperatorPropertyNode:
    def __init__(self, spec: F8OperatorSpec) -> None:
        self.spec = spec
        self.model = _FakePropertyModel()
        self.id = "op.test"
        self.svcId = "svc.test"
        self.graph = _FakeGraph()
        self.type_ = "f8.test.operator"
        self.nodePurpose = ""

    def name(self) -> str:
        return "Operator"

    def icon(self) -> str:
        return ""

    def effective_commands(self) -> list[F8Command]:
        return list(self.spec.commands or [])

    def effective_state_fields(self) -> list[F8StateSpec]:
        return list(self.spec.stateFields or [])


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
        self.id = "svc.test"

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
        def __init__(
            self,
            _parent: Any = None,
            *,
            title: str,
            field: Any,
            global_hotkey: str = "",
            current_binding_id: str = "",
            hotkey_conflict_lookup: Any = None,
            hotkey_capture_started: Any = None,
            hotkey_capture_finished: Any = None,
            ui_only: bool,
            lock_identity_fields: bool,
            read_only: bool,
        ):
            del title, field
            _ = (
                global_hotkey,
                current_binding_id,
                hotkey_conflict_lookup,
                hotkey_capture_started,
                hotkey_capture_finished,
            )
            captured["ui_only"] = bool(ui_only)
            captured["lock_identity_fields"] = bool(lock_identity_fields)
            captured["read_only"] = bool(read_only)

        def exec_(self) -> int:
            return QtWidgets.QDialog.Rejected

    monkeypatch.setattr(npw, "_F8EditStateFieldDialog", _FakeStateDialog)

    spec = F8ServiceSpec(
        serviceClass="f8.test",
        label="Test",
        editPolicy=F8SpecEditPolicy(stateFields=editable_collection_edit_policy()),
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
        def __init__(
            self,
            _parent: Any = None,
            *,
            title: str,
            port: Any,
            ui_only: bool,
            lock_identity_fields: bool,
            read_only: bool,
        ):
            del title, port
            captured["ui_only"] = bool(ui_only)
            captured["lock_identity_fields"] = bool(lock_identity_fields)
            captured["read_only"] = bool(read_only)

        def exec_(self) -> int:
            return QtWidgets.QDialog.Rejected

    monkeypatch.setattr(npw, "_F8EditDataPortDialog", _FakeDataDialog)

    spec = F8ServiceSpec(
        serviceClass="f8.test",
        label="Test",
        editPolicy=F8SpecEditPolicy(dataInPorts=editable_collection_edit_policy()),
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
        def __init__(
            self,
            _parent: Any = None,
            *,
            title: str,
            cmd: Any,
            ui_only: bool,
            lock_identity_fields: bool,
            allow_param_structure_mutation: bool,
            read_only: bool,
        ):
            del title, cmd
            captured["ui_only"] = bool(ui_only)
            captured["lock_identity_fields"] = bool(lock_identity_fields)
            captured["allow_param_structure_mutation"] = bool(allow_param_structure_mutation)
            captured["read_only"] = bool(read_only)

        def exec_(self) -> int:
            return QtWidgets.QDialog.Rejected

    monkeypatch.setattr(npw, "_F8EditCommandDialog", _FakeCommandDialog)

    spec = F8ServiceSpec(
        serviceClass="f8.test",
        label="Test",
        editPolicy=F8SpecEditPolicy(commands=editable_collection_edit_policy()),
        commands=[F8Command(name="run", params=[])],
    )
    node = _FakeCommandNode(spec, missing_locked=True)
    editor = npw._F8SpecCommandEditor(None, node=node, on_apply=None)

    editor._edit_command("run")

    assert captured["read_only"] is True


def test_node_property_widget_shows_commands_tab_for_operator_specs() -> None:
    _ensure_app()
    spec = F8OperatorSpec(
        serviceClass="f8.test",
        operatorClass="f8.test.operator",
        label="Operator",
        editPolicy=F8SpecEditPolicy(commands=editable_collection_edit_policy()),
        commands=[F8Command(name="run", params=[])],
    )
    node = _FakeOperatorPropertyNode(spec)

    widget = npw.F8StudioNodePropEditorWidget(None, node=node)
    tabs = widget.get_tab_widget()
    labels = [tabs.tabText(index) for index in range(tabs.count())]

    assert "Commands" in labels

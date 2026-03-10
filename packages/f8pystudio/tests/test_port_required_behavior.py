from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from qtpy import QtWidgets

from f8pysdk import F8DataPortSpec, F8ServiceSpec, F8StateAccess, F8StateSpec
from f8pysdk.schema_helpers import string_schema
from f8pystudio.widgets import node_property_widgets as npw
from f8pystudio.widgets.node_property_widgets import _F8SpecPortEditor
from f8pystudio.widgets.spec_mutations import set_ports


class _FakeModel:
    def __init__(self) -> None:
        self.f8_sys: dict[str, object] = {}


class _FakeNode:
    def __init__(self, spec: F8ServiceSpec) -> None:
        self.spec = spec
        self.model = _FakeModel()
        self.id = "svc.test"

    def data_port_show_on_node(self, name: str, *, is_in: bool) -> bool:
        ports = list(self.spec.dataInPorts or []) if is_in else list(self.spec.dataOutPorts or [])
        n = str(name or "").strip()
        for port in ports:
            if str(port.name or "").strip() == n:
                return bool(port.showOnNode)
        return False


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def _make_spec() -> F8ServiceSpec:
    return F8ServiceSpec(
        serviceClass="f8.test",
        label="Test",
        editableDataInPorts=True,
        editableDataOutPorts=True,
        editableStateFields=True,
        dataInPorts=[
            F8DataPortSpec(name="required_in", required=True, valueSchema=string_schema()),
            F8DataPortSpec(name="optional_in", required=False, valueSchema=string_schema()),
        ],
        dataOutPorts=[
            F8DataPortSpec(name="required_out", required=True, valueSchema=string_schema()),
            F8DataPortSpec(name="optional_out", required=False, valueSchema=string_schema()),
        ],
        stateFields=[
            F8StateSpec(name="required_state", valueSchema=string_schema(), access=F8StateAccess.rw, required=True),
            F8StateSpec(name="optional_state", valueSchema=string_schema(), access=F8StateAccess.rw, required=False),
        ],
    )


def _find_data_row(editor: _F8SpecPortEditor, *, name: str, is_in: bool) -> Any | None:
    rows = editor._sec_data_in.rows() if is_in else editor._sec_data_out.rows()
    for row in rows:
        if str(row.name_edit.text() or "").strip() == str(name or "").strip():
            return row
    return None


def test_set_ports_keeps_required_data_ports() -> None:
    spec = _make_spec()
    spec2 = set_ports(
        spec,
        data_in=[F8DataPortSpec(name="optional_in", required=False, valueSchema=string_schema())],
        data_out=[F8DataPortSpec(name="optional_out", required=False, valueSchema=string_schema())],
    )
    in_names = [str(p.name or "") for p in list(spec2.dataInPorts or [])]
    out_names = [str(p.name or "") for p in list(spec2.dataOutPorts or [])]
    assert in_names == ["required_in", "optional_in"]
    assert out_names == ["required_out", "optional_out"]


def test_port_editor_hides_delete_for_required_port() -> None:
    _ensure_app()
    node = _FakeNode(_make_spec())
    editor = _F8SpecPortEditor(None, node=node, on_apply=None)

    required_row = _find_data_row(editor, name="required_in", is_in=True)
    optional_row = _find_data_row(editor, name="optional_in", is_in=True)
    assert required_row is not None
    assert optional_row is not None

    assert required_row.del_btn.isHidden() is True
    assert required_row.name_edit.isReadOnly() is True
    assert optional_row.del_btn.isHidden() is False
    assert optional_row.name_edit.isReadOnly() is False


def test_port_editor_refuses_delete_or_rename_required_port() -> None:
    _ensure_app()
    node = _FakeNode(_make_spec())
    editor = _F8SpecPortEditor(None, node=node, on_apply=None)

    required_row = _find_data_row(editor, name="required_in", is_in=True)
    optional_row = _find_data_row(editor, name="optional_in", is_in=True)
    assert required_row is not None
    assert optional_row is not None

    editor._rename_data(required_row, "renamed_required_in")
    editor._delete_row(required_row)
    in_names_after_required_ops = [str(p.name or "") for p in list(node.spec.dataInPorts or [])]
    assert in_names_after_required_ops == ["required_in", "optional_in"]

    editor._delete_row(optional_row)
    in_names_after_optional_delete = [str(p.name or "") for p in list(node.spec.dataInPorts or [])]
    assert in_names_after_optional_delete == ["required_in"]


def test_required_data_port_dialog_is_not_ui_only_when_editable(monkeypatch) -> None:
    _ensure_app()
    captured: dict[str, bool] = {}

    class _FakeDataDialog:
        def __init__(self, _parent: Any = None, *, title: str, port: Any, ui_only: bool, read_only: bool):
            del _parent, title, port
            captured["ui_only"] = bool(ui_only)
            captured["read_only"] = bool(read_only)

        def exec_(self) -> int:
            return QtWidgets.QDialog.Rejected

    monkeypatch.setattr(npw, "_F8EditDataPortDialog", _FakeDataDialog)

    node = _FakeNode(_make_spec())
    editor = _F8SpecPortEditor(None, node=node, on_apply=None)
    required_row = _find_data_row(editor, name="required_in", is_in=True)
    assert required_row is not None
    editor._edit_data(required_row)

    assert captured["ui_only"] is False
    assert captured["read_only"] is False


def test_add_data_port_defaults_to_optional_and_hidden(monkeypatch) -> None:
    _ensure_app()
    captured: dict[str, Any] = {}

    class _FakeDataDialog:
        def __init__(self, _parent: Any = None, *, title: str, port: Any, ui_only: bool, read_only: bool):
            del _parent, title, ui_only, read_only
            captured["required"] = bool(port.required)
            captured["show_on_node"] = bool(port.showOnNode)

        def exec_(self) -> int:
            return QtWidgets.QDialog.Rejected

    monkeypatch.setattr(npw, "_F8EditDataPortDialog", _FakeDataDialog)

    node = _FakeNode(_make_spec())
    editor = _F8SpecPortEditor(None, node=node, on_apply=None)
    editor._add_data(is_in=True)

    assert captured["required"] is False
    assert captured["show_on_node"] is False


def test_required_state_field_dialog_is_not_ui_only_when_editable(monkeypatch) -> None:
    _ensure_app()
    captured: dict[str, bool] = {}

    class _FakeStateDialog:
        def __init__(self, _parent: Any = None, *, title: str, field: Any, ui_only: bool, read_only: bool):
            del _parent, title, field
            captured["ui_only"] = bool(ui_only)
            captured["read_only"] = bool(read_only)

        def exec_(self) -> int:
            return QtWidgets.QDialog.Rejected

    monkeypatch.setattr(npw, "_F8EditStateFieldDialog", _FakeStateDialog)

    class _FakeEditor:
        def __init__(self, spec: F8ServiceSpec) -> None:
            self._node = SimpleNamespace(
                spec=spec,
                model=SimpleNamespace(f8_sys={}),
                effective_state_fields=lambda: list(spec.stateFields or []),
            )

        def _apply_state_field_ui_override(self, _name: str, _field: Any) -> None:
            raise AssertionError("should not be called")

        def _apply_state_field_spec_replace(self, _name: str, _field: Any) -> None:
            raise AssertionError("should not be called")

        def _on_spec_applied(self) -> None:
            raise AssertionError("should not be called")

    spec = _make_spec()
    widget = _FakeEditor(spec)
    npw.F8StudioNodePropEditorWidget.open_state_field_editor(widget, "required_state")

    assert captured["ui_only"] is False
    assert captured["read_only"] is False


def test_add_state_field_defaults_to_optional_and_hidden(monkeypatch) -> None:
    _ensure_app()
    captured: dict[str, Any] = {}

    class _FakeStateDialog:
        def __init__(self, _parent: Any = None, *, title: str, field: Any, ui_only: bool, read_only: bool = False):
            del _parent, title, ui_only, read_only
            captured["required"] = bool(field.required)
            captured["show_on_node"] = bool(field.showOnNode)

        def exec_(self) -> int:
            return QtWidgets.QDialog.Rejected

        def field(self) -> Any:
            raise AssertionError("field() should not be called when dialog is rejected")

    monkeypatch.setattr(npw, "_F8EditStateFieldDialog", _FakeStateDialog)

    class _FakeEditor:
        def __init__(self, spec: F8ServiceSpec) -> None:
            self._node = SimpleNamespace(spec=spec, model=SimpleNamespace(f8_sys={}))

    spec = _make_spec()
    widget = _FakeEditor(spec)
    npw.F8StudioNodePropEditorWidget.add_state_field(widget)

    assert captured["required"] is False
    assert captured["show_on_node"] is False

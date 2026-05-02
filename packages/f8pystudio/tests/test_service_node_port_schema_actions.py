from __future__ import annotations

from types import SimpleNamespace

from qtpy import QtWidgets

from f8pysdk.specs import (
    F8DataPortSpec,
    F8ServiceSpec,
    F8SpecEditPolicy,
    F8StateAccess,
    F8StateFieldEditPolicy,
    F8StateSpec,
    editable_collection_edit_policy,
)
from f8pysdk.specs import number_schema, string_schema

from f8pystudio.nodegraph.items import service_node_port_schema_actions as actions


class _BackendNode:
    def __init__(self, spec: F8ServiceSpec) -> None:
        self.spec = spec

    def set_spec(self, spec: F8ServiceSpec, *, rebuild: bool = False) -> None:
        self.spec = spec

    def is_missing_locked(self) -> bool:
        return False


class _NodeItemStub:
    def __init__(self, backend_node: _BackendNode) -> None:
        self._backend = backend_node

    def _backend_node(self) -> _BackendNode:
        return self._backend

    def _viewer_safe(self) -> None:
        return None


def test_port_group_and_label_helpers() -> None:
    assert actions.port_group("[E]run") == "exec"
    assert actions.port_group("image[D]") == "data"
    assert actions.port_group("[S]gain") == "state"
    assert actions.port_group("plain") == "other"

    assert actions.display_port_label("[D]position") == "position"
    assert actions.display_port_label("output[S]") == "output"
    assert actions.display_port_label("[D]very_long_port_name", max_chars=8) == "very_lo..."


def test_schema_port_name_parse() -> None:
    assert actions.parse_schema_port_view_name("[D]in") == ("data", True, "in")
    assert actions.parse_schema_port_view_name("out[D]") == ("data", False, "out")
    assert actions.parse_schema_port_view_name("[S]gain") == ("state", True, "gain")
    assert actions.parse_schema_port_view_name("gain[S]") == ("state", False, "gain")
    assert actions.parse_schema_port_view_name("[E]next") is None


def test_data_and_state_tooltip_use_spec_schema_brief() -> None:
    spec = F8ServiceSpec(
        serviceClass="f8.tests.tooltip",
        label="Tooltip Test",
        dataInPorts=[
            F8DataPortSpec(
                name="gain",
                valueSchema=number_schema(),
                description="Input gain",
            )
        ],
        stateFields=[
            F8StateSpec(
                name="mode",
                valueSchema=string_schema(),
                access=F8StateAccess.rw,
                description="Runtime mode",
            )
        ],
    )
    node_item = _NodeItemStub(_BackendNode(spec))

    data_tip = actions.data_port_tooltip(node_item, is_in=True, port_name="gain")
    state_tip = actions.state_port_tooltip(node_item, is_in=True, field_name="mode")

    assert "schema: number" in data_tip
    assert "Input gain" in data_tip
    assert "schema: string" in state_tip
    assert "Runtime mode" in state_tip
    assert actions.port_tooltip_text(node_item, "[D]gain") == data_tip
    assert actions.port_tooltip_text(node_item, "[S]mode") == state_tip


def test_schema_clipboard_text_round_trip() -> None:
    payload = actions.schema_to_clipboard_text(number_schema(minimum=0, maximum=5))
    parsed = actions.schema_from_clipboard_text(payload)
    assert parsed is not None
    assert actions.schema_brief(parsed) == "number"
    assert actions.schema_from_clipboard_text("not-json") is None


def test_replace_data_port_schema_updates_spec() -> None:
    spec = F8ServiceSpec(
        serviceClass="f8.tests.schema-paste-data",
        label="Schema Paste Data",
        dataInPorts=[F8DataPortSpec(name="gain", valueSchema=number_schema())],
    )
    backend = _BackendNode(spec)
    node_item = _NodeItemStub(backend)

    parsed = actions.schema_from_clipboard_text('{"type":"string"}')
    assert parsed is not None
    changed = actions.replace_data_port_schema(node_item, is_in=True, port_name="gain", new_schema=parsed)
    assert changed is True
    assert actions.schema_brief(backend.spec.dataInPorts[0].valueSchema) == "string"


def test_replace_state_field_schema_updates_spec() -> None:
    spec = F8ServiceSpec(
        serviceClass="f8.tests.schema-paste-state",
        label="Schema Paste State",
        stateFields=[F8StateSpec(name="mode", valueSchema=number_schema(), access=F8StateAccess.rw)],
    )
    backend = _BackendNode(spec)
    node_item = _NodeItemStub(backend)

    parsed = actions.schema_from_clipboard_text('{"type":"string"}')
    assert parsed is not None
    changed = actions.replace_state_field_schema(node_item, field_name="mode", new_schema=parsed)
    assert changed is True
    assert actions.schema_brief(backend.spec.stateFields[0].valueSchema) == "string"


def test_open_state_field_schema_dialog_allows_required_rw_schema(monkeypatch) -> None:
    captured: dict[str, bool] = {}

    class _FakeSchemaDialog:
        def __init__(self, _parent: object, *, title: str, schema: object, read_only: bool) -> None:
            del _parent, title, schema
            captured["read_only"] = bool(read_only)

        def exec_(self) -> int:
            return QtWidgets.QDialog.Rejected

    monkeypatch.setattr(actions, "SchemaBuilderDialog", _FakeSchemaDialog)
    spec = F8ServiceSpec(
        serviceClass="f8.tests.schema-dialog-state-rw",
        label="Schema Dialog State RW",
        editPolicy=F8SpecEditPolicy(stateFields=editable_collection_edit_policy()),
        stateFields=[
            F8StateSpec(name="value", valueSchema=number_schema(), access=F8StateAccess.rw, required=True)
        ],
    )
    node_item = _NodeItemStub(_BackendNode(spec))

    actions.open_state_field_schema_dialog(node_item, field_name="value")

    assert captured["read_only"] is False


def test_open_state_field_schema_dialog_locks_explicitly_locked_schema(monkeypatch) -> None:
    captured: dict[str, bool] = {}

    class _FakeSchemaDialog:
        def __init__(self, _parent: object, *, title: str, schema: object, read_only: bool) -> None:
            del _parent, title, schema
            captured["read_only"] = bool(read_only)

        def exec_(self) -> int:
            return QtWidgets.QDialog.Rejected

    monkeypatch.setattr(actions, "SchemaBuilderDialog", _FakeSchemaDialog)
    spec = F8ServiceSpec(
        serviceClass="f8.tests.schema-dialog-state-ro",
        label="Schema Dialog State RO",
        editPolicy=F8SpecEditPolicy(stateFields=editable_collection_edit_policy()),
        stateFields=[
            F8StateSpec(
                name="preview",
                valueSchema=number_schema(),
                access=F8StateAccess.ro,
                required=True,
                editPolicy=F8StateFieldEditPolicy(canEditValueSchema=False),
            )
        ],
    )
    node_item = _NodeItemStub(_BackendNode(spec))

    actions.open_state_field_schema_dialog(node_item, field_name="preview")

    assert captured["read_only"] is True


def test_state_field_context_menu_enables_schema_paste_when_policy_allows(monkeypatch) -> None:
    class _FakeAction:
        def __init__(self, text: str) -> None:
            self.text = str(text)
            self.enabled = True

        def setEnabled(self, enabled: bool) -> None:
            self.enabled = bool(enabled)

    class _FakeMenu:
        last: "_FakeMenu | None" = None

        def __init__(self) -> None:
            self.actions: list[_FakeAction] = []
            _FakeMenu.last = self

        def addAction(self, text: str) -> _FakeAction:
            action = _FakeAction(text)
            self.actions.append(action)
            return action

        def exec_(self, _screen_pos: object) -> None:
            return None

    monkeypatch.setattr(actions.QtWidgets, "QMenu", _FakeMenu)
    spec = F8ServiceSpec(
        serviceClass="f8.tests.schema-menu-state-rw",
        label="Schema Menu State RW",
        editPolicy=F8SpecEditPolicy(stateFields=editable_collection_edit_policy()),
        stateFields=[
            F8StateSpec(name="value", valueSchema=number_schema(), access=F8StateAccess.ro, required=True)
        ],
    )
    node_item = _NodeItemStub(_BackendNode(spec))

    actions.on_port_right_click(node_item, SimpleNamespace(name="[S]value"), object())

    menu = _FakeMenu.last
    assert menu is not None
    paste_action = [action for action in menu.actions if action.text == "Paste valueSchema"][0]
    assert paste_action.enabled is True


def test_state_field_context_menu_disables_schema_paste_when_policy_locks(monkeypatch) -> None:
    class _FakeAction:
        def __init__(self, text: str) -> None:
            self.text = str(text)
            self.enabled = True

        def setEnabled(self, enabled: bool) -> None:
            self.enabled = bool(enabled)

    class _FakeMenu:
        last: "_FakeMenu | None" = None

        def __init__(self) -> None:
            self.actions: list[_FakeAction] = []
            _FakeMenu.last = self

        def addAction(self, text: str) -> _FakeAction:
            action = _FakeAction(text)
            self.actions.append(action)
            return action

        def exec_(self, _screen_pos: object) -> None:
            return None

    monkeypatch.setattr(actions.QtWidgets, "QMenu", _FakeMenu)
    spec = F8ServiceSpec(
        serviceClass="f8.tests.schema-menu-state-locked",
        label="Schema Menu State Locked",
        editPolicy=F8SpecEditPolicy(stateFields=editable_collection_edit_policy()),
        stateFields=[
            F8StateSpec(
                name="preview",
                valueSchema=number_schema(),
                access=F8StateAccess.ro,
                required=True,
                editPolicy=F8StateFieldEditPolicy(canEditValueSchema=False),
            )
        ],
    )
    node_item = _NodeItemStub(_BackendNode(spec))

    actions.on_port_right_click(node_item, SimpleNamespace(name="[S]preview"), object())

    menu = _FakeMenu.last
    assert menu is not None
    paste_action = [action for action in menu.actions if action.text == "Paste valueSchema"][0]
    assert paste_action.enabled is False

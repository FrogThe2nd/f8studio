from __future__ import annotations

from collections import OrderedDict

from f8pysdk import (
    F8Command,
    F8DataPortSpec,
    F8OperatorSpec,
    F8OperatorSchemaVersion,
    F8StateAccess,
    F8StateSpec,
    any_schema,
)
from f8pystudio.nodegraph.patch_hub_nodeitem import F8StudioPatchHubNodeItem
from f8pystudio.nodegraph.service_basenode import F8StudioServiceNodeItem


class _FakePort:
    def __init__(self, name: str) -> None:
        self._name = str(name)

    def name(self) -> str:
        return self._name

    def isVisible(self) -> bool:
        return True


class _BackendStub:
    def __init__(self, spec: F8OperatorSpec) -> None:
        self.spec = spec

    def effective_state_fields(self) -> list[F8StateSpec]:
        return list(self.spec.stateFields or [])

    def effective_commands(self) -> list[F8Command]:
        return list(self.spec.commands or [])

    def data_port_show_on_node(self, name: str, *, is_in: bool) -> bool:
        del name, is_in
        return True


class _ServiceItemStub:
    def __init__(self, spec: F8OperatorSpec) -> None:
        self._backend = _BackendStub(spec)
        self._input_items = OrderedDict(
            (
                (_FakePort("[E]beta"), object()),
                (_FakePort("[E]alpha"), object()),
                (_FakePort("[D]bottom"), object()),
                (_FakePort("[D]top"), object()),
                (_FakePort("[C]stop"), object()),
                (_FakePort("[C]go"), object()),
                (_FakePort("[S]second"), object()),
                (_FakePort("[S]first"), object()),
            )
        )
        self._output_items = OrderedDict(
            (
                (_FakePort("omega[E]"), object()),
                (_FakePort("second[D]"), object()),
                (_FakePort("first[D]"), object()),
                (_FakePort("stop[C]"), object()),
                (_FakePort("go[C]"), object()),
                (_FakePort("second[S]"), object()),
                (_FakePort("first[S]"), object()),
            )
        )
        self._state_inline_proxies: dict[str, object] = {}
        self._command_inline_proxies: dict[str, object] = {}
        self._command_inline_buttons: dict[str, object] = {"go": object()}

    def _backend_node(self) -> _BackendStub:
        return self._backend

    def _command_names_with_inline_buttons(self) -> set[str]:
        return {str(name) for name in self._command_inline_buttons}

    def _ordered_visible_state_names_from_spec(self) -> list[str]:
        return F8StudioServiceNodeItem._ordered_visible_state_names_from_spec(self)

    def _ordered_visible_command_names_from_spec(self) -> list[str]:
        return F8StudioServiceNodeItem._ordered_visible_command_names_from_spec(self)


class _PatchHubItemStub:
    def __init__(self, spec: F8OperatorSpec) -> None:
        self._backend = _BackendStub(spec)
        self._input_items = OrderedDict(
            (
                (_FakePort("[D]bottom"), object()),
                (_FakePort("[D]top"), object()),
                (_FakePort("[S]second"), object()),
                (_FakePort("[S]first"), object()),
            )
        )
        self._output_items = OrderedDict(
            (
                (_FakePort("bottom[D]"), object()),
                (_FakePort("top[D]"), object()),
                (_FakePort("second[S]"), object()),
                (_FakePort("first[S]"), object()),
            )
        )

    def _backend_node(self) -> _BackendStub:
        return self._backend

    @staticmethod
    def _port_name(port: _FakePort) -> str:
        return port.name()


def _make_spec() -> F8OperatorSpec:
    return F8OperatorSpec(
        schemaVersion=F8OperatorSchemaVersion.f8operator_1,
        serviceClass="svc",
        operatorClass="op",
        label="Test",
        execInPorts=["alpha", "beta"],
        execOutPorts=["omega"],
        dataInPorts=[
            F8DataPortSpec(name="top", valueSchema=any_schema()),
            F8DataPortSpec(name="bottom", valueSchema=any_schema()),
        ],
        dataOutPorts=[
            F8DataPortSpec(name="first", valueSchema=any_schema()),
            F8DataPortSpec(name="second", valueSchema=any_schema()),
        ],
        stateFields=[
            F8StateSpec(name="first", access=F8StateAccess.rw, showOnNode=True, valueSchema=any_schema()),
            F8StateSpec(name="second", access=F8StateAccess.rw, showOnNode=True, valueSchema=any_schema()),
        ],
        commands=[
            F8Command(name="go", showOnNode=True),
            F8Command(name="stop", showOnNode=True),
        ],
    )


def test_service_node_item_uses_spec_order_for_editable_groups() -> None:
    stub = _ServiceItemStub(_make_spec())

    exec_in = F8StudioServiceNodeItem._ordered_exec_port_names_for_layout(stub, is_in=True)
    data_in = F8StudioServiceNodeItem._ordered_data_port_names_for_layout(stub, is_in=True)
    state_names = F8StudioServiceNodeItem._visible_state_names_for_layout(stub)
    command_names = F8StudioServiceNodeItem._visible_command_names_for_layout(stub)
    command_ports = F8StudioServiceNodeItem._ordered_command_port_names_for_layout(stub, is_in=True)

    assert exec_in == ["[E]alpha", "[E]beta"]
    assert data_in == ["[D]top", "[D]bottom"]
    assert state_names == ["first", "second"]
    assert command_names == ["go", "stop"]
    assert command_ports == ["[C]stop"]


def test_patch_hub_item_uses_spec_order_for_terminal_layout() -> None:
    stub = _PatchHubItemStub(_make_spec())

    data_names = F8StudioPatchHubNodeItem._terminal_names(stub, kind="data")
    state_names = F8StudioPatchHubNodeItem._terminal_names(stub, kind="state")

    assert data_names == ["top", "bottom"]
    assert state_names == ["first", "second"]

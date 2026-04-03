from __future__ import annotations

import os
import sys
from collections import OrderedDict
from types import SimpleNamespace

PKG_STUDIO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PKG_SDK = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "f8pysdk"))
for p in (PKG_STUDIO, PKG_SDK):
    if p not in sys.path:
        sys.path.insert(0, p)

from qtpy import QtCore

from f8pysdk import F8DataPortSpec, F8OperatorSchemaVersion, F8OperatorSpec, any_schema
from f8pystudio.nodegraph.service_basenode import F8StudioServiceNodeItem
from f8pystudio.nodegraph.viz_operator_nodeitem import (
    F8StudioVizOperatorNodeItem,
    _required_non_state_port_region_height,
)
from f8pystudio.nodegraph.ui_override_mutations import apply_named_order, get_list_order_override


def test_required_non_state_port_region_height_zero_when_no_ports() -> None:
    assert _required_non_state_port_region_height(port_height=10.0, in_count=0, out_count=0) == 0.0


def test_required_non_state_port_region_height_scales_with_max_side_count() -> None:
    h1 = _required_non_state_port_region_height(port_height=10.0, in_count=1, out_count=0)
    h4 = _required_non_state_port_region_height(port_height=10.0, in_count=4, out_count=1)
    assert h4 > h1


def test_required_non_state_port_region_height_respects_port_height() -> None:
    h_small = _required_non_state_port_region_height(port_height=20.0, in_count=3, out_count=3)
    h_big = _required_non_state_port_region_height(port_height=30.0, in_count=3, out_count=3)
    assert h_big > h_small


class _FakePort:
    def __init__(self, name: str) -> None:
        self._name = str(name)
        self._pos = (0.0, 0.0)

    def name(self) -> str:
        return self._name

    def isVisible(self) -> bool:
        return True

    def boundingRect(self) -> QtCore.QRectF:
        return QtCore.QRectF(0.0, 0.0, 10.0, 10.0)

    def setPos(self, x: float, y: float) -> None:
        self._pos = (float(x), float(y))

    def y(self) -> float:
        return float(self._pos[1])

    @property
    def display_name(self) -> bool:
        return True


class _BackendStub:
    def __init__(self, spec: F8OperatorSpec) -> None:
        self.spec = spec
        self._ui_overrides: dict[str, object] = {}

    def ui_overrides(self) -> dict[str, object]:
        return dict(self._ui_overrides)

    def set_ui_overrides(self, value: dict[str, object] | None, *, rebuild: bool = True) -> None:
        _ = rebuild
        self._ui_overrides = dict(value or {})

    def ordered_exec_port_names(self, *, is_in: bool) -> list[str]:
        return []

    def ordered_data_port_specs(self, *, is_in: bool) -> list[F8DataPortSpec]:
        ports = list(self.spec.dataInPorts or []) if is_in else list(self.spec.dataOutPorts or [])
        key = "dataInPorts" if is_in else "dataOutPorts"
        ordered_names = apply_named_order(
            base_names=[str(port.name or "") for port in ports],
            override_names=get_list_order_override(self, key=key),
        )
        by_name = {str(port.name or ""): port for port in ports}
        return [by_name[name] for name in ordered_names if name in by_name]

    def ordered_command_specs(self) -> list[object]:
        return []

    def data_port_show_on_node(self, name: str, *, is_in: bool) -> bool:
        del is_in
        return bool(str(name or "").strip())


class _FakeWidgetProxy:
    def __init__(self, *, y: float, height: float) -> None:
        self._y = float(y)
        self._height = float(height)

    def pos(self) -> QtCore.QPointF:
        return QtCore.QPointF(0.0, self._y)

    def boundingRect(self) -> QtCore.QRectF:
        return QtCore.QRectF(0.0, 0.0, 160.0, self._height)


class _VizItemStub:
    _port_group = staticmethod(F8StudioServiceNodeItem._port_group)

    def __init__(self, spec: F8OperatorSpec) -> None:
        self._backend = _BackendStub(spec)
        self._input_items = OrderedDict(
            (
                (_FakePort("[D]alpha"), object()),
                (_FakePort("[D]beta"), object()),
            )
        )
        self._output_items = OrderedDict()
        self._widgets = {"plot": _FakeWidgetProxy(y=20.0, height=100.0)}
        self._width = 200.0
        self._height = 150.0
        self._ports_end_y = 18.0
        self._command_inline_buttons: dict[str, object] = {}

    @property
    def inputs(self) -> list[_FakePort]:
        return list(self._input_items.keys())

    @property
    def outputs(self) -> list[_FakePort]:
        return list(self._output_items.keys())

    def _backend_node(self) -> _BackendStub:
        return self._backend

    def _port_name(self, port: _FakePort) -> str:
        return port.name()

    def _ordered_exec_port_names_for_layout(self, *, is_in: bool) -> list[str]:
        return F8StudioServiceNodeItem._ordered_exec_port_names_for_layout(self, is_in=is_in)

    def _ordered_data_port_names_for_layout(self, *, is_in: bool) -> list[str]:
        return F8StudioServiceNodeItem._ordered_data_port_names_for_layout(self, is_in=is_in)

    def _ordered_command_port_names_for_layout(self, *, is_in: bool) -> list[str]:
        _ = is_in
        return []

    def _ordered_non_state_ports_for_widget_region(self, *, is_in: bool) -> list[object]:
        return F8StudioVizOperatorNodeItem._ordered_non_state_ports_for_widget_region(self, is_in=is_in)

    def _command_names_with_inline_buttons(self) -> set[str]:
        return set()

    def boundingRect(self) -> QtCore.QRectF:
        return QtCore.QRectF(0.0, 0.0, self._width, self._height)

    def _measure_embedded_widget(self, widget: _FakeWidgetProxy) -> SimpleNamespace:
        rect = widget.boundingRect()
        return SimpleNamespace(width=float(rect.width()), height=float(rect.height()))


def test_viz_widget_region_port_alignment_uses_ordered_data_ports() -> None:
    spec = F8OperatorSpec(
        schemaVersion=F8OperatorSchemaVersion.f8operator_1,
        serviceClass="f8.test",
        operatorClass="f8.viz.wave",
        label="WaveViz",
        dataInPorts=[
            F8DataPortSpec(name="alpha", valueSchema=any_schema()),
            F8DataPortSpec(name="beta", valueSchema=any_schema()),
        ],
        dataOutPorts=[],
    )
    stub = _VizItemStub(spec)
    stub._backend.set_ui_overrides({"listOrder": {"dataInPorts": ["beta", "alpha"]}}, rebuild=False)

    F8StudioVizOperatorNodeItem._align_viz_ports_to_widgets(stub, v_offset=18.0)

    ordered_names = [port.name() for port in F8StudioVizOperatorNodeItem._ordered_non_state_ports_for_widget_region(stub, is_in=True)]
    alpha_port, beta_port = list(stub._input_items.keys())

    assert ordered_names == ["[D]beta", "[D]alpha"]
    assert beta_port.y() < alpha_port.y()

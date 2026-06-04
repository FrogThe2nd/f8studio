from __future__ import annotations

from dataclasses import dataclass

from f8pystudio.automation.domain import (
    ConnectPortsOp,
    CreateNodeOp,
    GraphPatch,
    SetNodeStateOp,
)

PYENGINE_SERVICE_CLASS = "f8.pyengine"
PYENGINE_SERVICE_NODE_TYPE = f"svc.{PYENGINE_SERVICE_CLASS}"
PHASE_NODE_TYPE = f"{PYENGINE_SERVICE_CLASS}.f8.phase"
COSINE_NODE_TYPE = f"{PYENGINE_SERVICE_CLASS}.f8.cosine"
RANGE_MAP_NODE_TYPE = f"{PYENGINE_SERVICE_CLASS}.f8.range_map"


@dataclass(frozen=True)
class PyEngineSineGraphNodeIds:
    service: str = "pyengine_service"
    phase: str = "pyengine_phase_1hz"
    sine: str = "pyengine_sine_transform"
    range_map: str = "pyengine_sine_0_100"


def build_pyengine_sine_0_100_patch(
    *,
    expected_revision: int | None,
    node_ids: PyEngineSineGraphNodeIds = PyEngineSineGraphNodeIds(),
) -> GraphPatch:
    return GraphPatch(
        expected_revision=expected_revision,
        label="typed pyengine 1hz sine 0-100 validation graph",
        ops=(
            CreateNodeOp(
                node_type=PYENGINE_SERVICE_NODE_TYPE,
                node_id=node_ids.service,
                name="PyEngine",
                pos=(0.0, 0.0),
            ),
            CreateNodeOp(
                node_type=PHASE_NODE_TYPE,
                node_id=node_ids.phase,
                name="1 Hz Phase",
                pos=(40.0, 80.0),
            ),
            CreateNodeOp(
                node_type=COSINE_NODE_TYPE,
                node_id=node_ids.sine,
                name="Sine Transform",
                pos=(280.0, 80.0),
            ),
            CreateNodeOp(
                node_type=RANGE_MAP_NODE_TYPE,
                node_id=node_ids.range_map,
                name="Sine 0-100",
                pos=(520.0, 80.0),
            ),
            SetNodeStateOp(node_id=node_ids.phase, field="hz", value=1.0),
            SetNodeStateOp(node_id=node_ids.sine, field="amp", value=1.0),
            SetNodeStateOp(node_id=node_ids.sine, field="dc", value=0.0),
            SetNodeStateOp(node_id=node_ids.sine, field="phaseOffset", value=0.75),
            SetNodeStateOp(node_id=node_ids.range_map, field="inMin", value=-1.0),
            SetNodeStateOp(node_id=node_ids.range_map, field="inMax", value=1.0),
            SetNodeStateOp(node_id=node_ids.range_map, field="outMin", value=0.0),
            SetNodeStateOp(node_id=node_ids.range_map, field="outMax", value=100.0),
            SetNodeStateOp(node_id=node_ids.range_map, field="curve", value="LINEAR"),
            ConnectPortsOp(
                from_node_id=node_ids.phase,
                from_port="phase",
                to_node_id=node_ids.sine,
                to_port="phase",
            ),
            ConnectPortsOp(
                from_node_id=node_ids.sine,
                from_port="value",
                to_node_id=node_ids.range_map,
                to_port="value",
            ),
        ),
    )


def pyengine_sine_sample_port(node_ids: PyEngineSineGraphNodeIds = PyEngineSineGraphNodeIds()) -> tuple[str, str]:
    return node_ids.range_map, "value"


def pyengine_sine_expected_range() -> tuple[float, float]:
    return 0.0, 100.0

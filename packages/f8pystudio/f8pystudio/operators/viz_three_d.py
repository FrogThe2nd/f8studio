from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from f8pysdk.codec import coerce_flag, coerce_int, coerce_float
from f8pysdk.specs import (
    F8DataPortSpec,
    F8OperatorSchemaVersion,
    F8OperatorSpec,
    F8RuntimeNode,
    F8SpecEditPolicy,
    F8StateAccess,
    F8StateSpec,
    array_schema,
    any_schema,
    boolean_schema,
    complex_object_schema,
    editable_collection_edit_policy,
    integer_schema,
    number_schema,
    string_schema,
)
from f8pysdk.nats_naming import ensure_token
from f8pysdk.registry import Registry

from f8pystudio.studio_specs.identifiers import SERVICE_CLASS
from f8pystudio.visualization.skeletons import skeleton_edges_for_nodes
from f8pystudio.contracts.ui_commands import emit_ui_command
from .categories import PALETTE_CATEGORY_VIZ
from ._viz_base import StudioVizRuntimeNodeBase, viz_sampling_state_fields

logger = logging.getLogger(__name__)

OPERATOR_CLASS = "f8.viz.three_d"
RENDERER_CLASS = "viz_three_d"

_LARGE_SKELETON_NODE_THRESHOLD = 192
_LARGE_SKELETON_BOX_THRESHOLD = 384
_LARGE_SKELETON_UI_FPS_CAP = 30
_MEDIUM_SKELETON_LABEL_BUDGET_THRESHOLD = 64
_MEDIUM_SKELETON_LABEL_BUDGET = 32
_LARGE_SKELETON_LABEL_BUDGET = 256


def _viz_bone_schema():
    return complex_object_schema(
        properties={
            "name": string_schema(),
            "pos": array_schema(items=number_schema()),
            "rot": array_schema(items=number_schema()),
        }
    )


def _viz_skeleton_input_schema():
    return complex_object_schema(
        properties={
            "type": string_schema(),
            "schema": string_schema(),
            "modelName": string_schema(),
            "name": string_schema(),
            "timestampMs": integer_schema(),
            "frameId": integer_schema(),
            "boneCount": integer_schema(),
            "skeletonProtocol": string_schema(),
            "bones": array_schema(items=_viz_bone_schema()),
        }
    )


@dataclass(frozen=True)
class _NodeViz:
    index: int
    name: str
    pos: tuple[float, float, float]
    rot: tuple[float, float, float, float] | None


@dataclass(frozen=True)
class _PersonViz:
    name: str
    bbox: tuple[float, float, float, float, float, float] | None
    skeleton_protocol: str
    skeleton_edges: list[tuple[int, int]] | None
    nodes: list[_NodeViz]


class VizThreeDRuntimeNode(StudioVizRuntimeNodeBase):
    """
    Studio-side runtime node for 3D skeleton visualization.

    Input:
    - Any editable data-in port:
      - skeleton dict
      - list of skeleton dict
      - single bone dict (`pos` + `rot`) which is auto-wrapped as a 1-bone skeleton

    Runtime always keeps ingesting and publishing UI payloads. If the detached
    viewer window is closed, render-side will pause drawing but reuse the latest
    payload immediately when the window is re-opened.
    """

    def __init__(self, *, node_id: str, node: F8RuntimeNode, initial_state: dict[str, Any] | None = None) -> None:
        super().__init__(
            node_id=ensure_token(node_id, label="node_id"),
            data_in_ports=[p.name for p in (node.dataInPorts or [])],
            data_out_ports=[],
            state_fields=[s.name for s in (node.stateFields or [])],
            initial_state=initial_state,
        )
        self._config_loaded = False
        self._refresh_task: asyncio.Task[object] | None = None
        self._scheduled_refresh_ms: int | None = None
        self._last_refresh_ms: int | None = None

        self._dirty = False
        self._latest_people_by_port: dict[str, list[_PersonViz]] = {}
        self._latest_people: list[_PersonViz] = []
        self._last_input_ts_ms_by_port: dict[str, int] = {}
        self._last_input_ts_ms: int = 0

        self._throttle_ms = 33
        self._world_up = "+y"
        self._show_person_boxes = True
        self._show_person_names = False
        self._show_bone_points = True
        self._show_skeleton_lines = True
        self._show_bone_axes = False
        self._show_bone_names = False
        self._max_people = 64
        self._max_bones_per_person = 256
        self._auto_zoom_on_new_people = False
        self._ui_fps_cap = 60
        self._marker_scale = 1.0

        self._warned_signatures: set[str] = set()

    async def close(self) -> None:
        task = self._refresh_task
        self._refresh_task = None
        self._scheduled_refresh_ms = None
        if task is not None:
            try:
                task.cancel()
            except (RuntimeError, TypeError):
                pass
            try:
                await asyncio.gather(task, return_exceptions=True)
            except (RuntimeError, TypeError):
                pass
        emit_ui_command(self.node_id, "viz.three_d.detach", {}, ts_ms=int(time.time() * 1000))

    async def on_data(self, port: str, value: Any, *, ts_ms: int | None = None) -> None:
        port_name = str(port or "").strip()
        if not port_name:
            return
        await self._ensure_config_loaded()

        now_ms = int(time.time() * 1000)
        input_ts_ms = int(ts_ms) if ts_ms is not None else now_ms
        self._last_input_ts_ms_by_port[port_name] = input_ts_ms
        self._latest_people_by_port[port_name] = self._extract_people(port=port_name, value=value)
        self._latest_people = self._aggregate_people_by_port()
        if self._last_input_ts_ms_by_port:
            self._last_input_ts_ms = max(self._last_input_ts_ms_by_port.values())
        else:
            self._last_input_ts_ms = input_ts_ms
        self._dirty = True
        await self._schedule_refresh(now_ms=now_ms)

    async def on_state(self, field: str, value: Any, *, ts_ms: int | None = None) -> None:
        await self._ensure_config_loaded()
        name = str(field or "").strip()
        if not name:
            return

        updated = False
        if name == "throttleMs":
            self._throttle_ms = coerce_int(value, default=self._throttle_ms, minimum=0, maximum=60000)
            updated = True
        elif name == "worldUp":
            self._world_up = self._coerce_world_up(value, default=self._world_up)
            updated = True
        elif name == "showPersonBoxes":
            self._show_person_boxes = coerce_flag(value, default=self._show_person_boxes)
            updated = True
        elif name == "showPersonNames":
            self._show_person_names = coerce_flag(value, default=self._show_person_names)
            updated = True
        elif name == "showBonePoints":
            self._show_bone_points = coerce_flag(value, default=self._show_bone_points)
            updated = True
        elif name == "showSkeletonLines":
            self._show_skeleton_lines = coerce_flag(value, default=self._show_skeleton_lines)
            updated = True
        elif name == "showBoneAxes":
            self._show_bone_axes = coerce_flag(value, default=self._show_bone_axes)
            updated = True
        elif name == "showBoneNames":
            self._show_bone_names = coerce_flag(value, default=self._show_bone_names)
            updated = True
        elif name == "maxPeople":
            self._max_people = coerce_int(value, default=self._max_people, minimum=1, maximum=4096)
            updated = True
        elif name == "maxBonesPerPerson":
            self._max_bones_per_person = coerce_int(value, default=self._max_bones_per_person, minimum=1, maximum=8192)
            updated = True
        elif name == "autoZoomOnNewPeople":
            self._auto_zoom_on_new_people = coerce_flag(value, default=self._auto_zoom_on_new_people)
            updated = True
        elif name == "uiFpsCap":
            self._ui_fps_cap = coerce_int(value, default=self._ui_fps_cap, minimum=1, maximum=120)
            updated = True
        elif name == "markerScale":
            self._marker_scale = coerce_float(value, default=self._marker_scale, minimum=0.1, maximum=100000.0)
            updated = True

        if not updated:
            return
        self._dirty = True
        now_ms = int(time.time() * 1000)
        if name == "worldUp":
            emit_ui_command(
                self.node_id,
                "viz.three_d.world_up",
                {"worldUp": str(self._world_up)},
                ts_ms=int(now_ms),
            )
        await self._schedule_refresh(now_ms=now_ms)

    async def _ensure_config_loaded(self) -> None:
        if self._config_loaded:
            return
        self._throttle_ms = coerce_int(
            await self._get_state_or_initial("throttleMs", 33), default=33, minimum=0, maximum=60000
        )
        self._world_up = self._coerce_world_up(await self._get_state_or_initial("worldUp", "+y"), default="+y")
        self._show_person_boxes = coerce_flag(
            await self._get_state_or_initial("showPersonBoxes", True), default=True
        )
        self._show_person_names = coerce_flag(
            await self._get_state_or_initial("showPersonNames", False), default=False
        )
        self._show_bone_points = coerce_flag(
            await self._get_state_or_initial("showBonePoints", True), default=True
        )
        self._show_skeleton_lines = coerce_flag(
            await self._get_state_or_initial("showSkeletonLines", True), default=True
        )
        self._show_bone_axes = coerce_flag(await self._get_state_or_initial("showBoneAxes", False), default=False)
        self._show_bone_names = coerce_flag(await self._get_state_or_initial("showBoneNames", False), default=False)
        self._max_people = coerce_int(
            await self._get_state_or_initial("maxPeople", 64), default=64, minimum=1, maximum=4096
        )
        self._max_bones_per_person = coerce_int(
            await self._get_state_or_initial("maxBonesPerPerson", 256), default=256, minimum=1, maximum=8192
        )
        self._auto_zoom_on_new_people = coerce_flag(
            await self._get_state_or_initial("autoZoomOnNewPeople", False), default=False
        )
        self._ui_fps_cap = coerce_int(
            await self._get_state_or_initial("uiFpsCap", 60), default=60, minimum=1, maximum=120
        )
        self._marker_scale = coerce_float(
            await self._get_state_or_initial("markerScale", 1.0), default=1.0, minimum=0.1, maximum=100000.0
        )
        self._config_loaded = True

    async def _get_state_or_initial(self, name: str, default: Any) -> Any:
        value: Any = None
        try:
            value = await self.get_state_value(name)
        except Exception:
            value = None
        if value is not None:
            return value
        return self._initial_state.get(name, default)

    async def _schedule_refresh(self, *, now_ms: int) -> None:
        throttle_ms = max(0, int(self._throttle_ms))
        last_refresh = int(self._last_refresh_ms or 0)
        if throttle_ms <= 0 or last_refresh <= 0:
            await self._flush(now_ms=now_ms)
            return

        target_ms = last_refresh + throttle_ms
        if int(now_ms) >= int(target_ms):
            await self._flush(now_ms=now_ms)
            return

        if self._refresh_task is not None and not self._refresh_task.done():
            return

        delay_ms = max(0, int(target_ms) - int(now_ms))
        self._scheduled_refresh_ms = int(target_ms)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._refresh_task = loop.create_task(
            self._flush_after(delay_ms=delay_ms), name=f"pystudio:skeleton3d:flush:{self.node_id}"
        )

    async def _flush_after(self, *, delay_ms: int) -> None:
        try:
            await asyncio.sleep(float(max(0, int(delay_ms))) / 1000.0)
        except (RuntimeError, TypeError, ValueError):
            return
        await self._flush(now_ms=int(time.time() * 1000))

    async def _flush(self, *, now_ms: int) -> None:
        self._scheduled_refresh_ms = None
        if not self._dirty and not self._latest_people:
            self._last_refresh_ms = int(now_ms)
            return

        payload = self._build_payload(now_ms=now_ms, people=self._latest_people)
        emit_ui_command(self.node_id, "viz.three_d.set", payload, ts_ms=int(now_ms))
        self._last_refresh_ms = int(now_ms)
        self._dirty = False

    def _build_payload(self, *, now_ms: int, people: list[_PersonViz]) -> dict[str, Any]:
        people_json: list[dict[str, Any]] = []
        total_nodes = 0
        for person in list(people)[: self._max_people]:
            nodes_json: list[dict[str, Any]] = []
            for node in list(person.nodes)[: self._max_bones_per_person]:
                item: dict[str, Any] = {
                    "index": int(node.index),
                    "name": str(node.name),
                    "pos": [float(node.pos[0]), float(node.pos[1]), float(node.pos[2])],
                }
                if node.rot is not None:
                    item["rot"] = [float(node.rot[0]), float(node.rot[1]), float(node.rot[2]), float(node.rot[3])]
                nodes_json.append(item)
            total_nodes += len(nodes_json)

            bbox = None
            if person.bbox is not None:
                bbox = [
                    float(person.bbox[0]),
                    float(person.bbox[1]),
                    float(person.bbox[2]),
                    float(person.bbox[3]),
                    float(person.bbox[4]),
                    float(person.bbox[5]),
                ]
            people_json.append(
                {
                    "name": str(person.name),
                    "bbox": bbox,
                    "skeletonProtocol": str(person.skeleton_protocol),
                    "skeletonEdges": (
                        [[int(a), int(b)] for a, b in person.skeleton_edges]
                        if person.skeleton_edges is not None
                        else None
                    ),
                    "nodes": nodes_json,
                }
            )

        return {
            "tsMs": int(now_ms if now_ms > 0 else self._last_input_ts_ms or int(time.time() * 1000)),
            "worldUp": str(self._world_up),
            "uiFpsCap": int(self._ui_fps_cap),
            "renderFlags": {
                "showPersonBoxes": bool(self._show_person_boxes),
                "showPersonNames": bool(self._show_person_names),
                "showBonePoints": bool(self._show_bone_points),
                "showSkeletonLines": bool(self._show_skeleton_lines),
                "showBoneAxes": bool(self._show_bone_axes),
                "showBoneNames": bool(self._show_bone_names),
                "autoZoomOnNewPeople": bool(self._auto_zoom_on_new_people),
                "markerScale": float(self._marker_scale),
            },
            "limits": {
                "maxPeople": int(self._max_people),
                "maxBonesPerPerson": int(self._max_bones_per_person),
            },
            "performanceHints": self._build_performance_hints(total_nodes=total_nodes),
            "people": people_json,
        }

    def _build_performance_hints(self, *, total_nodes: int) -> dict[str, Any]:
        rendered_nodes = max(0, int(total_nodes))
        large_skeleton_mode = rendered_nodes >= _LARGE_SKELETON_NODE_THRESHOLD
        max_visible_bone_labels: int | None
        if large_skeleton_mode:
            max_visible_bone_labels = _LARGE_SKELETON_LABEL_BUDGET
        elif rendered_nodes >= _MEDIUM_SKELETON_LABEL_BUDGET_THRESHOLD:
            max_visible_bone_labels = _MEDIUM_SKELETON_LABEL_BUDGET
        else:
            max_visible_bone_labels = None
        return {
            "totalNodes": rendered_nodes,
            "largeSkeletonMode": large_skeleton_mode,
            "suppressBoneAxes": False,
            "suppressBoneNames": False,
            "suppressAxisTree": False,
            "suppressPersonBoxes": rendered_nodes >= _LARGE_SKELETON_BOX_THRESHOLD,
            "maxVisibleBoneLabels": max_visible_bone_labels,
            "recommendedFpsCap": (
                min(int(self._ui_fps_cap), _LARGE_SKELETON_UI_FPS_CAP)
                if large_skeleton_mode
                else int(self._ui_fps_cap)
            ),
        }

    def _aggregate_people_by_port(self) -> list[_PersonViz]:
        aggregated: list[_PersonViz] = []
        preferred = [str(name) for name in (self.data_in_ports or []) if str(name).strip()]
        known_ports = set(preferred)
        available_ports = list(self._latest_people_by_port.keys())
        unknown_ports = [name for name in available_ports if name not in known_ports]
        ordered_ports = preferred + unknown_ports

        for port_name in ordered_ports:
            for person in self._latest_people_by_port.get(port_name, []):
                aggregated.append(person)
                if len(aggregated) >= self._max_people:
                    return aggregated
        return aggregated

    def _extract_people(self, *, port: str, value: Any) -> list[_PersonViz]:
        raw_people = self._normalize_people_payload(port=port, value=value)
        if raw_people is None:
            self._log_bad_input_once(port=port, value=value)
            return []

        out: list[_PersonViz] = []
        for index, payload in enumerate(raw_people):
            person = self._extract_person(payload=payload, index=index, source_port=port)
            if person is not None:
                out.append(person)
            if len(out) >= self._max_people:
                break
        return out

    def _normalize_people_payload(self, *, port: str, value: Any) -> list[dict[str, Any]] | None:
        if isinstance(value, dict):
            if self._looks_like_skeleton_payload(value):
                return [value]
            wrapped_bone = self._wrap_bone_as_skeleton(port=port, bone_payload=value)
            if wrapped_bone is not None:
                return [wrapped_bone]
            return None

        if not isinstance(value, list):
            return None

        skeletons: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            if self._looks_like_skeleton_payload(item):
                skeletons.append(item)
        if skeletons:
            return skeletons
        return None

    @staticmethod
    def _looks_like_skeleton_payload(value: dict[str, Any]) -> bool:
        bones = value.get("bones")
        return isinstance(bones, list)

    def _wrap_bone_as_skeleton(self, *, port: str, bone_payload: dict[str, Any]) -> dict[str, Any] | None:
        pos = self._coerce_vec3(bone_payload.get("pos"))
        rot = self._coerce_quat(bone_payload.get("rot"))
        if pos is None or rot is None:
            return None
        bone_name_any = bone_payload.get("name")
        bone_name = str(bone_name_any).strip() if bone_name_any is not None else ""
        if not bone_name:
            bone_name = "bone_0"
        return {
            "type": "bones",
            "schema": "bone",
            "modelName": str(port),
            "name": str(port),
            "timestampMs": None,
            "frameId": None,
            "boneCount": 1,
            "skeletonProtocol": "none",
            "bones": [
                {
                    "name": bone_name,
                    "pos": [float(pos[0]), float(pos[1]), float(pos[2])],
                    "rot": [float(rot[0]), float(rot[1]), float(rot[2]), float(rot[3])],
                }
            ],
        }

    def _extract_person(self, *, payload: dict[str, Any], index: int, source_port: str) -> _PersonViz | None:
        person_name = self._extract_person_name(payload=payload, index=index, source_port=source_port)
        skeleton_protocol = self._extract_protocol(payload)
        bones_any = payload.get("bones")
        if not isinstance(bones_any, list):
            return _PersonViz(
                name=person_name,
                bbox=None,
                skeleton_protocol=skeleton_protocol,
                skeleton_edges=None,
                nodes=[],
            )

        nodes: list[_NodeViz] = []
        for bone_index, raw_bone in enumerate(bones_any):
            if not isinstance(raw_bone, dict):
                continue
            node = self._extract_node(raw_bone=raw_bone, index=bone_index)
            if node is None:
                continue
            nodes.append(node)
            if len(nodes) >= self._max_bones_per_person:
                break
        node_names = [node.name for node in nodes]
        skeleton_edges = skeleton_edges_for_nodes(skeleton_protocol, node_names)
        return _PersonViz(
            name=person_name,
            bbox=self._compute_bbox(nodes),
            skeleton_protocol=skeleton_protocol,
            skeleton_edges=skeleton_edges,
            nodes=nodes,
        )

    @staticmethod
    def _extract_protocol(payload: dict[str, Any]) -> str:
        value = payload.get("skeletonProtocol")
        text = str(value or "").strip().lower()
        if not text:
            return "none"
        return text

    @staticmethod
    def _extract_person_name(*, payload: dict[str, Any], index: int, source_port: str) -> str:
        base_name = ""
        for key in ("modelName", "name", "character", "actor"):
            value = payload.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                base_name = text
                break
        if not base_name:
            base_name = f"Person_{index + 1}"
        return f"{source_port}:{base_name}"

    @staticmethod
    def _extract_node(*, raw_bone: dict[str, Any], index: int) -> _NodeViz | None:
        pos = VizThreeDRuntimeNode._coerce_vec3(raw_bone.get("pos"))
        if pos is None:
            return None
        name_any = raw_bone.get("name")
        node_name = str(name_any).strip() if name_any is not None else ""
        if not node_name:
            node_name = f"bone_{index}"
        rot = VizThreeDRuntimeNode._coerce_quat(raw_bone.get("rot"))
        return _NodeViz(index=int(index), name=node_name, pos=pos, rot=rot)

    @staticmethod
    def _compute_bbox(nodes: list[_NodeViz]) -> tuple[float, float, float, float, float, float] | None:
        if not nodes:
            return None
        min_x = float(nodes[0].pos[0])
        min_y = float(nodes[0].pos[1])
        min_z = float(nodes[0].pos[2])
        max_x = float(nodes[0].pos[0])
        max_y = float(nodes[0].pos[1])
        max_z = float(nodes[0].pos[2])
        for node in nodes[1:]:
            x = float(node.pos[0])
            y = float(node.pos[1])
            z = float(node.pos[2])
            if x < min_x:
                min_x = x
            if y < min_y:
                min_y = y
            if z < min_z:
                min_z = z
            if x > max_x:
                max_x = x
            if y > max_y:
                max_y = y
            if z > max_z:
                max_z = z
        return (min_x, min_y, min_z, max_x, max_y, max_z)

    @staticmethod
    def _coerce_vec3(value: Any) -> tuple[float, float, float] | None:
        if not isinstance(value, (list, tuple)) or len(value) < 3:
            return None
        try:
            x = float(value[0])
            y = float(value[1])
            z = float(value[2])
        except (TypeError, ValueError):
            return None
        return (x, y, z)

    @staticmethod
    def _coerce_quat(value: Any) -> tuple[float, float, float, float] | None:
        if not isinstance(value, (list, tuple)) or len(value) < 4:
            return None
        try:
            qw = float(value[0])
            qx = float(value[1])
            qy = float(value[2])
            qz = float(value[3])
        except (TypeError, ValueError):
            return None
        return (qw, qx, qy, qz)

    @staticmethod
    def _coerce_world_up(value: Any, *, default: str) -> str:
        text = str(value or "").strip().lower()
        if text in ("+x", "-x", "+y", "-y", "+z", "-z"):
            return text
        if text == "x":
            return "+x"
        if text == "y":
            return "+y"
        if text == "z":
            return "+z"
        fallback = str(default or "+y").strip().lower()
        if fallback in ("+x", "-x", "+y", "-y", "+z", "-z"):
            return fallback
        return "+y"

    def _log_bad_input_once(self, *, port: str, value: Any) -> None:
        sig = f"{port}:{type(value).__name__}"
        if sig in self._warned_signatures:
            return
        self._warned_signatures.add(sig)
        logger.warning(
            "skeleton3d ignored invalid input port=%s type=%s nodeId=%s",
            port,
            type(value).__name__,
            self.node_id,
        )


def register_operator(registry: Registry) -> Registry:

    registry.register_operator(
        F8OperatorSpec(
            schemaVersion=F8OperatorSchemaVersion.f8operator_1,
            serviceClass=SERVICE_CLASS,
            paletteCategory=PALETTE_CATEGORY_VIZ,
            operatorClass=OPERATOR_CLASS,
            version="0.0.1",
            label="3D Viz",
            description="3D viewer for multi-person skeleton streams (Studio UI-only).",
            tags=["viz", "3d", "skeleton", "ui"],
            dataInPorts=[
                F8DataPortSpec(
                    name="skeletons",
                    description="Skeleton dict/list or single bone dict (auto-wrapped to 1-bone skeleton).",
                    valueSchema=any_schema(),
                ),
            ],
            dataOutPorts=[],
            editPolicy=F8SpecEditPolicy(dataInPorts=editable_collection_edit_policy()),
            rendererClass=RENDERER_CLASS,
            stateFields=[
                F8StateSpec(
                    name="throttleMs",
                    label="Push Throttle (ms)",
                    description="Runtime push interval to UI command channel.",
                    valueSchema=integer_schema(default=33, minimum=0, maximum=60000),
                    access=F8StateAccess.rw,
                    required=True,
                    showOnNode=False,
                ),
                F8StateSpec(
                    name="worldUp",
                    label="World Up",
                    description="World up axis for viewer transform.",
                    valueSchema=string_schema(default="+y", enum=["+x", "-x", "+y", "-y", "+z", "-z"]),
                    access=F8StateAccess.rw,
                    required=True,
                    showOnNode=True,
                ),
                F8StateSpec(
                    name="showPersonBoxes",
                    label="Show Person Boxes",
                    description="Display per-person 3D bounding boxes.",
                    valueSchema=boolean_schema(default=True),
                    access=F8StateAccess.rw,
                    required=True,
                    showOnNode=False,
                ),
                F8StateSpec(
                    name="showPersonNames",
                    label="Show Person Names",
                    description="Display per-person labels.",
                    valueSchema=boolean_schema(default=False),
                    access=F8StateAccess.rw,
                    required=True,
                    showOnNode=False,
                ),
                F8StateSpec(
                    name="showBonePoints",
                    label="Show Bone Points",
                    description="Display bone node points.",
                    valueSchema=boolean_schema(default=True),
                    access=F8StateAccess.rw,
                    required=True,
                    showOnNode=False,
                ),
                F8StateSpec(
                    name="showSkeletonLines",
                    label="Show Skeleton Lines",
                    description="Display protocol-based links for known skeleton protocols.",
                    valueSchema=boolean_schema(default=True),
                    access=F8StateAccess.rw,
                    required=True,
                    showOnNode=False,
                ),
                F8StateSpec(
                    name="showBoneAxes",
                    label="Show Bone Axes",
                    description="Display RGB axes per bone node.",
                    valueSchema=boolean_schema(default=False),
                    access=F8StateAccess.rw,
                    required=True,
                    showOnNode=False,
                ),
                F8StateSpec(
                    name="showBoneNames",
                    label="Show Bone Names",
                    description="Display labels per bone node.",
                    valueSchema=boolean_schema(default=False),
                    access=F8StateAccess.rw,
                    required=True,
                    showOnNode=True,
                ),
                F8StateSpec(
                    name="maxPeople",
                    label="Max People",
                    description="Maximum people rendered from each frame.",
                    valueSchema=integer_schema(default=64, minimum=1, maximum=4096),
                    access=F8StateAccess.rw,
                    required=True,
                    showOnNode=False,
                ),
                F8StateSpec(
                    name="maxBonesPerPerson",
                    label="Max Bones Per Person",
                    description="Maximum bones rendered for each person.",
                    valueSchema=integer_schema(default=256, minimum=1, maximum=8192),
                    access=F8StateAccess.rw,
                    required=True,
                    showOnNode=False,
                ),
                F8StateSpec(
                    name="autoZoomOnNewPeople",
                    label="Auto Zoom On New People",
                    description="Auto fit view when the person set changes.",
                    valueSchema=boolean_schema(default=False),
                    access=F8StateAccess.rw,
                    required=True,
                    showOnNode=False,
                ),
                F8StateSpec(
                    name="uiFpsCap",
                    label="UI FPS Cap",
                    description="Front-end render FPS cap.",
                    valueSchema=integer_schema(default=60, minimum=1, maximum=120),
                    access=F8StateAccess.rw,
                    required=True,
                    showOnNode=False,
                ),
                F8StateSpec(
                    name="markerScale",
                    label="Marker Scale",
                    description="Global scale for bone point size and bone axis size.",
                    valueSchema=number_schema(default=1.0, minimum=0.0, maximum=100000.0),
                    access=F8StateAccess.rw,
                    required=True,
                    showOnNode=False,
                ),
                *viz_sampling_state_fields(show_on_node=False),
            ],
        ),
        VizThreeDRuntimeNode,
        overwrite=True,
    )
    return registry

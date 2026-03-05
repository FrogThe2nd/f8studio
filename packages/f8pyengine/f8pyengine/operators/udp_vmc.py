from __future__ import annotations

import asyncio
import ipaddress
import logging
import math
import socket
import struct
from dataclasses import dataclass
from typing import Any

from f8pysdk import (
    F8DataPortSpec,
    F8OperatorSchemaVersion,
    F8OperatorSpec,
    F8RuntimeNode,
    F8StateAccess,
    F8StateSpec,
    array_schema,
    boolean_schema,
    complex_object_schema,
    integer_schema,
    number_schema,
    string_schema,
)
from f8pysdk.capabilities import EntrypointNode, NodeBus
from f8pysdk.executors.exec_flow import EntrypointContext
from f8pysdk.nats_naming import ensure_token
from f8pysdk.runtime_node import OperatorNode
from f8pysdk.runtime_node_registry import RuntimeNodeRegistry
from f8pysdk.time_utils import now_ms

from ..constants import SERVICE_CLASS

OPERATOR_CLASS = "f8.udp_vmc"
_VMC_MODEL_NAME = "VMC"
_VMC_BONE_POS_ADDR = "/VMC/Ext/Bone/Pos"
_VMC_ROOT_POS_ADDR = "/VMC/Ext/Root/Pos"
_VMC_OK_ADDR = "/VMC/Ext/OK"
logger = logging.getLogger(__name__)

_BONE_PARENT_CANDIDATES: dict[str, tuple[str, ...]] = {
    "Hips": (),
    "Spine": ("Hips",),
    "Chest": ("Spine",),
    "UpperChest": ("Chest", "Spine"),
    "Neck": ("UpperChest", "Chest", "Spine"),
    "Head": ("Neck", "UpperChest", "Chest", "Spine"),
    "LeftEye": ("Head",),
    "RightEye": ("Head",),
    "Jaw": ("Head",),
    "LeftUpperLeg": ("Hips",),
    "LeftLowerLeg": ("LeftUpperLeg",),
    "LeftFoot": ("LeftLowerLeg",),
    "LeftToes": ("LeftFoot",),
    "RightUpperLeg": ("Hips",),
    "RightLowerLeg": ("RightUpperLeg",),
    "RightFoot": ("RightLowerLeg",),
    "RightToes": ("RightFoot",),
    "LeftShoulder": ("UpperChest", "Chest", "Spine"),
    "LeftUpperArm": ("LeftShoulder", "UpperChest", "Chest", "Spine"),
    "LeftLowerArm": ("LeftUpperArm",),
    "LeftHand": ("LeftLowerArm",),
    "RightShoulder": ("UpperChest", "Chest", "Spine"),
    "RightUpperArm": ("RightShoulder", "UpperChest", "Chest", "Spine"),
    "RightLowerArm": ("RightUpperArm",),
    "RightHand": ("RightLowerArm",),
    "LeftThumbMetacarpal": ("LeftHand",),
    "LeftThumbProximal": ("LeftThumbMetacarpal", "LeftHand"),
    "LeftThumbIntermediate": ("LeftThumbProximal",),
    "LeftThumbDistal": ("LeftThumbIntermediate", "LeftThumbProximal"),
    "LeftIndexProximal": ("LeftHand",),
    "LeftIndexIntermediate": ("LeftIndexProximal",),
    "LeftIndexDistal": ("LeftIndexIntermediate",),
    "LeftMiddleProximal": ("LeftHand",),
    "LeftMiddleIntermediate": ("LeftMiddleProximal",),
    "LeftMiddleDistal": ("LeftMiddleIntermediate",),
    "LeftRingProximal": ("LeftHand",),
    "LeftRingIntermediate": ("LeftRingProximal",),
    "LeftRingDistal": ("LeftRingIntermediate",),
    "LeftLittleProximal": ("LeftHand",),
    "LeftLittleIntermediate": ("LeftLittleProximal",),
    "LeftLittleDistal": ("LeftLittleIntermediate",),
    "RightThumbMetacarpal": ("RightHand",),
    "RightThumbProximal": ("RightThumbMetacarpal", "RightHand"),
    "RightThumbIntermediate": ("RightThumbProximal",),
    "RightThumbDistal": ("RightThumbIntermediate", "RightThumbProximal"),
    "RightIndexProximal": ("RightHand",),
    "RightIndexIntermediate": ("RightIndexProximal",),
    "RightIndexDistal": ("RightIndexIntermediate",),
    "RightMiddleProximal": ("RightHand",),
    "RightMiddleIntermediate": ("RightMiddleProximal",),
    "RightMiddleDistal": ("RightMiddleIntermediate",),
    "RightRingProximal": ("RightHand",),
    "RightRingIntermediate": ("RightRingProximal",),
    "RightRingDistal": ("RightRingIntermediate",),
    "RightLittleProximal": ("RightHand",),
    "RightLittleIntermediate": ("RightLittleProximal",),
    "RightLittleDistal": ("RightLittleIntermediate",),
}

_BONE_ALIAS_TO_CANONICAL: dict[str, str] = {name.lower(): name for name in _BONE_PARENT_CANDIDATES}


@dataclass(frozen=True)
class _UdpConfig:
    bind_address: str
    port: int
    max_queue: int
    reuse_address: bool


@dataclass(frozen=True)
class _SkeletonEntry:
    rx_ts_ms: int
    payload: Any


def _skeleton_anim_schema():
    return complex_object_schema(
        properties={
            "normalizedTime": number_schema(),
            "layerIndex": integer_schema(),
            "clipName": string_schema(),
            "poseKey": string_schema(),
        }
    )


def _skeleton_trailer_schema():
    return complex_object_schema(
        properties={
            "magic": string_schema(),
            "extVersion": integer_schema(),
            "frameId": integer_schema(),
            "chunkIndex": integer_schema(),
            "chunkCount": integer_schema(),
            "totalBoneCount": integer_schema(),
            "characterId": integer_schema(),
            "assembledChunkCount": integer_schema(),
            "anim": _skeleton_anim_schema(),
        }
    )


def _skeleton_bone_schema():
    return complex_object_schema(
        properties={
            "name": string_schema(),
            "pos": array_schema(items=number_schema()),
            "rot": array_schema(items=number_schema()),
        }
    )


def _skeleton_payload_schema():
    return complex_object_schema(
        properties={
            "type": string_schema(),
            "modelName": string_schema(),
            "timestampMs": integer_schema(),
            "schema": string_schema(),
            "boneCount": integer_schema(),
            "bones": array_schema(items=_skeleton_bone_schema()),
            "trailer": _skeleton_trailer_schema(),
        }
    )


class _UdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, queue: asyncio.Queue[tuple[int, bytes, tuple[str, int]]], dropped_ref: list[int]) -> None:
        self._queue = queue
        self._dropped_ref = dropped_ref

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        item = (now_ms(), bytes(data), (str(addr[0]), int(addr[1])))
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            self._dropped_ref[0] += 1
            try:
                _ = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(item)
            except asyncio.QueueFull:
                pass


def _is_loopback_bind_address(value: str) -> bool:
    s = str(value or "").strip().lower()
    if not s:
        return False
    if s in ("localhost",):
        return True
    try:
        return bool(ipaddress.ip_address(s).is_loopback)
    except ValueError:
        return False


class UdpVmcRuntimeNode(OperatorNode, EntrypointNode):
    """Receive VMC (OSC over UDP) and emit skeleton payloads compatible with udp_skeleton."""

    def __init__(self, *, node_id: str, node: F8RuntimeNode, initial_state: dict[str, Any] | None = None) -> None:
        super().__init__(
            node_id=ensure_token(node_id, label="node_id"),
            data_in_ports=[p.name for p in (node.dataInPorts or [])],
            data_out_ports=[p.name for p in (node.dataOutPorts or [])],
            state_fields=[s.name for s in (node.stateFields or [])],
        )
        self._initial_state = dict(initial_state or {})

        self._lock = asyncio.Lock()
        self._models_lock = asyncio.Lock()
        self._cfg: _UdpConfig | None = None
        self._transport: asyncio.DatagramTransport | None = None
        self._queue: asyncio.Queue[tuple[int, bytes, tuple[str, int]]] | None = None
        self._dropped_ref: list[int] = [0]
        self._drain_task: asyncio.Task[object] | None = None

        self._packet_count = 0
        self._last_error: str | None = None
        self._allow_non_loopback_bind = self._parse_bool(
            self._initial_state.get("allowNonLoopbackBind"),
            default=False,
        )

        self._cleanup_after_ms = self._parse_int(self._initial_state.get("cleanupAfterMs", 10000), default=10000)
        self._selected_key = str(self._initial_state.get("selectedKey", "") or "")

        self._skeletons_by_key: dict[str, _SkeletonEntry] = {}
        self._bones_by_key: dict[str, dict[str, dict[str, Any]]] = {}
        self._pending_bones_by_key: dict[str, dict[str, dict[str, Any]]] = {}
        self._pending_root_by_key: dict[str, dict[str, Any]] = {}
        self._output_version = 0
        self._last_synced_keys: list[str] = []
        self._ctx_output_cache: dict[tuple[str, str | int | None], tuple[int, Any]] = {}
        self._entrypoint_ctx: EntrypointContext | None = None
        self._pending_exec_id: str | int | None = None
        self._emit_wakeup = asyncio.Event()
        self._emit_task: asyncio.Task[None] | None = None
        self._emit_seq = 0

    def attach(self, bus: Any) -> None:
        super().attach(bus)
        bus_like = bus if isinstance(bus, NodeBus) else None
        if bus_like is not None:
            try:
                loop = asyncio.get_running_loop()
                if not bool(bus_like.active):
                    loop.create_task(self._stop_receiver(), name=f"udp_vmc:deactivate:{self.node_id}")
                    return
                loop.create_task(self._ensure_receiver(), name=f"udp_vmc:attach_start:{self.node_id}")
            except RuntimeError:
                logger.exception("udp_vmc attach failed: no running loop nodeId=%s", self.node_id)

    async def on_lifecycle(self, active: bool, _meta: dict[str, Any]) -> None:
        if bool(active):
            await self._ensure_receiver()
        else:
            await self._stop_receiver()

    async def start_entrypoint(self, ctx: EntrypointContext) -> None:
        self._entrypoint_ctx = ctx

    async def stop_entrypoint(self) -> None:
        self._entrypoint_ctx = None
        await self._cancel_emit_task()

    def _bus_active(self) -> bool:
        bus = self._bus
        if bus is None:
            return True
        try:
            return bool(bus.active)
        except Exception:
            return True

    async def compute_output(self, port: str, ctx_id: str | int | None = None) -> Any:
        if not self._bus_active():
            await self._stop_receiver()
            return None
        p = str(port or "").strip()
        if p not in ("skeletons", "selectedSkeleton"):
            return None

        await self._ensure_receiver()
        await self._cleanup_stale()
        await self._sync_available_keys_and_selection()

        cache_key = (p, ctx_id)
        async with self._models_lock:
            current_version = int(self._output_version)
            cached = self._ctx_output_cache.get(cache_key)
            if cached is not None and int(cached[0]) == current_version:
                return cached[1]

            keys = sorted(self._skeletons_by_key.keys())
            if p == "skeletons":
                value = [self._skeletons_by_key[k].payload for k in keys]
            else:
                selected_key = str(self._selected_key or "").strip()
                selected = self._skeletons_by_key.get(selected_key) if selected_key else None
                value = None if selected is None else selected.payload
            if len(self._ctx_output_cache) > 512:
                self._ctx_output_cache.clear()
            self._ctx_output_cache[cache_key] = (current_version, value)
            return value

    async def on_state(self, field: str, value: Any, *, ts_ms: int | None = None) -> None:
        _ = ts_ms
        f = str(field)
        if f == "allowNonLoopbackBind":
            self._allow_non_loopback_bind = self._parse_bool(value, default=False)
            if self._bus_active():
                await self._ensure_receiver(force_restart=True)
            return
        if f in ("bindAddress", "port", "maxQueue", "reuseAddress"):
            if self._bus_active():
                await self._ensure_receiver(force_restart=True)
            else:
                await self._stop_receiver()
            return
        if f == "cleanupAfterMs":
            self._cleanup_after_ms = self._parse_int(value, default=self._cleanup_after_ms)
            await self._cleanup_stale()
            await self._sync_available_keys_and_selection()
            return
        if f == "selectedKey":
            selected_key = str(value or "").strip()
            if selected_key != self._selected_key:
                self._selected_key = selected_key
                self._bump_output_version()
            await self._sync_available_keys_and_selection()
            return

    async def close(self) -> None:
        await self.stop_entrypoint()
        await self._stop_receiver()

    @staticmethod
    def _parse_int(value: Any, *, default: int) -> int:
        if value is None:
            return int(default)
        if isinstance(value, bool):
            return int(default)
        try:
            return int(value)
        except (TypeError, ValueError):
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return int(default)

    @staticmethod
    def _parse_bool(value: Any, *, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        s = str(value or "").strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off", ""):
            return False
        return bool(default)

    async def validate_state(self, field: str, value: Any, *, ts_ms: int, meta: dict[str, Any]) -> Any:
        del ts_ms, meta
        f = str(field or "").strip()
        if f == "allowNonLoopbackBind":
            return self._parse_bool(value, default=False)
        if f == "bindAddress":
            bind_address = str(value or "").strip() or "127.0.0.1"
            allow_non_loopback = self._allow_non_loopback_bind
            if (not allow_non_loopback) and (not _is_loopback_bind_address(bind_address)):
                raise ValueError("bindAddress must be loopback unless allowNonLoopbackBind is true")
            return bind_address
        return value

    async def _read_cfg_from_state(self) -> _UdpConfig | None:
        bind_address = await self.get_state_value("bindAddress")
        if bind_address is None:
            bind_address = self._initial_state.get("bindAddress", "127.0.0.1")
        port = await self.get_state_value("port")
        if port is None:
            port = self._initial_state.get("port", 39539)
        max_queue = await self.get_state_value("maxQueue")
        if max_queue is None:
            max_queue = self._initial_state.get("maxQueue", 512)
        reuse_address = await self.get_state_value("reuseAddress")
        if reuse_address is None:
            reuse_address = self._initial_state.get("reuseAddress", False)

        bind_address_s = str(bind_address).strip() or "127.0.0.1"
        try:
            port_i = int(port)
        except (TypeError, ValueError):
            port_i = 39539
        try:
            max_q = int(max_queue)
        except (TypeError, ValueError):
            max_q = 512
        if port_i <= 0 or port_i >= 65536:
            self._last_error = f"Invalid port: {port_i}"
            return None
        max_q = max(1, min(4096, max_q))
        if (not self._allow_non_loopback_bind) and (not _is_loopback_bind_address(bind_address_s)):
            self._last_error = "bindAddress must be loopback unless allowNonLoopbackBind is true"
            return None

        if isinstance(reuse_address, bool):
            reuse_addr = reuse_address
        elif isinstance(reuse_address, (int, float)):
            reuse_addr = bool(reuse_address)
        else:
            reuse_addr = str(reuse_address).strip().lower() in ("1", "true", "yes", "on")

        return _UdpConfig(
            bind_address=bind_address_s,
            port=port_i,
            max_queue=max_q,
            reuse_address=reuse_addr,
        )

    async def _ensure_receiver(self, *, force_restart: bool = False) -> None:
        if not self._bus_active():
            await self._stop_receiver()
            return
        cfg = await self._read_cfg_from_state()
        async with self._lock:
            if cfg is None:
                await self._stop_receiver()
                return
            if not force_restart and self._cfg == cfg and self._transport is not None:
                return
            await self._stop_receiver()
            await self._start_receiver(cfg)

    async def _start_receiver(self, cfg: _UdpConfig) -> None:
        loop = asyncio.get_running_loop()
        self._cfg = cfg
        self._dropped_ref[0] = 0
        self._queue = asyncio.Queue(maxsize=int(cfg.max_queue))

        sock: socket.socket | None = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            if cfg.reuse_address:
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                except OSError:
                    pass
                try:
                    reuseport = socket.SO_REUSEPORT
                except AttributeError:
                    reuseport = None
                if reuseport is not None:
                    try:
                        sock.setsockopt(socket.SOL_SOCKET, reuseport, 1)
                    except OSError:
                        pass
            sock.bind((cfg.bind_address, cfg.port))
            sock.setblocking(False)

            transport, _protocol = await loop.create_datagram_endpoint(
                lambda: _UdpProtocol(self._queue, self._dropped_ref),
                sock=sock,
            )
            self._transport = transport  # type: ignore[assignment]
            self._last_error = None
        except Exception as exc:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            self._transport = None
            self._queue = None
            self._last_error = f"{type(exc).__name__}: {exc}"
            return

        self._drain_task = asyncio.create_task(self._drain_loop(), name=f"udp_vmc:{self.node_id}")

    async def _stop_receiver(self) -> None:
        t = self._drain_task
        self._drain_task = None
        if t is not None:
            t.cancel()
            await asyncio.gather(t, return_exceptions=True)
        tr = self._transport
        self._transport = None
        if tr is not None:
            tr.close()
        self._queue = None
        self._cfg = None

    async def _drain_loop(self) -> None:
        assert self._queue is not None
        q = self._queue
        while True:
            try:
                rx_ts_ms, raw, _addr = await q.get()
                if not self._bus_active():
                    continue
                self._packet_count += 1
                messages = self._decode_osc_packet(raw)
                if not messages:
                    continue

                keys_changed = False
                committed = False
                saw_ok = False
                async with self._models_lock:
                    pending_existing = self._pending_bones_by_key.get(_VMC_MODEL_NAME, {})
                    pending_working: dict[str, dict[str, Any]] = {
                        name: dict(bone) for name, bone in pending_existing.items()
                    }
                    root_working = self._pending_root_by_key.get(_VMC_MODEL_NAME)
                    has_bone_update = False

                    for address, args in messages:
                        normalized_address = str(address or "").strip().lower()
                        if normalized_address == _VMC_BONE_POS_ADDR.lower():
                            updated = self._update_bone_from_vmc(args)
                            if updated is None:
                                continue
                            bone_name, bone_payload = updated
                            pending_working[bone_name] = bone_payload
                            has_bone_update = True
                            continue
                        if normalized_address == _VMC_ROOT_POS_ADDR.lower():
                            root_parsed = self._parse_root_from_vmc(args)
                            if root_parsed is None:
                                continue
                            root_working = root_parsed
                            continue
                        if normalized_address == _VMC_OK_ADDR.lower():
                            saw_ok = True

                    self._pending_bones_by_key[_VMC_MODEL_NAME] = pending_working
                    if root_working is not None:
                        self._pending_root_by_key[_VMC_MODEL_NAME] = root_working

                    should_commit = has_bone_update or (saw_ok and bool(pending_working))

                    if should_commit:
                        local_bones = {name: dict(bone) for name, bone in pending_working.items()}
                        root_for_commit = self._pending_root_by_key.get(_VMC_MODEL_NAME)
                        live_bones = self._compute_world_bones(local_bones=local_bones, root=root_for_commit)
                        self._bones_by_key[_VMC_MODEL_NAME] = live_bones
                        payload = self._build_payload(rx_ts_ms=int(rx_ts_ms), bones_map=live_bones)
                        entry = _SkeletonEntry(rx_ts_ms=int(rx_ts_ms), payload=payload)
                        keys_changed = _VMC_MODEL_NAME not in self._skeletons_by_key
                        self._skeletons_by_key[_VMC_MODEL_NAME] = entry
                        self._bump_output_version()
                        committed = True

                if keys_changed:
                    await self._sync_available_keys_and_selection()
                if not committed:
                    continue
                self._emit_seq += 1
                self._request_exec_emit(exec_id=int(self._emit_seq))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("udp_vmc drain loop failed nodeId=%s", self.node_id)

    def _build_payload(self, *, rx_ts_ms: int, bones_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
        bones = [bones_map[name] for name in bones_map]
        return {
            "type": "skeleton_binary",
            "modelName": _VMC_MODEL_NAME,
            "timestampMs": int(rx_ts_ms),
            "schema": "f8.skeleton.v1",
            "boneCount": len(bones),
            "bones": bones,
            "trailer": None,
        }

    @staticmethod
    def _coerce_finite_float(value: Any) -> float | None:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        if math.isnan(numeric) or math.isinf(numeric):
            return None
        return float(numeric)

    @staticmethod
    def _canonicalize_bone_name(name: str) -> str:
        key = str(name or "").strip().lower()
        if not key:
            return ""
        canonical = _BONE_ALIAS_TO_CANONICAL.get(key)
        if canonical is not None:
            return canonical
        return str(name).strip()

    @staticmethod
    def _quat_mul(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        aw, ax, ay, az = a
        bw, bx, by, bz = b
        return (
            aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
        )

    @staticmethod
    def _quat_conjugate(q: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        return (q[0], -q[1], -q[2], -q[3])

    @staticmethod
    def _quat_normalize(q: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        norm = math.sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3])
        if norm <= 1e-12:
            return (1.0, 0.0, 0.0, 0.0)
        inv = 1.0 / norm
        return (q[0] * inv, q[1] * inv, q[2] * inv, q[3] * inv)

    @classmethod
    def _quat_rotate_vec(
        cls, q: tuple[float, float, float, float], v: tuple[float, float, float]
    ) -> tuple[float, float, float]:
        qn = cls._quat_normalize(q)
        p = (0.0, v[0], v[1], v[2])
        rotated = cls._quat_mul(cls._quat_mul(qn, p), cls._quat_conjugate(qn))
        return (rotated[1], rotated[2], rotated[3])

    def _parse_root_from_vmc(self, args: tuple[Any, ...]) -> dict[str, Any] | None:
        # Common VMC variants:
        # 1) name, px, py, pz, qx, qy, qz, qw
        # 2) px, py, pz, qx, qy, qz, qw
        values: list[float]
        if len(args) >= 8 and isinstance(args[0], str):
            values = []
            for idx in range(1, 8):
                value = self._coerce_finite_float(args[idx])
                if value is None:
                    return None
                values.append(value)
        elif len(args) >= 7:
            values = []
            for idx in range(0, 7):
                value = self._coerce_finite_float(args[idx])
                if value is None:
                    return None
                values.append(value)
        else:
            return None

        px, py, pz, qx, qy, qz, qw = values
        return {
            "pos": [px, py, pz],
            "rot": [qw, qx, qy, qz],
        }

    def _parse_pose(self, pose: dict[str, Any]) -> tuple[tuple[float, float, float], tuple[float, float, float, float]] | None:
        pos_raw = pose.get("pos")
        rot_raw = pose.get("rot")
        if (
            not isinstance(pos_raw, list)
            or not isinstance(rot_raw, list)
            or len(pos_raw) != 3
            or len(rot_raw) != 4
        ):
            return None
        try:
            pos = (float(pos_raw[0]), float(pos_raw[1]), float(pos_raw[2]))
            rot = (float(rot_raw[0]), float(rot_raw[1]), float(rot_raw[2]), float(rot_raw[3]))
        except (TypeError, ValueError):
            return None
        return (pos, self._quat_normalize(rot))

    def _compose_pose(
        self,
        parent_pos: tuple[float, float, float],
        parent_rot: tuple[float, float, float, float],
        local_pos: tuple[float, float, float],
        local_rot: tuple[float, float, float, float],
    ) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
        rotated_local_pos = self._quat_rotate_vec(parent_rot, local_pos)
        world_pos = (
            parent_pos[0] + rotated_local_pos[0],
            parent_pos[1] + rotated_local_pos[1],
            parent_pos[2] + rotated_local_pos[2],
        )
        world_rot = self._quat_normalize(self._quat_mul(parent_rot, local_rot))
        return (world_pos, world_rot)

    def _resolve_existing_parent(self, bone_name: str, local_bones: dict[str, dict[str, Any]]) -> str | None:
        candidates = _BONE_PARENT_CANDIDATES.get(bone_name)
        if not candidates:
            return None
        for candidate in candidates:
            if candidate in local_bones:
                return candidate
        return None

    def _compute_world_bones(
        self, *, local_bones: dict[str, dict[str, Any]], root: dict[str, Any] | None
    ) -> dict[str, dict[str, Any]]:
        parsed_root = self._parse_pose(root) if root is not None else None
        if parsed_root is None:
            root_pos = (0.0, 0.0, 0.0)
            root_rot = (1.0, 0.0, 0.0, 0.0)
        else:
            root_pos, root_rot = parsed_root

        world_pose_cache: dict[str, tuple[tuple[float, float, float], tuple[float, float, float, float]]] = {}
        visit_state: dict[str, int] = {}

        def _compute_for(name: str) -> tuple[tuple[float, float, float], tuple[float, float, float, float]] | None:
            pose_raw = local_bones.get(name)
            if pose_raw is None:
                return None
            cached = world_pose_cache.get(name)
            if cached is not None:
                return cached
            state = visit_state.get(name, 0)
            if state == 1:
                return None
            visit_state[name] = 1
            parsed_local = self._parse_pose(pose_raw)
            if parsed_local is None:
                visit_state[name] = 2
                return None
            local_pos, local_rot = parsed_local

            if name == "Hips":
                result = self._compose_pose(root_pos, root_rot, local_pos, local_rot)
            else:
                parent_name = self._resolve_existing_parent(name, local_bones)
                if parent_name is None:
                    # Unknown parent relationship: keep as-is to avoid incorrect expansion.
                    result = (local_pos, local_rot)
                else:
                    parent_world = _compute_for(parent_name)
                    if parent_world is None:
                        result = (local_pos, local_rot)
                    else:
                        parent_pos, parent_rot = parent_world
                        result = self._compose_pose(parent_pos, parent_rot, local_pos, local_rot)
            world_pose_cache[name] = result
            visit_state[name] = 2
            return result

        world_bones: dict[str, dict[str, Any]] = {}
        for name in local_bones:
            world_pose = _compute_for(name)
            if world_pose is None:
                continue
            pos, rot = world_pose
            world_bones[name] = {
                "name": name,
                "pos": [pos[0], pos[1], pos[2]],
                "rot": [rot[0], rot[1], rot[2], rot[3]],
            }
        return world_bones

    def _update_bone_from_vmc(self, args: tuple[Any, ...]) -> tuple[str, dict[str, Any]] | None:
        if len(args) < 8:
            return None
        bone_name = self._canonicalize_bone_name(str(args[0]).strip())
        if not bone_name:
            return None
        px = self._coerce_finite_float(args[1])
        py = self._coerce_finite_float(args[2])
        pz = self._coerce_finite_float(args[3])
        qx = self._coerce_finite_float(args[4])
        qy = self._coerce_finite_float(args[5])
        qz = self._coerce_finite_float(args[6])
        qw = self._coerce_finite_float(args[7])
        if px is None or py is None or pz is None or qx is None or qy is None or qz is None or qw is None:
            return None
        return (
            bone_name,
            {
                "name": bone_name,
                "pos": [px, py, pz],
                "rot": [qw, qx, qy, qz],
            },
        )

    def _request_exec_emit(self, *, exec_id: str | int) -> None:
        if self._entrypoint_ctx is None:
            return
        self._pending_exec_id = exec_id
        self._emit_wakeup.set()
        task = self._emit_task
        if task is not None and not task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._emit_task = loop.create_task(
            self._emit_exec_loop(),
            name=f"udp_vmc:emit_exec:{self.node_id}",
        )

    async def _emit_exec_loop(self) -> None:
        try:
            while True:
                await self._emit_wakeup.wait()
                self._emit_wakeup.clear()
                exec_id = self._pending_exec_id
                self._pending_exec_id = None
                if exec_id is None:
                    continue
                ctx = self._entrypoint_ctx
                if ctx is None:
                    continue
                try:
                    await ctx.emit_exec("packet", exec_id=exec_id)
                except Exception as exc:
                    logger.exception("[%s:udp_vmc] emit exec failed", self.node_id, exc_info=exc)
        except asyncio.CancelledError:
            raise
        finally:
            self._emit_task = None

    async def _cancel_emit_task(self) -> None:
        task = self._emit_task
        self._emit_task = None
        self._pending_exec_id = None
        self._emit_wakeup.clear()
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.exception("[%s:udp_vmc] stop emit task failed", self.node_id, exc_info=exc)

    def _bump_output_version(self) -> None:
        self._output_version += 1
        if len(self._ctx_output_cache) > 512:
            self._ctx_output_cache.clear()

    @staticmethod
    def _normalize_selected_key(keys: list[str], selected_key: str) -> str:
        if not keys:
            return ""
        if selected_key in keys:
            return selected_key
        return keys[0]

    async def _cleanup_stale(self, *, now_ts_ms: int | None = None) -> None:
        ttl_ms = int(self._cleanup_after_ms)
        if ttl_ms <= 0:
            return
        if now_ts_ms is None:
            now_ts_ms = int(now_ms())
        cutoff = int(now_ts_ms) - ttl_ms
        removed = False
        async with self._models_lock:
            for k, v in list(self._skeletons_by_key.items()):
                rx = int(v.rx_ts_ms)
                if rx and rx < cutoff:
                    self._skeletons_by_key.pop(k, None)
                    self._bones_by_key.pop(k, None)
                    self._pending_bones_by_key.pop(k, None)
                    self._pending_root_by_key.pop(k, None)
                    removed = True
            if removed:
                self._bump_output_version()
        if removed:
            await self._sync_available_keys_and_selection()

    async def _sync_available_keys_and_selection(self) -> None:
        async with self._models_lock:
            keys = sorted(self._skeletons_by_key.keys())
            selected_key = str(self._selected_key or "").strip()
        if keys != self._last_synced_keys:
            self._last_synced_keys = list(keys)
            await self.set_state("availableKeys", list(keys))
        next_selected_key = self._normalize_selected_key(keys, selected_key)
        if next_selected_key != self._selected_key:
            self._selected_key = next_selected_key
            self._bump_output_version()
            await self.set_state("selectedKey", next_selected_key)

    @staticmethod
    def _read_osc_string(buf: bytes, offset: int) -> tuple[str, int]:
        end = buf.find(b"\x00", offset)
        if end == -1:
            raise ValueError("Missing OSC string terminator")
        value = buf[offset:end].decode("utf-8", errors="ignore")
        end += 1
        pad = (4 - (end & 0x03)) & 0x03
        return value, end + pad

    @staticmethod
    def _decode_osc_message(data: bytes) -> tuple[str, tuple[Any, ...]] | None:
        if not data:
            return None
        try:
            offset = 0
            address, offset = UdpVmcRuntimeNode._read_osc_string(data, offset)
            if not address:
                return None
            typetags, offset = UdpVmcRuntimeNode._read_osc_string(data, offset)
            if not typetags.startswith(","):
                return (address, ())
            values: list[Any] = []
            for tag in typetags[1:]:
                if tag == "i":
                    if offset + 4 > len(data):
                        return None
                    values.append(struct.unpack(">i", data[offset : offset + 4])[0])
                    offset += 4
                elif tag == "f":
                    if offset + 4 > len(data):
                        return None
                    values.append(struct.unpack(">f", data[offset : offset + 4])[0])
                    offset += 4
                elif tag == "h":
                    if offset + 8 > len(data):
                        return None
                    values.append(struct.unpack(">q", data[offset : offset + 8])[0])
                    offset += 8
                elif tag == "d":
                    if offset + 8 > len(data):
                        return None
                    values.append(struct.unpack(">d", data[offset : offset + 8])[0])
                    offset += 8
                elif tag == "s":
                    text, offset = UdpVmcRuntimeNode._read_osc_string(data, offset)
                    values.append(text)
                elif tag == "T":
                    values.append(True)
                elif tag == "F":
                    values.append(False)
                elif tag in {"N", "I", "[", "]"}:
                    # Nil/Impulse/Array delimiters do not consume payload bytes.
                    values.append(None)
                elif tag in {"c", "r", "m"}:
                    if offset + 4 > len(data):
                        return None
                    offset += 4
                elif tag == "b":
                    if offset + 4 > len(data):
                        return None
                    size = struct.unpack(">i", data[offset : offset + 4])[0]
                    offset += 4
                    if size < 0 or offset + size > len(data):
                        return None
                    blob = data[offset : offset + size]
                    offset += size
                    pad = (4 - (size & 0x03)) & 0x03
                    if offset + pad > len(data):
                        return None
                    offset += pad
                    values.append(blob)
                else:
                    # Unknown payload-sized tags are skipped conservatively as 4 bytes.
                    if offset + 4 > len(data):
                        return None
                    offset += 4
            return (address, tuple(values))
        except (ValueError, struct.error):
            return None

    @staticmethod
    def _decode_osc_packet(packet: bytes) -> list[tuple[str, tuple[Any, ...]]]:
        messages: list[tuple[str, tuple[Any, ...]]] = []
        if not packet:
            return messages
        if packet.startswith(b"#bundle\x00"):
            if len(packet) < 16:
                return messages
            offset = 16
            while offset + 4 <= len(packet):
                size = struct.unpack(">I", packet[offset : offset + 4])[0]
                offset += 4
                if size <= 0 or offset + size > len(packet):
                    break
                element = packet[offset : offset + size]
                offset += size
                nested = UdpVmcRuntimeNode._decode_osc_packet(element)
                if nested:
                    messages.extend(nested)
            return messages
        decoded = UdpVmcRuntimeNode._decode_osc_message(packet)
        if decoded is not None:
            messages.append(decoded)
        return messages


UdpVmcRuntimeNode.SPEC = F8OperatorSpec(
    schemaVersion=F8OperatorSchemaVersion.f8operator_1,
    serviceClass=SERVICE_CLASS,
    operatorClass=OPERATOR_CLASS,
    version="0.0.1",
    label="UDP VMC",
    description="Receives VMC OSC packets, converts to skeleton payloads, and emits packet exec triggers.",
    tags=["io", "udp", "network", "skeleton", "mocap", "vmc", "osc"],
    execInPorts=[],
    execOutPorts=["packet"],
    dataOutPorts=[
        F8DataPortSpec(
            name="skeletons",
            description="List of latest payloads (ordered by key).",
            valueSchema=array_schema(items=_skeleton_payload_schema()),
        ),
        F8DataPortSpec(
            name="selectedSkeleton",
            description="Latest payload matching `selectedKey` (or None).",
            valueSchema=_skeleton_payload_schema(),
        ),
    ],
    stateFields=[
        F8StateSpec(
            name="bindAddress",
            label="Bind Address",
            description="Local address to bind (loopback by default).",
            valueSchema=string_schema(default="127.0.0.1"),
            access=F8StateAccess.rw,
            required=True,
            showOnNode=False,
        ),
        F8StateSpec(
            name="allowNonLoopbackBind",
            label="Allow Non-loopback Bind",
            description="When true, allow bindAddress values other than loopback.",
            valueSchema=boolean_schema(default=False),
            access=F8StateAccess.rw,
            required=True,
            showOnNode=False,
        ),
        F8StateSpec(
            name="port",
            label="Port",
            description="UDP listen port.",
            valueSchema=integer_schema(default=39539, minimum=1, maximum=65535),
            access=F8StateAccess.rw,
            required=True,
            showOnNode=True,
        ),
        F8StateSpec(
            name="maxQueue",
            label="Max Queue",
            description="Max queued packets before dropping (1..4096).",
            valueSchema=integer_schema(default=512, minimum=1, maximum=4096),
            access=F8StateAccess.rw,
            required=True,
            showOnNode=False,
        ),
        F8StateSpec(
            name="reuseAddress",
            label="Reuse Address",
            description="Best-effort: allow multiple listeners on same (address, port) if OS supports.",
            valueSchema=boolean_schema(default=False),
            access=F8StateAccess.rw,
            required=True,
            showOnNode=False,
        ),
        F8StateSpec(
            name="cleanupAfterMs",
            label="Cleanup After (ms)",
            description="Remove models that haven't updated for this many ms (<=0 disables cleanup).",
            valueSchema=integer_schema(default=10000, minimum=0, maximum=60_000_000),
            access=F8StateAccess.wo,
            required=True,
            showOnNode=False,
        ),
        F8StateSpec(
            name="selectedKey",
            label="Selected Key",
            description="If set and matches an available key, outputs `selectedSkeleton`; otherwise None.",
            valueSchema=string_schema(default=""),
            access=F8StateAccess.rw,
            required=True,
            uiControl="options:[availableKeys]",
            showOnNode=True,
        ),
        F8StateSpec(
            name="availableKeys",
            label="Available Keys",
            description="Read-only list of current keys (updated only on changes).",
            valueSchema=array_schema(items=string_schema()),
            access=F8StateAccess.ro,
            required=True,
            showOnNode=True,
        ),
    ],
)


def register_operator(registry: RuntimeNodeRegistry | None = None) -> RuntimeNodeRegistry:
    reg = registry or RuntimeNodeRegistry.instance()

    def _factory(node_id: str, node: F8RuntimeNode, initial_state: dict[str, Any]) -> OperatorNode:
        return UdpVmcRuntimeNode(node_id=node_id, node=node, initial_state=initial_state)

    reg.register(SERVICE_CLASS, OPERATOR_CLASS, _factory, overwrite=True)
    reg.register_operator_spec(UdpVmcRuntimeNode.SPEC, overwrite=True)
    return reg

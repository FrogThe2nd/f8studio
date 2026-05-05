from __future__ import annotations

import asyncio
import builtins
import inspect
import logging
import os
import time
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Callable

from f8pysdk.bus import ServiceBus
from f8pysdk.capabilities import ClosableNode, CommandableNode
from f8pysdk.codec import unwrap_json_value
from f8pysdk.nats_naming import ensure_token
from f8pysdk.nodes import ServiceNode
from f8pysdk.shm.video import VIDEO_FORMAT_BGRA32, VIDEO_FORMAT_FLOW2_F16
from f8pysdk.video_transport import (
    LatestVideoFrame,
    LatestVideoFrameTransport,
    LegacyShmLatestVideoFrameTransport,
    ZenohLatestVideoFrameTransport,
)

from .script_runtime_values import (
    PyScriptStatesView,
    normalize_script_output_value,
    normalize_script_output_value_fast,
)

logger = logging.getLogger(__name__)
VIDEO_TRANSPORT_LEGACY_SHM = "legacy_shm"
VIDEO_TRANSPORT_ZENOH = "zenoh"

try:
    import numpy as np
except ModuleNotFoundError:
    np = None  # type: ignore[assignment]


DEFAULT_CODE = (
    "# Hooks template (uncomment what you need):\n"
    "# - onStart(ctx)\n"
    "# - onStop(ctx)\n"
    "# - onPause(ctx, meta=None)\n"
    "# - onResume(ctx, meta=None)\n"
    "# - onState(ctx, field, value, ts_ms=None)\n"
    "# - onData(ctx, port, value, ts_ms=None)\n"
    "# - onTick(ctx, tick)\n"
    "# - onCommand(ctx, name, args, meta=None)\n"
    "#\n"
    "# Useful context helpers:\n"
    "# - ctx.states.<field> reads cached rw/ro/wo state snapshot\n"
    "#   - example: ctx.states.tickEnabled\n"
    "# - await ctx.read_state(field)  # fresh runtime read\n"
    "# - ctx.states.get(field)  # cached snapshot\n"
    "# - ctx.set_state(field, value)\n"
    "# - await ctx.set_state_async(field, value)\n"
    "# - ctx.emit(port, value)\n"
    "# - ctx.permission.local_exec_granted / ctx.permission.expires_ts_ms\n"
    "# - ctx.subscribe_video_latest(key, video_key='f8/svc/.../data/video', decode='auto')\n"
    "# - pkt = ctx.get_video_latest(key)\n"
    "# - ctx.subscribe_video_shm(key, shm_name, decode='auto')  # legacy compatibility\n"
    "# - TypeGuard helpers are available from f8_dynamic_inputs\n"
    "#   - example: from f8_dynamic_inputs import is_port_in\n"
    "#   - optional: from f8_dynamic_inputs import *\n"
    "#   - then: if is_port_in(value, port): ...\n"
    "# - State TypeGuard helpers are available from f8_dynamic_states\n"
    "#   - example: from f8_dynamic_states import is_state_tickEnabled\n"
    "#   - then: if is_state_tickEnabled(value, field): ...\n"
    "#\n"
    "from typing import TYPE_CHECKING, Any\n"
    "if TYPE_CHECKING:\n"
    "    from f8_script_api import F8PyScriptContext, F8States, F8Tick\n"
    "#\n"
    "def onStart(ctx: 'F8PyScriptContext') -> None:\n"
    "    ctx.log('pyscript started')\n"
    "\n"
    "# def onStop(ctx: 'F8PyScriptContext') -> None:\n"
    "#     ctx.log('pyscript stopped')\n"
    "#\n"
    "# def onPause(ctx: 'F8PyScriptContext', meta: dict[str, Any] | None = None) -> None:\n"
    "#     ctx.log(f'paused: {meta}')\n"
    "#\n"
    "# def onResume(ctx: 'F8PyScriptContext', meta: dict[str, Any] | None = None) -> None:\n"
    "#     ctx.log(f'resumed: {meta}')\n"
    "#\n"
    "# def onState(\n"
    "#     ctx: 'F8PyScriptContext',\n"
    "#     field: str,\n"
    "#     value: Any,\n"
    "#     ts_ms: int | None = None,\n"
    "# ) -> None:\n"
    "#     ctx.log(f'state {field}={value} ts_ms={ts_ms}')\n"
    "#\n"
    "# def onData(\n"
    "#     ctx: 'F8PyScriptContext',\n"
    "#     port: str,\n"
    "#     value: Any,\n"
    "#     ts_ms: int | None = None,\n"
    "# ) -> None:\n"
    "#     ctx.log(f'data port={port} value={value} ts_ms={ts_ms}')\n"
    "#\n"
    "# def onTick(ctx: 'F8PyScriptContext', tick: 'F8Tick') -> None:\n"
    "#     ctx.log(f'tick seq={tick.seq} tsMs={tick.tsMs} deltaMs={tick.deltaMs}')\n"
    "#\n"
    "# def onCommand(\n"
    "#     ctx: 'F8PyScriptContext',\n"
    "#     name: str,\n"
    "#     args: dict[str, Any],\n"
    "#     meta: dict[str, Any] | None = None,\n"
    "# ) -> dict[str, Any]:\n"
    "#     if name == 'ping':\n"
    "#         return {'ok': True, 'result': {'pong': True}}\n"
    "#     return {'ok': False, 'error': f'unknown command: {name}'}\n"
)

_SAFE_MODULES: set[str] = {
    "asyncio",
    "collections",
    "datetime",
    "functools",
    "itertools",
    "json",
    "math",
    "random",
    "re",
    "statistics",
    "time",
    "typing",
}

@dataclass
class _LatestVideoSubscription:
    key: str
    shm_name: str
    video_transport: str
    video_key: str
    decode_mode: str
    use_event: bool
    reader: LatestVideoFrameTransport | None = None
    task: asyncio.Task[object] | None = None
    latest_packet: dict[str, Any] | None = None
    last_frame_id: int = 0
    last_error_sig: str | None = None
    last_error_ts_ms: int = 0
    error_count: int = 0


def _video_subscription_source_metadata(sub: _LatestVideoSubscription) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "key": sub.key,
        "transport": sub.video_transport,
        "videoTransport": sub.video_transport,
    }
    if sub.video_transport == VIDEO_TRANSPORT_LEGACY_SHM:
        metadata["shmName"] = sub.shm_name
    else:
        metadata["videoKey"] = sub.video_key
    return metadata


def _video_subscription_status_metadata(sub: _LatestVideoSubscription) -> dict[str, Any]:
    metadata = _video_subscription_source_metadata(sub)
    metadata["decodeMode"] = sub.decode_mode
    metadata["hasPacket"] = sub.latest_packet is not None
    metadata["lastFrameId"] = int(sub.last_frame_id)
    metadata["errorCount"] = int(sub.error_count)
    return metadata


@dataclass(slots=True)
class PyScriptPermissionContext:
    local_exec_granted: bool
    expires_ts_ms: int | None
    grant_ts_ms: int
    session_id: str


@dataclass(slots=True)
class PyScriptServiceContext:
    _node: "PythonScriptServiceNode"
    service_id: str
    locals: dict[str, Any]
    _state_keys: tuple[str, ...]
    permission: PyScriptPermissionContext

    def with_permission(self, permission: PyScriptPermissionContext) -> "PyScriptServiceContext":
        return replace(self, permission=permission)

    @property
    def states(self) -> PyScriptStatesView:
        return self._node._build_states_view(self._state_keys)

    def log(self, message: object) -> None:
        logger.info("[%s:pyscript] %s", self.service_id, str(message))

    async def emit_async(self, port: str, value: Any) -> None:
        await self._node.emit(str(port), value)

    def emit(self, port: str, value: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            logger.error("[%s:pyscript] emit without running loop", self.service_id, exc_info=exc)
            return
        loop.create_task(self.emit_async(str(port), value), name=f"pyscript:emit:{self.service_id}:{port}")

    async def set_state_async(self, field: str, value: Any) -> None:
        await self._node._set_runtime_state(str(field), value)

    def set_state(self, field: str, value: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            logger.error("[%s:pyscript] set_state without running loop", self.service_id, exc_info=exc)
            return
        loop.create_task(
            self.set_state_async(str(field), value),
            name=f"pyscript:set_state:{self.service_id}:{field}",
        )

    async def read_state(self, field: str) -> Any:
        return await self._node.get_state_value(str(field))

    def subscribe_video_shm(self, key: str, shm_name: str, *, decode: str = "auto", use_event: bool = False) -> None:
        self.subscribe_video_latest(
            key,
            transport=VIDEO_TRANSPORT_LEGACY_SHM,
            shm_name=shm_name,
            decode=decode,
            use_event=use_event,
        )

    def subscribe_video_latest(
        self,
        key: str,
        *,
        video_key: str = "",
        transport: str = VIDEO_TRANSPORT_ZENOH,
        shm_name: str = "",
        decode: str = "auto",
        use_event: bool = False,
    ) -> None:
        key_name = str(key or "").strip()
        video_key_text = str(video_key or "").strip()
        shm = str(shm_name or "").strip()
        video_transport = self._node._normalize_video_transport(transport, video_key=video_key_text, shm_name=shm)
        if not key_name:
            return
        if video_transport == VIDEO_TRANSPORT_ZENOH and not video_key_text:
            return
        if video_transport == VIDEO_TRANSPORT_LEGACY_SHM and not shm:
            return
        self._node._unsubscribe_video_latest_sync(key_name)
        sub = _LatestVideoSubscription(
            key=key_name,
            shm_name=shm,
            video_transport=video_transport,
            video_key=video_key_text,
            decode_mode=self._node._normalize_decode_mode(decode),
            use_event=bool(use_event),
        )
        self._node._video_subscriptions[key_name] = sub
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            logger.error(
                "[%s:pyscript] subscribe_video_latest without running loop",
                self.service_id,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            return
        sub.task = loop.create_task(
            self._node._run_video_latest_subscription(key_name),
            name=f"pyscript:video_sub:{self.service_id}:{key_name}",
        )

    def get_video_shm(self, key: str) -> dict[str, Any] | None:
        return self.get_video_latest(key)

    def get_video_latest(self, key: str) -> dict[str, Any] | None:
        key_name = str(key or "").strip()
        if not key_name:
            return None
        sub = self._node._video_subscriptions.get(key_name)
        if sub is None:
            return None
        return self._node._copy_packet_for_script(sub.latest_packet)

    def unsubscribe_video_shm(self, key: str) -> None:
        self.unsubscribe_video_latest(key)

    def unsubscribe_video_latest(self, key: str) -> None:
        self._node._unsubscribe_video_latest_sync(str(key or "").strip())

    def list_video_shm_subscriptions(self) -> list[dict[str, Any]]:
        return self.list_video_latest_subscriptions()

    def list_video_latest_subscriptions(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for key_name in sorted(self._node._video_subscriptions.keys()):
            sub = self._node._video_subscriptions.get(key_name)
            if sub is None:
                continue
            items.append(_video_subscription_status_metadata(sub))
        return items

    async def exec_local(
        self,
        command: str,
        args: list[str] | tuple[str, ...] | None = None,
        *,
        timeout_ms: int | None = None,
        cwd: str | None = None,
        env: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._node._exec_local(
            command,
            args,
            timeoutMs=timeout_ms,
            cwd=cwd,
            env=env,
        )


class PythonScriptServiceNode(ServiceNode, CommandableNode, ClosableNode):
    def __init__(self, *, node_id: str, node: Any, initial_state: dict[str, Any] | None = None) -> None:
        super().__init__(
            node_id=ensure_token(node_id, label="node_id"),
            data_in_ports=[str(p.name) for p in list(node.dataInPorts or [])],
            data_out_ports=[str(p.name) for p in list(node.dataOutPorts or [])],
            state_fields=[str(s.name) for s in list(node.stateFields or [])],
        )
        self._readable_state_names = self._collect_readable_state_names(node)
        self._initial_state = dict(initial_state or {})

        self._code = str(self._initial_state.get("code") or DEFAULT_CODE)
        self._locals: dict[str, Any] = {}

        self._hook_on_start: Callable[..., Any] | None = None
        self._hook_on_stop: Callable[..., Any] | None = None
        self._hook_on_pause: Callable[..., Any] | None = None
        self._hook_on_resume: Callable[..., Any] | None = None
        self._hook_on_state: Callable[..., Any] | None = None
        self._hook_on_data: Callable[..., Any] | None = None
        self._hook_on_tick: Callable[..., Any] | None = None
        self._hook_on_command: Callable[..., Any] | None = None
        self._hook_on_start_is_async = False
        self._hook_on_stop_is_async = False
        self._hook_on_pause_is_async = False
        self._hook_on_resume_is_async = False
        self._hook_on_state_is_async = False
        self._hook_on_data_is_async = False
        self._hook_on_tick_is_async = False
        self._hook_on_command_is_async = False
        self._hook_on_state_maybe_awaitable = False
        self._hook_on_data_maybe_awaitable = False
        self._hook_on_tick_maybe_awaitable = False

        self._started = False
        self._paused = False
        self._active = True
        self._closing = False

        self._last_error: str | None = None
        self._error_dedupe: dict[str, int] = {}
        self._self_state_writes: dict[str, Any] = {}

        self._tick_enabled = bool(self._initial_state.get("tickEnabled") or False)
        self._tick_ms = self._coerce_tick_ms(self._initial_state.get("tickMs"), default=100)
        self._tick_task: asyncio.Task[object] | None = None
        self._tick_seq = 0

        self._video_subscriptions: dict[str, _LatestVideoSubscription] = {}
        self._zenoh_config_path: str | None = None
        self._zenoh_connect: tuple[str, ...] = ()
        self._zenoh_listen: tuple[str, ...] = ()
        self._zenoh_shm_pool_bytes = 256 * 1024 * 1024

        self._local_exec_granted = False
        self._grant_session_id = ""
        self._grant_ts_ms = 0
        self._grant_expires_ts_ms: int | None = None

        self._data_out_port_set: set[str] = set()
        self._single_data_out_port: str | None = None
        self._has_out_port = False
        self._refresh_data_out_port_cache()

        self._ctx: PyScriptServiceContext = self._build_ctx()

        self._compile_and_start()

    def __del__(self) -> None:
        if self._started and not self._closing:
            try:
                self._invoke_sync(self._hook_on_stop, self._hook_on_stop_is_async, "onStop")
            except Exception as exc:
                logger.error("[%s:pyscript] __del__ onStop failed", self.node_id, exc_info=exc)
        try:
            self._shutdown_video_subscriptions_sync()
        except Exception as exc:
            logger.error("[%s:pyscript] __del__ video cleanup failed", self.node_id, exc_info=exc)

    def attach(self, bus: Any) -> None:
        super().attach(bus)
        if isinstance(bus, ServiceBus):
            cfg = bus.config
            self._zenoh_config_path = cfg.zenoh_config_path
            self._zenoh_connect = cfg.zenoh_connect
            self._zenoh_listen = cfg.zenoh_listen
            self._zenoh_shm_pool_bytes = cfg.zenoh_shm_pool_bytes

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000.0)

    @staticmethod
    def _collect_readable_state_names(node: Any) -> tuple[str, ...]:
        out: list[str] = []
        seen: set[str] = set()
        for state in list(node.stateFields or []):
            name = str(state.name or "").strip()
            access_raw = state.access
            if not name or name in seen:
                continue
            access_value = access_raw.value if isinstance(access_raw, Enum) else access_raw
            access = str(access_value or "").strip().lower()
            if access not in ("rw", "ro", "wo"):
                continue
            seen.add(name)
            out.append(name)
        return tuple(out)

    @staticmethod
    def _coerce_tick_ms(value: Any, *, default: int) -> int:
        try:
            out = int(value)
        except (TypeError, ValueError):
            out = int(default)
        return max(1, out)

    @staticmethod
    def _normalize_decode_mode(decode: Any) -> str:
        mode = str(decode or "auto").strip().lower()
        if mode in ("none", "auto"):
            return mode
        return "auto"

    @staticmethod
    def _normalize_video_transport(transport: Any, *, video_key: str, shm_name: str) -> str:
        mode = str(transport or "").strip().lower()
        _ = shm_name
        if mode == VIDEO_TRANSPORT_ZENOH:
            return VIDEO_TRANSPORT_ZENOH
        if mode in (VIDEO_TRANSPORT_LEGACY_SHM, "shm"):
            return VIDEO_TRANSPORT_LEGACY_SHM
        if str(video_key or "").strip():
            return VIDEO_TRANSPORT_ZENOH
        return VIDEO_TRANSPORT_ZENOH

    @staticmethod
    def _header_to_dict(frame: LatestVideoFrame) -> dict[str, int]:
        return {
            "frameId": int(frame.frame_id),
            "tsMs": int(frame.ts_ms),
            "width": int(frame.width),
            "height": int(frame.height),
            "pitch": int(frame.pitch),
            "fmt": int(frame.fmt),
            "notifySeq": 0,
        }

    @staticmethod
    def _compact_rows(raw: bytes, *, width: int, height: int, pitch: int, row_bytes: int) -> bytes | None:
        if width <= 0 or height <= 0 or pitch < row_bytes or row_bytes <= 0:
            return None
        if pitch == row_bytes:
            return raw
        compact = bytearray(row_bytes * height)
        for y in range(height):
            src_off = y * pitch
            dst_off = y * row_bytes
            compact[dst_off : dst_off + row_bytes] = raw[src_off : src_off + row_bytes]
        return bytes(compact)

    def _decode_video_payload(self, *, header: dict[str, int], raw: bytes, decode_mode: str) -> dict[str, Any] | None:
        if decode_mode != "auto":
            return None
        width = int(header.get("width") or 0)
        height = int(header.get("height") or 0)
        pitch = int(header.get("pitch") or 0)
        fmt = int(header.get("fmt") or 0)
        if width <= 0 or height <= 0 or pitch <= 0:
            return None

        if fmt == VIDEO_FORMAT_BGRA32:
            row_bytes = width * 4
            compact = self._compact_rows(raw, width=width, height=height, pitch=pitch, row_bytes=row_bytes)
            if compact is None:
                return {"kind": "bgra32", "shape": [height, width, 4], "data": None}
            data = None
            if np is not None:
                try:
                    arr = np.frombuffer(compact, dtype=np.uint8)
                    if int(arr.size) == (height * width * 4):
                        data = arr.reshape(height, width, 4)
                except Exception as exc:
                    logger.warning("[%s:pyscript] decode bgra failed", self.node_id, exc_info=exc)
            return {"kind": "bgra32", "shape": [height, width, 4], "data": data}

        if fmt == VIDEO_FORMAT_FLOW2_F16:
            row_bytes = width * 4
            compact = self._compact_rows(raw, width=width, height=height, pitch=pitch, row_bytes=row_bytes)
            if compact is None:
                return {"kind": "flow2_f16", "shape": [height, width, 2], "data": None}
            data = None
            if np is not None:
                try:
                    arr = np.frombuffer(compact, dtype="<f2")
                    if int(arr.size) == (height * width * 2):
                        data = arr.reshape(height, width, 2)
                except Exception as exc:
                    logger.warning("[%s:pyscript] decode flow failed", self.node_id, exc_info=exc)
            return {"kind": "flow2_f16", "shape": [height, width, 2], "data": data}

        return None

    def _copy_packet_for_script(self, packet: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(packet, dict):
            return None
        header_src = packet.get("header")
        meta_src = packet.get("meta")
        decoded_src = packet.get("decoded")
        out: dict[str, Any] = {
            "header": dict(header_src) if isinstance(header_src, dict) else {},
            "meta": dict(meta_src) if isinstance(meta_src, dict) else {},
            "raw": packet.get("raw"),
            "decoded": None,
        }
        if isinstance(decoded_src, dict):
            decoded_out: dict[str, Any] = {}
            if "kind" in decoded_src:
                decoded_out["kind"] = decoded_src.get("kind")
            if "shape" in decoded_src:
                decoded_out["shape"] = list(decoded_src.get("shape") or [])
            if "data" in decoded_src:
                decoded_out["data"] = decoded_src.get("data")
            out["decoded"] = decoded_out
        return out

    def _set_error(self, stage: str, exc: BaseException) -> None:
        msg = f"{stage}: {exc}"
        self._last_error = msg
        logger.error("[%s:pyscript] error %s", self.node_id, msg, exc_info=exc)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _report_monitor_error() -> None:
            try:
                await self.report_error(
                    "PYSCRIPT_ERROR",
                    msg,
                    severity="error",
                    fingerprint=f"pyscript:{stage}:{type(exc).__name__}:{exc}",
                )
            except Exception as set_exc:
                logger.error("[%s:pyscript] report monitor error failed", self.node_id, exc_info=set_exc)

        loop.create_task(_report_monitor_error(), name=f"pyscript:reportError:{self.node_id}")

    def _log_error_deduped(self, key: str, message: str, exc: BaseException) -> None:
        now_ms = self._now_ms()
        last_ts = int(self._error_dedupe.get(key) or 0)
        if (now_ms - last_ts) < 2000:
            return
        self._error_dedupe[key] = now_ms
        logger.error("[%s:pyscript] %s", self.node_id, message, exc_info=exc)

    async def _set_runtime_state(self, field: str, value: Any) -> None:
        self._self_state_writes[str(field)] = value
        await self.set_state(str(field), value)

    def _permission_context(self) -> PyScriptPermissionContext:
        allowed = self._is_local_exec_allowed()
        return PyScriptPermissionContext(
            local_exec_granted=bool(allowed),
            expires_ts_ms=int(self._grant_expires_ts_ms) if self._grant_expires_ts_ms is not None else None,
            grant_ts_ms=int(self._grant_ts_ms or 0),
            session_id=str(self._grant_session_id or ""),
        )

    def _permission_view(self) -> dict[str, Any]:
        permission = self._permission_context()
        return {
            "localExecGranted": bool(permission.local_exec_granted),
            "expiresTsMs": permission.expires_ts_ms,
            "grantTsMs": int(permission.grant_ts_ms),
            "sessionId": str(permission.session_id),
        }

    def _is_local_exec_allowed(self) -> bool:
        if not self._local_exec_granted:
            return False
        expiry = self._grant_expires_ts_ms
        if expiry is not None and self._now_ms() > int(expiry):
            self._local_exec_granted = False
            self._grant_expires_ts_ms = None
            return False
        return True

    def _build_script_builtins(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "abs": builtins.abs,
            "all": builtins.all,
            "any": builtins.any,
            "bool": builtins.bool,
            "bytes": builtins.bytes,
            "callable": builtins.callable,
            "dict": builtins.dict,
            "enumerate": builtins.enumerate,
            "float": builtins.float,
            "int": builtins.int,
            "isinstance": builtins.isinstance,
            "len": builtins.len,
            "list": builtins.list,
            "max": builtins.max,
            "min": builtins.min,
            "pow": builtins.pow,
            "print": builtins.print,
            "range": builtins.range,
            "round": builtins.round,
            "set": builtins.set,
            "slice": builtins.slice,
            "sorted": builtins.sorted,
            "str": builtins.str,
            "sum": builtins.sum,
            "tuple": builtins.tuple,
            "zip": builtins.zip,
            "Exception": builtins.Exception,
            "ValueError": builtins.ValueError,
            "TypeError": builtins.TypeError,
            "RuntimeError": builtins.RuntimeError,
            "KeyError": builtins.KeyError,
            "IndexError": builtins.IndexError,
            "PermissionError": builtins.PermissionError,
        }

        def _guarded_import(name: str, globals_obj: Any = None, locals_obj: Any = None, fromlist: Any = (), level: int = 0) -> Any:
            module_name = str(name or "").strip()
            if not module_name:
                raise ImportError("empty module name")
            root_name = module_name.split(".")[0]
            if not self._is_local_exec_allowed() and root_name not in _SAFE_MODULES:
                raise PermissionError(f"import blocked without local exec grant: {module_name}")
            return builtins.__import__(module_name, globals_obj, locals_obj, fromlist, int(level))

        out["__import__"] = _guarded_import
        return out

    async def _exec_local(
        self,
        command: str,
        args: list[str] | tuple[str, ...] | None = None,
        *,
        timeoutMs: int | None = None,
        cwd: str | None = None,
        env: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self._is_local_exec_allowed():
            raise PermissionError("local execution is not granted")

        cmd = str(command or "").strip()
        if not cmd:
            raise ValueError("exec_local command is empty")

        argv = [cmd]
        if args is not None:
            for item in list(args):
                argv.append(str(item))

        run_cwd = str(cwd).strip() if cwd is not None else None
        proc_env: dict[str, str] | None = None
        if env is not None:
            proc_env = dict(os.environ)
            for key, value in dict(env).items():
                proc_env[str(key)] = str(value)

        logger.info("[%s:pyscript] exec_local command=%s args=%s", self.node_id, cmd, argv[1:])
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=run_cwd,
            env=proc_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        timeout_s: float | None
        if timeoutMs is None:
            timeout_s = None
        else:
            timeout_s = max(0.001, float(timeoutMs) / 1000.0)

        try:
            if timeout_s is None:
                stdout_raw, stderr_raw = await proc.communicate()
            else:
                stdout_raw, stderr_raw = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise TimeoutError(f"exec_local timeout command={cmd}") from exc

        stdout_text = (stdout_raw or b"").decode("utf-8", errors="replace")
        stderr_text = (stderr_raw or b"").decode("utf-8", errors="replace")
        return {
            "ok": bool(proc.returncode == 0),
            "returncode": int(proc.returncode or 0),
            "stdout": stdout_text,
            "stderr": stderr_text,
            "command": cmd,
            "args": argv[1:],
        }

    def _build_ctx(self) -> PyScriptServiceContext:
        return PyScriptServiceContext(
            _node=self,
            service_id=self.node_id,
            locals=self._locals,
            _state_keys=self._readable_state_names,
            permission=self._permission_context(),
        )

    def _build_invoke_ctx(self) -> PyScriptServiceContext:
        permission = self._permission_context()
        if self._ctx.permission == permission:
            return self._ctx
        self._ctx = self._ctx.with_permission(permission)
        return self._ctx

    def _refresh_data_out_port_cache(self) -> None:
        self._data_out_port_set = {str(name) for name in self.data_out_ports}
        if len(self._data_out_port_set) == 1:
            self._single_data_out_port = next(iter(self._data_out_port_set))
        else:
            self._single_data_out_port = None
        self._has_out_port = "out" in self._data_out_port_set

    def _build_states_view(self, state_keys: tuple[str, ...]) -> PyScriptStatesView:
        resolved_keys = [str(key) for key in state_keys if str(key)]
        if not resolved_keys:
            resolved_keys = [str(key) for key in self.state_fields if str(key)]
        if not resolved_keys:
            resolved_keys = [str(key) for key in self._readable_state_names if str(key)]
        unique_keys = tuple(sorted({key for key in resolved_keys if key}))
        snapshot: dict[str, Any] = {}
        for key in unique_keys:
            snapshot[str(key)] = self.get_state_cached(str(key), None)
        return PyScriptStatesView(snapshot)

    def _compile_script(self, code: str) -> None:
        env: dict[str, Any] = {"__builtins__": self._build_script_builtins()}
        exec(code, env, env)

        on_start = env.get("onStart")
        on_stop = env.get("onStop")
        on_pause = env.get("onPause")
        on_resume = env.get("onResume")
        on_state = env.get("onState")
        on_data = env.get("onData")
        on_tick = env.get("onTick")
        on_command = env.get("onCommand")

        self._hook_on_start = on_start if callable(on_start) else None
        self._hook_on_stop = on_stop if callable(on_stop) else None
        self._hook_on_pause = on_pause if callable(on_pause) else None
        self._hook_on_resume = on_resume if callable(on_resume) else None
        self._hook_on_state = on_state if callable(on_state) else None
        self._hook_on_data = on_data if callable(on_data) else None
        self._hook_on_tick = on_tick if callable(on_tick) else None
        self._hook_on_command = on_command if callable(on_command) else None
        self._hook_on_start_is_async = inspect.iscoroutinefunction(self._hook_on_start) if self._hook_on_start is not None else False
        self._hook_on_stop_is_async = inspect.iscoroutinefunction(self._hook_on_stop) if self._hook_on_stop is not None else False
        self._hook_on_pause_is_async = inspect.iscoroutinefunction(self._hook_on_pause) if self._hook_on_pause is not None else False
        self._hook_on_resume_is_async = (
            inspect.iscoroutinefunction(self._hook_on_resume) if self._hook_on_resume is not None else False
        )
        self._hook_on_state_is_async = inspect.iscoroutinefunction(self._hook_on_state) if self._hook_on_state is not None else False
        self._hook_on_data_is_async = inspect.iscoroutinefunction(self._hook_on_data) if self._hook_on_data is not None else False
        self._hook_on_tick_is_async = inspect.iscoroutinefunction(self._hook_on_tick) if self._hook_on_tick is not None else False
        self._hook_on_command_is_async = (
            inspect.iscoroutinefunction(self._hook_on_command) if self._hook_on_command is not None else False
        )
        self._hook_on_state_maybe_awaitable = bool(self._hook_on_state is not None and not self._hook_on_state_is_async)
        self._hook_on_data_maybe_awaitable = bool(self._hook_on_data is not None and not self._hook_on_data_is_async)
        self._hook_on_tick_maybe_awaitable = bool(self._hook_on_tick is not None and not self._hook_on_tick_is_async)

    def _compile_and_start(self) -> None:
        if self._started:
            self._invoke_sync(self._hook_on_stop, self._hook_on_stop_is_async, "onStop")
        self._shutdown_video_subscriptions_sync()

        self._locals = {}
        self._ctx = self._build_ctx()

        code = str(self._code or "").replace("\r\n", "\n").replace("\r", "\n").expandtabs(4)
        self._hook_on_start = None
        self._hook_on_stop = None
        self._hook_on_pause = None
        self._hook_on_resume = None
        self._hook_on_state = None
        self._hook_on_data = None
        self._hook_on_tick = None
        self._hook_on_command = None
        self._hook_on_start_is_async = False
        self._hook_on_stop_is_async = False
        self._hook_on_pause_is_async = False
        self._hook_on_resume_is_async = False
        self._hook_on_state_is_async = False
        self._hook_on_data_is_async = False
        self._hook_on_tick_is_async = False
        self._hook_on_command_is_async = False
        self._hook_on_state_maybe_awaitable = False
        self._hook_on_data_maybe_awaitable = False
        self._hook_on_tick_maybe_awaitable = False

        try:
            self._compile_script(code)
        except Exception as exc:
            self._started = False
            self._set_error("compile", exc)
            return

        self._invoke_sync(self._hook_on_start, self._hook_on_start_is_async, "onStart")
        self._started = True
        self._paused = False
        self._ensure_tick_task()

    def _invoke_sync(self, hook: Callable[..., Any] | None, hook_is_async: bool, stage: str, *args: Any) -> Any:
        if hook is None:
            return None
        try:
            invoke_ctx = self._build_invoke_ctx()
            if hook_is_async:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError as exc:
                    self._set_error(stage, exc)
                    return None
                coroutine = hook(invoke_ctx, *args)
                loop.create_task(coroutine, name=f"pyscript:{stage}:{self.node_id}")
                return None

            result = hook(invoke_ctx, *args)
            if inspect.isawaitable(result):
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError as exc:
                    self._set_error(stage, exc)
                    return None
                loop.create_task(result, name=f"pyscript:{stage}:{self.node_id}")
                return None
            return result
        except Exception as exc:
            self._set_error(stage, exc)
            return None

    async def _invoke_async(
        self,
        hook: Callable[..., Any] | None,
        hook_is_async: bool,
        hook_maybe_awaitable: bool,
        stage: str,
        *args: Any,
    ) -> tuple[Any, bool]:
        if hook is None:
            return None, hook_maybe_awaitable
        try:
            invoke_ctx = self._build_invoke_ctx()
            if hook_is_async:
                return await hook(invoke_ctx, *args), hook_maybe_awaitable
            result = hook(invoke_ctx, *args)
            if not hook_maybe_awaitable:
                return result, False
            if inspect.isawaitable(result):
                return await result, True
            return result, False
        except Exception as exc:
            self._set_error(stage, exc)
            raise

    def _extract_outputs(self, result: Any) -> dict[str, Any]:
        if result is None:
            return {}
        if isinstance(result, dict):
            raw_outputs = result.get("outputs")
            if isinstance(raw_outputs, dict):
                single_out_port = self._single_data_out_port
                if single_out_port is not None and len(raw_outputs) == 1 and single_out_port in raw_outputs:
                    return {single_out_port: normalize_script_output_value_fast(raw_outputs.get(single_out_port))}
                if len(raw_outputs) == 1:
                    for key, value in raw_outputs.items():
                        return {str(key): normalize_script_output_value_fast(value)}
                return {str(k): normalize_script_output_value_fast(v) for k, v in raw_outputs.items()}
            if "outputs" in result:
                raise ValueError("script return field 'outputs' must be a dict")
            raise ValueError("script dict return must include an 'outputs' dict")
        if not self._has_out_port:
            return {}
        return {"out": normalize_script_output_value_fast(result)}

    async def _emit_outputs(self, result: Any) -> None:
        try:
            outputs = self._extract_outputs(result)
        except ValueError as exc:
            self._set_error("result", exc)
            return
        for out_port, out_value in outputs.items():
            try:
                await self.emit(str(out_port), out_value)
            except Exception as exc:
                self._log_error_deduped("emit_output", f"emit failed port={out_port}", exc)

    def _unsubscribe_video_latest_sync(self, key: str) -> bool:
        sub = self._video_subscriptions.pop(str(key), None)
        if sub is None:
            return False
        task = sub.task
        sub.task = None
        if task is not None and not task.done():
            task.cancel()
            return True
        self._close_video_sub_reader(sub)
        return True

    def _shutdown_video_subscriptions_sync(self) -> None:
        keys = list(self._video_subscriptions.keys())
        for key in keys:
            self._unsubscribe_video_latest_sync(key)

    async def _shutdown_video_subscriptions_async(self) -> None:
        keys = list(self._video_subscriptions.keys())
        tasks: list[asyncio.Task[object]] = []
        for key in keys:
            sub = self._video_subscriptions.pop(key, None)
            if sub is None:
                continue
            task = sub.task
            sub.task = None
            if task is not None and not task.done():
                task.cancel()
                tasks.append(task)
            self._close_video_sub_reader(sub)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _close_video_sub_reader(self, sub: _LatestVideoSubscription) -> None:
        reader = sub.reader
        sub.reader = None
        if reader is None:
            return
        try:
            reader.close()
        except Exception as exc:
            self._log_error_deduped(
                f"video_reader_close:{sub.key}",
                f"video reader close failed key={sub.key} transport={sub.video_transport}",
                exc,
            )

    def _open_video_sub_reader(self, sub: _LatestVideoSubscription) -> LatestVideoFrameTransport:
        if sub.video_transport == VIDEO_TRANSPORT_ZENOH:
            return ZenohLatestVideoFrameTransport.open_subscriber(
                sub.video_key,
                config_path=self._zenoh_config_path,
                connect=self._zenoh_connect,
                listen=self._zenoh_listen,
                shm_pool_bytes=self._zenoh_shm_pool_bytes,
            )
        return LegacyShmLatestVideoFrameTransport.open_reader(sub.shm_name, use_event=bool(sub.use_event))

    async def _run_video_latest_subscription(self, key: str) -> None:
        sub_ref: _LatestVideoSubscription | None = None
        try:
            while True:
                sub = self._video_subscriptions.get(key)
                if sub is None:
                    return
                sub_ref = sub
                if not self._active or self._paused:
                    await asyncio.sleep(0.05)
                    continue

                if sub.reader is None:
                    try:
                        sub.reader = self._open_video_sub_reader(sub)
                    except Exception as exc:
                        sub.error_count += 1
                        self._log_error_deduped(
                            f"video_open:{sub.key}",
                            (
                                f"video latest open failed key={sub.key} transport={sub.video_transport} "
                                f"videoKey={sub.video_key} shm={sub.shm_name}"
                            ),
                            exc,
                        )
                        await asyncio.sleep(0.2)
                        continue

                assert sub.reader is not None
                try:
                    frame = sub.reader.wait_latest(20)
                    if frame is None:
                        await asyncio.sleep(0)
                        continue

                    try:
                        frame_id = int(frame.frame_id)
                        if frame_id <= 0:
                            await asyncio.sleep(0)
                            continue
                        if frame_id == int(sub.last_frame_id) and sub.latest_packet is not None:
                            await asyncio.sleep(0)
                            continue

                        frame_bytes = int(frame.frame_bytes)
                        if frame_bytes <= 0 or frame_bytes > len(frame.payload):
                            await asyncio.sleep(0)
                            continue

                        raw = bytes(frame.payload[:frame_bytes])
                        header_dict = self._header_to_dict(frame)
                        decoded = self._decode_video_payload(header=header_dict, raw=raw, decode_mode=sub.decode_mode)
                        sub.latest_packet = {
                            "header": header_dict,
                            "raw": raw,
                            "decoded": decoded,
                            "meta": _video_subscription_source_metadata(sub)
                            | {
                                "decodeMode": sub.decode_mode,
                                "lastUpdateMs": self._now_ms(),
                            },
                        }
                        sub.last_frame_id = frame_id
                    finally:
                        frame.release()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    sub.error_count += 1
                    self._log_error_deduped(
                        f"video_read:{sub.key}",
                        (
                            f"video latest read failed key={sub.key} transport={sub.video_transport} "
                            f"videoKey={sub.video_key} shm={sub.shm_name}"
                        ),
                        exc,
                    )
                    self._close_video_sub_reader(sub)
                    await asyncio.sleep(0.2)
        finally:
            if sub_ref is not None and sub_ref.reader is not None:
                self._close_video_sub_reader(sub_ref)

    def _ensure_tick_task(self) -> None:
        if self._tick_task is not None and not self._tick_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._tick_task = loop.create_task(self._tick_loop(), name=f"pyscript:tick:{self.node_id}")

    async def _tick_loop(self) -> None:
        last_tick_ts = self._now_ms()
        next_deadline = time.monotonic()
        while not self._closing:
            try:
                if not self._started or self._paused or not self._tick_enabled:
                    next_deadline = time.monotonic()
                    await asyncio.sleep(0.05)
                    continue

                now_mono = time.monotonic()
                wait_s = next_deadline - now_mono
                if wait_s > 0:
                    await asyncio.sleep(wait_s)

                # Re-check lifecycle gates after sleep to avoid a late extra tick
                # when pause/deactivate arrives during the wait interval.
                if not self._started or self._paused or not self._tick_enabled:
                    next_deadline = time.monotonic()
                    continue

                current_ts_ms = self._now_ms()
                delta_ms = max(0, current_ts_ms - last_tick_ts)
                last_tick_ts = current_ts_ms
                self._tick_seq += 1
                tick_payload = {
                    "seq": int(self._tick_seq),
                    "tsMs": int(current_ts_ms),
                    "deltaMs": int(delta_ms),
                }

                if self._hook_on_tick is not None:
                    result, self._hook_on_tick_maybe_awaitable = await self._invoke_async(
                        self._hook_on_tick,
                        self._hook_on_tick_is_async,
                        self._hook_on_tick_maybe_awaitable,
                        "onTick",
                        tick_payload,
                    )
                    await self._emit_outputs(result)

                interval_s = max(0.001, float(self._tick_ms) / 1000.0)
                now_after = time.monotonic()
                if next_deadline <= now_after:
                    next_deadline = now_after + interval_s
                else:
                    next_deadline += interval_s
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log_error_deduped("tick_loop", "tick loop failed", exc)
                await asyncio.sleep(0.05)

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            _, _ = await self._invoke_async(
                self._hook_on_stop,
                self._hook_on_stop_is_async,
                True,
                "onStop",
            )
            self._started = False
            self._paused = False
            tick_task = self._tick_task
            self._tick_task = None
            if tick_task is not None and not tick_task.done():
                tick_task.cancel()
                await asyncio.gather(tick_task, return_exceptions=True)
        finally:
            await self._shutdown_video_subscriptions_async()

    async def on_lifecycle(self, active: bool, meta: dict[str, Any]) -> None:
        self._active = bool(active)
        if not self._started:
            return

        if self._active:
            if self._paused:
                self._paused = False
                result, _ = await self._invoke_async(
                    self._hook_on_resume,
                    self._hook_on_resume_is_async,
                    True,
                    "onResume",
                    dict(meta or {}),
                )
                await self._emit_outputs(result)
            return

        if not self._paused:
            self._paused = True
            result, _ = await self._invoke_async(
                self._hook_on_pause,
                self._hook_on_pause_is_async,
                True,
                "onPause",
                dict(meta or {}),
            )
            await self._emit_outputs(result)

    async def validate_state(self, field: str, value: Any, *, ts_ms: int, meta: dict[str, Any]) -> Any:
        del ts_ms, meta
        name = str(field or "").strip()
        value_unwrapped = unwrap_json_value(value)
        if name == "code":
            return str(value_unwrapped or "")
        if name == "tickEnabled":
            return bool(value_unwrapped)
        if name == "tickMs":
            return self._coerce_tick_ms(value_unwrapped, default=self._tick_ms)
        return value_unwrapped

    async def on_state(self, field: str, value: Any, *, ts_ms: int | None = None) -> None:
        name = str(field or "").strip()
        value_unwrapped = unwrap_json_value(value)

        if name == "code":
            self._code = str(value_unwrapped or "")
            self._compile_and_start()
            return
        if name == "tickEnabled":
            self._tick_enabled = bool(value_unwrapped)
            self._ensure_tick_task()
            return
        if name == "tickMs":
            self._tick_ms = self._coerce_tick_ms(value_unwrapped, default=self._tick_ms)
            return

        if name in self._self_state_writes and self._self_state_writes.get(name) == value_unwrapped:
            return

        if self._hook_on_state is None:
            return
        result, self._hook_on_state_maybe_awaitable = await self._invoke_async(
            self._hook_on_state,
            self._hook_on_state_is_async,
            self._hook_on_state_maybe_awaitable,
            "onState",
            name,
            value_unwrapped,
            ts_ms,
        )
        await self._emit_outputs(result)

    async def on_data(self, port: str, value: Any, *, ts_ms: int | None = None) -> None:
        if not self._active or self._paused:
            return
        if self._hook_on_data is None:
            return
        in_port = str(port or "")
        result, self._hook_on_data_maybe_awaitable = await self._invoke_async(
            self._hook_on_data,
            self._hook_on_data_is_async,
            self._hook_on_data_maybe_awaitable,
            "onData",
            in_port,
            value,
            ts_ms,
        )
        await self._emit_outputs(result)

    async def on_command(self, name: str, args: dict[str, Any] | None = None, *, meta: dict[str, Any] | None = None) -> Any:
        call = str(name or "").strip()
        call_args = dict(args or {})
        call_meta = dict(meta or {})
        if not call:
            raise ValueError("empty command name")

        if call == "grant_local_exec":
            ttl_ms_raw = call_args.get("ttlMs")
            ttl_ms: int | None
            if ttl_ms_raw is None:
                ttl_ms = None
            else:
                try:
                    ttl_ms = max(1, int(ttl_ms_raw))
                except (TypeError, ValueError) as exc:
                    raise ValueError("ttlMs must be an integer") from exc

            self._local_exec_granted = True
            self._grant_ts_ms = self._now_ms()
            self._grant_session_id = str(call_meta.get("reqId") or call_meta.get("sessionId") or self._grant_ts_ms)
            self._grant_expires_ts_ms = (self._grant_ts_ms + ttl_ms) if ttl_ms is not None else None
            return {"ok": True, "result": self._permission_view()}

        if call == "revoke_local_exec":
            self._local_exec_granted = False
            self._grant_expires_ts_ms = None
            return {"ok": True, "result": self._permission_view()}

        if self._hook_on_command is None:
            raise ValueError(f"unknown command: {call}")

        result, _ = await self._invoke_async(
            self._hook_on_command,
            self._hook_on_command_is_async,
            True,
            "onCommand",
            call,
            call_args,
            call_meta,
        )
        return {"ok": True, "result": normalize_script_output_value(result)}

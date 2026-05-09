from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any
import numpy as np

from f8pysdk.specs import (
    F8DataPortSpec,
    F8OperatorSchemaVersion,
    F8OperatorSpec,
    F8RuntimeNode,
    F8SpecEditPolicy,
    F8StateAccess,
    F8StateSpec,
    any_schema,
    editable_collection_edit_policy,
    string_schema,
)
from f8pysdk.bus import ServiceBus
from f8pysdk.capabilities import ClosableNode
from f8pysdk.f8_naming import ensure_token
from f8pysdk.nodes import OperatorNode
from f8pysdk.registry import Registry
from f8pysdk.video_transport import VIDEO_FORMAT_BGRA32, VIDEO_FORMAT_FLOW2_F16
from f8pysdk.video_transport import (
    LatestVideoFrame,
    LatestVideoFrameTransport,
    ZenohLatestVideoFrameTransport,
)

from ..constants import SERVICE_CLASS
from ._ports import exec_out_ports
from .script_utils.input_binding import (
    INPUT_MODE_INPUT_VIEW,
    INPUT_MODE_MSGSPEC_STRUCT,
    INPUT_MODE_RAW_DICT,
    InputBinding,
    coerce_input_mode,
    infer_script_input_style,
)
from .script_utils.python_editor_assist import python_script_field_editor_assist_payload
from .script_utils.result_binding import normalize_script_output_value, normalize_script_output_value_fast
from .script_utils.script_runtime import HookSet, ScriptRuntimeCompiler
from .script_utils.state_binding import PyEngineStatesView

OPERATOR_CLASS = "f8.python_script"
_REPEATING_ERROR_LOG_INTERVAL_MS = 2000
logger = logging.getLogger(__name__)

@dataclass
class _LatestVideoSubscription:
    key: str
    stream_key: str
    decode_mode: str
    reader: LatestVideoFrameTransport | None = None
    task: asyncio.Task[object] | None = None
    latest_packet: dict[str, Any] | None = None
    last_frame_id: int = 0
    last_error_sig: str | None = None
    last_error_ts_ms: int = 0
    error_count: int = 0


def _video_subscription_source_metadata(sub: _LatestVideoSubscription) -> dict[str, Any]:
    return {"key": sub.key, "transport": "zenoh", "streamKey": sub.stream_key}


def _video_subscription_status_metadata(sub: _LatestVideoSubscription) -> dict[str, Any]:
    metadata = _video_subscription_source_metadata(sub)
    metadata["decodeMode"] = sub.decode_mode
    metadata["hasPacket"] = sub.latest_packet is not None
    metadata["lastFrameId"] = int(sub.last_frame_id)
    metadata["errorCount"] = int(sub.error_count)
    return metadata


@dataclass(slots=True)
class PyEngineContext:
    _node: "PythonScriptRuntimeNode"
    node_id: str
    locals: dict[str, Any]
    _state_keys: tuple[str, ...]
    exec_in: str | None = None

    def with_exec_in(self, exec_in: str | None) -> "PyEngineContext":
        if self.exec_in == exec_in:
            return self
        return PyEngineContext(
            _node=self._node,
            node_id=self.node_id,
            locals=self.locals,
            _state_keys=self._state_keys,
            exec_in=exec_in,
        )

    @property
    def states(self) -> PyEngineStatesView:
        return self._node._build_states_view(self._state_keys)

    @property
    def input_mode(self) -> str:
        return str(self._node._input_binding.mode)

    def log(self, message: object) -> None:
        self._node._log(str(message))

    async def emit_async(self, port: str, value: Any) -> None:
        await self._node.emit(str(port), value)

    def emit(self, port: str, value: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            logger.error("[%s:python_script] emit without running loop", self.node_id, exc_info=exc)
            return
        loop.create_task(self.emit_async(str(port), value), name=f"python_script:emit:{self.node_id}:{port}")

    async def set_state_async(self, field: str, value: Any) -> None:
        self._node._self_state_writes[str(field)] = value
        await self._node.set_state(str(field), value)

    def set_state(self, field: str, value: Any) -> None:
        self._node._self_state_writes[str(field)] = value
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            logger.error("[%s:python_script] set_state without running loop", self.node_id, exc_info=exc)
            return
        loop.create_task(
            self.set_state_async(str(field), value),
            name=f"python_script:set_state:{self.node_id}:{field}",
        )

    async def read_state(self, field: str) -> Any:
        return await self._node.get_state_value(str(field))

    def subscribe_video_latest(
        self,
        key: str,
        *,
        stream_key: str = "",
        decode: str = "auto",
    ) -> None:
        key_name = str(key or "").strip()
        stream_key_text = str(stream_key or "").strip()
        if not key_name:
            return
        if not stream_key_text:
            return
        decode_mode = self._node._normalize_decode_mode(decode)
        self._node._unsubscribe_video_latest_sync(key_name)
        sub = _LatestVideoSubscription(
            key=key_name,
            stream_key=stream_key_text,
            decode_mode=decode_mode,
        )
        self._node._video_subscriptions[key_name] = sub
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            sub.task = None
            logger.error(
                "[%s:python_script] subscribe_video_latest without running loop",
                self.node_id,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            return
        sub.task = loop.create_task(
            self._node._run_video_latest_subscription(key_name),
            name=f"python_script:video_sub:{self.node_id}:{key_name}",
        )

    def get_video_latest(self, key: str) -> dict[str, Any] | None:
        key_name = str(key or "").strip()
        if not key_name:
            return None
        sub = self._node._video_subscriptions.get(key_name)
        if sub is None:
            return None
        return self._node._copy_packet_for_script(sub.latest_packet)

    def unsubscribe_video_latest(self, key: str) -> None:
        self._node._unsubscribe_video_latest_sync(str(key or "").strip())

    def list_video_latest_subscriptions(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for key_name in sorted(self._node._video_subscriptions.keys()):
            sub = self._node._video_subscriptions.get(key_name)
            if sub is None:
                continue
            items.append(_video_subscription_status_metadata(sub))
        return items


DEFAULT_CODE = (
    "# Hooks template (uncomment what you need):\n"
    "# - onStart(ctx)\n"
    "# - onState(ctx, field, value, ts_ms=None)\n"
    "# - onMsg(ctx, inputs)\n"
    "# - onExec(ctx, exec_in, inputs)\n"
    "# - onStop(ctx)\n"
    "#\n"
    "# Notes:\n"
    "# - If you define no hooks, the node is a no-op.\n"
    "# - ctx.locals is preserved between calls (script-local memory)\n"
    "# - ctx.exec_in is set only for exec-triggered calls\n"
    "# - ctx.states.<field> reads cached rw/ro/wo state snapshot\n"
    "#   - example: ctx.states.foo / ctx.states.pose.x\n"
    "# - await ctx.read_state(field)  # fresh runtime read\n"
    "# - ctx.states.get(field)  # cached snapshot\n"
    "# - ctx.set_state(field, value)\n"
    "#   - await ctx.set_state_async(field, value)\n"
    "# - onStart return values are ignored; use ctx.emit()/ctx.set_state().\n"
    "# - inputs binding mode is configured by state `inputMode`:\n"
    "#   - input_view (default): supports dot and mapping access\n"
    "#   - raw_dict: plain dict only (faster for mapping-style high-frequency scripts)\n"
    "#   - msgspec_struct: typed struct from dataIn schema (faster for dot-style high-frequency scripts)\n"
    "# - State TypeGuard helpers are available from f8_dynamic_states\n"
    "#   - example: from f8_dynamic_states import is_state_inputMode\n"
    "#   - then: if is_state_inputMode(value, field): ...\n"
    "# - Video latest-frame helpers:\n"
    "#   - ctx.subscribe_video_latest(key, stream_key='f8/svc/.../data/video', decode='auto')\n"
    "#   - pkt = ctx.get_video_latest(key)\n"
    "#   - ctx.unsubscribe_video_latest(key)\n"
    "#   - ctx.list_video_latest_subscriptions()\n"
    "#\n"
    "# Return value protocol:\n"
    "# - onMsg: {'outputs': {...}} or any value (emits to 'out' if present)\n"
    "# - onExec: {'exec': ['exec', ...], 'outputs': {...}}\n"
    "\n"
    "from typing import TYPE_CHECKING, Any\n"
    "if TYPE_CHECKING:\n"
    "    from f8_script_api import F8Inputs, F8PyEngineContext, F8States\n\n"
    "def onStart(ctx: 'F8PyEngineContext') -> None:\n"
    "    ctx.log('python_script started')\n\n"
    "# def onState(\n"
    "#     ctx: 'F8PyEngineContext',\n"
    "#     field: str,\n"
    "#     value: Any,\n"
    "#     ts_ms: int | None = None,\n"
    "# ) -> None:\n"
    "#     ctx.log(f'state {field}={value} ts_ms={ts_ms}')\n"
    "#\n"
    "# def onMsg(ctx: 'F8PyEngineContext', inputs: 'F8Inputs') -> dict[str, Any]:\n"
    "#     msg = inputs.msg\n"
    "#     return {'outputs': {'out': msg}}\n"
    "#\n"
    "# def onExec(ctx: 'F8PyEngineContext', exec_in: str, inputs: 'F8Inputs') -> dict[str, Any]:\n"
    "#     if exec_in == 'exec2':\n"
    "#         return {'exec': ['exec2'], 'outputs': {'out': inputs.msg}}\n"
    "#     return {'exec': ['exec'], 'outputs': {'out': inputs.msg}}\n"
    "#\n"
    "# def onStop(ctx: 'F8PyEngineContext') -> None:\n"
    "#     ctx.log('python_script stopped')\n"
)


class PythonScriptRuntimeNode(OperatorNode, ClosableNode):
    """
    Execute user-provided python code with lifecycle hooks:

    - onStart(ctx): optional; invoked on construction (best-effort) and after recompiles
    - onState(ctx, field, value, ts_ms=None): optional; invoked on state updates (except 'code')
    - onMsg(ctx, inputs): optional; invoked on data arrival and as exec fallback when onExec is missing
    - onExec(ctx, exec_in, inputs): optional; invoked on exec triggers with a full pulled input snapshot
    - onStop(ctx): optional; invoked on close() (best-effort) and before recompiles
    """

    def __init__(self, *, node_id: str, node: F8RuntimeNode, initial_state: dict[str, Any] | None = None) -> None:
        super().__init__(
            node_id=ensure_token(node_id, label="node_id"),
            data_in_ports=[p.name for p in (node.dataInPorts or [])],
            data_out_ports=[p.name for p in (node.dataOutPorts or [])],
            state_fields=[s.name for s in (node.stateFields or [])],
        )
        self._readable_state_names = self._collect_readable_state_names(node)
        self._initial_state = dict(initial_state or {})
        self._exec_out_ports = exec_out_ports(node, default=["exec"])
        self._locals: dict[str, Any] = {}
        self._ctx: PyEngineContext = self._build_ctx()

        self._code = str(self._initial_state.get("code") or DEFAULT_CODE)
        self._script_runtime = ScriptRuntimeCompiler(set_error=self._set_error)
        self._hooks = HookSet(
            runtime={},
            on_msg=None,
            on_exec=None,
            on_state=None,
            on_msg_is_async=False,
            on_exec_is_async=False,
            on_state_is_async=False,
            on_msg_maybe_awaitable=False,
            on_exec_maybe_awaitable=False,
            on_state_maybe_awaitable=False,
        )
        self._started = False
        self._closing = False
        self._last_error: str | None = None
        self._error_seq = 0
        self._last_logged_error_fingerprint = ""
        self._last_logged_error_ts_ms = 0
        self._pending_monitor_error_message = ""
        self._pending_monitor_error_fingerprint = ""
        self._pull_error_once: set[str] = set()
        self._self_state_writes: dict[str, Any] = {}
        self._pull_cache_ctx_id: str | int | None = None
        self._pull_cache_outputs: dict[str, Any] = {}
        self._state_key_hint_logged = False
        self._video_subscriptions: dict[str, _LatestVideoSubscription] = {}
        self._zenoh_config_path: str | None = None
        self._zenoh_connect: tuple[str, ...] = ()
        self._zenoh_listen: tuple[str, ...] = ()
        self._zenoh_shm_pool_bytes = 256 * 1024 * 1024
        self._data_out_port_set: set[str] = set()
        self._data_in_port_names: tuple[str, ...] = tuple(str(name) for name in self.data_in_ports)
        self._single_data_out_port: str | None = None
        self._has_out_port = False
        self._input_mode = coerce_input_mode(self._initial_state.get("inputMode"), default=INPUT_MODE_INPUT_VIEW)
        self._input_binding = InputBinding(
            node_id=self.node_id,
            data_in_ports=list(node.dataInPorts or []),
            mode=self._input_mode,
        )
        self._input_decode_mode_logged: str | None = None
        self._metric_input_decode_time_us: float = 0.0
        self._metric_input_decode_errors = 0
        self._metric_hook_exec_time_us: float = 0.0
        self._metric_output_normalize_time_us: float = 0.0
        self._metrics_enabled = logger.isEnabledFor(logging.DEBUG)
        self._metrics_sample_mask = 0x7F
        self._metrics_sample_counter = 0
        self._refresh_data_out_port_cache()

        self._compile_and_start()

    def attach(self, bus: Any) -> None:
        super().attach(bus)
        if isinstance(bus, ServiceBus):
            cfg = bus.config
            self._zenoh_config_path = cfg.zenoh_config_path
            self._zenoh_connect = cfg.zenoh_connect
            self._zenoh_listen = cfg.zenoh_listen
            self._zenoh_shm_pool_bytes = cfg.zenoh_shm_pool_bytes
        self._flush_pending_monitor_error()

    def __del__(self) -> None:
        # Best-effort fallback: close() is awaited by ServiceBus when nodes are unregistered,
        # but __del__ provides an additional safety net for ad-hoc use.
        try:
            if self._started and not self._closing:
                self._invoke_hook_sync("onStop")
        except Exception as exc:
            logger.error("[%s:python_script] __del__ onStop failed", self.node_id, exc_info=exc)
        try:
            self._shutdown_video_subscriptions_sync()
        except Exception as exc:
            logger.error("[%s:python_script] __del__ video cleanup failed", self.node_id, exc_info=exc)

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            await self._invoke_hook_async("onStop")
        finally:
            await self._shutdown_video_subscriptions_async()
            self._started = False

    def _log(self, message: str) -> None:
        logger.info("[%s:python_script] %s", self.node_id, message)

    def get_performance_counters(self) -> dict[str, float | int]:
        return {
            "input_decode_time_us": float(self._metric_input_decode_time_us),
            "input_decode_errors": int(self._metric_input_decode_errors),
            "hook_exec_time_us": float(self._metric_hook_exec_time_us),
            "output_normalize_time_us": float(self._metric_output_normalize_time_us),
        }

    def _metrics_start(self) -> float | None:
        if not self._metrics_enabled:
            return None
        self._metrics_sample_counter = (int(self._metrics_sample_counter) + 1) & int(self._metrics_sample_mask)
        if self._metrics_sample_counter != 0:
            return None
        return time.perf_counter()

    def _metrics_add_hook_time(self, started_at: float | None) -> None:
        if started_at is None:
            return
        self._metric_hook_exec_time_us += (time.perf_counter() - started_at) * 1_000_000.0

    def _metrics_add_decode_time(self, started_at: float | None) -> None:
        if started_at is None:
            return
        self._metric_input_decode_time_us += (time.perf_counter() - started_at) * 1_000_000.0

    def _metrics_add_output_norm_time(self, started_at: float | None) -> None:
        if started_at is None:
            return
        self._metric_output_normalize_time_us += (time.perf_counter() - started_at) * 1_000_000.0

    @staticmethod
    def _error_fingerprint(stage: str, exc: BaseException) -> str:
        return f"python-script:{stage}:{type(exc).__name__}:{exc}"

    def _should_log_repeating_error(self, fingerprint: str, *, now_ms: int) -> bool:
        if fingerprint != self._last_logged_error_fingerprint:
            self._last_logged_error_fingerprint = fingerprint
            self._last_logged_error_ts_ms = int(now_ms)
            return True
        elapsed_ms = int(now_ms) - int(self._last_logged_error_ts_ms)
        if elapsed_ms < _REPEATING_ERROR_LOG_INTERVAL_MS:
            return False
        self._last_logged_error_ts_ms = int(now_ms)
        return True

    def _publish_monitor_error(self, *, message: str, fingerprint: str) -> None:
        bus = self._bus
        if bus is None:
            self._pending_monitor_error_message = str(message)
            self._pending_monitor_error_fingerprint = str(fingerprint)
            return
        try:
            bus.report_error(
                self.node_id,
                "PYTHON_SCRIPT_ERROR",
                str(message),
                severity="error",
                fingerprint=str(fingerprint),
            )
        except Exception as report_exc:
            logger.error("[%s:python_script] report monitor error failed", self.node_id, exc_info=report_exc)

    def _flush_pending_monitor_error(self) -> None:
        message = str(self._pending_monitor_error_message)
        fingerprint = str(self._pending_monitor_error_fingerprint)
        if not message or not fingerprint:
            return
        self._publish_monitor_error(message=message, fingerprint=fingerprint)
        if self._bus is not None:
            self._pending_monitor_error_message = ""
            self._pending_monitor_error_fingerprint = ""

    def _set_error(self, stage: str, exc: BaseException) -> None:
        self._error_seq = int(self._error_seq) + 1
        msg = f"{stage}: {exc}"
        self._last_error = msg
        fingerprint = self._error_fingerprint(stage, exc)
        if self._should_log_repeating_error(fingerprint, now_ms=self._now_ms()):
            logger.error("[%s:python_script] error %s", self.node_id, msg, exc_info=exc)
        self._publish_monitor_error(message=msg, fingerprint=fingerprint)

    def _clear_last_error(self) -> None:
        if not self._last_error:
            return
        self._last_error = None
        self._pending_monitor_error_message = ""
        self._pending_monitor_error_fingerprint = ""
        bus = self._bus
        if bus is None:
            return
        try:
            bus.clear_error(self.node_id)
        except Exception as exc:
            logger.error("[%s:python_script] clear monitor error failed", self.node_id, exc_info=exc)

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000.0)

    @staticmethod
    def _collect_readable_state_names(node: F8RuntimeNode) -> tuple[str, ...]:
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
    def _normalize_decode_mode(decode: Any) -> str:
        mode = str(decode or "auto").strip().lower()
        if mode in ("none", "auto"):
            return mode
        return "auto"

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
                except Exception:
                    data = None
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
                except Exception:
                    data = None
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
            "raw": packet.get("raw"),
            "meta": dict(meta_src) if isinstance(meta_src, dict) else {},
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

    def _log_video_sub_error(self, sub: _LatestVideoSubscription, stage: str, exc: BaseException) -> None:
        sub.error_count += 1
        now_ms = self._now_ms()
        sig = f"{stage}:{type(exc).__name__}:{exc}"
        if sub.last_error_sig == sig and (now_ms - int(sub.last_error_ts_ms)) < 2000:
            return
        sub.last_error_sig = sig
        sub.last_error_ts_ms = now_ms
        logger.error(
            "[%s:python_script] video latest subscribe failed key=%s stream_key=%s stage=%s",
            self.node_id,
            sub.key,
            sub.stream_key,
            stage,
            exc_info=(type(exc), exc, exc.__traceback__),
        )

    def _close_video_sub_reader(self, sub: _LatestVideoSubscription) -> None:
        reader = sub.reader
        sub.reader = None
        if reader is None:
            return
        try:
            reader.close()
        except Exception as exc:
            logger.error(
                "[%s:python_script] video reader close failed key=%s",
                self.node_id,
                sub.key,
                exc_info=exc,
            )

    def _open_video_sub_reader(self, sub: _LatestVideoSubscription) -> LatestVideoFrameTransport:
        return ZenohLatestVideoFrameTransport.open_subscriber(
            sub.stream_key,
            config_path=self._zenoh_config_path,
            connect=self._zenoh_connect,
            listen=self._zenoh_listen,
            shm_pool_bytes=self._zenoh_shm_pool_bytes,
        )

    def _unsubscribe_video_latest_sync(self, key: str) -> bool:
        key_name = str(key or "").strip()
        if not key_name:
            return False
        sub = self._video_subscriptions.pop(key_name, None)
        if sub is None:
            return False
        task = sub.task
        sub.task = None
        if task is not None and not task.done():
            task.cancel()
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

    async def _run_video_latest_subscription(self, key: str) -> None:
        key_name = str(key or "").strip()
        while True:
            sub = self._video_subscriptions.get(key_name)
            if sub is None:
                return

            if sub.reader is None:
                try:
                    sub.reader = self._open_video_sub_reader(sub)
                except Exception as exc:
                    self._log_video_sub_error(sub, "open", exc)
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

                    width = int(frame.width)
                    height = int(frame.height)
                    pitch = int(frame.pitch)
                    frame_bytes = int(frame.frame_bytes)
                    if width <= 0 or height <= 0 or pitch <= 0 or frame_bytes <= 0:
                        await asyncio.sleep(0)
                        continue
                    if frame_bytes > len(frame.payload):
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
                self._log_video_sub_error(sub, "read", exc)
                self._close_video_sub_reader(sub)
                await asyncio.sleep(0.2)

    def _build_ctx(self) -> PyEngineContext:
        return PyEngineContext(
            _node=self,
            node_id=self.node_id,
            locals=self._locals,
            _state_keys=self._readable_state_names,
            exec_in=None,
        )

    def _refresh_data_out_port_cache(self) -> None:
        self._data_out_port_set = {str(name) for name in self.data_out_ports}
        if len(self._data_out_port_set) == 1:
            self._single_data_out_port = next(iter(self._data_out_port_set))
        else:
            self._single_data_out_port = None
        self._has_out_port = "out" in self._data_out_port_set

    async def _pull_inputs_for_context(self, ctx_id: str | int | None) -> dict[str, Any]:
        inputs: dict[str, Any] = {}
        for in_port in self._data_in_port_names:
            try:
                inputs[in_port] = await self.pull(in_port, ctx_id=ctx_id)
            except Exception as exc:
                error_key = f"{in_port}:{type(exc).__name__}:{exc}"
                if error_key not in self._pull_error_once:
                    self._pull_error_once.add(error_key)
                    self._set_error(f"pull:{in_port}", exc)
        return inputs

    async def _await_msg_result(self, result: Any) -> Any:
        if self._hooks.on_msg_is_async:
            return await result
        if not self._hooks.on_msg_maybe_awaitable:
            return result
        if inspect.isawaitable(result):
            return await result
        self._hooks.on_msg_maybe_awaitable = False
        return result

    async def _await_exec_result(self, result: Any) -> Any:
        if self._hooks.on_exec_is_async:
            return await result
        if not self._hooks.on_exec_maybe_awaitable:
            return result
        if inspect.isawaitable(result):
            return await result
        self._hooks.on_exec_maybe_awaitable = False
        return result

    async def _await_state_result(self, result: Any) -> Any:
        if self._hooks.on_state_is_async:
            return await result
        if not self._hooks.on_state_maybe_awaitable:
            return result
        if inspect.isawaitable(result):
            return await result
        self._hooks.on_state_maybe_awaitable = False
        return result

    def _build_states_view(self, state_keys: tuple[str, ...]) -> PyEngineStatesView:
        resolved_keys = [str(key) for key in state_keys if str(key)]
        if not resolved_keys:
            resolved_keys = [str(key) for key in self.state_fields if str(key)]
        if not resolved_keys:
            resolved_keys = [str(key) for key in self._readable_state_names if str(key)]
        unique_keys = tuple(sorted({key for key in resolved_keys if key}))
        snapshot: dict[str, Any] = {}
        for key in unique_keys:
            snapshot[str(key)] = self.get_state_cached(str(key), None)
        return PyEngineStatesView(snapshot)

    def _compile_and_start(self) -> None:
        error_seq_start = int(self._error_seq)
        if self._started:
            self._invoke_hook_sync("onStop")
        self._shutdown_video_subscriptions_sync()
        self._locals = {}
        self._ctx = self._build_ctx()
        # Normalize line endings and tabs to avoid TabError on mixed indentation.
        code = str(self._code or "")
        code = code.replace("\r\n", "\n").replace("\r", "\n")
        code = code.expandtabs(4)
        self._input_decode_mode_logged = None
        self._hooks = self._script_runtime.compile(code)
        if self._input_binding.warnings:
            self._set_error("inputModel", ValueError("; ".join(self._input_binding.warnings)))

        style = infer_script_input_style(code)
        mode = self._input_binding.mode
        if style == "dot" and mode == "raw_dict":
            logger.warning(
                "[%s:python_script] script appears to use dot inputs, but inputMode=%s",
                self.node_id,
                mode,
            )
        elif style == "mapping" and mode == "msgspec_struct":
            logger.warning(
                "[%s:python_script] script appears to use mapping inputs, but inputMode=%s",
                self.node_id,
                mode,
            )

        if not self._hooks.runtime:
            self._started = False
            if int(self._error_seq) == error_seq_start:
                self._set_error(
                    "hooks",
                    ValueError(
                        "no hooks defined; add at least one of: onStart(ctx), onState(ctx, ...), "
                        "onMsg(ctx, inputs), onExec(ctx, exec_in, inputs), onStop(ctx)"
                    ),
                )
            return
        self._invoke_hook_sync("onStart")
        if int(self._error_seq) == error_seq_start:
            self._clear_last_error()

    def _build_invoke_ctx(self, *, exec_in: str | None) -> PyEngineContext:
        """
        Build a per-invocation context.

        `self._ctx` holds shared utilities/state references, while `exec_in`
        must be invocation-scoped to avoid cross-call leakage under concurrency.
        """
        if exec_in is None:
            return self._ctx
        return self._ctx.with_exec_in(exec_in)

    def _invoke_hook_sync(self, name: str, *args: Any) -> None:
        fn = self._hooks.runtime.get(name)
        if not callable(fn):
            if name == "onStart":
                self._started = True
            elif name == "onStop":
                self._started = False
            return
        try:
            t0 = self._metrics_start()
            r = fn(self._ctx, *args)
            if inspect.isawaitable(r):
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(r, name=f"python_script:{name}:{self.node_id}")
                except Exception:
                    pass
            self._metrics_add_hook_time(t0)
        except Exception as exc:
            self._set_error(name, exc)
        finally:
            if name == "onStart":
                self._started = True
            elif name == "onStop":
                self._started = False

    async def _invoke_hook_async(self, name: str, *args: Any) -> None:
        fn = self._hooks.runtime.get(name)
        if not callable(fn):
            if name == "onStart":
                self._started = True
            elif name == "onStop":
                self._started = False
            return
        try:
            t0 = self._metrics_start()
            r = fn(self._ctx, *args)
            if inspect.isawaitable(r):
                await r
            self._metrics_add_hook_time(t0)
        except Exception as exc:
            self._set_error(name, exc)
        finally:
            if name == "onStart":
                self._started = True
            elif name == "onStop":
                self._started = False

    async def on_state(self, field: str, value: Any, *, ts_ms: int | None = None) -> None:
        name = str(field)
        if name == "code":
            next_code = str(value or "")
            if next_code == self._code:
                return
            self._code = next_code
            self._compile_and_start()
            return
        if name == "inputMode":
            self._input_mode = coerce_input_mode(value, default=INPUT_MODE_INPUT_VIEW)
            self._input_binding.set_mode(self._input_mode)
            self._input_decode_mode_logged = None

        # Best-effort loop prevention for state writes originating from this node (via ctx.set_state()).
        if name in self._self_state_writes and self._self_state_writes.get(name) == value:
            return

        fn = self._hooks.on_state
        if not callable(fn):
            return
        try:
            r = fn(self._ctx, name, value, ts_ms)
            t0 = self._metrics_start()
            r = await self._await_state_result(r)
            self._metrics_add_hook_time(t0)
        except Exception as exc:
            self._set_error("onState", exc)

    async def validate_state(self, field: str, value: Any, *, ts_ms: int, meta: dict[str, Any]) -> Any:
        del ts_ms, meta
        name = str(field or "").strip()
        if name == "code":
            return str(value or "")
        if name == "inputMode":
            mode = coerce_input_mode(value, default=INPUT_MODE_INPUT_VIEW)
            if str(value or "").strip().lower().replace("-", "_") not in ("raw_dict", "input_view", "msgspec_struct"):
                raise ValueError(f"invalid inputMode: {value}")
            return mode
        return value

    async def on_data(self, port: str, value: Any, *, ts_ms: int | None = None) -> None:
        # Push-mode: treat incoming data as a message.
        if not self._hooks.runtime:
            return
        await self._run_on_msg({str(port): value}, exec_in=None)

    async def on_exec(self, exec_id: str | int, in_port: str | None = None) -> list[str]:
        # Exec-driven: pull current values for all inputs.
        if not self._hooks.runtime:
            return list(self._exec_out_ports)
        inputs = await self._pull_inputs_for_context(exec_id)
        exec_in = str(in_port or "").strip() or None
        return await self._run_on_exec(inputs, exec_in=exec_in)

    async def compute_output(self, port: str, ctx_id: str | int | None = None) -> Any:
        out_port = str(port or "")
        if out_port not in self._data_out_port_set:
            return None
        if not self._hooks.runtime:
            return None

        if ctx_id is not None and ctx_id == self._pull_cache_ctx_id:
            if out_port in self._pull_cache_outputs:
                return self._pull_cache_outputs.get(out_port)

        inputs = await self._pull_inputs_for_context(ctx_id)

        outputs = await self._compute_outputs_for_pull(inputs, exec_in=None)
        if ctx_id is not None:
            self._pull_cache_ctx_id = ctx_id
            self._pull_cache_outputs = dict(outputs)
        else:
            self._pull_cache_ctx_id = None
            self._pull_cache_outputs = {}
        return outputs.get(out_port)

    async def _run_on_exec(self, inputs: dict[str, Any], *, exec_in: str | None) -> list[str]:
        fn = self._hooks.on_exec
        if callable(fn):
            inputs_obj = self._decode_inputs(inputs, stage="onExec")
            if inputs_obj is None:
                return list(self._exec_out_ports)
            try:
                invoke_ctx = self._build_invoke_ctx(exec_in=exec_in)
                r = fn(invoke_ctx, str(exec_in or ""), inputs_obj)
                t0 = self._metrics_start()
                r = await self._await_exec_result(r)
                self._metrics_add_hook_time(t0)
            except Exception as exc:
                self._set_error("onExec", exc)
                return list(self._exec_out_ports)
            out_ports = await self._apply_result(r)
            return out_ports if out_ports is not None else list(self._exec_out_ports)

        await self._run_on_msg(inputs, exec_in=exec_in)
        return list(self._exec_out_ports)

    async def _compute_outputs_for_pull(self, inputs: dict[str, Any], *, exec_in: str | None) -> dict[str, Any]:
        fn_msg = self._hooks.on_msg
        fn_exec = self._hooks.on_exec
        inputs_obj = self._decode_inputs(inputs, stage="compute")
        if inputs_obj is None:
            return {}
        if self._hooks.on_msg_only_mode and callable(fn_msg):
            try:
                invoke_ctx = self._build_invoke_ctx(exec_in=exec_in)
                result = fn_msg(invoke_ctx, inputs_obj)
                t0 = self._metrics_start()
                result = await self._await_msg_result(result)
                self._metrics_add_hook_time(t0)
            except Exception as exc:
                self._set_error("onMsg", exc)
                return {}
            try:
                return self._extract_outputs(result)
            except ValueError as exc:
                self._set_error("onMsg", exc)
                return {}

        if callable(fn_exec):
            try:
                invoke_ctx = self._build_invoke_ctx(exec_in=exec_in)
                result = fn_exec(invoke_ctx, str(exec_in or ""), inputs_obj)
                t0 = self._metrics_start()
                result = await self._await_exec_result(result)
                self._metrics_add_hook_time(t0)
            except Exception as exc:
                self._set_error("onExec", exc)
                return {}
            try:
                return self._extract_outputs(result)
            except ValueError as exc:
                self._set_error("onExec", exc)
                return {}

        if not callable(fn_msg):
            return {}
        try:
            invoke_ctx = self._build_invoke_ctx(exec_in=exec_in)
            result = fn_msg(invoke_ctx, inputs_obj)
            t0 = self._metrics_start()
            result = await self._await_msg_result(result)
            self._metrics_add_hook_time(t0)
        except Exception as exc:
            self._set_error("onMsg", exc)
            return {}
        try:
            return self._extract_outputs(result)
        except ValueError as exc:
            self._set_error("onMsg", exc)
            return {}

    def _extract_outputs(self, result: Any) -> dict[str, Any]:
        if result is None:
            return {}

        data_out_ports = self._data_out_port_set
        outputs: dict[str, Any] = {}
        if isinstance(result, dict):
            raw_outputs = result.get("outputs")
            if isinstance(raw_outputs, dict):
                single_out_port = self._single_data_out_port
                if single_out_port is not None and len(raw_outputs) == 1:
                    value = raw_outputs.get(single_out_port, None)
                    if single_out_port in raw_outputs:
                        t0 = self._metrics_start()
                        normalized = normalize_script_output_value_fast(value)
                        self._metrics_add_output_norm_time(t0)
                        return {single_out_port: normalized}
                # Fast-path for dominant script pattern: {"outputs": {"tcode": value}}.
                if len(raw_outputs) == 1:
                    for raw_key, raw_value in raw_outputs.items():
                        if isinstance(raw_key, str):
                            if raw_key in data_out_ports:
                                t0 = self._metrics_start()
                                normalized = normalize_script_output_value_fast(raw_value)
                                self._metrics_add_output_norm_time(t0)
                                return {raw_key: normalized}
                            return {}
                        key_s = str(raw_key)
                        if key_s in data_out_ports:
                            t0 = self._metrics_start()
                            normalized = normalize_script_output_value_fast(raw_value)
                            self._metrics_add_output_norm_time(t0)
                            return {key_s: normalized}
                        return {}
                for k, v in raw_outputs.items():
                    k_s = str(k)
                    if k_s in data_out_ports:
                        t0 = self._metrics_start()
                        outputs[k_s] = normalize_script_output_value_fast(v)
                        self._metrics_add_output_norm_time(t0)
                return outputs

            if "outputs" in result:
                raise ValueError("script return field 'outputs' must be a dict")
            raise ValueError("script dict return must include an 'outputs' dict")

        if "out" in data_out_ports:
            t0 = self._metrics_start()
            outputs["out"] = normalize_script_output_value_fast(result)
            self._metrics_add_output_norm_time(t0)
        return outputs

    async def _run_on_msg(self, inputs: dict[str, Any], *, exec_in: str | None) -> None:
        fn = self._hooks.on_msg
        if not callable(fn):
            return
        inputs_obj = self._decode_inputs(inputs, stage="onMsg")
        if inputs_obj is None:
            return
        try:
            invoke_ctx = self._build_invoke_ctx(exec_in=exec_in)
            r = fn(invoke_ctx, inputs_obj)
            t0 = self._metrics_start()
            r = await self._await_msg_result(r)
            self._metrics_add_hook_time(t0)
        except Exception as exc:
            self._set_error("onMsg", exc)
            return
        await self._apply_result(r)

    async def _apply_result(self, r: Any) -> list[str] | None:
        """
        Apply a script return value:
        - dict:
          - exec routing: r.get("exec") -> str | list[str]
          - outputs: r.get("outputs") -> dict[dataOutPort,value]
        - non-dict: emit to 'out' if present
        Returns selected exec out ports if provided, else None.
        """
        if r is None:
            return None

        if isinstance(r, dict):
            exec_sel = r.get("exec") if "exec" in r else None
            try:
                outputs = self._extract_outputs(r)
            except ValueError as exc:
                self._set_error("result", exc)
                return None
            try:
                for k, v in outputs.items():
                    await self.emit(str(k), v)
            except Exception:
                return None

            if exec_sel is None:
                return None
            if isinstance(exec_sel, str):
                return [exec_sel]
            if isinstance(exec_sel, (list, tuple)):
                return [str(x) for x in exec_sel if str(x)]
            return None

        # Non-dict: send to default data output if present.
        if self._has_out_port:
            try:
                t0 = self._metrics_start()
                out_value = normalize_script_output_value(r)
                self._metrics_add_output_norm_time(t0)
                await self.emit("out", out_value)
            except Exception:
                pass
        return None

    def _decode_inputs(self, inputs: dict[str, Any], *, stage: str) -> Any | None:
        decode_mode = self._input_binding.mode
        if self._input_decode_mode_logged != decode_mode:
            logger.debug("[%s:python_script] input decode mode=%s", self.node_id, decode_mode)
            self._input_decode_mode_logged = decode_mode
        try:
            t0 = self._metrics_start()
            decoded = self._input_binding.decode(inputs)
            self._metrics_add_decode_time(t0)
            return decoded
        except Exception as exc:
            self._metric_input_decode_errors += 1
            logger.debug(
                "[%s:python_script] input decode failed mode=%s sample_types=%s",
                self.node_id,
                decode_mode,
                {str(k): type(v).__name__ for k, v in list(inputs.items())[:3]},
            )
            self._set_error(f"{stage}:inputs", exc)
            return None


PythonScriptRuntimeNode.SPEC = F8OperatorSpec(
    schemaVersion=F8OperatorSchemaVersion.f8operator_1,
    serviceClass=SERVICE_CLASS,
    paletteCategory=f"{SERVICE_CLASS}.expr",
    operatorClass=OPERATOR_CLASS,
    version="0.0.1",
    label="Python Script",
    description="Execute Python code with onStart/onState/onMsg/onExec/onStop hooks.",
    tags=["script", "python", "programmable"],
    execInPorts=["exec"],
    execOutPorts=["exec"],
    dataInPorts=[F8DataPortSpec(name="msg", description="Message input", valueSchema=any_schema(), required=False)],
    dataOutPorts=[F8DataPortSpec(name="out", description="Script output", valueSchema=any_schema(), required=False)],
    editPolicy=F8SpecEditPolicy(
        stateFields=editable_collection_edit_policy(),
        commands=editable_collection_edit_policy(),
        dataInPorts=editable_collection_edit_policy(),
        dataOutPorts=editable_collection_edit_policy(),
        execInPorts=editable_collection_edit_policy(),
        execOutPorts=editable_collection_edit_policy(),
    ),
    stateFields=[
        F8StateSpec(
            name="code",
            label="Code",
            description="Python source code optionally defining hooks: onStart/onState/onMsg/onExec/onStop.",
            uiControl="code[python]",
            valueSchema=string_schema(default=DEFAULT_CODE),
            access=F8StateAccess.rw,
            required=True,
            showOnNode=False,
            editorAssist=python_script_field_editor_assist_payload(),
        ),
        F8StateSpec(
            name="inputMode",
            label="Input Mode",
            description=(
                "Input binding mode: input_view | raw_dict | msgspec_struct. "
                "For high-frequency scripts, prefer raw_dict for mapping access or msgspec_struct for dot access."
            ),
            valueSchema=string_schema(
                default=INPUT_MODE_INPUT_VIEW,
                enum=[INPUT_MODE_INPUT_VIEW, INPUT_MODE_RAW_DICT, INPUT_MODE_MSGSPEC_STRUCT],
            ),
            access=F8StateAccess.rw,
            required=True,
            showOnNode=False,
        ),
    ],
)


def register_operator(registry: Registry) -> Registry:
    registry.register_operator(PythonScriptRuntimeNode.SPEC, PythonScriptRuntimeNode, overwrite=True)
    return registry

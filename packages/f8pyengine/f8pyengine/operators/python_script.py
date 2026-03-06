from __future__ import annotations

import asyncio
import ast
import inspect
import keyword
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from f8pysdk import (
    F8DataPortSpec,
    F8OperatorSchemaVersion,
    F8OperatorSpec,
    F8RuntimeNode,
    F8StateAccess,
    F8StateSpec,
    any_schema,
    string_schema,
)
from f8pysdk.capabilities import ClosableNode
from f8pysdk.nats_naming import ensure_token
from f8pysdk.runtime_node import OperatorNode
from f8pysdk.runtime_node_registry import RuntimeNodeRegistry
from f8pysdk.shm.video import VIDEO_FORMAT_BGRA32, VIDEO_FORMAT_FLOW2_F16, VideoShmHeader, VideoShmReader

from ..constants import SERVICE_CLASS
from ._ports import exec_out_ports
from .python_editor_assist import python_script_field_editor_assist_payload

OPERATOR_CLASS = "f8.python_script"
logger = logging.getLogger(__name__)
_MISSING = object()
_DICT_STYLE_INPUT_METHODS: frozenset[str] = frozenset({"get", "keys", "items", "values", "to_dict"})

try:
    import numpy as np
except ModuleNotFoundError:
    np = None  # type: ignore[assignment]


@dataclass
class _VideoShmSubscription:
    key: str
    shm_name: str
    decode_mode: str
    use_event: bool
    reader: VideoShmReader | None = None
    task: asyncio.Task[object] | None = None
    latest_packet: dict[str, Any] | None = None
    last_frame_id: int = 0
    last_error_sig: str | None = None
    last_error_ts_ms: int = 0
    error_count: int = 0


class _PyEngineObjectView:
    __slots__ = ("_data", "_attr_to_key")

    def __init__(
        self,
        data: dict[str, Any],
        *,
        copy_data: bool = False,
        build_attr_index: bool = False,
    ) -> None:
        if copy_data:
            self._data = dict(data)
        else:
            self._data = data
        self._attr_to_key: dict[str, str] | None
        if build_attr_index:
            self._attr_to_key = self._build_attr_to_key(self._data)
        else:
            self._attr_to_key = None

    @staticmethod
    def _build_attr_to_key(data: dict[str, Any]) -> dict[str, str]:
        attr_to_key: dict[str, str] = {}
        for raw_key in data.keys():
            key = str(raw_key or "")
            if key.isidentifier() and not keyword.iskeyword(key):
                attr_to_key[key] = key
        return attr_to_key

    def __getitem__(self, key: str) -> Any:
        return self._wrap_value(self._data[str(key)])

    def get(self, key: str, default: Any = None) -> Any:
        key_s = str(key)
        value = self._data.get(key_s, _MISSING)
        if value is _MISSING:
            return default
        return self._wrap_value(value)

    def keys(self):
        return self._data.keys()

    def items(self):
        return ((k, self._wrap_value(v)) for k, v in self._data.items())

    def values(self):
        return (self._wrap_value(v) for v in self._data.values())

    def __contains__(self, key: object) -> bool:
        return str(key or "") in self._data

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return repr(self.to_dict())

    def __str__(self) -> str:
        return str(self.to_dict())

    def __getattr__(self, name: str) -> Any:
        attr_to_key = self._attr_to_key
        if attr_to_key is None:
            attr_to_key = self._build_attr_to_key(self._data)
            self._attr_to_key = attr_to_key
        key = attr_to_key.get(str(name or ""))
        if key is None:
            raise AttributeError(f"Unknown attribute: {name}")
        return self._wrap_value(self._data.get(key))

    def to_dict(self) -> dict[str, Any]:
        return self._unwrap_value(self._data)

    @classmethod
    def _wrap_value(cls, value: Any) -> Any:
        value_t = type(value)
        if value_t in (str, int, float, bool, type(None)):
            return value
        if isinstance(value, _PyEngineObjectView):
            return value
        if value_t is dict:
            return cls(value)
        if isinstance(value, list):
            return [cls._wrap_value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(cls._wrap_value(item) for item in value)
        return value

    @classmethod
    def _unwrap_value(cls, value: Any) -> Any:
        if isinstance(value, _PyEngineObjectView):
            return {k: cls._unwrap_value(v) for k, v in value._data.items()}
        if isinstance(value, dict):
            return {str(k): cls._unwrap_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [cls._unwrap_value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(cls._unwrap_value(item) for item in value)
        return value


class PyEngineInputsView(_PyEngineObjectView):
    pass


class PyEngineStatesView(_PyEngineObjectView):
    pass


def _script_uses_inputs_object_access(code: str) -> bool:
    """
    Returns True when onMsg/onExec script body appears to rely on dot-style
    inputs access (e.g. inputs.msg), which requires PyEngineInputsView.
    """
    try:
        module = ast.parse(str(code or ""), mode="exec")
    except SyntaxError:
        return True

    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn_name = str(node.name or "")
            if fn_name == "onMsg":
                param_name = _inputs_param_name(node, expected_pos=1)
                if param_name and _function_uses_dot_inputs_access(node, param_name):
                    return True
            elif fn_name == "onExec":
                param_name = _inputs_param_name(node, expected_pos=2)
                if param_name and _function_uses_dot_inputs_access(node, param_name):
                    return True
    return False


def _inputs_param_name(node: ast.FunctionDef | ast.AsyncFunctionDef, *, expected_pos: int) -> str | None:
    pos_args = list(node.args.posonlyargs) + list(node.args.args)
    if expected_pos >= len(pos_args):
        return None
    raw_name = str(pos_args[expected_pos].arg or "").strip()
    return raw_name or None


def _function_uses_dot_inputs_access(node: ast.FunctionDef | ast.AsyncFunctionDef, param_name: str) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Attribute):
            continue
        base = child.value
        if not isinstance(base, ast.Name):
            continue
        if str(base.id or "") != param_name:
            continue
        attr_name = str(child.attr or "")
        if attr_name in _DICT_STYLE_INPUT_METHODS:
            continue
        return True
    return False


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

    def subscribe_video_shm(self, key: str, shm_name: str, *, decode: str = "auto", use_event: bool = False) -> None:
        key_name = str(key or "").strip()
        shm = str(shm_name or "").strip()
        if not key_name or not shm:
            return
        decode_mode = self._node._normalize_decode_mode(decode)
        self._node._unsubscribe_video_shm_sync(key_name)
        sub = _VideoShmSubscription(
            key=key_name,
            shm_name=shm,
            decode_mode=decode_mode,
            use_event=bool(use_event),
        )
        self._node._video_subscriptions[key_name] = sub
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            sub.task = None
            return
        sub.task = loop.create_task(
            self._node._run_video_shm_subscription(key_name),
            name=f"python_script:video_sub:{self.node_id}:{key_name}",
        )

    def get_video_shm(self, key: str) -> dict[str, Any] | None:
        key_name = str(key or "").strip()
        if not key_name:
            return None
        sub = self._node._video_subscriptions.get(key_name)
        if sub is None:
            return None
        return self._node._copy_packet_for_script(sub.latest_packet)

    def unsubscribe_video_shm(self, key: str) -> None:
        self._node._unsubscribe_video_shm_sync(str(key or "").strip())

    def list_video_shm_subscriptions(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for key_name in sorted(self._node._video_subscriptions.keys()):
            sub = self._node._video_subscriptions.get(key_name)
            if sub is None:
                continue
            items.append(
                {
                    "key": sub.key,
                    "shmName": sub.shm_name,
                    "decodeMode": sub.decode_mode,
                    "hasPacket": sub.latest_packet is not None,
                    "lastFrameId": int(sub.last_frame_id),
                    "errorCount": int(sub.error_count),
                }
            )
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
    "# - ctx.locals is preserved between calls (script-local memory)\n"
    "# - ctx.exec_in is set only for exec-triggered calls\n"
    "# - ctx.states.<field> reads cached rw/ro state snapshot\n"
    "#   - example: ctx.states.foo / ctx.states.pose.x\n"
    "# - await ctx.read_state(field)  # fresh runtime read\n"
    "# - ctx.states.get(field)  # cached snapshot\n"
    "# - ctx.set_state(field, value)\n"
    "#   - await ctx.set_state_async(field, value)\n"
    "# - inputs supports both dot-style and dict-style access\n"
    "# - Video SHM helpers:\n"
    "#   - ctx.subscribe_video_shm(key, shm_name, decode='auto', use_event=False)\n"
    "#   - pkt = ctx.get_video_shm(key)\n"
    "#   - ctx.unsubscribe_video_shm(key)\n"
    "#   - ctx.list_video_shm_subscriptions()\n"
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
    "#     msg = inputs.msg if 'msg' in inputs else inputs.get('msg')\n"
    "#     return {'outputs': {'out': msg}}\n"
    "#\n"
    "# def onExec(ctx: 'F8PyEngineContext', exec_in: str, inputs: 'F8Inputs') -> dict[str, Any]:\n"
    "#     if exec_in == 'exec2':\n"
    "#         return {'exec': ['exec2'], 'outputs': {'out': inputs.get('msg')}}\n"
    "#     return {'exec': ['exec'], 'outputs': {'out': inputs.get('msg')}}\n"
    "#\n"
    "# def onStop(ctx: 'F8PyEngineContext') -> None:\n"
    "#     ctx.log('python_script stopped')\n"
)


class PythonScriptRuntimeNode(OperatorNode, ClosableNode):
    """
    Execute user-provided python code with lifecycle hooks:

    - onStart(ctx): invoked on construction (best-effort) and after recompiles
    - onState(ctx, field, value, ts_ms=None): invoked on state updates (except 'code')
    - onMsg(ctx, inputs): invoked on exec or data arrival
    - onStop(ctx): invoked on close() (best-effort) and before recompiles
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
        self._runtime: dict[str, Callable[..., Any]] = {}
        self._started = False
        self._closing = False
        self._last_error: str | None = None
        self._self_state_writes: dict[str, Any] = {}
        self._pull_cache_ctx_id: str | int | None = None
        self._pull_cache_outputs: dict[str, Any] = {}
        self._state_key_hint_logged = False
        self._video_subscriptions: dict[str, _VideoShmSubscription] = {}
        self._hook_on_msg: Callable[..., Any] | None = None
        self._hook_on_exec: Callable[..., Any] | None = None
        self._hook_on_state: Callable[..., Any] | None = None
        self._on_msg_only_mode = False
        self._data_out_port_set: set[str] = set()
        self._has_out_port = False
        self._prefer_raw_inputs = False
        self._refresh_data_out_port_cache()

        self._compile_and_start()

    def __del__(self) -> None:
        # Best-effort fallback: close() is awaited by ServiceBus when nodes are unregistered,
        # but __del__ provides an additional safety net for ad-hoc use.
        try:
            if self._started and not self._closing:
                self._invoke_hook_sync("onStop")
        except Exception:
            pass
        try:
            self._shutdown_video_subscriptions_sync()
        except Exception:
            pass

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

    def _set_error(self, stage: str, exc: BaseException) -> None:
        msg = f"{stage}: {exc}"
        self._last_error = msg
        logger.error("[%s:python_script] error %s", self.node_id, msg, exc_info=exc)
        try:
            loop = asyncio.get_running_loop()

            async def _set_last_error() -> None:
                try:
                    await self.set_state("lastError", msg)
                except Exception:
                    return

            loop.create_task(_set_last_error(), name=f"python_script:lastError:{self.node_id}")
        except Exception:
            pass

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000.0)

    @staticmethod
    def _collect_readable_state_names(node: F8RuntimeNode) -> tuple[str, ...]:
        raw_states = getattr(node, "stateFields", None)
        if not isinstance(raw_states, list):
            raw_states = getattr(node, "state_fields", None)
        if not isinstance(raw_states, list):
            return ()
        out: list[str] = []
        seen: set[str] = set()
        for state in raw_states:
            if isinstance(state, dict):
                name = str(state.get("name") or "").strip()
                access_raw = state.get("access")
            else:
                name = str(getattr(state, "name", "") or "").strip()
                access_raw = getattr(state, "access", None)
            if not name or name in seen:
                continue
            access = str(getattr(access_raw, "value", access_raw) or "").strip().lower()
            if access not in ("rw", "ro"):
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
    def _header_to_dict(header: VideoShmHeader) -> dict[str, int]:
        return {
            "frameId": int(header.frame_id),
            "tsMs": int(header.ts_ms),
            "width": int(header.width),
            "height": int(header.height),
            "pitch": int(header.pitch),
            "fmt": int(header.fmt),
            "notifySeq": int(header.notify_seq),
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

    def _log_video_sub_error(self, sub: _VideoShmSubscription, stage: str, exc: BaseException) -> None:
        sub.error_count += 1
        now_ms = self._now_ms()
        sig = f"{stage}:{type(exc).__name__}:{exc}"
        if sub.last_error_sig == sig and (now_ms - int(sub.last_error_ts_ms)) < 2000:
            return
        sub.last_error_sig = sig
        sub.last_error_ts_ms = now_ms
        logger.exception(
            "[%s:python_script] video shm subscribe failed key=%s shm=%s stage=%s",
            self.node_id,
            sub.key,
            sub.shm_name,
            stage,
            exc_info=exc,
        )

    @staticmethod
    def _close_video_sub_reader(sub: _VideoShmSubscription) -> None:
        reader = sub.reader
        sub.reader = None
        if reader is None:
            return
        try:
            reader.close()
        except Exception:
            return

    def _unsubscribe_video_shm_sync(self, key: str) -> bool:
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
            self._unsubscribe_video_shm_sync(key)

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

    async def _run_video_shm_subscription(self, key: str) -> None:
        key_name = str(key or "").strip()
        while True:
            sub = self._video_subscriptions.get(key_name)
            if sub is None:
                return

            if sub.reader is None:
                try:
                    reader = VideoShmReader(sub.shm_name)
                    reader.open(use_event=bool(sub.use_event))
                    sub.reader = reader
                except Exception as exc:
                    self._log_video_sub_error(sub, "open", exc)
                    await asyncio.sleep(0.2)
                    continue

            assert sub.reader is not None
            try:
                has_new = bool(sub.reader.wait_new_frame(timeout_ms=20))
                if not has_new:
                    await asyncio.sleep(0)
                    continue

                header, payload = sub.reader.read_latest_frame()
                if header is None or payload is None:
                    await asyncio.sleep(0)
                    continue

                frame_id = int(header.frame_id)
                if frame_id <= 0:
                    await asyncio.sleep(0)
                    continue
                if frame_id == int(sub.last_frame_id) and sub.latest_packet is not None:
                    await asyncio.sleep(0)
                    continue

                width = int(header.width)
                height = int(header.height)
                pitch = int(header.pitch)
                frame_bytes = int(header.frame_bytes)
                if width <= 0 or height <= 0 or pitch <= 0 or frame_bytes <= 0:
                    await asyncio.sleep(0)
                    continue
                if frame_bytes > int(header.payload_capacity):
                    await asyncio.sleep(0)
                    continue
                if frame_bytes > len(payload):
                    await asyncio.sleep(0)
                    continue

                raw = bytes(payload[:frame_bytes])
                header_dict = self._header_to_dict(header)
                decoded = self._decode_video_payload(header=header_dict, raw=raw, decode_mode=sub.decode_mode)
                sub.latest_packet = {
                    "header": header_dict,
                    "raw": raw,
                    "decoded": decoded,
                    "meta": {
                        "key": sub.key,
                        "shmName": sub.shm_name,
                        "decodeMode": sub.decode_mode,
                        "lastUpdateMs": self._now_ms(),
                    },
                }
                sub.last_frame_id = frame_id
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

    def _refresh_runtime_hooks(self) -> None:
        on_msg_raw = self._runtime.get("onMsg")
        on_exec_raw = self._runtime.get("onExec")
        on_state_raw = self._runtime.get("onState")
        self._hook_on_msg = on_msg_raw if callable(on_msg_raw) else None
        self._hook_on_exec = on_exec_raw if callable(on_exec_raw) else None
        self._hook_on_state = on_state_raw if callable(on_state_raw) else None
        self._on_msg_only_mode = self._hook_on_msg is not None and self._hook_on_exec is None

    def _refresh_data_out_port_cache(self) -> None:
        self._data_out_port_set = {str(name) for name in self.data_out_ports}
        self._has_out_port = "out" in self._data_out_port_set

    def _build_states_view(self, state_keys: tuple[str, ...]) -> PyEngineStatesView:
        resolved_keys = [str(key) for key in state_keys if str(key)]
        if not resolved_keys:
            bus = self._bus
            state_access_map = getattr(bus, "_state_access_by_node_field", {}) if bus is not None else {}
            for raw_key, raw_access in dict(state_access_map).items():
                if not isinstance(raw_key, tuple) or len(raw_key) != 2:
                    continue
                node_id, field = raw_key
                if str(node_id) != self.node_id:
                    continue
                access = str(getattr(raw_access, "value", raw_access) or "").strip().lower()
                if access not in ("rw", "ro"):
                    continue
                resolved_keys.append(str(field))
        unique_keys = tuple(sorted({key for key in resolved_keys if key}))
        snapshot: dict[str, Any] = {}
        for key in unique_keys:
            snapshot[str(key)] = self.get_state_cached(str(key), None)
        return PyEngineStatesView(snapshot)

    def _compile_script(self, code: str) -> dict[str, Callable[..., Any]]:
        env: dict[str, Any] = {"__builtins__": __builtins__}
        try:
            exec(code, env, env)
        except Exception as exc:
            self._set_error("compile", exc)
            return {}
        runtime: dict[str, Callable[..., Any]] = {}
        for hook in ("onStart", "onState", "onMsg", "onExec", "onStop"):
            fn = env.get(hook)
            if callable(fn):
                runtime[hook] = fn
        return runtime

    def _compile_and_start(self) -> None:
        if self._started:
            self._invoke_hook_sync("onStop")
        self._shutdown_video_subscriptions_sync()
        self._locals = {}
        self._ctx = self._build_ctx()
        # Normalize line endings and tabs to avoid TabError on mixed indentation.
        code = str(self._code or "")
        code = code.replace("\r\n", "\n").replace("\r", "\n")
        code = code.expandtabs(4)
        self._runtime = self._compile_script(code)
        self._prefer_raw_inputs = not _script_uses_inputs_object_access(code)
        self._refresh_runtime_hooks()
        if not self._runtime:
            self._started = False
            return
        self._invoke_hook_sync("onStart")

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
        fn = self._runtime.get(name)
        if not callable(fn):
            if name == "onStart":
                self._started = True
            elif name == "onStop":
                self._started = False
            return
        try:
            r = fn(self._ctx, *args)
            if inspect.isawaitable(r):
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(r, name=f"python_script:{name}:{self.node_id}")
                except Exception:
                    pass
        except Exception as exc:
            self._set_error(name, exc)
        finally:
            if name == "onStart":
                self._started = True
            elif name == "onStop":
                self._started = False

    async def _invoke_hook_async(self, name: str, *args: Any) -> None:
        fn = self._runtime.get(name)
        if not callable(fn):
            if name == "onStart":
                self._started = True
            elif name == "onStop":
                self._started = False
            return
        try:
            r = fn(self._ctx, *args)
            if inspect.isawaitable(r):
                await r
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
            self._code = str(value or "")
            self._compile_and_start()
            return

        # Best-effort loop prevention for state writes originating from this node (via ctx.set_state()).
        if name in self._self_state_writes and self._self_state_writes.get(name) == value:
            return

        fn = self._hook_on_state
        if not callable(fn):
            return
        try:
            r = fn(self._ctx, name, value, ts_ms)
            if inspect.isawaitable(r):
                await r
        except Exception as exc:
            self._set_error("onState", exc)

    async def validate_state(self, field: str, value: Any, *, ts_ms: int, meta: dict[str, Any]) -> Any:
        del ts_ms, meta
        name = str(field or "").strip()
        if name == "code":
            return str(value or "")
        return value

    async def on_data(self, port: str, value: Any, *, ts_ms: int | None = None) -> None:
        # Push-mode: treat incoming data as a message.
        if not self._runtime:
            return
        await self._run_on_msg({str(port): value}, exec_in=None)

    async def on_exec(self, exec_id: str | int, in_port: str | None = None) -> list[str]:
        # Exec-driven: pull current values for all inputs.
        if not self._runtime:
            return list(self._exec_out_ports)
        inputs: dict[str, Any] = {}
        for p in self.data_in_ports:
            try:
                inputs[str(p)] = await self.pull(str(p), ctx_id=exec_id)
            except Exception:
                continue
        exec_in = str(in_port or "").strip() or None
        return await self._run_on_exec(inputs, exec_in=exec_in)

    async def compute_output(self, port: str, ctx_id: str | int | None = None) -> Any:
        out_port = str(port or "")
        if out_port not in self._data_out_port_set:
            return None
        if not self._runtime:
            return None

        if ctx_id is not None and ctx_id == self._pull_cache_ctx_id:
            if out_port in self._pull_cache_outputs:
                return self._pull_cache_outputs.get(out_port)

        inputs: dict[str, Any] = {}
        for in_port in self.data_in_ports:
            try:
                inputs[str(in_port)] = await self.pull(str(in_port), ctx_id=ctx_id)
            except Exception:
                continue

        outputs = await self._compute_outputs_for_pull(inputs, exec_in=None)
        if ctx_id is not None:
            self._pull_cache_ctx_id = ctx_id
            self._pull_cache_outputs = dict(outputs)
        else:
            self._pull_cache_ctx_id = None
            self._pull_cache_outputs = {}
        return outputs.get(out_port)

    async def _run_on_exec(self, inputs: dict[str, Any], *, exec_in: str | None) -> list[str]:
        fn = self._hook_on_exec
        if callable(fn):
            inputs_obj: dict[str, Any] | PyEngineInputsView
            if self._prefer_raw_inputs:
                inputs_obj = inputs
            else:
                inputs_obj = PyEngineInputsView(inputs)
            try:
                invoke_ctx = self._build_invoke_ctx(exec_in=exec_in)
                r = fn(invoke_ctx, str(exec_in or ""), inputs_obj)
                if inspect.isawaitable(r):
                    r = await r
            except Exception as exc:
                self._set_error("onExec", exc)
                return list(self._exec_out_ports)
            out_ports = await self._apply_result(r)
            return out_ports if out_ports is not None else list(self._exec_out_ports)

        await self._run_on_msg(inputs, exec_in=exec_in)
        return list(self._exec_out_ports)

    async def _compute_outputs_for_pull(self, inputs: dict[str, Any], *, exec_in: str | None) -> dict[str, Any]:
        fn_msg = self._hook_on_msg
        fn_exec = self._hook_on_exec
        inputs_obj: dict[str, Any] | PyEngineInputsView
        if self._prefer_raw_inputs:
            inputs_obj = inputs
        else:
            inputs_obj = PyEngineInputsView(inputs)
        if self._on_msg_only_mode and callable(fn_msg):
            try:
                invoke_ctx = self._build_invoke_ctx(exec_in=exec_in)
                result = fn_msg(invoke_ctx, inputs_obj)
                if inspect.isawaitable(result):
                    result = await result
            except Exception as exc:
                self._set_error("onMsg", exc)
                return {}
            return self._extract_outputs(result)

        if callable(fn_exec):
            try:
                invoke_ctx = self._build_invoke_ctx(exec_in=exec_in)
                result = fn_exec(invoke_ctx, str(exec_in or ""), inputs_obj)
                if inspect.isawaitable(result):
                    result = await result
            except Exception as exc:
                self._set_error("onExec", exc)
                return {}
            return self._extract_outputs(result)

        if not callable(fn_msg):
            return {}
        try:
            invoke_ctx = self._build_invoke_ctx(exec_in=exec_in)
            result = fn_msg(invoke_ctx, inputs_obj)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            self._set_error("onMsg", exc)
            return {}
        return self._extract_outputs(result)

    def _extract_outputs(self, result: Any) -> dict[str, Any]:
        if result is None:
            return {}

        data_out_ports = self._data_out_port_set
        outputs: dict[str, Any] = {}
        if isinstance(result, dict):
            raw_outputs = result.get("outputs")
            if isinstance(raw_outputs, dict):
                # Fast-path for dominant script pattern: {"outputs": {"tcode": value}}.
                if len(raw_outputs) == 1:
                    for raw_key, raw_value in raw_outputs.items():
                        if isinstance(raw_key, str):
                            if raw_key in data_out_ports:
                                return {raw_key: raw_value}
                            return {}
                        key_s = str(raw_key)
                        if key_s in data_out_ports:
                            return {key_s: raw_value}
                        return {}
                for k, v in raw_outputs.items():
                    k_s = str(k)
                    if k_s in data_out_ports:
                        outputs[k_s] = v
                return outputs

            for k, v in result.items():
                k_s = str(k)
                if k_s in ("exec", "outputs"):
                    continue
                if k_s in data_out_ports:
                    outputs[k_s] = v
            return outputs

        if "out" in data_out_ports:
            outputs["out"] = result
        return outputs

    async def _run_on_msg(self, inputs: dict[str, Any], *, exec_in: str | None) -> None:
        fn = self._hook_on_msg
        if not callable(fn):
            return
        inputs_obj: dict[str, Any] | PyEngineInputsView
        if self._prefer_raw_inputs:
            inputs_obj = inputs
        else:
            inputs_obj = PyEngineInputsView(inputs)
        try:
            invoke_ctx = self._build_invoke_ctx(exec_in=exec_in)
            r = fn(invoke_ctx, inputs_obj)
            if inspect.isawaitable(r):
                r = await r
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
          - backward compat: if "outputs" missing, treat remaining keys (excluding "exec") as outputs
        - non-dict: emit to 'out' if present
        Returns selected exec out ports if provided, else None.
        """
        if r is None:
            return None

        if isinstance(r, dict):
            exec_sel = r.get("exec") if "exec" in r else None
            outputs = self._extract_outputs(r)
            for k, v in outputs.items():
                try:
                    await self.emit(str(k), v)
                except Exception:
                    continue

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
                await self.emit("out", r)
            except Exception:
                pass
        return None


PythonScriptRuntimeNode.SPEC = F8OperatorSpec(
    schemaVersion=F8OperatorSchemaVersion.f8operator_1,
    serviceClass=SERVICE_CLASS,
    operatorClass=OPERATOR_CLASS,
    version="0.0.1",
    label="Python Script",
    description="Execute Python code with onStart/onState/onMsg/onExec/onStop hooks.",
    tags=["script", "python", "programmable"],
    execInPorts=["exec"],
    execOutPorts=["exec"],
    editableExecInPorts=True,
    editableExecOutPorts=True,
    dataInPorts=[F8DataPortSpec(name="msg", description="Message input", valueSchema=any_schema(), required=False)],
    dataOutPorts=[F8DataPortSpec(name="out", description="Script output", valueSchema=any_schema(), required=False)],
    editableDataInPorts=True,
    editableDataOutPorts=True,
    stateFields=[
        F8StateSpec(
            name="code",
            label="Code",
            description="Python source code defining onStart(ctx), onMsg(ctx, inputs), onStop(ctx).",
            uiControl="code",
            uiLanguage="python",
            valueSchema=string_schema(default=DEFAULT_CODE),
            access=F8StateAccess.rw,
            required=True,
            showOnNode=False,
            editorAssist=python_script_field_editor_assist_payload(),
        ),
        F8StateSpec(
            name="lastError",
            label="Last Error",
            description="Last script error (compile/runtime).",
            valueSchema=string_schema(default=""),
            access=F8StateAccess.wo,
            required=True,
            showOnNode=False,
        ),
    ],
    editableStateFields=True,
)


def register_operator(registry: RuntimeNodeRegistry | None = None) -> RuntimeNodeRegistry:
    reg = registry or RuntimeNodeRegistry.instance()

    def _factory(node_id: str, node: F8RuntimeNode, initial_state: dict[str, Any]) -> OperatorNode:
        return PythonScriptRuntimeNode(node_id=node_id, node=node, initial_state=initial_state)

    reg.register(SERVICE_CLASS, OPERATOR_CLASS, _factory, overwrite=True)
    reg.register_operator_spec(PythonScriptRuntimeNode.SPEC, overwrite=True)
    return reg

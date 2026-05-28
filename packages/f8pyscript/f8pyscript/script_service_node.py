from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Callable

from f8pysdk.bus import ServiceBus
from f8pysdk.capabilities import ClosableNode, CommandableNode
from f8pysdk.codec import unwrap_json_value
from f8pysdk.f8_naming import ensure_token
from f8pysdk.nodes import ServiceNode

from .error_reporter import PyScriptErrorReporter
from .local_exec import PyScriptLocalExec, PyScriptPermissionContext
from .script_runtime_values import (
    PyScriptStatesView,
    ScriptOutputPorts,
    build_script_output_ports,
    extract_script_outputs,
    normalize_script_output_value,
)
from .script_runtime import PyScriptHookSet, PyScriptRuntimeCompiler
from .video_latest import VideoLatestConfig, VideoLatestSubscriptions

logger = logging.getLogger(__name__)
_DESTRUCTOR_CLEANUP_ERRORS = (OSError, RuntimeError, TypeError, ValueError)
_HOOK_AWAITABLE_SCHEDULE_ERRORS = (RuntimeError, TypeError, ValueError)
_SCRIPT_OUTPUT_ERRORS = (LookupError, OSError, RuntimeError, TypeError, ValueError)
_SCRIPT_COMPILE_ERRORS = (Exception,)
_SCRIPT_USER_HOOK_ERRORS = (Exception,)
_SCRIPT_TICK_LOOP_ERRORS = (Exception,)


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
    "# - ctx.subscribe_video_latest(key, stream_key='f8/svc/.../data/video', decode='auto')\n"
    "# - pkt = ctx.get_video_latest(key)\n"
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
        self._node._video_latest.subscribe(key_name, stream_key=stream_key_text, decode=decode)

    def get_video_latest(self, key: str) -> dict[str, Any] | None:
        key_name = str(key or "").strip()
        if not key_name:
            return None
        return self._node._video_latest.get_packet(key_name)

    def unsubscribe_video_latest(self, key: str) -> None:
        self._node._video_latest.unsubscribe_sync(str(key or "").strip())

    def list_video_latest_subscriptions(self) -> list[dict[str, Any]]:
        return self._node._video_latest.list_status()

    async def exec_local(
        self,
        command: str,
        args: list[str] | tuple[str, ...] | None = None,
        *,
        timeout_ms: int | None = None,
        cwd: str | None = None,
        env: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._node._local_exec.exec_local(
            command,
            args,
            timeout_ms=timeout_ms,
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

        self._hooks = PyScriptHookSet.empty()

        self._started = False
        self._paused = False
        self._active = True
        self._closing = False

        self._error_reporter = PyScriptErrorReporter(
            node_id=self.node_id,
            logger=logger,
            report_error=self.report_error,
        )
        self._self_state_writes: dict[str, Any] = {}

        self._tick_enabled = bool(self._initial_state.get("tickEnabled") or False)
        self._tick_ms = self._coerce_tick_ms(self._initial_state.get("tickMs"), default=100)
        self._tick_task: asyncio.Task[object] | None = None
        self._tick_seq = 0

        self._video_latest = VideoLatestSubscriptions(
            node_id=self.node_id,
            log_context="pyscript",
            read_enabled=self._is_video_latest_read_enabled,
        )

        self._local_exec = PyScriptLocalExec(node_id=self.node_id, now_ms=self._now_ms)
        self._script_runtime = PyScriptRuntimeCompiler(is_local_exec_allowed=self._local_exec.is_allowed)

        self._script_output_ports = ScriptOutputPorts(
            data_out_ports=frozenset(),
            single_data_out_port=None,
            has_out_port=False,
        )
        self._refresh_data_out_port_cache()

        self._ctx: PyScriptServiceContext = self._build_ctx()

        self._compile_and_start()

    def __del__(self) -> None:
        if self._started and not self._closing:
            try:
                self._invoke_sync(self._hooks.on_stop, self._hooks.on_stop_is_async, "onStop")
            except _DESTRUCTOR_CLEANUP_ERRORS as exc:
                logger.error("[%s:pyscript] __del__ onStop failed", self.node_id, exc_info=exc)
        try:
            self._video_latest.shutdown_sync()
        except _DESTRUCTOR_CLEANUP_ERRORS as exc:
            logger.error("[%s:pyscript] __del__ video cleanup failed", self.node_id, exc_info=exc)

    def attach(self, bus: Any) -> None:
        super().attach(bus)
        if isinstance(bus, ServiceBus):
            cfg = bus.config
            self._video_latest.configure(
                VideoLatestConfig(
                    config_path=cfg.zenoh_config_path,
                    connect=cfg.zenoh_connect,
                    listen=cfg.zenoh_listen,
                    shm_pool_bytes=cfg.zenoh_shm_pool_bytes,
                )
            )

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

    def _set_error(self, stage: str, exc: BaseException) -> None:
        self._error_reporter.set_error(stage, exc)

    def _log_error_deduped(self, key: str, message: str, exc: BaseException) -> None:
        self._error_reporter.log_deduped(key, message, exc)

    async def _set_runtime_state(self, field: str, value: Any) -> None:
        self._self_state_writes[str(field)] = value
        await self.set_state(str(field), value)

    def _permission_context(self) -> PyScriptPermissionContext:
        return self._local_exec.permission_context()

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
        self._script_output_ports = build_script_output_ports(self.data_out_ports)

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
        self._hooks = self._script_runtime.compile(code)

    def _close_unscheduled_hook_awaitable(self, stage: str, result: Any) -> None:
        if not inspect.iscoroutine(result):
            return
        try:
            result.close()
        except RuntimeError as exc:
            self._set_error(f"{stage}:schedule", exc)

    def _schedule_sync_hook_awaitable(self, stage: str, result: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            self._close_unscheduled_hook_awaitable(stage, result)
            self._set_error(f"{stage}:schedule", exc)
            return
        try:
            task = asyncio.ensure_future(result, loop=loop)
        except _HOOK_AWAITABLE_SCHEDULE_ERRORS as exc:
            self._close_unscheduled_hook_awaitable(stage, result)
            self._set_error(f"{stage}:schedule", exc)
            return
        if isinstance(task, asyncio.Task):
            task.set_name(f"pyscript:{stage}:{self.node_id}")

    def _compile_and_start(self) -> None:
        if self._started:
            self._invoke_sync(self._hooks.on_stop, self._hooks.on_stop_is_async, "onStop")
        self._video_latest.shutdown_sync()

        self._locals = {}
        self._ctx = self._build_ctx()

        code = str(self._code or "").replace("\r\n", "\n").replace("\r", "\n").expandtabs(4)
        self._hooks = PyScriptHookSet.empty()

        try:
            self._compile_script(code)
        except _SCRIPT_COMPILE_ERRORS as exc:
            self._started = False
            self._set_error("compile", exc)
            return

        self._invoke_sync(self._hooks.on_start, self._hooks.on_start_is_async, "onStart")
        self._started = True
        self._paused = False
        self._ensure_tick_task()

    def _invoke_sync(self, hook: Callable[..., Any] | None, hook_is_async: bool, stage: str, *args: Any) -> Any:
        if hook is None:
            return None
        try:
            invoke_ctx = self._build_invoke_ctx()
            if hook_is_async:
                coroutine = hook(invoke_ctx, *args)
                self._schedule_sync_hook_awaitable(stage, coroutine)
                return None

            result = hook(invoke_ctx, *args)
            if inspect.isawaitable(result):
                self._schedule_sync_hook_awaitable(stage, result)
                return None
            return result
        except _SCRIPT_USER_HOOK_ERRORS as exc:
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
        except _SCRIPT_USER_HOOK_ERRORS as exc:
            self._set_error(stage, exc)
            raise

    async def _emit_outputs(self, result: Any) -> None:
        try:
            outputs = extract_script_outputs(result, ports=self._script_output_ports)
        except ValueError as exc:
            self._set_error("result", exc)
            return
        for out_port, out_value in outputs.items():
            try:
                await self.emit(str(out_port), out_value)
            except _SCRIPT_OUTPUT_ERRORS as exc:
                self._set_error(f"result:emit:{out_port}", exc)
                return

    def _is_video_latest_read_enabled(self) -> bool:
        return bool(self._active and not self._paused)

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

                if self._hooks.on_tick is not None:
                    result, self._hooks.on_tick_maybe_awaitable = await self._invoke_async(
                        self._hooks.on_tick,
                        self._hooks.on_tick_is_async,
                        self._hooks.on_tick_maybe_awaitable,
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
            except _SCRIPT_TICK_LOOP_ERRORS as exc:
                self._log_error_deduped("tick_loop", "tick loop failed", exc)
                await asyncio.sleep(0.05)

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            _, _ = await self._invoke_async(
                self._hooks.on_stop,
                self._hooks.on_stop_is_async,
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
            await self._video_latest.shutdown_async()

    async def on_lifecycle(self, active: bool, meta: dict[str, Any]) -> None:
        self._active = bool(active)
        if not self._started:
            return

        if self._active:
            if self._paused:
                self._paused = False
                result, _ = await self._invoke_async(
                    self._hooks.on_resume,
                    self._hooks.on_resume_is_async,
                    True,
                    "onResume",
                    dict(meta or {}),
                )
                await self._emit_outputs(result)
            return

        if not self._paused:
            self._paused = True
            result, _ = await self._invoke_async(
                self._hooks.on_pause,
                self._hooks.on_pause_is_async,
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

        if self._hooks.on_state is None:
            return
        result, self._hooks.on_state_maybe_awaitable = await self._invoke_async(
            self._hooks.on_state,
            self._hooks.on_state_is_async,
            self._hooks.on_state_maybe_awaitable,
            "onState",
            name,
            value_unwrapped,
            ts_ms,
        )
        await self._emit_outputs(result)

    async def on_data(self, port: str, value: Any, *, ts_ms: int | None = None) -> None:
        if not self._active or self._paused:
            return
        if self._hooks.on_data is None:
            return
        in_port = str(port or "")
        result, self._hooks.on_data_maybe_awaitable = await self._invoke_async(
            self._hooks.on_data,
            self._hooks.on_data_is_async,
            self._hooks.on_data_maybe_awaitable,
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
            ttl_ms = self._local_exec.coerce_ttl_ms(call_args.get("ttlMs"))
            session_id = call_meta.get("reqId") or call_meta.get("sessionId")
            return self._local_exec.grant(ttl_ms=ttl_ms, session_id=session_id)

        if call == "revoke_local_exec":
            return self._local_exec.revoke()

        if self._hooks.on_command is None:
            raise ValueError(f"unknown command: {call}")

        result, _ = await self._invoke_async(
            self._hooks.on_command,
            self._hooks.on_command_is_async,
            True,
            "onCommand",
            call,
            call_args,
            call_meta,
        )
        return {"ok": True, "result": normalize_script_output_value(result)}

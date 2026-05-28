from __future__ import annotations

import logging
import time
from typing import Any

from f8pysdk.bus import ServiceBus
from f8pysdk.capabilities import ClosableNode, CommandableNode
from f8pysdk.codec import unwrap_json_value
from f8pysdk.f8_naming import ensure_token
from f8pysdk.nodes import ServiceNode

from .error_reporter import PyScriptErrorReporter
from .hook_invoker import PyScriptHookInvoker
from .local_exec import PyScriptLocalExec, PyScriptPermissionContext
from .script_context import PyScriptServiceContext
from .script_runtime_values import (
    ScriptOutputPorts,
    build_script_output_ports,
    extract_script_outputs,
    normalize_script_output_value,
)
from .script_runtime import PyScriptHookSet, PyScriptRuntimeCompiler
from .state_access import PyScriptStateAccess, collect_readable_state_names
from .tick_scheduler import PyScriptTickScheduler
from .video_latest import VideoLatestConfig, VideoLatestSubscriptions

logger = logging.getLogger(__name__)
_DESTRUCTOR_CLEANUP_ERRORS = (OSError, RuntimeError, TypeError, ValueError)
_SCRIPT_OUTPUT_ERRORS = (LookupError, OSError, RuntimeError, TypeError, ValueError)
_SCRIPT_COMPILE_ERRORS = (Exception,)


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


class PythonScriptServiceNode(ServiceNode, CommandableNode, ClosableNode):
    def __init__(self, *, node_id: str, node: Any, initial_state: dict[str, Any] | None = None) -> None:
        super().__init__(
            node_id=ensure_token(node_id, label="node_id"),
            data_in_ports=[str(p.name) for p in list(node.dataInPorts or [])],
            data_out_ports=[str(p.name) for p in list(node.dataOutPorts or [])],
            state_fields=[str(s.name) for s in list(node.stateFields or [])],
        )
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
        self._state_access = PyScriptStateAccess(
            readable_state_names=collect_readable_state_names(list(node.stateFields or [])),
            state_fields=lambda: list(self.state_fields),
            get_cached=self.get_state_cached,
            set_state=self.set_state,
        )
        self._hook_invoker = PyScriptHookInvoker(
            node_id=self.node_id,
            build_context=self._build_invoke_ctx,
            set_error=self._set_error,
        )

        self._tick_scheduler = PyScriptTickScheduler(
            node_id=self.node_id,
            tick_enabled=bool(self._initial_state.get("tickEnabled") or False),
            tick_ms=PyScriptTickScheduler.coerce_tick_ms(self._initial_state.get("tickMs"), default=100),
            now_ms=self._now_ms,
            is_closing=lambda: bool(self._closing),
            is_tick_allowed=self._is_tick_allowed,
            run_tick=self._run_tick,
            log_error=self._log_error_deduped,
        )

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
                self._hook_invoker.invoke_sync(self._hooks.on_stop, self._hooks.on_stop_is_async, "onStop")
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

    def _set_error(self, stage: str, exc: BaseException) -> None:
        self._error_reporter.set_error(stage, exc)

    def _log_error_deduped(self, key: str, message: str, exc: BaseException) -> None:
        self._error_reporter.log_deduped(key, message, exc)

    def _permission_context(self) -> PyScriptPermissionContext:
        return self._local_exec.permission_context()

    def _build_ctx(self) -> PyScriptServiceContext:
        return PyScriptServiceContext(
            service_id=self.node_id,
            locals=self._locals,
            state_keys=self._state_access.readable_state_names,
            permission=self._permission_context(),
            build_states_view=self._state_access.build_states_view,
            emit_value=self.emit,
            set_state_value=self._state_access.set_runtime_state,
            read_state_value=self.get_state_value,
            subscribe_video_latest_value=self._video_latest.subscribe,
            get_video_latest_value=self._video_latest.get_packet,
            unsubscribe_video_latest_value=self._video_latest.unsubscribe_sync,
            list_video_latest_values=self._video_latest.list_status,
            exec_local_value=self._local_exec.exec_local,
        )

    def _build_invoke_ctx(self) -> PyScriptServiceContext:
        permission = self._permission_context()
        if self._ctx.permission == permission:
            return self._ctx
        self._ctx = self._ctx.with_permission(permission)
        return self._ctx

    def _refresh_data_out_port_cache(self) -> None:
        self._script_output_ports = build_script_output_ports(self.data_out_ports)

    def _compile_script(self, code: str) -> None:
        self._hooks = self._script_runtime.compile(code)

    def _compile_and_start(self) -> None:
        if self._started:
            self._hook_invoker.invoke_sync(self._hooks.on_stop, self._hooks.on_stop_is_async, "onStop")
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

        self._hook_invoker.invoke_sync(self._hooks.on_start, self._hooks.on_start_is_async, "onStart")
        self._started = True
        self._paused = False
        self._tick_scheduler.ensure_task()

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

    def _is_tick_allowed(self) -> bool:
        return bool(self._started and not self._paused)

    async def _run_tick(self, tick_payload: dict[str, int]) -> None:
        if self._hooks.on_tick is None:
            return
        result, self._hooks.on_tick_maybe_awaitable = await self._hook_invoker.invoke_async(
            self._hooks.on_tick,
            self._hooks.on_tick_is_async,
            self._hooks.on_tick_maybe_awaitable,
            "onTick",
            tick_payload,
        )
        await self._emit_outputs(result)

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            _, _ = await self._hook_invoker.invoke_async(
                self._hooks.on_stop,
                self._hooks.on_stop_is_async,
                True,
                "onStop",
            )
            self._started = False
            self._paused = False
            await self._tick_scheduler.shutdown()
        finally:
            await self._video_latest.shutdown_async()

    async def on_lifecycle(self, active: bool, meta: dict[str, Any]) -> None:
        self._active = bool(active)
        if not self._started:
            return

        if self._active:
            if self._paused:
                self._paused = False
                result, _ = await self._hook_invoker.invoke_async(
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
            result, _ = await self._hook_invoker.invoke_async(
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
            return PyScriptTickScheduler.coerce_tick_ms(value_unwrapped, default=self._tick_scheduler.tick_ms)
        return value_unwrapped

    async def on_state(self, field: str, value: Any, *, ts_ms: int | None = None) -> None:
        name = str(field or "").strip()
        value_unwrapped = unwrap_json_value(value)

        if name == "code":
            self._code = str(value_unwrapped or "")
            self._compile_and_start()
            return
        if name == "tickEnabled":
            self._tick_scheduler.set_enabled(bool(value_unwrapped))
            return
        if name == "tickMs":
            self._tick_scheduler.set_tick_ms(value_unwrapped)
            return

        if self._state_access.is_self_state_write(name, value_unwrapped):
            return

        if self._hooks.on_state is None:
            return
        result, self._hooks.on_state_maybe_awaitable = await self._hook_invoker.invoke_async(
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
        result, self._hooks.on_data_maybe_awaitable = await self._hook_invoker.invoke_async(
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

        result, _ = await self._hook_invoker.invoke_async(
            self._hooks.on_command,
            self._hooks.on_command_is_async,
            True,
            "onCommand",
            call,
            call_args,
            call_meta,
        )
        return {"ok": True, "result": normalize_script_output_value(result)}

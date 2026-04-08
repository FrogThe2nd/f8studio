from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from f8pysdk.nats_naming import svc_micro_name
from f8pysdk.service_runtime_tools.nats_bootstrap import ensure_nats_server_with_result, stop_nats_server_process

SINGLETON_GUARD_LOG_MESSAGE = "Another PyStudio instance is already running (micro service ping responded)."
SINGLETON_GUARD_DIALOG_TITLE = "F8PyStudio Already Running"
SINGLETON_GUARD_DIALOG_MESSAGE = (
    "Another F8PyStudio instance is already running.\n"
    "Please switch to the existing window."
)


class NatsConnectFunc(Protocol):
    async def __call__(
        self,
        nats_url: str,
        error_cb: Callable[[Exception], Awaitable[None]],
        connect_timeout_s: float,
        allow_reconnect: bool,
    ) -> Any: ...


class EnsureNatsServerFunc(Protocol):
    def __call__(self, nats_url: str, *, log_cb: Callable[[str], None]) -> Any: ...


class StopNatsServerFunc(Protocol):
    def __call__(self, pid: int, *, log_cb: Callable[[str], None]) -> bool: ...


async def _default_connect(
    nats_url: str,
    error_cb: Callable[[Exception], Awaitable[None]],
    connect_timeout_s: float,
    allow_reconnect: bool,
) -> Any:
    import nats  # type: ignore[import-not-found]

    return await nats.connect(
        servers=[str(nats_url)],
        connect_timeout=float(connect_timeout_s),
        allow_reconnect=bool(allow_reconnect),
        error_cb=error_cb,
    )


@dataclass(frozen=True)
class NatsSingletonGuardResult:
    should_start: bool
    connection: Any | None


@dataclass
class NatsConnectionManager:
    nats_url: str
    emit_log: Callable[[str], None]
    report_exception: Callable[[str, BaseException], None]
    connect_timeout_s: float = 2.0
    error_log_interval_s: float = 2.0
    _connect_func: NatsConnectFunc = _default_connect
    _last_error_log_s: float = 0.0

    async def _emit_connect_error(self, exc: Exception, *, will_retry: bool) -> None:
        now = time.monotonic()
        if (now - float(self._last_error_log_s)) < float(self.error_log_interval_s):
            return
        self._last_error_log_s = now
        retry_suffix = " (will retry)" if will_retry else ""
        self.emit_log(f"NATS not reachable at {self.nats_url!r}{retry_suffix}: {type(exc).__name__}: {exc}")

    async def _on_connect_error_retry(self, exc: Exception) -> None:
        await self._emit_connect_error(exc, will_retry=True)

    async def _on_connect_error_fail_fast(self, exc: Exception) -> None:
        await self._emit_connect_error(exc, will_retry=False)

    async def connect(self, *, context: str, allow_reconnect: bool = True) -> Any | None:
        error_cb = self._on_connect_error_retry if allow_reconnect else self._on_connect_error_fail_fast
        try:
            return await self._connect_func(
                str(self.nats_url).strip(),
                error_cb,
                float(self.connect_timeout_s),
                bool(allow_reconnect),
            )
        except Exception as exc:
            self.report_exception(context, exc)
            return None

    async def close(self, connection: Any | None, *, context: str) -> None:
        if connection is None:
            return
        try:
            await connection.close()
        except Exception as exc:
            self.report_exception(context, exc)

    async def singleton_guard(
        self,
        connection: Any | None,
        *,
        studio_service_id: str,
        ping_timeout_s: float = 0.2,
    ) -> NatsSingletonGuardResult:
        if connection is None:
            return NatsSingletonGuardResult(should_start=True, connection=None)

        try:
            await connection.request(
                f"$SRV.PING.{svc_micro_name(str(studio_service_id))}",
                b"",
                timeout=float(ping_timeout_s),
            )
            self.emit_log(SINGLETON_GUARD_LOG_MESSAGE)
            await self.close(connection, context="close nats connection failed after singleton ping")
            return NatsSingletonGuardResult(should_start=False, connection=None)
        except Exception as exc:
            exc_name = type(exc).__name__
            if exc_name not in {"TimeoutError", "NoRespondersError"}:
                self.report_exception("singleton ping failed", exc)
            return NatsSingletonGuardResult(should_start=True, connection=connection)


async def ensure_nats_server_owned_pid(
    nats_url: str,
    *,
    emit_log: Callable[[str], None],
    report_exception: Callable[[str, BaseException], None],
    bootstrap_func: EnsureNatsServerFunc = ensure_nats_server_with_result,
) -> int | None:
    try:
        bootstrap_result = await asyncio.to_thread(bootstrap_func, str(nats_url), log_cb=emit_log)
    except Exception as exc:
        report_exception("ensure nats server failed", exc)
        return None
    try:
        started_by_current_process = bool(bootstrap_result.started_by_current_process)
        started_pid = bootstrap_result.started_pid
    except AttributeError:
        return None
    if not started_by_current_process:
        return None
    if started_pid is None:
        return None
    try:
        return int(started_pid)
    except (TypeError, ValueError):
        return None


async def stop_owned_nats_server(
    owned_pid: int,
    *,
    emit_log: Callable[[str], None],
    report_exception: Callable[[str, BaseException], None],
    stop_func: StopNatsServerFunc = stop_nats_server_process,
) -> bool:
    try:
        return bool(
            await asyncio.to_thread(
                stop_func,
                int(owned_pid),
                log_cb=emit_log,
            )
        )
    except Exception as exc:
        report_exception("stop studio-owned nats server failed", exc)
        return False

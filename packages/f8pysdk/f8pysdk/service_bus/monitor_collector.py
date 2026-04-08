from __future__ import annotations

"""
Runtime monitor snapshot collection.

`MonitorCollector` is implemented under `service_bus` because it samples bus
runtime state, but the stable SDK-facing import is `f8pysdk.monitoring`.
"""

from ..msgspec_codec import dump_json, validate_as
import asyncio
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..codec import encode_obj
from ..generated import (
    F8MonitorCpu,
    F8MonitorError,
    F8MonitorFrame,
    F8MonitorGpu,
    F8MonitorMemory,
    F8MonitorQueue,
    F8MonitorSnapshot,
    F8MonitorTiming,
)
from ..nats_naming import data_subject
from ..time_utils import now_ms

if TYPE_CHECKING:
    from .runtime import ServiceBus


def _percentile95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    idx = int(round((len(ordered) - 1) * 0.95))
    if idx < 0:
        idx = 0
    if idx >= len(ordered):
        idx = len(ordered) - 1
    return float(ordered[idx])


class _TimedValues:
    def __init__(self, *, window_ms: int) -> None:
        self._window_ms = max(1000, int(window_ms))
        self._values: deque[tuple[int, float]] = deque()

    def set_window_ms(self, window_ms: int) -> None:
        self._window_ms = max(1000, int(window_ms))

    def add(self, *, ts_ms: int, value: float) -> None:
        self._values.append((int(ts_ms), float(value)))
        self._prune(int(ts_ms))

    def _prune(self, now_ms: int) -> None:
        cutoff = int(now_ms) - int(self._window_ms)
        while self._values and int(self._values[0][0]) < cutoff:
            self._values.popleft()

    def values(self, *, now_ms: int) -> list[float]:
        self._prune(int(now_ms))
        return [float(item[1]) for item in self._values]


class _ProcessSampler:
    def __init__(self) -> None:
        self._last_wall = time.perf_counter()
        self._last_proc_cpu = time.process_time()

    def sample_process_percent(self) -> float:
        wall = time.perf_counter()
        proc_cpu = time.process_time()
        d_wall = float(wall - self._last_wall)
        d_cpu = float(proc_cpu - self._last_proc_cpu)
        self._last_wall = wall
        self._last_proc_cpu = proc_cpu
        if d_wall <= 0.0:
            return 0.0
        cpu_count = float(max(1, int(os.cpu_count() or 1)))
        percent = (d_cpu / d_wall) * 100.0 / cpu_count
        if percent < 0.0:
            return 0.0
        return float(percent)

    def sample_system_percent(self) -> float:
        try:
            load1, _, _ = os.getloadavg()
        except (AttributeError, OSError):
            return 0.0
        cpu_count = float(max(1, int(os.cpu_count() or 1)))
        percent = (float(load1) / cpu_count) * 100.0
        if percent < 0.0:
            return 0.0
        return float(percent)


class _MemorySampler:
    def __init__(self) -> None:
        self._psutil_process = None
        try:
            import psutil  # type: ignore[import-not-found]

            self._psutil_process = psutil.Process(os.getpid())
        except (ImportError, OSError, RuntimeError):
            self._psutil_process = None

    def sample(self) -> tuple[int, int]:
        if self._psutil_process is not None:
            try:
                info = self._psutil_process.memory_info()
                rss = int(info.rss)
                vms = int(info.vms)
                if rss < 0:
                    rss = 0
                if vms < 0:
                    vms = 0
                return rss, vms
            except (AttributeError, OSError, RuntimeError, ValueError):
                return 0, 0
        try:
            import resource

            usage = resource.getrusage(resource.RUSAGE_SELF)
            rss_kb = int(usage.ru_maxrss)
            if rss_kb < 0:
                rss_kb = 0
            return rss_kb * 1024, 0
        except (ImportError, AttributeError, OSError, RuntimeError, ValueError):
            return 0, 0


class _GpuSampler:
    def __init__(self, *, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self._nvml = None
        self._initialized = False
        if not self._enabled:
            return
        try:
            import pynvml  # type: ignore[import-not-found]

            pynvml.nvmlInit()
            self._nvml = pynvml
            self._initialized = True
        except (ImportError, OSError, RuntimeError):
            self._nvml = None
            self._initialized = False

    def close(self) -> None:
        if self._initialized and self._nvml is not None:
            try:
                self._nvml.nvmlShutdown()
            except (OSError, RuntimeError):
                pass
        self._initialized = False
        self._nvml = None

    def sample(self) -> F8MonitorGpu:
        if (not self._enabled) or (not self._initialized) or self._nvml is None:
            return F8MonitorGpu(
                vendor="",
                deviceIndex=None,
                utilPercent=None,
                memoryUsedBytes=None,
                memoryTotalBytes=None,
                available=False,
            )
        try:
            handle = self._nvml.nvmlDeviceGetHandleByIndex(0)
            util = self._nvml.nvmlDeviceGetUtilizationRates(handle)
            mem = self._nvml.nvmlDeviceGetMemoryInfo(handle)
            return F8MonitorGpu(
                vendor="nvidia",
                deviceIndex=0,
                utilPercent=float(util.gpu),
                memoryUsedBytes=int(mem.used),
                memoryTotalBytes=int(mem.total),
                available=True,
            )
        except (OSError, RuntimeError, ValueError):
            return F8MonitorGpu(
                vendor="nvidia",
                deviceIndex=0,
                utilPercent=None,
                memoryUsedBytes=None,
                memoryTotalBytes=None,
                available=False,
            )


@dataclass(frozen=True)
class MonitorCollectorConfig:
    enabled: bool = True
    interval_ms: int = 1000
    window_ms: int = 30000
    gpu_enabled: bool = True


class MonitorCollector:
    def __init__(self, bus: "ServiceBus", config: MonitorCollectorConfig) -> None:
        self._bus = bus
        self._enabled = bool(config.enabled)
        self._interval_ms = max(200, int(config.interval_ms))
        self._window_ms = max(1000, int(config.window_ms))
        self._process_sampler = _ProcessSampler()
        self._memory_sampler = _MemorySampler()
        self._gpu_sampler = _GpuSampler(enabled=bool(config.gpu_enabled))
        self._lock = threading.Lock()

        self._ready = False
        self._observed = 0
        self._processed = 0
        self._dropped = 0
        self._local_only_emits = 0
        self._routed_cross_emits = 0
        self._suppressed_cross_publishes = 0
        self._callback_deliveries = 0
        self._buffer_pull_deliveries = 0
        self._last_error_code = ""
        self._last_error_message = ""
        self._last_error_ts_ms: int | None = None
        self._error_events: deque[int] = deque()
        self._wait_values = _TimedValues(window_ms=self._window_ms)
        self._process_values = _TimedValues(window_ms=self._window_ms)
        self._latency_values = _TimedValues(window_ms=self._window_ms)
        self._node_last_input_ts_ms: dict[str, int] = {}
        self._started_ts_ms = int(now_ms())

        self._task: asyncio.Task[object] | None = None
        self._latest: F8MonitorSnapshot | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._enabled)

    def latest_snapshot(self) -> F8MonitorSnapshot | None:
        with self._lock:
            latest = self._latest
        return latest

    def record_ready(self, ready: bool) -> None:
        if not self._enabled:
            return
        with self._lock:
            self._ready = bool(ready)

    def record_observed(self, *, port: str) -> None:
        if (not self._enabled) or str(port) == "monitor":
            return
        with self._lock:
            self._observed += 1

    def record_processed(self, *, port: str, emit_ts_ms: int, now_ts_ms: int) -> None:
        if (not self._enabled) or str(port) == "monitor":
            return
        with self._lock:
            self._processed += 1
            if int(emit_ts_ms) > 0 and int(now_ts_ms) >= int(emit_ts_ms):
                self._process_values.add(ts_ms=int(now_ts_ms), value=float(int(now_ts_ms) - int(emit_ts_ms)))

    def record_input_sample_ts(self, *, node_id: str, sample_ts_ms: int) -> None:
        if not self._enabled:
            return
        sid = str(node_id or "").strip()
        if not sid:
            return
        ts = int(sample_ts_ms)
        if ts <= 0:
            return
        with self._lock:
            existing = self._node_last_input_ts_ms.get(sid)
            if existing is None or int(ts) < int(existing):
                self._node_last_input_ts_ms[sid] = int(ts)

    def record_emit_completed(self, *, node_id: str, now_ts_ms: int) -> None:
        if not self._enabled:
            return
        sid = str(node_id or "").strip()
        if not sid:
            return
        now_ts = int(now_ts_ms)
        with self._lock:
            input_ts = self._node_last_input_ts_ms.pop(sid, None)
            if input_ts is None:
                return
            if now_ts < int(input_ts):
                return
            self._latency_values.add(ts_ms=now_ts, value=float(now_ts - int(input_ts)))

    def record_wait_ms(self, *, wait_ms: float) -> None:
        if not self._enabled:
            return
        if wait_ms < 0.0:
            return
        now_ts = int(now_ms())
        with self._lock:
            self._wait_values.add(ts_ms=now_ts, value=float(wait_ms))

    def record_dropped(self, *, dropped_count: int) -> None:
        if not self._enabled:
            return
        if dropped_count <= 0:
            return
        with self._lock:
            self._dropped += int(dropped_count)

    def record_local_only_emit(self) -> None:
        if not self._enabled:
            return
        with self._lock:
            self._local_only_emits += 1

    def record_routed_cross_emit(self) -> None:
        if not self._enabled:
            return
        with self._lock:
            self._routed_cross_emits += 1

    def record_suppressed_cross_publish(self) -> None:
        if not self._enabled:
            return
        with self._lock:
            self._suppressed_cross_publishes += 1

    def record_callback_delivery(self) -> None:
        if not self._enabled:
            return
        with self._lock:
            self._callback_deliveries += 1

    def record_buffer_pull_delivery(self) -> None:
        if not self._enabled:
            return
        with self._lock:
            self._buffer_pull_deliveries += 1

    def record_error(self, *, code: str, message: str, ts_ms: int | None = None) -> None:
        if not self._enabled:
            return
        ts = int(ts_ms) if ts_ms is not None else int(now_ms())
        with self._lock:
            self._last_error_code = str(code or "").strip()
            self._last_error_message = str(message or "").strip()
            self._last_error_ts_ms = ts
            self._error_events.append(ts)
            cutoff = ts - int(self._window_ms)
            while self._error_events and int(self._error_events[0]) < cutoff:
                self._error_events.popleft()

    async def start(self) -> None:
        if not self._enabled:
            return
        if self._task is not None:
            return
        self._started_ts_ms = int(now_ms())
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self._run_loop(), name=f"monitor:collector:{self._bus.service_id}")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._gpu_sampler.close()

    def _queue_depth(self) -> int:
        return self._bus.data_router.queue_depth()

    def _build_snapshot(self, *, ts_ms: int) -> F8MonitorSnapshot:
        process_percent = self._process_sampler.sample_process_percent()
        system_percent = self._process_sampler.sample_system_percent()
        rss_bytes, vms_bytes = self._memory_sampler.sample()
        gpu = self._gpu_sampler.sample()

        with self._lock:
            self._wait_values.set_window_ms(self._window_ms)
            self._process_values.set_window_ms(self._window_ms)
            self._latency_values.set_window_ms(self._window_ms)
            wait_values = self._wait_values.values(now_ms=int(ts_ms))
            process_values = self._process_values.values(now_ms=int(ts_ms))
            latency_values = self._latency_values.values(now_ms=int(ts_ms))
            error_cutoff = int(ts_ms) - int(self._window_ms)
            while self._error_events and int(self._error_events[0]) < error_cutoff:
                self._error_events.popleft()

            frame = F8MonitorFrame(
                observed=int(self._observed),
                processed=int(self._processed),
                dropped=int(self._dropped),
                localOnlyEmits=int(self._local_only_emits),
                routedCrossEmits=int(self._routed_cross_emits),
                suppressedCrossPublishes=int(self._suppressed_cross_publishes),
                callbackDeliveries=int(self._callback_deliveries),
                bufferPullDeliveries=int(self._buffer_pull_deliveries),
            )
            error = F8MonitorError(
                countWindow=int(len(self._error_events)),
                lastCode=str(self._last_error_code),
                lastMessage=str(self._last_error_message),
                lastTsMs=int(self._last_error_ts_ms) if self._last_error_ts_ms is not None else None,
            )
            ready = bool(self._ready)

        wait_avg = (sum(wait_values) / float(len(wait_values))) if wait_values else None
        process_avg = (sum(process_values) / float(len(process_values))) if process_values else None
        latency_avg = (sum(latency_values) / float(len(latency_values))) if latency_values else None
        timing = F8MonitorTiming(
            processMsAvg=float(process_avg) if process_avg is not None else None,
            processMsP95=_percentile95(process_values),
            waitMsAvg=float(wait_avg) if wait_avg is not None else None,
            waitMsP95=_percentile95(wait_values),
            latencyMsAvg=float(latency_avg) if latency_avg is not None else None,
            latencyMsP95=_percentile95(latency_values),
        )

        snapshot = F8MonitorSnapshot(
            schemaVersion="f8monitor/1",
            serviceId=str(self._bus.service_id),
            serviceClass=str(self._bus._service_class),
            nodeId=str(self._bus.service_id),
            tsMs=int(ts_ms),
            alive=True,
            ready=ready,
            active=bool(self._bus._active),
            uptimeMs=max(0, int(ts_ms - int(self._started_ts_ms))),
            cpu=F8MonitorCpu(
                processPercent=float(process_percent),
                systemPercent=float(system_percent),
            ),
            memory=F8MonitorMemory(
                rssBytes=max(0, int(rss_bytes)),
                vmsBytes=max(0, int(vms_bytes)),
            ),
            gpu=gpu,
            frame=frame,
            timing=timing,
            queue=F8MonitorQueue(depth=self._queue_depth()),
            error=error,
        )
        return validate_as(F8MonitorSnapshot, dump_json(snapshot, mode="json", by_alias=True))

    async def _publish_snapshot(self, snapshot: F8MonitorSnapshot) -> None:
        ts_value = int(snapshot.tsMs)
        payload = {
            "value": dump_json(snapshot, mode="json", by_alias=True),
            "ts": ts_value,
        }
        subject = data_subject(
            str(self._bus.service_id),
            from_node_id=str(self._bus.service_id),
            port_id="monitor",
        )
        await self._bus._transport.publish(subject, encode_obj(payload))

    async def _run_loop(self) -> None:
        interval_s = float(self._interval_ms) / 1000.0
        while True:
            await asyncio.sleep(interval_s)
            ts = int(now_ms())
            try:
                snapshot = self._build_snapshot(ts_ms=ts)
            except Exception as exc:
                self.record_error(
                    code="MONITOR_BUILD_ERROR",
                    message=f"{type(exc).__name__}: {exc}",
                    ts_ms=ts,
                )
                continue
            with self._lock:
                self._latest = snapshot
            try:
                await self._publish_snapshot(snapshot)
            except Exception as exc:
                self.record_error(
                    code="MONITOR_PUBLISH_ERROR",
                    message=f"{type(exc).__name__}: {exc}",
                    ts_ms=ts,
                )

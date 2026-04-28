from __future__ import annotations

from f8pysdk.codec import dump_json, validate_as
from collections import deque
from dataclasses import dataclass
from typing import Any

from f8pysdk.specs import (
    F8MonitorErrorSummary,
    F8MonitorHotspotEntry,
    F8MonitorHotspots,
    F8MonitorReport,
    F8MonitorServiceSummary,
    F8MonitorSnapshot,
)
from f8pysdk.time_utils import now_ms


@dataclass(frozen=True)
class _ServiceOverrides:
    alive: bool | None = None
    ready: bool | None = None
    active: bool | None = None


class MonitorCenter:
    def __init__(self, *, window_ms: int = 30 * 60 * 1000) -> None:
        self._window_ms = max(10_000, int(window_ms))
        self._by_service: dict[str, deque[F8MonitorSnapshot]] = {}
        self._overrides: dict[str, _ServiceOverrides] = {}

    def ingest_snapshot(self, payload: dict[str, Any]) -> F8MonitorSnapshot:
        snapshot = validate_as(F8MonitorSnapshot, payload)
        service_id = str(snapshot.serviceId)
        series = self._by_service.get(service_id)
        if series is None:
            series = deque()
            self._by_service[service_id] = series
        series.append(snapshot)
        self._prune_service(service_id=service_id, now_ts_ms=int(snapshot.tsMs))
        return snapshot

    def update_service_status(
        self,
        *,
        service_id: str,
        alive: bool | None = None,
        ready: bool | None = None,
        active: bool | None = None,
    ) -> None:
        sid = str(service_id or "").strip()
        if not sid:
            return
        prev = self._overrides.get(sid, _ServiceOverrides())
        self._overrides[sid] = _ServiceOverrides(
            alive=prev.alive if alive is None else bool(alive),
            ready=prev.ready if ready is None else bool(ready),
            active=prev.active if active is None else bool(active),
        )

    def build_report(self) -> F8MonitorReport:
        ts = int(now_ms())
        self._prune_all(now_ts_ms=ts)

        service_summaries: list[F8MonitorServiceSummary] = []
        cpu_hot: list[F8MonitorHotspotEntry] = []
        gpu_hot: list[F8MonitorHotspotEntry] = []
        wait_hot: list[F8MonitorHotspotEntry] = []
        error_rows: list[F8MonitorErrorSummary] = []

        for service_id in sorted(self._by_service.keys()):
            series = self._by_service.get(service_id)
            if series is None or not series:
                continue
            latest = series[-1]
            override = self._overrides.get(service_id, _ServiceOverrides())
            alive = bool(latest.alive) if override.alive is None else bool(override.alive)
            ready = bool(latest.ready) if override.ready is None else bool(override.ready)
            active = bool(latest.active) if override.active is None else bool(override.active)

            service_summaries.append(
                F8MonitorServiceSummary(
                    serviceId=str(latest.serviceId),
                    serviceClass=str(latest.serviceClass),
                    alive=alive,
                    ready=ready,
                    active=active,
                    latest=latest,
                )
            )

            process_percent = latest.cpu.processPercent
            if process_percent is not None:
                cpu_hot.append(F8MonitorHotspotEntry(serviceId=str(latest.serviceId), value=float(process_percent)))
            gpu_percent = latest.gpu.utilPercent
            if gpu_percent is not None:
                gpu_hot.append(F8MonitorHotspotEntry(serviceId=str(latest.serviceId), value=float(gpu_percent)))
            wait_p95 = latest.timing.waitMsP95
            if wait_p95 is not None:
                wait_hot.append(F8MonitorHotspotEntry(serviceId=str(latest.serviceId), value=float(wait_p95)))

            if int(latest.error.countWindow) > 0:
                error_rows.append(
                    F8MonitorErrorSummary(
                        serviceId=str(latest.serviceId),
                        count=int(latest.error.countWindow),
                        lastCode=str(latest.error.lastCode),
                        lastMessage=str(latest.error.lastMessage),
                        lastTsMs=int(latest.error.lastTsMs) if latest.error.lastTsMs is not None else None,
                    )
                )

        cpu_hot.sort(key=lambda item: float(item.value), reverse=True)
        gpu_hot.sort(key=lambda item: float(item.value), reverse=True)
        wait_hot.sort(key=lambda item: float(item.value), reverse=True)
        error_rows.sort(key=lambda item: int(item.count), reverse=True)

        return F8MonitorReport(
            schemaVersion="f8monitorReport/1",
            generatedAtMs=ts,
            windowMs=int(self._window_ms),
            services=service_summaries,
            hotspots=F8MonitorHotspots(
                cpuTop=cpu_hot[:10],
                gpuTop=gpu_hot[:10],
                waitTop=wait_hot[:10],
            ),
            errors=error_rows[:50],
        )

    def export_report_json(self) -> dict[str, Any]:
        return dump_json(self.build_report(), mode="json", by_alias=True)

    def latest_snapshot(self, *, service_id: str) -> F8MonitorSnapshot | None:
        sid = str(service_id or "").strip()
        if not sid:
            return None
        series = self._by_service.get(sid)
        if series is None or not series:
            return None
        return series[-1]

    def latest_snapshots(self) -> dict[str, F8MonitorSnapshot]:
        self._prune_all(now_ts_ms=int(now_ms()))
        out: dict[str, F8MonitorSnapshot] = {}
        for service_id, series in self._by_service.items():
            if not series:
                continue
            out[str(service_id)] = series[-1]
        return out

    def drop_service(self, *, service_id: str) -> None:
        sid = str(service_id or "").strip()
        if not sid:
            return
        self._by_service.pop(sid, None)
        self._overrides.pop(sid, None)

    def _prune_service(self, *, service_id: str, now_ts_ms: int) -> None:
        series = self._by_service.get(service_id)
        if series is None:
            return
        cutoff = int(now_ts_ms) - int(self._window_ms)
        while series and int(series[0].tsMs) < cutoff:
            series.popleft()
        if not series:
            self._by_service.pop(service_id, None)
            self._overrides.pop(service_id, None)

    def _prune_all(self, *, now_ts_ms: int) -> None:
        for service_id in list(self._by_service.keys()):
            self._prune_service(service_id=service_id, now_ts_ms=int(now_ts_ms))

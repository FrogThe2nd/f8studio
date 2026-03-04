from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from f8pysdk.generated import F8MonitorSnapshot


@dataclass(frozen=True)
class ServiceMonitorRow:
    service_id: str
    service_class: str
    running: bool
    alive: bool | None
    ready: bool | None
    active: bool | None
    cpu_process_percent: float | None
    memory_rss_bytes: int | None
    gpu_util_percent: float | None
    latency_ms_p95: float | None
    wait_ms_p95: float | None
    error_count_window: int | None
    latest_snapshot: dict[str, Any] | None


def collect_known_service_ids(
    *,
    managed_service_ids: set[str],
    managed_service_classes: Mapping[str, str],
    service_alive_cache: Mapping[str, tuple[bool, float]],
    service_status_cache: Mapping[str, tuple[bool | None, float]],
    latest_snapshot_by_service: Mapping[str, Any],
    process_service_ids_provider: Callable[[], Iterable[str]],
    on_process_ids_error: Callable[[Exception], None] | None = None,
) -> list[str]:
    known_service_ids: set[str] = set()
    known_service_ids.update(managed_service_ids)
    known_service_ids.update(managed_service_classes.keys())
    known_service_ids.update(service_alive_cache.keys())
    known_service_ids.update(service_status_cache.keys())
    known_service_ids.update(latest_snapshot_by_service.keys())
    try:
        known_service_ids.update(process_service_ids_provider())
    except Exception as exc:
        if on_process_ids_error is not None:
            on_process_ids_error(exc)

    out: list[str] = []
    for service_id in sorted(known_service_ids):
        normalized = str(service_id or "").strip()
        if normalized:
            out.append(normalized)
    return out


def build_service_monitor_rows(
    *,
    service_ids: Iterable[str],
    latest_snapshot_by_service: Mapping[str, F8MonitorSnapshot],
    is_service_running: Callable[[str], bool],
    get_cached_service_active: Callable[[str], bool | None],
    managed_service_classes: Mapping[str, str],
    service_alive_cache: Mapping[str, tuple[bool, float]],
) -> list[ServiceMonitorRow]:
    rows: list[ServiceMonitorRow] = []

    for service_id in service_ids:
        latest_snapshot = latest_snapshot_by_service.get(service_id)
        running = bool(is_service_running(service_id))
        alive: bool | None = None
        ready: bool | None = None
        active: bool | None = get_cached_service_active(service_id)
        service_class = str(managed_service_classes.get(service_id, "") or "").strip()
        cpu_process_percent: float | None = None
        memory_rss_bytes: int | None = None
        gpu_util_percent: float | None = None
        latency_ms_p95: float | None = None
        wait_ms_p95: float | None = None
        error_count_window: int | None = None
        latest_payload: dict[str, Any] | None = None

        if latest_snapshot is not None:
            try:
                latest_payload = latest_snapshot.model_dump(mode="json", by_alias=True)
            except (AttributeError, TypeError, ValueError):
                latest_payload = None
            service_class = str(latest_snapshot.serviceClass or "").strip() or service_class
            alive = bool(latest_snapshot.alive)
            ready = bool(latest_snapshot.ready)
            if active is None:
                active = bool(latest_snapshot.active)
            cpu_process_percent = latest_snapshot.cpu.processPercent
            memory_rss_bytes = int(latest_snapshot.memory.rssBytes)
            gpu_util_percent = latest_snapshot.gpu.utilPercent
            latency_ms_p95 = latest_snapshot.timing.latencyMsP95
            wait_ms_p95 = latest_snapshot.timing.waitMsP95
            error_count_window = int(latest_snapshot.error.countWindow)
        else:
            alive_cache = service_alive_cache.get(service_id)
            if alive_cache is not None:
                alive = bool(alive_cache[0])

        rows.append(
            ServiceMonitorRow(
                service_id=str(service_id),
                service_class=service_class,
                running=running,
                alive=alive,
                ready=ready,
                active=active,
                cpu_process_percent=cpu_process_percent,
                memory_rss_bytes=memory_rss_bytes,
                gpu_util_percent=gpu_util_percent,
                latency_ms_p95=latency_ms_p95,
                wait_ms_p95=wait_ms_p95,
                error_count_window=error_count_window,
                latest_snapshot=latest_payload,
            )
        )

    return rows

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class ServiceStatusStore:
    service_status_cache: dict[str, tuple[bool | None, float]] = field(default_factory=dict)
    service_alive_cache: dict[str, tuple[bool, float]] = field(default_factory=dict)
    service_status_inflight: set[str] = field(default_factory=set)
    service_status_req_s: dict[str, float] = field(default_factory=dict)

    def cache_service_active(self, service_id: str, active: bool | None) -> None:
        sid = str(service_id or "").strip()
        if not sid:
            return
        self.service_status_cache[sid] = (active, time.monotonic())

    def cache_service_alive(self, service_id: str, alive: bool) -> None:
        sid = str(service_id or "").strip()
        if not sid:
            return
        self.service_alive_cache[sid] = (bool(alive), time.monotonic())

    def cached_service_active(self, service_id: str) -> bool | None:
        sid = str(service_id or "").strip()
        if not sid:
            return None
        value = self.service_status_cache.get(sid)
        if value is None:
            return None
        return value[0]

    def begin_request(self, service_id: str, *, min_interval_s: float = 0.25) -> bool:
        sid = str(service_id or "").strip()
        if not sid:
            return False
        now = time.monotonic()
        last = float(self.service_status_req_s.get(sid, 0.0))
        if (now - last) < float(min_interval_s):
            return False
        if sid in self.service_status_inflight:
            return False
        self.service_status_inflight.add(sid)
        self.service_status_req_s[sid] = now
        return True

    def end_request(self, service_id: str) -> None:
        sid = str(service_id or "").strip()
        if not sid:
            return
        self.service_status_inflight.discard(sid)

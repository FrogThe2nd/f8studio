from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StoredStateValue:
    service_id: str
    node_id: str
    field: str
    value: Any
    ts_ms: int


class RuntimeObservationStore:
    def __init__(self, *, max_port_samples: int = 256) -> None:
        self._state_values: dict[tuple[str, str, str], StoredStateValue] = {}
        self._port_samples: dict[tuple[str, str, str], deque[dict[str, Any]]] = {}
        self._max_port_samples = max(1, int(max_port_samples))
        self._condition = threading.Condition()

    def put_state(self, *, service_id: str, node_id: str, field: str, value: Any, ts_ms: int) -> None:
        sid = str(service_id or "").strip()
        nid = str(node_id or "").strip()
        name = str(field or "").strip()
        if not sid or not nid or not name:
            return
        stored = StoredStateValue(
            service_id=sid,
            node_id=nid,
            field=name,
            value=value,
            ts_ms=int(ts_ms),
        )
        with self._condition:
            self._state_values[(sid, nid, name)] = stored
            self._condition.notify_all()

    def get_state(self, *, service_id: str, node_id: str, field: str) -> StoredStateValue | None:
        key = (str(service_id).strip(), str(node_id).strip(), str(field).strip())
        with self._condition:
            return self._state_values.get(key)

    def wait_state(
        self,
        *,
        service_id: str,
        node_id: str,
        field: str,
        after_ts_ms: int | None = None,
        timeout_s: float = 1.0,
    ) -> StoredStateValue | None:
        key = (str(service_id).strip(), str(node_id).strip(), str(field).strip())
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        with self._condition:
            while True:
                value = self._state_values.get(key)
                if value is not None and (after_ts_ms is None or int(value.ts_ms) > int(after_ts_ms)):
                    return value
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return value
                self._condition.wait(timeout=remaining)

    def put_port_sample(self, *, service_id: str, node_id: str, port: str, sample: dict[str, Any]) -> None:
        key = (str(service_id).strip(), str(node_id).strip(), str(port).strip())
        if not all(key):
            return
        with self._condition:
            samples = self._port_samples.get(key)
            if samples is None:
                samples = deque(maxlen=self._max_port_samples)
                self._port_samples[key] = samples
            payload = dict(sample)
            payload.setdefault("observedAtMs", int(time.time() * 1000))
            samples.append(payload)
            self._condition.notify_all()

    def get_port_samples(self, *, service_id: str, node_id: str, port: str, limit: int = 1) -> list[dict[str, Any]]:
        key = (str(service_id).strip(), str(node_id).strip(), str(port).strip())
        capped = max(1, min(int(limit), 100))
        with self._condition:
            samples = list(self._port_samples.get(key) or ())
            return [dict(item) for item in samples[-capped:]]

    def wait_port_samples(
        self,
        *,
        service_id: str,
        node_id: str,
        port: str,
        min_count: int = 1,
        limit: int = 1,
        after_observed_at_ms: int | None = None,
        timeout_s: float = 1.0,
    ) -> list[dict[str, Any]]:
        key = (str(service_id).strip(), str(node_id).strip(), str(port).strip())
        target_count = max(1, min(int(min_count), 100))
        capped = max(1, min(int(limit), 100))
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        with self._condition:
            while True:
                samples = list(self._port_samples.get(key) or ())
                if after_observed_at_ms is not None:
                    samples = [
                        item
                        for item in samples
                        if int(item.get("observedAtMs") or 0) > int(after_observed_at_ms)
                    ]
                if len(samples) >= target_count:
                    return [dict(item) for item in samples[-capped:]]
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return [dict(item) for item in samples[-capped:]]
                self._condition.wait(timeout=remaining)

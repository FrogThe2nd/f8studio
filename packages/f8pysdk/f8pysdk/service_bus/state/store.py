from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...generated import F8StateAccess
from ...f8_naming import ensure_token
from ...state import StateRead
from ...codec import decode_obj
from ...zenoh_naming import zenoh_state_key
from ..internal.cache import CappedOrderedDict
from .helpers import coerce_inbound_ts_ms, extract_ts_field

if TYPE_CHECKING:
    from ..runtime import ServiceBus


class StateStore:
    def __init__(self, bus: "ServiceBus", *, cache_max_entries: int) -> None:
        self._bus = bus
        self._cache: CappedOrderedDict[tuple[str, str], tuple[Any, int]] = CappedOrderedDict(
            max_entries=max(0, int(cache_max_entries))
        )
        self._access_by_node_field: dict[tuple[str, str], F8StateAccess] = {}

    @property
    def cache(self) -> CappedOrderedDict[tuple[str, str], tuple[Any, int]]:
        return self._cache

    @property
    def access_by_node_field(self) -> dict[tuple[str, str], F8StateAccess]:
        return self._access_by_node_field

    def set_access_map(self, access_by_node_field: dict[tuple[str, str], F8StateAccess]) -> None:
        self._access_by_node_field = dict(access_by_node_field)

    def clear_access_map(self) -> None:
        self._access_by_node_field.clear()

    def access_for(self, *, node_id: str, field: str) -> F8StateAccess | None:
        return self._access_by_node_field.get((str(node_id), str(field)))

    def cache_entry(self, *, node_id: str, field: str) -> tuple[Any, int] | None:
        return self._cache.get((str(node_id), str(field)))

    def cache_value(self, *, node_id: str, field: str, value: Any, ts_ms: int) -> None:
        self._cache[(str(node_id), str(field))] = (value, int(ts_ms))

    def clear_cache(self) -> None:
        self._cache.clear()

    async def read_state(self, node_id: str, field: str) -> StateRead:
        node_id_s = ensure_token(node_id, label="node_id")
        field_s = str(field)

        cached = self.cache_entry(node_id=node_id_s, field=field_s)
        if cached is not None:
            return StateRead(found=True, value=cached[0], ts_ms=cached[1])

        key = zenoh_state_key(self._bus.service_id, node_id=node_id_s, field=field_s)
        raw = await self._bus._transport.retained_get(key)
        if not raw:
            if self._bus._debug_state:
                print(
                    "state_debug[%s] get_state miss node=%s field=%s"
                    % (self._bus.service_id, node_id_s, field_s)
                )
            return StateRead(found=False, value=None, ts_ms=None)

        try:
            payload = decode_obj(raw)
        except ValueError:
            self.cache_value(node_id=node_id_s, field=field_s, value=raw, ts_ms=0)
            return StateRead(found=True, value=raw, ts_ms=0)

        if isinstance(payload, dict) and "value" in payload:
            value = payload.get("value")
            ts_ms_value = coerce_inbound_ts_ms(extract_ts_field(payload), default=0)
            self.cache_value(node_id=node_id_s, field=field_s, value=value, ts_ms=ts_ms_value)
            if self._bus._debug_state:
                print(
                    "state_debug[%s] get_state kv node=%s field=%s ts=%s"
                    % (self._bus.service_id, node_id_s, field_s, str(ts_ms_value))
                )
            return StateRead(found=True, value=value, ts_ms=ts_ms_value)

        self.cache_value(node_id=node_id_s, field=field_s, value=payload, ts_ms=0)
        return StateRead(found=True, value=payload, ts_ms=0)

    def get_cached_value(self, node_id: str, field: str, default: Any = None) -> Any:
        node_id_s = ensure_token(node_id, label="node_id")
        field_s = str(field)
        cached = self.cache_entry(node_id=node_id_s, field=field_s)
        if cached is None:
            return default
        return cached[0]


__all__ = ["StateStore"]

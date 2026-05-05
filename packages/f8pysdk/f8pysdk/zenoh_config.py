from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)


def apply_zenoh_shared_memory_config(
    config: Any,
    *,
    zenoh_module: Any,
    shm_pool_bytes: int,
    log_context: str,
) -> None:
    """
    Enable Zenoh SHM plus transport-optimization pools for large payloads.

    Zenoh 1.9 exposes the pool under
    `transport/shared_memory/transport_optimization/pool_size`. The legacy
    `transport/shared_memory/pool_size` write is kept as a best-effort fallback
    for older configs.
    """
    config.insert_json5("transport/shared_memory/enabled", "true")
    _insert_optional_json5(config, zenoh_module, "transport/shared_memory/mode", json.dumps("init"), log_context)
    _insert_optional_json5(
        config,
        zenoh_module,
        "transport/shared_memory/transport_optimization/enabled",
        "true",
        log_context,
    )
    pool_bytes = max(0, int(shm_pool_bytes))
    if pool_bytes <= 0:
        return
    pool_json = json.dumps(pool_bytes)
    _insert_optional_json5(
        config,
        zenoh_module,
        "transport/shared_memory/transport_optimization/pool_size",
        pool_json,
        log_context,
    )
    _insert_optional_json5(
        config,
        zenoh_module,
        "transport/shared_memory/pool_size",
        pool_json,
        log_context,
    )


def _insert_optional_json5(config: Any, zenoh_module: Any, key: str, value: str, log_context: str) -> None:
    try:
        config.insert_json5(str(key), str(value))
    except zenoh_module.ZError as exc:
        log.debug("zenoh config key unavailable context=%s key=%s", log_context, key, exc_info=exc)


__all__ = ["apply_zenoh_shared_memory_config"]

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ...codec import decode_obj
from ...runtime_transport import RuntimeTransport
from ...time_utils import now_ms
from ...f8_naming import ensure_token


log = logging.getLogger(__name__)


async def wait_service_ready(
    tr: RuntimeTransport,
    *,
    timeout_s: float = 6.0,
    min_ts_ms: int | None = None,
    max_age_ms: int | None = None,
    service_id: str | None = None,
) -> None:
    """
    Wait until a service announces readiness.

    Readiness is published as a retained status sample at
    `f8/svc/{service_id}/status/ready`. This function waits with a retained
    watch after an initial read.
    """
    min_ts = int(min_ts_ms) if min_ts_ms is not None else None
    max_age = int(max_age_ms) if max_age_ms is not None else None

    def _accept(payload: Any) -> bool:
        if not isinstance(payload, dict) or payload.get("ready") is not True:
            return False
        try:
            ts = int(payload.get("ts") or 0)
        except (TypeError, ValueError):
            ts = 0
        if min_ts is not None and ts < min_ts:
            return False
        if max_age is not None:
            age = int(now_ms()) - ts
            if ts <= 0 or age > max_age:
                return False
        return True

    if service_id is None:
        raise ValueError("service_id is required")
    service_id_s = ensure_token(service_id, label="service_id")
    key = f"f8/svc/{service_id_s}/status/ready"
    try:
        raw = await tr.retained_get(key)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        log.debug("initial ready retained_get failed key=%s", key, exc_info=exc)
        raw = None
    if raw:
        try:
            payload = decode_obj(raw)
        except ValueError:
            payload = {}
        if _accept(payload):
            return

    loop = asyncio.get_running_loop()
    fut: asyncio.Future[None] = loop.create_future()

    async def _on_ready(_key: str, value: bytes) -> None:
        if fut.done():
            return
        try:
            payload = decode_obj(value or b"")
        except ValueError:
            payload = {}
        if _accept(payload):
            fut.set_result(None)

    watch = None
    try:
        watch = await tr.retained_watch(key, cb=_on_ready, with_initial=True)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        log.debug("ready retained_watch failed key=%s", key, exc_info=exc)
        watch = None

    try:
        try:
            raw2 = await tr.retained_get(key)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.debug("second ready retained_get failed key=%s", key, exc_info=exc)
            raw2 = None
        if raw2:
            try:
                payload2: Any = decode_obj(raw2)
            except ValueError:
                payload2 = {}
            if _accept(payload2):
                return
        await asyncio.wait_for(fut, timeout=float(timeout_s))
    finally:
        if watch is not None:
            if isinstance(watch, tuple):
                watcher, task = watch
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                    log.error("ready watch task stop failed key=%s", key, exc_info=exc)
                try:
                    await watcher.stop()
                except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                    log.error("ready watcher stop failed key=%s", key, exc_info=exc)
            else:
                try:
                    await watch.stop()
                except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                    log.error("ready watch stop failed key=%s", key, exc_info=exc)


__all__ = ["wait_service_ready"]

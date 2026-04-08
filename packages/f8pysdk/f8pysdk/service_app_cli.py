from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, replace

from .service_runtime import ServiceRuntimeConfig


def env_or(default: str, key: str) -> str:
    value = os.environ.get(key)
    return value.strip() if value and value.strip() else default


def parse_bool_arg(value: str) -> bool:
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"invalid boolean value: {value!r}")


def env_bool(default: bool, key: str) -> bool:
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return bool(default)
    try:
        return parse_bool_arg(raw)
    except argparse.ArgumentTypeError:
        return bool(default)


def env_int(default: int, key: str) -> int:
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except ValueError:
        return int(default)


@dataclass(frozen=True)
class MonitorRuntimeOverrides:
    enabled: bool = True
    interval_ms: int = 1000
    window_ms: int = 30000
    gpu_enabled: bool = True


def apply_monitor_overrides(
    config: ServiceRuntimeConfig, *, overrides: MonitorRuntimeOverrides | None
) -> ServiceRuntimeConfig:
    if overrides is None:
        return config
    new_bus = replace(
        config.bus,
        monitor_enabled=bool(overrides.enabled),
        monitor_interval_ms=max(200, int(overrides.interval_ms)),
        monitor_window_ms=max(1000, int(overrides.window_ms)),
        monitor_gpu_enabled=bool(overrides.gpu_enabled),
    )
    return replace(config, bus=new_bus)


def build_monitor_overrides(
    *,
    enabled: bool,
    interval_ms: int,
    window_ms: int,
    gpu_enabled: bool,
) -> MonitorRuntimeOverrides:
    return MonitorRuntimeOverrides(
        enabled=bool(enabled),
        interval_ms=max(200, int(interval_ms)),
        window_ms=max(1000, int(window_ms)),
        gpu_enabled=bool(gpu_enabled),
    )


def print_describe_payload(payload: dict[str, object]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    try:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except AttributeError:
            pass
        print(text)
    except UnicodeEncodeError:
        try:
            sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))
            sys.stdout.buffer.write(b"\n")
            sys.stdout.flush()
        except Exception:
            print(json.dumps(payload, ensure_ascii=True, indent=1))

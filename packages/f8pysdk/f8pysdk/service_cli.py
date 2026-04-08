from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from typing import Any

from .msgspec_codec import dump_json
from .monitor_schema import validate_describe_monitor_contract
from .registry import create_runtime_node_registry, shared_runtime_node_registry, RuntimeNodeRegistry
from .service_runtime import ServiceRuntime, ServiceRuntimeConfig


log = logging.getLogger(__name__)


def _env_or(default: str, key: str) -> str:
    v = os.environ.get(key)
    return v.strip() if v and v.strip() else default


def _parse_bool_arg(value: str) -> bool:
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"invalid boolean value: {value!r}")


def _env_bool(default: bool, key: str) -> bool:
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return bool(default)
    try:
        return _parse_bool_arg(raw)
    except argparse.ArgumentTypeError:
        return bool(default)


def _env_int(default: int, key: str) -> int:
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


class ServiceCliTemplate(ABC):
    """
    Opinionated CLI entrypoint template for a service process.

    Goal: make each service entrypoint a small "fill the blanks" class:
    - register runtime node specs
    - attach service-specific runtime wiring (executors, binders, listeners)
    - run forever with a consistent CLI (`--describe`, `--service-id`, `--nats-url`)
    """

    @property
    @abstractmethod
    def service_class(self) -> str:
        raise NotImplementedError

    # ---- registry/app construction ------------------------------------
    def build_registry(self) -> RuntimeNodeRegistry:
        return create_runtime_node_registry()

    @staticmethod
    def build_shared_registry() -> RuntimeNodeRegistry:
        """
        Explicit opt-in for process-global registry sharing.

        Use this only when shared registration state is required across multiple
        callers in the same process.
        """
        return shared_runtime_node_registry()

    @abstractmethod
    def register_specs(self, registry: RuntimeNodeRegistry) -> None:
        raise NotImplementedError

    def build_runtime_config(self, *, service_id: str, nats_url: str) -> ServiceRuntimeConfig:
        return ServiceRuntimeConfig.from_values(service_id=service_id, service_class=self.service_class, nats_url=nats_url)

    @staticmethod
    def _apply_monitor_overrides(
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

    def build_runtime(
        self,
        *,
        service_id: str,
        nats_url: str,
        registry: RuntimeNodeRegistry,
        monitor_overrides: MonitorRuntimeOverrides | None = None,
    ) -> ServiceRuntime:
        runtime_cfg = self.build_runtime_config(service_id=service_id, nats_url=nats_url)
        runtime_cfg = self._apply_monitor_overrides(runtime_cfg, overrides=monitor_overrides)
        return ServiceRuntime(runtime_cfg, registry=registry)

    # ---- lifecycle hooks ----------------------------------------------
    async def setup(self, runtime: ServiceRuntime) -> None:
        """
        Hook point for wiring service-specific runtime pieces.
        Called before `runtime.start()`.
        """

    async def teardown(self, runtime: ServiceRuntime) -> None:
        """
        Hook point for stopping service-specific runtime pieces.
        Called before `runtime.stop()` (best-effort).
        """

    # ---- running -------------------------------------------------------
    async def run_forever(
        self,
        *,
        service_id: str,
        nats_url: str,
        monitor_overrides: MonitorRuntimeOverrides | None = None,
    ) -> None:
        registry = self.build_registry()
        self.register_specs(registry)

        runtime = self.build_runtime(
            service_id=service_id,
            nats_url=nats_url,
            registry=registry,
            monitor_overrides=monitor_overrides,
        )
        await self.setup(runtime)
        await runtime.start()

        try:
            await runtime.bus.wait_terminate()
        finally:
            try:
                await self.teardown(runtime)
            except Exception as exc:
                log.error("service teardown failed service_class=%s", self.service_class, exc_info=exc)
            await runtime.stop()

    def describe_json(self) -> dict[str, Any]:
        registry = self.build_registry()
        self.register_specs(registry)
        payload = dump_json(registry.describe(self.service_class), mode="json")
        validate_describe_monitor_contract(payload)
        return payload

    # ---- CLI -----------------------------------------------------------
    def cli(self, argv: list[str] | None = None, *, program_name: str | None = None) -> int:
        parser = argparse.ArgumentParser(description=program_name or self.service_class)
        parser.add_argument("--describe", action="store_true", help="Output the service description in JSON format")
        parser.add_argument("--service-id", default=_env_or("", "F8_SERVICE_ID"), help="Service instance id (required)")
        parser.add_argument("--nats-url", default=_env_or("nats://127.0.0.1:4222", "F8_NATS_URL"), help="NATS server URL")
        parser.add_argument(
            "--monitor-enabled",
            default=_env_bool(True, "F8_MONITOR_ENABLED"),
            type=_parse_bool_arg,
            help="Enable monitor emission (env: F8_MONITOR_ENABLED, default: true).",
        )
        parser.add_argument(
            "--monitor-interval-ms",
            default=_env_int(1000, "F8_MONITOR_INTERVAL_MS"),
            type=int,
            help="Monitor sampling interval in milliseconds (env: F8_MONITOR_INTERVAL_MS, default: 1000).",
        )
        parser.add_argument(
            "--monitor-window-ms",
            default=_env_int(30000, "F8_MONITOR_WINDOW_MS"),
            type=int,
            help="Window size for rolling monitor stats in milliseconds (env: F8_MONITOR_WINDOW_MS, default: 30000).",
        )
        parser.add_argument(
            "--monitor-gpu-enabled",
            default=_env_bool(True, "F8_MONITOR_GPU_ENABLED"),
            type=_parse_bool_arg,
            help="Enable GPU/VRAM sampling (env: F8_MONITOR_GPU_ENABLED, default: true).",
        )
        args = parser.parse_args(argv)

        if args.describe:
            payload = json.dumps(self.describe_json(), ensure_ascii=False, indent=1)
            try:
                # On Windows, the console encoding may be cp1252 and crash on unicode.
                try:
                    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
                except AttributeError:
                    pass
                print(payload)
            except UnicodeEncodeError:
                try:
                    sys.stdout.buffer.write(payload.encode("utf-8", errors="replace"))
                    sys.stdout.buffer.write(b"\n")
                    sys.stdout.flush()
                except Exception:
                    print(json.dumps(self.describe_json(), ensure_ascii=True, indent=1))
            return 0

        service_id = str(args.service_id or "").strip()
        if not service_id:
            raise SystemExit("Missing --service-id (or env F8_SERVICE_ID)")

        monitor_overrides = MonitorRuntimeOverrides(
            enabled=bool(args.monitor_enabled),
            interval_ms=max(200, int(args.monitor_interval_ms)),
            window_ms=max(1000, int(args.monitor_window_ms)),
            gpu_enabled=bool(args.monitor_gpu_enabled),
        )
        asyncio.run(
            self.run_forever(
                service_id=service_id,
                nats_url=str(args.nats_url).strip(),
                monitor_overrides=monitor_overrides,
            )
        )
        return 0

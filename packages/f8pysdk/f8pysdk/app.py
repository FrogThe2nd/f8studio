from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any

from .bus import ServiceBusConfig
from .codec import dump_json
from .monitoring import validate_describe_monitor_contract
from .registry import Registry, RuntimeNodeRegistry, shared_runtime_node_registry
from .runtime import ServiceRuntime, ServiceRuntimeConfig

log = logging.getLogger(__name__)


RuntimeLifecycleHook = Callable[[ServiceRuntime], Awaitable[None] | None]
RuntimeConfigFactory = Callable[[str, str], ServiceRuntimeConfig]


@dataclass(frozen=True)
class MonitorRuntimeOverrides:
    enabled: bool = True
    interval_ms: int = 1000
    window_ms: int = 30000
    gpu_enabled: bool = True


@dataclass(frozen=True)
class ServiceAppDefaults:
    bus: ServiceBusConfig = field(default_factory=ServiceBusConfig)
    registry_modules: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        modules = tuple(str(module).strip() for module in self.registry_modules if str(module).strip())
        object.__setattr__(self, "bus", self.bus.normalized())
        object.__setattr__(self, "registry_modules", modules)

    def build_bus_config(
        self,
        *,
        service_id: str,
        service_class: str,
        nats_url: str | None = None,
    ) -> ServiceBusConfig:
        return self.bus.for_service(
            service_id=service_id,
            service_class=service_class,
            nats_url=nats_url,
        )


def _env_or(default: str, key: str) -> str:
    value = os.environ.get(key)
    return value.strip() if value and value.strip() else default


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


def _print_describe_payload(payload: dict[str, object]) -> None:
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


def _build_monitor_overrides(
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
    ).normalized()
    return replace(config, bus=new_bus)


async def _run_runtime_hook(hook: RuntimeLifecycleHook | None, runtime: ServiceRuntime) -> None:
    if hook is None:
        return
    result = hook(runtime)
    if inspect.isawaitable(result):
        await result


def _wrap_registry(registry: Registry | RuntimeNodeRegistry | None) -> Registry:
    if registry is None:
        return Registry()
    if isinstance(registry, Registry):
        return registry
    if isinstance(registry, RuntimeNodeRegistry):
        return Registry.wrap(registry)
    raise TypeError(f"registry must be Registry or RuntimeNodeRegistry, got {type(registry).__name__}")


class ServiceApp:
    """
    User-facing service entrypoint owner.

    `ServiceApp` packages registry ownership, describe output, runtime creation,
    and CLI entry wiring behind one explicit object so service authors do not need
    to manually compose `RuntimeNodeRegistry` and `ServiceRuntime`.
    """

    def __init__(
        self,
        *,
        service_class: str,
        registry: Registry | RuntimeNodeRegistry | None = None,
        defaults: ServiceAppDefaults | None = None,
        setup: RuntimeLifecycleHook | None = None,
        teardown: RuntimeLifecycleHook | None = None,
        runtime_config_factory: RuntimeConfigFactory | None = None,
    ) -> None:
        service_class_text = str(service_class or "").strip()
        if not service_class_text:
            raise ValueError("service_class must be non-empty")
        self._service_class = service_class_text
        self._registry = _wrap_registry(registry)
        self._defaults = defaults if defaults is not None else ServiceAppDefaults()
        self._setup = setup
        self._teardown = teardown
        self._runtime_config_factory = runtime_config_factory

    @property
    def service_class(self) -> str:
        return self._service_class

    @property
    def registry(self) -> Registry:
        return self._registry

    @property
    def runtime_registry(self) -> RuntimeNodeRegistry:
        return self._registry.runtime_registry

    @staticmethod
    def build_shared_registry() -> Registry:
        return Registry.wrap(shared_runtime_node_registry())

    def build_runtime_config(self, *, service_id: str, nats_url: str | None = None) -> ServiceRuntimeConfig:
        resolved_nats_url = str(nats_url or self._defaults.bus.nats_url).strip()
        if self._runtime_config_factory is not None:
            return self._runtime_config_factory(str(service_id), resolved_nats_url)
        defaults = self._defaults
        bus = defaults.build_bus_config(
            service_id=str(service_id),
            service_class=self.service_class,
            nats_url=resolved_nats_url,
        )
        return ServiceRuntimeConfig(bus=bus, registry_modules=defaults.registry_modules)

    def build_runtime(
        self,
        *,
        service_id: str,
        nats_url: str | None = None,
        monitor_overrides: MonitorRuntimeOverrides | None = None,
    ) -> ServiceRuntime:
        runtime_cfg = self.build_runtime_config(service_id=service_id, nats_url=nats_url)
        runtime_cfg = _apply_monitor_overrides(runtime_cfg, overrides=monitor_overrides)
        return ServiceRuntime(runtime_cfg, registry=self.runtime_registry)

    def describe_json(self) -> dict[str, Any]:
        payload = dump_json(self.runtime_registry.describe(self.service_class), mode="json")
        validate_describe_monitor_contract(payload)
        return payload

    async def run_async(
        self,
        *,
        service_id: str,
        nats_url: str | None = None,
        monitor_overrides: MonitorRuntimeOverrides | None = None,
    ) -> None:
        runtime = self.build_runtime(
            service_id=service_id,
            nats_url=nats_url,
            monitor_overrides=monitor_overrides,
        )
        await _run_runtime_hook(self._setup, runtime)
        await runtime.start()
        try:
            await runtime.bus.wait_terminate()
        finally:
            try:
                await _run_runtime_hook(self._teardown, runtime)
            except Exception as exc:
                log.error("service teardown failed service_class=%s", self.service_class, exc_info=exc)
            await runtime.stop()

    async def run_forever(
        self,
        *,
        service_id: str,
        nats_url: str | None = None,
        monitor_overrides: MonitorRuntimeOverrides | None = None,
    ) -> None:
        await self.run_async(
            service_id=service_id,
            nats_url=nats_url,
            monitor_overrides=monitor_overrides,
        )

    def run(
        self,
        *,
        service_id: str,
        nats_url: str | None = None,
        monitor_overrides: MonitorRuntimeOverrides | None = None,
    ) -> None:
        asyncio.run(
            self.run_async(
                service_id=service_id,
                nats_url=nats_url,
                monitor_overrides=monitor_overrides,
            )
        )

    def cli(self, argv: list[str] | None = None, *, program_name: str | None = None) -> int:
        parser = argparse.ArgumentParser(description=program_name or self.service_class)
        parser.add_argument("--describe", action="store_true", help="Output the service description in JSON format")
        parser.add_argument("--service-id", default=_env_or("", "F8_SERVICE_ID"), help="Service instance id (required)")
        parser.add_argument("--nats-url", default=_env_or(self._defaults.bus.nats_url, "F8_NATS_URL"), help="NATS server URL")
        parser.add_argument(
            "--monitor-enabled",
            default=_env_bool(self._defaults.bus.monitor_enabled, "F8_MONITOR_ENABLED"),
            type=_parse_bool_arg,
            help="Enable monitor emission (env: F8_MONITOR_ENABLED, default: true).",
        )
        parser.add_argument(
            "--monitor-interval-ms",
            default=_env_int(self._defaults.bus.monitor_interval_ms, "F8_MONITOR_INTERVAL_MS"),
            type=int,
            help="Monitor sampling interval in milliseconds (env: F8_MONITOR_INTERVAL_MS, default: 1000).",
        )
        parser.add_argument(
            "--monitor-window-ms",
            default=_env_int(self._defaults.bus.monitor_window_ms, "F8_MONITOR_WINDOW_MS"),
            type=int,
            help="Window size for rolling monitor stats in milliseconds (env: F8_MONITOR_WINDOW_MS, default: 30000).",
        )
        parser.add_argument(
            "--monitor-gpu-enabled",
            default=_env_bool(self._defaults.bus.monitor_gpu_enabled, "F8_MONITOR_GPU_ENABLED"),
            type=_parse_bool_arg,
            help="Enable GPU/VRAM sampling (env: F8_MONITOR_GPU_ENABLED, default: true).",
        )
        args = parser.parse_args(argv)

        if args.describe:
            _print_describe_payload(self.describe_json())
            return 0

        service_id = str(args.service_id or "").strip()
        if not service_id:
            raise SystemExit("Missing --service-id (or env F8_SERVICE_ID)")

        monitor_overrides = _build_monitor_overrides(
            enabled=bool(args.monitor_enabled),
            interval_ms=int(args.monitor_interval_ms),
            window_ms=int(args.monitor_window_ms),
            gpu_enabled=bool(args.monitor_gpu_enabled),
        )
        self.run(
            service_id=service_id,
            nats_url=str(args.nats_url).strip(),
            monitor_overrides=monitor_overrides,
        )
        return 0

__all__ = [
    "MonitorRuntimeOverrides",
    "ServiceApp",
    "ServiceAppDefaults",
]

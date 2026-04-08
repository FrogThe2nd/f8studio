from __future__ import annotations

import argparse
import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from nats.js.api import StorageType  # type: ignore[import-not-found]

from .data import CrossPublishPolicy, DataDeliveryMode
from .msgspec_codec import dump_json
from .monitor_schema import validate_describe_monitor_contract
from .registry import Registry, shared_runtime_node_registry, RuntimeNodeRegistry
from .service_app_cli import (
    apply_monitor_overrides,
    build_monitor_overrides,
    env_bool,
    env_int,
    env_or,
    MonitorRuntimeOverrides,
    parse_bool_arg,
    print_describe_payload,
)
from .service_runtime import ServiceRuntime, ServiceRuntimeConfig


log = logging.getLogger(__name__)


RuntimeLifecycleHook = Callable[[ServiceRuntime], Awaitable[None] | None]
RuntimeConfigFactory = Callable[[str, str], ServiceRuntimeConfig]


@dataclass(frozen=True)
class ServiceAppDefaults:
    service_name: str | None = None
    nats_url: str = "nats://127.0.0.1:4222"
    cross_publish_policy: CrossPublishPolicy = "routed"
    kv_storage: StorageType = StorageType.MEMORY
    delete_bucket_on_start: bool = False
    delete_bucket_on_stop: bool = False
    data_delivery: DataDeliveryMode = "callback"
    state_sync_concurrency: int = 8
    state_cache_max_entries: int = 8192
    data_input_max_buffers: int = 4096
    data_input_default_queue_size: int = 256
    monitor_enabled: bool = True
    monitor_interval_ms: int = 1000
    monitor_window_ms: int = 30000
    monitor_gpu_enabled: bool = True
    registry_modules: tuple[str, ...] = ()
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
        resolved_nats_url = str(nats_url or self._defaults.nats_url).strip()
        if self._runtime_config_factory is not None:
            return self._runtime_config_factory(str(service_id), resolved_nats_url)
        defaults = self._defaults
        return ServiceRuntimeConfig.from_values(
            service_id=str(service_id),
            service_class=self.service_class,
            service_name=defaults.service_name,
            nats_url=resolved_nats_url,
            cross_publish_policy=defaults.cross_publish_policy,
            kv_storage=defaults.kv_storage,
            delete_bucket_on_start=defaults.delete_bucket_on_start,
            delete_bucket_on_stop=defaults.delete_bucket_on_stop,
            data_delivery=defaults.data_delivery,
            state_sync_concurrency=defaults.state_sync_concurrency,
            state_cache_max_entries=defaults.state_cache_max_entries,
            data_input_max_buffers=defaults.data_input_max_buffers,
            data_input_default_queue_size=defaults.data_input_default_queue_size,
            monitor_enabled=defaults.monitor_enabled,
            monitor_interval_ms=defaults.monitor_interval_ms,
            monitor_window_ms=defaults.monitor_window_ms,
            monitor_gpu_enabled=defaults.monitor_gpu_enabled,
            registry_modules=defaults.registry_modules,
        )

    def build_runtime(
        self,
        *,
        service_id: str,
        nats_url: str | None = None,
        monitor_overrides: MonitorRuntimeOverrides | None = None,
    ) -> ServiceRuntime:
        runtime_cfg = self.build_runtime_config(service_id=service_id, nats_url=nats_url)
        runtime_cfg = apply_monitor_overrides(runtime_cfg, overrides=monitor_overrides)
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
        parser.add_argument("--service-id", default=env_or("", "F8_SERVICE_ID"), help="Service instance id (required)")
        parser.add_argument("--nats-url", default=env_or(self._defaults.nats_url, "F8_NATS_URL"), help="NATS server URL")
        parser.add_argument(
            "--monitor-enabled",
            default=env_bool(self._defaults.monitor_enabled, "F8_MONITOR_ENABLED"),
            type=parse_bool_arg,
            help="Enable monitor emission (env: F8_MONITOR_ENABLED, default: true).",
        )
        parser.add_argument(
            "--monitor-interval-ms",
            default=env_int(self._defaults.monitor_interval_ms, "F8_MONITOR_INTERVAL_MS"),
            type=int,
            help="Monitor sampling interval in milliseconds (env: F8_MONITOR_INTERVAL_MS, default: 1000).",
        )
        parser.add_argument(
            "--monitor-window-ms",
            default=env_int(self._defaults.monitor_window_ms, "F8_MONITOR_WINDOW_MS"),
            type=int,
            help="Window size for rolling monitor stats in milliseconds (env: F8_MONITOR_WINDOW_MS, default: 30000).",
        )
        parser.add_argument(
            "--monitor-gpu-enabled",
            default=env_bool(self._defaults.monitor_gpu_enabled, "F8_MONITOR_GPU_ENABLED"),
            type=parse_bool_arg,
            help="Enable GPU/VRAM sampling (env: F8_MONITOR_GPU_ENABLED, default: true).",
        )
        args = parser.parse_args(argv)

        if args.describe:
            print_describe_payload(self.describe_json())
            return 0

        service_id = str(args.service_id or "").strip()
        if not service_id:
            raise SystemExit("Missing --service-id (or env F8_SERVICE_ID)")

        monitor_overrides = build_monitor_overrides(
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

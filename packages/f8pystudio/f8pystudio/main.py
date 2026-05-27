from __future__ import annotations

import argparse
import os
import sys
from typing import cast

from f8pysdk.bus import BusBackend
from f8pysdk.service_bus.config import DEFAULT_ZENOH_SHM_POOL_BYTES
from f8pysdk.service_runtime_tools.inventory.policy import (
    DISABLED_SERVICE_CLASSES_ENV,
    split_service_class_values,
)
from f8pystudio.bridge.runtime_config import PyStudioServiceBridgeConfig
from f8pystudio.diagnostics.logging import configure_root_logging_from_env
from f8pystudio.diagnostics.process_logging import install_process_diagnostics


def _env_or(default: str, name: str) -> str:
    value = os.environ.get(name)
    if value is None:
        return str(default)
    return str(value)


def _env_backend(default: BusBackend, name: str) -> BusBackend:
    text = str(os.environ.get(name, "") or "").strip().lower()
    if text in ("zenoh", "mem"):
        return cast(BusBackend, text)
    return default


def _env_int(default: int, name: str) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except ValueError:
        return int(default)


def _env_flag(default: bool, name: str) -> bool:
    raw = str(os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    return bool(default)


def _split_endpoint_values(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    out: list[str] = []
    for value in values:
        for part in str(value or "").split(","):
            item = part.strip()
            if item:
                out.append(item)
    return tuple(out)


def _split_service_class_values(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    return split_service_class_values(values)


def _env_tuple(default: tuple[str, ...], name: str) -> tuple[str, ...]:
    raw = str(os.environ.get(name, "") or "")
    if not raw.strip():
        return tuple(default)
    return _split_endpoint_values([raw])


def _build_bridge_config(args: argparse.Namespace) -> PyStudioServiceBridgeConfig:
    return PyStudioServiceBridgeConfig(
        bus_backend=cast(BusBackend, str(args.bus_backend)),
        supervision_mode="studio_owned",
        kill_managed_services_on_exit=bool(args.kill_managed_services_on_exit),
        zenoh_config_path=str(args.zenoh_config or "").strip() or None,
        zenoh_connect=_split_endpoint_values(list(args.zenoh_connect or [])),
        zenoh_listen=_split_endpoint_values(list(args.zenoh_listen or [])),
        zenoh_shm_pool_bytes=max(0, int(args.zenoh_shm_pool_bytes)),
    )


def _set_env_or_clear(name: str, value: str | None) -> None:
    text = str(value or "").strip()
    if text:
        os.environ[name] = text
        return
    os.environ.pop(name, None)


def _install_runtime_env(config: PyStudioServiceBridgeConfig) -> None:
    os.environ["F8_BUS_BACKEND"] = str(config.bus_backend)
    if config.bus_backend == "zenoh":
        _set_env_or_clear("F8_ZENOH_CONFIG", config.zenoh_config_path)
        _set_env_or_clear("F8_ZENOH_CONNECT", ",".join(config.zenoh_connect))
        _set_env_or_clear("F8_ZENOH_LISTEN", ",".join(config.zenoh_listen))
        os.environ["F8_ZENOH_SHM_POOL_BYTES"] = str(max(0, int(config.zenoh_shm_pool_bytes)))
        return
    os.environ.pop("F8_ZENOH_CONFIG", None)
    os.environ.pop("F8_ZENOH_CONNECT", None)
    os.environ.pop("F8_ZENOH_LISTEN", None)
    os.environ.pop("F8_ZENOH_SHM_POOL_BYTES", None)


def _install_disabled_service_env(disabled_service_classes: tuple[str, ...]) -> None:
    if disabled_service_classes:
        os.environ[DISABLED_SERVICE_CLASSES_ENV] = os.pathsep.join(disabled_service_classes)
        return
    os.environ.pop(DISABLED_SERVICE_CLASSES_ENV, None)


def _force_process_exit(exit_code: int) -> None:
    try:
        sys.stdout.flush()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        pass
    try:
        sys.stderr.flush()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        pass
    os._exit(int(exit_code))


def main(argv: list[str] | None = None, *, force_process_exit: bool = False) -> int:
    configure_root_logging_from_env()
    install_process_diagnostics()

    parser = argparse.ArgumentParser(description="F8PyStudio")
    parser.add_argument("--describe", action="store_true", help="Output the service description in JSON format")
    parser.add_argument(
        "--discovery-live",
        action="store_true",
        help="Disable static describe.json/inline describe fast-paths; always run describe subprocesses.",
    )
    parser.add_argument(
        "--bus-backend",
        choices=("zenoh", "mem"),
        default=_env_backend("zenoh", "F8_BUS_BACKEND"),
        help="Runtime bus backend (env: F8_BUS_BACKEND, default: zenoh).",
    )
    parser.add_argument(
        "--zenoh-config",
        default=_env_or("", "F8_ZENOH_CONFIG"),
        help="Zenoh config file path (env: F8_ZENOH_CONFIG).",
    )
    parser.add_argument(
        "--zenoh-connect",
        action="append",
        default=list(_env_tuple((), "F8_ZENOH_CONNECT")),
        help="Zenoh connect endpoint(s), comma-separated or repeated (env: F8_ZENOH_CONNECT).",
    )
    parser.add_argument(
        "--zenoh-listen",
        action="append",
        default=list(_env_tuple((), "F8_ZENOH_LISTEN")),
        help="Zenoh listen endpoint(s), comma-separated or repeated (env: F8_ZENOH_LISTEN).",
    )
    parser.add_argument(
        "--zenoh-shm-pool-bytes",
        default=_env_int(DEFAULT_ZENOH_SHM_POOL_BYTES, "F8_ZENOH_SHM_POOL_BYTES"),
        type=int,
        help="Zenoh SHM pool size hint in bytes (env: F8_ZENOH_SHM_POOL_BYTES).",
    )
    parser.add_argument(
        "--kill-managed-services-on-exit",
        action=argparse.BooleanOptionalAction,
        default=_env_flag(True, "F8_KILL_MANAGED_SERVICES_ON_EXIT"),
        help="Stop managed services during PyStudio shutdown (env: F8_KILL_MANAGED_SERVICES_ON_EXIT).",
    )
    parser.add_argument(
        "--disable-service",
        action="append",
        default=list(_split_service_class_values((os.environ.get(DISABLED_SERVICE_CLASSES_ENV) or "",))),
        metavar="SERVICE_CLASS",
        help=(
            "Skip service discovery/registration for a service class. Repeatable; comma-separated values are accepted "
            f"(env: {DISABLED_SERVICE_CLASSES_ENV})."
        ),
    )
    args = parser.parse_args(argv)

    from f8pystudio.app.program import PyStudioProgram

    if args.discovery_live:
        os.environ["F8_DISCOVERY_DISABLE_STATIC_DESCRIBE"] = "1"

    config = _build_bridge_config(args)
    _install_runtime_env(config)
    _install_disabled_service_env(_split_service_class_values(list(args.disable_service or [])))
    prog = PyStudioProgram(config)
    if args.describe:
        print(prog.describe_json_text())
        return 0
    exit_code = int(prog.run())
    if force_process_exit and _env_flag(True, "F8_PYSTUDIO_FORCE_PROCESS_EXIT"):
        _force_process_exit(exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(force_process_exit=True))

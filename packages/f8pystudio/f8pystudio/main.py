from __future__ import annotations

import argparse
import os
from typing import cast

from f8pysdk.bus import BusBackend
from f8pysdk.service_bus.config import DEFAULT_ZENOH_SHM_POOL_BYTES
from f8pystudio.bridge.runtime_config import PyStudioServiceBridgeConfig
from f8pystudio.diagnostics.logging import configure_root_logging_from_env


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


def _split_endpoint_values(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    out: list[str] = []
    for value in values:
        for part in str(value or "").split(","):
            item = part.strip()
            if item:
                out.append(item)
    return tuple(out)


def _env_tuple(default: tuple[str, ...], name: str) -> tuple[str, ...]:
    raw = str(os.environ.get(name, "") or "")
    if not raw.strip():
        return tuple(default)
    return _split_endpoint_values([raw])


def _build_bridge_config(args: argparse.Namespace) -> PyStudioServiceBridgeConfig:
    return PyStudioServiceBridgeConfig(
        bus_backend=cast(BusBackend, str(args.bus_backend)),
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


def main(argv: list[str] | None = None) -> int:
    configure_root_logging_from_env()

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
    args = parser.parse_args(argv)

    from f8pystudio.app.program import PyStudioProgram

    if args.discovery_live:
        os.environ["F8_DISCOVERY_DISABLE_STATIC_DESCRIBE"] = "1"

    config = _build_bridge_config(args)
    _install_runtime_env(config)
    prog = PyStudioProgram(config)
    if args.describe:
        print(prog.describe_json_text())
        return 0
    return prog.run()


if __name__ == "__main__":
    raise SystemExit(main())

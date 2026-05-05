from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import msgspec

from ...bus import BusBackend
from .._internal.error_reporting import ExceptionLogOnce, fingerprint_exception
from ..inventory.catalog import ServiceCatalog
from ..inventory.entry import load_service_entry


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServiceProcessConfig:
    service_class: str
    service_id: str
    bus_backend: BusBackend = "zenoh"
    nats_url: str = "nats://127.0.0.1:4222"
    zenoh_config_path: str | None = None
    zenoh_connect: tuple[str, ...] = ()
    zenoh_listen: tuple[str, ...] = ()
    zenoh_shm_pool_bytes: int = 256 * 1024 * 1024
    purge_kv_bucket_on_start: bool = True


class ServiceProcessManager:
    """
    Launch/track local service processes based on discovery `service.yml`.
    """

    def __init__(self, catalog: ServiceCatalog | None = None) -> None:
        self._catalog = catalog or ServiceCatalog.instance()
        self._procs: dict[str, subprocess.Popen[Any]] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._exception_log_once = ExceptionLogOnce()

    def service_ids(self) -> list[str]:
        return list(self._procs.keys())

    def _start_reader(self, *, service_id: str, proc: subprocess.Popen[Any], on_output: Any | None) -> None:
        if on_output is None:
            return

        def _run() -> None:
            try:
                stream = proc.stdout
                if stream is None:
                    return
                for line in iter(stream.readline, ""):
                    if line == "" and proc.poll() is not None:
                        break
                    try:
                        on_output(str(service_id), str(line))
                    except Exception as exc:
                        fp = fingerprint_exception(context="service_process_manager.on_output", exc=exc)
                        if self._exception_log_once.should_log(fp):
                            logger.error("Service output callback raised (service_id=%s)", service_id, exc_info=exc)
            except Exception as exc:
                fp = fingerprint_exception(context="service_process_manager.reader_thread", exc=exc)
                if self._exception_log_once.should_log(fp):
                    logger.error("Service output reader thread failed (service_id=%s)", service_id, exc_info=exc)

        t = threading.Thread(target=_run, name=f"svc-log:{service_id}", daemon=True)
        self._threads[service_id] = t
        t.start()

    def is_running(self, service_id: str) -> bool:
        sid = str(service_id)
        proc = self._procs.get(sid)
        if proc is None:
            return False
        if proc.poll() is None:
            return True
        self._cleanup_entry(sid)
        return False

    def _cleanup_entry(self, service_id: str) -> None:
        sid = str(service_id)
        proc = self._procs.pop(sid, None)
        thread = self._threads.pop(sid, None)
        if proc is None:
            return
        try:
            if (thread is None or not thread.is_alive()) and proc.stdout:
                proc.stdout.close()
        except (AttributeError, RuntimeError, TypeError):
            pass

    def stop(self, service_id: str) -> bool:
        sid = str(service_id)
        proc = self._procs.get(sid)
        if proc is None:
            return True

        try:
            pid = proc.pid
        except (AttributeError, RuntimeError, TypeError):
            pid = None

        try:
            proc.terminate()
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            proc.wait(timeout=0.8)
        except (subprocess.TimeoutExpired, OSError, RuntimeError, TypeError, ValueError):
            pass

        if os.name == "nt" and pid:
            try:
                creationflags = subprocess.CREATE_NO_WINDOW
            except Exception:
                creationflags = 0
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=2,
                    check=False,
                    creationflags=creationflags,
                )
            except (OSError, RuntimeError, TypeError, ValueError, subprocess.SubprocessError):
                pass
        else:
            try:
                if proc.poll() is None:
                    proc.kill()
            except (AttributeError, RuntimeError, TypeError):
                pass

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                if proc.poll() is not None:
                    break
            except (AttributeError, RuntimeError, TypeError):
                break
            time.sleep(0.05)

        if proc.poll() is None:
            return False
        self._cleanup_entry(sid)
        return True

    def start(self, cfg: ServiceProcessConfig, *, on_output: Any | None = None) -> None:
        service_class = str(cfg.service_class).strip()
        service_id = str(cfg.service_id).strip()
        bus_backend = str(cfg.bus_backend or "zenoh").strip().lower()
        nats_url = str(cfg.nats_url).strip()

        entry_path = self._catalog.service_entry_path(service_class)
        if entry_path is None:
            raise ValueError(f"Missing discovery entry path for serviceClass={service_class!r}")
        service_dir = Path(entry_path).resolve()
        try:
            if service_dir.is_file() and service_dir.name.lower() == "service.yml":
                service_dir = service_dir.parent.resolve()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            pass
        entry = load_service_entry(service_dir)

        if self.is_running(service_id):
            return
        self._cleanup_entry(service_id)

        if cfg.purge_kv_bucket_on_start and bus_backend == "nats":
            try:
                from f8pysdk.nats_naming import kv_bucket_for_service
                from f8pysdk.transport import reset_kv_bucket_sync

                reset_kv_bucket_sync(url=nats_url, kv_bucket=kv_bucket_for_service(service_id), timeout_s=2.5)
                if on_output is not None:
                    on_output(service_id, "[kv] purged bucket on start\n")
            except Exception as exc:
                if on_output is not None:
                    on_output(service_id, f"[kv] purge bucket failed (ignored): {exc}\n")

        launch = entry.launch
        cmd = [str(launch.command), *[str(a) for a in (launch.args or [])]]
        cmd += ["--service-id", service_id, "--bus-backend", bus_backend]
        if bus_backend == "nats":
            cmd += ["--nats-url", nats_url]
        elif bus_backend == "zenoh":
            zenoh_config_path = str(cfg.zenoh_config_path or "").strip()
            if zenoh_config_path:
                cmd += ["--zenoh-config", zenoh_config_path]
            for endpoint in tuple(str(item).strip() for item in cfg.zenoh_connect if str(item).strip()):
                cmd += ["--zenoh-connect", endpoint]
            for endpoint in tuple(str(item).strip() for item in cfg.zenoh_listen if str(item).strip()):
                cmd += ["--zenoh-listen", endpoint]
            cmd += ["--zenoh-shm-pool-bytes", str(max(0, int(cfg.zenoh_shm_pool_bytes)))]

        env = os.environ.copy()
        launch_env = launch.env
        if isinstance(launch_env, dict):
            try:
                env.update({str(k): str(v) for k, v in launch_env.items()})
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass
        env["F8_SERVICE_ID"] = service_id
        env["F8_BUS_BACKEND"] = bus_backend
        if bus_backend == "nats":
            env["F8_NATS_URL"] = nats_url
            env.pop("F8_ZENOH_CONFIG", None)
            env.pop("F8_ZENOH_CONNECT", None)
            env.pop("F8_ZENOH_LISTEN", None)
            env.pop("F8_ZENOH_SHM_POOL_BYTES", None)
        elif bus_backend == "zenoh":
            env.pop("F8_NATS_URL", None)
            zenoh_config_path = str(cfg.zenoh_config_path or "").strip()
            if zenoh_config_path:
                env["F8_ZENOH_CONFIG"] = zenoh_config_path
            else:
                env.pop("F8_ZENOH_CONFIG", None)
            if cfg.zenoh_connect:
                env["F8_ZENOH_CONNECT"] = ",".join(str(item).strip() for item in cfg.zenoh_connect if str(item).strip())
            else:
                env.pop("F8_ZENOH_CONNECT", None)
            if cfg.zenoh_listen:
                env["F8_ZENOH_LISTEN"] = ",".join(str(item).strip() for item in cfg.zenoh_listen if str(item).strip())
            else:
                env.pop("F8_ZENOH_LISTEN", None)
            env["F8_ZENOH_SHM_POOL_BYTES"] = str(max(0, int(cfg.zenoh_shm_pool_bytes)))
        else:
            env.pop("F8_NATS_URL", None)
            env.pop("F8_ZENOH_CONFIG", None)
            env.pop("F8_ZENOH_CONNECT", None)
            env.pop("F8_ZENOH_LISTEN", None)
            env.pop("F8_ZENOH_SHM_POOL_BYTES", None)
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")

        workdir_value = launch.workdir
        workdir_raw = "./" if workdir_value is None or isinstance(workdir_value, msgspec.UnsetType) else str(workdir_value)
        workdir = Path(workdir_raw).expanduser()
        if not workdir.is_absolute():
            workdir = (service_dir / workdir).resolve()
        else:
            workdir = workdir.resolve()

        proc = subprocess.Popen(
            cmd,
            cwd=str(workdir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self._procs[service_id] = proc
        if on_output is not None:
            try:
                pid_txt = str(proc.pid)
            except (AttributeError, RuntimeError, TypeError):
                pid_txt = "?"
            on_output(service_id, f"[proc] started pid={pid_txt} cmd={' '.join(cmd)}\n")
        self._start_reader(service_id=service_id, proc=proc, on_output=on_output)

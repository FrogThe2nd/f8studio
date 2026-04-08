from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from f8pysdk._specs.builtin_fields import normalize_describe_payload_dict
from f8pysdk.codec import dump_json, validate_as
from f8pysdk.monitoring import MonitorContractError, validate_describe_monitor_contract
from f8pysdk.specs import F8ServiceDescribe, F8ServiceEntry

from .entry import _read_yaml


logger = logging.getLogger(__name__)
_LAST_DISCOVERY_TIMING_LINES: list[str] = []
_DISCOVERY_ERROR_LOCK = threading.Lock()
_LAST_DISCOVERY_ERROR_LINES: list[str] = []


def last_discovery_timing_lines() -> list[str]:
    return list(_LAST_DISCOVERY_TIMING_LINES)


def last_discovery_error_lines() -> list[str]:
    with _DISCOVERY_ERROR_LOCK:
        return list(_LAST_DISCOVERY_ERROR_LINES)


def _truncate_text(text: str, *, max_chars: int) -> str:
    raw = str(text or "")
    if len(raw) <= max_chars:
        return raw
    head = max_chars // 2
    tail = max_chars - head - 20
    return raw[:head] + "\n... <truncated> ...\n" + raw[-tail:]


def clear_discovery_errors() -> None:
    with _DISCOVERY_ERROR_LOCK:
        _LAST_DISCOVERY_ERROR_LINES.clear()


def _add_discovery_error(line: str) -> None:
    text = str(line or "").strip()
    if not text:
        return
    with _DISCOVERY_ERROR_LOCK:
        _LAST_DISCOVERY_ERROR_LINES.append(text)


def _read_static_describe_file(service_dir: Path) -> dict[str, Any] | None:
    if (os.environ.get("F8_DISCOVERY_DISABLE_STATIC_DESCRIBE") or "").strip():
        return None

    service_dir = Path(service_dir).resolve()
    json_path = service_dir / "describe.json"
    if json_path.is_file():
        try:
            raw = json_path.read_text("utf-8")
            obj = json.loads(raw) if raw.strip() else None
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None

    for name in ("describe.yml", "describe.yaml"):
        yaml_path = service_dir / name
        if not yaml_path.is_file():
            continue
        try:
            obj = _read_yaml(yaml_path)
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
    return None


def _read_inline_describe(entry: F8ServiceEntry) -> dict[str, Any] | None:
    if (os.environ.get("F8_DISCOVERY_DISABLE_STATIC_DESCRIBE") or "").strip():
        return None
    try:
        extra = entry.model_extra or {}
    except Exception:
        extra = {}
    if not isinstance(extra, dict):
        return None
    describe_obj = extra.get("describe")
    return describe_obj if isinstance(describe_obj, dict) else None


def _filter_benign_stderr(text: str) -> str:
    if not text:
        return ""
    lines: list[str] = []
    for line in str(text).splitlines():
        stripped = str(line).strip()
        if not stripped:
            continue
        if stripped.startswith("Pixi task ("):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _extract_last_json_obj(text: str) -> Any | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    index = 0
    last: Any | None = None
    while index < len(raw):
        match = re.search(r"[\{\[]", raw[index:])
        if match is None:
            break
        start = index + match.start()
        try:
            obj, end = decoder.raw_decode(raw[start:])
            last = obj
            index = start + end
        except Exception:
            index = start + 1
    return last


def _is_pixi_command(command: str) -> bool:
    normalized = str(command or "").strip().lower()
    if not normalized:
        return False
    if normalized in ("pixi", "pixi.exe", "pixi.bat", "pixi.cmd"):
        return True
    return Path(normalized).name in ("pixi", "pixi.exe", "pixi.bat", "pixi.cmd")


def describe_entry(service_dir: Path, entry: F8ServiceEntry) -> dict[str, Any] | None:
    service_dir = Path(service_dir).resolve()

    inline_payload = _read_inline_describe(entry)
    if inline_payload is not None:
        initial_data: dict[str, Any] = dict(inline_payload)
    else:
        static_payload = _read_static_describe_file(service_dir)
        initial_data = static_payload if static_payload is not None else {}

    try:
        launch = entry.launch
        describe_args = list(entry.describeArgs or ["--describe"])
        timeout_ms = int(entry.timeoutMs or 4000)
        if _is_pixi_command(str(launch.command)):
            timeout_ms = max(timeout_ms, 15000)
    except Exception:
        return None

    payload_obj: Any = initial_data if initial_data else None
    cmd = [str(launch.command), *[str(arg) for arg in (launch.args or [])], *[str(arg) for arg in describe_args]]

    env = os.environ.copy()
    try:
        env.update({str(k): str(v) for k, v in (launch.env or {}).items()})
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass

    cwd = service_dir
    try:
        workdir_raw = str(launch.workdir or "./")
        workdir_path = Path(workdir_raw).expanduser()
        if not workdir_path.is_absolute():
            workdir_path = (service_dir / workdir_path).resolve()
        else:
            workdir_path = workdir_path.resolve()
        cwd = workdir_path
    except Exception:
        cwd = service_dir

    if payload_obj is None:
        started_at = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(cwd),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(0.1, timeout_ms / 1000.0),
                check=False,
            )
        except Exception as exc:
            message = f"describe subprocess failed for {service_dir}: {exc} (cwd={cwd}, cmd={' '.join(cmd)})"
            _add_discovery_error(message)
            logger.error(message)
            return None
        finally:
            if logger.isEnabledFor(logging.DEBUG):
                dt_ms = (time.perf_counter() - started_at) * 1000.0
                logger.debug("describe took %.1fms: %s", dt_ms, " ".join(cmd))

        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        filtered_stderr = _filter_benign_stderr(stderr)
        if filtered_stderr and not stdout:
            message = (
                f"describe stderr (no stdout) for {service_dir}: {' '.join(cmd)}\n"
                f"{_truncate_text(filtered_stderr, max_chars=800)}"
            )
            _add_discovery_error(message)
            logger.error(message)
            return None
        if filtered_stderr:
            logger.warning(
                "Error output from describe command %s:\n%s",
                " ".join(cmd),
                _truncate_text(filtered_stderr, max_chars=800),
            )

        payload_obj = _extract_last_json_obj(stdout)
        if not isinstance(payload_obj, dict):
            message = (
                f"describe produced no JSON object for {service_dir}: {' '.join(cmd)}\n"
                f"stdout:\n{_truncate_text(stdout, max_chars=600)}\n"
                f"stderr:\n{_truncate_text(filtered_stderr or stderr, max_chars=600)}"
            )
            _add_discovery_error(message)
            logger.error(message)
            return None

    data = payload_obj
    if not isinstance(data, dict):
        return None
    data = normalize_describe_payload_dict(data)
    try:
        validate_describe_monitor_contract(data)
    except MonitorContractError as exc:
        message = f"describe monitor contract invalid for {service_dir}: {exc}"
        _add_discovery_error(message)
        logger.error(message)
        return None

    try:
        payload = validate_as(F8ServiceDescribe, data)
        data = dump_json(payload, mode="json")
    except Exception:
        if "service" not in data:
            message = f"describe JSON missing required key 'service' for {service_dir}: {' '.join(cmd)}"
            _add_discovery_error(message)
            logger.error(message)
            return None
        if "operators" not in data:
            data["operators"] = []

    try:
        service_payload = data.get("service") or {}
        if isinstance(service_payload, dict) and not service_payload.get("launch"):
            service_payload["launch"] = dump_json(entry.launch, mode="json")
            data["service"] = service_payload
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass

    try:
        entry_service_class = str(entry.serviceClass or "").strip()
        described_service_class = str((data.get("service") or {}).get("serviceClass") or "").strip()
        if entry_service_class and described_service_class and entry_service_class != described_service_class:
            message = (
                f"Service class mismatch for {service_dir}: entry has '{entry_service_class}', "
                f"described has '{described_service_class}'"
            )
            _add_discovery_error(message)
            logger.error(message)
            return None
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass

    return data


def describe_entry_timed(service_dir: Path, entry: F8ServiceEntry) -> tuple[dict[str, Any] | None, float, str]:
    started_at = time.perf_counter()

    if _read_inline_describe(entry) is not None:
        payload = describe_entry(service_dir, entry)
        return payload, (time.perf_counter() - started_at) * 1000.0, "inline"

    if _read_static_describe_file(service_dir) is not None:
        payload = describe_entry(service_dir, entry)
        return payload, (time.perf_counter() - started_at) * 1000.0, "file"

    payload = describe_entry(service_dir, entry)
    return payload, (time.perf_counter() - started_at) * 1000.0, "subprocess"


def discovery_parallelism(service_count: int) -> int:
    raw = (os.environ.get("F8_DESCRIBE_JOBS") or os.environ.get("F8_DISCOVERY_JOBS") or "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except Exception:
            return 1

    cpu_count = os.cpu_count() or 4
    return max(1, min(service_count, min(6, cpu_count)))


def discovery_log_timings_enabled() -> bool:
    raw = (os.environ.get("F8_DISCOVERY_LOG_TIMINGS") or "").strip().lower()
    if raw in ("1", "true", "yes", "on", "enable", "enabled"):
        return True
    if raw in ("0", "false", "no", "off", "disable", "disabled", ""):
        return False
    return True


def discovery_slow_ms_default() -> float:
    raw = (os.environ.get("F8_DISCOVERY_SLOW_MS") or "").strip()
    if not raw:
        return 0.0
    try:
        return max(0.0, float(raw))
    except Exception:
        return 0.0


def set_discovery_timing_lines(lines: list[str]) -> None:
    global _LAST_DISCOVERY_TIMING_LINES
    _LAST_DISCOVERY_TIMING_LINES = list(lines)


__all__ = [
    "clear_discovery_errors",
    "describe_entry",
    "describe_entry_timed",
    "discovery_log_timings_enabled",
    "discovery_parallelism",
    "discovery_slow_ms_default",
    "last_discovery_error_lines",
    "last_discovery_timing_lines",
    "set_discovery_timing_lines",
]

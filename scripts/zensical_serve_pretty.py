from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class ServeInfo:
    root: str
    url: str


_SERVING_RE = re.compile(r"^Serving\s+(?P<root>.+?)\s+on\s+(?P<url>https?://\S+)\s*$")
_BUILD_FINISHED_PREFIX = "Build finished"
_BUILD_STARTED = "Build started"


def _parse_serving_line(line: str) -> ServeInfo | None:
    m = _SERVING_RE.match(line.strip())
    if not m:
        return None
    root = str(m.group("root") or "").strip()
    url = str(m.group("url") or "").strip()
    if not (root and url):
        return None
    return ServeInfo(root=root, url=url)


def _print_ready(info: ServeInfo) -> None:
    # Keep it short and grep-friendly.
    sys.stdout.write(f"Ready: {info.url}\n")
    sys.stdout.flush()


def main() -> int:
    forward_args = list(sys.argv[1:])
    # Pixi passes a literal `--` separator through to the task command.
    # Strip it so users can run:
    #   pixi run doc_serve -- --dev-addr localhost:8001
    if forward_args and forward_args[0] == "--":
        forward_args = forward_args[1:]
    cmd = ["zensical", "serve", *forward_args]
    env = dict(os.environ)
    proc: subprocess.Popen[str] | None = None
    serve_info: ServeInfo | None = None
    saw_build_start = False
    ready_printed = False
    idle_delay_s_raw = env.get("F8_DOCSERVE_READY_IDLE_S", "0.8")
    try:
        idle_delay_s = max(0.1, float(idle_delay_s_raw))
    except ValueError:
        idle_delay_s = 0.8

    lock = threading.Lock()
    stop_event = threading.Event()
    last_activity_s = time.monotonic()

    def _forward_signal(signum: int, _frame: object) -> None:
        nonlocal proc
        if proc is None:
            return
        try:
            proc.send_signal(signum)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                return

    try:
        signal.signal(signal.SIGINT, _forward_signal)
        signal.signal(signal.SIGTERM, _forward_signal)
    except Exception:
        # Some platforms restrict signal handling (e.g. Windows). Best-effort.
        pass

    def _mark_activity() -> None:
        nonlocal last_activity_s
        last_activity_s = time.monotonic()

    def _maybe_print_ready(*, force: bool) -> None:
        nonlocal ready_printed
        if ready_printed:
            return
        if serve_info is None:
            return
        if not saw_build_start and not force:
            return
        _print_ready(serve_info)
        ready_printed = True

    def _idle_watcher() -> None:
        # When zensical finishes spamming paths, it often becomes silent.
        # Print the URL again at that moment so it stays at the bottom.
        while not stop_event.is_set():
            time.sleep(0.2)
            with lock:
                if serve_info is None or ready_printed or not saw_build_start:
                    continue
                idle_s = time.monotonic() - float(last_activity_s)
                if idle_s >= float(idle_delay_s):
                    _maybe_print_ready(force=False)

    watcher_thread = threading.Thread(target=_idle_watcher, name="zensical_idle_watcher", daemon=True)
    watcher_thread.start()

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
    except FileNotFoundError as exc:
        sys.stderr.write(f"Failed to start zensical: {exc}\n")
        stop_event.set()
        return 127
    except Exception as exc:
        sys.stderr.write(f"Failed to start zensical: {type(exc).__name__}: {exc}\n")
        stop_event.set()
        return 1

    assert proc.stdout is not None
    try:
        for raw_line in proc.stdout:
            line = str(raw_line)
            sys.stdout.write(line)
            sys.stdout.flush()
            with lock:
                _mark_activity()

            info = _parse_serving_line(line)
            if info is not None:
                with lock:
                    serve_info = info
                continue

            if line.strip() == _BUILD_STARTED:
                with lock:
                    saw_build_start = True
                    ready_printed = False
                continue

            if line.strip().startswith(_BUILD_FINISHED_PREFIX):
                with lock:
                    saw_build_start = True
                    _maybe_print_ready(force=True)

        stop_event.set()
        return int(proc.wait())
    except KeyboardInterrupt:
        stop_event.set()
        try:
            proc.send_signal(signal.SIGINT)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
        try:
            return int(proc.wait())
        except Exception:
            return 130
    finally:
        stop_event.set()
        try:
            watcher_thread.join(timeout=0.5)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass
class _SupervisorStopState:
    requested: bool = False


def _parent_alive(parent_pid: int) -> bool:
    if parent_pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(parent_pid))
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return False
    try:
        os.kill(int(parent_pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _kill_process_tree(proc: subprocess.Popen[object]) -> None:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(int(proc.pid)), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2.0,
                check=False,
            )
        except (OSError, RuntimeError, TypeError, ValueError, subprocess.SubprocessError) as exc:
            print(f"[supervisor] taskkill failed pid={proc.pid}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError as exc:
        print(f"[supervisor] killpg failed pid={proc.pid}: {type(exc).__name__}: {exc}", file=sys.stderr)
    try:
        proc.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        print(f"[supervisor] child did not exit after kill pid={proc.pid}", file=sys.stderr)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"[supervisor] wait after kill failed pid={proc.pid}: {type(exc).__name__}: {exc}", file=sys.stderr)


def _terminate_process_tree(proc: subprocess.Popen[object], *, grace_s: float) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            proc.terminate()
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            print(f"[supervisor] terminate failed pid={proc.pid}: {type(exc).__name__}: {exc}", file=sys.stderr)
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError as exc:
            print(f"[supervisor] terminate process group failed pid={proc.pid}: {type(exc).__name__}: {exc}", file=sys.stderr)
    try:
        proc.wait(timeout=max(0.0, float(grace_s)))
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)


def _wait_then_terminate_process_tree(proc: subprocess.Popen[object], *, wait_s: float, terminate_grace_s: float) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.wait(timeout=max(0.0, float(wait_s)))
        return
    except subprocess.TimeoutExpired:
        pass
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"[supervisor] wait before terminate failed pid={proc.pid}: {type(exc).__name__}: {exc}", file=sys.stderr)
    _terminate_process_tree(proc, grace_s=float(terminate_grace_s))


def _install_signal_handlers(
    proc: subprocess.Popen[object],
    *,
    grace_s: float,
    soft_wait_s: float,
    stop_state: _SupervisorStopState,
) -> None:
    stopping = threading.Event()

    def _handle_gentle_signal(signum: int, _frame: object) -> None:
        if stopping.is_set():
            return
        stopping.set()
        stop_state.requested = True
        print(f"[supervisor] signal {int(signum)} received; waiting before terminate child pid={proc.pid}", file=sys.stderr)
        _wait_then_terminate_process_tree(proc, wait_s=float(soft_wait_s), terminate_grace_s=float(grace_s))

    def _handle_terminate_signal(signum: int, _frame: object) -> None:
        if stopping.is_set():
            return
        stopping.set()
        stop_state.requested = True
        print(f"[supervisor] signal {int(signum)} received; terminate child pid={proc.pid}", file=sys.stderr)
        _terminate_process_tree(proc, grace_s=float(grace_s))

    try:
        signal.signal(signal.SIGINT, _handle_gentle_signal)
    except (OSError, RuntimeError, ValueError):
        pass
    try:
        signal.signal(signal.SIGTERM, _handle_terminate_signal)
    except (OSError, RuntimeError, ValueError):
        pass
    if os.name == "nt":
        try:
            signal.signal(signal.SIGBREAK, _handle_gentle_signal)
        except (AttributeError, OSError, RuntimeError, ValueError):
            pass


def _start_stdin_control_thread(
    proc: subprocess.Popen[object],
    *,
    grace_s: float,
    soft_wait_s: float,
    stop_state: _SupervisorStopState,
) -> None:
    stopping = threading.Event()

    def _run() -> None:
        try:
            for raw_line in sys.stdin:
                command = str(raw_line or "").strip().lower()
                if command not in {"stop", "quit", "exit"}:
                    continue
                if stopping.is_set():
                    return
                stopping.set()
                stop_state.requested = True
                print(f"[supervisor] stdin stop requested; waiting for child pid={proc.pid}", file=sys.stderr)
                _wait_then_terminate_process_tree(proc, wait_s=float(soft_wait_s), terminate_grace_s=float(grace_s))
                return
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            print(f"[supervisor] stdin control failed pid={proc.pid}: {type(exc).__name__}: {exc}", file=sys.stderr)

    thread = threading.Thread(target=_run, name="supervisor-stdin-control", daemon=True)
    thread.start()


def _run_supervisor(
    *,
    parent_pid: int,
    poll_s: float,
    grace_s: float,
    soft_wait_s: float,
    child_cmd: Sequence[str],
) -> int:
    if not child_cmd:
        raise ValueError("child command is empty")
    popen_kwargs: dict[str, object] = {}
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True
    else:
        try:
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        except AttributeError:
            pass
    proc = subprocess.Popen(list(child_cmd), **popen_kwargs)
    print(f"[supervisor] child started pid={proc.pid} parentPid={int(parent_pid)}", flush=True)
    stop_state = _SupervisorStopState()
    _install_signal_handlers(
        proc,
        grace_s=float(grace_s),
        soft_wait_s=float(soft_wait_s),
        stop_state=stop_state,
    )
    _start_stdin_control_thread(
        proc,
        grace_s=float(grace_s),
        soft_wait_s=float(soft_wait_s),
        stop_state=stop_state,
    )
    while True:
        rc = proc.poll()
        if rc is not None:
            if stop_state.requested:
                return 0
            return int(rc)
        if not _parent_alive(int(parent_pid)):
            print(f"[supervisor] parent pid={int(parent_pid)} disappeared; stopping child pid={proc.pid}", file=sys.stderr)
            _terminate_process_tree(proc, grace_s=float(grace_s))
            return 0
        time.sleep(max(0.05, float(poll_s)))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a service child bound to the PyStudio parent process.")
    parser.add_argument("--parent-pid", required=True, type=int)
    parser.add_argument("--poll-s", default=0.5, type=float)
    parser.add_argument("--soft-wait-s", default=2.0, type=float)
    parser.add_argument("--grace-s", default=2.0, type=float)
    parser.add_argument("child_cmd", nargs=argparse.REMAINDER)
    args = parser.parse_args(list(argv) if argv is not None else None)
    child_cmd = list(args.child_cmd)
    if child_cmd and child_cmd[0] == "--":
        child_cmd = child_cmd[1:]
    return _run_supervisor(
        parent_pid=int(args.parent_pid),
        poll_s=float(args.poll_s),
        grace_s=float(args.grace_s),
        soft_wait_s=float(args.soft_wait_s),
        child_cmd=child_cmd,
    )


if __name__ == "__main__":
    raise SystemExit(main())

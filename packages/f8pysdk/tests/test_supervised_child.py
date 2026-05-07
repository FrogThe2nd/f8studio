from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from f8pysdk.service_runtime_tools.deploy import supervised_child


def _supervisor_script_path() -> str:
    return str(Path(supervised_child.__file__).resolve())


def test_supervised_child_propagates_child_failure_exit_code() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            _supervisor_script_path(),
            "--parent-pid",
            str(os.getpid()),
            "--",
            sys.executable,
            "-c",
            "raise SystemExit(7)",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=5.0,
        check=False,
    )

    assert proc.returncode == 7


def test_supervised_child_returns_success_when_stdin_stop_requested() -> None:
    proc = subprocess.Popen(
        [
            sys.executable,
            _supervisor_script_path(),
            "--parent-pid",
            str(os.getpid()),
            "--soft-wait-s",
            "0.1",
            "--",
            sys.executable,
            "-c",
            "\n".join(
                [
                    "import signal",
                    "import time",
                    "def on_int(signum, frame):",
                    "    print('unexpected-sigint', flush=True)",
                    "signal.signal(signal.SIGINT, on_int)",
                    "print('child-ready', flush=True)",
                    "time.sleep(30)",
                ]
            ),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    try:
        first_line = proc.stdout.readline() if proc.stdout is not None else ""
        assert first_line.startswith("[supervisor] child started")
        assert proc.stdin is not None
        proc.stdin.write("stop\n")
        proc.stdin.flush()
        stdout, stderr = proc.communicate(timeout=5.0)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5.0)

    assert proc.returncode == 0
    assert "stdin stop requested" in stderr
    assert "unexpected-sigint" not in stdout
    assert stdout == "" or "child-ready" in stdout


def test_supervised_child_returns_success_when_supervisor_is_terminated() -> None:
    proc = subprocess.Popen(
        [
            sys.executable,
            _supervisor_script_path(),
            "--parent-pid",
            str(os.getpid()),
            "--soft-wait-s",
            "0.1",
            "--",
            sys.executable,
            "-c",
            "import time; print('child-ready', flush=True); time.sleep(30)",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    try:
        first_line = proc.stdout.readline() if proc.stdout is not None else ""
        assert first_line.startswith("[supervisor] child started")
        proc.terminate()
        _stdout, stderr = proc.communicate(timeout=5.0)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5.0)

    assert proc.returncode == 0
    assert "terminate child" in stderr

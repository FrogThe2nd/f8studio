from __future__ import annotations

import importlib
import json
import os
import sys

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PACKAGE_ROOTS = (
    os.path.join(REPO_ROOT, "f8pysdk"),
    os.path.join(REPO_ROOT, "f8pydl"),
    os.path.join(REPO_ROOT, "f8pyaudiofeat"),
    os.path.join(REPO_ROOT, "f8pyscript"),
    os.path.join(REPO_ROOT, "f8pyengine"),
    os.path.join(REPO_ROOT, "f8pymppose"),
    os.path.join(REPO_ROOT, "f8proclauncher"),
)
for path in PACKAGE_ROOTS:
    if path not in sys.path:
        sys.path.insert(0, path)


@pytest.mark.parametrize(
    ("module_name", "callable_name", "expected_service_class"),
    [
        ("f8pydl.main_classifier", "main", "f8.dl.classifier"),
        ("f8pydl.main_detector", "main", "f8.dl.detector"),
        ("f8pydl.main_detsorter", "main", "f8.dl.detsorter"),
        ("f8pydl.main_humandetector", "main", "f8.dl.humandetector"),
        ("f8pydl.main_optflow", "main", "f8.dl.optflow"),
        ("f8pydl.main_tcnwave", "main", "f8.dl.tcnwave"),
        ("f8pyaudiofeat.main_core", "main", "f8.audiofeat.core"),
        ("f8pyaudiofeat.main_rhythm", "main", "f8.audiofeat.rhythm"),
        ("f8pymppose.main_pose", "main", "f8.mp.pose"),
        ("f8pyscript.main_expr", "_main", "f8.pyexpr"),
        ("f8pyscript.main_script", "_main", "f8.pyscript"),
        ("f8pyengine.main", "_main", "f8.pyengine"),
        ("f8proclauncher.main", "_main", "f8.proclauncher"),
    ],
)
def test_service_entrypoint_cli_describe_smoke(
    module_name: str,
    callable_name: str,
    expected_service_class: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = importlib.import_module(module_name)
    runner = getattr(module, callable_name)

    code = runner(["--describe"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 0
    assert payload["service"]["serviceClass"] == expected_service_class

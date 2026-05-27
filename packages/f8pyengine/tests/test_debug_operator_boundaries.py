from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from typing import Any

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SDK_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "f8pysdk"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if SDK_ROOT not in sys.path:
    sys.path.insert(0, SDK_ROOT)

from f8pyengine.operators.print import PrintRuntimeNode  # noqa: E402
from f8pyengine.operators.tick import TickRuntimeNode  # noqa: E402


@dataclass(frozen=True)
class _Port:
    name: str


@dataclass(frozen=True)
class _State:
    name: str


@dataclass(frozen=True)
class _Node:
    dataInPorts: list[_Port] | None = None
    dataOutPorts: list[_Port] | None = None
    stateFields: list[_State] | None = None
    execOutPorts: list[str] | None = None


def test_print_runtime_decodes_bytes_with_replacement(capsys) -> None:
    node = PrintRuntimeNode(
        node_id="printer",
        node=_Node(dataInPorts=[_Port("value")]),
        initial_state={"strip": True},
    )

    asyncio.run(node.on_data("value", b" hello \xff \n"))

    captured = capsys.readouterr()
    assert "[printer] value=hello" in captured.out
    assert "\ufffd" in captured.out


def test_tick_timer_resolution_failure_is_logged(monkeypatch, caplog) -> None:
    node = TickRuntimeNode(node_id="tick", node=_Node(execOutPorts=["exec"]), initial_state={})

    monkeypatch.setattr("f8pyengine.operators.tick.sys.platform", "win32")
    caplog.set_level("DEBUG", logger="f8pyengine.operators.tick")
    node._apply_windows_timer_resolution(True)

    assert "Windows timer resolution request failed enabled=True" in caplog.text

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from ..capabilities import CommandableNode
from ..command_state import (
    command_input_state_field,
    command_key_for_name,
    command_output_state_field,
)
from ..generated import F8Command, F8ServiceSpec
from .error_utils import log_error_once
from .state_write import StateWriteOrigin, StateWriteSource

if TYPE_CHECKING:
    from .api.bus import ServiceBus


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommandBinding:
    node_id: str
    command_name: str
    command_key: str
    input_field: str
    output_field: str
    param_names: tuple[str, ...]


@dataclass
class _CommandDispatchState:
    running: bool = False
    pending: bool = False
    latest_value: Any = None
    latest_ts_ms: int | None = None
    latest_meta: dict[str, Any] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _command_specs_for_node(node: Any) -> list[F8Command]:
    spec = getattr(node, "spec", None)
    if isinstance(spec, F8ServiceSpec):
        return list(spec.commands or [])
    commands = getattr(spec, "commands", None)
    if isinstance(commands, list):
        return [command for command in commands if isinstance(command, F8Command)]
    return []


def build_command_bindings(bus: "ServiceBus") -> tuple[
    dict[tuple[str, str], CommandBinding],
    dict[tuple[str, str], CommandBinding],
    set[tuple[str, str]],
]:
    input_bindings: dict[tuple[str, str], CommandBinding] = {}
    output_bindings: dict[tuple[str, str], CommandBinding] = {}
    hidden_fields: set[tuple[str, str]] = set()

    for node_id, node in list(bus._nodes.items()):
        for command in _command_specs_for_node(node):
            command_name = str(command.name or "").strip()
            if not command_name:
                continue
            command_key = command_key_for_name(command_name)
            input_field = command_input_state_field(command_name)
            output_field = command_output_state_field(command_name)
            binding = CommandBinding(
                node_id=str(node_id),
                command_name=command_name,
                command_key=command_key,
                input_field=input_field,
                output_field=output_field,
                param_names=tuple(str(param.name or "").strip() for param in list(command.params or []) if str(param.name or "").strip()),
            )
            input_bindings[(binding.node_id, binding.input_field)] = binding
            output_bindings[(binding.node_id, binding.command_name)] = binding
            hidden_fields.add((binding.node_id, binding.input_field))
            hidden_fields.add((binding.node_id, binding.output_field))
    return input_bindings, output_bindings, hidden_fields


def command_state_bindings_ready(bus: "ServiceBus") -> None:
    input_bindings, output_bindings, hidden_fields = build_command_bindings(bus)
    bus._command_input_bindings = input_bindings
    bus._command_output_bindings = output_bindings
    bus._command_hidden_fields = hidden_fields


def map_command_args(value: Any, param_names: tuple[str, ...]) -> dict[str, Any]:
    if not param_names:
        return {}
    if isinstance(value, dict):
        args: dict[str, Any] = {}
        for name in param_names:
            if name in value:
                args[name] = value[name]
        return args
    if isinstance(value, (list, tuple)):
        args = {}
        for index, item in enumerate(value):
            if index >= len(param_names):
                break
            args[param_names[index]] = item
        return args
    return {param_names[0]: value}


async def write_command_output(
    bus: "ServiceBus",
    *,
    node_id: str,
    call: str,
    result: Any,
    ts_ms: int | None,
    meta: dict[str, Any] | None,
) -> None:
    from .domain.state_pipeline import publish_state

    binding = bus._command_output_bindings.get((str(node_id), str(call)))
    if binding is None:
        return
    payload_meta = dict(meta or {})
    payload_meta.setdefault("command", str(call))
    await publish_state(
        bus,
        binding.node_id,
        binding.output_field,
        result,
        origin=StateWriteOrigin.runtime,
        source=StateWriteSource.cmd,
        ts_ms=ts_ms,
        meta=payload_meta,
    )


async def dispatch_command_input(
    bus: "ServiceBus",
    *,
    node_id: str,
    field: str,
    value: Any,
    ts_ms: int,
    meta: dict[str, Any],
) -> None:
    binding = bus._command_input_bindings.get((str(node_id), str(field)))
    if binding is None:
        return
    dispatch_state = bus._command_dispatch_states.setdefault(
        (binding.node_id, binding.command_name),
        _CommandDispatchState(),
    )

    async with dispatch_state.lock:
        dispatch_state.latest_value = value
        dispatch_state.latest_ts_ms = int(ts_ms)
        dispatch_state.latest_meta = dict(meta)
        if dispatch_state.running:
            dispatch_state.pending = True
            return
        dispatch_state.running = True

    try:
        while True:
            async with dispatch_state.lock:
                dispatch_state.pending = False
                latest_value = dispatch_state.latest_value
                latest_ts_ms = dispatch_state.latest_ts_ms
                latest_meta = dict(dispatch_state.latest_meta)
            await _dispatch_command_once(
                bus,
                binding=binding,
                value=latest_value,
                ts_ms=latest_ts_ms,
                meta=latest_meta,
            )
            async with dispatch_state.lock:
                if not dispatch_state.pending:
                    dispatch_state.running = False
                    return
    except Exception:
        async with dispatch_state.lock:
            dispatch_state.running = False
        raise


async def _dispatch_command_once(
    bus: "ServiceBus",
    *,
    binding: CommandBinding,
    value: Any,
    ts_ms: int | None,
    meta: dict[str, Any],
) -> None:
    service_node = bus.get_node(binding.node_id)
    if service_node is None or not isinstance(service_node, CommandableNode):
        log_error_once(
            bus,
            key=f"command_dispatch_missing_node:{binding.node_id}:{binding.command_name}",
            message=f"command dispatch target missing or not commandable: {binding.node_id}.{binding.command_name}",
        )
        return

    args = map_command_args(value, binding.param_names)
    call_meta = dict(meta)
    call_meta.setdefault("source", StateWriteSource.state_edge_intra.value)
    call_meta.setdefault("commandInputField", binding.input_field)
    try:
        result = await service_node.on_command(binding.command_name, args, meta=call_meta)  # type: ignore[misc]
    except Exception as exc:
        log_error_once(
            bus,
            key=f"command_dispatch_failed:{binding.node_id}:{binding.command_name}:{type(exc).__name__}:{exc}",
            message=f"command dispatch failed for {binding.node_id}.{binding.command_name}",
            exc=exc,
        )
        return

    await write_command_output(
        bus,
        node_id=binding.node_id,
        call=binding.command_name,
        result=result,
        ts_ms=ts_ms,
        meta={
            "command": binding.command_name,
            "commandInputField": binding.input_field,
            "source": StateWriteSource.cmd.value,
        },
    )

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any

from .generated import F8Command, F8StateAccess, F8StateSpec
from ._specs.schema import any_schema


_COMMAND_STATE_PREFIX = "__cmd__."
_COMMAND_INPUT_SUFFIX = ".in"
_COMMAND_OUTPUT_SUFFIX = ".out"


def _fnv1a32(text: str) -> int:
    data = str(text or "").encode("utf-8")
    value = 0x811C9DC5
    for byte in data:
        value ^= int(byte)
        value = (value * 0x01000193) & 0xFFFFFFFF
    return value


def command_key_for_name(name: str) -> str:
    raw = str(name or "").strip()
    parts: list[str] = []
    last_was_sep = False
    for ch in raw.lower():
        if ("a" <= ch <= "z") or ("0" <= ch <= "9"):
            parts.append(ch)
            last_was_sep = False
            continue
        if not last_was_sep:
            parts.append("_")
            last_was_sep = True
    base = "".join(parts).strip("_") or "command"
    return f"{base}_{_fnv1a32(raw):08x}"


def command_input_state_field(command_name: str) -> str:
    return command_input_state_field_for_key(command_key_for_name(command_name))


def command_output_state_field(command_name: str) -> str:
    return command_output_state_field_for_key(command_key_for_name(command_name))


def command_input_state_field_for_key(command_key: str) -> str:
    return f"{_COMMAND_STATE_PREFIX}{str(command_key).strip()}{_COMMAND_INPUT_SUFFIX}"


def command_output_state_field_for_key(command_key: str) -> str:
    return f"{_COMMAND_STATE_PREFIX}{str(command_key).strip()}{_COMMAND_OUTPUT_SUFFIX}"


def parse_hidden_command_state_field(field_name: str) -> tuple[str, str] | None:
    raw = str(field_name or "").strip()
    if not raw.startswith(_COMMAND_STATE_PREFIX):
        return None
    if raw.endswith(_COMMAND_INPUT_SUFFIX):
        key = raw[len(_COMMAND_STATE_PREFIX) : -len(_COMMAND_INPUT_SUFFIX)].strip()
        return (key, "in") if key else None
    if raw.endswith(_COMMAND_OUTPUT_SUFFIX):
        key = raw[len(_COMMAND_STATE_PREFIX) : -len(_COMMAND_OUTPUT_SUFFIX)].strip()
        return (key, "out") if key else None
    return None


def is_hidden_command_state_field(field_name: str) -> bool:
    return parse_hidden_command_state_field(field_name) is not None


def command_input_port_name(command_name: str) -> str:
    return f"[C]{str(command_name or '').strip()}"


def command_output_port_name(command_name: str) -> str:
    return f"{str(command_name or '').strip()}[C]"


def parse_command_port_name(port_name: str) -> tuple[bool, str] | None:
    raw = str(port_name or "").strip()
    if raw.startswith("[C]"):
        name = str(raw[3:] or "").strip()
        return (True, name) if name else None
    if raw.endswith("[C]"):
        name = str(raw[:-3] or "").strip()
        return (False, name) if name else None
    return None


def hidden_command_state_specs(commands: list[F8Command] | tuple[F8Command, ...]) -> list[F8StateSpec]:
    fields: list[F8StateSpec] = []
    seen: set[str] = set()
    for command in list(commands or []):
        name = str(command.name or "").strip()
        if not name:
            continue
        command_key = command_key_for_name(name)
        input_name = command_input_state_field_for_key(command_key)
        output_name = command_output_state_field_for_key(command_key)
        if input_name not in seen:
            fields.append(
                F8StateSpec(
                    name=input_name,
                    valueSchema=any_schema(),
                    access=F8StateAccess.wo,
                    showOnNode=False,
                    description=f"Hidden backing state for command input: {name}",
                )
            )
            seen.add(input_name)
        if output_name not in seen:
            fields.append(
                F8StateSpec(
                    name=output_name,
                    valueSchema=any_schema(),
                    access=F8StateAccess.ro,
                    showOnNode=False,
                    description=f"Hidden backing state for command output: {name}",
                )
            )
            seen.add(output_name)
    return fields


class CommandExecutionErrorKind(enum.Enum):
    missing_target = "missing_target"
    invalid_args = "invalid_args"
    handler_failed = "handler_failed"


class CommandOutputPolicy(enum.Enum):
    hidden_state = "hidden_state"
    none = "none"


@dataclass(frozen=True)
class CommandExecutionResult:
    value: Any = None
    error_kind: CommandExecutionErrorKind | None = None
    error_message: str | None = None

    @property
    def ok(self) -> bool:
        return self.error_kind is None


__all__ = [
    "command_input_port_name",
    "command_input_state_field",
    "command_input_state_field_for_key",
    "command_key_for_name",
    "command_output_port_name",
    "command_output_state_field",
    "command_output_state_field_for_key",
    "CommandExecutionErrorKind",
    "CommandExecutionResult",
    "CommandOutputPolicy",
    "hidden_command_state_specs",
    "is_hidden_command_state_field",
    "parse_command_port_name",
    "parse_hidden_command_state_field",
]

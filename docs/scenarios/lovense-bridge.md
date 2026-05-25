# Lovense Bridge

## Goal

Use `Lovense Mock Server` only as the ingress node, then move protocol parsing into one or more `Python Script` nodes.

This replaces the old monolithic adapter style where one node knew every sub-protocol and already decided the motion mapping. The new split is:

`Lovense Mock Server -> Python Script parser(s) -> user post-process -> Handy / ProgramWave / Sequence / TCode`

The parser layer only does:

- command kind detection
- field extraction
- explicit type coercion
- lightweight normalization such as `strengths -> list[float]` or `rule -> stepMs`

The parser layer should not decide the final motion behavior for the user. That part stays in downstream scripts or operators.

## Why This Design

- A single adapter node is too adhoc for this job: it mixes protocol parsing, normalization, and output policy in one place.
- Different Lovense sub-protocols naturally evolve independently, so "one protocol = one parser script" is easier to maintain.
- `python_script` already supports custom state fields, `onState`, `ctx.emit()`, and `ctx.set_state()`, so it is a better home for this bridge layer.
- Users can inspect and modify parser code directly in the graph, instead of recompiling a dedicated operator.

## Canonical Event Contract

`Lovense Mock Server` should be treated as the source of truth. A parser script should consume the `event` state emitted by that node.

The canonical event shape is based on `lovense_mock_server.py` and should be read from these fields:

```python
event = {
    "seq": 12,
    "eventId": "node_id:12",
    "tsMs": 1712345678901,
    "ts": "2026-04-22T12:34:56.789Z",
    "remote": "127.0.0.1",
    "path": "/command",
    "command": {
        "name": "Function",
        "apiVer": 1,
        "kind": "solace_thrusting",
    },
    "toys": {
        "scope": "selected",
        "ids": ["ff922f7fd345"],
        "names": ["Solace Pro"],
        "unknown": [],
    },
    "params": {
        "action": "Thrusting:12,Depth:4",
        "timeSec": 10,
        "loopRunningSec": 3,
        "loopPauseSec": 1,
    },
}
```

Important notes:

- Prefer `event["command"]["kind"]` for protocol dispatch.
- Prefer `event["params"]` for command parameters.
- Prefer `event["toys"]` for target toy routing.
- Do not treat `event["summary"]` as canonical for new scripts. The current mock server builds `command / toys / params`.

## Recommended Graph

Recommended structure:

1. `Lovense Mock Server`
2. Several `Python Script` parser nodes, each with a dedicated protocol
3. One or more downstream user scripts that map the parsed message into motion logic
4. Device/output operators such as `Handy Out`, `ProgramWave`, `Exec Sequence`, or `TCode`

Suggested wiring:

- `lovense_mock_server.event` state edge -> every parser node's `lovenseEvent`
- parser node `out` -> user-specific post-process node
- parser node optional `lastParsed` state -> debug UI

Recommended parser split:

- `lovense_function_solace_thrusting_parser`
- `lovense_function_all_vibrate_parser`
- `lovense_pattern_parser`
- `lovense_stop_parser`
- optional `lovense_other_parser` for logging unknown commands

This keeps each script explicit and grep-able, and avoids a single giant branch table.

## Python Script Node Setup

For each parser node:

1. Use operator `f8.python_script`.
2. Keep the default data output port `out`.
3. Add a custom state field named `lovenseEvent`.
4. Optionally add `lastParsed` and `lastKind` state fields for debugging.
5. Drive the script from `onState`, not from `onMsg`.

Suggested custom state fields:

- `lovenseEvent`: incoming event object from `Lovense Mock Server`
- `lastParsed`: latest parsed payload emitted by this parser
- `lastKind`: latest `command.kind` seen by this parser

The common pattern is:

```python
def onState(ctx, field, value, ts_ms=None):
    if field != "lovenseEvent":
        return
    if not isinstance(value, dict):
        return

    kind = _kind_of(value)
    ctx.set_state("lastKind", kind)
    if kind != "<this parser kind>":
        return

    parsed = _parse_event(value)
    ctx.set_state("lastParsed", parsed)
    ctx.emit("out", parsed)
```

## Shared Parsing Conventions

Downstream scripts become much easier to write if every parser emits a stable schema:

```python
{
    "protocol": "solace_thrusting",
    "tsMs": 1712345678901,
    "eventId": "node_id:12",
    "apiVer": 1,
    "toyIds": ["ff922f7fd345"],
    "toyNames": ["Solace Pro"],
    "timeSec": 10.0,
    "loopRunningSec": 3.0,
    "loopPauseSec": 1.0,
    "payload": {
        ...
    },
    "rawEvent": event,
}
```

Guidelines:

- Keep raw fields and normalized fields together.
- Normalize numbers into `float` or `int` once in the parser layer.
- Preserve `rawEvent` so the user can still access original fields downstream.
- Raise explicit `ValueError` for malformed required protocol fields instead of silently guessing.

## Script: `solace_thrusting` Parser

Use this for `command.kind == "solace_thrusting"`.

```python
from typing import Any


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    return float(text)


def _parse_int(value: Any) -> int | None:
    number = _parse_float(value)
    if number is None:
        return None
    return int(number)


def _split_action(action: Any) -> list[str]:
    text = str(action or "").strip()
    if not text:
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def _parse_action_value(action: Any, prefix: str) -> int | None:
    for part in _split_action(action):
        if not part.startswith(prefix):
            continue
        suffix = part[len(prefix) :].strip()
        if not suffix:
            return None
        return int(suffix)
    return None


def _kind_of(event: dict[str, Any]) -> str:
    command = event.get("command")
    if not isinstance(command, dict):
        return ""
    return str(command.get("kind") or "")


def _parse_event(event: dict[str, Any]) -> dict[str, Any]:
    command = event.get("command")
    params = event.get("params")
    toys = event.get("toys")

    if not isinstance(command, dict):
        raise ValueError("event.command must be a dict")
    if not isinstance(params, dict):
        raise ValueError("event.params must be a dict")
    if not isinstance(toys, dict):
        raise ValueError("event.toys must be a dict")

    action = params.get("action")
    thrusting = _parse_int(_parse_action_value(action, "Thrusting:"))
    depth = _parse_int(_parse_action_value(action, "Depth:"))
    if thrusting is None:
        raise ValueError("solace_thrusting missing Thrusting:<n> in params.action")
    if depth is None:
        raise ValueError("solace_thrusting missing Depth:<n> in params.action")

    return {
        "protocol": "solace_thrusting",
        "tsMs": int(event.get("tsMs") or 0),
        "eventId": str(event.get("eventId") or ""),
        "apiVer": int(command.get("apiVer") or 0),
        "toyIds": list(toys.get("ids") or []),
        "toyNames": list(toys.get("names") or []),
        "timeSec": float(_parse_float(params.get("timeSec")) or 0.0),
        "loopRunningSec": _parse_float(params.get("loopRunningSec")),
        "loopPauseSec": _parse_float(params.get("loopPauseSec")),
        "payload": {
            "action": str(action or ""),
            "thrusting": thrusting,
            "depth": depth,
        },
        "rawEvent": event,
    }


def onState(ctx, field, value, ts_ms=None):
    if field != "lovenseEvent":
        return
    if not isinstance(value, dict):
        return

    kind = _kind_of(value)
    ctx.set_state("lastKind", kind)
    if kind != "solace_thrusting":
        return

    parsed = _parse_event(value)
    ctx.set_state("lastParsed", parsed)
    ctx.emit("out", parsed)
```

## Script: `all_vibrate` Parser

Use this for `command.kind == "all_vibrate"`.

```python
from typing import Any


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    return float(text)


def _parse_int(value: Any) -> int | None:
    number = _parse_float(value)
    if number is None:
        return None
    return int(number)


def _kind_of(event: dict[str, Any]) -> str:
    command = event.get("command")
    if not isinstance(command, dict):
        return ""
    return str(command.get("kind") or "")


def _parse_all_from_action(action: Any) -> int | None:
    text = str(action or "").strip()
    if not text.startswith("All:"):
        return None
    return _parse_int(text.split("All:", 1)[1])


def _parse_event(event: dict[str, Any]) -> dict[str, Any]:
    command = event.get("command")
    params = event.get("params")
    toys = event.get("toys")

    if not isinstance(command, dict):
        raise ValueError("event.command must be a dict")
    if not isinstance(params, dict):
        raise ValueError("event.params must be a dict")
    if not isinstance(toys, dict):
        raise ValueError("event.toys must be a dict")

    action = params.get("action")
    strength = _parse_all_from_action(action)
    if strength is None:
        raise ValueError("all_vibrate missing All:<n> in params.action")

    return {
        "protocol": "all_vibrate",
        "tsMs": int(event.get("tsMs") or 0),
        "eventId": str(event.get("eventId") or ""),
        "apiVer": int(command.get("apiVer") or 0),
        "toyIds": list(toys.get("ids") or []),
        "toyNames": list(toys.get("names") or []),
        "timeSec": float(_parse_float(params.get("timeSec")) or 0.0),
        "loopRunningSec": _parse_float(params.get("loopRunningSec")),
        "loopPauseSec": _parse_float(params.get("loopPauseSec")),
        "payload": {
            "action": str(action or ""),
            "strength": strength,
        },
        "rawEvent": event,
    }


def onState(ctx, field, value, ts_ms=None):
    if field != "lovenseEvent":
        return
    if not isinstance(value, dict):
        return

    kind = _kind_of(value)
    ctx.set_state("lastKind", kind)
    if kind != "all_vibrate":
        return

    parsed = _parse_event(value)
    ctx.set_state("lastParsed", parsed)
    ctx.emit("out", parsed)
```

## Script: `vibration_pattern` Parser

Use this for `command.kind == "vibration_pattern"`.

```python
from typing import Any


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    return float(text)


def _kind_of(event: dict[str, Any]) -> str:
    command = event.get("command")
    if not isinstance(command, dict):
        return ""
    return str(command.get("kind") or "")


def _parse_strengths(value: Any) -> list[float]:
    text = str(value or "").strip()
    if not text:
        raise ValueError("vibration_pattern missing params.strength")

    values: list[float] = []
    for part in text.split(";"):
        item = part.strip()
        if not item:
            continue
        values.append(float(item))

    if not values:
        raise ValueError("vibration_pattern strength parsed as empty list")
    return values


def _parse_step_ms(rule: Any) -> float:
    text = str(rule or "").strip()
    if not text:
        raise ValueError("vibration_pattern missing params.rule")

    marker = "S:"
    index = text.find(marker)
    if index < 0:
        raise ValueError("vibration_pattern rule does not contain S:<ms>")

    suffix = text[index + len(marker) :]
    digits = ""
    for char in suffix:
        if char.isdigit():
            digits += char
            continue
        break

    if not digits:
        raise ValueError("vibration_pattern rule S:<ms> has no digits")

    step_ms = float(int(digits))
    if step_ms <= 0.0:
        raise ValueError("vibration_pattern stepMs must be > 0")
    return step_ms


def _parse_event(event: dict[str, Any]) -> dict[str, Any]:
    command = event.get("command")
    params = event.get("params")
    toys = event.get("toys")

    if not isinstance(command, dict):
        raise ValueError("event.command must be a dict")
    if not isinstance(params, dict):
        raise ValueError("event.params must be a dict")
    if not isinstance(toys, dict):
        raise ValueError("event.toys must be a dict")

    strengths = _parse_strengths(params.get("strength"))
    step_ms = _parse_step_ms(params.get("rule"))

    return {
        "protocol": "vibration_pattern",
        "tsMs": int(event.get("tsMs") or 0),
        "eventId": str(event.get("eventId") or ""),
        "apiVer": int(command.get("apiVer") or 0),
        "toyIds": list(toys.get("ids") or []),
        "toyNames": list(toys.get("names") or []),
        "timeSec": float(_parse_float(params.get("timeSec")) or 0.0),
        "payload": {
            "rule": str(params.get("rule") or ""),
            "stepMs": step_ms,
            "strengths": strengths,
        },
        "rawEvent": event,
    }


def onState(ctx, field, value, ts_ms=None):
    if field != "lovenseEvent":
        return
    if not isinstance(value, dict):
        return

    kind = _kind_of(value)
    ctx.set_state("lastKind", kind)
    if kind != "vibration_pattern":
        return

    parsed = _parse_event(value)
    ctx.set_state("lastParsed", parsed)
    ctx.emit("out", parsed)
```

## Script: `stop` Parser

Use this for `command.kind == "stop"`.

```python
from typing import Any


def _kind_of(event: dict[str, Any]) -> str:
    command = event.get("command")
    if not isinstance(command, dict):
        return ""
    return str(command.get("kind") or "")


def _parse_event(event: dict[str, Any]) -> dict[str, Any]:
    command = event.get("command")
    toys = event.get("toys")

    if not isinstance(command, dict):
        raise ValueError("event.command must be a dict")
    if not isinstance(toys, dict):
        raise ValueError("event.toys must be a dict")

    return {
        "protocol": "stop",
        "tsMs": int(event.get("tsMs") or 0),
        "eventId": str(event.get("eventId") or ""),
        "apiVer": int(command.get("apiVer") or 0),
        "toyIds": list(toys.get("ids") or []),
        "toyNames": list(toys.get("names") or []),
        "payload": {},
        "rawEvent": event,
    }


def onState(ctx, field, value, ts_ms=None):
    if field != "lovenseEvent":
        return
    if not isinstance(value, dict):
        return

    kind = _kind_of(value)
    ctx.set_state("lastKind", kind)
    if kind != "stop":
        return

    parsed = _parse_event(value)
    ctx.set_state("lastParsed", parsed)
    ctx.emit("out", parsed)
```

## Optional User Post-Process Stage

After the parser stage, the user can choose any policy they want. For example:

- map `solace_thrusting.payload.thrusting` to `ProgramWave.hz`
- map `solace_thrusting.payload.depth` to amplitude shaping
- map `all_vibrate.payload.strength` to a fixed-frequency wave
- map `vibration_pattern.payload.strengths` to a sequence player
- route different toys to different device outputs

That downstream stage can also be another `Python Script`. Example idea:

```python
from typing import Any
import math


def onMsg(ctx, inputs):
    msg = inputs.msg
    if not isinstance(msg, dict):
        return

    protocol = str(msg.get("protocol") or "")
    if protocol != "solace_thrusting":
        return

    payload = msg.get("payload")
    if not isinstance(payload, dict):
        return

    thrusting = float(payload.get("thrusting") or 0.0)
    depth = float(payload.get("depth") or 0.0)

    thrusting_max = 20.0
    depth_max = 20.0
    min_hz = 0.0
    max_hz = 3.0
    speed_gamma = 1.0

    thrust_norm = max(0.0, min(1.0, thrusting / thrusting_max))
    depth_norm = max(0.0, min(1.0, depth / depth_max))
    thrust_norm = math.pow(thrust_norm, speed_gamma)

    hz = min_hz + thrust_norm * (max_hz - min_hz)
    program = {
        "tsMs": int(msg.get("tsMs") or 0),
        "timeSec": float(msg.get("timeSec") or 0.0),
        "hz": hz,
    }
    return {
        "outputs": {
            "program": program,
            "amplitude": depth_norm,
        }
    }
```

This preserves the separation of concerns:

- parser script: understand Lovense message schema
- post-process script: decide behavior and mapping policy

## Validation Checklist

1. Start `Lovense Mock Server` and confirm `listening == true`.
2. Send a `Function` request with `Thrusting:x,Depth:y`.
3. Confirm only the `solace_thrusting` parser emits.
4. Confirm `lastParsed.payload.thrusting` and `lastParsed.payload.depth` are populated correctly.
5. Send a `Function` request with `All:n`.
6. Confirm only the `all_vibrate` parser emits.
7. Send a `Pattern` request.
8. Confirm `vibration_pattern.payload.strengths` is a numeric list and `stepMs` is parsed from `rule`.
9. Send `action=Stop`.
10. Confirm the `stop` parser emits a compact stop payload for downstream shutdown logic.

## Migration Note

The intent of this scene is to keep the main Lovense bridge workflow fully script-driven.

After this pattern is validated in real graphs, the dedicated adapter operator can be reduced or removed, because:

- `lovense_mock_server.py` already provides a stable ingress event
- `python_script.py` is a better extension point for protocol-specific parsing
- user post-processing should stay configurable rather than baked into a single bridge operator

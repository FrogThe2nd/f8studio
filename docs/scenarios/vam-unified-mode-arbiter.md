# VAM (4): Unified Mode Arbiter

This scene combines the three VAM branches:

- VAM (1) shaft branch: male/dildo reference to target.
- VAM (2) contact branch: female-female surface contact.
- VAM (3) self-motion branch: single-bone dance/fallback motion.

The unified graph should not force all branches to share one reference model.
They do not mean the same thing. Instead, each branch owns its own geometry and
then emits a compatible axis bus.

## Unified Graph

```mermaid
flowchart TB
    Skel["Skeleton Decoder.skeletons"] --> Shaft["VAM (1) Shaft Branch"]
    Skel --> Contact["VAM (2) Contact Branch"]
    Skel --> Self["VAM (3) Self-Motion Branch"]

    Shaft --> ShaftBus["shaftAxisBus"]
    Contact --> ContactBus["contactAxisBus"]
    Self --> SelfBus["selfAxisBus"]

    Shaft --> Arb["VAM Mode Arbiter"]
    Contact --> Arb
    Self --> Arb
    User["User Mode Control"] --> Arb

    Arb --> Router["VAM Axis Router"]
    ShaftBus --> Router
    ContactBus --> Router
    SelfBus --> Router

    Router --> Normalize["Shared Normalize / Shape"]
    Normalize --> TCode["TCode"]
```

The important contract is:

```text
branch-specific geometry -> branch-specific raw axes -> common axis bus
```

## Mode Strategy

Do not start with full automation. Use three stages:

| Stage | `modeControl` | Behavior |
| --- | --- | --- |
| Manual | `manual` | User selects `manualMode`. Most reliable while building the graph. |
| Assist | `assist` | Arbiter outputs `suggestedMode` and confidence, but does not switch the output. |
| Auto | `auto` | Arbiter switches with confidence margin and minimum hold time. |

Recommended default:

```text
modeControl = assist
manualMode = shaft
```

This keeps the user in control while showing what the automatic system would do.

## Branch Confidence

Each branch should output a small status object:

```json
{
  "valid": true,
  "confidence": 0.75,
  "reason": "branch-specific reason"
}
```

Suggested first confidence meanings:

| Branch | Valid when | Confidence means |
| --- | --- | --- |
| `shaft` | Reference and target are resolved. | Target is close to useful shaft geometry. |
| `contact` | Two contact-capable skeletons and selected bones are resolved. | Contact distance and/or slide activity are strong. |
| `self` | Selected bone exists and self-motion axes are valid. | One-bone residual activity is present. |

The `self` branch is the fallback. It should usually win only when `shaft` and
`contact` are invalid or low confidence.

## Common Axis Bus

Before the router, convert each branch to this shape:

```json
{
  "valid": true,
  "mode": "shaft",
  "confidence": 0.82,
  "L0": 0.51,
  "L1": 0.42,
  "L2": 0.58,
  "R0": 0.50,
  "R1": 0.50,
  "R2": 0.50,
  "reason": "..."
}
```

The values should already be normalized `0..1` if you want a single shared
TCode output chain. If you prefer branch-specific normalization, keep it inside
each branch and route only normalized values.

## `VAM Mode Arbiter`

Create a `Python Script` node named `VAM Mode Arbiter`.

Set `inputMode` to:

```text
raw_dict
```

Add data input ports:

| Port | Purpose |
| --- | --- |
| `shaftStatus` | VAM (1) status or axis bus. |
| `contactStatus` | VAM (2) status or axis bus. |
| `selfStatus` | VAM (3) status or axis bus. |

Add data output ports:

| Port | Purpose |
| --- | --- |
| `selectedMode` | `shaft`, `contact`, `self`, or `neutral`. |
| `suggestedMode` | Highest-confidence valid mode. |
| `status` | Full arbiter status. |

Add state fields:

| State field | Type | Default | Purpose |
| --- | --- | --- | --- |
| `modeControl` | string | `assist` | `manual`, `assist`, or `auto`. |
| `manualMode` | string | `shaft` | Used when `modeControl=manual` or `assist`. |
| `switchMargin` | number | `0.15` | New mode must beat current confidence by this much. |
| `minHoldMs` | number | `1500` | Minimum time to hold a selected auto mode. |
| `shaftMinConfidence` | number | `0.35` | Minimum usable shaft confidence. |
| `contactMinConfidence` | number | `0.35` | Minimum usable contact confidence. |
| `selfMinConfidence` | number | `0.10` | Minimum usable self confidence. |
| `fallbackMode` | string | `self` | `self`, `neutral`, or `hold`. |

Paste this script:

```python
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from f8_script_api import F8Inputs, F8PyEngineContext

MODES = ("shaft", "contact", "self")


def _state_text(ctx: "F8PyEngineContext", field: str, default: str) -> str:
    value = ctx.states.get(field, default)
    if value is None:
        return default
    return str(value).strip().lower()


def _state_float(ctx: "F8PyEngineContext", field: str, default: float) -> float:
    value = ctx.states.get(field, default)
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        try:
            return float(text)
        except ValueError:
            return default
    return default


def _now_ms(ts_ms: int | None) -> int:
    if ts_ms is None:
        return 0
    return int(ts_ms)


def _confidence(value: Any) -> float:
    if not isinstance(value, dict):
        return 0.0
    if value.get("valid") is not True:
        return 0.0
    raw = value.get("confidence", 1.0)
    if isinstance(raw, bool) or raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return max(0.0, min(1.0, float(raw)))
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return 0.0
        try:
            return max(0.0, min(1.0, float(text)))
        except ValueError:
            return 0.0
    return 0.0


def _valid_modes(ctx: "F8PyEngineContext", statuses: dict[str, Any]) -> dict[str, float]:
    minimums = {
        "shaft": _state_float(ctx, "shaftMinConfidence", 0.35),
        "contact": _state_float(ctx, "contactMinConfidence", 0.35),
        "self": _state_float(ctx, "selfMinConfidence", 0.10),
    }
    values: dict[str, float] = {}
    for mode in MODES:
        confidence = _confidence(statuses.get(mode))
        if confidence >= minimums[mode]:
            values[mode] = confidence
    return values


def _best_mode(values: dict[str, float]) -> tuple[str, float]:
    if not values:
        return "neutral", 0.0
    ordered = sorted(values.items(), key=lambda item: (-item[1], item[0]))
    return ordered[0][0], ordered[0][1]


def _select_auto(ctx: "F8PyEngineContext", values: dict[str, float], now_ms: int) -> tuple[str, str]:
    current_raw = ctx.locals.get("selected_mode")
    current = current_raw if isinstance(current_raw, str) else ""
    selected_since_raw = ctx.locals.get("selected_since_ms")
    selected_since = int(selected_since_raw) if isinstance(selected_since_raw, int) else now_ms
    best, best_confidence = _best_mode(values)
    if best == "neutral":
        fallback = _state_text(ctx, "fallbackMode", "self")
        if fallback == "hold" and current:
            return current, "auto fallback hold"
        if fallback in MODES and fallback in values:
            return fallback, f"auto fallback {fallback}"
        return "neutral", "auto no valid mode"

    if current not in values:
        ctx.locals["selected_since_ms"] = now_ms
        return best, f"auto selected {best}; previous invalid"

    min_hold_ms = max(0.0, _state_float(ctx, "minHoldMs", 1500.0))
    if now_ms > 0 and now_ms - selected_since < min_hold_ms:
        return current, f"auto hold {current}; minHoldMs"

    current_confidence = values[current]
    margin = max(0.0, _state_float(ctx, "switchMargin", 0.15))
    if best != current and best_confidence >= current_confidence + margin:
        ctx.locals["selected_since_ms"] = now_ms
        return best, f"auto switched {current}->{best}"
    return current, f"auto kept {current}"


def _run_arbiter(ctx: "F8PyEngineContext", inputs: dict[str, Any], ts_ms: int | None) -> dict[str, Any]:
    statuses = {
        "shaft": inputs.get("shaftStatus"),
        "contact": inputs.get("contactStatus"),
        "self": inputs.get("selfStatus"),
    }
    values = _valid_modes(ctx, statuses)
    suggested_mode, suggested_confidence = _best_mode(values)
    mode_control = _state_text(ctx, "modeControl", "assist")
    manual_mode = _state_text(ctx, "manualMode", "shaft")
    now_ms = _now_ms(ts_ms)

    if mode_control == "manual":
        selected = manual_mode if manual_mode in MODES else "neutral"
        reason = f"manual {selected}"
    elif mode_control == "assist":
        selected = manual_mode if manual_mode in MODES else "neutral"
        reason = f"assist selected manual {selected}; suggested {suggested_mode}"
    elif mode_control == "auto":
        selected, reason = _select_auto(ctx, values, now_ms)
    else:
        selected = manual_mode if manual_mode in MODES else "neutral"
        reason = f"unknown modeControl; using manualMode {selected}"

    ctx.locals["selected_mode"] = selected
    status = {
        "valid": selected in MODES,
        "modeControl": mode_control,
        "selectedMode": selected,
        "suggestedMode": suggested_mode,
        "selectedConfidence": float(values.get(selected, 0.0)),
        "suggestedConfidence": float(suggested_confidence),
        "shaftConfidence": float(values.get("shaft", 0.0)),
        "contactConfidence": float(values.get("contact", 0.0)),
        "selfConfidence": float(values.get("self", 0.0)),
        "reason": reason,
    }
    return {"selectedMode": selected, "suggestedMode": suggested_mode, "status": status}


def onStart(ctx: "F8PyEngineContext") -> None:
    ctx.log("VAM Mode Arbiter started")


def onMsg(ctx: "F8PyEngineContext", inputs: "F8Inputs") -> dict[str, Any]:
    outputs = _run_arbiter(ctx, inputs, None)
    return {"outputs": outputs}


def onExec(ctx: "F8PyEngineContext", exec_in: str, inputs: "F8Inputs") -> dict[str, Any]:
    ts_ms_raw = inputs.get("tickMs")
    ts_ms = int(ts_ms_raw) if isinstance(ts_ms_raw, (int, float)) else None
    outputs = _run_arbiter(ctx, inputs, ts_ms)
    return {"exec": ["exec"], "outputs": outputs}


def onStop(ctx: "F8PyEngineContext") -> None:
    ctx.log("VAM Mode Arbiter stopped")
```

## `VAM Axis Router`

Create another `Python Script` node named `VAM Axis Router`.

Set `inputMode` to:

```text
raw_dict
```

Add data input ports:

| Port | Purpose |
| --- | --- |
| `selectedMode` | From `VAM Mode Arbiter.selectedMode`. |
| `shaftAxes` | Normalized axis bus from VAM (1). |
| `contactAxes` | Normalized axis bus from VAM (2). |
| `selfAxes` | Normalized axis bus from VAM (3). |

Add data output ports:

| Port | Purpose |
| --- | --- |
| `L0` | Routed normalized axis. |
| `L1` | Routed normalized axis. |
| `L2` | Routed normalized axis. |
| `R0` | Routed normalized axis. |
| `R1` | Routed normalized axis. |
| `R2` | Routed normalized axis. |
| `axes` | Combined routed object. |
| `status` | Routing status. |

Paste this script:

```python
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from f8_script_api import F8Inputs, F8PyEngineContext

AXES = ("L0", "L1", "L2", "R0", "R1", "R2")


def _number(value: Any, default: float) -> float:
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        try:
            return max(0.0, min(1.0, float(text)))
        except ValueError:
            return default
    return default


def _axis_bus(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _select_bus(inputs: dict[str, Any], mode: str) -> tuple[dict[str, Any], str]:
    if mode == "shaft":
        return _axis_bus(inputs.get("shaftAxes")), "shaft"
    if mode == "contact":
        return _axis_bus(inputs.get("contactAxes")), "contact"
    if mode == "self":
        return _axis_bus(inputs.get("selfAxes")), "self"
    return {}, "neutral"


def _run_router(ctx: "F8PyEngineContext", inputs: dict[str, Any]) -> dict[str, Any]:
    del ctx
    mode_raw = inputs.get("selectedMode")
    mode = str(mode_raw or "neutral").strip().lower()
    bus, selected = _select_bus(inputs, mode)
    valid = bool(bus.get("valid") is True)
    axes: dict[str, Any] = {"valid": valid, "mode": selected, "reason": str(bus.get("reason", ""))}
    outputs: dict[str, Any] = {}
    for axis in AXES:
        value = _number(bus.get(axis), 0.5)
        axes[axis] = value
        outputs[axis] = value
    outputs["axes"] = axes
    outputs["status"] = {"valid": valid, "selectedMode": selected, "reason": axes["reason"]}
    return outputs


def onStart(ctx: "F8PyEngineContext") -> None:
    ctx.log("VAM Axis Router started")


def onMsg(ctx: "F8PyEngineContext", inputs: "F8Inputs") -> dict[str, Any]:
    outputs = _run_router(ctx, inputs)
    return {"outputs": outputs}


def onExec(ctx: "F8PyEngineContext", exec_in: str, inputs: "F8Inputs") -> dict[str, Any]:
    outputs = _run_router(ctx, inputs)
    return {"exec": ["exec"], "outputs": outputs}


def onStop(ctx: "F8PyEngineContext") -> None:
    ctx.log("VAM Axis Router stopped")
```

## Building Axis Buses

Each branch can use a small `Data Expr` or Python Script to pack normalized
outputs into the common bus. The shape should be:

```python
{
    "valid": True,
    "mode": "self",
    "confidence": confidence,
    "L0": L0,
    "L1": L1,
    "L2": L2,
    "R0": R0,
    "R1": R1,
    "R2": R2,
    "reason": "self fallback active",
}
```

Keep the bus values normalized `0..1`. This makes the router boring and keeps
branch-specific normalization inside each branch.

## Recommended Rollout

1. Build VAM (1), VAM (2), and VAM (3) independently.
2. Add the arbiter in `assist` mode and observe `suggestedMode`.
3. Add the axis router, still using `manual` or `assist`.
4. Enable `auto` only after the confidence values look stable.

Do not let three branches write directly to the same TCode node. Let exactly one
branch pass through the router.

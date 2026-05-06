# Scene 06: VAM Modular Pose Pipeline

This scene is a design guide for the VAM pose pipeline in `PyStudio`. The graph
uses explicit stages for pose resolution, relative pose output, axis extraction,
normalization, signal shaping, and final TCode formatting.

Recommended pipeline:

```text
Skeletons -> Pose Resolver -> Relative Pose(s) -> Axis Extraction -> Normalization -> Signal Shaping -> TCode
```

The first script operator is `VAM Pose Resolver`. It analyzes streamed
skeletons, decides which reference and target are active, and emits explicit
pose objects that downstream operators can inspect and transform.

## Design Principle

The PyStudio graph uses these explicit roles:

- `VAM Pose Resolver` resolves a reference frame and a target frame.
- `VAM Pose Axes` or small `Data Expr` nodes project pose into semantic axes.
- `Range Map`, `Smooth Filter`, `Rate Limiter`, `Switch Mixer`, and similar
  nodes handle output policy.
- `TCode` assembles final TCode command strings.

That keeps each stage small, replaceable, and easy to debug.

## Recommended Graph

The recommended graph shape is:

```text
UDP In.packet -> Skeleton Decoder.packet
Skeleton Decoder.skeletons -> VAM Pose Resolver.skeletons
Tick.exec -> VAM Pose Resolver.exec

VAM Pose Resolver.referenceFrame -> Patch Hub.referenceFrame
VAM Pose Resolver.targetWorldBone -> Patch Hub.targetWorldBone
VAM Pose Resolver.targetInReference -> Patch Hub.targetInReference
VAM Pose Resolver.targetInPlane -> Patch Hub.targetInPlane

Patch Hub.* -> optional Bone Filter / debug visualizers
Patch Hub.* -> VAM Pose Axes or Data Expr scalar extractors

Pose scalar outputs -> Range Map nodes
Range Map outputs -> Smooth Filter / Rate Limiter / Switch Mixer
Processed L0/L1/L2/R0/R1/R2 -> TCode -> Serial Out or UDP Out
```

`Patch Hub` is optional, but useful once the same pose outputs feed several
debug, analysis, and output branches.

### Minimal V1 Graph

For a first working scene:

```text
UDP In -> Skeleton Decoder -> Python Script: VAM Pose Resolver
VAM Pose Resolver -> Python Script: VAM Pose Axes
VAM Pose Axes -> Range Map x6 -> Smooth Filter x6 -> Rate Limiter x6 -> TCode -> Serial Out
```

This keeps the custom Python limited to two explicit tasks:

1. Pick and reconstruct pose.
2. Convert pose into semantic axis signals.

Everything after that is regular graph signal processing.

## Operator Roles

| Operator | Role in the modular design |
| --- | --- |
| `UDP In` | Receives UDP packets from `ignore/Feel8.SkeletonStreamer`. |
| `Skeleton Decoder` | Decodes streamed skeleton packets, handles keys, and keeps latest skeletons. |
| `Python Script: VAM Pose Resolver` | Selects reference/target streams and emits structured pose objects for the next layer. |
| `Patch Hub` | Optional canvas routing node for fan-out and cleaner wiring. |
| `Bone Filter` | Optional smoothing for `targetWorldBone`, `targetInReference`, or `targetInPlane`. Useful before scalar extraction if skeleton jitter is visible. |
| `Python Script: VAM Pose Axes` | Optional small script for exact ToySerialController-style signed-angle math. This is separate from resolver selection logic. |
| `Data Expr` | Good for simple scalar extraction such as `x["pos"][1]`, simple clamps, or offsets. Use a script once the formula stops being a small expression. |
| `Quat To Euler` | Operator-friendly rotation decomposition for relative quaternions. Good for a v1 approximation or debugging. Use `VAM Pose Axes` for signed-angle mapping. |
| `Range Map` | Converts raw meters, degrees, or signed normalized values into final `0..1` channel values. |
| `Smooth Filter` | Replaces ToySerialController's global smoothing with per-channel smoothing. |
| `Rate Limiter` | Adds per-channel slew and acceleration limits before device output. |
| `Switch Mixer` | Handles manual override, fallback poses, or alternate mappings without changing the resolver. |
| `Silence Detector` | Detects stale branches and can drive fallback or hold behavior. |
| `TCode` | Final formatter. It expects normalized `0..1` axes and outputs command strings. |
| `Serial Out` / `UDP Out` | Physical or network transport for the TCode string. |

## Layer Contract

The graph is easier to reason about if every layer has a narrow contract.

| Layer | Input | Output | Owns |
| --- | --- | --- | --- |
| Ingest | UDP packet | decoded skeleton cache | network and binary format |
| Pose resolver | skeleton cache | reference frame, target world bone, relative pose bones | target selection and frame reconstruction |
| Pose analysis | relative pose bones | raw semantic axes | geometry projection and rotation math |
| Normalization | raw axes | `0..1` channels | user ranges, inversion, offsets |
| Signal shaping | `0..1` channels | processed `0..1` channels | smoothing, rate limits, overrides, fallback |
| Output | processed `0..1` channels | TCode string | command packaging and transport |

The resolver should stay intentionally boring: no curves, no serial output, no
TCode string assembly, and no hidden device policy.

## Pose Resolver Script

Use one `Python Script` node as `VAM Pose Resolver`.

### Data Ports

Data in:

- `skeletons`

Data out:

- `referenceFrame`
- `targetWorldBone`
- `targetInReference`
- `targetInPlane`
- `status`
- `debug`

`status` should be a small object with fields such as `valid`, `referenceKey`,
`targetKey`, `targetBone`, and `reason`. `debug` can carry richer inspection
data, but downstream logic should use the named contract ports above.

### State Fields

Writable state:

| Name | Type | Purpose |
| --- | --- | --- |
| `trackingMode` | `auto | manual` | Select automatic target picking or manual binding. |
| `referenceKey` | `string` | Manual reference skeleton key. |
| `targetKey` | `string` | Manual target skeleton key. |
| `targetBone` | `string` | Manual target bone when `trackingMode=manual`. |
| `referenceBasis` | `reference | plane` | Default rotation basis preference for downstream hints. |
| `referenceRadiusHint` | `number` | Synthetic radius in meters until the stream exposes real radius. |
| `targetUpOffset` | `number` | Optional offset along target local up, matching ToySerialController target offset behavior. |
| `targetForwardOffset` | `number` | Optional offset along target local forward. |
| `autoVagina` | `boolean` | Enable `Vagina` as an auto target. |
| `autoMouth` | `boolean` | Enable `Mouth` as an auto target. |
| `autoAnus` | `boolean` | Enable `Anus` as an auto target. |
| `autoLeftHand` | `boolean` | Enable `LeftHand` as an auto target. |
| `autoRightHand` | `boolean` | Enable `RightHand` as an auto target. |
| `autoFeet` | `boolean` | Enable `Feet` as an auto target. |
| `autoChest` | `boolean` | Enable `Chest` when available. |

Read-only diagnostic state:

| Name | Type | Purpose |
| --- | --- | --- |
| `availableReferenceKeys` | `string[]` | Skeleton keys that can produce a reference frame. |
| `availableTargetKeys` | `string[]` | Skeleton keys with at least one enabled target bone. |
| `availableTargetBones` | `string[]` | Target bones available for the current target stream. |
| `lockedReferenceKey` | `string` | Reference key actually used this tick. |
| `lockedTargetKey` | `string` | Target key actually used this tick. |
| `lockedTargetBone` | `string` | Target bone actually used this tick. |
| `lastPickReason` | `string` | Short explanation of the latest selection. |

### Output Shape

Keep the resolver outputs plain, explicit dictionaries. The shape should be
stable enough that downstream `Data Expr` and script nodes can rely on it.

`referenceFrame`:

```python
{
    "valid": True,
    "key": "Dildo",
    "schema": "VAMDildo@1.0",
    "pos": [x, y, z],
    "rot": [w, x, y, z],
    "up": [x, y, z],
    "right": [x, y, z],
    "forward": [x, y, z],
    "length": 0.18,
    "radius": None,
    "radiusHint": 0.03,
    "planeNormal": [x, y, z],
    "planeTangent": [x, y, z],
    "planeForward": [x, y, z],
}
```

`targetWorldBone`:

```python
{
    "valid": True,
    "name": "Vagina",
    "key": "Female",
    "schema": "VAMFemale@1.0",
    "pos": [x, y, z],
    "rot": [w, x, y, z],
    "up": [x, y, z],
    "right": [x, y, z],
    "forward": [x, y, z],
}
```

`targetInReference`:

```python
{
    "valid": True,
    "name": "TargetInReference",
    "referenceKey": "Dildo",
    "targetKey": "Female",
    "targetBone": "Vagina",
    "pos": [axisMeters, forwardMeters, rightMeters],
    "rot": [w, x, y, z],
}
```

`targetInPlane`:

```python
{
    "valid": True,
    "name": "TargetInPlane",
    "referenceKey": "Dildo",
    "targetKey": "Female",
    "targetBone": "Vagina",
    "pos": [normalMeters, planeForwardMeters, planeTangentMeters],
    "rot": [w, x, y, z],
}
```

The two relative bones are intentionally separate:

- `targetInReference` answers "where and how is the target relative to the
  reference shaft frame?"
- `targetInPlane` answers "where and how is the target relative to the
  reference plane frame?"

This gives downstream nodes both useful bases directly.

### Selection Behavior

The resolver should implement this behavior:

- A reference candidate must contain `ReferenceStart`, `ReferenceEnd`,
  `PlaneStart`, and `PlaneEnd`.
- A target candidate must contain at least one enabled target bone.
- Auto mode picks the enabled target bone with the smallest world-space
  distance to the active reference position.
- Manual mode uses `referenceKey`, `targetKey`, and `targetBone` exactly.
- If the manual selection is invalid, emit `status.valid=False` with a reason
  and keep the selection explicit.
- Do not output stale pose objects as valid. If a pose cannot be resolved,
  emit objects with `valid=False`.

The useful target bones from the current streamer are:

- `Vagina`
- `Mouth`
- `Anus`
- `LeftHand`
- `RightHand`
- `LeftFoot`
- `RightFoot`
- `Feet`
- `Chest`
- `LeftNipple`
- `RightNipple`

For auto mode, start with the ToySerialController-like subset:

- `Vagina`
- `Mouth`
- `Anus`
- `LeftHand`
- `RightHand`
- `Feet`
- `Chest`

## Source And Target Support

Current `ignore/Feel8.SkeletonStreamer` schemas:

| Schema | Reference | Target | Notes |
| --- | --- | --- | --- |
| `VAMMale@1.0` | Yes | Yes | Emits interaction bones and reference-frame bones. |
| `VAMFemale@1.0` | No | Yes | Emits rich target bones, but no reference-frame bones in the current streamer. |
| `VAMDildo@1.0` | Yes | No | Emits reference-frame bones only. |
| `CustomUnityAsset` | No | No | The current streamer does not expose asset reference or target data. |

Supported v1 pairings:

- `VAMDildo@1.0` reference -> `VAMFemale@1.0` target
- `VAMDildo@1.0` reference -> `VAMMale@1.0` target
- `VAMMale@1.0` reference -> `VAMFemale@1.0` target
- `VAMMale@1.0` reference -> `VAMMale@1.0` target

Unsupported until the streamer grows richer data:

- `VAMFemale@1.0` as a reference
- `CustomUnityAsset` as a reference or target
- Exact reference radius from ToySerialController

## Frame Reconstruction

The current `ignore/Feel8.SkeletonStreamer` emits F8 canonical right-handed
coordinates. The canonical frame is Three/Y-up:

- `+Y` is up.
- Unity/VAM `X` and `Y` are preserved.
- Unity/VAM `Z` is mirrored before the packet is sent.
- Unity/VAM rotation `[w, x, y, z]` is converted to `[w, -x, -y, z]`.
- Schema names stay `VAMMale@1.0`, `VAMFemale@1.0`, and `VAMDildo@1.0`.
- `3D Viz.worldUp` should stay `+y`; it controls the viewer/camera up axis.

The streamed bone format is:

```text
pos = [x, y, z]
rot = [w, x, y, z]
```

Keep that convention in every pose object. `rot` is already transformed into
the canonical frame by the streamer.

### Reference Frame

Reconstruct the reference frame from streamer bones:

```text
ref_pos = ReferenceStart.pos
ref_up = normalize(ReferenceEnd.pos - ReferenceStart.pos)
ref_length = length(ReferenceEnd.pos - ReferenceStart.pos)
ref_right = normalize(PlaneEnd.pos - PlaneStart.pos)
ref_forward = normalize(cross(ref_right, ref_up))
plane_normal = normalize(cross(ref_up, ref_right))
plane_tangent = ref_right
plane_forward = normalize(cross(plane_tangent, plane_normal))
```

Then build two rotation bases:

```text
reference_rot = rotation_from_basis(up=ref_up, right=ref_right, forward=ref_forward)
plane_rot = rotation_from_basis(up=plane_normal, right=plane_tangent, forward=plane_forward)
```

### Target Frame

Reconstruct the target frame from the selected target bone:

```text
target_pos = target_bone.pos
target_rot = target_bone.rot
target_right = rotate(target_rot, [1, 0, 0])
target_up = rotate(target_rot, [0, 1, 0])
target_forward = rotate(target_rot, [0, 0, 1])
```

Apply target offsets before emitting `targetWorldBone`:

```text
target_pos = target_pos + target_up * targetUpOffset + target_forward * targetForwardOffset
```

### Relative Pose Bones

For `targetInReference.pos`:

```text
diff = target_pos - ref_pos
axisMeters = clamp(dot(diff, ref_up), 0, ref_length)

diffOnPlane = project_on_plane(diff, plane_normal)
forwardMeters = dot(diffOnPlane, ref_forward)
rightMeters = dot(diffOnPlane, ref_right)
```

For `targetInReference.rot`:

```text
targetInReference.rot = inverse(reference_rot) * target_rot
```

For `targetInPlane.pos`:

```text
normalMeters = dot(diff, plane_normal)
planeForwardMeters = dot(diff, plane_forward)
planeTangentMeters = dot(diff, plane_tangent)
```

For `targetInPlane.rot`:

```text
targetInPlane.rot = inverse(plane_rot) * target_rot
```

The resolver can also emit `status.distanceToAxis`:

```text
closestPoint = ref_pos + ref_up * axisMeters
distanceToAxis = length(target_pos - closestPoint)
```

That is enough for optional collision or proximity gating downstream.

## Pose Axis Extraction

This layer converts relative pose objects into semantic axis signals. It should
be separate from `VAM Pose Resolver`.

Two approaches are useful:

1. Use a small `Python Script: VAM Pose Axes` for exact vector math.
2. Use `Data Expr`, `Quat To Euler`, and `Range Map` for simple inspectable
   approximations.

### Position Axes

From `targetInReference`:

```text
axisMeters = targetInReference.pos[0]
forwardMeters = targetInReference.pos[1]
rightMeters = targetInReference.pos[2]
```

Raw semantic position axes:

```text
L0_geom = 1 - clamp01(axisMeters / referenceFrame.length)
L1_m = forwardMeters
L2_m = rightMeters
```

`L0_geom` is already normalized because it is a fraction of reference length.
`L1_m` and `L2_m` stay in meters until the normalization layer.

### Rotation Axes

For exact ToySerialController-style rotation, keep the signed-angle math in
`VAM Pose Axes`:

```text
Target-Reference basis:
  basis_up = referenceFrame.up
  basis_right = referenceFrame.right
  basis_forward = referenceFrame.forward

Target-Plane basis:
  basis_up = referenceFrame.planeNormal
  basis_right = referenceFrame.planeTangent
  basis_forward = referenceFrame.planeForward
```

Then compute:

```text
corrected_right = project_on_plane(targetWorldBone.right, basis_up)
if dot(corrected_right, basis_right) < 0:
    corrected_right = corrected_right - 2 * project(corrected_right, basis_right)

R0_turns = signed_angle(basis_right, corrected_right, basis_up) / pi
R1_turns = -signed_angle(basis_up, project_on_plane(targetWorldBone.up, basis_forward), basis_forward) / (pi / 2)
R2_turns = signed_angle(basis_up, project_on_plane(targetWorldBone.up, basis_right), basis_right) / (pi / 2)
```

`R0_turns`, `R1_turns`, and `R2_turns` are raw signed normalized rotation
signals.

For a simpler v1 graph, use:

```text
targetInReference.rot -> Quat To Euler -> Data Expr selectors -> Range Map
```

For signed-angle mapping, use `VAM Pose Axes`.

## Normalization

Normalize raw axes into `0..1` before shaping and TCode.

Recommended default ranges mirror ToySerialController's UI defaults:

| Axis | Raw value | Default range | Normalized formula |
| --- | --- | --- | --- |
| `L0` | `L0_geom` | `0..1` | `L0_norm = clamp01(L0_geom)` |
| `L1` | `L1_m` | `-0.15..0.15` meters | `L1_norm = clamp01(0.5 + L1_m / (2 * 0.15))` |
| `L2` | `L2_m` | `-0.15..0.15` meters | `L2_norm = clamp01(0.5 + L2_m / (2 * 0.15))` |
| `R0` | twist angle | `-90..90` deg | `R0_norm = clamp01(0.5 + twistDeg / (2 * 90))` |
| `R1` | roll angle | `-30..30` deg | `R1_norm = clamp01(0.5 + rollDeg / (2 * 30))` |
| `R2` | pitch angle | `-30..30` deg | `R2_norm = clamp01(0.5 + pitchDeg / (2 * 30))` |

Use `Range Map` nodes for these linear mappings:

- `L0`: `inMin=0`, `inMax=1`, `outMin=0`, `outMax=1`
- `L1`: `inMin=-0.15`, `inMax=0.15`, `outMin=0`, `outMax=1`
- `L2`: `inMin=-0.15`, `inMax=0.15`, `outMin=0`, `outMax=1`
- `R0`: `inMin=-90`, `inMax=90`, `outMin=0`, `outMax=1` if using degrees
- `R1`: `inMin=-30`, `inMax=30`, `outMin=0`, `outMax=1` if using degrees
- `R2`: `inMin=-30`, `inMax=30`, `outMin=0`, `outMax=1` if using degrees

Invert an axis by swapping `outMin` and `outMax`.

Use output limits by narrowing `outMin/outMax`, for example `0.15..0.85`.

Use offsets either before `Range Map` with `Data Expr`, or after `Range Map`
with a tiny expression such as:

```python
max(0.0, min(1.0, x + offset))
```

## Signal Shaping Before TCode

After normalization, treat `L0_norm`, `L1_norm`, and friends as regular
signals. This layer owns per-axis tuning before final TCode output.

Recommended per-axis chain:

```text
Range Map.value -> Smooth Filter.value -> Rate Limiter.value -> TCode.<axis>
```

Useful variants:

- Put `Smooth Filter` before `Range Map` when you want to smooth raw meters or
  angles.
- Put `Smooth Filter` after `Range Map` when you want device-space smoothing.
- Put `Rate Limiter` after all user mixing so the final command cannot jump.
- Use `Switch Mixer` after normalization for manual overrides or fallback.
- Use `Silence Detector` on `status.valid`, raw axes, or normalized axes to
  hold the last valid value when tracking drops.

This is also the right place for future features:

- dead zones
- nonlinear curves
- per-axis output ceilings
- per-axis inversion
- hysteresis around auto target changes
- collision hold/release behavior
- blend between auto and manual mappings

Keep these features in the signal-shaping layer.

## Collision And Proximity

The current streamer does not expose the real reference radius. Use
`referenceRadiusHint` for proximity gating.

In this modular design:

- The resolver emits `status.distanceToAxis`.
- The resolver emits `referenceFrame.radiusHint`.
- A downstream `Data Expr` or small script computes `isNearReference`.
- `Switch Mixer` or `Silence Detector` decides whether to hold, release, or
  fade outputs.

Recommended v1 default:

```text
collision/proximity gating = disabled
```

Add it later as a separate branch once pose tracking is visually correct.

## TCode Output

Only feed `TCode` normalized values in `0..1`.

Suggested naming:

- `L0_geom`: geometry-derived, before final mapping.
- `L0_norm`: mapped to `0..1`.
- `L0_cmd`: final post-processed value connected to `TCode.L0`.

Example:

```text
VAM Pose Axes.L0_geom
  -> Range Map L0.value
  -> Smooth Filter L0.value
  -> Rate Limiter L0.value
  -> TCode.L0
```

The same pattern applies to `L1`, `L2`, `R0`, `R1`, and `R2`.

`TCode.intervalMs` should be driven by the same cadence as the pose graph. For
example:

```text
Tick.tickMs -> TCode.intervalMs
```

## Graph-First Script Boundary

Keep Python scripts focused and explicit:

- `VAM Pose Resolver` selects actors and emits relative pose objects.
- `VAM Pose Axes` converts relative pose objects into raw semantic axes.
- `Range Map`, `Smooth Filter`, `Rate Limiter`, `Switch Mixer`, and `TCode`
  own shaping and output behavior.

This keeps the graph inspectable:

- You can inspect the selected reference and target before any mapping.
- You can visualize relative pose before converting it to axes.
- You can swap the rotation strategy without touching target selection.
- You can tune ranges, smoothing, and rate limits live in ordinary nodes.
- You can branch the same pose into multiple devices or visualizers.
- You can test each layer with simple known inputs.

The resolver script remains small enough to promote into a dedicated operator
later, if the pattern proves stable.

## Validation Checklist

Use this checklist when bringing the graph up:

- Confirm `UDP In.packet -> Skeleton Decoder.packet` produces live skeletons.
- Confirm `Skeleton Decoder.availableKeys` updates as actors appear and
  disappear.
- Confirm `VAM Pose Resolver.availableReferenceKeys` includes male or dildo
  streams with reference bones.
- Confirm `VAM Pose Resolver.availableTargetKeys` includes target person
  streams with enabled target bones.
- Confirm `trackingMode=auto` picks a reasonable nearest target and writes
  `lockedReferenceKey`, `lockedTargetKey`, and `lockedTargetBone`.
- Confirm `trackingMode=manual` emits invalid status when the chosen key or
  bone is missing.
- Confirm `targetWorldBone`, `targetInReference`, and `targetInPlane` can be
  inspected independently.
- Confirm `targetInReference.pos[0]` changes along the reference shaft.
- Confirm `targetInReference.pos[1]` and `targetInReference.pos[2]` change with
  forward/right offsets.
- Confirm rotation extraction is stable before any `Range Map`.
- Confirm each `Range Map` output stays inside `0..1`.
- Confirm smoothing and rate limiting are applied before `TCode`.
- Confirm `TCode` receives only final normalized command values.

## Implementation Notes

Keep resolver and axis scripts explicit:

- Use named helper functions for vector, quaternion, and pose parsing.
- Use explicit dictionary keys such as `bone["pos"]` and `frame["length"]`.
- Do not hide known fields behind dynamic attribute access.
- Return `valid=False` objects for missing data, with a clear `reason`.
- Let real script errors surface through the Python Script monitor and avoid
  swallowing them.

The scene pulls useful VAM pose geometry into a graph-first pipeline where
selection, relative pose, normalization, shaping, and output are all separate,
inspectable steps.

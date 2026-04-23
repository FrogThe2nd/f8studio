# Scene 06: VAM Pose Tracking

This scene is a design-and-implementation guide for rebuilding the core pose-tracking logic of `ToySerialController` inside `PyStudio` with existing `f8.pyengine` operators.

## Goal

Use `ignore/ToySerialController` as the behavior reference, `ignore/Feel8.SkeletonStreamer` as the VaM-to-UDP bridge, and `PyStudio/PyEngine` as the reimplementation layer.

The v1 goal is intentionally limited:

- Reconstruct the reference/target pose relationship from streamed skeleton data.
- Lock a target automatically or manually inside a `Python Script` node.
- Emit raw semantic pose axes `L0`, `L1`, `L2`, `R0`, `R1`, `R2`.
- Hand off later smoothing, normalization, switching, visualization, and optional `TCode` packaging to downstream nodes.

## Verdict

The current operator set is enough for a v1 VAM integration in `PyStudio` without adding a new runtime operator.

The recommended graph is:

```text
UDP In.packet -> Skeleton Decoder.packet
Skeleton Decoder.skeletons -> Python Script.skeletons
Tick.exec -> Python Script.exec
Python Script.(L0/L1/L2/R0/R1/R2)
  -> optional filters / mappers / mixers / visualizers
  -> optional TCode
```

This is a graph-first, raw-signal-first approach.

- `Skeleton Decoder` already solves packet ingest, decode, key selection, and chunk reassembly.
- `Python Script` is the only existing operator that can combine multi-skeleton auto/manual locking, persistent state, editable ports, editable state fields, and six-output pose math in one place.
- Supporting operators still matter, but they do not replace the core solver.

## Operator Fit Analysis

| Operator | Verdict | Role in this scene |
| --- | --- | --- |
| `UDP In` | Required | Receives `Feel8.SkeletonStreamer` UDP packets. Keep the chain on `UDP In.packet`, not ad-hoc payload parsing, so the decoder gets the expected packet contract. |
| `Skeleton Decoder` | Required | Decodes binary skeleton payloads, keeps the latest skeleton per key, exposes `availableKeys`, and reassembles chunked frames. |
| `Python Script` | Core solver | Best fit for target locking, reference/target pairing, stateful auto/manual selection, and `L0..R2` math. |
| `Bone Selector` | Debug tool | Useful for manually inspecting one skeleton bone at a time. Not enough for multi-stream reference/target pairing by itself. |
| `Bone Filter` | Debug and prep tool | Useful for stabilizing a chosen bone branch before deriving scalar signals. It does not solve target selection or reference-frame reconstruction. |
| `Quat To Euler` | Debug tool | Useful for visualization and sanity-checking rotations. Not a substitute for `R0/R1/R2` because `ToySerialController` uses signed-angle math against custom bases, not plain Euler output. |
| `Data Expr` | Not primary | Too small and stateless for multi-step vector/quaternion math plus actor locking. |
| `State Expr` | Not primary | Good for lightweight derived UI parameters, not for the main solver. |
| `Switch Mixer` | Optional downstream | Useful for manual override vs auto, primary vs fallback, or soft switching between processed channels after the solver. |
| `Silence Detector` | Optional downstream | Useful for detecting stale or inactive scalar branches and triggering fallback logic without changing the solver. |
| `TCode` | Optional final formatter | Converts already-normalized axes into a TCode string. Raw `L0..R2` should usually be mapped first. |

## Recommended Graph

The recommended v1 graph is:

```text
UDP In.packet -> Skeleton Decoder.packet
Skeleton Decoder.skeletons -> Python Script.skeletons
Tick.exec -> Python Script.exec
Tick.tickMs -> optional downstream timing inputs

Python Script.L0 -> optional Range Map / visualizer
Python Script.L1 -> optional Range Map / visualizer
Python Script.L2 -> optional Range Map / visualizer
Python Script.R0 -> optional Range Map / visualizer
Python Script.R1 -> optional Range Map / visualizer
Python Script.R2 -> optional Range Map / visualizer

Mapped channels -> optional Switch Mixer / Silence Detector -> TCode -> Serial Out / Viz
```

Why `Skeleton Decoder.skeletons` instead of `selectedSkeleton`:

- `selectedSkeleton` is good when the graph only follows one already-known model.
- This scene needs the full active model set because the solver must auto-pick or manually bind both a reference stream and a target stream.

Recommended execution model:

- Drive the `Python Script` node from `Tick.exec` so the graph has a deterministic evaluation cadence.
- Treat the script as the stateful pose solver.
- Treat everything after the script as downstream signal shaping or output routing.

## Supported Source And Target Combinations

Current `Feel8.SkeletonStreamer` support is:

| Schema | Current role in scene | Notes |
| --- | --- | --- |
| `VAMMale@1.0` | Reference or target | Includes both interaction bones and reference-frame bones. |
| `VAMFemale@1.0` | Target only | Includes interaction bones but no `ReferenceStart/ReferenceEnd/PlaneStart/PlaneEnd` in the current streamer. |
| `VAMDildo@1.0` | Reference only | Current toy path in scope. Provides reference-frame bones, not interaction bones. |
| `CustomUnityAsset` | Unsupported | `ToySerialController` supports asset workflows, but `Feel8.SkeletonStreamer` does not currently stream an asset parser. |

Practical v1 pairings that are supported now:

- `VAMMale@1.0` reference -> `VAMFemale@1.0` target
- `VAMMale@1.0` reference -> `VAMMale@1.0` target
- `VAMDildo@1.0` reference -> `VAMFemale@1.0` target
- `VAMDildo@1.0` reference -> `VAMMale@1.0` target

Practical v1 pairings that are not supported now:

- `VAMFemale@1.0` as the reference stream
- Any `CustomUnityAsset` reference or target path

## Python Script Contract

For this scene, the `Python Script` node is the v1 core. Configure it explicitly as follows.

### Data Ports

Data in:

- `skeletons`

Data out:

- `L0`
- `L1`
- `L2`
- `R0`
- `R1`
- `R2`
- `debug`

Notes:

- `skeletons` should be fed from `Skeleton Decoder.skeletons`.
- `debug` is optional but strongly recommended for live inspection.

### Writable State Fields

| Name | Type | Purpose |
| --- | --- | --- |
| `trackingMode` | `auto | manual` | Select automatic target picking or manual binding. |
| `referenceKey` | `string` | Manual reference stream key. Use current `Skeleton Decoder` model keys. |
| `targetKey` | `string` | Manual target stream key. Use current `Skeleton Decoder` model keys. |
| `manualTargetBone` | `string` | Manual target-bone selection on the chosen target stream. |
| `rotationMode` | `target_reference | target_plane` | Select which basis is used for `R0/R1/R2`. |
| `collisionEnabled` | `boolean` | Optional gating by synthetic reference radius. Recommended default is `false` in v1. |
| `referenceRadiusHint` | `number` | Script-side radius hint because the current stream does not expose the real reference radius. |
| `autoVagina` | `boolean` | Enable `Vagina` as an auto-target candidate. |
| `autoMouth` | `boolean` | Enable `Mouth` as an auto-target candidate. |
| `autoLeftHand` | `boolean` | Enable `LeftHand` as an auto-target candidate. |
| `autoRightHand` | `boolean` | Enable `RightHand` as an auto-target candidate. |
| `autoFeet` | `boolean` | Enable `Feet` as an auto-target candidate. |
| `autoAnus` | `boolean` | Enable `Anus` as an auto-target candidate. |
| `autoChest` | `boolean` | Enable `Chest` as an auto-target candidate when available. |

### Read-Only Diagnostic State Fields

| Name | Type | Purpose |
| --- | --- | --- |
| `availableReferenceKeys` | `string[]` | Current skeleton keys that expose a usable reference frame. |
| `availableTargetKeys` | `string[]` | Current skeleton keys that expose at least one enabled target bone. |
| `availableTargetBones` | `string[]` | Current target-bone choices for the selected or locked target stream. |
| `lockedReferenceKey` | `string` | The reference stream actually used by the solver. |
| `lockedTargetKey` | `string` | The target stream actually used by the solver. |
| `resolvedTargetBone` | `string` | The target bone actually used by the solver. |
| `lastPickReason` | `string` | Diagnostic text describing the latest auto/manual decision. |

### Solver Behavior

The scene should implement the following behavior contract:

- Reference candidates are skeletons that contain `ReferenceStart`, `ReferenceEnd`, `PlaneStart`, and `PlaneEnd`.
- Target candidates are skeletons that contain at least one enabled interaction bone.
- Auto mode picks the enabled target bone with the smallest world-space distance to the active reference.
- Manual mode respects `referenceKey`, `targetKey`, and `manualTargetBone`.
- `VAMDildo@1.0` is treated as the current toy path in scope.
- `CustomUnityAsset` stays out of scope until the streamer grows an asset parser.

Recommended target-bone set for v1:

- `Vagina`
- `Mouth`
- `LeftHand`
- `RightHand`
- `Feet`
- `Anus`
- `Chest`

Notes:

- `autoChest` only applies when the target schema actually emits `Chest`.
- `VAMFemale@1.0` provides the richest target set in the current streamer.
- `VAMDildo@1.0` currently provides reference bones only, so it is not an auto-target candidate.

## Pose Math Notes

The streamed bone format used by `Skeleton Decoder` is:

- `pos = [x, y, z]`
- `rot = [w, x, y, z]`

This matches the current decoder and should be preserved in the script.

### Reconstructing The Reference Frame

Freeze the reference reconstruction math as:

```text
ref_pos = ReferenceStart.pos
ref_up = normalize(ReferenceEnd.pos - ReferenceStart.pos)
ref_length = ||ReferenceEnd.pos - ReferenceStart.pos||
ref_right = normalize(PlaneEnd.pos - PlaneStart.pos)
plane_normal = normalize(cross(ref_up, ref_right))
ref_forward = normalize(cross(ref_right, ref_up))
```

This is the practical external equivalent of the geometry that `ToySerialController` uses for its reference frame.

### Reconstructing The Target Frame

The target bone is read from the selected interaction bone.

Its basis should be reconstructed from the quaternion:

```text
target_pos = target_bone.pos
target_rot = target_bone.rot
target_right = rotate(target_rot, [1, 0, 0])
target_up = rotate(target_rot, [0, 1, 0])
target_forward = rotate(target_rot, [0, 0, 1])
```

### Raw Axis Math

Freeze the raw axis math as:

```text
diff = target_pos - ref_pos
axis_distance = clamp(dot(diff, ref_up), 0, ref_length)
closest_point = ref_pos + ref_up * axis_distance

L0 = 1 - clamp01(axis_distance / ref_length)

diff_on_plane = project_on_plane(diff, plane_normal)
L1 = dot(diff_on_plane, ref_forward)
L2 = dot(diff_on_plane, ref_right)
```

Rotation depends on `rotationMode`.

When `rotationMode = target_reference`, compare the target basis against:

- `basis_up = ref_up`
- `basis_right = ref_right`
- `basis_forward = ref_forward`

When `rotationMode = target_plane`, compare the target basis against:

- `basis_up = plane_normal`
- `basis_right = ref_right`
- `basis_forward = cross(ref_right, plane_normal)`

Then compute:

```text
corrected_right = project_on_plane(target_right, basis_up)
if dot(corrected_right, basis_right) < 0:
    corrected_right = corrected_right - 2 * project(corrected_right, basis_right)

R0 = signed_angle(basis_right, corrected_right, basis_up) / pi
R1 = -signed_angle(basis_up, project_on_plane(target_up, basis_forward), basis_forward) / (pi / 2)
R2 = signed_angle(basis_up, project_on_plane(target_up, basis_right), basis_right) / (pi / 2)
```

This mirrors the effective production math described in `ignore/Feel8.SkeletonStreamer/doc/TCodeReferenceMapping.md`.

### Collision And Radius Gating

Collision/radius gating is optional in v1.

- `Feel8.SkeletonStreamer` currently does not expose the real reference radius used by `ToySerialController`.
- If collision gating is needed, use `referenceRadiusHint` as a synthetic radius.
- Recommended default for v1 is `collisionEnabled = false`.
- If later enabled, gate updates by checking the target distance to the reference axis against `referenceRadiusHint`.

## Practical Limits And Honesty Gaps

This scene intentionally does not claim full parity with `ToySerialController`.

Current parity limits:

- `Feel8.SkeletonStreamer` currently supports `Person` and `Dildo`, not `CustomUnityAsset`.
- `VAMFemale@1.0` does not currently emit reference-frame bones, so it is target-only in this graph.
- The current stream does not expose `ReferenceRadius`, so exact `ToySerialController.IsColliding()` parity is not available.
- `ToySerialController` also contains later output shaping, smoothing, and curve logic for final device channels. This scene stops at raw `L0/L1/L2/R0/R1/R2` as the primary deliverable.

Recommended v1 stance:

- Get the graph behavior correct first.
- Validate it in real scenes.
- Only then decide whether the math should be promoted into a dedicated operator.

## Validation Checklist

Use this checklist when bringing the scene up inside `PyStudio`.

- Confirm `UDP In.packet -> Skeleton Decoder.packet` produces live decoded skeletons.
- Confirm `Skeleton Decoder.availableKeys` updates as scene actors appear or disappear.
- Confirm `Python Script.availableReferenceKeys`, `availableTargetKeys`, and `availableTargetBones` update as scene actors appear or disappear.
- Confirm `trackingMode=auto` locks an enabled target bone and updates `lockedReferenceKey`, `lockedTargetKey`, and `resolvedTargetBone`.
- Confirm `trackingMode=manual` respects the selected `referenceKey`, `targetKey`, and `manualTargetBone`.
- Confirm the six raw outputs can be inspected independently before any normalization or `TCode` packaging.
- Confirm optional downstream `Switch Mixer` and `Silence Detector` provide hold or fallback behavior without changing the pose solver.
- Confirm raw `L1/L2/R0/R1/R2` are range-mapped before feeding `TCode`.

The relevant repo validation has already passed through `pixi`:

```bash
pixi run pytest packages/f8pyengine/tests/test_python_script_state.py packages/f8pyengine/tests/test_bone_selector.py packages/f8pyengine/tests/test_bone_filter.py packages/f8pyengine/tests/test_udp_skeleton_exec_trigger.py packages/f8pyengine/tests/test_udp_skeleton_chunk_reassembly.py -q
```

Result:

```text
43 passed
```

## Assumptions

- The scene stays graph-first: no new runtime operator is required for the first documented solution.
- The primary output layer for `scene-06` is raw semantic pose axes, not final device-normalized motion.
- "Toy" scope in this scene means the currently streamed `VAMDildo@1.0` route.
- `CustomUnityAsset` parity is intentionally future work until `Feel8.SkeletonStreamer` adds an asset parser and richer geometric metadata.

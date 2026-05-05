# PyStudio Nodes

These nodes are editor-local features shipped by `f8.pystudio`. They are first-class authoring tools, but they are not external runtime services discovered from `services/**/describe.json`.

## Source of Truth

- `packages/f8pystudio/f8pystudio/pystudio_node_registry.py`
- `packages/f8pystudio/f8pystudio/operators/__init__.py`

## `f8.viz.text`

- What it does: previews incoming values as readable text in the editor.
- Use it when: you need fast inspection of dict payloads, strings, or intermediate values.
- Common wiring: attach to outputs such as feature maps, expression results, or debug payloads.
- Key fields: `uiUpdate`, `uiWrap`, `throttleMs`.
- Related scenarios: `Scene 03`, `Scene 04`.

## `f8.viz.wave`

- What it does: plots one or more numeric streams over time.
- Use it when: tuning envelopes, filters, rate limiters, and mapped control signals.
- Common wiring: place after `Envelope`, `Smooth Filter`, `Range Map`, or other scalar transforms.
- Key fields: `bufferLimit`, `timeWindowMs`, `refreshMs`, `legend`, `min`, `max`.
- Related scenarios: `Scene 01`, `Scene 02`, `Scene 03`, `Scene 04`.

## `f8.viz.video`

- What it does: previews Zenoh latest-frame video and optional flow/scalar overlays from typed data ports.
- Use it when: validating screen/video ingestion or checking optical-flow/scalar-field outputs.
- Common wiring: connect `video`, `flow`, or `scalar` data inputs to `f8.implayer`, `f8.screencap`, or downstream flow/scalar producers.
- Key fields: `flowDisplay`, `scalarDisplay`, and render refresh controls.
- Related scenarios: best paired with media, CVKit, and flow-metric service pages.

## `f8.viz.audio`

- What it does: previews waveform data from Zenoh latest-audio chunks on a typed data input.
- Use it when: checking capture health, input latency, and whether audio activity matches downstream features.
- Common wiring: connect its `audio` data input to `f8.audiocap`.
- Key fields: `historyMs`, `channel`, `refreshMs`.
- Related scenarios: `Scene 03`.

## `f8.viz.track`

- What it does: visualizes tracking/template-match style regions and motion state.
- Use it when: inspecting tracker confidence, region drift, or template-lock behavior.
- Common wiring: pair with `f8.cvkit.tracking` and template-match capture workflows.
- Key fields: upstream sampling mode plus tracker-specific overlay options exposed in the node.
- Related scenarios: `Scene 01`.

## `f8.viz.three_d`

- What it does: renders skeleton/body data in a 3D viewer.
- Use it when: validating pose streams, world-up assumptions, or filtered skeleton outputs.
- Common wiring: pair with `UDP In + Skeleton Decoder`, `UDP In + VMC Decoder`, or filtered bone pipelines.
- Key fields: world-up, viewer update throttling, and upstream sampling controls.
- Related scenarios: `Scene 02`.

## `f8.control_panel`

- What it does: publishes a chosen value to downstream state edges from a central UI control.
- Use it when: one manually adjusted parameter should drive several nodes at once.
- Common wiring: connect the `value` state to multiple target state fields with shared presets.
- Key fields: `value`, `options`.
- Related scenarios: best used as an authoring aid in production sessions and demos.

## `f8.note`

- What it does: stores markdown notes directly on the canvas.
- Use it when: a graph needs operator intent, handoff notes, or release-time warnings preserved in context.
- Common wiring: none; use it as inline documentation.
- Key fields: `content`.
- Related scenarios: useful in every reusable session.

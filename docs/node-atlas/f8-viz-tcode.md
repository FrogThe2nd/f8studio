# `f8.viz.tcode` (`TCodeViz`)

`f8.viz.tcode` is a repo-local PyStudio plugin node that opens a detached OSR-style emulator viewer for `TCode` string streams.

## Source of Truth

- `packages/f8pystudio_ext_viz_tcode/f8pystudio_ext_viz_tcode/plugin.py`
- `packages/f8pystudio_ext_viz_tcode/f8pystudio_ext_viz_tcode/operators/viz_tcode.py`

## What It Is Good For

- Validate `TCode` output without real hardware attached
- Compare mapping quality before sending commands to `Serial Out`, `Handy Out`, or protocol adapters
- Demo a motion pipeline to non-hardware stakeholders

## Typical Wiring

1. Produce `TCode` strings from `f8.tcode`
2. Branch the same stream to `f8.viz.tcode`
3. Keep the real hardware output node in parallel for A/B checks

## Key Fields

- `model`: emulator body type such as `OSR2`, `SR6`, or `SSR1`
- `throttleMs`: UI push throttling
- `maxLineLength`: protection against malformed or oversized lines

## Pitfalls

- It is a viewer, not a runtime device sink
- If the upstream signal is bursty, increase `throttleMs` before assuming the pipeline itself is unstable
- Treat it as a validation branch, not the authoritative output path

## Related Scenarios

- [CVKit Template Tracking](../scenarios/cvkit-template-tracking.md)
- [Audio Driven TCode](../scenarios/audio-driven-tcode.md)
- [Functional TCode Generation](../scenarios/functional-tcode-generation.md)


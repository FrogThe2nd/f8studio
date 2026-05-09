# Scene 03: Audio Driven TCode

## Goal

Capture live audio, extract audio features, and transform signal energy into smooth real-time `TCode` output.

## Main Pipeline

`Audio Capture -> Audio Feature Core -> Python Expr Service (msg['rms']) -> Envelope -> Smooth Filter -> Range Map -> TCode -> Serial Out`

## Steps

1. Import the script and start `Audio Capture`, `Audio Feature Core`, `Audio Feature Rhythm`, and `PyEngine`.
2. Verify `Audio Capture.audio` is connected to both `Audio Viz.audio` and `Audio Feature Core.audio`.
3. Use `Python Expr Service` to extract the drive value from `coreFeatures` (current expression: `msg['rms']`).
4. Check output stability through `Envelope`, `Smooth Filter`, and `Range Map`.
5. Verify timing link `Tick.tickMs -> TCode.intervalMs` (both are `100` in this script).
6. Configure and enable `Serial Out`; validate command stream with `TCodeViz`.

## Key Parameters

- `Audio Feature Core.windowMs/hopMs`: `768/64` (responsiveness vs. stability)
- `Envelope.method`: `SMA`
- `Smooth Filter.filter_type`: `ONEEURO`
- `Range Map.outMin/outMax`: `0.4/1.0`

## Validation

- `Text Viz 1/2` continuously display core and rhythm feature payloads.
- `Wave Viz` shows a sensible progression from raw -> envelope -> smoothed.
- `TCodeViz` and serial stream stay continuous and stable.

## Image Placeholders

- `docs/media/images/scenarios/scene-03-overview.png`
- `docs/media/images/scenarios/scene-03-audio-features.png`

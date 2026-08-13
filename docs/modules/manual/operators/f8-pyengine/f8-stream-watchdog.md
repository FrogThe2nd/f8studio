## When to Use

- Use `Stream Watchdog` at every physical-output boundary driven by live
  skeleton or tracking data.
- Drive `check` from a fixed `Tick` and feed the latest decoded data into
  `value`.
- Connect `valid` to the output node's exec input so stale tracking cannot
  continue sending commands.

## Common Wiring Patterns

- **Skeleton Safety Gate**: `Skeleton Decoder.skeletons -> value`, `Tick.exec ->
  check`, and `valid -> Serial Out.exec`.
- **TCode Preview Plus Hardware**: Let TCode visualization continue receiving
  data while only the serial execution path is watchdog-gated.
- **250 ms Default**: Start with `timeoutMs=250` for a 50 Hz Unity stream and
  adjust only from observed frame cadence.

## Pitfalls / Gotchas

- **Receive Time, Not Source Clock**: Freshness uses local `receivedAtMs`
  attached by the decoder, avoiding clock synchronization assumptions.
- **Data Output, Not State Telemetry**: Validity and per-frame timing belong on
  data/monitor channels; do not mirror them into service state fields.
- **Not An Arm Switch**: The watchdog handles stale input. Keep `Serial
  Out.enabled=false` until the user separately validates and arms hardware.

## When to Use

- Use `f8.implayer` to turn local media files, RTSP streams, or supported web URLs into a stable video Shared Memory (SHM) producer.
- It is the primary way to ingest video content into the Feel8 graph, providing a deterministic clock for demos, QA passes, and replayable scenarios.
- It uses a C++ MPV-based backend, supporting a wide range of codecs and protocols (including YouTube-dl support for online video URLs).
- It is the best choice when you need precise seeking, looping, or volume control through commands.

## Common Wiring Patterns

- **Standard Consumption**: Feed its SHM output (defaulting to the service instance id or `videoShmName`) into CVKit, DL, or visualization consumers (`f8.viz.video`).
- **Media Master**: Keep one `implayer` node as the canonical media producer for a scenario and branch the SHM signal to multiple analysis pipelines in parallel.
- **Dynamic Control**: Use `f8.pyengine` or scripts to send `open`, `play`, `pause`, `next`, `previous`, or `seek` commands based on application logic or UI events.

### Cookie/Auth Notes

- Use browser or cookies-file auth modes only when URL playback (e.g., private streams) requires session credentials.
- Treat auth-related state as sensitive runtime configuration; it is not persisted in session files.

## Pitfalls / Gotchas

- **Codec Availability**: Missing system codecs or internal MPV errors can result in "empty" shared memory; check the `monitor` port and console logs for explicit load failures.
- **SHM Naming**: Mismatched `shmName` between producer and consumer is the most frequent cause of "no video" issues. Verify the `videoShmName` property matches the consumer's input.
- **Network Stability**: For URL sources, high latency or connection drops can stall the graph if downstream nodes wait synchronously. Monitor `monitor.frame.dropped` to detect performance issues.

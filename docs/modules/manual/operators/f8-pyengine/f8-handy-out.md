## When to Use

- Use `Handy Out` when your graph needs to send TCode commands directly to "The Handy" device over a network connection.
- It is a specialized device sink that handles the specific transport requirements for The Handy while maintaining the Feel8 graph's temporal consistency.
- Use it to synchronize your computer vision or audio-driven logic with a physical Handy device in real-time.

## Common Wiring Patterns

- **Standard Handy Setup**: Feed it finalized `TCode` strings from a `f8-tcode` operator. Ensure your scaling and safety limits are applied upstream.
- **Monitoring Bridge**: Keep an `f8-viz-text` or `TCodeViz` attached to the input port while validating your connection to the device's API.
- **Connection Management**: Use the node properties to manage the Handy's connection key and transport mode (e.g., WebSocket vs REST).

## Pitfalls / Gotchas

- **Transport Role**: Treat this node strictly as a communication layer. Do not attempt to fix signal timing or motion logic here; those issues should be addressed in the `f8-tcode` or `Range Map` nodes.
- **Latency Over network**: Commands sent to The Handy are subject to network jitter. If the motion feels "stuck" or delayed, check your local Wi-Fi stability and the `intervalMs` setting to ensure you aren't overwhelming the device's buffer.
- **Key Sensitivity**: The connection key is sensitive information. Avoid sharing session files that contain your unique device key if you are collaborating with others.

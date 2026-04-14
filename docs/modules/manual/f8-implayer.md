## When to Use

- Use `f8.implayer` as the main video playback entry point for local files, stream URLs, and supported online sources.
- It is ideal for demos, replayable test passes, and scenarios where several graph branches should share one video source.
- Choose it when you need explicit playback control such as play, pause, seek, loop, and volume.

## Common Wiring Patterns

- The standard pattern is `f8.implayer -> CV / DL / Viz`.
- When several branches consume the same footage, keep `implayer` as the single canonical producer.
- If playback needs to be controlled from graph logic, send commands from `f8.pyengine` or a script service.

## Pitfalls / Gotchas

- If the image is empty, check service logs and verify that downstream consumers are using the correct `videoShmName`.
- Stream-based sources are more sensitive to network and codec environment issues than local files.
- If the graph will be shared, remember that URL and cookie-related state may be sensitive.

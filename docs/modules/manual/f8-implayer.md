## When to Use

- Use `f8.implayer` to turn local media files or supported URLs into a stable video SHM producer.
- It is the easiest deterministic source for demos, QA passes, and replayable scenarios.

## Common Wiring Patterns

- Feed its SHM output into CVKit, DL, pose, or `f8.viz.video` consumers.
- Keep one `implayer` node as the canonical media producer for a scenario and branch from there.

### Cookie/Auth Notes

- Use browser or cookies-file auth only when URL playback truly needs it.
- Treat auth-related state as sensitive runtime configuration, not a value to casually share in example sessions.

## Pitfalls / Gotchas

- Missing codecs or URL auth problems can look like empty downstream graphs rather than explicit load failures.
- SHM-name mismatches are a more common problem than actual playback failure.


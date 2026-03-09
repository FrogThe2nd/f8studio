## When to Use

- Use `f8.pyscript` when a graph needs custom lifecycle hooks or bespoke glue logic as a standalone service.
- It is the most flexible service-level integration point in the repo.

## Common Wiring Patterns

- Use it for orchestration, protocol glue, or custom data shaping that does not fit cleanly into declarative service config.
- Keep its state/port schema explicit so Studio tooling and future readers still understand the contract.

## Pitfalls / Gotchas

- Poorly scoped scripts become opaque mini-applications and are hard to debug during release prep.
- Silent assumptions about input shape or timing are more dangerous here than in declarative nodes.


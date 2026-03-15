## When to Use

- Use `f8.proclauncher` when a scenario needs to start an external helper process alongside the graph.
- It is useful for one-shot utility launchers, bridges, or companion tools that are easier to run as separate OS processes.

## Common Wiring Patterns

- Set `programPath` to the exact executable or command line you want to launch, then leave `singleton=true` to avoid accidental duplicate starts during iteration.
- Keep launcher nodes near the part of the graph that depends on the external tool so the process relationship stays obvious in the session.

## Pitfalls / Gotchas

- `detached=true` means the launched process will keep running after the service stops, which is convenient for helpers but easy to forget during debugging.
- Most failures come from bad command lines, quoting issues, or assuming the child process inherits a working environment it does not actually have.

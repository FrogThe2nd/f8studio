## When to Use

- Use `f8.proclauncher` when a scenario needs to start an external helper process, companion tool, or bridge alongside the Feel8 graph.
- It is ideal for one-shot utility launchers (e.g., opening a browser to a dashboard) or background workers that are more efficient to run as separate OS processes.
- It manages the lifecycle of the child process, allowing you to choose whether the process should be killed when the service stops or left running "detached".

## Common Wiring Patterns

- **Standard Tooling**: Set `programPath` to the exact executable path or command line. Use quotes if the path or arguments contain spaces.
- **Service Dependency**: Leave `singleton=true` (default) to ensure that starting the service multiple times doesn't spawn redundant instances of the same tool.
- **Topology Layout**: Keep launcher nodes near the specific part of the graph that depends on the external tool, making the dependency relationship clear in the Studio's node view.

## Pitfalls / Gotchas

- **Detached Zombies**: If `detached=true`, the launched process will persist even after the Feel8 service stops or the Studio is closed. This can lead to hidden background processes using system resources; use `detached=false` if the helper should strictly follow the graph's lifecycle.
- **Environment Context**: Child processes may not inherit the same environment variables or working directory as the Studio. If the tool depends on specific paths, use absolute paths in the `programPath`.
- **Command Quoting**: Poorly quoted command lines are the most common cause of failure. If your program path or arguments have spaces, ensure they are properly wrapped (e.g., `"C:\Path With Spaces\app.exe" --arg "Value"`).

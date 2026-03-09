# Lifecycle and Monitoring

Studio lets you manage runtime processes from the canvas without leaving the graph context.

## Service Toolbar States

Each service node has a compact process toolbar for disable/start, activate/stop, sync, and restart actions.

| Status | Screenshot | Meaning |
| --- | --- | --- |
| Not Run | ![not run](../assets/studio/status-not-run.png) | Process not started |
| Disabled | ![disabled](../assets/studio/status-disabled.png) | Excluded from compile, deploy, and auto-start |
| Running | ![running](../assets/studio/status-running.png) | Process is alive |
| Paused | ![paused](../assets/studio/status-paused.png) | Process is alive but inactive |

## Deploy Flow

1. Edit the graph on canvas
2. Validate required state fields and host-service bindings
3. Compile the runtime graph
4. Send the compiled rungraph to target services
5. Watch service monitor rows and logs for acceptance or rejection

## Monitoring Views

`Service Manager` is the central dashboard for runtime health. Watch these first:

- CPU/RAM/GPU usage spikes
- latency and tick drift
- error counters and repeated failures
- alive/ready/active transitions after deploy

## Good Operational Habits

- Start with infrastructure services first, then hosts such as `f8.pyengine`, then downstream producers/consumers.
- Restart only the service that owns the failure when possible.
- Keep `Disabled` nodes in the graph for experiments, but avoid leaving them wired into production scenarios without notes.


## When to Use

- Use `f8.screencap` when your graph needs to analyze the desktop, a specific monitor, or a fixed region of the screen as a live video Shared Memory (SHM) source.
- It is the standard producer for screen-driven computer vision, UI automation, game analysis, and streaming scenarios.
- Choose this when you need low-latency access to any visual content displayed on the host OS.

## Common Wiring Patterns

- **UI Vision Pipeline**: Feed its video SHM into CV nodes (`f8.cvkit.templatematch`, `f8.cvkit.denseoptflow`) or DL nodes (`f8.dl.detector`) to "read" the interface.
- **Reference Visualization**: Keep a parallel visualization branch (`f8.viz.video`) attached to the capture SHM during setup to lock in the exact `captureRegion` and `scale`.
- **Logic Sync**: Use the `monitor` port to track capture frame rate and latency, ensuring your downstream analysis doesn't lag behind the physical screen updates.

## Pitfalls / Gotchas

- **Permissions & OS Blocks**: On many operating systems (including Windows and macOS), the Studio or the capture service may require explicit Accessibility or Screen Recording permissions to function. A "dead" graph often means the OS is blocking the capture.
- **Resolution Overhead**: Capturing a full 4K screen at 60FPS can consume massive CPU/GPU bandwidth. Use the `captureRegion` and `scale` properties to capture only the area of interest at the minimum required resolution.
- **Refresh Sync**: If the capture looks "jittery," verify that the `targetFps` matches the monitor's refresh rate or is a clean divisor of it.

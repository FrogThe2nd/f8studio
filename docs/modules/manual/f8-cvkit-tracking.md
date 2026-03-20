## When to Use

- Use `f8.cvkit.tracking` when you need to maintain a continuous "lock" on a moving Region of Interest (ROI) after it has been initialized.
- It is designed for targets that change position frame-to-frame but maintain some level of visual continuity (e.g., a person walking across a room, a moving vehicle).
- It is more robust than simple template matching for objects that might slightly change shape or scale as they move.

## Common Wiring Patterns

- **Initialize & Follow**: Start by finding the target with `f8.cvkit.templatematch` or a manual "Capture ROI" workflow, then feed that initial bounding box into the tracking service to maintain the lock.
- **Visual Feedback**: Always keep an `f8.viz.track` node attached to the tracker output during development to visually verify if the tracker has "lost" the object.
- **Downstream Analysis**: Feed the tracked ROI coordinates into `f8.pyengine` operators to drive camera following, distance estimation, or motion-triggered logic based on position.

## Pitfalls / Gotchas

- **Loss of Lock**: If an object is occluded or moves too fast for the configured `searchWindow`, the tracker will "lose" the target. You must implement logic (usually via `f8.pyengine`) to re-initialize the tracker when confidence drops.
- **Drift Accumulation**: Small errors in frame-to-frame matching can accumulate, causing the tracking box to slowly slide away from the actual target. Periodically re-syncing with a global detector (like `f8.dl.detector`) is a common fix.
- **Startup Order**: The tracker requires a valid initial ROI and a live input stream. Ensure the video producer is running before attempting to initialize the tracking state.

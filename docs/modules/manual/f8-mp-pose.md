## When to Use

- Use `f8.mp.pose` when your graph needs real-time, lightweight 2D or 3D body pose estimation from a video stream.
- It is the ideal choice for human-machine interaction, gesture recognition, and skeleton-driven animation authoring within the Feel8 ecosystem.
- Choose this for rapid prototyping and scenarios where a general-purpose, high-performance pose model is preferred over training custom networks.

## Common Wiring Patterns

- **Skeleton Mapping**: Feed video from `f8.implayer` or `f8.screencap`, then connect the `skeleton` output to `f8.pyengine` operators for bone angle calculation or joint-to-control mapping.
- **3D Inspection**: Connect the 3D landmark output to `f8.viz.three_d` to visualize the estimated body posture in a 3D space directly within Studio.
- **Feedback Loop**: Keep the original video path visible in a `f8.viz.video` node with skeleton overlays matched to the source frames to diagnose tracking accuracy.

## Pitfalls / Gotchas

- **Framing & Occulusion**: Pose accuracy is highly dependent on the subject being clearly visible. Partial occlusions (e.g., sitting behind a desk) or extreme camera angles can cause joints to "flicker" or be misidentified.
- **Distance & Scale**: The subject should ideally fill a significant portion of the frame. Small subjects (far away) or very low-resolution video will result in jittery tracking.
- **Lighting Dependency**: While MediaPipe is robust, extreme darkness or strong backlighting can confuse the initial body detection, leading to no skeleton being produced even if a person is present.

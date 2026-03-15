## When to Use

- Use `f8.cvkit.templatematch` when you have a target with a stable, consistent appearance and you need to find its location (ROI) within a video frame or screen capture.
- It is a fast, efficient solution for finding UI markers, anchored icons, or fixed regions in controlled camera environments.
- Choose this when you don't need the complexity of a neural network detector and the target's scale and orientation remain relatively constant.

## Common Wiring Patterns

- **Capture & Track**: Pair it with `f8.screencap` or `f8.implayer`. Use the `f8.viz.track` overlay to visually confirm where the template is being found.
- **Workflow Integration**: Use the `Template Match Capture` plugin (if available in Studio) to interactively grab the target image from the live stream and sync it to the service's `templatePath`.
- **Handoff to Tracking**: Set the initial search region using a fixed ROI, then use the match result to initialize a more robust continuous tracker if the target begins moving unpredictably.

## Pitfalls / Gotchas

- **Appearance Shifts**: Template matching is sensitive to changes in lighting, scale, and rotation. If the target looks different than the captured template (even slightly), match confidence will drop significantly.
- **Initial Quality**: Capturing a template that includes too much background or lacks distinct features will lead to "drifting" or false positive matches in other parts of the frame.
- **Search Area Overhead**: Matching a large template against a full 4K frame is slow. Use the `searchRegion` property to limit the scan area to where the target is expected to appear.

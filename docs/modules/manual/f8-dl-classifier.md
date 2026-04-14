## When to Use

- Use `f8.dl.classifier` when you need category predictions rather than detection boxes.
- It fits graphs where a target region is already known and the next question is "what class is this?"
- It is often the right tool for scene-state recognition or ROI-level classification.

## Common Wiring Patterns

- A common setup sends an ROI from a detector or tracker into the classifier.
- If you classify full frames, make sure the model was trained for that style of input.
- Route classification results into `f8.pyengine`, `Text Viz`, or UI logic.

## Pitfalls / Gotchas

- Classification quality depends heavily on model choice and correct preprocessing.
- If the output feels unstable, verify the input crop before blaming the classifier.
- Thresholds should be chosen from observed score distributions, not guessed in isolation.

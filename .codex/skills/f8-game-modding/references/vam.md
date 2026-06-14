# VaM

VaM support is phase 2 and should reuse the same public PyStudio modding tool contract.

Current exporter source:

- `ignore\VAM\Feel8.SkeletonStreamer`

Expected user flow:

1. Package the streamer as a managed VaM plugin artifact.
2. User opens VaM and adds the plugin to a scene.
3. Plugin streams UDP skeleton data to PyStudio on port `39540`.
4. PyStudio verifies with `Skeleton Decoder` and records the recipe.

Recipe records should include plugin or `.var` version, scene/plugin instructions, UDP target, and compatible PyStudio graph references.

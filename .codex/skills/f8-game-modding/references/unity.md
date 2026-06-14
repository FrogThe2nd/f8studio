# Unity

Use `H:\Feel8\f8unitymods` as the authoritative Unity toolchain.

PyStudio wraps:

- `game_setup.run_detect()`
- `game_setup.run_diagnose()`
- `game_setup.run_install()`

Default behavior:

- Exporter option is `auto`; profile metadata resolves to `F8SkeletonStreamer` or `F8Live2DStreamer`.
- Default UDP skeleton stream port is `39540`.
- Mono/IL2CPP backend detection chooses the matching BepInEx stack.
- Optional utility flags are explicit booleans: RuntimeUnityEditor, CinematicUnityExplorer, ConfigurationManager, UniversalUnityDemosaics.
- Unknown Unity games need a preview and approval before a generated custom profile is installed.

Expected graph:

`UDP In(port=39540) -> Skeleton Decoder -> Viz 3D`

Only add TCode branches after stream verification or when the recipe already contains a TCode graph.

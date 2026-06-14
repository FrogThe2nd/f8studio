# Unreal

Unreal support is phase 2 and should reuse the same public PyStudio modding tool contract.

Detection should look for:

- Game `.exe`
- `Engine/Binaries`
- `<GameName>/Binaries/Win64`
- `.uproject`
- UE4SS folder markers

Planned exporter bundle:

- Current scripts under `ignore\VAM\Unreal\Scripts`
- UE4SS Lua script placement
- Socket dependencies
- Generated config or sidecar for actors, mode, UDP host/port, coordinate conversion, and send interval

Always preview exact writes under UE4SS scripts/mods directories before applying.

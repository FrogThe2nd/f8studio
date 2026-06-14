---
name: f8-game-modding
description: Guide PyStudio game modding workflows for local Unity, Unreal, or VaM targets. Use when Codex needs to detect a game engine/backend, preview and approve exporter/plugin installation, verify UDP skeleton streams on port 39540, build PyStudio skeleton graphs, or save/share a modding recipe.
---

# F8 Game Modding

Use PyStudio tools as the deterministic boundary. Do not directly write into a game directory from scripts or shell unless the user explicitly asks for manual repair outside the PyStudio workflow.

Workflow:

1. Call `modding_detect_target` with the user-provided game `.exe` or root folder.
2. If Unity is detected, call `modding_preview_install` with explicit options.
3. Present blocking errors and exact game-directory writes before installation.
4. Call `modding_apply_install` only after explicit approval or `confirm=true`.
5. Ask the user to start the game, then call `modding_verify_stream` on UDP port `39540`.
6. Use the returned `graphBuildPlan` to call `graph_preview_build_plan`, then apply only after approval.
7. Verify `UDP In -> Skeleton Decoder -> Viz 3D` before proposing TCode output.
8. Save reusable results with `modding_create_recipe` when the user wants a draft or shareable process.

Safety rules:

- Never guess-install a loader.
- Preserve custom configs and profiles unless the preview explicitly says they are managed and will be updated.
- Do not put high-frequency stream counters into service state; use monitor/data samples.
- Record failed attempts and verification notes into recipe drafts when they help future users.

References:

- Read `references/unity.md` for Unity/BepInEx/f8unitymods details.
- Read `references/unreal.md` before planning UE4SS support.
- Read `references/vam.md` before planning VaM MVRScript support.

# Node Library and Sessions

`Node Library` is the fastest way to discover and place nodes while keeping variants and session reuse under control.

## Node Library

![Node library](../assets/studio/node-library.png)

- Search by label, tags, service class, operator class, or description
- Click a node, then left-click on canvas to place it
- Use right-click menus for details, variants, and cleanup actions

Useful context actions:

1. `Show Details`
2. `Manage Variants...`
3. `Delete Variant...`
4. `Variants`

![Context menu](../assets/studio/node-library-context-menu.png)
![Show details](../assets/studio/node-library-show-details.png)
![Variant manager](../assets/studio/node-variant-manager.png)

## Variants

Variants are best for stable parameter presets, not for large structural changes.

- Keep one variant per meaningful behavior profile
- Name variants after the scenario they unlock
- Avoid using variants to hide missing documentation

Stored file:

`~/.f8/studio/nodeVariants.json`

## Session Files

Studio auto-saves the latest session to:

`~/.f8/studio/lastSession.json`

Common session shortcuts:

1. `Ctrl+S`: save current session
2. `Ctrl+O`: load last session
3. `Ctrl+Shift+O`: open session file
4. `Ctrl+Shift+S`: save as
5. `Ctrl+Shift+I`: insert external graph into current canvas


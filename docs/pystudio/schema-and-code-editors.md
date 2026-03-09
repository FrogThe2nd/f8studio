# Schema and Code Editors

The `Properties` area is where Studio moves from canvas sketching into real interface definition.

## Properties Tabs

1. `State`: edit state fields and value schemas
2. `Commands`: define callable commands and parameters
3. `Port`: declare data inputs/outputs and their schemas
4. `Node`: change labels, colors, and other visual metadata

![State tab](../assets/studio/properties-state-tab.png)
![Commands tab](../assets/studio/properties-commands-tab.png)
![Port tab](../assets/studio/properties-port-tab.png)
![Node tab](../assets/studio/properties-node-tab.png)

## Schema Editor

![Schema UI](../assets/studio/schema-editor-ui.png)
![Schema JSON](../assets/studio/schema-editor-json.png)

Use the schema editor to make contracts explicit. In Studio, `valueSchema` drives:

- runtime validation
- editor form rendering
- completion and inline assistance in Python-related editors

Prefer clear schemas over loose `any` ports for graphs you plan to maintain or hand off.

## Monaco Code Editor

![Monaco editor](../assets/studio/code-editor-monaco.png)

Common shortcuts:

1. `Ctrl+S`: save
2. `Ctrl+Q`: close
3. `Ctrl+Space` / `Ctrl+J`: completion
4. `Ctrl+Shift+Space` / `Ctrl+Shift+J`: parameter hints
5. `Esc`: close suggestion popup

## Authoring Tips

- Model state and ports first; write code second.
- Put reusable logic in operator/service nodes, not ad-hoc text notes.
- Tight schemas improve grep-ability, refactoring safety, and editor support.


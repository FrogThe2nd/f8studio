# Studio (GUI)

`f8pystudio` is the visual node-graph environment for building, debugging, and deploying Feel8 service graphs.

## Launch

```bash
pixi run -e default f8pystudio
```

For live discovery during development:

```bash
python -m f8pystudio.main --discovery-live
```

## 5-Minute Workflow

1. Place a runtime host such as `PyEngine`
2. Place service producers/consumers such as `IM Player` or `CVKit Tracking`
3. Place operators and set each operator `Service Id` to the host service node `id`
4. Configure required state fields
5. Wire `Exec`, `Data`, and `State` edges
6. Use `Ctrl+R` to compile and `F5` to deploy

## Where To Go Next

- [PyStudio Guide](../pystudio/index.md) for the full editor manual
- [Node Atlas](../node-atlas/index.md) for node-by-node usage guidance
- [Modules Overview](../modules/index.md) for canonical service/operator specs
- [Scenarios](../scenarios/index.md) for complete runnable examples

## Common First Problems

1. Operator does not run: check `Service Id`
2. Ports do not connect: check edge type and schema intent
3. Deploy is rejected: check required state fields and disabled nodes
4. Graph looks right in canvas but not at runtime: check actual service lifecycle state


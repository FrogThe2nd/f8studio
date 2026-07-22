# Component Authoring

Components are reusable authoring-time graph templates. They are not runtime
nodes and they do not introduce a second graph execution model.

## Insertion Semantics

When a component is placed on the canvas, Studio:

1. copies its saved graph content
2. remaps conflicting node and edge identities
3. places the copied nodes and connections on the canvas
4. returns ownership of those ordinary nodes to the current graph

There is no parent component node after insertion. Users may edit, delete, or
replace every inserted node. Updating the source component does not silently
modify graphs that previously inserted it.

Linked drafts describe the relationship between a local asset draft and its
publish target. They do not create linked component instances on the canvas.

## Component Roles

Use one primary role tag:

- `role:source`: produces media, network, mod, or skeleton input
- `role:detect`: extracts a bounded signal or event from a source
- `role:shape`: maps, filters, mixes, limits, or encodes signals
- `role:output`: terminates at one or more device/protocol outputs
- `role:view`: visualizes or diagnoses values
- `role:complete`: provides a complete starter workflow

Additional tags describe discovery dimensions:

- `workflow:video`, `workflow:audio`, `workflow:modding`, `workflow:skeleton`
- `signal:position`, `signal:vibrate`, `signal:rotate`, `signal:tcode`
- `protocol:tcode`, `protocol:serial`, `protocol:handy`, `protocol:lovense`, `protocol:buttplug`
- `level:starter`, `level:advanced`

Unknown tags remain valid for forward compatibility. Reserved role, workflow,
signal, protocol, and level prefixes must use normalized lowercase values.

Studio edits these dimensions through Component-specific metadata fields. The
Role field is a single-choice selector. Workflows, Signals, Protocols, Levels,
and custom Tags accept comma-separated values. The editor serializes them back
to the existing flat `tags` list, so component files, local drafts, and cloud
records do not require a schema migration. Custom tags, including unknown
namespaced tags such as `author:example`, are preserved separately from the
reserved dimensions.

## Composition Rules

Protocol-independent components should expose normalized semantic signals and
stop before device transport. Output components should consume those signals
and contain the protocol-specific tail of the graph.

Do not translate one device protocol into another. Reuse happens before the
protocol boundary:

```text
source -> detect -> shape -> normalized signal -> output node
```

Components with physical outputs must:

- store every physical output node as disabled
- omit local device selections, URLs, tokens, and connection keys
- include or document a safe visualization path
- state the expected input range in their description

## Compatibility

Component tags support search and recommendations only. They do not change
runtime compilation or bypass normal port schema validation. A recommendation
is valid only when both the semantic signal tag and the actual graph port schema
are compatible.

`graph_match_library` returns component candidates alongside node candidates.
Every component candidate includes its role, workflows, signals, and protocols.
Compatibility is marked as not evaluated unless the caller supplies all three
of `source_node_id`, `source_port`, and `signal`. With that context, Studio uses
the source output's declared schema and returns explicit compatibility reasons
and warnings. Matching never inserts a component, creates a connection, enables
an output node, or starts a physical device.

## Bundled Official Components

Studio ships a small read-only official library for common protocol boundaries:

- `Position to Lovense`: normalized position to Lovense `sendPositionCmd`
- `Position to Buttplug`: normalized position to Buttplug/Intiface `sendPositionCmd`
- `Position to Handy`: normalized position to The Handy HDSP output
- `Position to TCode`: normalized axis values to a TCode v0.3 string
- `TCode to Serial`: a TCode string to serial transport

These are protocol tails and encoders, not complete graphs. They do not create a
PyEngine service, media source, detector, Tick node, or visualization. Place
them inside an existing PyEngine service and connect the visible data and exec
ports to the graph's existing flow.

Bundled assets use stable component and node IDs, are loaded directly from
package resources, and are never seeded into the user's database. They appear
as installed Official entries and may be copied to an editable draft. They
cannot be pulled, removed, published in place, or queried for remote history.

Every physical output in a bundled component stores `enabled=false` and clears
device selections, serial ports, connection keys, and target toy IDs. The user
must configure the transport and explicitly enable it after inspecting the
signal path.

Regenerate or validate the assets after changing an embedded OperatorSpec:

```text
pixi run official_components
pixi run official_components_check
```

## When to Use

- Use `Skeleton Decoder` after `UDP In` when the incoming packets contain Feel8 skeleton payloads or chunked skeleton frames.
- It keeps the transport layer separate from payload decoding, which makes the graph easier to test and easier to swap to other packet sources later.
- It is the canonical replacement for the removed `UDP Skeleton` operator.

## Common Wiring Patterns

- **Latest Skeleton Stream**: Connect `UDP In.packet` to `Skeleton Decoder.packet`, then feed `selectedSkeleton` into `Bone Selector`, `Bone Filter`, or visualizers.
- **Multi-Character Monitor**: Use `skeletons` to drive inspection tools that need the full active set, while `selectedSkeleton` drives the main control chain.
- **Chunk Reassembly**: Keep this decoder close to the packet source so fragmented skeleton frames are reassembled before other operators consume them.

## Pitfalls / Gotchas

- **Transport Assumption**: This node expects a packet object from `UDP In.packet`; it is not meant to parse arbitrary text or JSON payloads directly.
- **Selection Confusion**: `selectedKey` only works when the incoming model key exists in `availableKeys`; inspect that list first when nothing appears downstream.
- **No Legacy Wrapper**: Older graphs using `UDP Skeleton` should be migrated to `UDP In -> Skeleton Decoder` rather than relying on compatibility aliases.

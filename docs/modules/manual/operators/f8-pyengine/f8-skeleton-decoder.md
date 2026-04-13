## When to Use

- Use `Skeleton Decoder` after `UDP In` when the incoming packets contain Feel8 skeleton payloads or chunked skeleton frames.
- It keeps the transport layer separate from payload decoding, which makes the graph easier to test and easier to swap to other packet sources later.
- Use it as the payload decoder in a `UDP In -> Skeleton Decoder` chain for skeleton streams.

## Common Wiring Patterns

- **Latest Skeleton Stream**: Connect `UDP In.packet` to `Skeleton Decoder.packet`, then feed `selectedSkeleton` into `Bone Selector`, `Bone Filter`, or visualizers.
- **Multi-Character Monitor**: Use `skeletons` to drive inspection tools that need the full active set, while `selectedSkeleton` drives the main control chain.
- **Chunk Reassembly**: Keep this decoder close to the packet source so fragmented skeleton frames are reassembled before other operators consume them.

## Pitfalls / Gotchas

- **Transport Assumption**: This node expects a packet object from `UDP In.packet`; it is not meant to parse arbitrary text or JSON payloads directly.
- **Selection Confusion**: `selectedKey` only works when the incoming model key exists in `availableKeys`; inspect that list first when nothing appears downstream.
- **Packet Contract**: Keep the upstream node on `UDP In.packet`; this decoder expects the packet object rather than ad-hoc payload fragments.

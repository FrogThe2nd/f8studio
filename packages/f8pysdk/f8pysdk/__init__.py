"""
Compatibility package root.

Prefer stable owner modules such as `f8pysdk.specs`, `f8pysdk.bus`,
`f8pysdk.app`, `f8pysdk.registry`, `f8pysdk.nodes`, `f8pysdk.state`, and
`f8pysdk.transport`.

Keep this lightweight so utility submodules (for example `f8pysdk.shm`) can be
imported without pulling in optional/extra dependencies used by generated
schemas.
"""

try:
    from .msgspec_codec import *  # type: ignore
    from .specs import *  # type: ignore
except ImportError:
    # Optional deps for generated schemas may be missing in some
    # runtime environments; allow importing lightweight helpers regardless.
    pass

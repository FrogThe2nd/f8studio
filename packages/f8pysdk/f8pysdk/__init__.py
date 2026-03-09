"""
Package init.

Keep this lightweight so utility submodules (e.g. `f8pysdk.shm`) can be imported
without pulling in optional/extra dependencies used by generated schemas.
"""

try:
    from .generated import *  # type: ignore
    from .msgspec_codec import *  # type: ignore
    from .schema_helpers import *  # type: ignore
except ImportError:
    # Optional deps for generated schemas may be missing in some
    # runtime environments; allow importing lightweight helpers regardless.
    pass

from __future__ import annotations

import os
import warnings

# Keep Qt-based tests headless by default so `pytest` is safe over SSH/CI.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# NodeGraphQt still uses `distutils.version.LooseVersion` internally.
# Filter that third-party deprecation noise until the dependency is updated.
warnings.filterwarnings(
    "ignore",
    message="distutils Version classes are deprecated. Use packaging.version instead.",
    category=DeprecationWarning,
)
warnings.filterwarnings(
    "ignore",
    message="distutils Version classes are deprecated. Use packaging.version instead.",
    category=DeprecationWarning,
)

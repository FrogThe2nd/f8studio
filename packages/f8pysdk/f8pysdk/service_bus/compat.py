from __future__ import annotations

"""Central compatibility warnings for deprecated `service_bus.*` shim modules."""

import warnings


class ServiceBusCompatWarning(DeprecationWarning):
    """Warning emitted when callers import deprecated compatibility shim modules."""


def warn_compat_import(*, module_path: str, replacement: str) -> None:
    warnings.warn(
        f"`{module_path}` is a deprecated compatibility shim; import `{replacement}` instead.",
        ServiceBusCompatWarning,
        stacklevel=2,
    )

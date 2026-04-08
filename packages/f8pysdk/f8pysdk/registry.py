from __future__ import annotations

from .runtime_node_registry import (
    OperatorFactoryNotRegistered,
    OperatorAlreadyRegistered,
    OperatorFactory,
    RegistryError,
    RuntimeNodeRegistry,
    ServiceFactory,
    ServiceFactoryNotRegistered,
    ServiceNotRegistered,
)


def create_runtime_node_registry() -> RuntimeNodeRegistry:
    """
    Create a fresh process-local runtime registry.
    """
    return RuntimeNodeRegistry()


def shared_runtime_node_registry() -> RuntimeNodeRegistry:
    """
    Return the explicit process-global runtime registry singleton.
    """
    return RuntimeNodeRegistry.instance()


__all__ = [
    "create_runtime_node_registry",
    "OperatorFactoryNotRegistered",
    "OperatorAlreadyRegistered",
    "OperatorFactory",
    "RegistryError",
    "RuntimeNodeRegistry",
    "ServiceFactory",
    "ServiceFactoryNotRegistered",
    "ServiceNotRegistered",
    "shared_runtime_node_registry",
]

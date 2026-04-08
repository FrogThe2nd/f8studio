from __future__ import annotations

from typing import Any, TypeAlias

from .generated import F8OperatorSpec, F8RuntimeNode, F8ServiceDescribe, F8ServiceSpec
from .nodes import OperatorNode, RuntimeNode
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


ServiceFactoryLike: TypeAlias = ServiceFactory | type[RuntimeNode]
OperatorFactoryLike: TypeAlias = OperatorFactory | type[OperatorNode]


def _coerce_service_factory(factory: ServiceFactoryLike) -> ServiceFactory:
    if isinstance(factory, type):
        if not issubclass(factory, RuntimeNode):
            raise TypeError(f"service factory type must inherit RuntimeNode, got {factory.__name__}")

        def _build(node_id: str, node: F8RuntimeNode, initial_state: dict[str, Any]) -> RuntimeNode:
            created = factory(
                node_id=str(node_id),
                node=node,
                initial_state=dict(initial_state or {}),
            )
            if not isinstance(created, RuntimeNode):
                raise TypeError(f"service factory returned {type(created).__name__}, expected RuntimeNode")
            return created

        return _build
    return factory


def _coerce_operator_factory(factory: OperatorFactoryLike) -> OperatorFactory:
    if isinstance(factory, type):
        if not issubclass(factory, OperatorNode):
            raise TypeError(f"operator factory type must inherit OperatorNode, got {factory.__name__}")

        def _build(node_id: str, node: F8RuntimeNode, initial_state: dict[str, Any]) -> OperatorNode:
            created = factory(
                node_id=str(node_id),
                node=node,
                initial_state=dict(initial_state or {}),
            )
            if not isinstance(created, OperatorNode):
                raise TypeError(f"operator factory returned {type(created).__name__}, expected OperatorNode")
            return created

        return _build
    return factory


class Registry:
    """
    User-facing registry owner that combines spec registration and runtime factories.

    `RuntimeNodeRegistry` remains available as the low-level owner. `Registry`
    provides a smaller ergonomic surface for service authors while still exposing
    the underlying runtime registry explicitly via `runtime_registry`.
    """

    def __init__(self, runtime_registry: RuntimeNodeRegistry | None = None) -> None:
        self._runtime_registry = runtime_registry if runtime_registry is not None else RuntimeNodeRegistry()

    @classmethod
    def wrap(cls, runtime_registry: RuntimeNodeRegistry) -> "Registry":
        return cls(runtime_registry=runtime_registry)

    @property
    def runtime_registry(self) -> RuntimeNodeRegistry:
        return self._runtime_registry

    def services(self) -> list[str]:
        return self._runtime_registry.services()

    def service_spec(self, service_class: str) -> F8ServiceSpec | None:
        return self._runtime_registry.service_spec(service_class)

    def operator_specs(self, service_class: str) -> list[F8OperatorSpec]:
        return self._runtime_registry.operator_specs(service_class)

    def describe(self, service_class: str) -> F8ServiceDescribe:
        return self._runtime_registry.describe(service_class)

    def register_service_spec(self, spec: F8ServiceSpec, *, overwrite: bool = False) -> None:
        self._runtime_registry.register_service_spec(spec, overwrite=overwrite)

    def register_operator_spec(self, spec: F8OperatorSpec, *, overwrite: bool = False) -> None:
        self._runtime_registry.register_operator_spec(spec, overwrite=overwrite)

    def register_service_factory(
        self,
        service_class: str,
        factory: ServiceFactoryLike,
        *,
        overwrite: bool = False,
    ) -> None:
        self._runtime_registry.register_service_factory(
            service_class,
            _coerce_service_factory(factory),
            overwrite=overwrite,
        )

    def register_operator_factory(
        self,
        service_class: str,
        operator_class: str,
        factory: OperatorFactoryLike,
        *,
        overwrite: bool = False,
    ) -> None:
        self._runtime_registry.register_operator_factory(
            service_class,
            operator_class,
            _coerce_operator_factory(factory),
            overwrite=overwrite,
        )

    def register_service(
        self,
        spec: F8ServiceSpec,
        factory: ServiceFactoryLike,
        *,
        overwrite: bool = False,
    ) -> None:
        self.register_service_spec(spec, overwrite=overwrite)
        self.register_service_factory(str(spec.serviceClass or ""), factory, overwrite=overwrite)

    def register_operator(
        self,
        spec: F8OperatorSpec,
        factory: OperatorFactoryLike,
        *,
        overwrite: bool = False,
    ) -> None:
        self.register_operator_spec(spec, overwrite=overwrite)
        self.register_operator_factory(
            str(spec.serviceClass or ""),
            str(spec.operatorClass or ""),
            factory,
            overwrite=overwrite,
        )

    def create_service_node(
        self,
        *,
        service_class: str,
        node_id: str,
        initial_state: dict[str, Any] | None = None,
        node: F8RuntimeNode | None = None,
    ) -> RuntimeNode:
        return self._runtime_registry.create_service_node(
            service_class=service_class,
            node_id=node_id,
            initial_state=initial_state,
            node=node,
        )

    def create_operator_node(
        self,
        *,
        node_id: str,
        node: F8RuntimeNode,
        initial_state: dict[str, Any] | None = None,
    ) -> OperatorNode:
        return self._runtime_registry.create_operator_node(
            node_id=node_id,
            node=node,
            initial_state=initial_state,
        )

    def create_runtime_node(
        self,
        *,
        node_id: str,
        node: F8RuntimeNode,
        initial_state: dict[str, Any] | None = None,
    ) -> RuntimeNode:
        return self._runtime_registry.create_runtime_node(
            node_id=node_id,
            node=node,
            initial_state=initial_state,
        )

    def load_modules(self, modules: list[str]) -> None:
        self._runtime_registry.load_modules(modules)


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


def create_registry() -> Registry:
    """
    Create a fresh user-facing registry wrapper over a fresh runtime registry.
    """
    return Registry()


def shared_registry() -> Registry:
    """
    Return a user-facing registry wrapper over the explicit shared runtime registry.
    """
    return Registry.wrap(shared_runtime_node_registry())


__all__ = [
    "create_registry",
    "create_runtime_node_registry",
    "OperatorFactoryNotRegistered",
    "OperatorAlreadyRegistered",
    "OperatorFactory",
    "Registry",
    "RegistryError",
    "OperatorFactoryLike",
    "RuntimeNodeRegistry",
    "ServiceFactory",
    "ServiceFactoryLike",
    "ServiceFactoryNotRegistered",
    "ServiceNotRegistered",
    "shared_registry",
    "shared_runtime_node_registry",
]

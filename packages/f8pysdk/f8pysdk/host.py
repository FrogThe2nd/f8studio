from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import msgspec

from .bus import ServiceBus
from .generated import F8RuntimeGraph, F8RuntimeNode
from .codec import unwrap_json_value
from .nodes import OperatorNode, RuntimeNode
from .registry import OperatorFactoryNotRegistered, RuntimeNodeRegistry, create_runtime_node_registry

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServiceHostConfig:
    """
    Generic push-based service host.

    - One process hosts exactly one service instance (service_id).
    - `service_class` optionally overrides `ServiceBusConfig.service_class`.
    """

    service_class: str | None = None


class ServiceHost:
    """
    Push-based service host that binds a `ServiceBus` to per-node runtime implementations.

    - Rungraph drives creation/removal of local runtime nodes.
    - Runtime buffers data edges; exec/data evaluation is driven by the engine layer.
    """

    def __init__(
        self,
        bus: ServiceBus,
        *,
        config: ServiceHostConfig | None = None,
        registry: RuntimeNodeRegistry | None = None,
    ) -> None:
        self._bus = bus
        self._config = config if config is not None else ServiceHostConfig()
        self._registry = registry if registry is not None else create_runtime_node_registry()

        self._service_node: RuntimeNode | None = None
        self._operator_nodes: dict[str, OperatorNode] = {}
        self._bus.register_rungraph_hook(self)

    async def start(self) -> None:
        """
        Ensure the service node exists before any rungraph arrives.

        Rungraph-provided service nodes are treated as state snapshots only.
        """
        if self._service_node is not None:
            return
        service_class = self._service_class()
        if not service_class:
            raise ValueError("service_class must be non-empty")
        node_id = str(self._bus.service_id).strip()
        try:
            node = self._registry.create_service_node(service_class=service_class, node_id=node_id, initial_state={})
        except Exception as exc:
            raise RuntimeError(f"failed to create service node service_class={service_class} node_id={node_id}") from exc
        if node is None:
            raise RuntimeError(f"service node factory returned None service_class={service_class} node_id={node_id}")
        self._service_node = node
        try:
            self._bus.register_node(node)
        except Exception as exc:
            self._service_node = None
            raise RuntimeError(f"failed to register service node node_id={node_id}") from exc

    async def apply_rungraph(self, graph: F8RuntimeGraph) -> None:
        """
        Register/unregister local runtime nodes based on the latest rungraph snapshot.
        """
        if self._service_node is None:
            await self.start()
        service_class = self._service_class()

        want_operator_nodes: list[F8RuntimeNode] = []
        service_snapshot: F8RuntimeNode | None = None
        for node in graph.nodes:
            if service_class and str(node.serviceClass) != service_class:
                continue
            operator_class = node.operatorClass
            is_service_node = operator_class is None or isinstance(operator_class, msgspec.UnsetType)
            if is_service_node:
                if node.nodeId == str(self._bus.service_id):
                    service_snapshot = node
                continue
            want_operator_nodes.append(node)

        if service_snapshot is not None and self._service_node is not None:
            self._service_node.data_in_ports = [str(port.name) for port in (service_snapshot.dataInPorts or [])]
            self._service_node.data_out_ports = [str(port.name) for port in (service_snapshot.dataOutPorts or [])]
            self._service_node.state_fields = [str(field.name) for field in (service_snapshot.stateFields or [])]

        want_ids = {str(node.nodeId) for node in want_operator_nodes}

        for node_id in list(self._operator_nodes.keys()):
            if node_id in want_ids:
                continue
            try:
                self._bus.unregister_node(node_id)
            except Exception as exc:
                log.error("failed to unregister runtime node node_id=%s", node_id, exc_info=exc)
            self._operator_nodes.pop(node_id, None)

        for node in want_operator_nodes:
            node_id = str(node.nodeId)
            if node_id in self._operator_nodes:
                existing = self._operator_nodes.get(node_id)
                if existing is not None and self._needs_recreate(existing, node):
                    try:
                        self._bus.unregister_node(node_id)
                    except Exception as exc:
                        log.error("failed to unregister recreated node node_id=%s", node_id, exc_info=exc)
                    self._operator_nodes.pop(node_id, None)
                else:
                    continue
            initial_state = self._node_initial_state(node)
            try:
                runtime_node = self._registry.create_operator_node(
                    node_id=node_id,
                    node=node,
                    initial_state=initial_state,
                )
            except OperatorFactoryNotRegistered:
                log.error(
                    "missing operator runtime factory service_class=%s operator_class=%s node_id=%s",
                    node.serviceClass,
                    node.operatorClass,
                    node_id,
                )
                runtime_node = None
            except Exception as exc:
                log.error("failed to create runtime node node_id=%s", node_id, exc_info=exc)
                runtime_node = None
            if runtime_node is None:
                continue
            if not isinstance(runtime_node, OperatorNode):
                log.error("runtime node has invalid type node_id=%s type=%s", node_id, type(runtime_node).__name__)
                continue
            runtime_node.data_in_ports = [str(port.name) for port in (node.dataInPorts or [])]
            runtime_node.data_out_ports = [str(port.name) for port in (node.dataOutPorts or [])]
            runtime_node.state_fields = [str(field.name) for field in (node.stateFields or [])]
            runtime_node.exec_in_ports = [str(port) for port in (node.execInPorts or [])]
            runtime_node.exec_out_ports = [str(port) for port in (node.execOutPorts or [])]
            self._operator_nodes[node_id] = runtime_node
            try:
                self._bus.register_node(runtime_node)
            except Exception as exc:
                log.error("failed to register runtime node node_id=%s", node_id, exc_info=exc)
                self._operator_nodes.pop(node_id, None)
                continue

    async def on_rungraph(self, graph: F8RuntimeGraph) -> None:
        await self.apply_rungraph(graph)

    async def validate_rungraph(self, graph: F8RuntimeGraph) -> None:
        _ = graph

    def _service_class(self) -> str:
        configured_service_class = str(self._config.service_class or "").strip()
        if configured_service_class:
            return configured_service_class
        return str(self._bus.service_class or "").strip()

    @staticmethod
    def _needs_recreate(node: OperatorNode, snapshot: F8RuntimeNode) -> bool:
        desired_in = [str(port.name) for port in (snapshot.dataInPorts or [])]
        desired_out = [str(port.name) for port in (snapshot.dataOutPorts or [])]
        desired_state = [str(field.name) for field in (snapshot.stateFields or [])]

        current_in = [str(port) for port in list(node.data_in_ports or [])]
        current_out = [str(port) for port in list(node.data_out_ports or [])]
        current_state = [str(field) for field in list(node.state_fields or [])]

        if current_in != desired_in or current_out != desired_out or current_state != desired_state:
            return True

        desired_exec_in = [str(port) for port in (snapshot.execInPorts or [])]
        desired_exec_out = [str(port) for port in (snapshot.execOutPorts or [])]
        current_exec_in = [str(port) for port in list(node.exec_in_ports or [])]
        current_exec_out = [str(port) for port in list(node.exec_out_ports or [])]
        return current_exec_in != desired_exec_in or current_exec_out != desired_exec_out

    @staticmethod
    def _node_initial_state(node: F8RuntimeNode) -> dict[str, Any]:
        initial_state: dict[str, Any] = {}
        values = node.stateValues or {}
        if not values:
            return initial_state
        for key, value in dict(values).items():
            initial_state[str(key)] = unwrap_json_value(value)
        return initial_state

__all__ = ["ServiceHost", "ServiceHostConfig"]

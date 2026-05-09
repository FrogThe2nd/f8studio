from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from f8pystudio.bridge.process_manager import (
    ServiceProcessConfig,
    ServiceProcessManager,
    ServiceProcessMatch,
    ServiceProcessTerminateResult,
    find_service_processes_by_service_id,
    terminate_service_processes_by_service_id,
)


@dataclass(frozen=True)
class StartServiceRequest:
    config: ServiceProcessConfig
    on_output: Callable[[str, str], None] | None = None


@dataclass(frozen=True)
class StopServiceRequest:
    service_id: str


@dataclass(frozen=True)
class StopServiceResult:
    service_id: str
    success: bool


class ServiceProcessGateway(Protocol):
    def service_ids(self) -> list[str]: ...

    def external_processes(self, service_id: str) -> list[ServiceProcessMatch]: ...

    def terminate_external_processes(self, service_id: str) -> ServiceProcessTerminateResult: ...

    def is_running(self, service_id: str) -> bool: ...

    def start(self, req: StartServiceRequest) -> None: ...

    def stop(self, req: StopServiceRequest) -> StopServiceResult: ...


@dataclass(frozen=True)
class LocalServiceProcessGateway:
    manager: ServiceProcessManager

    def service_ids(self) -> list[str]:
        return [str(service_id) for service_id in self.manager.service_ids()]

    def external_processes(self, service_id: str) -> list[ServiceProcessMatch]:
        tracked_ids = set(str(item) for item in self.manager.service_ids())
        if str(service_id) in tracked_ids:
            return []
        return find_service_processes_by_service_id(str(service_id), use_cached_windows_rows=True)

    def terminate_external_processes(self, service_id: str) -> ServiceProcessTerminateResult:
        return terminate_service_processes_by_service_id(str(service_id))

    def is_running(self, service_id: str) -> bool:
        return bool(self.manager.is_running(str(service_id)))

    def start(self, req: StartServiceRequest) -> None:
        self.manager.start(req.config, on_output=req.on_output)

    def stop(self, req: StopServiceRequest) -> StopServiceResult:
        service_id = str(req.service_id)
        success = bool(self.manager.stop(service_id))
        return StopServiceResult(service_id=service_id, success=success)

from __future__ import annotations

from f8pysdk.app import ServiceCliTemplate, ServiceRuntimeConfig
from f8pysdk.registry import RuntimeNodeRegistry

from .constants import DETECTION_SORTER_SERVICE_CLASS
from .node_registry import register_specs


class DlDetectionSorterService(ServiceCliTemplate):
    @property
    def service_class(self) -> str:
        return DETECTION_SORTER_SERVICE_CLASS

    def build_runtime_config(self, *, service_id: str, nats_url: str) -> ServiceRuntimeConfig:
        # This service is purely reactive to inbound data edges (it does not `pull()` inputs),
        # so it must run with push-based delivery to invoke `node.on_data(...)`.
        return ServiceRuntimeConfig.from_values(
            service_id=service_id,
            service_class=self.service_class,
            nats_url=nats_url,
            data_delivery="push",
        )

    def register_specs(self, registry: RuntimeNodeRegistry) -> None:
        register_specs(registry)


def main(argv: list[str] | None = None) -> int:
    return DlDetectionSorterService().cli(argv, program_name=DETECTION_SORTER_SERVICE_CLASS)


if __name__ == "__main__":
    raise SystemExit(main())

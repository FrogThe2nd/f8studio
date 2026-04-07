from __future__ import annotations

from f8pysdk.app import ServiceCliTemplate
from f8pysdk.registry import RuntimeNodeRegistry

from .constants import OPTFLOW_SERVICE_CLASS
from .node_registry import register_specs


class DlOptflowService(ServiceCliTemplate):
    @property
    def service_class(self) -> str:
        return OPTFLOW_SERVICE_CLASS

    def register_specs(self, registry: RuntimeNodeRegistry) -> None:
        register_specs(registry)


def main(argv: list[str] | None = None) -> int:
    return DlOptflowService().cli(argv, program_name=OPTFLOW_SERVICE_CLASS)


if __name__ == "__main__":
    raise SystemExit(main())

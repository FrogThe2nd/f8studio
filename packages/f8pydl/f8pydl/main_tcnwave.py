from __future__ import annotations

from f8pysdk.app import ServiceCliTemplate
from f8pysdk.registry import RuntimeNodeRegistry

from .constants import TCNWAVE_SERVICE_CLASS
from .node_registry import register_specs


class DlTcnWaveService(ServiceCliTemplate):
    @property
    def service_class(self) -> str:
        return TCNWAVE_SERVICE_CLASS

    def register_specs(self, registry: RuntimeNodeRegistry) -> None:
        register_specs(registry)


def main(argv: list[str] | None = None) -> int:
    return DlTcnWaveService().cli(argv, program_name=TCNWAVE_SERVICE_CLASS)


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from typing import Any

from f8pysdk.codec import dump_json
from f8pysdk.service_runtime_tools.inventory.catalog import ServiceCatalog
from f8pysdk.specs import F8OperatorSpec, F8ServiceSpec, UNSET


def service_library_payload(params: dict[str, Any]) -> dict[str, Any]:
    query = str(params.get("query") or "").strip().lower()
    limit = max(1, min(int(params.get("limit") or 200), 1000))
    catalog = ServiceCatalog.instance()
    services: list[dict[str, Any]] = []
    for spec in sorted(catalog.services.all(), key=lambda item: str(item.serviceClass)):
        summary = _service_spec_summary(spec)
        search_text = " ".join(
            str(summary.get(key, "")) for key in ("serviceClass", "label", "description", "tags")
        ).lower()
        if query and query not in search_text:
            continue
        services.append(summary)
        if len(services) >= limit:
            break
    return {"services": services, "count": len(services)}


def operator_library_payload(params: dict[str, Any]) -> dict[str, Any]:
    service_class = str(params.get("serviceClass") or "").strip()
    query = str(params.get("query") or "").strip().lower()
    limit = max(1, min(int(params.get("limit") or 300), 1500))
    catalog = ServiceCatalog.instance()
    operators = catalog.operators.query(service_class or None)
    out: list[dict[str, Any]] = []
    for spec in sorted(operators, key=lambda item: (str(item.serviceClass), str(item.operatorClass))):
        summary = _operator_spec_summary(spec)
        search_text = " ".join(
            str(summary.get(key, "")) for key in ("serviceClass", "operatorClass", "label", "description", "tags")
        ).lower()
        if query and query not in search_text:
            continue
        out.append(summary)
        if len(out) >= limit:
            break
    return {"operators": out, "count": len(out)}


def operator_detail_payload(params: dict[str, Any]) -> dict[str, Any]:
    service_class = _required_text(params, "serviceClass")
    operator_class = _required_text(params, "operatorClass")
    catalog = ServiceCatalog.instance()
    operator_spec = catalog.operators.get(service_class, operator_class)
    payload: dict[str, Any] = {"operator": _spec_to_json(operator_spec)}
    if catalog.services.has(service_class):
        payload["service"] = _spec_to_json(catalog.services.get(service_class))
    return payload


def _required_text(params: dict[str, Any], key: str) -> str:
    value = str(params.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _service_spec_summary(spec: F8ServiceSpec) -> dict[str, Any]:
    return {
        "serviceClass": str(spec.serviceClass),
        "label": str(spec.label),
        "description": _text_or_empty(spec.description),
        "tags": list(spec.tags or []),
        "paletteCategory": _text_or_empty(spec.paletteCategory),
        "stateFieldCount": len(list(spec.stateFields or [])),
        "commandCount": len(list(spec.commands or [])),
        "dataInPortCount": len(list(spec.dataInPorts or [])),
        "dataOutPortCount": len(list(spec.dataOutPorts or [])),
    }


def _operator_spec_summary(spec: F8OperatorSpec) -> dict[str, Any]:
    return {
        "serviceClass": str(spec.serviceClass),
        "operatorClass": str(spec.operatorClass),
        "label": str(spec.label),
        "description": _text_or_empty(spec.description),
        "tags": list(spec.tags or []),
        "paletteCategory": _text_or_empty(spec.paletteCategory),
        "stateFieldCount": len(list(spec.stateFields or [])),
        "commandCount": len(list(spec.commands or [])),
        "execInPorts": list(spec.execInPorts or []),
        "execOutPorts": list(spec.execOutPorts or []),
        "dataInPorts": [_port_summary(port) for port in list(spec.dataInPorts or [])],
        "dataOutPorts": [_port_summary(port) for port in list(spec.dataOutPorts or [])],
    }


def _port_summary(port: Any) -> dict[str, Any]:
    return {
        "name": str(port.name or ""),
        "description": _text_or_empty(port.description),
        "required": bool(port.required) if port.required is not UNSET else False,
    }


def _spec_to_json(spec: F8ServiceSpec | F8OperatorSpec) -> dict[str, Any]:
    payload = dump_json(spec, mode="json", by_alias=True)
    if isinstance(payload, dict):
        return payload
    return {}


def _text_or_empty(value: Any) -> str:
    if value is None or value is UNSET:
        return ""
    return str(value)

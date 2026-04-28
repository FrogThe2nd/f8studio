from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
import re
from typing import Any

import msgspec


def _run_cli_codegen(*, protocol_path: Path, output_path: Path) -> None:
    command = [
        sys.executable,
        "-m",
        "datamodel_code_generator",
        "--input",
        str(protocol_path),
        "--input-file-type",
        "openapi",
        "--output-model-type",
        "msgspec.Struct",
        "--output",
        str(output_path),
        "--use-default",
        "--strict-nullable",
        "--allow-population-by-field-name",
        "--use-title-as-name",
        "--use-annotated",
        "--keyword-only",
    ]
    subprocess.run(command, check=True, cwd=protocol_path.parent.parent)


def _run_python_codegen(*, protocol_path: Path, output_path: Path) -> None:
    from datamodel_code_generator import DataModelType, InputFileType, generate

    generate(
        protocol_path,
        input_file_type=InputFileType.OpenAPI,
        output=output_path,
        output_model_type=DataModelType.MsgspecStruct,
        use_annotated=True,
        use_title_as_name=True,
        strict_nullable=True,
        field_constraints=True,
        use_default=True,
        extra_template_data=defaultdict(
            dict,
            {
                "#all#": {
                    "base_class_kwargs": {
                        "kw_only": True,
                    }
                }
            },
        ),
    )


def _import_module(module_path: Path) -> Any:
    module_name = f"f8_generated_msgspec_{module_path.stat().st_mtime_ns}"
    spec = importlib.util.spec_from_file_location(module_name, str(module_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load module spec for {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _smoke_test_generated(output_path: Path) -> None:
    generated = _import_module(output_path)
    runtime_graph_type = getattr(generated, "F8RuntimeGraph", None)
    if runtime_graph_type is None:
        raise RuntimeError("generated module missing F8RuntimeGraph")
    rungraph_req_type = getattr(generated, "F8SetRungraphRequest", None)
    rungraph_args_type = getattr(generated, "F8SetRungraphArgs", None)
    rungraph_reply_type = getattr(generated, "F8SetRungraphReply", None)
    cmd_req_type = getattr(generated, "F8CommandInvokeRequest", None)
    cmd_reply_type = getattr(generated, "F8CommandInvokeReply", None)
    if rungraph_req_type is None or rungraph_args_type is None or rungraph_reply_type is None:
        raise RuntimeError("generated module missing rungraph control-plane models")
    if cmd_req_type is None or cmd_reply_type is None:
        raise RuntimeError("generated module missing command control-plane models")

    payload = {
        "graphId": "g-smoke",
        "revision": "r1",
        "nodes": [
            {
                "nodeId": "op1",
                "serviceId": "svc1",
                "serviceClass": "svc.a",
                "operatorClass": "svc.a.op1",
                "stateFields": [
                    {
                        "name": "x",
                        "valueSchema": {"type": "number"},
                        "access": "rw",
                    }
                ],
                "stateValues": {"x": 1.0},
            }
        ],
        "edges": [],
    }
    model = msgspec.convert(payload, type=runtime_graph_type)
    _ = msgspec.to_builtins(model)

    rungraph_req_payload = {
        "reqId": "req-rungraph-smoke",
        "args": {"graph": payload},
        "meta": {"source": "smoke"},
    }
    rungraph_req = msgspec.convert(rungraph_req_payload, type=rungraph_req_type)
    _ = msgspec.to_builtins(rungraph_req)

    cmd_req_payload = {
        "reqId": "req-cmd-smoke",
        "call": "ping",
        "args": {"x": 1},
        "meta": {"actor": "smoke"},
    }
    cmd_req = msgspec.convert(cmd_req_payload, type=cmd_req_type)
    _ = msgspec.to_builtins(cmd_req)

    cmd_reply_payload = {
        "reqId": "req-cmd-smoke",
        "ok": True,
        "result": {"pong": True},
        "error": None,
    }
    cmd_reply = msgspec.convert(cmd_reply_payload, type=cmd_reply_type)
    _ = msgspec.to_builtins(cmd_reply)


def _postprocess_generated(output_path: Path) -> None:
    source = output_path.read_text(encoding="utf-8")
    updated = re.sub(
        r"class F8ComponentRecord\(Struct, kw_only=True\):",
        "class F8ComponentRecord(Struct, kw_only=True, forbid_unknown_fields=True):",
        source,
        count=1,
    )
    if updated == source:
        raise RuntimeError("generated module is missing F8ComponentRecord for post-processing")
    output_path.write_text(updated, encoding="utf-8")


def _generate_with_fallback(*, protocol_path: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cli_error: Exception | None = None
    smoke_error: Exception | None = None

    try:
        _run_cli_codegen(protocol_path=protocol_path, output_path=output_path)
    except Exception as exc:  # pragma: no cover - boundary path
        cli_error = exc
    else:
        try:
            _postprocess_generated(output_path)
            _smoke_test_generated(output_path)
            return
        except Exception as exc:  # pragma: no cover - boundary path
            smoke_error = exc

    _run_python_codegen(protocol_path=protocol_path, output_path=output_path)
    _postprocess_generated(output_path)
    _smoke_test_generated(output_path)

    if cli_error is not None:
        print(
            f"[protocol_codegen_msgspec] CLI codegen failed, python fallback succeeded: "
            f"{type(cli_error).__name__}: {cli_error}",
            file=sys.stderr,
        )
    elif smoke_error is not None:
        print(
            f"[protocol_codegen_msgspec] CLI smoke test failed, python fallback succeeded: "
            f"{type(smoke_error).__name__}: {smoke_error}",
            file=sys.stderr,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate msgspec models from OpenAPI protocol schema.")
    parser.add_argument("--protocol", required=True, help="Path to protocol OpenAPI YAML")
    parser.add_argument("--output", required=True, help="Path to generated python output file")
    args = parser.parse_args()

    protocol_path = Path(str(args.protocol)).resolve()
    output_path = Path(str(args.output)).resolve()
    _generate_with_fallback(protocol_path=protocol_path, output_path=output_path)


if __name__ == "__main__":
    main()

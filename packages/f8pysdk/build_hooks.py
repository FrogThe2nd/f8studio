from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        project_root = Path(self.root).resolve()
        repo_root = project_root.parent.parent
        protocol_path = repo_root / "schemas" / "protocol.yml"
        output_path = project_root / "f8pysdk" / "generated" / "__init__.py"
        script_path = repo_root / "scripts" / "protocol_codegen_msgspec.py"

        output_path.parent.mkdir(parents=True, exist_ok=True)

        command = [
            sys.executable,
            str(script_path),
            "--protocol",
            str(protocol_path),
            "--output",
            str(output_path),
        ]
        subprocess.run(command, check=True, cwd=repo_root)

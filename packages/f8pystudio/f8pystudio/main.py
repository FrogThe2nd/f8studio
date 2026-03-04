from __future__ import annotations

import argparse
import os

from .app_logging import configure_root_logging_from_env


def main(argv: list[str] | None = None) -> int:
    configure_root_logging_from_env()

    parser = argparse.ArgumentParser(description="F8PyStudio")
    parser.add_argument("--describe", action="store_true", help="Output the service description in JSON format")
    parser.add_argument(
        "--discovery-live",
        action="store_true",
        help="Disable static describe.json/inline describe fast-paths; always run describe subprocesses.",
    )
    args = parser.parse_args(argv)

    from .pystudio_program import PyStudioProgram

    if args.discovery_live:
        os.environ["F8_DISCOVERY_DISABLE_STATIC_DESCRIBE"] = "1"

    prog = PyStudioProgram()
    if args.describe:
        print(prog.describe_json_text())
        return 0
    return prog.run()


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import logging
import os


def _main(argv: list[str] | None = None) -> int:
    if not logging.getLogger().handlers:
        raw = (os.environ.get("F8_LOG_LEVEL") or "").strip().upper()
        level = getattr(logging, raw, logging.WARNING) if raw else logging.WARNING
        logging.basicConfig(level=level, format="%(levelname)s:%(name)s:%(message)s")

    from .proclauncher_service import build_app

    return build_app().cli(argv, program_name="F8ProcLauncher")


if __name__ == "__main__":
    raise SystemExit(_main())

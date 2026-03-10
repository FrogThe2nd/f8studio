from __future__ import annotations

import argparse
import logging

from .server import ServerConfig, run_server


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m f8pystudio.web", description="Headless PyStudio Web backend.")
    ap.add_argument("--http-host", default="127.0.0.1")
    ap.add_argument("--http-port", type=int, default=8765)
    ap.add_argument("--ws-host", default="127.0.0.1")
    ap.add_argument("--ws-port", type=int, default=8766)
    ap.add_argument("--nats-url", default="nats://127.0.0.1:4222")
    ap.add_argument("--studio-service-id", default="studio")
    ap.add_argument("--log-level", default="INFO", help="Python logging level (DEBUG/INFO/WARNING/ERROR).")
    return ap


def main() -> int:
    ap = _build_parser()
    args = ap.parse_args()
    level_name = str(args.log_level or "INFO").strip().upper()
    level_by_name: dict[str, int] = {
        "CRITICAL": logging.CRITICAL,
        "ERROR": logging.ERROR,
        "WARNING": logging.WARNING,
        "INFO": logging.INFO,
        "DEBUG": logging.DEBUG,
    }
    level = int(level_by_name.get(level_name, logging.INFO))
    logging.basicConfig(level=int(level), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = ServerConfig(
        http_host=str(args.http_host),
        http_port=int(args.http_port),
        ws_host=str(args.ws_host),
        ws_port=int(args.ws_port),
        nats_url=str(args.nats_url),
        studio_service_id=str(args.studio_service_id),
    )
    run_server(cfg=cfg)
    return 0


raise SystemExit(main())

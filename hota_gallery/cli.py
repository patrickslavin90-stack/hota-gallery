"""Command-line entry points - same shape as sacn2wiz's cli.py."""

from __future__ import annotations

import argparse
import logging
import threading

from .config import ConfigError, load_config
from .engine import Engine

log = logging.getLogger("hota_gallery")


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        log.error("config invalid:\n%s", exc)
        return 1
    print(f"\n{args.config} is valid\n")
    print(f"  fixtures   {len(cfg['fixtures'])}")
    print(f"  zones      {len(cfg['zones'])}")
    print(f"  fps        {cfg['fps']}")
    print(f"  look       {cfg['current_look']}")
    print(f"  schedule   {len(cfg['schedule'])} entries")
    print()
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .clock_chime import ClockChime
    from .randomizer import Randomizer
    from .scheduler import Scheduler
    from .webapp import create_server

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        log.error("config invalid:\n%s", exc)
        return 1

    engine = Engine(cfg, config_path=args.config)
    engine.start_background()

    httpd = create_server(engine, args.config, host=args.host, port=args.port or cfg["web_port"])
    server_thread = threading.Thread(target=httpd.serve_forever, name="webui", daemon=True)
    server_thread.start()
    log.info("web UI at http://%s:%d", args.host, args.port or cfg["web_port"])

    # Same Store the API uses - a scheduled look change is saved and
    # reloaded exactly like a manual one, not a separate code path.
    scheduler = Scheduler(httpd.store.get_config, httpd.store.set_default_look, httpd.store.set_fixture_looks)
    scheduler.start_background()

    randomizer = Randomizer(httpd.store.get_config, httpd.store.set_fixture_looks)
    randomizer.start_background()

    clock_chime = ClockChime(httpd.store.get_config, httpd.store.set_fixture_looks)
    clock_chime.start_background()

    from .engine import sd_notify
    sd_notify("READY=1\nSTATUS=serving")

    try:
        server_thread.join()
    except KeyboardInterrupt:
        pass
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    parser = argparse.ArgumentParser(prog="hota-gallery")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="validate a config file")
    p_validate.add_argument("--config", required=True)
    p_validate.set_defaults(func=cmd_validate)

    p_serve = sub.add_parser("serve", help="run the engine + web API")
    p_serve.add_argument("--config", required=True)
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=None)
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    return args.func(args)

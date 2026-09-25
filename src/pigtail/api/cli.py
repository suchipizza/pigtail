"""`pigtail ui hash-password` and `pigtail ui serve` (D1 preview, R13.3, R14.2)."""

from __future__ import annotations

import argparse
import getpass
import sys


def cmd_hash_password(_args: argparse.Namespace) -> int:
    """Print an argon2id hash for PIGTAIL_OPERATOR_PASSWORD_HASH (the password is never stored)."""
    from pigtail.api.auth import hash_password

    if sys.stdin.isatty():
        pw = getpass.getpass("operator password: ")
        if getpass.getpass("repeat: ") != pw:
            print("passwords differ", file=sys.stderr)
            return 2
    else:
        pw = sys.stdin.readline().rstrip("\n")
    try:
        print(hash_password(pw))
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Serve the read-only API and the built UI in one process."""
    import uvicorn

    from pigtail.api.app import create_app
    from pigtail.api.settings import UISettings
    from pigtail.capture.snapshots import build_store
    from pigtail.config import Settings
    from pigtail.db.migrate import migrate
    from pigtail.logsafe import configure_logging

    configure_logging()  # CB-18
    try:
        s = Settings.from_env()
        ui = UISettings.from_env()
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    if not s.database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    migrate(s.database_url)
    if not (ui.dist_dir / "index.html").is_file():
        print(
            f"warning: UI not built at {ui.dist_dir}; the API works, pages return 503. "
            "Run `pnpm --dir ui install && pnpm --dir ui build`.",
            file=sys.stderr,
        )
    if args.host not in ("127.0.0.1", "::1", "localhost"):
        print(
            f"warning: binding to {args.host}. The app holds private data: put it behind TLS "
            "and keep it off the public internet (docs/guides/operator.md).",
            file=sys.stderr,
        )
    app = create_app(conninfo=s.database_url, ui=ui, store=build_store(s), settings=s)
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
        server_header=False,
        # Access logs carry client IPs; off by default (CB-18/CB-19: the audit log keeps only a
        # keyed hash of the truncated address). `--access-log` turns them on for debugging.
        access_log=args.access_log,
        proxy_headers=args.proxy_headers,
        forwarded_allow_ips=args.forwarded_allow_ips,
    )
    return 0


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    ui = sub.add_parser("ui", help="private web app: D1 preview (R13.3, R14.2)")
    ui_sub = ui.add_subparsers(dest="ui_command", required=True)
    hp = ui_sub.add_parser(
        "hash-password", help="argon2id hash for PIGTAIL_OPERATOR_PASSWORD_HASH (reads stdin)"
    )
    hp.set_defaults(func=cmd_hash_password)
    sv = ui_sub.add_parser("serve", help="serve the API and the built UI")
    sv.add_argument("--host", default="127.0.0.1", help="bind address (default loopback only)")
    sv.add_argument("--port", type=int, default=8080)
    sv.add_argument(
        "--proxy-headers",
        action="store_true",
        help="trust X-Forwarded-* from --forwarded-allow-ips (behind a TLS reverse proxy)",
    )
    sv.add_argument("--forwarded-allow-ips", default="127.0.0.1")
    sv.add_argument(
        "--access-log", action="store_true", help="enable uvicorn access logs (they contain IPs)"
    )
    sv.set_defaults(func=cmd_serve)

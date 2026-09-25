"""Child entry point for scheduled jobs: CB-18 log filter first, then the normal CLI.

python -m pigtail.scheduler.child capture hn-ranks --once
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    from pigtail.logsafe import configure_logging

    configure_logging()  # CB-18: installed before any job code can log
    from pigtail.cli import main as cli_main

    return cli_main(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())

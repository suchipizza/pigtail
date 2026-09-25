"""Read-only HTTP API behind the private UI (PRD R14.2) and operator authentication (R13.3, CB-19).

`create_app()` builds the FastAPI app; `pigtail ui serve` runs it together with the built UI
(`ui/dist`). Every `/api` route except login needs an operator session. Database access goes
through a pool whose sessions are read-only (`default_transaction_read_only=on`); only the
session and audit tables are written, through a separate small pool.
"""

from pigtail.api.app import create_app
from pigtail.api.settings import UISettings

__all__ = ["UISettings", "create_app"]

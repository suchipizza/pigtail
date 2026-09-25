"""`/healthz` and `/livez` on a stdlib HTTP server (M1-T21). No web framework needed.

- `/livez`: 200 while the scheduler loop ticks, else 503.
- `/healthz`: the full health report as JSON (cached for `cache_seconds`). 200 while the loop
  ticks and the database is reachable; 503 otherwise. Job failures, doctor warnings and disk
  usage are reported in the body (`status`: ok | warn | fail) and alerted, but do not make the
  container unhealthy: restarting it would not fix them.

Bind to 127.0.0.1 by default (`PIGTAIL_HEALTH_BIND`); the compose file binds 0.0.0.0 inside the
container and publishes the port on the host's loopback only.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

log = logging.getLogger("pigtail.scheduler.http")
DEFAULT_PORT = 8787


class HealthServer:
    def __init__(
        self,
        report: Callable[[], dict[str, Any]],
        alive: Callable[[], bool],
        *,
        bind: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
        cache_seconds: float = 30.0,
    ) -> None:
        self._report = report
        self._alive = alive
        self.cache_seconds = cache_seconds
        self._cached: tuple[float, dict[str, Any]] | None = None
        self._mu = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                path = self.path.split("?", 1)[0]
                if path == "/livez":
                    ok = outer._alive()
                    self._send(200 if ok else 503, {"alive": ok})
                elif path == "/healthz":
                    code, body = outer.healthz()
                    self._send(code, body)
                else:
                    self._send(404, {"error": "not found"})

            def _send(self, code: int, body: dict[str, Any]) -> None:
                data = json.dumps(body, default=str).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: Any) -> None:  # quiet access log
                return

        self.httpd = ThreadingHTTPServer((bind, port), Handler)
        self.httpd.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1])

    def healthz(self) -> tuple[int, dict[str, Any]]:
        with self._mu:
            now = time.monotonic()
            if self._cached is None or now - self._cached[0] > self.cache_seconds:
                try:
                    body = self._report()
                except Exception as e:
                    body = {"status": "fail", "error": type(e).__name__}
                self._cached = (now, body)
            body = dict(self._cached[1])
        alive = self._alive()
        db = next((c for c in body.get("checks", []) if c.get("name") == "database"), None)
        db_ok = db is None or db.get("status") != "fail"
        body["alive"] = alive
        return (200 if alive and db_ok and "error" not in body else 503), body

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self.httpd.serve_forever, name="healthz", daemon=True
        )
        self._thread.start()
        log.info("health endpoint on port %d (/healthz, /livez)", self.port)

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

"""Generate the SYNTHETIC GH Archive hourly fixtures (M1-T3). All logins/repos are fake.

Run: `uv run python tests/fixtures/gharchive/generate.py` (deterministic output, gzip mtime=0).

Scenario, hours 2026-09-20 00..02 UTC:
- org-a/repo-1 (id 1000001): 60 + 70 + 10 distinct stargazers (user0001..user0140); ~70 % of
  them also push elsewhere (not star-only). One duplicate star, one star from a `[bot]`, one fork
  from `ci-bot`. Crosses 100 filtered stars in 48 h at hour 01 -> one velocity case.
- org-b/repo-2 (id 1000002): hour 01 has 120 stars from farm0001..farm0120 who do nothing else
  (lockstep burst -> filtered 0); hour 02 has 5 organic stars. No case.
- org-c/repo-3 (id 1000003): 3 stars and 2 forks per hour. No case.
- Noise: IssuesEvent, a PushEvent by dependabot[bot], one malformed line.
"""

from __future__ import annotations

import gzip
import io
import json
from pathlib import Path

HERE = Path(__file__).parent
REPOS = {
    1000001: "org-a/repo-1",
    1000002: "org-b/repo-2",
    1000003: "org-c/repo-3",
    1000009: "org-a/repo-9",
}


class Hour:
    def __init__(self, hour: int) -> None:
        self.hour = hour
        self.lines: list[str] = []
        self.n = 0

    def ev(
        self, etype: str, login: str, repo: int, payload: dict[str, object] | None = None
    ) -> None:
        self.n += 1
        minute = self.n % 60
        self.lines.append(
            json.dumps(
                {
                    "id": f"{self.hour:02d}{self.n:06d}",
                    "type": etype,
                    "actor": {"id": 10_000 + self.n, "login": login},
                    "repo": {"id": repo, "name": REPOS[repo]},
                    "payload": payload or {},
                    "public": True,
                    "created_at": f"2026-09-20T{self.hour:02d}:{minute:02d}:00Z",
                },
                sort_keys=True,
            )
        )

    def write(self) -> None:
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
            gz.write(("\n".join(self.lines) + "\n").encode())
        (HERE / f"2026-09-20-{self.hour}.json.gz").write_bytes(buf.getvalue())


def stargazers(hour: Hour, repo: int, first: int, count: int) -> None:
    for i in range(first, first + count):
        login = f"user{i:04d}"
        hour.ev("WatchEvent", login, repo, {"action": "started"})
        if i % 10 < 7:  # ~70 % have other activity in the window
            hour.ev("PushEvent", login, 1000009, {"size": 1})


def main() -> None:
    h0, h1, h2 = Hour(0), Hour(1), Hour(2)
    stargazers(h0, 1000001, 1, 60)
    h0.ev("WatchEvent", "user0001", 1000001, {"action": "started"})  # duplicate star
    h0.ev("WatchEvent", "helper-app[bot]", 1000001, {"action": "started"})
    h0.ev("ForkEvent", "ci-bot", 1000001)
    h0.ev("ForkEvent", "user0002", 1000001)
    stargazers(h1, 1000001, 61, 70)
    stargazers(h2, 1000001, 131, 10)
    for i in range(1, 121):
        h1.ev("WatchEvent", f"farm{i:04d}", 1000002, {"action": "started"})
    stargazers(h2, 1000002, 501, 5)
    for n, h in enumerate((h0, h1, h2)):
        stargazers(h, 1000003, 900 + 3 * n, 3)
        h.ev("ForkEvent", f"user{950 + n:04d}", 1000003)
        h.ev("ForkEvent", f"user{960 + n:04d}", 1000003)
        h.ev("IssuesEvent", f"user{970 + n:04d}", 1000009, {"action": "opened"})
        h.ev("PushEvent", "dependabot[bot]", 1000009, {"size": 1})
    h1.lines.append("{not json")
    for h in (h0, h1, h2):
        h.write()


if __name__ == "__main__":
    main()

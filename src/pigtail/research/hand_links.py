"""Reddit posts added by hand with --reddit-links (owner decision 2026-10-11).

Web search returns almost no reddit.com pages, and published reports never use Reddit's API, so a
person can list the Reddit posts they know about in a small text file, one per line:

    https://www.reddit.com/r/SideProject/comments/1abc23/my_post/ | 2026-04-06 | Post title

Lines starting with # are ignored. Pigtail keeps only the link, date and title (like a web-search
Reddit link) and never reads the post. The person who lists a post is responsible for where they
got it: for published reports, links found by browsing Reddit, not copied from Reddit API results.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pigtail.errors import UsageError

_POST = re.compile(r"^https://(?:www\.|old\.)?reddit\.com/r/[A-Za-z0-9_]+/comments/[a-z0-9]+(?:/[^\s|]*)?$")


@dataclass(frozen=True)
class RedditLink:
    url: str
    day: date
    title: str


def read_reddit_links(path: Path) -> list[RedditLink]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise UsageError(f"Could not read the Reddit links file {path}: {exc.strerror}.") from exc
    out: list[RedditLink] = []
    for n, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|", 2)]
        hint = "Each line is: post link | YYYY-MM-DD | title"
        if len(parts) != 3 or not parts[2]:
            raise UsageError(f"{path}, line {n}: expected 3 parts separated by '|'.", hint=hint)
        url, day, title = parts
        if not _POST.match(url):
            raise UsageError(f"{path}, line {n}: not a Reddit post link: {url}", hint=hint)
        try:
            when = date.fromisoformat(day)
        except ValueError:
            raise UsageError(f"{path}, line {n}: the date must look like 2026-04-06, not {day!r}.", hint=hint) from None
        out.append(RedditLink(url=url, day=when, title=title))
    return out

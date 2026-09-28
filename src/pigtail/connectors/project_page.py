"""A project's own homepage (M23 case evidence; TM-29 conditions per site; ADR-072.7: project
pages only; ADR-086).

One GET of the page named in the repo's homepage field, **only** when:

- it is an `http(s)` URL on a host that is not a person's profile or a social platform
  (`not_project_page`: GitHub user roots, `<user>.github.io` roots unless the repo is that
  site, social networks, blogs by person), and
- the host's `robots.txt` (RFC 9309) allows the path for pigtail's User-Agent. `robots.txt` is
  read without being stored (`Connector.check`); a 4xx answer means no rules (allowed), a 5xx or
  unreachable `robots.txt` means disallowed (RFC 9309 §2.3.1.4), so the page is a gap.

The site's terms are not read by the pipeline: the evidence's `terms_basis` records
`TM-29:<host>:<date>:robots=allowed;terms=not-reviewed` so the operator can review them (TM-29
asks for a per-site check; an open question for the owner, ADR-086). At most one fetch per page
and day (the caller reuses a same-day snapshot). The page is snapshotted as fetched (project
level, `rendered_html` → reliability `medium`, codebook §2.3); coders see its visible text only,
cut down and redacted.
"""

from __future__ import annotations

import re
import urllib.robotparser
from collections.abc import Iterable
from datetime import datetime
from html.parser import HTMLParser
from typing import Any, ClassVar
from urllib.parse import urlsplit

from pigtail.capture.models import Evidence, evidence_id
from pigtail.capture.snapshots import SnapshotMeta, sha256_hex
from pigtail.connectors.base import (
    USER_AGENT,
    Clearance,
    Connector,
    ConnectorDisabled,
    Fetched,
    FetchError,
    NotFound,
    Record,
    TermsMetadata,
)
from pigtail.connectors.github import Probe

TERMS = TermsMetadata(
    terms_url="docs/compliance/terms-memos.md#tm-29-careers-pages-project-websites",
    terms_basis="TM-29 (per site): robots.txt checked for pigtail's User-Agent; project facts only",
    clearance=Clearance.CLEARED_WITH_CONDITIONS,
    commercial_use=None,
    deletion_obligation=None,
    notes="Project homepage, one GET per page per day; person and social hosts refused.",
)

# Hosts whose pages are profiles or posts of people, never a project's own page.
SOCIAL_HOSTS = frozenset(
    {
        "twitter.com",
        "x.com",
        "bsky.app",
        "linkedin.com",
        "facebook.com",
        "instagram.com",
        "threads.net",
        "mastodon.social",
        "youtube.com",
        "youtu.be",
        "reddit.com",
        "news.ycombinator.com",
        "medium.com",
        "dev.to",
        "substack.com",
        "t.me",
        "discord.gg",
        "discord.com",
        "patreon.com",
        "ko-fi.com",
        "buymeacoffee.com",
        "github.com",
        "gitlab.com",
        "gist.github.com",
    }
)
MAX_BYTES = 2_000_000


class PageTooLarge(FetchError):
    pass


def project_page_url(url: str | None, repo_full_name: str) -> tuple[str | None, str | None]:
    """(the URL to fetch, None) when `url` is a project page, else (None, gap reason)."""
    if not url or not url.strip():
        return None, "no_homepage"
    u = url.strip()
    if re.match(r"^[a-z][a-z0-9+.-]*://", u, re.IGNORECASE) and not re.match(
        r"^https?://", u, re.IGNORECASE
    ):
        return None, "not_http"
    if not re.match(r"^https?://", u, re.IGNORECASE):
        u = "https://" + u
    parts = urlsplit(u)
    host = (parts.hostname or "").lower()
    if not host or parts.scheme.lower() not in ("http", "https"):
        return None, "not_http"
    bare = host.removeprefix("www.")
    if bare in SOCIAL_HOSTS or any(bare.endswith("." + h) for h in SOCIAL_HOSTS):
        return None, "not_project_page"
    owner, _, name = repo_full_name.lower().partition("/")
    if bare.endswith(".github.io"):
        site_owner = bare.removesuffix(".github.io")
        path = parts.path.strip("/").lower()
        # a user or org site root is a person's page unless the repo *is* that site
        if not path and name != bare and site_owner == owner:
            return None, "not_project_page"
        if not path and site_owner != owner:
            return None, "not_project_page"
    return u, None


def robots_url(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}/robots.txt"


def robots_allows(status: int | None, body: bytes, url: str, agent: str = USER_AGENT) -> bool:
    """RFC 9309: 2xx → the rules; 4xx → allowed (no rules); 5xx or unreachable → disallowed."""
    if status is None or status >= 500:
        return False
    if 400 <= status < 500:
        return True
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(body.decode("utf-8", errors="replace").splitlines())
    return rp.can_fetch(agent, url) and rp.can_fetch("pigtail", url)


class _Text(HTMLParser):
    SKIP: ClassVar[frozenset[str]] = frozenset(
        {"script", "style", "noscript", "svg", "template", "head"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.title: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self.skip += 1
        if tag in ("p", "br", "li", "h1", "h2", "h3", "h4", "div", "section", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title.append(data)
        if not self.skip:
            self.parts.append(data)


def visible_text(html: bytes) -> str:
    """The page's title and visible text, whitespace collapsed per line (no scripts or styles)."""
    p = _Text()
    try:
        p.feed(html.decode("utf-8", errors="replace"))
        p.close()
    except Exception:  # malformed markup: keep what was read
        pass
    lines = [" ".join(line.split()) for line in "".join(p.parts).splitlines()]
    body = "\n".join(line for line in lines if line)
    title = " ".join("".join(p.title).split())
    return (f"Title: {title}\n" if title else "") + body


class ProjectPageConnector(Connector):
    name: ClassVar[str] = "project_page"
    version: ClassVar[str] = "0.1.0"
    terms: ClassVar[TermsMetadata] = TERMS
    enabled_by_default: ClassVar[bool] = True
    person_level_hold: ClassVar[bool] = False
    rate_per_second: ClassVar[float] = 0.5
    retention_class = "project_level"
    reliability = "medium"  # rendered_html (codebook §2.3)
    timeout_seconds: ClassVar[float] = 20.0

    def terms_basis_for(self, url: str, when: datetime) -> str:
        host = (urlsplit(url).hostname or "").lower()
        return f"TM-29:{host}:{when.date().isoformat()}:robots=allowed;terms=not-reviewed"

    def allowed(self, url: str) -> bool:
        """Whether the host's robots.txt allows `url` (read, never stored)."""
        r = self.check(robots_url(url))
        return robots_allows(r.status, r.data, url)

    def fetch_page(self, url: str, *, repo_id: str | None = None) -> Fetched:
        """One GET of the page, snapshotted with the per-site terms basis (the caller checked
        `allowed` first). Raises `NotFound`/`FetchError` like `fetch`, `PageTooLarge` above
        `MAX_BYTES` (nothing stored)."""
        if not self.enabled:
            raise ConnectorDisabled(f"connector {self.name!r} is disabled")
        resp = self._request(url, None, {"Accept": "text/html,*/*;q=0.5"})
        if resp.status_code == 404:
            raise NotFound(url, 404)
        if not resp.is_success:
            raise FetchError(url, resp.status_code)
        data = resp.content
        if len(data) > MAX_BYTES:
            raise PageTooLarge(url, resp.status_code, "page too large")
        now = self.clock()
        meta = SnapshotMeta(
            source=self.name,
            url=url,
            fetched_at=now,
            collector_version=self.collector_version,
            terms_basis=self.terms_basis_for(url, now),
            content_type=resp.headers.get("Content-Type"),
        )
        h = self.store.put(data, meta)  # snapshot or drop
        ev = Evidence(
            id=evidence_id(self.name, url, h),
            source=self.name,
            url=url,
            fetched_at=now,
            content_hash=h,
            snapshot_ref=self.store.ref(h),
            content_type=meta.content_type,
            http_status=resp.status_code,
            reliability=self.reliability,
            terms_basis=meta.terms_basis,
            retention_class="project_level",
            deletion_state="present",
            collector_version=self.collector_version,
            case_id=None,
            repo_id=repo_id,
            run_id=self.run.id if self.run is not None else None,
        )
        if self.evidence_sink is not None:
            self.evidence_sink(ev)
        return Fetched(data=data, content_hash=h, evidence=ev, meta=meta)

    def probe(
        self, url: str, *, etag: str | None = None, last_modified: str | None = None
    ) -> Probe:
        """Conditional GET for evidence decay: robots.txt re-read first; nothing stored."""
        if not self.allowed(url):
            return Probe(None, None, None, None, note="robots_disallowed")
        hdrs: dict[str, str] = {}
        if etag:
            hdrs["If-None-Match"] = etag
        if last_modified:
            hdrs["If-Modified-Since"] = last_modified
        try:
            resp = self._request(url, None, hdrs)
        except Exception:
            return Probe(None, None, None, None)
        ok = resp.is_success
        return Probe(
            resp.status_code,
            sha256_hex(resp.content) if ok else None,
            resp.headers.get("ETag"),
            resp.headers.get("Last-Modified"),
        )

    def _parse(self, data: bytes, meta: SnapshotMeta) -> Iterable[Record]:
        yield {"url": meta.url, "text_chars": len(visible_text(data))}

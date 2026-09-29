"""Report facts per case (M24, `report-facts-v1`; ADR-088 item 4, ADR-089; PRD R5.1, R19.1;
DELIVERABLES D1/D2): what a case posted, where, when and under which title, the assets its README
showed at the anchor, its amplifiers by role and bucket, and its star trajectory around each
event with the bursts derived from star-history timestamps. **Deterministic code, no LLM.**

Every fact cites an evidence id of the case (snapshot or drop): the stored `launch_events`,
`releases`, `readme_at_anchor` and `repo_metadata` items of the case-evidence stage, plus two
report-only derived items written here:

- `hn_stories`: the HN stories of the case's HN launch events (Show HN, Launch HN, the first HN
  mention when it is a story) by item id, one Algolia request (`HNStoryMetaConnector`,
  project-level story metadata only: title, url, points, time; the raw page dropped at parse)
  plus one for Algolia's `front_page` tag. Titles are scrubbed of identifiers and the owner
  login (`[owner]`).
- `star_trajectory`: the case's daily net stars (`repo_star_daily`, ended days, "unfiltered,
  anomaly-checked", ADR-070.4) and the bursts `velocity-v0` finds in them
  (`pigtail.analysis.bursts.segment`), as a JSON snapshot.

Neither is ever shown to the coders (they carry outcome-proximal numbers, blind-v1).

**Rules** (versions in `RULES`):

- *Launch events* (`events-v1`): the Show HN / Launch HN posts, confirmed Product Hunt launches,
  declared-maintainer Bluesky posts worded as a launch, launch-worded GitHub releases, the first
  HN mention, the anchor and the relaunch events the selection stored, deduplicated by (kind,
  ref); every other release in the case's release window as a non-launch `release` event (a
  candidate explanation of bursts). Title: the HN story title (verbatim, first 25 words), or the
  release name; Product Hunt names and Bluesky post texts are never stored (ADR-085), so their
  title is `null` with the reason.
- *Assets at launch* (`assets-v1`): from the README at the anchor (the last commit touching it
  before T; its text scrubbed of identifiers), then the release notes of the release window:
  demo GIF or video, screenshots, install one-liner, benchmarks, docs site, comparison table, a
  "featured in" claim; `present` with the first matching line as a verbatim excerpt (<= 300
  characters), `absent` when the README at T was captured and nothing matches, `unknown` when it
  wasn't. The README's structure (headings, counts, quick-start / features sections) is recorded
  as found.
- *Amplifiers* (`amplifiers-v1`), by role and bucket only (ADR-066.1, ADR-022/073.2):
  `maintainer` (first-party launch posts), `community` (the venues of the launch events),
  `hn_front_page` (Algolia's tag; its absence is `unknown`, there is no historical front-page
  source), `organization` (the repository is owned by an organisation account), `newsletter`
  (only a first-party "featured in" claim; no newsletter source is collected) and
  `account_by_follower_bucket` (always `unknown`: it needs person-level data, held).
- *Trajectory* (`trajectory-v1`): per launch event, the stars before it (net stars from the
  creation day to the day before the event's endpoint day; `unknown` when the series doesn't
  reach the creation day) and the net stars gained on the endpoint days `[D, D + k)` for k in
  1, 3, 7, 30 (`pending` before the day has ended, `unknown` with a missing day).
- *Bursts* (`velocity-v0` over the whole series) and what explains each (`explain-v1`): the
  event whose endpoint day is closest to the burst's onset day within `[onset - 3 d, onset +
  1 d]`, launch events before releases before mentions, then the earlier; none: `unexplained`.
  Day-level attribution (ADR-070.2).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg

from pigtail.briefs.candidates import strip_owner
from pigtail.capture.db import CaptureDB
from pigtail.capture.snapshots import SnapshotStore
from pigtail.forensics import store as fstore
from pigtail.forensics.evidence import _derived_url, store_derived
from pigtail.forensics.store import EvidenceRow, PilotCase
from pigtail.pseudonymize import scrub_identifiers

FACTS_VERSION = "report-facts-v2"
RULES_V1 = {
    "events": "events-v1",
    "assets": "assets-v1",
    "amplifiers": "amplifiers-v1",
    "trajectory": "trajectory-v1",
    "bursts": "velocity-v0",
    "explain": "explain-v1",
}
RULES = {
    "events": "events-v2",
    "assets": "assets-v2",
    "amplifiers": "amplifiers-v2",
    "trajectory": "trajectory-v1",
    "bursts": "velocity-v0+size-v1",
    "explain": "explain-v2",
}
TITLE_MAX_WORDS = 25
EXCERPT_MAX = 300
OFFSETS_DAYS: tuple[int, ...] = (1, 3, 7, 30)
EXPLAIN_BEFORE_DAYS = 3
EXPLAIN_AFTER_DAYS = 1
DAY_LEVEL = "day-level"  # ADR-070.2
STAR_LABEL = "unfiltered, anomaly-checked"  # ADR-070.4
HELD = "held_person_level_adr_073_2"

WHERE = {
    "show_hn": "Hacker News (Show HN)",
    "launch_hn": "Hacker News (Launch HN)",
    "product_hunt": "Product Hunt",
    "bluesky_maintainer_post": "Bluesky (declared maintainer account)",
    "release_launch": "GitHub release (worded as a launch)",
    "release": "GitHub release",
    "first_mention": "Hacker News (first mention of the repo)",
    "anchor": "anchor T of the view",
}
HN_KINDS = ("show_hn", "launch_hn", "first_mention")
LAUNCH_KINDS = ("show_hn", "launch_hn", "product_hunt", "bluesky_maintainer_post", "release_launch")
_EXPLAIN_ORDER = {k: i for i, k in enumerate((*LAUNCH_KINDS, "release", "first_mention"))}


def title_words(title: str | None) -> tuple[str | None, bool]:
    """(the first `TITLE_MAX_WORDS` words, verbatim; whether it was cut)."""
    if not title:
        return None, False
    words = title.split()
    return " ".join(words[:TITLE_MAX_WORDS]), len(words) > TITLE_MAX_WORDS


def _t(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        t = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _iso(t: datetime | None) -> str | None:
    return None if t is None else t.astimezone(UTC).isoformat()


# --- launch events ---------------------------------------------------------------------------
@dataclass
class Event:
    kind: str
    ref: str
    at: datetime | None
    evidence_ids: list[str]
    launch: bool
    title: str | None = None
    title_truncated: bool = False
    title_evidence_id: str | None = None
    title_missing: str | None = None
    detail: dict[str, Any] | None = None
    # HN stories only (events-v2): True when the story links the repo or the project's own
    # homepage, or the selection matched it by URL; False: a title-only match; None: unchecked
    confirmed: bool | None = None

    @property
    def counts(self) -> bool:
        """A launch event the patterns, amplifiers and burst explanations may use: confirmed,
        or a kind that needs no confirmation (events-v2)."""
        if self.kind in HN_KINDS:
            return self.launch and self.confirmed is True
        return self.launch

    def to_dict(self) -> dict[str, Any]:
        where = WHERE.get(self.kind, self.kind)
        if self.kind in HN_KINDS and self.confirmed is not True:
            where += " — unconfirmed (title match)"
        return {
            "kind": self.kind,
            "where": where,
            "confirmed": self.confirmed if self.kind in HN_KINDS else True,
            "counts": self.counts,
            "ref": self.ref,
            "at": _iso(self.at),
            "launch": self.launch,
            "title": self.title,
            "title_truncated": self.title_truncated,
            "title_evidence_id": self.title_evidence_id,
            "title_missing": self.title_missing,
            "evidence_ids": sorted(set(self.evidence_ids)),
            **({"detail": self.detail} if self.detail else {}),
        }


def events_from_docs(
    launch_doc: Mapping[str, Any] | None,
    launch_ev: str | None,
    releases_doc: Mapping[str, Any] | None,
    releases_ev: str | None,
) -> list[Event]:
    """The case's events from the stored documents (rule `events-v1`), before HN titles."""
    out: dict[tuple[str, str], Event] = {}

    def add(e: Event) -> None:
        key = (e.kind, e.ref)
        if key in out:
            have = out[key]
            have.evidence_ids.extend(e.evidence_ids)
            have.title = have.title or e.title
            have.at = have.at or e.at
            if e.confirmed is True:
                have.confirmed = True
            if e.detail:
                have.detail = {**(have.detail or {}), **e.detail}
            return
        out[key] = e

    lev = [launch_ev] if launch_ev else []
    for sig in (launch_doc or {}).get("signals") or []:
        src = sig.get("source")
        if src == "show_hn" and sig.get("hn_item_id") is not None:
            title, cut = title_words(sig.get("title"))
            add(
                Event(
                    "show_hn",
                    str(sig["hn_item_id"]),
                    _t(sig.get("time")),
                    list(lev),
                    True,
                    title,
                    cut,
                    launch_ev if title else None,
                )
            )
        elif src == "hn_launch_lookup" and sig.get("hn_item_id") is not None:
            kind = "launch_hn" if sig.get("kind") == "launch_hn" else "show_hn"
            by_url = str(sig.get("match") or "").startswith("url")
            add(
                Event(
                    kind,
                    str(sig["hn_item_id"]),
                    _t(sig.get("time")),
                    list(lev),
                    True,
                    confirmed=True if by_url else None,
                    detail={"match": sig.get("match")},
                )
            )
        elif src == "ph_launch":
            for p in sig.get("posts") or []:
                if p.get("confirmed") is not True:
                    continue
                at = _t(p.get("featuredAt") or p.get("createdAt"))
                add(
                    Event(
                        "product_hunt",
                        str(p.get("id")),
                        at,
                        list(lev),
                        True,
                        title_missing="Product Hunt name and tagline are not stored (ADR-085)",
                    )
                )
        elif src == "bsky_maintainer_posts":
            for i, p in enumerate(sig.get("posts") or []):
                if p.get("kind") != "bluesky_maintainer_post" or p.get("role") != "maintainer":
                    continue
                add(
                    Event(
                        "bluesky_maintainer_post",
                        f"{p.get('match')}#{i}",
                        _t(p.get("time")),
                        list(lev),
                        True,
                        title_missing="post text is person-level and never stored (ADR-085)",
                    )
                )
        elif src == "gh_releases":
            for r in sig.get("launch_worded") or []:
                add(
                    Event(
                        "release_launch",
                        str(r.get("tag")),
                        _t(r.get("published_at")),
                        list(lev),
                        True,
                    )
                )
        elif src == "hn_first_mention" and sig.get("item_id") is not None:
            add(
                Event(
                    "first_mention",
                    str(sig["item_id"]),
                    _t(sig.get("time")),
                    list(lev),
                    False,
                    detail={"item_kind": sig.get("kind")},
                )
            )
    for rel in (launch_doc or {}).get("relaunch_events") or []:
        kind = str(rel.get("source") or rel.get("kind") or "relaunch")
        ref = str(rel.get("ref") or rel.get("item_id") or rel.get("at"))
        if (kind, ref) not in out:
            add(
                Event(
                    kind,
                    ref,
                    _t(rel.get("at")),
                    list(lev),
                    kind in LAUNCH_KINDS,
                    detail={"relaunch": True},
                )
            )
    rels = (releases_doc or {}).get("releases") or []
    for r in rels:
        tag = str(r.get("tag"))
        name, cut = title_words(r.get("name") or None)
        key = ("release_launch", tag)
        rev = [releases_ev] if releases_ev else []
        if key in out:
            e = out[key]
            e.evidence_ids.extend(rev)
            if name and not e.title:
                e.title, e.title_truncated, e.title_evidence_id = name, cut, releases_ev
            continue
        add(
            Event(
                "release",
                tag,
                _t(r.get("published_at")),
                rev,
                False,
                name,
                cut,
                releases_ev if name else None,
                detail={"prerelease": bool(r.get("prerelease"))},
            )
        )
    for e in out.values():
        if e.kind == "release_launch" and not e.title:
            e.title_missing = "release name not in the case's release window"
        if e.kind in ("show_hn", "launch_hn", "first_mention") and not e.title:
            e.title_missing = "HN story title not fetched"
    return sorted(
        out.values(), key=lambda e: (e.at or datetime.max.replace(tzinfo=UTC), e.kind, e.ref)
    )


def source_status(launch_doc: Mapping[str, Any] | None) -> dict[str, str | None]:
    """Whether the selection searched each launch source completely (`complete`), as it stored
    it: an event kind's absence is evidence only then (the pattern step reads this)."""
    out: dict[str, str | None] = {
        "ph_launch": None,
        "bsky_maintainer_posts": None,
        "gh_releases": None,
    }
    for sig in (launch_doc or {}).get("signals") or []:
        src = sig.get("source")
        if src in out:
            st = sig.get("status")
            if src == "gh_releases" and st is None and sig.get("complete") is True:
                st = "complete"
            out[src] = None if st is None else str(st)
    return out


# --- assets at launch (assets-v2) --------------------------------------------------------------
# v2 (ADR-089 addendum 4, after verifier M24 round 1 measured v1 at ~56 % precision): logos,
# banners, icons and wordmarks are not screenshots; a docs site must be on the project's own host
# (its homepage's domain, `<owner>.github.io`, or a host naming the repo); an install one-liner is
# one line naming the project and not a development install; a "featured in" claim needs a press
# or newsletter context; a comparison is a table with check marks or a table under a comparison
# heading ("vs" in lower case, so "VS Code" is not one); release notes count "at launch" only up
# to T + 1 day (later ones are listed as after launch).
_DEMO = re.compile(
    r"(?i)(\.(gif|mp4|webm|mov)\b|youtube\.com/watch|youtu\.be/|vimeo\.com/|loom\.com/share"
    r"|asciinema\.org/a/|<video\b)"
)
_BADGE = re.compile(r"(?i)(shields\.io|badge|travis-ci|codecov|circleci|badgen|/actions/workflows)")
_IMAGE = re.compile(r"(?i)(!\[[^\]]*\]\([^)]+\.(png|jpe?g|webp)[^)]*\)|<img\b[^>]*>)")
_LOGO = re.compile(r"(?i)(logo|banner|icon|wordmark|favicon|avatar|brand|sponsor|\.svg\b)")
_INSTALL = re.compile(
    r"(?i)^\s*(?:\$\s*)?(pip3? install|pipx install|uv (tool|pip) install|uvx |npm (i|install) "
    r"(-g|--global)\b|npx |yarn global add|pnpm (add -g|dlx)|bunx? |brew install|"
    r"cargo (install|binstall)|go install|gem install|curl [^\n`]*\|\s*(ba|z)?sh|"
    r"wget [^\n`]*\|\s*(ba)?sh|winget install|scoop install|nix (run|profile install)|"
    r"conda install|dotnet tool install|composer global require|deno (install|run)|docker run)"
)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_BENCH_HEADING = re.compile(r"(?im)^\s{0,3}#{1,6}\s.*\b(benchmarks?|performance)\b")
_BENCH_LINE = re.compile(r"(?i)\bbenchmark(s|ed|ing)?\b.*\d|\d.*\bbenchmark(s|ed|ing)?\b")
_URL = re.compile(r"https?://([^\s/)\]\"'>]+)([^\s)\]\"'>]*)")
_DOCS_HINT = re.compile(r"(?i)(^docs?\.|\.readthedocs\.io$|\.gitbook\.io$|\.mintlify\.app$)")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")
_CHECKS = re.compile(r"(✅|❌|✔|✓|✗|✘|:white_check_mark:|:x:|:heavy_check_mark:)")
_COMPARE_HEADING = re.compile(
    r"^\s{0,3}#{1,6}\s.*((?i:comparison|compared (to|with)|alternatives)|\bvs\.?\s)"
)
_ANY_IMAGE = re.compile(r"(?i)(!\[[^\]]*\]\([^)]+\)|<img\b)")
_FEATURED = re.compile(r"(?i)\b(featured|as seen|highlighted|covered)\s+(in|on|by)\b")
_PRESS = re.compile(
    r"(?i)(newsletter|weekly|digest|magazine|podcast|press|blog|news|techcrunch|hacker news|"
    r"product hunt|changelog|console\.dev|tldr)"
)
_HEADING = re.compile(r"^\s{0,3}(#{1,3})\s+(.+?)\s*#*\s*$")
_QUICKSTART = re.compile(r"(?i)(quick\s*start|getting started|installation|install|usage)")
_FEATURES = re.compile(r"(?i)\bfeatures?\b")
NOTES_AT_LAUNCH_AFTER = timedelta(days=1)  # release notes up to T + 1 day count "at launch"

ASSETS = (
    "demo_media",
    "screenshots",
    "install_one_liner",
    "benchmarks",
    "docs_site",
    "comparison_table",
    "featured_in_claim",
)


@dataclass(frozen=True)
class Project:
    """What the asset rules need to know about the project itself (no person data)."""

    name: str = ""  # the repo name (without owner)
    owner: str = ""  # the owner login (already `[owner]` in scrubbed text)
    homepage_host: str | None = None

    def name_forms(self) -> set[str]:
        n = self.name.lower()
        if not n:
            return set()
        return {n, n.replace("-", "_"), n.replace("_", "-"), n.replace("-", "").replace("_", "")}

    def own_host(self, host: str) -> bool:
        h = host.lower().split(":")[0]
        if self.homepage_host:
            hp = self.homepage_host.lower()
            base = ".".join(hp.split(".")[-2:])
            if h in (hp, base) or h.endswith("." + base):
                return True
        if h.endswith(".github.io") and (
            h.split(".")[0] in ("[owner]", self.owner.lower()) or not self.owner
        ):
            return True
        return any(f in h for f in self.name_forms() if len(f) >= 4)


def _excerpt(line: str) -> str:
    return " ".join(line.split())[:EXCERPT_MAX]


def _first_line(text: str, rx: re.Pattern[str], skip: Callable[[str], bool] | None = None) -> str:
    for ln in text.splitlines():
        if rx.search(ln) and not (skip and skip(ln)):
            return ln
    return ""


def _tables(text: str) -> list[tuple[int, list[str]]]:
    lines = text.splitlines()
    out: list[tuple[int, list[str]]] = []
    for i, ln in enumerate(lines):
        if _TABLE_SEP.match(ln) and i > 0 and "|" in lines[i - 1]:
            j = i + 1
            rows = [lines[i - 1]]
            while j < len(lines) and "|" in lines[j]:
                rows.append(lines[j])
                j += 1
            out.append((i - 1, rows))
    return out


def _install_line(text: str, proj: Project) -> str:
    """One line (a code line or an inline code span) that installs or runs *this* project."""
    names = proj.name_forms()
    cands: list[str] = []
    for ln in text.splitlines():
        cands.append(ln)
        cands.extend(m.group(1) for m in _INLINE_CODE.finditer(ln))
    for c in cands:
        s = c.strip()
        if s.endswith("\\") or not _INSTALL.match(s):
            continue
        low = s.lower()
        own = bool(proj.homepage_host and proj.homepage_host.lower() in low)
        if names and not any(n in low for n in names) and not own:
            continue
        return s
    return ""


def _docs_line(text: str, proj: Project) -> str:
    for ln in text.splitlines():
        for m in _URL.finditer(ln):
            host, path = m.group(1), m.group(2)
            if "github.com" in host or "githubusercontent" in host or "shields.io" in host:
                continue
            docsy = bool(_DOCS_HINT.search(host)) or bool(re.search(r"(?i)/docs?\b", path))
            if docsy and proj.own_host(host):
                return ln
    return ""


def _screenshot_line(text: str) -> str:
    for ln in text.splitlines():
        for m in _IMAGE.finditer(ln):
            img = m.group(0)
            if _BADGE.search(img) or _LOGO.search(img):
                continue
            return ln
    return ""


def _comparison_line(text: str) -> str:
    lines = text.splitlines()
    for start, rows in _tables(text):
        hit = next((r for r in rows if _CHECKS.search(r)), "")
        if hit:
            return hit
        # a table directly under a comparison heading (only blank or text lines between)
        for k in range(start - 1, max(-1, start - 6), -1):
            if _HEADING.match(lines[k]) or lines[k].lstrip().startswith("#"):
                if _COMPARE_HEADING.match(lines[k]):
                    return lines[k]
                break
    return ""


def detect_assets(text: str, proj: Project | None = None) -> dict[str, str]:
    """asset -> the first matching line (empty: none) in one text (`assets-v2`)."""
    proj = proj or Project()
    found: dict[str, str] = {}
    found["demo_media"] = _first_line(text, _DEMO)
    found["screenshots"] = _screenshot_line(text)
    found["install_one_liner"] = _install_line(text, proj)
    found["benchmarks"] = _first_line(text, _BENCH_HEADING) or _first_line(text, _BENCH_LINE)
    found["docs_site"] = _docs_line(text, proj)
    found["comparison_table"] = _comparison_line(text)
    found["featured_in_claim"] = _first_line(text, _FEATURED, skip=lambda ln: not _PRESS.search(ln))
    return found


def readme_structure(text: str) -> dict[str, Any]:
    """Headings (levels 1-3, <= 80 characters, at most 30), counts and the quick-start and
    features sections of a README (as found)."""
    heads: list[dict[str, Any]] = []
    in_code = False
    code_blocks = 0
    for ln in text.splitlines():
        if ln.strip().startswith("```"):
            in_code = not in_code
            code_blocks += 0 if in_code else 1
            continue
        if in_code:
            continue
        m = _HEADING.match(ln)
        if m:
            heads.append({"level": len(m.group(1)), "text": m.group(2)[:80]})
    images = [ln for ln in text.splitlines() if _ANY_IMAGE.search(ln) or _DEMO.search(ln)]
    return {
        "headings": heads[:30],
        "headings_total": len(heads),
        "words": len(text.split()),
        "code_blocks": code_blocks,
        "tables": len(_tables(text)),
        "badges": sum(1 for ln in images if _BADGE.search(ln)),
        "images": sum(1 for ln in images if not _BADGE.search(ln)),
        "quick_start_section": any(_QUICKSTART.search(h["text"]) for h in heads),
        "features_section": any(_FEATURES.search(h["text"]) for h in heads),
    }


def assets_at_launch(
    readme: tuple[str, str] | None,
    release_notes: tuple[str, str] | None,
    proj: Project | None = None,
    after_notes: tuple[str, str] | None = None,
) -> dict[str, Any]:
    """`assets-v2` over the README at T (evidence id, scrubbed text) and the release notes
    published up to T + 1 day (evidence id, text). Release notes can only turn an asset
    `present`. Assets found only in later release notes (`after_notes`) are listed under
    `after_launch`, never as at launch."""
    out: dict[str, Any] = {}
    r_found = detect_assets(readme[1], proj) if readme else {}
    n_found = detect_assets(release_notes[1], proj) if release_notes else {}
    for a in ASSETS:
        if readme and r_found.get(a):
            out[a] = {
                "value": "present",
                "evidence_id": readme[0],
                "excerpt": _excerpt(r_found[a]),
                "source": "readme_at_anchor",
            }
        elif release_notes and n_found.get(a):
            out[a] = {
                "value": "present",
                "evidence_id": release_notes[0],
                "excerpt": _excerpt(n_found[a]),
                "source": "release_notes_until_t_plus_1d",
            }
        elif readme:
            out[a] = {
                "value": "absent",
                "evidence_id": readme[0],
                "excerpt": None,
                "source": "readme_at_anchor",
            }
        else:
            out[a] = {
                "value": "unknown",
                "evidence_id": None,
                "excerpt": None,
                "reason": "no README at the anchor",
            }
    later: dict[str, Any] = {}
    if after_notes:
        a_found = detect_assets(after_notes[1], proj)
        for a in ASSETS:
            if a_found.get(a) and out[a]["value"] != "present":
                later[a] = {"evidence_id": after_notes[0], "excerpt": _excerpt(a_found[a])}
    return {
        "rule": RULES["assets"],
        "assets": out,
        "all_unknown": all(v["value"] == "unknown" for v in out.values()),
        "after_launch": later,
        "readme_structure": (
            {"evidence_id": readme[0], **readme_structure(readme[1])} if readme else None
        ),
    }


# --- amplifiers (amplifiers-v2) ----------------------------------------------------------------
COMMUNITY_KINDS = ("show_hn", "launch_hn", "product_hunt", "bluesky_maintainer_post")


def amplifiers(
    events: Sequence[Event],
    *,
    front_page: set[str],
    front_page_ev: str | None,
    owner_type: str | None,
    meta_ev: str | None,
    assets: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Roles and buckets only (`amplifiers-v2`): unconfirmed HN title matches and releases are
    never amplification (a release is the project's own channel, listed as such)."""
    launch = [e for e in events if e.counts]
    first_party = [
        e for e in launch if e.kind in ("show_hn", "launch_hn", "bluesky_maintainer_post")
    ]
    community = [e for e in launch if e.kind in COMMUNITY_KINDS]
    venues = sorted({WHERE[e.kind] for e in community})
    fp = [e for e in events if e.ref in front_page and e.kind in HN_KINDS and e.confirmed]
    feat = (assets.get("assets") or {}).get("featured_in_claim") or {}
    unconfirmed = [e for e in events if e.kind in HN_KINDS and e.launch and not e.confirmed]

    def ev_ids(es: Sequence[Event]) -> list[str]:
        return sorted({i for e in es for i in e.evidence_ids})

    out: list[dict[str, Any]] = [
        {
            "role": "maintainer",
            "value": "present" if first_party else "unknown",
            "basis": "confirmed first-party launch posts (Show HN / Launch HN linking the repo or "
            "its homepage; declared-maintainer Bluesky posts)",
            "events": [f"{e.kind}:{e.ref}" for e in first_party],
            "evidence_ids": ev_ids(first_party),
            **({} if first_party else {"reason": "no confirmed first-party post"}),
            **(
                {"unconfirmed_title_matches": [f"{e.kind}:{e.ref}" for e in unconfirmed]}
                if unconfirmed
                else {}
            ),
        },
        {
            "role": "community",
            "value": "present" if venues else "unknown",
            "venues": venues,
            "basis": "community venues of the confirmed launch events (releases excluded)",
            "evidence_ids": ev_ids(community),
            **({} if venues else {"reason": "no confirmed community launch event"}),
        },
        {
            "role": "hn_front_page",
            "value": "present" if fp else "unknown",
            "basis": "Algolia front_page tag on a confirmed story",
            "evidence_ids": [front_page_ev] if fp and front_page_ev else [],
            **(
                {}
                if fp
                else {
                    "reason": "no historical front-page source; the tag's absence is not evidence"
                }
            ),
        },
        {
            "role": "organization",
            "value": {"Organization": "present", "User": "absent"}.get(owner_type or "", "unknown"),
            "basis": "the repository is owned by an organisation account (its own channels)",
            "evidence_ids": [meta_ev] if meta_ev and owner_type else [],
        },
        {
            "role": "newsletter",
            "value": "claimed" if feat.get("value") == "present" else "unknown",
            "basis": "a first-party 'featured in' claim in a press or newsletter context only; "
            "no newsletter source is collected",
            "evidence_ids": [feat["evidence_id"]] if feat.get("value") == "present" else [],
            **(
                {"excerpt": feat.get("excerpt")}
                if feat.get("value") == "present"
                else {"reason": "source_not_collected"}
            ),
        },
        {
            "role": "account_by_follower_bucket",
            "value": "unknown",
            "evidence_ids": [],
            "basis": "needs person-level data",
            "reason": HELD,
        },
    ]
    return out


# --- star trajectory and bursts ------------------------------------------------------------------
def _day(t: datetime) -> date:
    from pigtail.briefs.outcomes import endpoint_day

    return endpoint_day(t)


def trajectory(
    series: Mapping[date, int],
    event_at: datetime,
    *,
    created: date | None,
    as_of: date,
) -> dict[str, Any]:
    """`trajectory-v1` for one event: stars before it and the net gains over `[D, D + k)`."""
    d0 = _day(event_at)
    out: dict[str, Any] = {"endpoint_day": d0.isoformat()}
    if not series:
        out["stars_before"] = {"value": None, "reason": "no_star_history"}
    elif created is None:
        out["stars_before"] = {"value": None, "reason": "no_creation_date"}
    elif created >= d0:
        out["stars_before"] = {"value": 0}
    elif min(series) > created:
        out["stars_before"] = {"value": None, "reason": "series_does_not_reach_creation"}
    else:
        days = [created + timedelta(days=i) for i in range((d0 - created).days)]
        missing = [d for d in days if d not in series]
        out["stars_before"] = (
            {"value": None, "reason": "incomplete_series"}
            if missing
            else {"value": sum(series[d] for d in days)}
        )
    gains: dict[str, Any] = {}
    for k in OFFSETS_DAYS:
        days = [d0 + timedelta(days=i) for i in range(k)]
        if days[-1] > as_of:
            gains[f"+{k}d"] = {"value": None, "reason": "pending"}
        elif not series or any(d not in series for d in days):
            gains[f"+{k}d"] = {"value": None, "reason": "incomplete_series"}
        else:
            gains[f"+{k}d"] = {"value": sum(series[d] for d in days)}
    out["gained"] = gains
    before = out["stars_before"].get("value")
    out["stars_at"] = {
        k: (None if before is None or v.get("value") is None else before + v["value"])
        for k, v in gains.items()
    }
    return out


def explain(onset_day: date, events: Sequence[Event]) -> dict[str, Any] | None:
    """`explain-v2`: among the events that count (confirmed HN stories, Product Hunt, declared
    maintainers' Bluesky posts, releases of the whole history), the one whose endpoint day is
    closest to the onset day within [-3 d, +1 d] (launch events before releases before
    mentions, then the earlier). When a launch event and a release are within 24 h of each
    other, the launch event is preferred and the release is named with it (`also`)."""
    cands = []
    for e in events:
        if e.at is None:
            continue
        if e.kind in HN_KINDS and e.confirmed is not True:
            continue  # an unconfirmed title match never explains a burst
        if e.launch and not e.counts:
            continue
        delta = (_day(e.at) - onset_day).days
        if -EXPLAIN_BEFORE_DAYS <= delta <= EXPLAIN_AFTER_DAYS:
            cands.append((abs(delta), _EXPLAIN_ORDER.get(e.kind, 99), e.at, e.ref, delta, e))
    if not cands:
        return None
    cands.sort(key=lambda c: c[:4])
    best = cands[0]
    e = best[5]
    releases = [c for c in cands if c[5].kind in ("release", "release_launch")]
    launches = [c for c in cands if c[5].kind in COMMUNITY_KINDS]
    also: list[str] = []
    if e.kind in ("release", "release_launch") and e.at is not None:
        near = [c for c in launches if c[2] is not None and abs(c[2] - e.at) <= timedelta(hours=24)]
        if near:
            also.append(f"{e.kind}:{e.ref}")
            best = sorted(near, key=lambda c: c[:4])[0]
            e = best[5]
    elif e.kind in COMMUNITY_KINDS and e.at is not None:
        also = [
            f"{c[5].kind}:{c[5].ref}"
            for c in releases
            if c[2] is not None and abs(c[2] - e.at) <= timedelta(hours=24)
        ]
    out: dict[str, Any] = {"event": f"{e.kind}:{e.ref}", "kind": e.kind, "days_from_onset": best[4]}
    if also:
        out["also"] = also
    return out


def bursts(
    series: Mapping[date, int], events: Sequence[Event], *, created: date | None
) -> list[dict[str, Any]]:
    """Every `velocity-v0` burst of the series with its whole size (`size-v1`: net stars from
    the onset day to the last day before the rate is back at baseline, or to the series' end
    for an open burst; the first 48 h are kept as `stars_48h`), and the event that best
    explains it or `unexplained` (day-level attribution)."""
    from pigtail.analysis.bursts import segment

    if not series:
        return []
    seg = segment(series, min(series), max(series), created=created)
    out = []
    for b in seg.bursts:
        why = explain(b.onset.day, events)
        days = [b.onset.day + timedelta(days=i) for i in range((b.last_day - b.onset.day).days + 1)]
        known = [d for d in days if d in series]
        out.append(
            {
                "onset_day": b.onset.day.isoformat(),
                "onset_precision": b.onset.precision,
                "end_day": None if b.end is None else b.end.isoformat(),
                "last_day": b.last_day.isoformat(),
                "open": b.end is None,
                "days": len(days),
                "stars_total": sum(series[d] for d in known),
                "stars_total_complete": len(known) == len(days),
                "peak_day": None if b.peak_day is None else b.peak_day.isoformat(),
                "peak_stars": b.peak_stars,
                "stars_48h": b.stars_48h,
                "z": round(b.z, 2),
                "multi_peak": b.multi_peak,
                "shape": b.shape,
                "explained_by": why["event"] if why else "unexplained",
                "explained_kind": why["kind"] if why else None,
                "explained_also": why.get("also", []) if why else [],
                "days_from_onset": why["days_from_onset"] if why else None,
                "label": DAY_LEVEL,
            }
        )
    return out


def _host(url: str | None) -> str | None:
    from urllib.parse import urlsplit

    if not url:
        return None
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def _release_history(cand: Any, events: Sequence[Event]) -> list[Event]:
    """Every release of the repo's history the selection stored (tag, date; `gh_releases`), not
    already an event: the candidates that explain-v2 considers besides the case's own events."""
    have = {e.ref for e in events if e.kind in ("release", "release_launch")}
    out: list[Event] = []
    for sig in (getattr(cand, "sources", None) or []) if cand is not None else []:
        if sig.get("source") != "gh_releases":
            continue
        for r in sig.get("releases") or []:
            tag = str(r.get("tag"))
            at = _t(r.get("published_at"))
            if tag in have or at is None:
                continue
            have.add(tag)
            kind = "release_launch" if r.get("launch") else "release"
            out.append(Event(kind, tag, at, [], kind == "release_launch"))
    return out


# --- the stage ---------------------------------------------------------------------------------
class FactsStage:
    """Computes and stores the report facts of each case not done yet (checkpointed per case on
    `brief_pilot_case.facts`). `hn` is an `HNStoryMetaConnector` (None: HN titles and the
    front-page tag are recorded as a gap)."""

    def __init__(
        self,
        conn: psycopg.Connection[Any],
        *,
        snapshots: SnapshotStore,
        hn: Any = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        run_id: str | None = None,
        code_commit: str | None = None,
    ) -> None:
        self.conn = conn
        self.db = CaptureDB(conn)
        self.snaps = snapshots
        self.hn = hn
        self.clock = clock
        self.run_id = run_id
        self.code_commit = code_commit

    def run(
        self,
        pilot: dict[str, Any],
        cases: Sequence[PilotCase],
        candidates: Mapping[str, Any] | None = None,
    ) -> dict[str, int]:
        """Cases without facts or with facts of an older version (`--refresh-facts`)."""
        from pigtail.privacy.snapshot_retention import link

        done = 0
        for c in cases:
            if c.facts is not None and c.facts.get("version") == FACTS_VERSION:
                continue
            cand = (candidates or {}).get(c.candidate_ref)
            facts, new_ids = self.case_facts(pilot, c, cand)
            if new_ids:
                link(self.db, pilot["brief_run_id"], new_ids)
            fstore.set_case_facts(
                self.conn, pilot["brief_run_id"], c.case_key, facts, FACTS_VERSION
            )
            c.facts = facts
            done += 1
        return {"cases_with_facts": done}

    def _items(self, rid: str, c: PilotCase) -> dict[str, dict[str, Any]]:
        return {r["kind"]: r for r in fstore.evidence_rows(self.conn, rid, c.case_key)}

    def _bytes(self, row: Mapping[str, Any] | None) -> bytes | None:
        from pigtail.capture.snapshots import SnapshotError

        if row is None:
            return None
        try:
            return self.snaps.get(row["content_hash"])
        except (SnapshotError, KeyError):
            return None

    def case_facts(
        self, pilot: dict[str, Any], c: PilotCase, cand: Any = None
    ) -> tuple[dict[str, Any], list[str]]:
        from urllib.parse import urlsplit

        from pigtail.connectors.github import parse_readme_json, parse_repo_node

        rid = pilot["brief_run_id"]
        items = self._items(rid, c)
        gaps: dict[str, str] = {}
        new_ids: list[str] = []

        def doc(kind: str) -> tuple[dict[str, Any] | None, str | None]:
            row = items.get(kind)
            data = self._bytes(row)
            if row is None or data is None:
                return None, None
            return json.loads(data), str(row["evidence_id"])

        launch_doc, launch_ev = doc("launch_events")
        rel_doc, rel_ev = doc("releases")
        events = events_from_docs(launch_doc, launch_ev, rel_doc, rel_ev)
        # metadata (owner type, creation, homepage for the HN and docs rules)
        meta_row = items.get("repo_metadata")
        meta_data = self._bytes(meta_row)
        owner_type, created, homepage = None, None, None
        if meta_row is not None and meta_data is not None:
            node = (json.loads(meta_data).get("data") or {}).get("r0")
            m = parse_repo_node(node)
            if m is not None:
                owner_type = m.owner_type
                created = _day(m.created_at) if m.created_at else None
                homepage = m.homepage
        if not homepage and cand is not None:
            homepage = (getattr(cand, "metadata", None) or {}).get("homepage")
        hp_host = urlsplit(homepage).hostname if homepage else None
        proj = Project(c.repo_full_name.partition("/")[2], c.owner, hp_host)
        # HN story titles (and Algolia's front-page tag)
        front: set[str] = set()
        hn_ev = None
        hn_ids = sorted(
            {
                int(e.ref)
                for e in events
                if e.kind in ("show_hn", "launch_hn", "first_mention") and e.ref.isdigit()
            }
        )
        if hn_ids:
            got = self._hn_stories(pilot, c, hn_ids, gaps)
            if got is not None:
                stories, front, hn_ev = got
                new_ids.append(hn_ev)
                for e in events:
                    st = stories.get(e.ref)
                    if st is None or e.kind not in ("show_hn", "launch_hn", "first_mention"):
                        continue
                    e.evidence_ids.append(hn_ev)
                    host = st.get("url_host")
                    ok = bool(st.get("links_repo")) or bool(host and proj.own_host(host))
                    if ok:
                        e.confirmed = True
                    elif e.confirmed is None:
                        e.confirmed = False  # a title-only match (events-v2)
                    if st.get("title"):
                        e.title, e.title_truncated = title_words(st["title"])
                        e.title_evidence_id, e.title_missing = hn_ev, None
                    if e.at is None:
                        e.at = _t(st.get("time"))
        # README at T, release notes (at launch: up to T + 1 day)
        readme = None
        r_row = items.get("readme_at_anchor")
        r_data = self._bytes(r_row)
        if r_row is not None and r_data is not None:
            text = scrub_identifiers(strip_owner(parse_readme_json(r_data).text, c.owner) or "")
            readme = (str(r_row["evidence_id"]), text)
        notes = after = None
        t_at = _t(c.anchor.get("at")) or next(
            (e.at for e in events if e.counts and e.at is not None), None
        )
        if rel_doc and rel_ev and t_at is not None:
            cut = t_at + NOTES_AT_LAUNCH_AFTER
            rels = rel_doc.get("releases") or []
            early = [
                str(r.get("notes") or "") for r in rels if (_t(r.get("published_at")) or cut) <= cut
            ]
            late = [
                str(r.get("notes") or "") for r in rels if (_t(r.get("published_at")) or cut) > cut
            ]
            notes = (rel_ev, "\n".join(early)) if early else None
            after = (rel_ev, "\n".join(late)) if late else None
        assets = assets_at_launch(readme, notes, proj, after)
        amps = amplifiers(
            events,
            front_page=front,
            front_page_ev=hn_ev,
            owner_type=owner_type,
            meta_ev=str(meta_row["evidence_id"]) if meta_row else None,
            assets=assets,
        )
        # star trajectory and bursts
        traj: dict[str, Any] = {"label": STAR_LABEL, "rule": RULES["trajectory"]}
        host_id = c.repo_host_id if c.repo_host_id is not None else self._host_id(pilot, c)
        if host_id is None:
            gaps["star_trajectory"] = "no_repo_host_id"
            traj.update(evidence_id=None, per_event=[], bursts=[])
        else:
            from pigtail.briefs.outcomes import star_series

            as_of = self.clock().date() - timedelta(days=1)
            series = star_series(self.conn, int(host_id), as_of)
            per_event = [
                {
                    "event": f"{e.kind}:{e.ref}",
                    **trajectory(series, e.at, created=created, as_of=as_of),
                }
                for e in events
                if e.at is not None and (e.launch or e.kind == "first_mention")
            ]
            # no anchor in the selection: the first launch event stands in (ADR-089 add. 2)
            first = next((e.at for e in events if e.launch and e.at is not None), None)
            anchor_at = _t(c.anchor.get("at")) or first
            anchor_traj = (
                trajectory(series, anchor_at, created=created, as_of=as_of) if anchor_at else None
            )
            history = _release_history(cand, events)
            bs = bursts(series, [*events, *history], created=created)
            tdoc = {
                "repo": f"[owner]/{c.repo_full_name.partition('/')[2]}",
                "series": {d.isoformat(): n for d, n in sorted(series.items())},
                "as_of": as_of.isoformat(),
                "created_day": None if created is None else created.isoformat(),
                "bursts": bs,
                "explanation_releases": [
                    {"tag": e.ref, "published_at": _iso(e.at)} for e in history
                ],
                "rules": {k: RULES[k] for k in ("trajectory", "bursts", "explain")},
                "label": STAR_LABEL,
            }
            ev = store_derived(
                self.db,
                self.snaps,
                url=_derived_url(c.repo_full_name, f"star-trajectory/{c.view}"),
                doc=tdoc,
                repo_id=c.repo_id,
                now=self.clock(),
                run_id=self.run_id,
                terms_basis="derived from repo_star_daily (star-history, TM-02)",
            )
            self._add_item(
                pilot,
                c,
                "star_trajectory",
                ev.id,
                ev.content_hash,
                as_of.isoformat(),
                {"days": len(series), "bursts": len(bs)},
            )
            new_ids.append(ev.id)
            if not series:
                gaps["star_trajectory"] = "no_star_history"
            traj.update(
                evidence_id=ev.id,
                days=len(series),
                anchor=anchor_traj,
                per_event=per_event,
                bursts=bs,
            )
        for src, reason in gaps.items():
            fstore.add_gap(self.conn, rid, c, src, reason)
        facts = {
            "version": FACTS_VERSION,
            "rules": RULES,
            "anchor": {k: v for k, v in c.anchor.items() if k != "relaunch_events"},
            "anchor_source": "selection"
            if c.anchor.get("at")
            else (
                "first_launch_event"
                if any(e.launch and e.at is not None for e in events)
                else "none"
            ),
            "events": [e.to_dict() for e in events],
            "assets": assets,
            "amplifiers": amps,
            "trajectory": traj,
            "gaps": gaps,
            "source_status": source_status(launch_doc),
            "evidence": {k: str(v["evidence_id"]) for k, v in sorted(items.items())}
            | ({"hn_stories": hn_ev} if hn_ev else {})
            | ({"star_trajectory": traj["evidence_id"]} if traj.get("evidence_id") else {}),
        }
        return facts, new_ids

    def _host_id(self, pilot: Mapping[str, Any], c: PilotCase) -> int | None:
        """The repo's GitHub id from the brief's candidate row (the selection's case rows the
        case rule reads don't carry it)."""
        row = self.conn.execute(
            "SELECT repo_host_id FROM brief_candidate WHERE brief_id = %s AND brief_version = %s"
            " AND candidate_ref = %s",
            (pilot["brief_id"], pilot["brief_version"], c.candidate_ref),
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def _add_item(
        self,
        pilot: dict[str, Any],
        c: PilotCase,
        kind: str,
        evidence_id: str,
        content_hash: str,
        item_date: str,
        detail: dict[str, Any],
    ) -> None:
        fstore.add_evidence(
            self.conn,
            pilot,
            c,
            EvidenceRow(kind, evidence_id, content_hash, item_date, detail=detail),
            captured_at=self.clock(),
            code_commit=self.code_commit,
            schedule_decay=False,
        )

    def _hn_stories(
        self, pilot: dict[str, Any], c: PilotCase, ids: list[int], gaps: dict[str, str]
    ) -> tuple[dict[str, dict[str, Any]], set[str], str] | None:
        """(stories by item id, the ids Algolia tags front_page, the derived item's evidence
        id), or None (a gap)."""
        from pigtail.connectors.base import FetchError
        from pigtail.connectors.hn import parse_show_hn_page
        from pigtail.privacy.deletion import PARSE_ERRORS, DeletionLog
        from pigtail.privacy.deletion import drop_after_parse as drop

        if self.hn is None:
            gaps["hn_stories"] = "connector_off"
            return None
        dlog = DeletionLog(self.db, "retention", run_id=self.run_id)
        stories: dict[str, dict[str, Any]] = {}
        front: set[str] = set()
        try:
            for fp in (False, True):
                f = self.hn.stories_by_id(
                    ids,
                    front_page=fp,
                    evidence_url=f"https://hn.algolia.com/api/v1/search?pigtail_story_meta="
                    f"{c.repo_full_name}&front_page={str(fp).lower()}",
                )
                try:
                    parsed, _n = parse_show_hn_page(f.data)
                finally:
                    drop(self.db, self.hn.store, f.evidence.id, f.content_hash, dlog)
                for st in parsed:
                    if fp:
                        front.add(str(st.item_id))
                        continue
                    title = scrub_identifiers(strip_owner(st.title, c.owner) or "") or None
                    stories[str(st.item_id)] = {
                        "item_id": st.item_id,
                        "title": title,
                        "time": _iso(st.created_at),
                        "points": st.points,
                        "links_repo": (st.repo_full_name or "").lower() == c.repo_full_name,
                        "url_host": _host(st.url),
                    }
        except FetchError as e:
            gaps["hn_stories"] = f"fetch_failed:{e.status}"
            return None
        except PARSE_ERRORS:
            gaps["hn_stories"] = "parse_failed"
            return None
        hdoc = {
            "repo": f"[owner]/{c.repo_full_name.partition('/')[2]}",
            "stories": [stories[k] for k in sorted(stories)],
            "front_page_tagged": sorted(front),
            "note": "Algolia front_page tag; its absence is not evidence (no historical source)",
        }
        ev = store_derived(
            self.db,
            self.snaps,
            url=_derived_url(c.repo_full_name, f"hn-stories/{c.view}"),
            doc=hdoc,
            repo_id=c.repo_id,
            now=self.clock(),
            run_id=self.run_id,
            terms_basis="derived from HN Algolia story metadata (TM-03); no author, no comment",
        )
        self._add_item(
            pilot,
            c,
            "hn_stories",
            ev.id,
            ev.content_hash,
            self.clock().date().isoformat(),
            {"stories": len(stories)},
        )
        return stories, front, ev.id

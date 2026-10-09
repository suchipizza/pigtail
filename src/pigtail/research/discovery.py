"""Source discovery: plan queries, run the search provider, triage candidates with the model.

Triage refers to candidates by index only, so the model can drop or classify sources but never
introduce a URL. User-supplied `--source` URLs always enter the candidate list and go through the
same fetching, extraction and verification path as discovered ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from pigtail.logging import get_logger
from pigtail.policies.loader import PolicyRegistry, surface_for_url
from pigtail.providers.base import ModelProvider, SearchProvider, SearchQuery, StructuredModelRequest
from pigtail.research.builder import canonicalize_url
from pigtail.research.target_resolution import ResolvedTarget

log = get_logger("discovery")

SKIP_HOSTS = (
    "youtube.com",
    "youtu.be",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "linkedin.com",
    "pinterest.com",
    "google.com",
    "apps.apple.com",
    "play.google.com",
)


@dataclass
class SourceCandidate:
    url: str
    title: str | None
    origin: str  # search | user | github | hacker_news | homepage
    surface_key: str
    query: str | None = None
    page_age: str | None = None
    source_kind: str = "unknown"
    reason: str | None = None


@dataclass
class DiscoveryPlan:
    queries: list[SearchQuery] = field(default_factory=list)


def build_plan(t: ResolvedTarget, max_queries: int) -> DiscoveryPlan:
    n = t.name
    q: list[str] = []
    if t.repo:
        full = t.repo.full_name
        q += [
            f'"{n}" {t.repo.owner} launch announcement',
            # Reddit posts about a project usually link the repository but often never repeat a short
            # name ("brag") in their title, so search by repository path as well as by name.
            f'site:reddit.com "{full}"',
            f'site:reddit.com "{n}" github',
            f'"{n}" Show HN',
            f'"{n}" Product Hunt launch',
            f"how {n} grew github stars",
            f'"{full}" open source',
            f'"{n}" founder interview',
            f'"{n}" blog introducing',
        ]
        if t.domain:
            q += [f"site:{t.domain} blog", f'"{n}" {t.domain} funding OR raised OR acquired']
    else:
        d = t.domain or ""
        q += [
            f'"{n}" founder interview how we got our first users',
            f'"{n}" {d} growth story bootstrapped',
            f'"{n}" MRR OR ARR OR revenue milestone',
            f'"{n}" Product Hunt launch',
            f"site:{d} blog",
            f'"{n}" {d} launch announcement',
            f'"{n}" how {n} grew',
            f'"{n}" {d} users milestone',
            f'site:reddit.com "{n}" {d}',
            f'"{n}" {d} pricing change OR funding OR acquisition',
        ]
    seen, out = set(), []
    for text in q:
        if text not in seen:
            seen.add(text)
            out.append(SearchQuery(text=text, purpose="discovery"))
    return DiscoveryPlan(queries=out[:max_queries])


class TriageItem(BaseModel):
    index: int
    keep: bool
    about_target: bool = Field(description="True only if the page is clearly about this exact product/project.")
    source_kind: Literal[
        "first_party",
        "founder_interview",
        "news",
        "third_party_analysis",
        "community",
        "launch_page",
        "directory_or_listing",
        "other",
    ]
    reason: str = Field(description="Under 15 words.")


class TriageResult(BaseModel):
    items: list[TriageItem]


TRIAGE_SYSTEM = (
    "You select web sources for evidence-based research on how a specific product grew. You are strict "
    "about identity: different products with similar names must be rejected. Prefer first-party posts "
    "(company blog, founder posts, changelogs), especially milestone retrospectives (revenue/user milestones, "
    "'year one', launch results, how-we-grew posts), founder interviews, launch posts, credible reporting and "
    "detailed analyses. Skip routine feature announcements unless they mark a major launch or relaunch. Reject SEO listicles, app-directory stubs, scraped duplicates, job ads, generic "
    "'alternatives to X' pages and pages about other products."
)


LINK_ONLY_NOTE = (
    "\n\nThese are community posts (Reddit, X, Product Hunt) that Pigtail may not read: you only see the "
    "title, URL and the search query that found them. Makers often title posts about their own project "
    'without its name ("My side project crossed 7,000 GitHub stars"). Set about_target=true when the '
    "query that found the post names this exact project (its repository path or domain) and the title "
    "plausibly describes it, even if the title does not repeat the name. Reject posts clearly about "
    "something else."
)


async def triage(
    candidates: list[SourceCandidate],
    t: ResolvedTarget,
    model: ModelProvider,
    limit: int,
    *,
    link_only: bool = False,
) -> list[SourceCandidate]:
    if not candidates:
        return []
    identity = [f"Name: {t.name}", f"URL: {t.canonical_url}"]
    if t.description:
        identity.append(f"Description: {t.description}")
    if t.repo:
        identity.append(f"GitHub repository: {t.repo.url}")
        if t.repo.homepage:
            identity.append(f"Homepage: {t.repo.homepage}")
    lines = [
        f"[{i}] {c.title or '(no title)'} | {c.url}" + (f" | found by: {c.query}" if c.query else "")
        for i, c in enumerate(candidates)
    ]
    prompt = (
        "Target identity:\n"
        + "\n".join(identity)
        + f"\n\nCandidate sources ({len(candidates)}):\n"
        + "\n".join(lines)
        + f"\n\nReturn one item per candidate index. Mark keep=true for at most {limit} sources that are about "
        "this exact target and likely to contain verifiable facts about its history, launches, users, "
        "metrics, channels or strategy." + (LINK_ONLY_NOTE if link_only else "")
    )
    res = await model.structured(
        StructuredModelRequest(
            system=TRIAGE_SYSTEM,
            prompt=prompt,
            output_type=TriageResult,
            purpose="source triage",
            max_tokens=12000,
            effort="low",
        )
    )
    kept: list[SourceCandidate] = []
    by_idx = {it.index: it for it in res.items}
    for i, c in enumerate(candidates):
        it = by_idx.get(i)
        if c.origin == "user":
            c.source_kind = it.source_kind if it else "other"
            kept.append(c)
            continue
        if it and it.keep and it.about_target:
            c.source_kind = it.source_kind
            c.reason = it.reason
            kept.append(c)
    rank = {
        "first_party": 0,
        "founder_interview": 1,
        "launch_page": 2,
        "news": 3,
        "third_party_analysis": 4,
        "community": 5,
        "directory_or_listing": 6,
        "other": 7,
        "unknown": 8,
    }
    kept.sort(key=lambda c: (c.origin != "user", rank.get(c.source_kind, 9)))
    return kept[: max(limit, sum(1 for c in kept if c.origin == "user"))]


async def discover(
    t: ResolvedTarget,
    search: SearchProvider,
    plan: DiscoveryPlan,
    policies: PolicyRegistry,
    extra: list[str],
) -> tuple[list[SourceCandidate], list[str]]:
    """Return (candidates, errors). Candidates are deduplicated by canonical URL."""
    errors: list[str] = []
    seen: dict[str, SourceCandidate] = {}

    def add(c: SourceCandidate) -> None:
        key = canonicalize_url(c.url)
        host = key.split("/")[2] if "//" in key else key
        if any(host == h or host.endswith("." + h) for h in SKIP_HOSTS) and c.origin != "user":
            return
        if key not in seen:
            seen[key] = c

    for u in extra:
        add(SourceCandidate(url=u, title=None, origin="user", surface_key=surface_for_url(u)))
    context = f"{t.name} ({t.canonical_url})"
    results = await search.search_many(plan.queries, context=context)
    for res in results:
        if res.error:
            errors.append(f"search: {res.error}")
        for r in res.results:
            sk = surface_for_url(r.url)
            pol = policies.policy_for(r.url, sk)
            if not pol.access.discovery_allowed:
                continue
            # GitHub pages for this repo are covered by the GitHub adapter.
            if sk == "github" and t.repo and t.repo.full_name.lower() in r.url.lower():
                continue
            add(
                SourceCandidate(
                    url=r.url,
                    title=r.title,
                    origin="search",
                    surface_key=sk,
                    query=r.query,
                    page_age=r.page_age,
                )
            )
    return list(seen.values()), errors

"""V1 research orchestrator (spec §29.14). No database anywhere on this path."""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from rich.console import Console

from pigtail.bundle.writer import dump_json, write_bundle
from pigtail.config import Config
from pigtail.domain.time import iso, parse_dt, range_from_partial, utcnow
from pigtail.errors import BundleValidationError, CredentialsError, PigtailError, RenderError
from pigtail.logging import get_logger
from pigtail.policies.loader import default_registry, surface_for_url
from pigtail.providers.base import Meter
from pigtail.providers.community.hacker_news import HackerNewsClient
from pigtail.providers.fetchers.web import PARSER_VERSION, FetchedPage, WebFetcher
from pigtail.providers.github.client import GitHubClient
from pigtail.providers.models.registry import make_model_provider
from pigtail.providers.search.registry import make_search_provider
from pigtail.renderer.render import ForensicRenderer
from pigtail.research import conflicts, discovery, feeds, gaps, normalization, reconstruction, synthesis
from pigtail.research.builder import BundleBuilder, EvidenceSpec, sha256_text
from pigtail.research.extraction import PageExtraction, VerifiedClaim, extract_claims
from pigtail.research.repository_analysis import RepoState, analyze_repository, hn_event, link_episodes_and_launches
from pigtail.research.target_resolution import ResolvedTarget, resolve

log = get_logger("orchestrator")

STAGES = [
    "Resolve target",
    "Repository history",
    "Discover sources",
    "Fetch evidence",
    "Extract claims",
    "Reconstruct timeline",
    "Analyze growth",
    "Validate evidence and gaps",
    "Write Research Bundle",
    "Render report",
]


@dataclass
class AnalysisResult:
    status: str
    output_dir: Path
    bundle_path: Path
    report_path: Path | None
    cost: float
    duration_s: float


class Progress:
    def __init__(self, console: Console, verbose: bool):
        self.console, self.verbose = console, verbose
        self.t0 = time.monotonic()
        self.cur: str | None = None
        self.ts = 0.0

    def stage(self, name: str) -> None:
        self.done()
        self.cur, self.ts = name, time.monotonic()
        self.console.print(f"[bold]›[/] {name}…")

    def done(self, note: str = "") -> None:
        if self.cur:
            dt = time.monotonic() - self.ts
            if self.verbose or note:
                self.console.print(f"  [dim]{note + ' · ' if note else ''}{dt:.1f}s[/]")
        self.cur = None

    def info(self, msg: str) -> None:
        self.console.print(f"  [dim]{msg}[/]")


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "target"


def _target_dict(t: ResolvedTarget) -> dict:
    from pigtail.domain.ids import new_id

    ext = []
    if t.domain:
        ext.append({"namespace": "domain", "value": t.domain, "url": f"https://{t.domain}"})
    return {
        "id": new_id(),
        "kind": t.kind if t.kind in ("repository", "product", "company") else "product",
        "name": t.name,
        "canonical_url": t.canonical_url,
        "domain": t.domain,
        "description": t.description,
        "primary_repository_id": None,
        "external_ids": ext,
        "aliases": t.aliases,
    }


DIRECTNESS = {
    ("first_party", "company_or_founder"): "primary_direct",
    ("founder_interview", "company_or_founder"): "primary_indirect",
    ("launch_page", "company_or_founder"): "primary_direct",
    ("news", "company_or_founder"): "primary_indirect",
    ("third_party_analysis", "company_or_founder"): "primary_indirect",
    ("community", "company_or_founder"): "primary_indirect",
}


def _evidence_fields(page_kind: str, vc: VerifiedClaim) -> tuple[str, str]:
    c = vc.claim
    directness = DIRECTNESS.get((page_kind, c.speaker))
    if directness is None:
        directness = {
            "news": "independent_direct",
            "third_party_analysis": "secondary_synthesis",
            "community": "community_report",
            "first_party": "primary_direct",
            "founder_interview": "primary_indirect",
        }.get(page_kind, "unknown")
    if c.speaker == "company_or_founder" and directness.startswith("primary"):
        ev_class = "company_measured" if c.metric else "documented"
    elif c.evidence_class in ("third_party_measured",):
        ev_class = "third_party_measured"
    elif directness.startswith("primary"):
        ev_class = "documented"
    else:
        ev_class = "third_party_reported"
    return ev_class, directness


ANNOUNCEMENT_KINDS = {"launch", "product_change", "pricing", "business_model", "partnership", "funding"}


def _claim_time(vc: VerifiedClaim, published: str | None, cutoff: datetime, page_kind: str = "other") -> dict:
    from pigtail.research.reconstruction import time_from

    c = vc.claim
    tr = time_from(c.date, c.date_end, c.date_label)
    s = parse_dt(tr["start"])
    pub_tr = range_from_partial(published) if published else None
    pub = parse_dt(pub_tr["end"]) if pub_tr else None
    if s and (s > cutoff or (pub and s > pub + timedelta(days=31))):
        return {"start": None, "end": None, "precision": "unknown", "label": c.date_label}
    # An undated announcement in a first-party post is anchored to the post's own date, with a label
    # that says so (the precision is the post's, not invented).
    if (
        not s
        and pub_tr
        and pub_tr["start"]
        and page_kind in ("first_party", "launch_page")
        and (c.kind in ANNOUNCEMENT_KINDS)
    ):
        return {**pub_tr, "label": f"announced in a post dated {pub_tr['start'][:10]}"}
    return tr


async def run_analysis(
    raw_target: str, cfg: Config, *, extra_sources: list[str], console: Console, verbose: bool = False
) -> AnalysisResult:
    t_start = time.monotonic()
    started_at = iso(utcnow()) or ""
    cutoff = datetime.now(UTC)
    meter = Meter()
    prog = Progress(console, verbose)
    policies = default_registry()

    # Fail before expensive work if credentials are missing (PRD E2/E3).
    if not cfg.secret(cfg.model.api_key_env):
        raise CredentialsError(
            f"Missing model credentials: set {cfg.model.api_key_env}.",
            hint="Run `pigtail doctor`, and see docs/quickstart.md.",
        )
    model = make_model_provider(cfg, meter)
    search = make_search_provider(cfg, meter)
    gh = GitHubClient(cfg.secret(cfg.github.token_env), meter, timeout=cfg.github.request_timeout_seconds)
    hn = HackerNewsClient(meter)
    web = WebFetcher(meter)
    deadline = t_start + cfg.engine.max_runtime_minutes * 60
    try:
        # ---------------------------------------------------------------- resolve
        prog.stage(STAGES[0])
        target = await resolve(raw_target, gh)
        prog.info(
            f"{target.name} · {target.canonical_url}" + (f" · repo {target.repo.full_name}" if target.repo else "")
        )
        b = BundleBuilder(
            target=_target_dict(target),
            started_at=started_at,
            cutoff_at=iso(cutoff) or "",
            model_provider=cfg.model.provider,
            model_id=cfg.model.model,
            discovery_provider=search.provider_key,
            discovery_version=search.provider_version,
        )
        if not gh.token:
            b.gap(
                "rate_limited",
                "No GitHub token was configured, so GitHub data was limited to 60 requests/hour.",
                surface_key="github",
            )
        if target.kind == "product" and target.linked_repos and not target.repo:
            b.gap(
                "unresolved_identity",
                "The website links to GitHub repositories, but none links back to this "
                "domain, so Pigtail did not merge them: " + ", ".join(f"{o}/{r}" for o, r in target.linked_repos[:3]),
                severity="minor",
            )

        # ---------------------------------------------------------------- repository
        st: RepoState | None = None
        if target.repo:
            prog.stage(STAGES[1])
            st = await analyze_repository(
                b,
                target.repo,
                gh,
                hn,
                policies,
                name=target.name,
                homepage=target.repo.homepage,
                today=cutoff.date(),
            )
            prog.info(
                f"{target.repo.stars:,} stars · history {st.history.quality} · {len(st.episodes)} growth "
                f"episode(s) · {len(st.hn_stories)} HN stor{'y' if len(st.hn_stories) == 1 else 'ies'}"
            )

        # ---------------------------------------------------------------- discover
        prog.stage(STAGES[2])
        plan = discovery.build_plan(target, cfg.discovery.max_queries)
        cands, errs = await discovery.discover(target, search, plan, policies, extra_sources)
        for e in errs:
            b.gap(
                "discovery_error",
                f"Web search partly failed ({e}); some sources may be missing.",
                surface_key=None,
            )
        own_hosts = []
        for d in [target.domain, (target.repo.homepage if target.repo else None)]:
            if d:
                h = d.replace("https://", "").replace("http://", "").split("/")[0].removeprefix("www.")
                own_hosts += [h, f"blog.{h}", f"www.{h}"]
        own_hosts = list(dict.fromkeys(own_hosts))
        if own_hosts:
            feed_items = await feeds.discover_feeds(own_hosts, web, meter)
            seen_urls = {discovery.canonicalize_url(i.url) for i in feed_items}
            for it in await feeds.discover_sitemap_posts(own_hosts, web, meter):
                if discovery.canonicalize_url(it.url) not in seen_urls:
                    feed_items.append(it)
            known = {discovery.canonicalize_url(c.url) for c in cands}
            for it in feed_items:
                if discovery.canonicalize_url(it.url) not in known:
                    cands.append(
                        discovery.SourceCandidate(
                            url=it.url,
                            title=it.title + (f" ({it.published[:10]})" if it.published else ""),
                            origin="first_party_feed",
                            surface_key=surface_for_url(it.url),
                            page_age=it.published,
                        )
                    )
            if feed_items:
                prog.info(f"{len(feed_items)} posts listed in the product's own feeds/sitemaps")
        fixed = 1 + (1 if target.kind == "product" else 0)
        budget = max(5, cfg.engine.max_sources - fixed)
        link_only = [
            c for c in cands if not policies.policy_for(c.url, c.surface_key).access.automated_retrieval_allowed
        ]
        fetchable = [c for c in cands if c not in link_only]
        selected = await discovery.triage(fetchable, target, model, budget) if fetchable else []
        link_only_kept = await discovery.triage(link_only, target, model, 15) if link_only else []
        prog.info(f"{len(cands)} candidates · {len(selected)} selected to read · {len(link_only_kept)} link-only")

        if st and st.weak_hn:
            weak = [
                discovery.SourceCandidate(
                    url=story.item_url,
                    title=f"Hacker News: {story.title} ({story.points} points)",
                    origin="hacker_news",
                    surface_key="hacker_news",
                )
                for story in st.weak_hn
            ]
            keep = {c.url for c in await discovery.triage(weak, target, model, len(weak))}
            hpol = policies.policy_for("https://news.ycombinator.com", "hacker_news")
            for story in st.weak_hn:
                if story.item_url in keep:
                    ev = hn_event(b, story, hpol)
                    st.events.append(ev)
                    st.hn_stories.append(story)
                    if story.points >= 100:
                        st.notable_event_ids.add(ev["id"])
            prog.info(f"{len(keep)}/{len(weak)} Hacker News title mentions confirmed as about this project")

        # ---------------------------------------------------------------- fetch
        prog.stage(STAGES[3])
        pages: list[tuple[discovery.SourceCandidate, FetchedPage]] = []
        first_party: list[tuple[str, str, str]] = []  # (url, title, text) read from first-party APIs
        if st and st.readme:
            first_party.append((st.readme[1], f"{target.repo.full_name} README", st.readme[0]))  # type: ignore[union-attr]
        if target.kind == "product":
            selected.insert(
                0,
                discovery.SourceCandidate(
                    url=target.canonical_url + "/",
                    title=target.name,
                    origin="homepage",
                    surface_key="web",
                    source_kind="first_party",
                ),
            )

        async def get(c: discovery.SourceCandidate) -> tuple[discovery.SourceCandidate, FetchedPage]:
            return c, await web.fetch(c.url, policies.policy_for(c.url, c.surface_key))

        pages = await asyncio.gather(*(get(c) for c in selected))
        link_counts: dict[str, int] = {}
        for c in link_only_kept:
            pol = policies.policy_for(c.url, c.surface_key)
            s = b.add_source(c.url, surface_key=c.surface_key, source_type=c.source_kind, policy=pol, title=c.title)
            b.add_fetch(s, status="skipped_policy", error_code="link_only_policy", parser_version="none")
            link_counts[c.surface_key] = link_counts.get(c.surface_key, 0) + 1
        ok_pages = [(c, p) for c, p in pages if p.status == "success" and p.text]
        prog.info(
            f"{len(ok_pages)}/{len(pages)} pages read"
            + (f" · {len(link_only_kept)} link-only sources recorded" if link_only_kept else "")
        )

        # ---------------------------------------------------------------- extract
        prog.stage(STAGES[4])
        tdesc = f"{target.name} ({target.canonical_url})" + (f" — {target.description}" if target.description else "")
        if target.repo:
            tdesc += f"; GitHub repository {target.repo.url}"
        cutoff_s = cutoff.strftime("%Y-%m-%d")

        async def ext(url: str, title: str | None, text: str):
            try:
                return await extract_claims(
                    model=model, text=text, url=url, title=title, target_desc=tdesc, cutoff=cutoff_s
                )
            except PigtailError as exc:
                if isinstance(exc, CredentialsError):
                    raise
                log.warning("extraction failed for %s: %s", url, exc.message)
                return None, [], 0

        jobs = [ext(p.final_url, p.title or c.title, p.text or "") for c, p in ok_pages]
        jobs += [ext(u, t, x) for u, t, x in first_party]
        results = await asyncio.gather(*jobs)
        n_claims = n_rejected = 0
        for idx, (meta, verified, rejected) in enumerate(results):
            n_rejected += rejected
            if idx < len(ok_pages):
                c, p = ok_pages[idx]
                url, surface, title = p.final_url, surface_for_url(p.final_url), p.title or c.title
                pol = policies.policy_for(url, surface)
                page_kind = (meta.page_kind if meta else c.source_kind) or "other"
                if c.origin == "homepage":
                    page_kind = "first_party"
                src = b.add_source(
                    url,
                    surface_key=surface,
                    source_type=page_kind,
                    policy=pol,
                    title=title,
                    author=(meta.author if meta else None) or p.author,
                    published_at=_pub(meta, p.published),
                )
                f = b.add_fetch(
                    src,
                    status="success",
                    http_status=p.http_status,
                    content_hash=p.content_hash,
                    etag=p.etag,
                    last_modified=p.last_modified,
                    parser_version=PARSER_VERSION,
                )
                published = (meta.published_date if meta else None) or p.published
            else:
                u, t, x = first_party[idx - len(ok_pages)]
                gpol = policies.policy_for(u, "github")
                src = b.add_source(
                    u,
                    surface_key="github",
                    source_type="readme",
                    policy=gpol,
                    title=t,
                    author=target.repo.owner if target.repo else None,
                )
                f = b.add_fetch(
                    src,
                    status="success",
                    http_status=200,
                    content_hash=sha256_text(x),
                    parser_version="github-readme-raw",
                )
                page_kind, published = "first_party", None
            if meta is not None and not meta.about_target:
                b.gap(
                    "off_target_source",
                    f"A discovered source was about a different entity and was ignored: {src['url']}",
                    severity="minor",
                )
                continue
            for vc in verified:
                ev_class, directness = _evidence_fields(page_kind, vc)
                b.add_claim(
                    vc.claim.statement,
                    kind=vc.claim.kind,
                    time=_claim_time(vc, published, cutoff, page_kind),
                    evidence=[
                        EvidenceSpec(
                            src["id"],
                            f["id"],
                            ev_class,
                            directness,
                            "text_fragment",
                            vc.quote[:160],
                            excerpt=vc.quote,
                        )
                    ],
                    certainty=vc.claim.certainty,
                    negative=vc.claim.is_negative_sensitive,
                )
                n_claims += 1
        for c, p in pages:
            if p.status != "success" or not p.text:
                pol = policies.policy_for(c.url, c.surface_key)
                s = b.add_source(
                    c.url,
                    surface_key=surface_for_url(c.url),
                    source_type=c.source_kind,
                    policy=pol,
                    title=c.title,
                )
                b.add_fetch(
                    s,
                    status=p.status if p.status != "success" else "parse_failed",
                    http_status=p.http_status,
                    content_hash=p.content_hash,
                    error_code=p.error_code,
                    parser_version=PARSER_VERSION,
                )
        merged = normalization.merge_duplicate_claims(b)
        prog.info(
            f"{n_claims} verified claims ({n_rejected} rejected: quote not found or allegation-only;"
            f" {merged} merged as duplicates)"
        )

        # ---------------------------------------------------------------- reconstruct
        prog.stage(STAGES[5])
        ep_lines = [
            f"{g['time']['start'][:10]}..{g['time']['end'][:10]}: {g['summary']}"
            for _, g in (st.episodes if st else [])
        ]
        inp = reconstruction.build_input(b, tdesc, ep_lines)
        if inp.claim_lines and time.monotonic() < deadline:
            rx = await reconstruction.reconstruct(model, inp)
            dropped = reconstruction.apply_reconstruction(b, rx, inp)
            if dropped:
                prog.info("dropped by verification: " + ", ".join(f"{k} ×{v}" for k, v in dropped.items()))
        elif not inp.claim_lines:
            b.gap(
                "insufficient_evidence",
                "No verifiable claims beyond repository data were found in public sources.",
                severity="material",
            )

        # ---------------------------------------------------------------- analyze growth
        prog.stage(STAGES[6])
        if st:
            link_episodes_and_launches(b, st, cutoff.date())
        n_conf = conflicts.detect_metric_conflicts(b)
        if time.monotonic() < deadline and len(b.c["claims"]) >= 3:
            dropped_n = await synthesis.synthesize(model, b, tdesc, [])
            if dropped_n:
                prog.info(f"{dropped_n} narrative block(s) dropped for lack of cited claims")
        elif time.monotonic() >= deadline:
            b.gap(
                "runtime_limit",
                "The run hit its time limit before writing the narrative summary.",
                severity="minor",
            )
        if n_conf:
            prog.info(f"{n_conf} conflicting metric report(s) preserved")

        # ---------------------------------------------------------------- gaps + status
        prog.stage(STAGES[7])
        gaps.record_standard_gaps(
            b,
            is_repo=bool(st),
            surfaces_seen={s["surface_key"] for s in b.c["sources"]},
            link_only=link_counts,
        )
        thin = gaps.insufficient(b, is_repo=bool(st))
        if thin:
            b.gap(
                "insufficient_evidence",
                "Insufficient public evidence for a useful Pigtail forensic.",
                severity="material",
            )
        material = any(g["severity"] == "material" for g in b.c["gaps"])
        status = "completed_with_gaps" if (material or thin) else "completed"
        bundle = b.build(status=status, cost=meter.cost(), usage=meter.usage())

        # ---------------------------------------------------------------- write
        prog.stage(STAGES[8])
        out_dir = Path(cfg.engine.output_root) / f"{_slug(target.name)}-{b.run_id.replace('-', '')[-8:]}"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "assets").mkdir(exist_ok=True)
        bundle_path = out_dir / "research-bundle.json"
        try:
            write_bundle(bundle, bundle_path)
        except BundleValidationError as exc:
            raise BundleValidationError(
                exc.message + " (this is a Pigtail bug; please report it)", errors=exc.errors
            ) from exc
        duration = time.monotonic() - t_start
        run_info = {
            **bundle.run.model_dump(mode="json"),
            "target": raw_target,
            "duration_seconds": round(duration, 1),
            "model_calls": meter.model_calls,
            "pages_fetched": meter.pages_fetched,
            "sources": len(bundle.sources),
            "claims": len(bundle.claims),
            "events": len(bundle.events),
        }
        (out_dir / "run.json").write_text(dump_json(run_info), encoding="utf-8")

        # ---------------------------------------------------------------- render
        prog.stage(STAGES[9])
        report_path: Path | None = None
        try:
            report_path = ForensicRenderer().render(bundle, out_dir).report_path
        except (RenderError, BundleValidationError) as exc:  # PRD E12: keep the valid bundle
            console.print(
                f"[red]Rendering failed:[/] {exc}. The Research Bundle was saved; retry with "
                f"`pigtail render {bundle_path}`."
            )
        prog.done()
        return AnalysisResult(
            status=status,
            output_dir=out_dir,
            bundle_path=bundle_path,
            report_path=report_path,
            cost=meter.total_cost,
            duration_s=duration,
        )
    finally:
        await asyncio.gather(gh.close(), hn.close(), web.close(), return_exceptions=True)


def _pub(meta: PageExtraction | None, fallback: str | None) -> str | None:
    val = (meta.published_date if meta else None) or fallback
    tr = range_from_partial(val) if val else None
    return tr["start"] if tr and tr["start"] else None


__all__ = ["AnalysisResult", "json", "run_analysis"]

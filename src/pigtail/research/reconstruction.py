"""Reconstruct historical and interpretive objects from verified claims.

The model sees claims by short reference (c1, c2 …) and existing events (e1 …). It may only
cite those references; anything citing an unknown reference is dropped. Numbers in metrics must
appear in a cited claim. Dates must agree with a cited claim. Causal attribution is capped unless
a first-party claim states the attribution itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from pigtail.domain.time import parse_dt, range_from_partial
from pigtail.logging import get_logger
from pigtail.providers.base import ModelProvider, StructuredModelRequest
from pigtail.research.builder import BundleBuilder

log = get_logger("reconstruction")

EventType = Literal[
    "launch",
    "product_hunt_launch",
    "show_hn",
    "hacker_news_post",
    "reddit_post",
    "announcement",
    "release",
    "first_users",
    "community",
    "content",
    "press",
    "partnership",
    "pricing_change",
    "funding",
    "hiring",
    "milestone",
    "metric_milestone",
    "product",
    "strategy_change",
    "reversal",
    "other",
]


EVENT_TYPE_HELP = "One of: " + ", ".join(EventType.__args__)  # type: ignore[attr-defined]


class RXEvent(BaseModel):
    event_type: str = Field(description=EVENT_TYPE_HELP)
    title: str = Field(description="Short title, max 70 chars")
    summary: str = Field(description="1–2 sentences, factual, paraphrased")
    date: str = Field(description="(empty if unknown) YYYY, YYYY-MM or YYYY-MM-DD taken from the cited claims")
    date_end: str = Field(description="empty string if unknown")
    date_label: str = Field(description="empty string if unknown")
    claims: list[str] = Field(description="Claim refs like c3")
    is_negative_sensitive: bool


class RXMetric(BaseModel):
    metric_key: str = Field(description="snake_case, e.g. mrr, arr, users, weekly_signups, customers, team_size")
    label: str
    value: float | None
    value_text: str = Field(description="empty string if unknown")
    unit: str
    currency: str = Field(description="empty string if unknown")
    date: str = Field(description="empty string if unknown")
    date_label: str = Field(description="empty string if unknown")
    claims: list[str]


class RXPerson(BaseModel):
    name: str
    role: str = Field(description="empty string if unknown")
    claims: list[str]


class RXPhase(BaseModel):
    label: str
    summary: str
    date: str = Field(description="empty string if unknown")
    date_end: str = Field(description="empty string if unknown")
    date_label: str = Field(description="empty string if unknown")
    claims: list[str]


class RXTacticUse(BaseModel):
    implementation: str = Field(description="What this project concretely did")
    date: str = Field(description="empty string if unknown")
    date_label: str = Field(description="empty string if unknown")
    event_ref: str = Field(description="(empty if unknown) e-ref or n-ref of the event where it happened, if any")
    claims: list[str]


class RXTactic(BaseModel):
    name: str = Field(description="Reusable name another operator would understand")
    description: str
    mechanism: str = Field(description="Why it works")
    prerequisites: list[str] = Field(description="Conditions needed, only if supported by claims")
    uses: list[RXTacticUse]
    claims: list[str]


class RXEngine(BaseModel):
    name: str
    description: str
    mechanism: str
    state: Literal["emerging", "active", "weakening", "ended", "unknown"]
    strength: str = Field(description="Plain words, e.g. 'primary channel per founder', or 'unknown'")
    date: str = Field(description="empty string if unknown")
    date_end: str = Field(description="empty string if unknown")
    date_label: str = Field(description="empty string if unknown")
    claims: list[str]


class RXOutcome(BaseModel):
    summary: str
    event_ref: str = Field(description="empty string if unknown")
    attribution: Literal["company_attributed", "weakly_associated", "unknown"] = Field(
        description="company_attributed ONLY if a cited first-party claim itself states the cause."
    )
    claims: list[str]


class RXConflict(BaseModel):
    summary: str
    claims: list[str]


class RXConstraint(BaseModel):
    name: str
    description: str
    claims: list[str]


class Timeline(BaseModel):
    events: list[RXEvent]
    metrics: list[RXMetric]
    people: list[RXPerson]
    conflicts: list[RXConflict]
    missing: list[str] = Field(description="Important questions the claims cannot answer (max 6)")


class Interpretation(BaseModel):
    company_stages: list[RXPhase]
    strategy_phases: list[RXPhase]
    tactics: list[RXTactic]
    growth_engines: list[RXEngine]
    outcomes: list[RXOutcome]
    constraints: list[RXConstraint]


class Reconstruction(BaseModel):
    events: list[RXEvent]
    metrics: list[RXMetric]
    people: list[RXPerson]
    company_stages: list[RXPhase]
    strategy_phases: list[RXPhase]
    tactics: list[RXTactic]
    growth_engines: list[RXEngine]
    outcomes: list[RXOutcome]
    constraints: list[RXConstraint]
    conflicts: list[RXConflict]
    missing: list[str] = Field(description="Important questions the claims cannot answer (max 6)")


SYSTEM = (
    "You reconstruct how a product grew from a list of verified, sourced claims. You must not use outside "
    "knowledge. Every object must cite the claim refs that support it, and nothing may go beyond what those "
    "claims say. Keep these concepts distinct:\n"
    "- Event: something that happened at a time (launch, post, pricing change, funding, milestone).\n"
    "- Metric: a measured number at a time, copied exactly from a claim.\n"
    "- CompanyStage: the business's situation (e.g. 'Pre-revenue side project', 'Bootstrapped, profitable').\n"
    "- StrategyPhase: how it tried to grow in a period (e.g. 'Founder-led outreach').\n"
    "- Tactic: a bounded, reusable action another operator could copy. Tactics are not events.\n"
    "- GrowthEngine: a persistent mechanism that repeatedly creates growth (e.g. product-led attribution loop).\n"
    "- Outcome: an interpretation of a result. Timing alone is never causation: use 'weakly_associated' when "
    "something only happened nearby in time, and 'company_attributed' only when the company or founder "
    "explicitly credits the cause in a cited claim.\n"
    "Do not create events that duplicate the existing events listed (e-refs); reference them instead. "
    "Do not create star-history metrics (those are computed separately). "
    "Preserve conflicts between sources instead of choosing one; never average conflicting numbers. "
    "Do not infer misconduct, fraud, intention or motive. Prefer fewer, well-supported objects over many weak ones. "
    "If the claims are too thin for a section, return an empty list for it."
)


@dataclass
class ReconstructionInput:
    target_desc: str
    claim_refs: dict[str, str]  # ref -> claim id
    claim_lines: list[str]
    event_refs: dict[str, str]  # ref -> event id
    event_lines: list[str]
    episode_lines: list[str]


def build_input(b: BundleBuilder, target_desc: str, episode_lines: list[str]) -> ReconstructionInput:
    claim_refs, lines = {}, []
    src_by_id = {s["id"]: s for s in b.c["sources"]}
    for i, c in enumerate(b.c["claims"], 1):
        if c["claim_kind"] in ("star_history", "star_growth_episode", "release_count"):
            continue
        ref = f"c{i}"
        claim_refs[ref] = c["id"]
        evs = b.evidence_for(c["id"])
        src = src_by_id.get(evs[0]["source_id"]) if evs else None
        who = evs[0]["source_directness"] if evs else "unknown"
        date = c["time"]["label"] or (c["time"]["start"] or "")[:10] or "undated"
        host = src["canonical_url"].split("/")[2] if src else "?"
        lines.append(f"{ref} [{date}; {c['claim_kind']}; {who}; {host}] {c['statement']}")
    event_refs, elines = {}, []
    for i, e in enumerate(b.c["events"], 1):
        ref = f"e{i}"
        event_refs[ref] = e["id"]
        elines.append(f"{ref} [{(e['time']['start'] or '')[:10]}; {e['event_type']}] {e['title']}")
    return ReconstructionInput(target_desc, claim_refs, lines, event_refs, elines, episode_lines)


def _claims_block(inp: ReconstructionInput) -> str:
    return (
        f"Target: {inp.target_desc}\n\n"
        f"Verified claims ({len(inp.claim_lines)}), format: ref [date; kind; source directness; host] statement\n"
        + "\n".join(inp.claim_lines)
    )


async def reconstruct(model: ModelProvider, inp: ReconstructionInput) -> Reconstruction:
    """Two focused calls (timeline, then interpretation) keep each output schema small."""
    base = _claims_block(inp)
    prompt1 = (
        base
        + "\n\nExisting events already in the record (do not duplicate them):\n"
        + ("\n".join(inp.event_lines) or "(none)")
        + "\n\nTask: reconstruct the factual timeline. Create events, metrics, people and conflicts from the "
        "claims. New events must come from claims, not from the existing events. Metrics are growth and business measures only "
        "(users, customers, revenue/MRR/ARR, signups, downloads, waitlist, traffic, team size, funding), not technical "
        "benchmarks. For metrics, copy the value "
        "exactly as stated in a claim (convert '$20.7K' to 20700 with currency USD; percentages as 0–100)."
    )
    tl = await model.structured(
        StructuredModelRequest(
            system=SYSTEM,
            prompt=prompt1,
            output_type=Timeline,
            purpose="timeline reconstruction",
            max_tokens=32000,
            effort="medium",
        )
    )
    new_lines = [f"n{i} [{e.date or 'undated'}; {e.event_type}] {e.title}" for i, e in enumerate(tl.events, 1)]
    prompt2 = (
        base
        + "\n\nEvents (reference by e-ref or n-ref):\n"
        + "\n".join(inp.event_lines + new_lines)
        + (
            "\n\nDetected star-growth episodes (timing context only):\n" + "\n".join(inp.episode_lines)
            if inp.episode_lines
            else ""
        )
        + "\n\nTask: interpret the growth history. Identify company stages (business situation), strategy "
        "phases (growth approach), reusable tactics with where this target used them, growth engines, outcomes "
        "and constraints. Tactics should be useful to another founder and grounded in what this target did. "
        "Every object must cite claim refs."
    )
    it = await model.structured(
        StructuredModelRequest(
            system=SYSTEM,
            prompt=prompt2,
            output_type=Interpretation,
            purpose="growth interpretation",
            max_tokens=32000,
            effort="medium",
        )
    )
    return Reconstruction(
        events=tl.events,
        metrics=tl.metrics,
        people=tl.people,
        conflicts=tl.conflicts,
        missing=tl.missing,
        company_stages=it.company_stages,
        strategy_phases=it.strategy_phases,
        tactics=it.tactics,
        growth_engines=it.growth_engines,
        outcomes=it.outcomes,
        constraints=it.constraints,
    )


# ---------------------------------------------------------------------- verification helpers
def number_variants(v: float) -> set[str]:
    out = set()
    if float(v).is_integer():
        iv = int(v)
        out |= {str(iv), f"{iv:,}"}
    else:
        out |= {f"{v:g}", f"{v:,.2f}".rstrip("0").rstrip(".")}
    for div, suf in ((1e3, "k"), (1e6, "m"), (1e9, "b")):
        if abs(v) >= div:
            x = v / div
            out |= {f"{x:g}{suf}", f"{x:.1f}{suf}", f"{round(x)}{suf}", f"{x:g} {suf}"}
            word = {"k": "thousand", "m": "million", "b": "billion"}[suf]
            out |= {f"{x:g} {word}", f"{x:.1f} {word}", f"{round(x)} {word}"}
    return {s.lower() for s in out}


def number_supported(v: float, texts: list[str]) -> bool:
    hay = " ".join(t.lower().replace(" ", " ") for t in texts)
    hay_nocomma = hay.replace(",", "")
    for s in number_variants(v):
        pat = re.escape(s)
        if re.search(rf"(?<![\d.]){pat}(?![\d])", hay) or re.search(rf"(?<![\d.]){pat}(?![\d])", hay_nocomma):
            return True
    return False


def time_from(date: str | None, date_end: str | None, label: str | None) -> dict:
    date, date_end, label = (date or None), (date_end or None), (label or None)
    tr = range_from_partial(date, label)
    if date_end and tr["start"]:
        end = range_from_partial(date_end)
        if end["end"] and end["end"] >= tr["start"]:
            tr = {"start": tr["start"], "end": end["end"], "precision": "range", "label": label}
    return tr


def consistent_with_claims(tr: dict, claims: list[dict]) -> bool:
    s = parse_dt(tr.get("start"))
    if not s:
        return True
    for c in claims:
        cs, ce = parse_dt(c["time"]["start"]), parse_dt(c["time"]["end"])
        if cs and ce and cs.date() <= s.date() <= ce.date():
            return True
        if cs and ce and parse_dt(tr.get("end")) and cs <= parse_dt(tr["end"]) and s <= ce:  # type: ignore[operator]
            return True
    return not any(parse_dt(c["time"]["start"]) for c in claims)


# ---------------------------------------------------------------------- apply to bundle
PRIMARY = {"primary_direct", "primary_indirect"}


def apply_reconstruction(b: BundleBuilder, rx: Reconstruction, inp: ReconstructionInput) -> dict[str, int]:
    """Write verified objects into the builder. Returns counts of dropped objects by reason."""
    dropped: dict[str, int] = {}

    def drop(reason: str) -> None:
        dropped[reason] = dropped.get(reason, 0) + 1

    def ids(refs: list[str]) -> list[str]:
        return list(dict.fromkeys(inp.claim_refs[r] for r in refs if r in inp.claim_refs))

    def claims_of(cids: list[str]) -> list[dict]:
        return [b.claim(c) for c in cids]

    def neg(cids: list[str]) -> bool:
        return any(b.claim(c)["is_negative_sensitive"] for c in cids)

    def checked_time(date: str | None, date_end: str | None, label: str | None, cids: list[str]) -> dict:
        tr = time_from(date, date_end, label)
        cl = claims_of(cids)
        if not consistent_with_claims(tr, cl):
            drop("date_not_in_claims")
            dated = [c for c in cl if c["time"]["start"]]
            tr = (
                dict(dated[0]["time"])
                if dated
                else {"start": None, "end": None, "precision": "unknown", "label": label}
            )
        return tr

    new_event_refs: dict[str, str] = {}
    for i, e in enumerate(rx.events, 1):
        cids = ids(e.claims)
        if not cids:
            drop("event_without_claims")
            continue
        ev = b.add(
            "events",
            {
                "event_type": e.event_type,
                "title": e.title[:120],
                "summary": e.summary,
                "time": checked_time(e.date, e.date_end, e.date_label, cids),
                "surface_ids": _surfaces_for(b, e.event_type),
                "claim_ids": cids,
                "metric_snapshot_ids": [],
                "outcome_ids": [],
                "tactic_occurrence_ids": [],
                "launch_episode_id": None,
                "review_state": "machine_inferred",
                "is_negative_sensitive": e.is_negative_sensitive or neg(cids),
            },
        )
        b.attach("event", ev, cids)
        new_event_refs[f"n{i}"] = ev["id"]
    all_event_refs = {**inp.event_refs, **new_event_refs}
    # Allow the model's own new events to be referenced by their index as n1.. or by title order.
    events_by_id = {e["id"]: e for e in b.c["events"]}

    for m in rx.metrics:
        cids = ids(m.claims)
        if not cids:
            drop("metric_without_claims")
            continue
        texts = [b.claim(c)["statement"] for c in cids] + [
            ev["excerpt"] or "" for c in cids for ev in b.evidence_for(c)
        ]
        if m.value is not None and not number_supported(m.value, texts):
            drop("metric_value_not_in_claims")
            continue
        value_text = m.value_text or None
        if m.value is None and not value_text:
            drop("metric_without_value")
            continue
        if m.metric_key in ("github_stars", "stars"):
            drop("star_metric_from_model")
            continue
        unit = "percent" if m.unit in ("percent", "%") else m.unit or "count"
        cur = (m.currency or "").upper() if m.currency and re.fullmatch(r"[A-Za-z]{3}", m.currency) else None
        ms = b.add(
            "metric_snapshots",
            {
                "metric_key": re.sub(r"[^a-z0-9_]", "_", m.metric_key.lower())[:40],
                "label": m.label[:60],
                "value_numeric": m.value,
                "value_text": None if m.value is not None else value_text,
                "unit": unit,
                "currency": cur,
                "time": checked_time(m.date, None, m.date_label, cids),
                "repository_id": None,
                "claim_ids": cids,
                "review_state": "machine_extracted",
            },
        )
        b.attach("metric_snapshot", ms, cids)

    for p in rx.people:
        cids = ids(p.claims)
        if cids:
            b.add("people", {"name": p.name, "role": p.role or None, "external_ids": [], "claim_ids": cids})

    phase_ids = []
    for coll, items in (("company_stages", rx.company_stages), ("strategy_phases", rx.strategy_phases)):
        for ph in items:
            cids = ids(ph.claims)
            if not cids:
                drop(f"{coll}_without_claims")
                continue
            obj = b.add(
                coll,
                {
                    "label": ph.label,
                    "summary": ph.summary,
                    "time": checked_time(ph.date, ph.date_end, ph.date_label, cids),
                    "claim_ids": cids,
                    "review_state": "machine_inferred",
                },
            )
            if coll == "strategy_phases":
                phase_ids.append(obj)

    def phase_for(tr: dict) -> str | None:
        s = parse_dt(tr.get("start"))
        if not s:
            return None
        for ph in phase_ids:
            ps, pe = parse_dt(ph["time"]["start"]), parse_dt(ph["time"]["end"])
            if ps and pe and ps <= s <= pe:
                return ph["id"]
        return None

    for t in rx.tactics:
        tcids = ids(t.claims)
        uses = [(u, ids(u.claims)) for u in t.uses]
        all_c = list(dict.fromkeys(tcids + [c for _, cs in uses for c in cs]))
        if not all_c:
            drop("tactic_without_claims")
            continue
        tac = b.add(
            "tactics",
            {
                "name": t.name[:80],
                "description": t.description,
                "mechanism": t.mechanism,
                "status": "candidate",
                "claim_ids": all_c,
            },
        )
        for u, ucids in uses:
            ucids = ucids or all_c
            ev_id = all_event_refs.get(u.event_ref or "")
            tr = checked_time(u.date, None, u.date_label, ucids)
            occ = b.add(
                "tactic_occurrences",
                {
                    "tactic_id": tac["id"],
                    "event_id": ev_id,
                    "strategy_phase_id": phase_for(tr),
                    "time": tr,
                    "implementation": u.implementation,
                    "outcome_ids": [],
                    "claim_ids": ucids,
                    "review_state": "machine_inferred",
                },
            )
            if ev_id and ev_id in events_by_id:
                events_by_id[ev_id]["tactic_occurrence_ids"].append(occ["id"])
        for pre in t.prerequisites[:3]:
            b.add(
                "prerequisites",
                {
                    "name": pre[:80],
                    "description": pre,
                    "applies_to_refs": [{"type": "tactic", "id": tac["id"]}],
                    "claim_ids": all_c,
                    "review_state": "machine_inferred",
                },
            )

    for g in rx.growth_engines:
        cids = ids(g.claims)
        if not cids:
            drop("engine_without_claims")
            continue
        eng = b.add(
            "growth_engines",
            {
                "name": g.name[:80],
                "description": g.description,
                "mechanism": g.mechanism,
                "status": "candidate",
                "claim_ids": cids,
            },
        )
        tr = checked_time(g.date, g.date_end, g.date_label, cids)
        b.add(
            "growth_engine_occurrences",
            {
                "growth_engine_id": eng["id"],
                "strategy_phase_ids": [p for p in [phase_for(tr)] if p],
                "time": tr,
                "state": g.state,
                "strength": g.strength or "unknown",
                "tactic_occurrence_ids": [],
                "outcome_ids": [],
                "claim_ids": cids,
                "review_state": "machine_inferred",
            },
        )

    for o in rx.outcomes:
        cids = ids(o.claims)
        if not cids:
            drop("outcome_without_claims")
            continue
        attribution = o.attribution
        if attribution == "company_attributed":
            stated = any(
                b.claim(c)["claim_kind"] == "attribution"
                and any(ev["source_directness"] in PRIMARY for ev in b.evidence_for(c))
                for c in cids
            )
            if not stated:
                attribution = "weakly_associated"
                drop("attribution_downgraded")
        ev_id = all_event_refs.get(o.event_ref or "")
        out = b.add(
            "outcomes",
            {
                "summary": o.summary,
                "event_id": ev_id,
                "tactic_occurrence_id": None,
                "metric_snapshot_ids": [],
                "causal_attribution": attribution,
                "claim_ids": cids,
                "review_state": "machine_inferred",
            },
        )
        b.attach("outcome", out, cids, causal=attribution)
        if ev_id and ev_id in events_by_id:
            events_by_id[ev_id]["outcome_ids"].append(out["id"])

    for c in rx.constraints:
        cids = ids(c.claims)
        if cids:
            b.add(
                "constraints",
                {
                    "name": c.name[:80],
                    "description": c.description,
                    "applies_to_refs": [],
                    "claim_ids": cids,
                    "review_state": "machine_inferred",
                },
            )

    for cf in rx.conflicts:
        cids = ids(cf.claims)
        if len(cids) >= 2:
            b.add(
                "conflicts",
                {
                    "summary": cf.summary,
                    "claim_ids": cids,
                    "related_refs": [{"type": "claim", "id": c} for c in cids],
                    "status": "unresolved",
                },
            )
    for q in rx.missing[:6]:
        b.gap("open_question", q, severity="minor")
    return dropped


def _surfaces_for(b: BundleBuilder, etype: str) -> list[str]:
    key = {
        "product_hunt_launch": "product_hunt",
        "show_hn": "hacker_news",
        "hacker_news_post": "hacker_news",
        "reddit_post": "reddit",
        "release": "github",
    }.get(etype)
    return [b.surface(key)["id"]] if key else []

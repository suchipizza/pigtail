"""Turn a Research Bundle into a presentation view model. Reads only the Bundle (spec §22.1)."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from pigtail.bundle.models import ResearchBundle
from pigtail.domain.time import human_label, parse_dt

EVIDENCE_LABELS = {
    "documented": "Documented",
    "company_measured": "Company-reported metric",
    "third_party_measured": "Third-party measurement",
    "third_party_reported": "Third-party report",
    "inferred": "Pigtail inference",
    "unknown": "Unknown",
}
DIRECTNESS_LABELS = {
    "primary_direct": "first-party, direct",
    "primary_indirect": "first-party, indirect",
    "independent_direct": "independent, direct",
    "secondary_synthesis": "secondary synthesis",
    "community_report": "community report",
    "unknown": "unknown origin",
}
CAUSAL_LABELS = {
    "directly_measured": "Directly measured effect",
    "company_attributed": "Attributed by the company",
    "strongly_associated": "Strongly associated (not proven cause)",
    "weakly_associated": "Observed near this period (timing only)",
    "unknown": "No attribution",
}
INFERENCE_LABELS = {
    "explicit": "Stated in sources",
    "strong_inference": "Strong inference",
    "moderate_inference": "Moderate inference",
    "weak_inference": "Weak inference",
    "not_applicable": "",
}
EVENT_KIND = {
    "release": ("Release", "release"),
    "hacker_news_post": ("Hacker News", "hn"),
    "show_hn": ("Show HN", "hn"),
    "product_hunt_launch": ("Product Hunt", "ph"),
    "reddit_post": ("Reddit", "reddit"),
    "repository_created": ("Repository", "repo"),
    "launch": ("Launch", "launch"),
    "announcement": ("Announcement", "launch"),
    "funding": ("Funding", "business"),
    "pricing_change": ("Pricing", "business"),
    "milestone": ("Milestone", "milestone"),
    "metric_milestone": ("Milestone", "milestone"),
    "press": ("Press", "press"),
    "content": ("Content", "press"),
    "partnership": ("Partnership", "business"),
    "community": ("Community", "community"),
    "first_users": ("First users", "community"),
    "product": ("Product", "product"),
    "strategy_change": ("Strategy", "strategy"),
    "hiring": ("Team", "business"),
    "reversal": ("Reversal", "strategy"),
}


HIDDEN_METRIC_KEYS = {"github_forks", "github_stars_window", "github_stars"}


def _fmt_num(v: float | None) -> str:
    if v is None:
        return "—"
    if abs(v) >= 1_000_000:
        return f"{v / 1_000_000:.1f}M".replace(".0M", "M")
    if abs(v) >= 10_000:
        return f"{v / 1000:.0f}K"
    if abs(v) >= 1000:
        return f"{v / 1000:.1f}K".replace(".0K", "K")
    if float(v).is_integer():
        return f"{int(v):,}"
    return f"{v:,.2f}"


def fmt_metric(m: dict) -> str:
    if m.get("value_text"):
        return str(m["value_text"])
    v = m.get("value_numeric")
    unit = m.get("unit") or ""
    if unit == "percent":
        return f"{_fmt_num(v)}%"
    if m.get("currency"):
        sym = {"USD": "$", "EUR": "€", "GBP": "£"}.get(m["currency"], m["currency"] + " ")
        return f"{sym}{_fmt_num(v)}"
    out = _fmt_num(v)
    if unit and unit not in ("count",):
        out += f" {unit}"
    return out


def _host(url: str) -> str:
    return (urlparse(url).hostname or url).removeprefix("www.")


def build_view_model(bundle: ResearchBundle) -> dict[str, Any]:
    b = bundle.model_dump(mode="json")
    sources = {s["id"]: s for s in b["sources"]}
    fetches = {f["id"]: f for f in b["source_fetches"]}
    claims = {c["id"]: c for c in b["claims"]}
    surfaces = {s["id"]: s for s in b["surfaces"]}

    links_by_claim: dict[str, list[dict]] = defaultdict(list)
    for el in b["evidence_links"]:
        if el["target_ref"]["type"] == "claim":
            links_by_claim[el["claim_id"]].append(el)

    # Source numbering in order of first citation, for compact [n] markers.
    source_num: dict[str, int] = {}

    def num(sid: str) -> int:
        if sid not in source_num:
            source_num[sid] = len(source_num) + 1
        return source_num[sid]

    def claim_view(cid: str) -> dict | None:
        c = claims.get(cid)
        if not c:
            return None
        evs = []
        for el in links_by_claim.get(cid, []):
            s = sources[el["source_id"]]
            f = fetches.get(el["source_fetch_id"], {})
            evs.append(
                {
                    "n": num(s["id"]),
                    "source_id": s["id"],
                    "url": s["url"],
                    "title": s["title"] or _host(s["url"]),
                    "host": _host(s["url"]),
                    "surface": s["surface_key"],
                    "evidence_class": el["evidence_class"],
                    "evidence_label": EVIDENCE_LABELS[el["evidence_class"]],
                    "directness": DIRECTNESS_LABELS[el["source_directness"]],
                    "corroboration": el["corroboration"].replace("_", " "),
                    "locator": f"{el['locator']['kind']}: {el['locator']['value']}",
                    "excerpt": el["excerpt"],
                    "retrieved_at": f.get("retrieved_at"),
                    "fetch_status": f.get("status"),
                    "content_hash": f.get("content_hash"),
                    "display_mode": s["policy"]["public_display_mode"],
                }
            )
        return {
            "id": cid,
            "statement": c["statement"],
            "kind": c["claim_kind"],
            "time": human_label(c["time"]),
            "review_state": c["review_state"].replace("_", " "),
            "status": c["status"],
            "certainty": c["extraction_certainty"],
            "evidence": evs,
        }

    claim_views: dict[str, dict] = {}

    def cite(claim_ids: list[str]) -> list[dict]:
        """Return deduplicated source citations for a list of claims, registering claim views."""
        seen: dict[int, dict] = {}
        for cid in claim_ids:
            cv = claim_views.get(cid) or claim_view(cid)
            if not cv:
                continue
            claim_views[cid] = cv
            for ev in cv["evidence"]:
                seen.setdefault(ev["n"], {"n": ev["n"], "url": ev["url"], "title": ev["title"], "claim_ids": []})
                if cid not in seen[ev["n"]]["claim_ids"]:
                    seen[ev["n"]]["claim_ids"].append(cid)
        return [seen[k] for k in sorted(seen)]

    def strongest_class(claim_ids: list[str]) -> str:
        order = [
            "documented",
            "company_measured",
            "third_party_measured",
            "third_party_reported",
            "inferred",
            "unknown",
        ]
        best = "unknown"
        for cid in claim_ids:
            for el in links_by_claim.get(cid, []):
                if order.index(el["evidence_class"]) < order.index(best):
                    best = el["evidence_class"]
        return best

    t = b["target"]
    repo = next((r for r in b["repositories"] if r["id"] == t.get("primary_repository_id")), None)
    if repo is None and b["repositories"]:
        repo = b["repositories"][0]

    # ---- metrics -------------------------------------------------------------------
    star_points: list[dict] = []
    other_metrics: dict[str, list[dict]] = defaultdict(list)
    metric_by_id = {m["id"]: m for m in b["metric_snapshots"]}
    for m in b["metric_snapshots"]:
        if m["metric_key"] == "github_stars" and repo and m["repository_id"] == repo["id"]:
            dt = parse_dt(m["time"]["end"] or m["time"]["start"])
            if dt and m["value_numeric"] is not None:
                star_points.append(
                    {
                        "t": dt.strftime("%Y-%m-%d"),
                        "v": m["value_numeric"],
                        "q": (m["time"].get("label") or ""),
                    }
                )
        elif m["metric_key"] in HIDDEN_METRIC_KEYS:
            continue
        else:
            other_metrics[m["metric_key"]].append(m)
    star_points.sort(key=lambda p: p["t"])

    metric_series = []
    metric_cards = []
    for key, ms in other_metrics.items():
        ms_sorted = sorted(ms, key=lambda m: m["time"]["start"] or "9999")
        numeric = [m for m in ms_sorted if m["value_numeric"] is not None and m["time"]["start"]]
        entry = {
            "key": key,
            "label": ms_sorted[0]["label"],
            "unit": ms_sorted[0]["unit"],
            "currency": ms_sorted[0]["currency"],
            "points": [
                {
                    "t": (parse_dt(m["time"]["start"]) or datetime.now(UTC)).strftime("%Y-%m-%d"),
                    "label": human_label(m["time"]),
                    "v": m["value_numeric"],
                    "display": fmt_metric(m),
                    "cites": cite(m["claim_ids"]),
                }
                for m in numeric
            ],
            "rows": [
                {
                    "when": human_label(m["time"]),
                    "value": fmt_metric(m),
                    "cites": cite(m["claim_ids"]),
                    "label": m["label"],
                    "class": EVIDENCE_LABELS[strongest_class(m["claim_ids"])],
                }
                for m in ms_sorted
            ],
        }
        if len(numeric) >= 3:
            metric_series.append(entry)
        metric_cards.append(entry)
    metric_series.sort(key=lambda e: -len(e["points"]))

    # ---- events ---------------------------------------------------------------------
    event_views = []
    for e in sorted(b["events"], key=lambda e: e["time"]["start"] or "9999"):
        label, css = EVENT_KIND.get(e["event_type"], (e["event_type"].replace("_", " ").title(), "other"))
        start = parse_dt(e["time"]["start"])
        event_views.append(
            {
                "id": e["id"],
                "type": e["event_type"],
                "kind_label": label,
                "css": css,
                "title": e["title"],
                "summary": e["summary"],
                "when": human_label(e["time"]),
                "t": start.strftime("%Y-%m-%d") if start else None,
                "precision": e["time"]["precision"],
                "surfaces": [surfaces[s]["name"] for s in e["surface_ids"] if s in surfaces],
                "cites": cite(e["claim_ids"]),
                "claim_ids": e["claim_ids"],
                "evidence_class": strongest_class(e["claim_ids"]),
                "evidence_label": EVIDENCE_LABELS[strongest_class(e["claim_ids"])],
                "metrics": [
                    fmt_metric(metric_by_id[m]) + " " + metric_by_id[m]["label"]
                    for m in e["metric_snapshot_ids"]
                    if m in metric_by_id
                ],
                "launch_episode_id": e["launch_episode_id"],
                "review_state": e["review_state"].replace("_", " "),
                "is_inferred": e["review_state"] == "machine_inferred",
            }
        )
    event_view_by_id = {ev["id"]: ev for ev in event_views}
    categories = sorted({ev["kind_label"] for ev in event_views})

    # ---- repository forensics ----------------------------------------------------------
    growth_eps = []
    for g in sorted(b["growth_episodes"], key=lambda g: g["time"]["start"] or ""):
        s, e = parse_dt(g["time"]["start"]), parse_dt(g["time"]["end"])
        rel = [event_view_by_id[i] for i in g["related_event_ids"] if i in event_view_by_id]
        growth_eps.append(
            {
                "id": g["id"],
                "title": g["title"],
                "when": human_label(g["time"]),
                "start": s.strftime("%Y-%m-%d") if s else None,
                "end": e.strftime("%Y-%m-%d") if e else None,
                "delta": g["delta_numeric"],
                "delta_display": f"+{_fmt_num(g['delta_numeric'])}" if g["delta_numeric"] is not None else "—",
                "summary": g["summary"],
                "attribution": g["causal_attribution"],
                "attribution_label": CAUSAL_LABELS[g["causal_attribution"]],
                "events": rel,
                "unexplained": not rel,
                "cites": cite(g["claim_ids"]),
            }
        )

    outcomes_by_event: dict[str, list[dict]] = defaultdict(list)
    for o in b["outcomes"]:
        if o["event_id"]:
            outcomes_by_event[o["event_id"]].append(o)

    launch_eps = []
    for le in sorted(b["launch_episodes"], key=lambda x: x["time"]["start"] or ""):
        evs = [event_view_by_id[i] for i in le["event_ids"] if i in event_view_by_id]
        outs = []
        for ev_id in le["event_ids"]:
            for o in outcomes_by_event.get(ev_id, []):
                outs.append(
                    {
                        "summary": o["summary"],
                        "attribution_label": CAUSAL_LABELS[o["causal_attribution"]],
                        "metrics": [
                            {
                                "label": metric_by_id[m]["label"],
                                "value": fmt_metric(metric_by_id[m]),
                                "when": human_label(metric_by_id[m]["time"]),
                                "raw": metric_by_id[m]["value_numeric"],
                            }
                            for m in o["metric_snapshot_ids"]
                            if m in metric_by_id
                        ],
                        "cites": cite(o["claim_ids"]),
                    }
                )
        windows = {mtr["label"]: mtr["raw"] for o in outs for mtr in o["metrics"]}
        before = windows.get("Stars before")
        cols = []
        for lab in ("+24h", "+7d", "+30d", "+90d"):
            v = windows.get(f"Stars {lab}")
            cols.append("—" if v is None or before is None else f"+{_fmt_num(v - before)}")
        launch_eps.append(
            {
                "before": _fmt_num(before) if before is not None else "—",
                "cols": cols,
                "id": le["id"],
                "title": le["title"],
                "when": human_label(le["time"]),
                "summary": le["summary"],
                "events": evs,
                "outcomes": outs,
                "cites": cite(le["claim_ids"]),
            }
        )

    # ---- interpretation layers ---------------------------------------------------------
    tactic_occ_by_tactic: dict[str, list[dict]] = defaultdict(list)
    for to in b["tactic_occurrences"]:
        tactic_occ_by_tactic[to["tactic_id"]].append(to)
    tactics = []
    for tac in b["tactics"]:
        occs = tactic_occ_by_tactic.get(tac["id"], [])
        all_claims = list(tac["claim_ids"]) + [c for o in occs for c in o["claim_ids"]]
        tactics.append(
            {
                "name": tac["name"],
                "description": tac["description"],
                "mechanism": tac["mechanism"],
                "occurrences": [
                    {
                        "when": human_label(o["time"]),
                        "implementation": o["implementation"],
                        "event": event_view_by_id.get(o["event_id"] or "", {}).get("title"),
                    }
                    for o in occs
                ],
                "prereqs": [
                    p["name"] for p in b["prerequisites"] if any(r["id"] == tac["id"] for r in p["applies_to_refs"])
                ],
                "constraints": [
                    c["name"] for c in b["constraints"] if any(r["id"] == tac["id"] for r in c["applies_to_refs"])
                ],
                "cites": cite(all_claims),
                "evidence_label": EVIDENCE_LABELS[strongest_class(all_claims)],
            }
        )

    engine_occ: dict[str, list[dict]] = defaultdict(list)
    for go in b["growth_engine_occurrences"]:
        engine_occ[go["growth_engine_id"]].append(go)
    engines = []
    for eng in b["growth_engines"]:
        occs = engine_occ.get(eng["id"], [])
        all_claims = list(eng["claim_ids"]) + [c for o in occs for c in o["claim_ids"]]
        engines.append(
            {
                "name": eng["name"],
                "description": eng["description"],
                "mechanism": eng["mechanism"],
                "periods": [
                    {"when": human_label(o["time"]), "state": o["state"], "strength": o["strength"]} for o in occs
                ],
                "cites": cite(all_claims),
            }
        )

    phases = [
        {
            "label": p["label"],
            "summary": p["summary"],
            "when": human_label(p["time"]),
            "cites": cite(p["claim_ids"]),
            "start": p["time"]["start"] or "",
        }
        for p in b["strategy_phases"]
    ]
    phases.sort(key=lambda p: p["start"])
    stages = [
        {
            "label": s["label"],
            "summary": s["summary"],
            "when": human_label(s["time"]),
            "cites": cite(s["claim_ids"]),
            "start": s["time"]["start"] or "",
        }
        for s in b["company_stages"]
    ]
    stages.sort(key=lambda p: p["start"])
    constraints = [
        {"name": c["name"], "description": c["description"], "cites": cite(c["claim_ids"])}
        for c in b["constraints"]
        if not c["applies_to_refs"]
    ]
    reversals = [ev for ev in event_views if ev["type"] in ("reversal", "pricing_change", "strategy_change")]

    def narrative_block(key: str) -> dict | None:
        blk = b["narrative"].get(key)
        if not blk:
            return None
        return {
            "text": blk["text"],
            "cites": cite(blk["claim_ids"]),
            "inference": INFERENCE_LABELS[blk["inference_strength"]],
            "inferred": blk["inference_strength"] != "explicit",
        }

    narrative = {
        k: narrative_block(k) for k in ("thirty_second", "origin", "first_users", "flywheel", "did_differently")
    }
    takeaways = [
        {
            "text": k["text"],
            "cites": cite(k["claim_ids"]),
            "inference": INFERENCE_LABELS[k["inference_strength"]],
        }
        for k in b["narrative"]["key_takeaways"]
    ]

    people = [{"name": p["name"], "role": p["role"], "cites": cite(p["claim_ids"])} for p in b["people"]]
    founders = [p for p in people if p["role"] and "found" in p["role"].lower()][:3]
    for p in founders:
        role = p["role"] or ""
        p["short_role"] = "Co-founder" if "co-found" in role.lower() else "Founder"

    conflicts = [
        {
            "summary": c["summary"],
            "status": c["status"],
            "claims": [claim_view(i) for i in c["claim_ids"] if i in claims],
        }
        for c in b["conflicts"]
    ]
    for c in conflicts:
        for cv in c["claims"]:
            if cv:
                claim_views[cv["id"]] = cv
    gaps = sorted(
        [
            {"type": g["gap_type"].replace("_", " "), "summary": g["summary"], "severity": g["severity"]}
            for g in b["gaps"]
        ],
        key=lambda g: (g["severity"] != "material", g["type"]),
    )

    # Everything cited so far gets a number; list uncited sources at the end.
    for s in b["sources"]:
        num(s["id"])
    used_by: dict[str, int] = defaultdict(int)
    for el in b["evidence_links"]:
        if el["target_ref"]["type"] == "claim":
            used_by[el["source_id"]] += 1
    fetch_by_source: dict[str, dict] = {}
    for f in b["source_fetches"]:
        fetch_by_source[f["source_id"]] = f
    source_list = sorted(
        [
            {
                "n": source_num[s["id"]],
                "id": s["id"],
                "url": s["url"],
                "title": s["title"] or _host(s["url"]),
                "host": _host(s["url"]),
                "surface": s["surface_key"],
                "type": s["source_type"].replace("_", " "),
                "author": s["author"],
                "published": human_label(
                    {"start": s["published_at"], "end": s["published_at"], "precision": "day", "label": None}
                )
                if s["published_at"]
                else None,
                "tier": s["policy"]["coverage_tier"],
                "display_mode": s["policy"]["public_display_mode"].replace("_", " "),
                "claims": used_by.get(s["id"], 0),
                "fetch_status": (fetch_by_source.get(s["id"]) or {}).get("status", "not fetched"),
            }
            for s in b["sources"]
        ],
        key=lambda s: s["n"],
    )

    # ---- header stats ---------------------------------------------------------------
    stats = []
    if repo:
        cur = repo["current"]
        if cur["stars"] is not None:
            stats.append({"value": _fmt_num(cur["stars"]), "label": "GitHub stars (now)"})
        if cur["forks"] is not None:
            stats.append({"value": _fmt_num(cur["forks"]), "label": "forks"})
        if cur["contributors"] is not None:
            stats.append({"value": _fmt_num(cur["contributors"]), "label": "contributors"})
        created = parse_dt(repo["created_at"])
        if created:
            stats.append({"value": created.strftime("%b %Y"), "label": "repository created"})
    for entry in sorted(metric_cards, key=lambda e: -len(e["rows"])):
        if len(stats) >= 4:
            break
        last = entry["rows"][-1]
        stats.append({"value": last["value"], "label": f"{entry['label']} · {last['when']}"})

    run = b["run"]
    started, completed = parse_dt(run["started_at"]), parse_dt(run["completed_at"])
    duration = (completed - started).total_seconds() if started and completed else None

    chart_events = [
        {
            "id": ev["id"],
            "t": ev["t"],
            "title": ev["title"],
            "kind": ev["kind_label"],
            "css": ev["css"],
            "when": ev["when"],
            "precision": ev["precision"],
        }
        for ev in event_views
        if ev["t"]
    ]
    chart = None
    if repo and star_points:
        chart = {
            "stars": star_points,
            "quality": repo["star_history_quality"],
            "events": chart_events,
            "episodes": [
                {
                    "id": g["id"],
                    "start": g["start"],
                    "end": g["end"],
                    "delta": g["delta_display"],
                    "events": [e["id"] for e in g["events"]],
                    "attribution": g["attribution"],
                }
                for g in growth_eps
                if g["start"] and g["end"]
            ],
        }

    insufficient = not b["events"] and not star_points and not any(narrative.values()) and len(b["claims"]) < 5

    return {
        "bundle_id": b["bundle_id"],
        "schema_version": b["schema_version"],
        "generated_at": b["generated_at"],
        "target": {
            "name": t["name"],
            "kind": t["kind"],
            "url": t["canonical_url"],
            "domain": t["domain"],
            "description": t["description"],
            "aliases": t["aliases"],
        },
        "repo": repo,
        "repo_url": repo["url"] if repo else None,
        "stats": stats[:4],
        "run": {
            "status": run["status"],
            "status_label": {
                "completed": "Completed",
                "completed_with_gaps": "Completed with gaps",
                "failed": "Failed",
            }[run["status"]],
            "date": (completed or started or datetime.now(UTC)).strftime("%b %d, %Y"),
            "cutoff": human_label({"start": run["source_cutoff_at"], "end": None, "precision": "day", "label": None}),
            "model": f"{run['model']['provider']}/{run['model']['model']}",
            "discovery": run["discovery"]["provider"],
            "cost": run["cost"]["total_cost"],
            "duration_min": round(duration / 60, 1) if duration else None,
            "usage": run["usage"],
            "engine_version": run["engine_version"],
        },
        "narrative": narrative,
        "takeaways": takeaways,
        "people": people,
        "founders": founders,
        "events": event_views,
        "categories": categories,
        "growth_episodes": growth_eps,
        "launch_episodes": launch_eps,
        "chart": chart,
        "metric_series": metric_series,
        "metric_cards": metric_cards,
        "tactics": tactics,
        "engines": engines,
        "phases": phases,
        "stages": stages,
        "constraints": constraints,
        "reversals": reversals,
        "conflicts": conflicts,
        "gaps": gaps,
        "sources": source_list,
        "claims": claim_views,
        "counts": {
            "sources": len(b["sources"]),
            "claims": len(b["claims"]),
            "events": len(b["events"]),
            "evidence_links": len(b["evidence_links"]),
        },
        "insufficient": insufficient,
    }

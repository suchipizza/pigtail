"""`pigtail plan <brief_id>`: the D3 plan, generated from a stored brief version and its stored
D2 report (M25; DELIVERABLES D3 items 1-9; PRD F10, F11 R11.1-R11.2; ADR-091). **Deterministic
code, no LLM**: the same brief version, report and plan inputs give a byte-identical plan.

Input: the brief version (profile: channels planned / avoided, own audience, success
definition), the report JSON `pigtail report brief` wrote (`report-<date>.json`, private), the
optional plan inputs (`--inputs` YAML: available assets, time budget in hours per week, launch
window, active modules) and, when a database is available, the report's coding run and selection
(`PlanContext`: per case and feature the evidence ids behind it, and why a case is similar).

Output (`plan-<date>-<version>.{md,json}` next to the report, 0600, never in git, ADR-073.1),
sections in D3 order:

1. **Similar projects**: the report's view-A headline winners and their nearest matched losers
   (and the exemplars), with why each is similar (the selection's field distance, covariates and
   pair distance) and how to open its D1 timeline.
2. **Recommended patterns** (`rank-v2`, ADR-091 addendum 1): only the report's features, never a
   new one, each titled with its plain-language name, a one-sentence definition and its id
   (`feature_names`). A feature is recommended when its evidence is sufficient (ADR-050.3),
   d >= `MIN_D` (0.15), winner-only discordant pairs outnumber loser-only ones, it is a practice
   (not a condition of the project: modules, novelty claim, organisation owner; not an
   anti-pattern), its channel is not in the brief's `channels.avoid`, and at least `MIN_CASES`
   (3) cases support it. Ordered by language check (holds / not assessable, then weakens, then
   reverses, labelled), then discordant-pair margin, then d. A sufficient practice that fails
   the rule while at least half of both sides show it is **common practice** (table stakes, not
   a differentiator), listed with its n. Each recommendation shows n among winners and losers,
   the loser contrast, the discordant pairs, counterexamples, the report's current reliability
   labels, preconditions met / unmet / unknown, evidence ids (or why there are none) and >= 3
   cited cases.
3. **Trending opportunities**, labelled experimental: positive contrasts the report shows only
   among the distribution examples (exemplars against their matched losers) or below the
   minimum evidence with >= 3 cases present, >= 3 known per side and a positive discordant
   margin; never recommended.
4. **Readiness gaps**: the recommended assets and README sections against `available_assets`.
5. **Asset checklist**: the recommended assets with examples (verbatim excerpts and evidence ids)
   from the report's case fact sheets.
6. **Sequenced plan**: launch day L (the launch window's first day, else symbolic), L-28 ... L+42:
   pre-launch (assets, readiness), launch day and post-launch weeks with the channel order,
   spacing and UTC timing windows of the winners' launch events in the report's fact sheets; a
   channel is scheduled only when its launch pattern is recommended and >= `MIN_CASES` winners
   give its timing, and L is the first scheduled channel's day; a common-practice channel is
   table stakes, listed without a date.
7. **Predictions**: per view and outcome metric, the matched pairs' outcome distribution (the
   report's absolute numbers): the expected band (matched losers' median to winners' median) and
   the observed range; per recommended pattern, the share of the cases showing it that were
   winners (an empirical share in this neighbourhood, not a calibrated probability).
   `pigtail plan lock` pre-registers them (R11.2: hashed and timestamped; `lock_predictions`).
8. **Adaptation notes**: per recommended pattern, how to test it and what would falsify it.
9. Markdown and JSON; versioned (`plan_version`: SHA-256 of the generator version, the brief
   version's content hash, the report's hash, the inputs and the appended file).

Plus "Fast-path verdict": the file passed with `--append` (ADR-088.6), verbatim.

Where the report has too little evidence for a section, the section says "insufficient evidence
in this neighbourhood" and nothing is extrapolated.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from pigtail.forensics.facts import ASSETS, WHERE
from pigtail.forensics.feature_names import NOT_RECOMMENDABLE, describe, title
from pigtail.forensics.patterns import CONDITION_FEATURES, INSUFFICIENT

PLAN_VERSION = "plan-v1"
RANK_VERSION = "rank-v2"
MIN_CASES = 3
MIN_D = 0.15  # rank-v2 (ADR-091 addendum 1)
COMMON_SHARE = 0.5  # both sides at least this share: common practice
MAX_CITED = 6
MAX_RECOMMENDED = 12
PRE_DAYS, POST_DAYS = 28, 42
VIEW_ORDER = ("A", "B", "B-undeclared")
EXPERIMENTAL = "experimental: not a recommendation"
EMPIRICAL = "empirical share in this neighbourhood, not a calibrated probability"
README_FEATURES = ("quick_start_section", "features_section")
ASSET_NAMES = (*ASSETS, *README_FEATURES)
# the brief's channel slugs each launch feature uses (for channels.avoid / planned)
CHANNEL_OF = {
    "launch.show_hn": "hacker_news",
    "launch.launch_hn": "hacker_news",
    "launch.product_hunt": "product_hunt",
    "launch.bluesky_maintainer_post": "bluesky",
    "launch.release_launch": "github",
}
KIND_OF_WHERE = {v: k for k, v in WHERE.items()}
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


# --- inputs ---------------------------------------------------------------------------------
class LaunchWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: date
    end: date | None = None


class PlanInputs(BaseModel):
    """D3 plan inputs (`--inputs` YAML). Absent fields are reported as missing."""

    model_config = ConfigDict(extra="forbid")
    available_assets: list[Literal[ASSET_NAMES]] | None = None  # type: ignore[valid-type]
    time_budget_hours_per_week: Annotated[float, Field(ge=0, le=168)] | None = None
    launch_window: LaunchWindow | None = None
    modules_active: list[str] | None = None

    def missing(self) -> list[str]:
        return [
            k
            for k in ("available_assets", "time_budget_hours_per_week", "launch_window")
            if getattr(self, k) is None
        ]


def load_inputs(path: Path | None) -> PlanInputs:
    if path is None:
        return PlanInputs()
    import yaml

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return PlanInputs.model_validate(data)


# --- context from the database (optional) ---------------------------------------------------
@dataclass
class PlanContext:
    """What the report JSON doesn't carry, read from the report's coding run and selection:
    `evidence[(view label, repo, feature)]` -> evidence ids; `similarity[(view label, repo)]`
    -> why the case is similar (field distance, covariates, pair distance)."""

    evidence: dict[tuple[str, str, str], list[str]] = field(default_factory=dict)
    similarity: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)


VIEW_LABEL = {"follow_through": "A", "launch": "B", "launch_undeclared": "B-undeclared"}


def feature_evidence(
    feature: str, facts: Mapping[str, Any] | None, final_ids: Mapping[str, Sequence[str]]
) -> list[str]:
    """The evidence ids a case's value of `feature` rests on (report facts or final codings)."""
    f = facts or {}
    if feature.startswith(("module_active.", "pattern.")) or feature == "novelty_claim":
        return sorted(set(final_ids.get(feature) or []))
    if feature.startswith("asset."):
        a = ((f.get("assets") or {}).get("assets") or {}).get(feature.split(".", 1)[1]) or {}
        return [a["evidence_id"]] if a.get("evidence_id") else []
    if feature.startswith("readme."):
        rs = (f.get("assets") or {}).get("readme_structure") or {}
        return [rs["evidence_id"]] if rs.get("evidence_id") else []
    if feature.startswith("launch."):
        kind = feature.split(".", 1)[1]
        ids = {
            i
            for e in f.get("events") or []
            # report-facts-v2: an unconfirmed HN title match does not count (`counts` false)
            if e.get("kind") == kind and e.get("counts", e.get("launch"))
            for i in e.get("evidence_ids") or []
        }
        return sorted(ids)
    if feature == "amplifier.organization":
        ids = {
            i
            for a in f.get("amplifiers") or []
            if a.get("role") == "organization"
            for i in a.get("evidence_ids") or []
        }
        return sorted(ids)
    return []


def similarity_of(row: Mapping[str, Any]) -> dict[str, Any]:
    d = row.get("detail") or {}
    cov = d.get("covariates") or {}
    pair = d.get("pair") or {}
    out: dict[str, Any] = {
        "field_distance": row.get("distance"),
        "rank": row.get("rank"),
        "covariates": {
            k: cov.get(k)
            for k in ("language", "launch_half_year", "audience_band", "surface", "launch_type")
            if cov.get(k) is not None
        },
    }
    if pair:
        dist = pair.get("distance")
        out["pair"] = {
            "pair_id": pair.get("pair_id"),
            "distance": round(float(dist), 3) if isinstance(dist, int | float) else None,
            "same_language_group": pair.get("same_language_group"),
        }
    return out


def similarity_text(sim: Mapping[str, Any]) -> str:
    parts = []
    if sim.get("field_distance") is not None:
        parts.append(f"in the brief's field (distance {sim['field_distance']} from the core)")
    cov = sim.get("covariates") or {}
    parts += [f"{k.replace('_', ' ')} {v}" for k, v in sorted(cov.items())]
    pair = sim.get("pair")
    if pair:
        same = pair.get("same_language_group")
        parts.append(
            f"matched pair distance {pair.get('distance')}"
            + ("" if same is None else f" (same language group: {'yes' if same else 'no'})")
        )
    return "; ".join(parts) or "unknown"


def load_context(conn: Any, report: Mapping[str, Any]) -> PlanContext:
    """Read the context of `report` from its coding run and selection (read-only)."""
    from pigtail.briefs.selection_store import cases as sel_cases
    from pigtail.forensics import store as fstore

    p = report.get("provenance") or {}
    ctx = PlanContext()
    rid, sel = p.get("coding_run"), p.get("selection_id")
    if not rid or not sel:
        return ctx
    finals: dict[str, dict[str, list[str]]] = {}
    for r in fstore.codings(conn, rid, "final"):
        finals.setdefault(r["case_key"], {})[r["unit"]] = list(r.get("evidence_ids") or [])
    from pigtail.forensics.patterns import feature_names

    for c in fstore.load_cases(conn, rid):
        vl = VIEW_LABEL.get(c.view, c.view)
        for feat in feature_names():
            ids = feature_evidence(feat, c.facts, finals.get(c.case_key, {}))
            if ids:
                ctx.evidence[(vl, c.repo_full_name, feat)] = ids
    for row in sel_cases(conn, sel):
        vl = VIEW_LABEL.get(row["view"], row["view"])
        ctx.similarity[(vl, row["repo_full_name"])] = similarity_of(row)
    return ctx


# --- helpers --------------------------------------------------------------------------------
def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def plan_version(
    brief_hash: str, report_hash: str, inputs: PlanInputs, append_hash: str | None
) -> str:
    return sha256_text(
        canonical(
            {
                "generator": PLAN_VERSION,
                "brief": brief_hash,
                "report": report_hash,
                "inputs": inputs.model_dump(mode="json"),
                "append": append_hash,
            }
        )
    )


def _t(s: Any) -> datetime | None:
    if not s:
        return None
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _median(xs: Sequence[float]) -> float | None:
    return None if not xs else round(float(statistics.median(xs)), 2)


def _is_practice(feature: str) -> bool:
    return not feature.startswith("module_active.") and feature not in CONDITION_FEATURES


def _role_side(label: str) -> str:
    lab = label.lower()
    if "loser" in lab:
        return "loser"
    return "winner" if ("winner" in lab or "exemplar" in lab) else "other"


# --- sections -------------------------------------------------------------------------------
def similar_projects(report: Mapping[str, Any], ctx: PlanContext) -> dict[str, Any]:
    rows = []
    for n in report.get("narratives") or []:
        label = str(n.get("label") or "")
        view = "A"
        if "view B-undeclared" in label:
            view = "B-undeclared"
        elif "view B" in label:
            view = "B"
        sim = ctx.similarity.get((view, n["case"]))
        rows.append(
            {
                "case": n["case"],
                "label": label,
                "side": _role_side(label),
                "why_similar": sim if sim is not None else "unknown (no selection context)",
                "d1_timeline": f"`pigtail ui` → Cases → {n['case']} (a D1 case exists only when "
                "the repo is tracked)",
            }
        )
    if not any(r["side"] == "winner" for r in rows):
        return {"status": INSUFFICIENT, "cases": rows}
    return {"status": "ok", "cases": rows}


def _preconditions(
    feature: str,
    view: str,
    report: Mapping[str, Any],
    brief: Any,
    inputs: PlanInputs,
    lang: Mapping[str, Any] | None,
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    ch = CHANNEL_OF.get(feature)
    if ch is not None:
        planned = ch in (brief.channels.planned or [])
        out.append(
            {
                "condition": f"channel `{ch}` in the brief's planned channels",
                "status": "met" if planned else "unmet",
            }
        )
    ex = (report.get("patterns") or {}).get("distribution_examples") or {}
    for vl in (view, *VIEW_ORDER):
        feats = (ex.get(vl) or {}).get("features") or []
        row = next((f for f in feats if f["feature"] == feature), None)
        if row is None:
            continue
        t = row.get("transferability") or {}
        for c in t.get("conditions") or []:
            mod = str(c).split(" ", 1)[0]
            if inputs.modules_active is None:
                st = "unknown (list `modules_active` in the plan inputs)"
            else:
                st = "met" if mod in inputs.modules_active else "unmet"
            out.append({"condition": f"module `{mod}` active (exemplars: {c})", "status": st})
        break
    if lang:
        res = lang.get("result")
        out.append(
            {
                "condition": "language check (ADR-084.3)",
                "status": f"{res}" + (" (language-dependent)" if res == "reverses" else ""),
            }
        )
    return out


_SUFFIX = re.compile(r" \[[^\]]*\]$")
_N_LABEL = re.compile(r"^(pilot|full run), n = ")
LANG_TIER = {"holds": 0, "not assessable": 0, "no pattern": 0, "weakens": 1, "reverses": 2}
LANG_LABEL = {
    "weakens": "language check weakens it (smaller within same-language pairs)",
    "reverses": "language-dependent (the contrast reverses within same-language pairs)",
}


def _bare(name: str) -> str:
    """A case name without the report's flags (`org/repo [definition-sensitive]`)."""
    return _SUFFIX.sub("", str(name))


def current_labels(report: Mapping[str, Any]) -> dict[str, list[str]]:
    """field -> the report's current reliability labels (its `reliability` block, relabelled
    for a full run), which win over the labels stored with each pattern row."""
    out: dict[str, list[str]] = {}
    for r in report.get("reliability") or []:
        if r.get("statistic") == "nominal":
            out[str(r["field"])] = list(r.get("labels") or [])
    return out


def _rel_labels(feature: str, f: Mapping[str, Any], cur: Mapping[str, list[str]]) -> list[str]:
    labels = list((f.get("reliability") or {}).get("labels") or [])
    now = [x for x in cur.get(feature, []) if _N_LABEL.match(x)]
    if now:
        labels = [x for x in labels if not _N_LABEL.match(x)] + now
    return labels + list(f.get("labels") or [])


def _margin(f: Mapping[str, Any]) -> int:
    p = f.get("pairs") or {}
    return int(p.get("winner_only") or 0) - int(p.get("loser_only") or 0)


def recommended_patterns(
    report: Mapping[str, Any], brief: Any, inputs: PlanInputs, ctx: PlanContext
) -> dict[str, Any]:
    """`rank-v2` (ADR-091 addendum 1): see the module docstring."""
    views = (report.get("patterns") or {}).get("views") or {}
    avoid = set(brief.channels.avoid or [])
    cur = current_labels(report)
    cands: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    common: list[dict[str, Any]] = []
    for vl in VIEW_ORDER:
        v = views.get(vl)
        if not v:
            continue
        for f in v.get("features") or []:
            feat = f["feature"]
            d = f.get("d")
            lang = f.get("language_check") or {}
            sup = f.get("supporting") or {}
            support = list(sup.get("winners_with") or []) + list(sup.get("losers_without") or [])
            w, lo = f["winners"], f["matched_losers"]
            margin = _margin(f)
            if not f.get("sufficient"):
                continue
            why = None
            if feat in NOT_RECOMMENDABLE:
                why = "anti-pattern: detection and contrast only, never recommended"
            elif not _is_practice(feat):
                why = "a condition of the project, not a practice"
            elif CHANNEL_OF.get(feat) in avoid:
                why = f"channel `{CHANNEL_OF[feat]}` is in the brief's channels to avoid"
            elif d is None or d < MIN_D:
                why = f"contrast below the threshold (d = {d} < {MIN_D})"
            elif margin <= 0:
                why = (
                    f"discordant pairs don't favour winners (winner only "
                    f"{(f.get('pairs') or {}).get('winner_only')} vs loser only "
                    f"{(f.get('pairs') or {}).get('loser_only')})"
                )
            elif len(support) < MIN_CASES:
                why = f"fewer than {MIN_CASES} supporting cases"
            if why is not None:
                row = {
                    "view": vl, "feature": feat, "name": title(feat), "d": d, "winners": w,
                    "matched_losers": lo, "pairs": f.get("pairs"), "why": why,
                }  # fmt: skip
                both = (w.get("share") or 0) >= COMMON_SHARE and (
                    lo.get("share") or 0
                ) >= COMMON_SHARE
                if both and _is_practice(feat) and feat not in NOT_RECOMMENDABLE:
                    common.append(row)
                elif (d or 0) > 0:
                    rejected.append(row)
                continue
            cited = [c for c in sup.get("winners_with") or []][: MAX_CITED // 2 + 1]
            cited += list(sup.get("losers_without") or [])[: MAX_CITED - len(cited)]
            ev = sorted({i for c in cited for i in ctx.evidence.get((vl, _bare(c), feat), [])})
            if ev:
                ev_note = None
            elif not (ctx.evidence or ctx.similarity):
                ev_note = "not loaded (no database context)"
            else:
                ev_note = (
                    "none attached: the cited cases' values of this feature carry no evidence id "
                    "(an `absent` value or a derived field cites nothing)"
                )
            res = lang.get("result")
            labels = _rel_labels(feat, f, cur)
            if res in LANG_LABEL:
                labels.append(LANG_LABEL[res])
            name, definition = describe(feat)
            cands.append(
                {
                    "view": vl,
                    "feature": feat,
                    "name": name,
                    "title": title(feat),
                    "definition": definition,
                    "pattern_ref": f"report patterns, view {vl}, feature {feat}",
                    "winners": w,
                    "matched_losers": lo,
                    "d": d,
                    "discordant_margin": margin,
                    "loser_contrast": f"{w['n_present']}/{w['n_known']} winners vs "
                    f"{lo['n_present']}/{lo['n_known']} matched losers (d = {d})",
                    "pairs": f.get("pairs"),
                    "counterexamples": f.get("counterexamples"),
                    "reliability": f.get("reliability"),
                    "reliability_labels": labels,
                    "language_check": res,
                    "preconditions": _preconditions(feat, vl, report, brief, inputs, lang),
                    "cited_cases": cited,
                    "supporting_cases": len(support),
                    "evidence_ids": ev,
                    "evidence_note": ev_note,
                    "p_winner_given_pattern": _p_winner(w, lo),
                }
            )
    cands.sort(
        key=lambda r: (
            LANG_TIER.get(str(r["language_check"]), 0),
            -r["discordant_margin"],
            -(r["d"] or 0),
            VIEW_ORDER.index(r["view"]),
            r["feature"],
        )
    )
    seen: dict[str, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for r in cands:
        if r["feature"] in seen:
            seen[r["feature"]].setdefault("also_in", []).append({"view": r["view"], "d": r["d"]})
            continue
        seen[r["feature"]] = r
        out.append(r)
    for i, r in enumerate(out, 1):
        r["rank"] = i
    rec = {r["feature"] for r in out}
    common_seen: set[str] = set()
    common_out = []
    for r in common:
        if r["feature"] in rec or r["feature"] in common_seen:
            continue
        common_seen.add(r["feature"])
        common_out.append(r)
    return {
        "status": "ok" if out else INSUFFICIENT,
        "rule": RANK_VERSION,
        "thresholds": {
            "min_d": MIN_D,
            "discordant_margin": "> 0",
            "min_supporting_cases": MIN_CASES,
            "common_practice_share": COMMON_SHARE,
        },
        "patterns": out[:MAX_RECOMMENDED],
        "common_practice": common_out,
        "not_recommended": [r for r in rejected if r["feature"] not in rec],
    }


def _p_winner(w: Mapping[str, Any], lo: Mapping[str, Any]) -> dict[str, Any]:
    a, b = int(w.get("n_present") or 0), int(lo.get("n_present") or 0)
    return {
        "winners": a,
        "cases_with_pattern": a + b,
        "share": None if a + b == 0 else round(a / (a + b), 4),
        "label": EMPIRICAL,
    }


def trending(report: Mapping[str, Any], recommended: Sequence[str]) -> dict[str, Any]:
    pats = report.get("patterns") or {}
    rows: list[dict[str, Any]] = []
    for vl in VIEW_ORDER:
        for f in (pats.get("distribution_examples") or {}).get(vl, {}).get("features") or []:
            t = (f.get("transferability") or {}).get("label")
            if (
                f["feature"] in recommended
                or not _is_practice(f["feature"])
                or f["feature"] in NOT_RECOMMENDABLE
                or (f.get("d") or 0) <= 0
                or t == "not transferable"
                or f["winners"]["n_present"] < 2
            ):
                continue
            rows.append(
                {
                    "source": f"distribution examples, view {vl}",
                    "feature": f["feature"],
                    "exemplars": f["winners"],
                    "matched_losers": f["matched_losers"],
                    "d": f["d"],
                    "transferability": f.get("transferability"),
                    "labels": [EXPERIMENTAL, *f.get("labels", [])],
                }
            )
        for f in (pats.get("views") or {}).get(vl, {}).get("features") or []:
            w, lo = f["winners"], f["matched_losers"]
            if (
                f.get("sufficient")
                or f["feature"] in recommended
                or not _is_practice(f["feature"])
                or f["feature"] in NOT_RECOMMENDABLE
                or (f.get("d") or 0) <= 0
                or w["n_present"] + lo["n_present"] < MIN_CASES
                or min(w["n_known"], lo["n_known"]) < MIN_CASES
                or _margin(f) <= 0
            ):
                continue
            rows.append(
                {
                    "source": f"view {vl}, below the minimum evidence",
                    "feature": f["feature"],
                    "winners": w,
                    "matched_losers": lo,
                    "d": f["d"],
                    "labels": [EXPERIMENTAL, *f.get("labels", [])],
                }
            )
    return {"status": "ok" if rows else INSUFFICIENT, "label": EXPERIMENTAL, "signals": rows}


def _asset_of(feature: str) -> str | None:
    if feature.startswith("asset."):
        return feature.split(".", 1)[1]
    if feature.startswith("readme."):
        return feature.split(".", 1)[1]
    return None


def readiness(recs: Sequence[Mapping[str, Any]], inputs: PlanInputs) -> dict[str, Any]:
    items = []
    for r in recs:
        a = _asset_of(r["feature"])
        if a is None:
            continue
        if inputs.available_assets is None:
            st = "unknown (plan input `available_assets` missing)"
        else:
            st = "ready" if a in inputs.available_assets else "gap: fix before launch"
        items.append({"asset": a, "pattern": r["feature"], "view": r["view"], "status": st})
    return {"status": "ok" if items else INSUFFICIENT, "items": items}


def asset_checklist(report: Mapping[str, Any], recs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    items = []
    for r in recs:
        a = _asset_of(r["feature"])
        if a is None:
            continue
        cited = set(r["cited_cases"])
        examples = []
        for n in report.get("narratives") or []:
            if _role_side(str(n.get("label"))) != "winner":
                continue
            facts = n.get("facts") or {}
            if r["feature"].startswith("asset."):
                for x in facts.get("assets_at_launch") or []:
                    if x.get("asset") == a and x.get("value") == "present":
                        examples.append(
                            {
                                "case": n["case"],
                                "excerpt": x.get("excerpt"),
                                "evidence_ids": list(x.get("evidence_ids") or []),
                                "cited": n["case"] in cited,
                            }
                        )
            else:
                rd = facts.get("readme_at_launch") or {}
                if rd.get(a):
                    examples.append(
                        {
                            "case": n["case"],
                            "excerpt": "README headings: " + "; ".join(rd.get("headings") or []),
                            "evidence_ids": list(rd.get("evidence_ids") or []),
                            "cited": n["case"] in cited,
                        }
                    )
        examples.sort(key=lambda e: (not e["cited"], e["case"]))
        items.append({"asset": a, "pattern": r["feature"], "examples": examples[:3]})
    return {"status": "ok" if items else INSUFFICIENT, "items": items}


def launch_timing(report: Mapping[str, Any]) -> dict[str, Any]:
    """Channel order, spacing and UTC timing windows of the winners' (and losers') launch
    events in the report's fact sheets (relative to each case's first launch event)."""
    per_side: dict[str, dict[str, list[dict[str, Any]]]] = {"winner": {}, "loser": {}}
    spacing: list[float] = []
    for n in report.get("narratives") or []:
        side = _role_side(str(n.get("label")))
        if side not in per_side:
            continue
        evs = []
        for e in (n.get("facts") or {}).get("launch_events") or []:
            if e.get("counts") is False:  # unconfirmed HN title match (report-facts-v2)
                continue
            kind = e.get("kind") or KIND_OF_WHERE.get(str(e.get("where")))
            t = _t(e.get("when"))
            if kind is None or kind in ("first_mention", "anchor", "release") or t is None:
                continue
            evs.append((t, kind, list(e.get("evidence_ids") or [])))
        if not evs:
            continue
        evs.sort(key=lambda x: (x[0], x[1]))
        t0 = evs[0][0]
        if side == "winner":
            spacing += [round((b[0] - a[0]).total_seconds() / 86400, 2) for a, b in pairwise(evs)]
        for t, kind, ids in evs:
            per_side[side].setdefault(kind, []).append(
                {
                    "case": n["case"],
                    "offset_days": round((t - t0).total_seconds() / 86400, 2),
                    "weekday": WEEKDAYS[t.weekday()],
                    "hour_utc": t.hour,
                    "evidence_ids": ids,
                }
            )
    channels: list[dict[str, Any]] = []
    for kind, obs in per_side["winner"].items():
        cases = sorted({o["case"] for o in obs})
        hours = sorted(o["hour_utc"] for o in obs)
        wd: dict[str, int] = {}
        for o in obs:
            wd[o["weekday"]] = wd.get(o["weekday"], 0) + 1
        lo = per_side["loser"].get(kind, [])
        channels.append(
            {
                "kind": kind,
                "channel": CHANNEL_OF.get(f"launch.{kind}"),
                "where": WHERE.get(kind, kind),
                "winners": len(cases),
                "median_offset_days": _median([o["offset_days"] for o in obs]),
                "first_for_winners": sum(1 for o in obs if o["offset_days"] == 0),
                "hours_utc": {"min": hours[0], "median": _median(hours), "max": hours[-1]},
                "weekdays": dict(sorted(wd.items(), key=lambda x: WEEKDAYS.index(x[0]))),
                "losers": len({o["case"] for o in lo}),
                "loser_median_offset_days": _median([o["offset_days"] for o in lo]),
                "cases": cases,
                "evidence_ids": sorted({i for o in obs for i in o["evidence_ids"]}),
                "sufficient": len(cases) >= MIN_CASES,
            }
        )
    channels.sort(key=lambda c: (c["median_offset_days"] or 0, -c["winners"], c["kind"]))
    return {
        "channels": channels,
        "median_spacing_days": _median(spacing),
        "spacings_observed": len(spacing),
    }


def calendar(
    brief: Any,
    inputs: PlanInputs,
    timing: Mapping[str, Any],
    ready: Mapping[str, Any],
    recommended: Sequence[str] = (),
    common: Sequence[str] = (),
) -> dict[str, Any]:
    """Losers count: a channel is scheduled only when its launch pattern is recommended and
    >= MIN_CASES winners give its timing. L is the first scheduled channel's day (offsets are
    shifted so the earliest scheduled median offset is 0). A channel in "common practice" is
    table stakes: listed without a date (the evidence doesn't say when). Other channels the
    winners used are reference only."""
    start = inputs.launch_window.start if inputs.launch_window else None

    def day(off: int) -> str:
        lab = f"L{off:+d}" if off else "L"
        return lab if start is None else f"{lab} ({(start + timedelta(days=off)).isoformat()})"

    avoid = set(brief.channels.avoid or [])
    rec, com = set(recommended), set(common)
    ok_ch = [
        c
        for c in timing["channels"]
        if c["sufficient"] and (c["channel"] is None or c["channel"] not in avoid)
    ]
    sched = [c for c in ok_ch if f"launch.{c['kind']}" in rec]
    stakes = [
        {"where": WHERE.get(f.split(".", 1)[1], f), "pattern": title(f),
         "note": "table stakes: do it; the evidence doesn't say when"}
        for f in sorted(com)
        if f.startswith("launch.") and CHANNEL_OF.get(f) not in avoid
    ]  # fmt: skip
    reference = [
        {"where": c["where"], "winners": c["winners"], "losers": c["losers"],
         "why": "not a recommended pattern (no sufficient winner-vs-loser contrast)"}
        for c in ok_ch
        if c not in sched and f"launch.{c['kind']}" not in com
    ]  # fmt: skip
    thin = [c["where"] for c in timing["channels"] if not c["sufficient"]]
    base = min((c["median_offset_days"] or 0 for c in sched), default=0)
    entries: list[dict[str, Any]] = []
    gaps = [i for i in ready.get("items") or [] if not str(i["status"]).startswith("ready")]
    if gaps:
        entries.append(
            {
                "when": f"{day(-PRE_DAYS)} … {day(-7)}",
                "phase": "pre-launch",
                "action": "produce the recommended assets: "
                + ", ".join(sorted({g["asset"] for g in gaps})),
                "basis": "readiness gaps / asset checklist",
            }
        )
    entries.append(
        {
            "when": f"{day(-7)} … {day(-1)}",
            "phase": "pre-launch",
            "action": "check every checklist item against the README at launch; lock the "
            "predictions (`pigtail plan lock`)",
            "basis": "PRD F11 R11.2 (predictions locked before launch)",
        }
    )  # fmt: skip
    for c in sched:
        off = round((c["median_offset_days"] or 0) - base)
        hrs = c["hours_utc"]
        wd = ", ".join(f"{k} {v}" for k, v in c["weekdays"].items())
        entries.append(
            {
                "when": day(off),
                "phase": "launch day" if off == 0 else "post-launch",
                "action": f"{c['where']} ({title('launch.' + c['kind'])})",
                "timing_window": f"{hrs['min']:02d}–{hrs['max']:02d} UTC (median "
                f"{hrs['median']}); winners' weekdays: {wd}",
                "basis": f"{c['winners']} winners (median offset {c['median_offset_days']} d "
                f"from their first launch event); {c['losers']} matched losers used it",
                "evidence_ids": c["evidence_ids"],
            }
        )
    for w in range(1, POST_DAYS // 7 + 1):
        entries.append(
            {
                "when": f"{day(7 * (w - 1) + 1)} … {day(7 * w)}",
                "phase": f"post-launch week {w}",
                "action": "record the outcome against the locked predictions (launch mode)",
                "basis": "D3 item 7, D5 launch mode",
            }
        )
    entries.sort(key=lambda e: (_offset_key(e["when"]), e["phase"]))
    return {
        "status": "ok" if sched else INSUFFICIENT,
        "launch_day": ("L (symbolic: plan input `launch_window` missing)" if start is None
                       else start.isoformat())
        + ("; L = the first scheduled channel" if sched else "; no channel has a recommended "
           "timing"),
        "median_spacing_days": timing["median_spacing_days"],
        "channels_scheduled": [c["where"] for c in sched],
        "table_stakes": stakes,
        "channels_reference_only": reference,
        "channels_insufficient": thin,
        "channels_avoided": sorted(avoid),
        "time_budget_hours_per_week": inputs.time_budget_hours_per_week,
        "pre_launch_hours": None
        if inputs.time_budget_hours_per_week is None
        else round(inputs.time_budget_hours_per_week * PRE_DAYS / 7, 1),
        "entries": entries,
    }  # fmt: skip


def _offset_key(when: str) -> int:
    s = when.split(" ", 1)[0]
    if s == "L":
        return 0
    try:
        return int(s[1:])
    except ValueError:
        return 0


def _window(metric: str) -> str:
    if "@" not in metric:
        return "unknown"
    w = metric.rsplit("@", 1)[1]
    a, _, b = w.partition("-")
    return f"days {a}–{b} after T" if b else f"day {a}"


def predictions(report: Mapping[str, Any], recs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    views = (report.get("patterns") or {}).get("views") or {}
    for vl in VIEW_ORDER:
        v = views.get(vl)
        if not v:
            continue
        nums = v.get("absolute_numbers") or {}
        w_all, l_all = nums.get("winners") or {}, nums.get("matched_losers") or {}
        top = next((r for r in recs if r["view"] == vl), None)
        for m in sorted(set(w_all) & set(l_all)):
            w, lo = w_all[m], l_all[m]
            if min(int(w.get("n") or 0), int(lo.get("n") or 0)) < MIN_CASES:
                continue
            pid = f"P-{vl}-{m}"
            item: dict[str, Any] = {
                "id": pid,
                "view": vl,
                "metric": m,
                "window": _window(m),
                "expected_band": {"low": lo["median"], "high": w["median"]},
                "observed_range": {
                    "min": min(lo["min"], w["min"]),
                    "max": max(lo["max"], w["max"]),
                },
                "winners": w,
                "matched_losers": lo,
                "claim": f"{m} lands at or above the matched losers' median ({lo['median']})",
            }
            if top is not None:
                p = top["p_winner_given_pattern"]
                item["pattern"] = top["feature"]
                item["probability"] = p["share"]
                item["probability_basis"] = (
                    f"{p['winners']}/{p['cases_with_pattern']} cases with {top['feature']} "
                    f"were winners ({EMPIRICAL})"
                )
            else:
                item["pattern"] = None
                item["probability"] = None
                item["probability_basis"] = "no recommended pattern in this view"
            items.append(item)
    return {
        "status": "ok" if items else INSUFFICIENT,
        "outcome_label": (report.get("patterns") or {}).get("outcome_label"),
        "items": items,
        "lock": "pigtail plan lock <brief_id> pre-registers these (hash + timestamp; R11.2)",
    }


def adaptation_notes(recs: Sequence[Mapping[str, Any]], preds: Mapping[str, Any]) -> dict[str, Any]:
    notes = []
    by_view: dict[str, Mapping[str, Any]] = {}
    for p in preds.get("items") or []:
        by_view.setdefault(p["view"], p)
    for r in recs:
        p = by_view.get(r["view"])
        feat = r["feature"]
        if feat.startswith("launch."):
            test = (
                f"post on {WHERE.get(feat.split('.', 1)[1], feat)} as scheduled and compare "
                "the stars gained on days +1/+7 with the winners' fact sheets"
            )
        elif _asset_of(feat) is not None:
            test = (
                f"ship `{_asset_of(feat)}` in the README before L; check it is in the README at T"
            )
        else:
            test = "apply the practice before L and record when and how (launch mode)"
        fals = (
            f"the outcome ({p['metric']}) lands below the matched losers' median "
            f"({p['expected_band']['low']}) with the pattern in place"
            if p
            else "no outcome prediction in this view (insufficient evidence)"
        )
        fals += (
            f"; or a later run of this brief shows d <= 0 or a reversing language check for {feat}"
        )
        notes.append(
            {"pattern": feat, "view": r["view"], "how_to_test": test, "falsified_if": fals}
        )
    return {"status": "ok" if notes else INSUFFICIENT, "notes": notes}


# --- the plan -------------------------------------------------------------------------------
def build_plan(
    brief: Any,
    report: Mapping[str, Any],
    *,
    report_hash: str,
    inputs: PlanInputs | None = None,
    ctx: PlanContext | None = None,
    append_text: str | None = None,
) -> dict[str, Any]:
    """The whole D3 plan as a dict (module docstring). Pure and deterministic."""
    inputs = inputs or PlanInputs()
    ctx = ctx or PlanContext()
    p = report.get("provenance") or {}
    bh = brief.content_hash()
    if p.get("brief_hash") and p["brief_hash"] != bh:
        raise ValueError("the report was written for another content of this brief version")
    append_hash = None if append_text is None else sha256_text(append_text)
    version = plan_version(bh, report_hash, inputs, append_hash)
    rec = recommended_patterns(report, brief, inputs, ctx)
    recs = rec["patterns"]
    ready = readiness(recs, inputs)
    timing = launch_timing(report)
    preds = predictions(report, recs)
    return {
        "provenance": {
            "plan_version": version,
            "generator": PLAN_VERSION,
            "rank_rule": RANK_VERSION,
            "brief_id": brief.brief_id,
            "brief_version": brief.version,
            "brief_hash": bh,
            "report_hash": report_hash,
            "report_date": p.get("date"),
            "report_run": p.get("report_run"),
            "coding_run": p.get("coding_run"),
            "data_version": p.get("data_version"),
            "report_code_commit": p.get("code_commit"),
            "label": p.get("label"),
            "coding_label": p.get("coding_label"),
            "success_primary": brief.success.primary,
            "channels_planned": list(brief.channels.planned or []),
            "channels_avoid": list(brief.channels.avoid or []),
            "inputs": inputs.model_dump(mode="json"),
            "inputs_missing": inputs.missing(),
            "context_loaded": bool(ctx.evidence or ctx.similarity),
            "append_hash": append_hash,
            "llm": "none (deterministic)",
        },
        "similar_projects": similar_projects(report, ctx),
        "recommended_patterns": rec,
        "trending_opportunities": trending(report, [r["feature"] for r in recs]),
        "readiness_gaps": ready,
        "asset_checklist": asset_checklist(report, recs),
        "calendar": calendar(
            brief,
            inputs,
            timing,
            ready,
            [r["feature"] for r in recs],
            [r["feature"] for r in rec["common_practice"]],
        ),
        "launch_timing": timing,
        "predictions": preds,
        "adaptation_notes": adaptation_notes(recs, preds),
        "fast_path_verdict": {
            "status": "provided" if append_text is not None else "not provided",
            "text": append_text,
        },
        "limitations": LIMITATIONS,
    }


LIMITATIONS = [
    "Associations between winners and matched losers in one neighbourhood, not causes; a "
    "pattern is recommended only from the report, never extrapolated.",
    "Probabilities are empirical shares of the report's cases, not calibrated forecasts; they "
    "are scored after launch (R11.2).",
    "The calendar uses only the launch events of the cases with a fact sheet in the report "
    "(exemplars and view A's best-ranked pairs); a channel is scheduled only with >= 3 winners.",
    "Timing windows are UTC hours and weekdays of the winners' posts, not causal effects.",
    "Reliability labels are the report's (LLM-coded features are not human-validated).",
]


# --- markdown -------------------------------------------------------------------------------
def _c(v: Any) -> str:
    s = "" if v is None else str(v)
    return s.replace("|", "\\|").replace("\n", " ")


def _nw(x: Mapping[str, Any]) -> str:
    w, lo, p = x["winners"], x["matched_losers"], x.get("pairs") or {}
    return (
        f"{w['n_present']}/{w['n_known']} winners vs {lo['n_present']}/{lo['n_known']} matched "
        f"losers (d = {x['d']}; discordant pairs {p.get('winner_only')} winner only / "
        f"{p.get('loser_only')} loser only)"
    )


def _ins(section: Mapping[str, Any]) -> list[str]:
    return [f"_{INSUFFICIENT}._", ""] if section.get("status") == INSUFFICIENT else []


def plan_markdown(plan: Mapping[str, Any]) -> str:
    p = plan["provenance"]
    out = [
        f"# Launch plan (D3) — brief {p['brief_id']} v{p['brief_version']}",
        "",
        "Private (ADR-073.1): never commit, publish or share this file.",
        "",
        f"**{p['label']}** · {p['coding_label']} · generated by deterministic rules, no LLM",
        "",
        "| Provenance | |",
        "|---|---|",
        f"| plan version | {p['plan_version'][:16]} ({p['generator']}, {p['rank_rule']}) |",
        f"| brief | v{p['brief_version']} (hash {p['brief_hash'][:12]}); success: "
        f"{p['success_primary']} |",
        f"| report | {p['report_date']} (hash {p['report_hash'][:12]}; run {p['report_run']}; "
        f"coding run {p['coding_run']}) |",
        f"| data version | {p['data_version']} |",
        f"| channels | planned: {_c(', '.join(p['channels_planned']) or '-')}; avoid: "
        f"{_c(', '.join(p['channels_avoid']) or '-')} |",
        f"| plan inputs | {_c(canonical(p['inputs']))} |",
        f"| inputs missing | {_c(', '.join(p['inputs_missing']) or 'none')} |",
        "",
    ]
    if p["inputs_missing"]:
        out += [
            "Missing plan inputs: " + ", ".join(p["inputs_missing"]) + ". The calendar is "
            "relative to launch day L (L-28 … L+42); pass `--inputs` to fill them.",
            "",
        ]
    sp = plan["similar_projects"]
    out += ["## 1. Similar projects", "", *_ins(sp)]
    for c in sp["cases"]:
        w = c["why_similar"]
        why = w if isinstance(w, str) else _c(similarity_text(w))
        out.append(f"- **{c['case']}** — {c['label']}. Why similar: {why}. D1: {c['d1_timeline']}")
    rp = plan["recommended_patterns"]
    th = rp["thresholds"]
    out += [
        "",
        "## 2. Recommended patterns",
        "",
        f"Rule {rp['rule']}: sufficient evidence, d ≥ {th['min_d']}, more winner-only than "
        f"loser-only discordant pairs, ≥ {th['min_supporting_cases']} supporting cases; ordered "
        "by language check (holds first), then discordant-pair margin, then d.",
        "",
        *_ins(rp),
    ]
    for r in rp["patterns"]:
        ce = r["counterexamples"] or {}
        ce_txt = (
            "none found"
            if ce.get("none_found")
            else f"winners without: {', '.join(ce.get('winners_without') or []) or '-'}; "
            f"losers with: {', '.join(ce.get('losers_with') or []) or '-'}"
        )
        a = (r["reliability"] or {}).get("alpha")
        out += [
            f"### {r['rank']}. {r['title']} (view {r['view']})",
            "",
            f"_{r['definition']}_",
            "",
            f"- Pattern: {r['pattern_ref']}",
            f"- Discordant-pair margin (winner only − loser only): {r['discordant_margin']}",
            f"- n: {r['loser_contrast']}",
            f"- Pairs (winner only / loser only / both / neither): "
            f"{_c((r['pairs'] or {}).get('winner_only'))} / "
            f"{_c((r['pairs'] or {}).get('loser_only'))} / {_c((r['pairs'] or {}).get('both'))}"
            f" / {_c((r['pairs'] or {}).get('neither'))}",
            f"- Counterexamples: {ce_txt}",
            f"- Reliability: {'-' if a is None else f'alpha {a:.2f}'}; "
            f"{'; '.join(r['reliability_labels']) or '-'}",
            f"- Language check: {r['language_check'] or '-'}",
            "- Preconditions: "
            + ("; ".join(f"{x['condition']}: {x['status']}" for x in r["preconditions"]) or "none"),
            f"- Cited cases ({len(r['cited_cases'])} of {r['supporting_cases']} supporting): "
            + ", ".join(r["cited_cases"]),
            f"- Evidence: {', '.join(r['evidence_ids']) or r['evidence_note']}",
        ]
        if r.get("also_in"):
            out.append(
                "- Also in: " + ", ".join(f"view {x['view']} (d = {x['d']})" for x in r["also_in"])
            )
        out.append("")
    out += [
        "### Common practice (winners and losers both do it; table stakes, not a differentiator)",
        "",
    ]
    out += [
        f"- {x['name']} (view {x['view']}): {_nw(x)}; {x['why']}" for x in rp["common_practice"]
    ] or ["- none"]
    out.append("")
    if rp["not_recommended"]:
        out += ["### Positive contrasts not recommended", ""]
        out += [f"- {x['name']} (view {x['view']}): {_nw(x)}; {x['why']}"
                for x in rp["not_recommended"]]  # fmt: skip
        out.append("")
    tr = plan["trending_opportunities"]
    out += [f"## 3. Trending opportunities ({tr['label']})", "", *_ins(tr)]
    for s in tr["signals"]:
        w = s.get("exemplars") or s.get("winners") or {}
        lo = s.get("matched_losers") or {}
        t = (s.get("transferability") or {}).get("label")
        out.append(
            f"- {title(s['feature'])} — {s['source']}: {w.get('n_present')}/{w.get('n_known')} vs "
            f"{lo.get('n_present')}/{lo.get('n_known')} (d = {s['d']})"
            + (f"; transferability: {t}" if t else "")
            + f"; {'; '.join(s['labels'])}"
        )
    rg = plan["readiness_gaps"]
    out += ["", "## 4. Readiness gaps", "", *_ins(rg)]
    out += [f"- {title(i['pattern'])} (view {i['view']}): {i['status']}" for i in rg["items"]]
    ac = plan["asset_checklist"]
    out += ["", "## 5. Asset checklist", "", *_ins(ac)]
    for i in ac["items"]:
        out.append(f"- [ ] {title(i['pattern'])}: {describe(i['pattern'])[1]}")
        if not i["examples"]:
            out.append("  - no example in the report's fact sheets")
        for e in i["examples"]:
            out.append(f"  - {e['case']}: “{_c(e['excerpt'])}” [{', '.join(e['evidence_ids'])}]")
    cal = plan["calendar"]
    out += [
        "",
        "## 6. Sequenced plan",
        "",
        *_ins(cal),
        f"Launch day: {cal['launch_day']}. Median spacing between the winners' launch events: "
        f"{cal['median_spacing_days'] if cal['median_spacing_days'] is not None else 'unknown'}"
        " days. Channels scheduled: "
        f"{', '.join(cal['channels_scheduled']) or 'none'}; not scheduled (fewer than "
        f"{MIN_CASES} winners): {', '.join(cal['channels_insufficient']) or 'none'}; avoided: "
        f"{', '.join(cal['channels_avoided']) or 'none'}. Used by winners but not scheduled "
        "(not a recommended pattern; losers count): "
        + (
            ", ".join(
                f"{x['where']} ({x['winners']} winners, {x['losers']} matched losers)"
                for x in cal["channels_reference_only"]
            )
            or "none"
        )
        + ".",
        "",
    ]
    if cal["table_stakes"]:
        out += [
            "Table stakes (common practice among winners and losers): "
            + "; ".join(f"{x['where']} — {x['note']}" for x in cal["table_stakes"]),
            "",
        ]
    if cal["time_budget_hours_per_week"] is not None:
        out += [
            f"Time budget: {cal['time_budget_hours_per_week']} h/week "
            f"({cal['pre_launch_hours']} h before launch).",
            "",
        ]
    out += ["| when | phase | action | timing window | basis |", "|---|---|---|---|---|"]
    for e in cal["entries"]:
        out.append(
            f"| {_c(e['when'])} | {_c(e['phase'])} | {_c(e['action'])} | "
            f"{_c(e.get('timing_window', '-'))} | {_c(e['basis'])} |"
        )
    pr = plan["predictions"]
    out += ["", "## 7. Predictions", "", *_ins(pr),
            f"Outcome: {pr['outcome_label']}. {pr['lock']}.", ""]  # fmt: skip
    for i in pr["items"]:
        out.append(
            f"- **{i['id']}** {i['metric']} ({i['window']}): expected band "
            f"{i['expected_band']['low']}–{i['expected_band']['high']} (matched losers' median to "
            f"winners' median), observed range {i['observed_range']['min']}–"
            f"{i['observed_range']['max']}; winners n = {i['winners']['n']}, losers n = "
            f"{i['matched_losers']['n']}. Claim: {i['claim']}; p = "
            f"{i['probability'] if i['probability'] is not None else 'unknown'} "
            f"({i['probability_basis']})."
        )
    an = plan["adaptation_notes"]
    out += ["", "## 8. Adaptation notes", "", *_ins(an)]
    for n in an["notes"]:
        out.append(
            f"- {title(n['pattern'])} (view {n['view']}): test — {n['how_to_test']}; falsified if "
            f"{n['falsified_if']}."
        )
    fp = plan["fast_path_verdict"]
    out += ["", "## Fast-path verdict (ADR-088.6)", ""]
    out += [fp["text"].rstrip("\n")] if fp["text"] is not None else ["_Not provided._"]
    out += ["", "## Limitations", "", *[f"- {x}" for x in plan["limitations"]], ""]
    return "\n".join(out)


# --- files and the lock ---------------------------------------------------------------------
def plan_json(plan: Mapping[str, Any]) -> str:
    return json.dumps(plan, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n"


def latest_report(data_dir: Path, brief_id: str, version: int) -> Path | None:
    from pigtail.forensics.report import report_dir

    d = report_dir(data_dir, brief_id, version)
    found = sorted(d.glob("report-*.json")) if d.is_dir() else []
    return found[-1] if found else None


def write_plan(
    data_dir: Path, brief_id: str, version: int, plan: Mapping[str, Any], *, day: date
) -> dict[str, str]:
    """`plan-<day>-<version12>.{json,md}` beside the report (0600, private)."""
    from pigtail.forensics.report import _write, report_dir

    d = report_dir(data_dir, brief_id, version)
    stem = f"plan-{day.isoformat()}-{plan['provenance']['plan_version'][:12]}"
    j, m = d / f"{stem}.json", d / f"{stem}.md"
    _write(j, plan_json(plan))
    _write(m, plan_markdown(plan))
    return {"json": str(j), "md": str(m)}


def latest_plan(data_dir: Path, brief_id: str, version: int) -> Path | None:
    from pigtail.forensics.report import report_dir

    d = report_dir(data_dir, brief_id, version)
    if not d.is_dir():
        return None
    found = sorted(d.glob("plan-2*.json"), key=lambda p: (p.stat().st_mtime, p.name))
    return found[-1] if found else None


@dataclass
class LockResult:
    status: str  # locked | already_locked | refused
    message: str
    path: str | None = None
    predictions_sha256: str | None = None
    locked_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def lock_predictions(plan_path: Path, *, now: datetime) -> LockResult:
    """PRD F11 R11.2: pre-register the plan's predictions: their SHA-256 and the time, in a
    write-once `plan-lock-<version12>.json` beside the plan (private; no git write). A second
    lock of the same plan returns the first one."""
    from pigtail.forensics.report import _write

    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    preds = plan.get("predictions") or {}
    if not preds.get("items"):
        return LockResult("refused", f"nothing to lock: predictions: {INSUFFICIENT}")
    ver = plan["provenance"]["plan_version"]
    digest = sha256_text(canonical(preds["items"]))
    path = Path(plan_path).parent / f"plan-lock-{ver[:12]}.json"
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        if old.get("predictions_sha256") != digest:
            return LockResult("refused", "a different lock exists for this plan version", str(path))
        return LockResult(
            "already_locked", "already locked", str(path), digest, old.get("locked_at")
        )
    at = now.astimezone(UTC).isoformat()
    rec = {
        "plan_version": ver,
        "brief_id": plan["provenance"]["brief_id"],
        "brief_version": plan["provenance"]["brief_version"],
        "brief_hash": plan["provenance"]["brief_hash"],
        "report_hash": plan["provenance"]["report_hash"],
        "predictions": preds["items"],
        "predictions_sha256": digest,
        "locked_at": at,
        "scoring": "Brier score and calibration after launch (R11.2; launch mode)",
    }
    _write(path, json.dumps(rec, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    return LockResult("locked", "predictions locked", str(path), digest, at)


# --- running --------------------------------------------------------------------------------
@dataclass
class PlanOutcome:
    status: str  # written | refused
    exit_code: int
    message: str
    paths: dict[str, str] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


SECTIONS = (
    "similar_projects", "recommended_patterns", "trending_opportunities", "readiness_gaps",
    "asset_checklist", "calendar", "predictions", "adaptation_notes",
)  # fmt: skip


def run_plan(
    brief: Any,
    data_dir: Path,
    *,
    report_path: Path | None = None,
    inputs: PlanInputs | None = None,
    append_path: Path | None = None,
    conn: Any = None,
    day: date,
) -> PlanOutcome:
    """Read the report (default: the brief version's latest), build and write the plan."""
    assert brief.version is not None
    path = report_path or latest_report(data_dir, brief.brief_id, brief.version)
    if path is None or not Path(path).is_file():
        return PlanOutcome(
            "refused", 1, "no report of this brief version: run `pigtail report brief` first"
        )
    raw = Path(path).read_bytes()
    report = json.loads(raw)
    p = report.get("provenance") or {}
    if p.get("brief_id") != brief.brief_id or p.get("brief_version") != brief.version:
        return PlanOutcome("refused", 1, "the report belongs to another brief or version")
    append = None if append_path is None else Path(append_path).read_text(encoding="utf-8")
    ctx = load_context(conn, report) if conn is not None else PlanContext()
    try:
        plan = build_plan(
            brief,
            report,
            report_hash=hashlib.sha256(raw).hexdigest(),
            inputs=inputs,
            ctx=ctx,
            append_text=append,
        )
    except ValueError as e:
        return PlanOutcome("refused", 1, str(e))
    paths = write_plan(data_dir, brief.brief_id, brief.version, plan, day=day)
    return PlanOutcome(
        "written",
        0,
        "plan written",
        paths,
        {
            "plan_version": plan["provenance"]["plan_version"],
            "recommended": len(plan["recommended_patterns"]["patterns"]),
            "predictions": len(plan["predictions"]["items"]),
            "insufficient_sections": [s for s in SECTIONS if plan[s]["status"] == INSUFFICIENT],
            "inputs_missing": plan["provenance"]["inputs_missing"],
            "context_loaded": plan["provenance"]["context_loaded"],
        },
    )

"""The pattern step of a brief's report (M24, `patterns-v1`; ADR-089; PRD §5.2, F20; DELIVERABLES
D2; ADR-050.3, ADR-057, ADR-084.3 / BACKLOG M24-L). **Deterministic code**: an LLM may phrase the
report, never compute any of this.

**Features.** Per case, each feature is `present`, `absent` or `unknown`:

- coded (the final value of the double coding, `pilot-frame-v1`): `module_active.<m>` (yes /
  no), `novelty_claim`, `pattern.<MC-xx>` (MC-12 is derived from the star-anomaly flag);
- report facts (`report-facts-v1`, deterministic): `asset.<a>` at launch, the README's
  quick-start and features sections, `launch.<kind>` (a launch event of that kind: Show HN,
  Launch HN, Product Hunt, a declared maintainer's Bluesky post, a launch-worded release;
  `absent` only where the source was searched completely: the HN lookup always runs, Product
  Hunt, Bluesky and releases by their stored status), `amplifier.organization`.

Star trajectories and bursts are **not** features: they are the outcome itself (circular).

**Per view** (A follow-through, B launch, B-undeclared; the view's headline pairs: its winners
and their matched losers flagged headline): n present and n known among winners and among
matched losers, the shares and the contrast d = share among winners - share among losers, the
pair counts (pairs where only the winner has it, only the loser, both, neither), and the named
counterexamples (winners without it, losers with it). **Insufficient evidence in this
neighbourhood** (ADR-050.3) below 10 known cases per side or 3 present in total; d = 0 is no
pattern.

**Language check** (ADR-084.3, M24-L): d on the view's same-language-group headline pairs
(d_same) against d on all of them (d_all): holds (same sign and |d_same| >= 0.5 |d_all|),
weakens (same sign and smaller, or d_same = 0), reverses (opposite sign: "language-dependent"),
not assessable below the minimum evidence.

**Reliability.** Coded features carry their field's alpha from the coding run (`low reliability`
below 0.70, and the run's labels); report facts are `deterministic rule (no alpha)`.

**Distribution examples** (exemplars against their matched losers; ADR-057): the same counts, and
a transferability label per feature (`transfer-v1`): a feature that is a condition of the project
(a module, the novelty claim, an organisation owner) is `not transferable`; a practice (assets,
launch venues, README sections, the MC patterns) is `conditional` on the modules active in at
least half of the exemplars that show it, else `transferable`.

**Absolute numbers** per view and class: n, median, minimum and maximum of every numeric outcome
value the selection stored (stars on the view's windows, HN points, ...), labelled
"unfiltered, anomaly-checked" for stars; download values, if a connector added any, appear as a
secondary, exploratory outcome (never pre-registered, never used by the sort).
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pigtail.forensics.facts import ASSETS
from pigtail.forensics.frame import CODED_PATTERNS, DERIVED_PATTERNS, MODULES
from pigtail.forensics.store import PilotCase

PATTERNS_VERSION = "patterns-v1"
TRANSFER_VERSION = "transfer-v1"
MIN_KNOWN_PER_SIDE = 10  # ADR-050.3
MIN_PRESENT_TOTAL = 3
LOW_ALPHA = 0.70
VIEWS = {"follow_through": "A", "launch": "B", "launch_undeclared": "B-undeclared"}
INSUFFICIENT = "insufficient evidence in this neighbourhood"
DETERMINISTIC = "deterministic rule (no alpha)"
LAUNCH_FEATURES = (
    "show_hn",
    "launch_hn",
    "product_hunt",
    "bluesky_maintainer_post",
    "release_launch",
)
# sources whose absence of an event counts as `absent` only when searched completely
_SOURCE_OF = {
    "product_hunt": "ph_launch",
    "bluesky_maintainer_post": "bsky_maintainer_posts",
    "release_launch": "gh_releases",
}
CONDITION_FEATURES = ("novelty_claim", "amplifier.organization")


def feature_names() -> list[str]:
    return [
        *(f"module_active.{m}" for m in MODULES),
        "novelty_claim",
        *(f"pattern.{p}" for p in (*CODED_PATTERNS, *DERIVED_PATTERNS)),
        *(f"asset.{a}" for a in ASSETS),
        "readme.quick_start_section",
        "readme.features_section",
        *(f"launch.{k}" for k in LAUNCH_FEATURES),
        "amplifier.organization",
    ]


def is_coded(feature: str) -> bool:
    return feature.startswith(("module_active.", "pattern.")) or feature == "novelty_claim"


def _tri(v: str | None) -> str:
    return {"yes": "present", "present": "present", "no": "absent", "absent": "absent"}.get(
        v or "", "unknown"
    )


def case_features(final: Mapping[str, str], facts: Mapping[str, Any] | None) -> dict[str, str]:
    """feature -> present | absent | unknown for one case (module docstring)."""
    out: dict[str, str] = {}
    for f in feature_names():
        if is_coded(f):
            out[f] = _tri(final.get(f))
    facts = facts or {}
    assets = ((facts.get("assets") or {}).get("assets")) or {}
    for a in ASSETS:
        out[f"asset.{a}"] = _tri((assets.get(a) or {}).get("value"))
    rs = (facts.get("assets") or {}).get("readme_structure")
    for k in ("quick_start_section", "features_section"):
        out[f"readme.{k}"] = "unknown" if rs is None else ("present" if rs.get(k) else "absent")
    kinds = {e.get("kind") for e in facts.get("events") or [] if e.get("launch")}
    status = facts.get("source_status") or {}
    for k in LAUNCH_FEATURES:
        if k in kinds:
            out[f"launch.{k}"] = "present"
        elif k in ("show_hn", "launch_hn"):
            out[f"launch.{k}"] = "absent"  # the HN launch lookup runs for every repo (ADR-082)
        else:
            src = status.get(_SOURCE_OF[k])
            out[f"launch.{k}"] = "absent" if src == "complete" else "unknown"
    amps: list[dict[str, Any]] = list(facts.get("amplifiers") or [])
    org: dict[str, Any] = next((a for a in amps if a.get("role") == "organization"), {})
    out["amplifier.organization"] = _tri(org.get("value"))
    return out


@dataclass(frozen=True)
class Side:
    present: list[str]
    absent: list[str]
    unknown: list[str]

    @property
    def known(self) -> int:
        return len(self.present) + len(self.absent)

    @property
    def share(self) -> float | None:
        return None if self.known == 0 else len(self.present) / self.known

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_present": len(self.present),
            "n_known": self.known,
            "n_unknown": len(self.unknown),
            "share": None if self.share is None else round(self.share, 4),
        }


def _side(feature: str, cases: Sequence[str], feats: Mapping[str, Mapping[str, str]]) -> Side:
    p: list[str] = []
    a: list[str] = []
    u: list[str] = []
    for k in cases:
        v = feats.get(k, {}).get(feature, "unknown")
        (p if v == "present" else a if v == "absent" else u).append(k)
    return Side(sorted(p), sorted(a), sorted(u))


def contrast(w: Side, lo: Side) -> float | None:
    if w.share is None or lo.share is None:
        return None
    return round(w.share - lo.share, 4)


def sufficient(w: Side, lo: Side) -> bool:
    return (
        w.known >= MIN_KNOWN_PER_SIDE
        and lo.known >= MIN_KNOWN_PER_SIDE
        and len(w.present) + len(lo.present) >= MIN_PRESENT_TOTAL
    )


def language_rule(d_all: float | None, d_same: float | None, same_ok: bool) -> str:
    """ADR-084.3 (`language_groups.pattern_rule`)."""
    if d_all is None or d_all == 0:
        return "no pattern"
    if not same_ok or d_same is None:
        return "not assessable"
    if d_same == 0:
        return "weakens"
    if (d_same > 0) != (d_all > 0):
        return "reverses"
    return "holds" if abs(d_same) >= 0.5 * abs(d_all) else "weakens"


def _pairs_count(
    feature: str, pairs: Sequence[tuple[str, str]], feats: Mapping[str, Mapping[str, str]]
) -> dict[str, int]:
    out = {"winner_only": 0, "loser_only": 0, "both": 0, "neither": 0, "not_known": 0}
    for w, lo in pairs:
        a = feats.get(w, {}).get(feature, "unknown")
        b = feats.get(lo, {}).get(feature, "unknown")
        if "unknown" in (a, b):
            out["not_known"] += 1
        elif a == b == "present":
            out["both"] += 1
        elif a == b == "absent":
            out["neither"] += 1
        elif a == "present":
            out["winner_only"] += 1
        else:
            out["loser_only"] += 1
    return out


def alpha_of(feature: str, rel: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not is_coded(feature):
        return {"alpha": None, "labels": [DETERMINISTIC]}
    for r in rel:
        if r["field"] == feature and r["statistic"] == "nominal":
            labels = list(r.get("labels") or [])
            low = r["alpha"] is not None and r["alpha"] < LOW_ALPHA
            if low and "low reliability" not in labels:
                labels.append("low reliability")
            return {"alpha": r["alpha"], "labels": labels, "assessed": bool(r["assessed"])}
    return {"alpha": None, "labels": ["reliability not assessed"]}


@dataclass(frozen=True)
class ViewCases:
    view: str
    winners: list[str]  # case keys
    losers: list[str]
    pairs: list[tuple[str, str]]  # (winner key, loser key)
    same_group_pairs: list[tuple[str, str]]


def view_cases(
    cases: Sequence[PilotCase],
    sel_rows: Mapping[tuple[str, str], Mapping[str, Any]],
    view: str,
    *,
    panel: str = "field",
) -> ViewCases:
    """A view's headline pairs (field panel) or its exemplar pairs (`panel` exemplar)."""
    w_role = "winner" if panel == "field" else "exemplar"
    l_role = "matched_loser" if panel == "field" else "exemplar_matched_loser"
    by_pair: dict[int, dict[str, list[PilotCase]]] = {}
    for c in cases:
        if c.view != view or c.pair_id is None:
            continue
        row = sel_rows.get((c.view, c.candidate_ref)) or {}
        if row.get("pair_panel", panel) != panel:
            continue
        slot = by_pair.setdefault(int(c.pair_id), {"w": [], "l": []})
        if c.role == w_role:
            slot["w"].append(c)
        elif c.role == l_role and (panel != "field" or row.get("headline")):
            slot["l"].append(c)
    winners, losers, pairs, same = [], [], [], []
    for _pid, slot in sorted(by_pair.items()):
        if not slot["w"] or not slot["l"]:
            continue
        w = slot["w"][0]
        winners.append(w.case_key)
        for lo in slot["l"]:
            losers.append(lo.case_key)
            pairs.append((w.case_key, lo.case_key))
            pair = ((sel_rows.get((lo.view, lo.candidate_ref)) or {}).get("detail") or {}).get(
                "pair"
            ) or {}
            if pair.get("same_language_group") is True:
                same.append((w.case_key, lo.case_key))
    return ViewCases(view, sorted(set(winners)), sorted(set(losers)), pairs, same)


def feature_row(
    feature: str,
    vc: ViewCases,
    feats: Mapping[str, Mapping[str, str]],
    rel: Sequence[Mapping[str, Any]],
    names: Mapping[str, str],
    *,
    language: bool = True,
) -> dict[str, Any]:
    w = _side(feature, vc.winners, feats)
    lo = _side(feature, vc.losers, feats)
    d = contrast(w, lo)
    ok = sufficient(w, lo)
    labels: list[str] = []
    if not ok:
        labels.append(INSUFFICIENT)
    elif d == 0:
        labels.append("no pattern (d = 0)")
    row: dict[str, Any] = {
        "feature": feature,
        "winners": w.to_dict(),
        "matched_losers": lo.to_dict(),
        "d": d,
        "pairs": _pairs_count(feature, vc.pairs, feats),
        "counterexamples": {
            "winners_without": [names.get(k, k) for k in w.absent],
            "losers_with": [names.get(k, k) for k in lo.present],
            "none_found": not w.absent and not lo.present,
        },
        "supporting": {
            "winners_with": [names.get(k, k) for k in w.present],
            "losers_without": [names.get(k, k) for k in lo.absent],
        },
        "reliability": alpha_of(feature, rel),
        "sufficient": ok,
        "labels": labels,
    }
    if language:
        sw = sorted({a for a, _b in vc.same_group_pairs})
        sl = sorted({b for _a, b in vc.same_group_pairs})
        ws, ls = _side(feature, sw, feats), _side(feature, sl, feats)
        d_same = contrast(ws, ls)
        verdict = language_rule(d if ok else None, d_same, sufficient(ws, ls))
        row["language_check"] = {
            "pairs_same_group": len(vc.same_group_pairs),
            "d_all": d,
            "d_same": d_same,
            "result": verdict,
            **({"label": "language-dependent"} if verdict == "reverses" else {}),
        }
        if verdict == "reverses":
            labels.append("language-dependent")
    return row


def transfer_label(
    feature: str, ex: ViewCases, feats: Mapping[str, Mapping[str, str]]
) -> dict[str, Any]:
    """`transfer-v1` (module docstring)."""
    if feature.startswith("module_active.") or feature in CONDITION_FEATURES:
        return {"label": "not transferable", "why": "a condition of the project, not a practice"}
    showing = [k for k in ex.winners if feats.get(k, {}).get(feature) == "present"]
    if not showing:
        return {"label": "not assessable", "why": "no exemplar shows it"}
    conds = []
    for m in MODULES:
        n = sum(1 for k in showing if feats.get(k, {}).get(f"module_active.{m}") == "present")
        if n * 2 >= len(showing):
            conds.append(f"{m} ({n}/{len(showing)})")
    if conds:
        return {"label": "conditional", "conditions": conds}
    return {"label": "transferable", "conditions": []}


def numeric_summary(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "median": None, "min": None, "max": None}
    return {
        "n": len(values),
        "median": round(statistics.median(values), 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
    }


def absolute_numbers(
    keys: Sequence[str], sel_rows_by_key: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """Every numeric stored outcome value of these cases: n, median, min, max per metric."""
    return _numbers(
        keys,
        {
            k: ((r or {}).get("detail") or {}).get("values") or {}
            for k, r in sel_rows_by_key.items()
        },
    )


def _numbers(keys: Sequence[str], values: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    per: dict[str, list[float]] = {}
    for k in keys:
        for metric, rec in (values.get(k) or {}).items():
            v = rec.get("value") if isinstance(rec, dict) else None
            if isinstance(v, int | float) and not isinstance(v, bool):
                per.setdefault(metric, []).append(float(v))
    return {m: numeric_summary(v) for m, v in sorted(per.items())}


def is_download_metric(metric: str) -> bool:
    m = metric.lower()
    return m.startswith("adopt") or "download" in m


def run_patterns(
    cases: Sequence[PilotCase],
    finals: Mapping[str, Mapping[str, str]],
    sel_rows: Mapping[tuple[str, str], Mapping[str, Any]],
    rel: Sequence[Mapping[str, Any]],
    secondary: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    """The whole pattern step (module docstring). `finals`: case key -> unit -> final value;
    `sel_rows`: (view, candidate ref) -> the stored selection row; `secondary`: candidate ref
    -> metric -> record of the exploratory download outcomes (ADR-090, view A only), shown as
    absolute numbers, never used for a pattern."""
    sec_by_key = {
        c.case_key: dict((secondary or {}).get(c.candidate_ref) or {})
        for c in cases
        if c.view == "follow_through"
    }
    feats = {c.case_key: case_features(finals.get(c.case_key, {}), c.facts) for c in cases}
    names = {c.case_key: c.repo_full_name for c in cases}
    by_key = {c.case_key: sel_rows.get((c.view, c.candidate_ref)) or {} for c in cases}
    views_out: dict[str, Any] = {}
    downloads = False
    for view, label in VIEWS.items():
        vc = view_cases(cases, sel_rows, view)
        if not vc.winners:
            continue
        rows = [feature_row(f, vc, feats, rel, names) for f in feature_names()]
        abs_w = absolute_numbers(vc.winners, by_key)
        abs_l = absolute_numbers(vc.losers, by_key)
        downloads = downloads or any(is_download_metric(m) for m in (*abs_w, *abs_l))
        sec = {}
        if view == "follow_through" and any(sec_by_key.values()):
            sec = {
                "winners": _numbers(vc.winners, sec_by_key),
                "matched_losers": _numbers(vc.losers, sec_by_key),
            }
            downloads = downloads or any(v["n"] for side in sec.values() for v in side.values())
        views_out[label] = {
            "view": view,
            "outcome_dimension": "attention",
            "winners": len(vc.winners),
            "matched_losers": len(vc.losers),
            "pairs": len(vc.pairs),
            "same_language_group_pairs": len(vc.same_group_pairs),
            "features": rows,
            "absolute_numbers": {"winners": abs_w, "matched_losers": abs_l},
            "secondary_exploratory": sec,
        }
    ex_out: dict[str, Any] = {}
    for view, label in VIEWS.items():
        ex = view_cases(cases, sel_rows, view, panel="exemplar")
        if not ex.winners:
            continue
        rows = []
        for f in feature_names():
            r = feature_row(f, ex, feats, rel, names, language=False)
            r["transferability"] = transfer_label(f, ex, feats)
            rows.append(r)
        ex_out[label] = {
            "view": view,
            "exemplars": len(ex.winners),
            "matched_losers": len(ex.losers),
            "features": rows,
            "absolute_numbers": {
                "exemplars": absolute_numbers(ex.winners, by_key),
                "matched_losers": absolute_numbers(ex.losers, by_key),
            },
        }
    return {
        "version": PATTERNS_VERSION,
        "transfer_version": TRANSFER_VERSION,
        "min_evidence": {"known_per_side": MIN_KNOWN_PER_SIDE, "present_total": MIN_PRESENT_TOTAL},
        "outcome_label": "attention and downloads (downloads exploratory)"
        if downloads
        else "attention-based",
        "downloads_present": downloads,
        "views": views_out,
        "distribution_examples": ex_out,
        "case_features": feats,
    }

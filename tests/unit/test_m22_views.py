"""Owner decision 2026-09-27 (ADR-083), pure parts: view A's follow-through metric (OLS residual
and log-ratio, degenerate cases), view A pairing on launch size where plain `att.stars@30` got
no pair, view B's population, pre-launch matching and balance, sensitivity per view, the
context view C, stars before launch, the E confirmation rules 1-3 and the Haiku check (fake
backends: true, false, unsure, fail closed without approval, budget stop, cache), the guard
hash inside the pre-registered parameters, recompute determinism and the estimate. Every repo
and title is synthetic (`org-…`).
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from pigtail.briefs import confirm as conf
from pigtail.briefs import outcomes as out
from pigtail.briefs import selection as selmod
from pigtail.briefs.budget import BudgetGuard
from pigtail.briefs.confirm import (
    JOB,
    PROMPT,
    Check,
    Confirmer,
    confirm_by_rules,
    confirmation_params,
    haiku_input,
    keywords,
    owner_named,
    url_domain,
)
from pigtail.briefs.estimate import SelectionState, estimate, run_scope
from pigtail.briefs.model import Budget, sha256_json
from pigtail.briefs.outcomes import (
    anchor_rule_source_sha256,
    creation_pages,
    star_window,
    stars_before_launch,
)
from pigtail.briefs.selection import (
    ANCHOR_RULE_SOURCE_SHA256,
    CONTEXT_LABEL,
    FOLLOW_STARS,
    FT_LOGRATIO,
    FT_RESID,
    LAUNCH_SIZE,
    LSM_CALIPER_SD,
    MIN_POPULATION,
    VIEW_FOLLOW_THROUGH,
    VIEW_LAUNCH,
    Anchor,
    CaseInput,
    Context,
    Covariates,
    Definition,
    Value,
    _check,
    follow_through_fit,
    select,
    select_views,
)
from pigtail.config import DEFAULT_STAGE_MODELS
from pigtail.llm import BatchPending, LLMClient
from pigtail.llm.api import BatchItemResult, BatchStatus, build_params
from pigtail.llm.batch import MemoryBatchStore
from pigtail.llm.redact import alias_redact
from pigtail.llm.store import LLMStore
from pigtail.llm.types import BackendResponse
from tests.selection_fake import H2_2025, brief, obs

B = brief(minimums={}, primary_threshold="top_quartile")  # attention primary, top quartile


# --- fixtures ----------------------------------------------------------------------------------
def vcase(
    i: int,
    *,
    launch: float,
    follow: float,
    anchor: str = "launch",
    prelaunch: int | None = 40,
    age: float = 2.0,
    lang: str = "go",
    distance: int = 0,
    flag: str = "false",
) -> CaseInput:
    at = H2_2025 + timedelta(days=i % 50)
    values = {
        LAUNCH_SIZE: obs(launch),
        FOLLOW_STARS: obs(follow),
        "att.stars@30": obs(launch + follow),
        "att.hn_points": obs(10 + i) if anchor == "launch" else Value("unknown", reason="x"),
    }
    return CaseInput(
        ref=f"gh:org-v/repo-{i:02d}",
        distance=distance,
        anchor=Anchor(
            anchor,
            at,
            "hour" if anchor == "launch" else "day",  # type: ignore[arg-type]
            "show_hn" if anchor == "launch" else "velocity-v0",
        ),
        values=values,
        covariates=Covariates(
            lsm=math.log10(1 + max(0.0, launch)),
            launch_quarter=at.year * 4 + (at.month - 1) // 3,
            launch_half_year=f"{at.year}H{1 if at.month <= 6 else 2}",
            age_log10=age,
            audience_band="unknown",
            language=lang,
            launch_type="show_hn" if anchor == "launch" else "burst",
            prelaunch_stars=prelaunch,
            prelaunch_log=None if prelaunch is None else math.log10(1 + prelaunch),
        ),
        star_anomaly_flag=flag,  # type: ignore[arg-type]
    )


def split_field(n_bursts: int = 4) -> list[CaseInput]:
    """Launch size decides `att.stars@30`: 10 very large launches, 30 small ones (a gap wider
    than the LSM caliper), follow-through ratios independent of launch size, and a few
    burst-anchored small cases."""
    out_: list[CaseInput] = []
    for i in range(40):
        big = i < 10
        launch = 3000.0 + 100 * i if big else 10.0 + 3 * i
        r = 0.05 + ((i * 37) % 40) / 40 * 0.9  # not related to launch size
        out_.append(vcase(i, launch=launch, follow=round(launch * r), prelaunch=40 + i % 5))
    for j in range(n_bursts):
        i = 40 + j
        out_.append(vcase(i, launch=20.0 + j, follow=15.0, anchor="burst"))
    return out_


def ctx(view: Any = None, b: Any = B) -> Context:
    c = Context.from_brief(b)
    return c if view is None else replace(c, view=view)


# --- 1. view A's metric --------------------------------------------------------------------------
def fit_cases(xs: list[float], ys: list[float]) -> list[CaseInput]:
    return [vcase(i, launch=x, follow=y) for i, (x, y) in enumerate(zip(xs, ys, strict=True))]


def test_residual_is_closed_form_ols_on_log1p_and_the_log_ratio_is_y_minus_x():
    rnd = random.Random(3)
    xs = [float(rnd.randint(0, 400)) for _ in range(MIN_POPULATION)]
    ys = [float(rnd.randint(0, 900)) for _ in range(MIN_POPULATION)]
    derived, fit = follow_through_fit(fit_cases(xs, ys))
    lx = [math.log1p(x) for x in xs]
    ly = [math.log1p(y) for y in ys]
    mx, my = sum(lx) / len(lx), sum(ly) / len(ly)
    b = sum((a - mx) * (c - my) for a, c in zip(lx, ly, strict=True)) / sum(
        (a - mx) ** 2 for a in lx
    )
    a = my - b * mx
    assert fit["status"] == "ok" and fit["n"] == MIN_POPULATION
    assert fit["slope"] == pytest.approx(b, abs=1e-6)
    assert fit["intercept"] == pytest.approx(a, abs=1e-6)
    for i, (x, y) in enumerate(zip(lx, ly, strict=True)):
        d = derived[f"gh:org-v/repo-{i:02d}"]
        assert d[FT_RESID].status == "observed"
        assert d[FT_RESID].value == pytest.approx(y - (a + b * x), abs=1e-8)
        assert d[FT_LOGRATIO].value == pytest.approx(y - x, abs=1e-8)
        assert d[FT_RESID].floor_basis == ys[i]  # the zero floor checks stars on days 3..29
    assert sum(v[FT_RESID].value or 0 for v in derived.values()) == pytest.approx(0, abs=1e-6)


def test_degenerate_fits_give_no_residuals_and_no_percentiles():
    # fewer than MIN_POPULATION cases with both values observed: no fit
    small = fit_cases([float(i) for i in range(19)], [float(2 * i) for i in range(19)])
    d, fit = follow_through_fit(small)
    assert fit["status"] == "small_population" and fit["slope"] is None
    assert {v[FT_RESID].status for v in d.values()} == {"unknown"}
    assert {v[FT_RESID].reason for v in d.values()} == {"no_fit:small_population"}
    assert {v[FT_LOGRATIO].status for v in d.values()} == {"observed"}  # needs no fit
    # zero variance of launch size: no fit either
    flat = fit_cases([7.0] * 25, [float(i) for i in range(25)])
    d, fit = follow_through_fit(flat)
    assert fit["status"] == "zero_variance" and fit["n"] == 25
    assert {v[FT_RESID].reason for v in d.values()} == {"no_fit:zero_variance"}
    ev = selmod.evaluate(VIEW_FOLLOW_THROUGH.definition(Definition.from_brief(B)), flat)
    assert not any(p is not None for p in ev.pct[FT_RESID].values())  # no percentile at all
    assert ev.populations[FT_RESID]["fit"]["status"] == "zero_variance"
    assert ev.populations[FT_RESID]["n_by_group"] == {}
    assert all(q.outcome == "undetermined" for q in ev.qual.values())


def test_pending_and_unknown_inputs_and_negative_counts():
    cs = fit_cases([float(i) for i in range(22)], [float(3 * i) for i in range(22)])
    cs[0] = replace(cs[0], values={**cs[0].values, LAUNCH_SIZE: Value("pending", reason="h")})
    cs[1] = replace(cs[1], values={**cs[1].values, FOLLOW_STARS: Value("unknown", reason="gap")})
    cs[2] = replace(cs[2], values={**cs[2].values, LAUNCH_SIZE: obs(-3)})  # net unstars
    d, fit = follow_through_fit(cs)
    assert fit["n"] == 20
    assert d[cs[0].ref][FT_RESID].status == "pending"
    assert d[cs[1].ref][FT_LOGRATIO].status == "unknown"
    assert d[cs[1].ref][FT_LOGRATIO].reason == "unknown:gap"
    assert d[cs[2].ref][FT_LOGRATIO].value == pytest.approx(math.log1p(6))  # ln(1+max(0,-3))=0


def test_zero_floor_checks_the_follow_through_count_not_the_residual():
    d = VIEW_FOLLOW_THROUGH.definition(Definition.from_brief(B))
    c = vcase(0, launch=10, follow=0)
    c = replace(c, values={**c.values, FT_RESID: Value("observed", 1.5, floor_basis=0.0)})
    rec = _check(c, "attention", 75.0, d, {FT_RESID: {c.ref: 99.0}})
    assert rec["result"] == "fail" and rec["reason"] == "zero_floor"
    ok = replace(c, values={**c.values, FT_RESID: Value("observed", -0.2, floor_basis=4.0)})
    assert _check(ok, "attention", 75.0, d, {FT_RESID: {c.ref: 80.0}})["result"] == "pass"


def test_star_windows_use_endpoint_day_indices_and_the_horizon_rule():
    first = date(2025, 10, 1)
    series = {first + timedelta(days=i): i + 1 for i in range(40)}
    assert star_window(series, first, 0, 2, date(2026, 1, 1)).value == 1 + 2  # days 0, 1
    assert star_window(series, first, 3, 30, date(2026, 1, 1)).value == sum(range(4, 31))
    # pending until first + 30 + settle lag (3 days), like att.stars@30
    assert star_window(series, first, 3, 30, first + timedelta(days=32)).status == "pending"
    assert star_window(series, first, 3, 30, first + timedelta(days=33)).status == "observed"
    gap = {d: n for d, n in series.items() if d != first + timedelta(days=2)}
    assert star_window(gap, first, 3, 30, date(2026, 1, 1)).status == "observed"  # day 2 unused
    assert star_window(gap, first, 0, 3, date(2026, 1, 1)).reason == "incomplete_series"


# --- 2. view A pairs where plain stars@30 got none; view B ----------------------------------------
def test_view_a_pairs_on_launch_size_where_plain_stars_at_30_got_none():
    cases = split_field(0)
    base = Definition.from_brief(B)
    plain = select(cases, ctx(), base)
    assert plain.summary["counts"]["winners"] == 10
    assert plain.summary["counts"]["matched_losers"] == 0  # the verifier's round-5 finding
    a = select(cases, ctx(VIEW_FOLLOW_THROUGH), base)
    assert a.summary["view"] == "follow_through"
    assert a.summary["final_definition"]["metrics"]["attention"] == FT_RESID
    assert a.summary["counts"]["winners"] >= 10 and a.summary["counts"]["matched_losers"] >= 5
    sd = a.level.sds["lsm"]
    by = {c.ref: c for c in cases}
    for p in a.level.pairs:  # the LSM caliper still holds (ADR-054.1, ADR-078 unchanged)
        w, lo = by[p.winner], by[p.loser]
        assert abs((w.covariates.lsm or 0) - (lo.covariates.lsm or 0)) <= LSM_CALIPER_SD * sd
        assert w.covariates.launch_half_year == lo.covariates.launch_half_year
    # balance on the plain covariates, LSM included
    assert set(a.balance["after_matching"]) == {"lsm", "age_log10", "language"}
    # burst-anchored cases are in view A's population
    with_bursts = select(split_field(), ctx(VIEW_FOLLOW_THROUGH), base)
    assert with_bursts.summary["reference_population_n"] == 44
    assert "not_in_view" not in with_bursts.summary["roles"]


def test_view_b_excludes_bursts_ignores_launch_size_and_balances_pre_launch_covariates():
    cases = split_field()
    b = select(cases, ctx(VIEW_LAUNCH), Definition.from_brief(B))
    roles = {c["candidate_ref"]: c["role"] for c in b.cases}
    bursts = [c.ref for c in cases if c.anchor and c.anchor.type == "burst"]
    assert {roles[r] for r in bursts} == {"not_in_view"} and len(bursts) == 4
    assert b.summary["reference_population_n"] == 40
    assert any("4 burst-anchored cases left out" in w for w in b.summary["warnings"])
    assert b.summary["final_definition"]["metrics"]["attention"] == LAUNCH_SIZE
    winners = {c.ref for c in b.level.winners}
    assert winners == {c.ref for c in cases[:10]}  # the largest launches
    assert b.level.pairs, "view B pairs on pre-launch characteristics"
    sds = b.level.sds
    by = {c.ref: c for c in cases}
    lsm_gaps = []
    for p in b.level.pairs:
        w, lo = by[p.winner], by[p.loser]
        lsm_gaps.append(abs((w.covariates.lsm or 0) - (lo.covariates.lsm or 0)))
        pw, pl = w.covariates.prelaunch_log or 0, lo.covariates.prelaunch_log or 0
        assert abs(pw - pl) <= 0.5 * (sds["prelaunch_log"] or 0) + 1e-9
        assert "lsm" not in p.diffs
    assert max(lsm_gaps) > LSM_CALIPER_SD * (sds["lsm"] or 0)  # launch size is no key in B
    bal = b.balance
    assert set(bal["after_matching"]) == {"age_log10", "prelaunch_log", "language", "category"}
    assert bal["covariates"] == ["age_log10", "prelaunch_log", "language", "category"]
    # a case with unknown stars before launch fails view B's caliper (can't be checked)
    unk = [replace(c, covariates=replace(c.covariates, prelaunch_log=None)) for c in cases]
    assert select(unk, ctx(VIEW_LAUNCH), Definition.from_brief(B)).level.pairs == []


def test_view_b_category_is_a_distance_term_and_a_headline_covariate():
    cases = split_field(0)
    cases = [replace(c, distance=1) if i % 2 else c for i, c in enumerate(cases)]
    b = select(cases, ctx(VIEW_LAUNCH), Definition.from_brief(B))
    for p in b.level.pairs:
        assert "category" in p.diffs
        assert ("category" in p.excluded_on) == (p.diffs["category"] == 1.0)


def test_sensitivity_per_view():
    cases = split_field()
    sels = select_views(cases, ctx(), Definition.from_brief(B))
    a = {x["key"]: x for x in sels.views["follow_through"].sensitivity["alternatives"]}
    assert a[f"metric:attention:{FT_LOGRATIO}"]["ran"] is True
    assert a["metric:attention:att.stars@30"]["ran"] is True
    assert a["metric:attention:att.stars@30"]["jaccard"] is not None
    bsens = {x["key"] for x in sels.views["launch"].sensitivity["alternatives"]}
    assert not any(k.startswith("metric:") for k in bsens)
    assert "band_shift:attention:looser" in bsens
    # D refits view A's residual without the flagged cases (whole outcome sort recomputed)
    flagged = [replace(c, star_anomaly_flag="true") if i in (3, 17) else c
               for i, c in enumerate(cases)]  # fmt: skip
    s2 = select_views(flagged, ctx(), Definition.from_brief(B))
    d = {x["key"]: x for x in s2.views["follow_through"].sensitivity["alternatives"]}
    assert (
        d["exclude_anomaly_flagged"]["ran"]
        and d["exclude_anomaly_flagged"]["excluded_anomaly_flagged"] == 2
    )


def test_context_view_compares_winners_with_the_whole_pool_per_view():
    sels = select_views(split_field(), ctx(), Definition.from_brief(B))
    assert sels.context["label"] == CONTEXT_LABEL == "context, not a headline"
    for key, s in sels.views.items():
        cv = sels.context["views"][key]
        assert cv["label"] == CONTEXT_LABEL and cv["view"] == key
        assert cv["winners"] == len(s.level.winners)
        assert cv["loser_pool"] == len(s.level.loser_pool)
        assert cv["loser_pool_unmatched"] == len(s.level.loser_pool) - len(s.level.pairs)
        assert cv["covariates"] == s.balance["before_matching"]
        assert cv["outcomes"][LAUNCH_SIZE]["winners"]["n"] == len(s.level.winners)
    a = sels.context["views"]["follow_through"]["outcomes"]
    assert a[FT_RESID]["winners"]["median"] > a[FT_RESID]["pool"]["median"]


def test_recompute_is_deterministic_and_each_view_has_its_own_hash():
    cases = split_field()
    s1 = select_views(cases, ctx(), Definition.from_brief(B))
    shuffled = list(cases)
    random.Random(5).shuffle(shuffled)
    s2 = select_views(shuffled, ctx(), Definition.from_brief(B))
    assert s1.result_hash == s2.result_hash and s1.inputs_hash == s2.inputs_hash
    h = {k: v.result_hash for k, v in s1.views.items()}
    assert h == {k: v.result_hash for k, v in s2.views.items()}
    assert len(set(h.values())) == 2 and s1.result_hash not in h.values()
    assert s1.summary["views"]["follow_through"]["result_hash"] == h["follow_through"]


# --- 3. stars before launch ----------------------------------------------------------------------
def test_stars_before_launch_from_creation_and_unknown_when_the_series_is_short():
    created = datetime(2025, 6, 1, 18, tzinfo=UTC)  # 11:00 Pacific: endpoint day 1 June
    first = date(2025, 10, 1)
    full = {date(2025, 5, 26) + timedelta(days=i): 1 for i in range(200)}  # creation week on
    n, why = stars_before_launch(full, created, first)
    assert why is None and n == (first - date(2025, 6, 1)).days
    late = {d: v for d, v in full.items() if d >= date(2025, 8, 1)}  # older pages not fetched
    assert stars_before_launch(late, created, first) == (None, "series_does_not_reach_creation")
    gap = {d: v for d, v in full.items() if d != date(2025, 7, 4)}
    assert stars_before_launch(gap, created, first) == (None, "incomplete_series")
    assert stars_before_launch(full, None, first) == (None, "no_creation_date")
    assert stars_before_launch({}, created, first) == (None, "no_star_history")
    assert stars_before_launch(full, created, date(2025, 6, 1)) == (0, None)  # day of creation


def test_history_is_fetched_back_to_creation_for_launched_repos():
    assert creation_pages(date(2020, 1, 1), date(2026, 9, 26)) == math.ceil((352 + 2) / 30)
    assert creation_pages(date(1900, 1, 1), date(2026, 9, 26)) == 100  # the page cap as now
    from pigtail.briefs.candidates import Candidate

    t = datetime(2025, 10, 1, tzinfo=UTC)
    win = (datetime(2025, 3, 1, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC))
    launched = Candidate(ref="gh:org-a/x", repo_full_name="org-a/x", sources=[
        {"source": "show_hn", "hn_item_id": 1, "time": t.isoformat()}])  # fmt: skip
    assert out.has_declared_launch(launched, *win)
    assert not out.has_declared_launch(Candidate(ref="gh:org-b/y", repo_full_name="org-b/y"), *win)


# --- 4. E: rules 1-3 -----------------------------------------------------------------------------
def test_rule_1_homepage_domain_but_never_a_shared_host():
    assert url_domain("https://www.Acme.dev:8443/launch?x=1") == "acme.dev"
    assert url_domain("acme.dev/x") == "acme.dev" and url_domain("") is None
    kw = {"full_name": "org-a/acmeforge", "description": None, "title": "Show HN: Acmeforge"}
    assert confirm_by_rules(**kw, homepage_domain="acme.dev", url="https://www.acme.dev/x") == (
        "homepage_domain"
    )
    assert confirm_by_rules(**kw, homepage_domain="acme.dev", url="https://other.dev") is None
    assert confirm_by_rules(**kw, homepage_domain="medium.com", url="https://medium.com/p") is None
    assert confirm_by_rules(**kw, homepage_domain=None, url="https://acme.dev") is None


def test_rule_2_owner_login_as_a_whole_word_in_the_title_or_the_url():
    assert owner_named("Show HN: Tallyho – by orgtally", None, "orgtally/tallyho")
    assert owner_named("Show HN: Tallyho", "https://orgtally.dev/launch", "orgtally/tallyho")
    assert owner_named("Show HN: Tallyho", "https://example.org/orgtally/x", "orgtally/tallyho")
    assert not owner_named("Show HN: Tallyho by orgtallyx", None, "orgtally/tallyho")
    assert not owner_named("Show HN: Tallyho", "https://orgtally-labs.dev", "orgtally/tallyho")
    assert not owner_named("Show HN: Tallyho by app", None, "app/tallyho")  # < 4 characters
    assert not owner_named("Show HN: Tallyho, open", None, "open/tallyho")  # a stop-word
    assert (
        confirm_by_rules(
            full_name="orgtally/tallyho",
            description=None,
            homepage_domain=None,
            title="Show HN: Tallyho – made at OrgTally",
            url=None,
        )
        == "owner_login"
    )


def test_rule_3_two_distinctive_description_keywords():
    assert keywords("Show HN: A YC W24 tool, 2024, for Kubernetes clusters") == {
        "kubernete",  # trailing s dropped from tokens of 5+ characters
        "cluster",
    }
    desc = "Declarative Kubernetes clusters, defined in YAML"
    kw = {"full_name": "org-k/kubeforge", "homepage_domain": None, "url": None}
    title = "Show HN: KubeForge – declarative clusters from YAML manifests"
    assert confirm_by_rules(**kw, description=desc, title=title) == "description_keywords"
    one = "Show HN: KubeForge – clusters for everyone"
    assert confirm_by_rules(**kw, description=desc, title=one) is None  # only 1 shared
    generic = "Show HN: KubeForge – a simple, fast, open source tool"
    assert (
        confirm_by_rules(**kw, description="A simple fast open source tool", title=generic) is None
    )
    # the repo name itself never counts (the title names it by construction)
    assert confirm_by_rules(**kw, description="kubeforge forge", title="Show HN: KubeForge") is None


def test_haiku_input_is_project_level_and_owner_stripped():
    text = haiku_input(
        "orgtally/tallyho", "Counts things for orgtally", "tallyho.dev",
        "Show HN: Tallyho – orgtally's counter", "https://www.tallyho.dev/x",
    )  # fmt: skip
    assert "orgtally" not in text.lower() and "[owner]" in text
    assert '"post_url_domain": "tallyho.dev"' in text and '"repo_name": "tallyho"' in text


# --- 5. E: the Haiku check -----------------------------------------------------------------------
def verdict_for(text: str) -> str:
    return "true" if "TRUE-POST" in text else ("false" if "FALSE-POST" in text else "unsure")


class VerdictBackend:
    """Standard-call fake (`api`): the verdict follows a marker in the post title."""

    name = "api"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *, prompt: str, model: str, **kw: Any) -> BackendResponse:
        self.calls += 1
        return BackendResponse(
            data={"same_project": verdict_for(prompt), "reason": "synthetic"},
            model=model, input_tokens=300, output_tokens=40, cost_usd=0.0004,
        )  # fmt: skip


@dataclass
class VerdictBatchBackend:
    """Batch-capable fake: `polls_until_end` status calls before a batch ends."""

    name: str = "api"
    supports_batch: bool = True
    polls_until_end: int = 0
    submitted: list[list[tuple[str, dict[str, Any]]]] = field(default_factory=list)
    polls: dict[str, int] = field(default_factory=dict)

    def params(self, **kw: Any) -> dict[str, Any]:
        return build_params(max_tokens=100, **kw)

    def complete(self, **kw: Any) -> BackendResponse:
        raise AssertionError("no standard call expected")

    def submit_batch(self, requests: list[tuple[str, dict[str, Any]]]) -> str:
        self.submitted.append(list(requests))
        return f"msgbatch_{len(self.submitted)}"

    def batch_status(self, batch_id: str) -> BatchStatus:
        self.polls[batch_id] = self.polls.get(batch_id, 0) + 1
        ended = self.polls[batch_id] > self.polls_until_end
        return BatchStatus(batch_id, "ended" if ended else "in_progress", {"succeeded": 1})

    def batch_results(self, batch_id: str) -> Iterator[BatchItemResult]:
        n = int(batch_id.rsplit("_", 1)[1])
        for cid, params in self.submitted[n - 1]:
            text = params["messages"][0]["content"]
            yield BatchItemResult(
                cid,
                "succeeded",
                response=BackendResponse(
                    data={"same_project": verdict_for(text), "reason": "synthetic"},
                    model=params["model"],
                    input_tokens=300,
                    output_tokens=40,
                    cost_usd=0.0002,
                    batch_id=batch_id,
                ),
            )


def llm(backend: Any, store: LLMStore | None = None, *, batch: bool = False) -> LLMClient:
    return LLMClient(
        backends={"api": backend},
        default_backend="api",
        store=store or LLMStore(":memory:"),
        models={str(k): v for k, v in DEFAULT_STAGE_MODELS.items()},
        redactor=alias_redact,
        use_batch=batch,
        batch_store=MemoryBatchStore(),
    )


def guard(store: LLMStore, *, approved: bool = True, cap: float = 10.0, backend: str = "api"):
    return BudgetGuard(
        Budget(llm_backend=backend, money_usd=cap),  # type: ignore[arg-type]
        store,
        approved_paid=approved,
    )


def confirmer(client: LLMClient, g: BudgetGuard, **kw: Any) -> Confirmer:
    return Confirmer(
        client,
        brief_run_id=None,
        before_submit=g.before_submit,
        check_backend=lambda b: g.check_backend(b, job=JOB),
        sleep=lambda s: None,
        poll_seconds=0,
        **kw,
    )


def checks() -> list[Check]:
    def mk(i: int, marker: str) -> Check:
        name = f"org-h/widget{i}"
        return Check((f"gh:{name}", 100 + i), haiku_input(
            name, "a synthetic project", None, f"Show HN: Widget{i} – {marker}",
            "https://example.org"))  # fmt: skip

    return [mk(1, "TRUE-POST"), mk(2, "FALSE-POST"), mk(3, "no idea")]


def test_haiku_true_false_unsure_with_provenance():
    store = LLMStore(":memory:")
    be = VerdictBackend()
    got = confirmer(llm(be, store), guard(store)).run(checks())
    by = {k[1]: v for k, v in got.items()}
    assert by[101].confirmed is True and by[101].confirmation == "haiku"
    assert (by[102].confirmed, by[102].confirmation) == (False, "unconfirmed:haiku_false")
    assert (by[103].confirmed, by[103].confirmation) == (False, "unconfirmed:haiku_unsure")
    p = by[101].provenance or {}
    assert p["prompt_id"] == "title_match_check" and p["prompt_version"] == "1"
    assert p["prompt_fingerprint"] == PROMPT.fingerprint
    assert p["model"] == "claude-haiku-4-5-20251001" and p["cached"] is False
    assert be.calls == 3


def test_haiku_fails_closed_without_approval_or_client_or_backend_and_on_a_budget_stop():
    store = LLMStore(":memory:")
    be = VerdictBackend()
    c = llm(be, store)
    for g, reason in (
        (guard(store, approved=False), "haiku_not_approved"),
        (guard(store, cap=1e-9), "haiku_budget_stop"),
        (guard(store, backend="subscription"), "haiku_unavailable"),  # no backend switch
    ):
        got = confirmer(c, g).run(checks())
        assert {o.confirmation for o in got.values()} == {f"unconfirmed:{reason}"}
        assert not any(o.confirmed for o in got.values())
    assert be.calls == 0  # nothing was sent
    got = Confirmer(None).run(checks())
    assert {o.confirmation for o in got.values()} == {"unconfirmed:haiku_unavailable"}


def test_haiku_verdicts_are_cached_and_served_without_approval():
    store = LLMStore(":memory:")
    be = VerdictBackend()
    c = llm(be, store)
    first = confirmer(c, guard(store)).run(checks())
    assert be.calls == 3
    again = confirmer(c, guard(store, approved=False)).run(checks())
    assert be.calls == 3  # all from the cache
    assert {k: o.confirmation for k, o in again.items()} == {
        k: o.confirmation for k, o in first.items()
    }
    assert all((o.provenance or {}).get("cached") for o in again.values())


def test_haiku_checks_go_through_the_batch_api_and_a_running_batch_propagates():
    store = LLMStore(":memory:")
    be = VerdictBatchBackend()
    got = confirmer(llm(be, store, batch=True), guard(store)).run(checks())
    assert len(be.submitted) == 1 and len(be.submitted[0]) == 3  # one batch
    assert sorted(o.confirmation for o in got.values()) == [
        "haiku", "unconfirmed:haiku_false", "unconfirmed:haiku_unsure",
    ]  # fmt: skip
    assert all((o.provenance or {}).get("batch_id") == "msgbatch_1" for o in got.values())
    slow = VerdictBatchBackend(polls_until_end=5)
    with pytest.raises(BatchPending):
        confirmer(llm(slow, LLMStore(":memory:"), batch=True), guard(store),
                  timeout_seconds=0).run(checks())  # fmt: skip


# --- 6. the guard and the pre-registered parameters ---------------------------------------------
def test_guard_hash_is_in_the_params_and_any_guarded_change_changes_the_params_hash(monkeypatch):
    p = Context.from_brief(B).params()
    assert p["anchor_rule_source_sha256"] == anchor_rule_source_sha256()
    assert p["anchor_rule_source_sha256"] == ANCHOR_RULE_SOURCE_SHA256  # pinned
    h0 = sha256_json(p)

    def h() -> str:
        return sha256_json(Context.from_brief(B).params())

    def other_owner_named(title: Any, url: Any, full_name: str) -> bool:
        return False

    for mod, name, value in (
        (conf, "KEYWORD_MIN_SHARED", 3),  # the confirmation logic's threshold
        (conf, "owner_named", other_owner_named),  # the confirmation logic's code
        (out, "_lookup_repo", lambda *a: None),  # the lookup loop
        (selmod, "FIT_EPS", 1e-9),  # view A's fit
    ):
        monkeypatch.setattr(mod, name, value)
        assert h() != h0, name
        monkeypatch.undo()
    import pigtail.connectors.hn as hn

    monkeypatch.setattr(hn, "normalize_github_repo", other_owner_named)  # the URL normalizer
    assert h() != h0
    monkeypatch.undo()
    assert h() == h0


def test_params_define_the_views_the_confirmation_rule_and_the_prompt():
    p = Context.from_brief(B).params()
    assert p["selection_version"] == "selection-v5" and p["anchor_rule_version"] == "anchor-v4"
    assert set(p["views"]) == {"follow_through", "launch"}
    a, b = p["views"]["follow_through"], p["views"]["launch"]
    assert a["attention_metric"] == FT_RESID and a["matching"]["calipers"]["lsm_sd"] == 0.5
    assert a["sensitivity_extra"] == [f"metric:attention:{FT_LOGRATIO}",
                                      "metric:attention:att.stars@30"]  # fmt: skip
    assert b["attention_metric"] == LAUNCH_SIZE and "lsm_sd" not in b["matching"]["calipers"]
    assert b["matching"]["calipers"]["prelaunch_log_sd"] == 0.5
    assert "burst" in b["population"]
    assert p["follow_through"]["follow_days"] == [3, 30]
    assert p["context_view"]["label"] == CONTEXT_LABEL
    tc = p["title_confirmation"]
    assert tc == confirmation_params() and tc["haiku"]["prompt_fingerprint"] == PROMPT.fingerprint
    assert tc["keyword_min_shared"] == 2 and "has_url_launch" in tc["rule"]


def test_the_brief_success_definition_hash_is_unchanged_by_the_views():
    d = Definition.from_brief(B).to_dict()
    assert d["metrics"]["attention"] == "att.stars@30"  # the brief's own definition
    assert set(d) == {
        "primary",
        "floors",
        "metrics",
        "business_minimum",
        "if_not_applicable",
        "weights",
        "no_measurable_adoption",
        "accept_self_reported",
        "rank_by",
    }


# --- 7. the estimate ------------------------------------------------------------------------------
def test_estimate_counts_prelaunch_pages_and_haiku_checks_while_the_selection_is_pending():
    e = estimate(B, selection=SelectionState(pending=True, shortlisted=114))
    assert e.selection["title_match_checks"] == math.ceil(114 * 0.1) == 12
    assert e.selection["prelaunch_extra_core_requests"] == math.ceil(114 * 0.25) * 4
    tc = {s.stage: s for s in e.stages}["title_match_check"]
    assert tc.model == "claude-haiku-4-5-20251001" and tc.llm_calls == 12
    scope = run_scope(e, ("selection",))
    assert scope["selection"]["llm_calls"] == 12 and scope["requires_approval"] is True
    assert scope["selection"]["api_usd"] == pytest.approx(tc.usd, abs=1e-4)
    done = estimate(B, selection=SelectionState(pending=False))
    assert done.selection["title_match_checks"] == 0
    assert done.selection["prelaunch_extra_core_requests"] == 0

"""M22 verifier round 2 fixes, without a database (synthetic data and fakes only):

- the fallback budget check re-reads the brief's recorded spend (R15.11, R18.5): the verifier's
  probe (batch estimate $0.10, the item charged $0.10 for invalid output, fallback estimated at
  $0.20, cap $0.25) now stops before the fallback call;
- headline-first matching (ADR-078): fewer pairs excluded from the headline on the seeded
  fixtures, still deterministic, exact match and calipers unchanged; a missing language counts
  as a mismatch;
- balance says how winners are counted; sensitivity says why an alternative doesn't apply;
- pre-registration hashes carry no brief text, and a file quoting the brief is recognised (R8.2).
"""

from __future__ import annotations

import random
from dataclasses import replace
from functools import partial
from typing import Any

import pytest

from pigtail.briefs.budget import BudgetGuard, BudgetStop
from pigtail.briefs.model import Budget
from pigtail.briefs.preregistration import hashes, quotes_brief
from pigtail.briefs.selection import (
    LSM_CALIPER_SD,
    SELECTION_VERSION,
    Context,
    Covariates,
    Definition,
    match_losers,
    select,
)
from pigtail.llm.batch import MemoryBatchStore
from pigtail.llm.store import LLMStore, UsageRow
from tests.selection_fake import brief, case, population
from tests.unit.test_llm_m21b import PROMPT, FakeBatchBackend, client, items


# --- 1. fallback budget check on fresh spend ----------------------------------------------------
class ChargedBatchBackend(FakeBatchBackend):
    """Every batch result costs `usd` (the probe's invalid output is charged in full)."""

    usd: float = 0.10

    def batch_results(self, batch_id: str) -> Any:
        for r in super().batch_results(batch_id):
            if r.response is not None:
                r = replace(r, response=replace(r.response, cost_usd=self.usd))
            yield r


class Ledger:
    """In-memory cost ledger (`llm_cost_ledger` stand-in): the brief's API total."""

    def __init__(self) -> None:
        self.rows: list[UsageRow] = []

    def record(self, row: UsageRow) -> None:
        self.rows.append(row)

    def total(self) -> float:
        return sum(r.cost_usd for r in self.rows if r.backend == "api")


def probe(fresh: bool) -> tuple[Any, Ledger, ChargedBatchBackend]:
    fb = ChargedBatchBackend(polls_until_end=0)
    fb.standard.script = [{"value": 1, "label": "std"}]
    ledger = Ledger()
    store = LLMStore(":memory:")
    c = client({"api": fb}, store=store, cost_sink=ledger, batch_store=MemoryBatchStore())
    g = BudgetGuard(
        Budget(llm_backend="api", money_usd=0.25),
        store,
        approved_paid=True,
        brief_ledger=ledger.total if fresh else None,
    )
    run = c.run_batch(PROMPT, items("SCHEMA"), type(_echo()), job="extraction",
                      before_submit=g.before_submit, est_usd_per_item=0.10,
                      sleep=lambda s: None)  # fmt: skip
    return run, ledger, fb


def _echo() -> Any:
    from tests.conftest import Echo

    return Echo(value=1, label="x")


def test_r15_11_fallback_check_counts_the_batch_items_already_charged():
    """Verifier probe: without a fresh read the fallback passes (0 + 0.20 <= 0.25) and the brief
    ends above its cap; with it, 0.10 charged + 0.20 > 0.25 stops before the standard call."""
    run, _ledger, fb = probe(fresh=False)  # the old behaviour, reproduced
    assert run.standard_fallbacks == 1 and len(fb.standard.calls) == 1
    with pytest.raises(BudgetStop) as ei:
        probe(fresh=True)
    assert ei.value.kind == "money" and "0.10 spent" in ei.value.detail
    assert "0.20 would exceed" in ei.value.detail and "H6" in ei.value.detail


def test_r15_11_fresh_read_before_every_check_and_never_lower():
    spent = {"usd": 0.0}
    store = LLMStore(":memory:")
    g = BudgetGuard(Budget(llm_backend="api", money_usd=1.0), store, approved_paid=True,
                    brief_ledger=lambda: spent["usd"])  # fmt: skip
    g.before_submit("relevance", 10, 0.5)
    spent["usd"] = 0.8  # charged since the guard was built
    with pytest.raises(BudgetStop):
        g.before_submit("relevance", 10, 0.3)
    assert g.spent_usd == pytest.approx(0.8)
    g.charge_paid("paid service", 0.1)  # non-API money is added on top of the ledger
    spent["usd"] = 0.0  # a ledger that reads lower never lowers the spend
    assert g.refresh() == pytest.approx(0.9)
    # the monthly cap already reads the usage ledger at every check
    store.record(UsageRow("api", "extraction", "m", "p", "1", "ok", 1, 1, cost_usd=199.95))
    g2 = BudgetGuard(Budget(llm_backend="api", money_usd=500), store, approved_paid=True)
    with pytest.raises(BudgetStop) as ei:
        g2.before_submit("relevance", 1, 0.1)
    assert ei.value.kind == "month"


# --- 2. headline-first matching (ADR-078) --------------------------------------------------------
def excluded(cases: list[Any], prefer: bool, monkeypatch: pytest.MonkeyPatch) -> tuple[int, ...]:
    """(pairs excluded from the headline, pairs, unmatched winners) of the whole selection, with
    the selection-v1 rule (`prefer=False`) or the ADR-078 rule."""
    from pigtail.briefs import selection as S

    orig = match_losers
    with monkeypatch.context() as m:
        m.setattr(S, "match_losers", partial(orig, prefer_headline=prefer))
        b = brief()
        sel = select(cases, Context.from_brief(b), Definition.from_brief(b))
    bal = sel.balance
    return bal["headline_excluded"]["pairs"], bal["pairs"], bal["unmatched_winners"]


def test_adr_078_headline_first_matching_reduces_exclusions_deterministically(monkeypatch):
    """Seeded 80-case fixtures: selection-v1 (nearest only) vs selection-v2 (headline-passing
    first). Same winners, same number of pairs and matched winners; never more exclusions, and
    fewer overall (ADR-078 records the counts)."""
    before, after = [], []
    for seed in range(1, 11):
        cases = population(80, seed)
        b0, n0, m0 = excluded(cases, False, monkeypatch)
        b1, n1, m1 = excluded(cases, True, monkeypatch)
        assert (n0, m0) == (n1, m1)  # coverage is kept: every matchable winner still matched
        assert b1 <= b0
        before.append(b0)
        after.append(b1)
    assert before == [15, 15, 14, 11, 13, 13, 18, 8, 11, 15]  # ADR-078 "before" (80 cases)
    assert after == [15, 15, 13, 11, 10, 10, 17, 3, 11, 15]  # ADR-078 "after"
    assert sum(after) < sum(before)
    # deterministic: shuffled input, same result hash
    cases = population(80, 8)
    b = brief()
    sel = select(cases, Context.from_brief(b), Definition.from_brief(b))
    for s in (1, 2):
        shuffled = list(cases)
        random.Random(s).shuffle(shuffled)
        again = select(shuffled, Context.from_brief(b), Definition.from_brief(b))
        assert again.result_hash == sel.result_hash
    assert sel.params["selection_version"] == SELECTION_VERSION == "selection-v3"
    assert "headline-passing first" in sel.params["matching"]
    # exact match and calipers are unchanged by the preference
    assert sel.balance["exact_match"]["ok"] is True
    sd = sel.balance["sd"]["lsm"]
    by = {c["candidate_ref"]: c for c in sel.cases}
    for c in sel.cases:
        p = c["pair"]
        if p and p["side"] == "loser" and p["panel"] == "field":
            (w,) = [x for x in sel.cases if x["pair"] and x["pair"]["pair_id"] == p["pair_id"]
                    and x["role"] == "winner"]  # fmt: skip
            assert abs(w["covariates"]["lsm"] - by[c["candidate_ref"]]["covariates"]["lsm"]) <= (
                LSM_CALIPER_SD * sd + 1e-9
            )
            assert w["covariates"]["launch_half_year"] == c["covariates"]["launch_half_year"]
    assert sel.balance["headline_excluded"]["pairs"] == 3


def test_adr_078_round_one_covers_every_matchable_winner_before_extra_losers():
    sel = select(population(80, 8), Context.from_brief(brief()), Definition.from_brief(brief()))
    rounds: dict[int, set[int]] = {}
    for c in sel.cases:
        p = c["pair"]
        if p and p["side"] == "loser" and p["panel"] == "field":
            rounds.setdefault(p["round"], set()).add(p["pair_id"])
    assert rounds[1] == {c["pair"]["pair_id"] for c in sel.cases if c["role"] == "winner"
                         and c["pair"]}  # fmt: skip
    assert sorted(rounds) == list(range(1, len(rounds) + 1))  # round numbers without gaps


def test_adr_078_missing_language_is_a_mismatch_for_the_headline_and_the_distance():
    cases = [case(i, stars=10 + i) for i in range(30)]
    cases = [replace(c, covariates=replace(c.covariates, language=None)) for c in cases]
    b = brief(minimums={}, primary_threshold="top_quartile")
    sel = select(cases, Context.from_brief(b), Definition.from_brief(b))
    losers = [c["pair"] for c in sel.cases if c["pair"] and c["pair"]["side"] == "loser"]
    assert losers and all(p["diffs"]["language"] == 1.0 for p in losers)
    assert all("language" in p["excluded_on"] and p["headline"] is False for p in losers)
    assert "missing language" in sel.balance["headline_excluded"]["rule"]
    assert Covariates().language is None


# --- 4d, 4g: balance counting note, sensitivity reasons ------------------------------------------
def test_balance_counts_each_winner_once_and_sensitivity_says_why_not_applicable():
    b = brief()
    ctx = replace(Context.from_brief(b), min_winners=14)
    sel = select(population(), ctx, Definition.from_brief(b))
    assert "each matched winner counts once" in sel.balance["winner_counting"]
    steps = [s["step"] for s in sel.summary["steps"]]
    assert "drop_community_minimum" in steps
    alts = {a["key"]: a for a in sel.sensitivity["alternatives"]}
    dropped = alts["band_shift:community"]
    assert dropped["ran"] is False
    assert dropped["reason"].startswith("not applicable: the community minimum was dropped")
    assert all(a["reason"].startswith("not applicable") for a in alts.values() if not a["ran"])


# --- 2. pre-registration hashes (R8.2) ------------------------------------------------------------
def test_r8_2_hashes_reveal_no_brief_text_and_quotes_are_recognised():
    b = brief()
    h = hashes(b)
    assert h["brief_sha256"] == b.content_hash() and h["brief_version"] == 1
    assert len(h["success_definition_sha256"]) == len(h["selection_params_sha256"]) == 64
    blob = repr(h)
    assert b.project.description not in blob and b.field.core_field not in blob
    assert hashes(b) == h  # stable
    b2 = brief(primary_threshold="top_decile")
    assert hashes(b2)["success_definition_sha256"] != h["success_definition_sha256"]
    # a file quoting a free-text brief value is recognised; method words and enums are not
    assert quotes_brief(f"# Pre-registration\n\n{b.project.description.upper()}\n", b)
    assert not quotes_brief("Hypotheses MC-01, MC-02. relax_primary_to_top_third. SHA-256 "
                            + h["brief_sha256"], b)  # fmt: skip

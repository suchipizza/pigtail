"""M4-T4: holdout split (threshold-calibration pre-registration §1.1) and the H-sealed guard.

Synthetic ids only (`case_example_*`, `case_synth_*`): none is a real case.
"""

from __future__ import annotations

import hashlib
import itertools
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest

from pigtail.analysis import split
from pigtail.analysis.split import (
    HeldOutCaseError,
    MemoryUnsealLog,
    PgUnsealLog,
    SealedCaseError,
    UnsealToken,
    calibration_only,
    guard_cases,
    sealed_guard,
    split_m,
    split_of,
)

REGISTRY = frozenset({"ADR-019", "ADR-039"})


def _find(m: int) -> str:
    """A synthetic id whose `m` is `m` (searched, so the test does not hard-code digests)."""
    for i in itertools.count():
        cid = f"case_synth_{i:05d}"
        if split_m(cid) == m:
            return cid
    raise AssertionError("unreachable")


def _ids(split_name: str, n: int) -> list[str]:
    out = []
    for i in itertools.count():
        cid = f"case_synth_{i:05d}"
        if split_of(cid) == split_name:
            out.append(cid)
            if len(out) == n:
                return out
    raise AssertionError("unreachable")


# --- §1.1 split rule ----------------------------------------------------------------------------
@pytest.mark.parametrize(("case_id", "m", "expected"), split.TEST_VECTORS)
def test_m4_t4_preregistered_test_vectors(case_id, m, expected):
    assert split_m(case_id) == m
    assert split_of(case_id) == expected


def test_m4_t4_published_vector_table_is_unchanged():
    assert split.TEST_VECTORS == (
        ("case_example_04", 5, "h_eval"),
        ("case_example_00", 20, "h_sealed"),
        ("case_example_a", 32, "calibration"),
        ("case_example_b", 58, "calibration"),
    )


def test_m4_t4_formula_is_exactly_the_preregistered_one():
    for cid in ("case_example_a", "case_00000000000000000001", "case_ü_utf8"):
        ref = int(hashlib.sha256(("pigtail-outcome-holdout-v1" + cid).encode("utf-8")).hexdigest(), 16) % 100  # fmt: skip  # noqa: E501
        assert split_m(cid) == ref
    assert split.HOLDOUT_SALT == "pigtail-outcome-holdout-v1"


@pytest.mark.parametrize(
    ("m", "expected"), [(19, "h_eval"), (20, "h_sealed"), (29, "h_sealed"), (30, "calibration")]
)
def test_m4_t4_boundaries(m, expected):
    cid = _find(m)
    assert split_m(cid) == m
    assert split_of(cid) == expected
    assert split.is_held_out(cid) is (m < 30)
    assert split.split_for_m(m) == expected


def test_m4_t4_split_for_m_covers_0_to_99():
    got = [split.split_for_m(m) for m in range(100)]
    assert got.count("calibration") == 70 and got.count("h_eval") == 20
    assert got.count("h_sealed") == 10
    with pytest.raises(ValueError):
        split.split_for_m(100)


def test_m4_t4_case_id_used_exactly_as_stored():
    cid = "case_example_a"
    assert split_m(cid) == 32
    # no normalization: whitespace or case changes are different ids
    assert {split_m(" " + cid), split_m(cid.upper()), split_m(cid + "\n")} != {32}
    with pytest.raises(TypeError):
        split_m("")
    with pytest.raises(TypeError):
        split_m(123)  # type: ignore[arg-type]


def test_m4_t4_calibration_only_drops_held_out_before_reading():
    ids = [*_ids("calibration", 3), *_ids("h_eval", 2), *_ids("h_sealed", 2)]
    assert calibration_only(ids) == ids[:3]


# --- guard --------------------------------------------------------------------------------------
def test_m4_t4_guard_passes_calibration_cases_untouched():
    ids = _ids("calibration", 5)
    log = MemoryUnsealLog()
    assert guard_cases(iter(ids), computation="percentiles", unseal_log=log) == ids
    assert log.entries == []


def test_m4_t4_guard_refuses_h_sealed_without_token():
    ids = [*_ids("calibration", 2), _find(25)]
    with pytest.raises(SealedCaseError, match="1 H-sealed"):
        guard_cases(ids, computation="classes", thresholds_frozen=True)


def test_m4_t4_guard_refuses_h_sealed_even_after_freeze_and_with_eval_allowed():
    with pytest.raises(SealedCaseError):
        guard_cases([_find(20)], computation="classes", thresholds_frozen=True)


def test_m4_t4_guard_refuses_h_eval_before_freeze_and_allows_after():
    ev = _ids("h_eval", 2)
    with pytest.raises(HeldOutCaseError):
        guard_cases(ev, computation="percentiles")
    # no token unseals H-eval early
    with pytest.raises(HeldOutCaseError):
        guard_cases(
            ev,
            computation="percentiles",
            unseal_token=UnsealToken("ADR-039", "x"),
            unseal_log=MemoryUnsealLog(),
            adr_registry=REGISTRY,
        )
    assert guard_cases(ev, computation="percentiles", thresholds_frozen=True) == ev


def test_m4_t4_unseal_token_must_reference_an_adr():
    for bad in ("", "adr-019", "ADR-19", "ADR 019", "DEC-019"):
        with pytest.raises(ValueError):
            UnsealToken(bad, "held-out re-run")
    with pytest.raises(ValueError):
        UnsealToken("ADR-019", "  ")
    with pytest.raises(SealedCaseError, match="not in the decision log"):
        guard_cases(
            [_find(21)],
            computation="classes",
            thresholds_frozen=True,
            unseal_token=UnsealToken("ADR-999", "re-run"),
            unseal_log=MemoryUnsealLog(),
            adr_registry=REGISTRY,
        )


def test_m4_t4_unseal_requires_a_log_and_every_unseal_is_logged():
    sealed = [_find(22), _find(27)]
    ids = [*_ids("calibration", 2), *sealed]
    tok = UnsealToken("ADR-039", "post-freeze threshold change re-run (§5.3)")
    with pytest.raises(SealedCaseError, match="logged"):
        guard_cases(ids, computation="classes", thresholds_frozen=True, unseal_token=tok,
                    adr_registry=REGISTRY)  # fmt: skip
    log = MemoryUnsealLog()
    at = datetime(2027, 1, 1, tzinfo=UTC)
    got = guard_cases(ids, computation="classes", thresholds_frozen=True, unseal_token=tok,
                      unseal_log=log, adr_registry=REGISTRY, code_commit="abc1234",
                      clock=lambda: at)  # fmt: skip
    assert got == ids
    (e,) = log.entries
    assert (e.adr_id, e.computation, e.at, e.code_commit) == ("ADR-039", "classes", at, "abc1234")
    assert e.case_ids == tuple(sorted(sealed))  # only the H-sealed ids
    guard_cases(ids, computation="percentiles", thresholds_frozen=True, unseal_token=tok,
                unseal_log=log, adr_registry=REGISTRY, code_commit="abc1234")  # fmt: skip
    assert [x.computation for x in log.entries] == ["classes", "percentiles"]


def test_m4_t4_unseal_checks_the_real_decision_log():
    """ADR-039 (sealed second holdout) is in ops/DECISIONS.md; a made-up id is not."""
    adrs = split.known_adrs()
    assert {"ADR-019", "ADR-039"} <= adrs and "ADR-9999" not in adrs


def test_m4_t4_sealed_guard_decorator(monkeypatch):
    monkeypatch.setattr(split, "known_adrs", lambda: REGISTRY)
    calls: list[list[str]] = []

    @sealed_guard("test_classes")
    def classes(
        case_ids: Any,
        *,
        unseal_token: UnsealToken | None = None,
        unseal_log: Any = None,
        thresholds_frozen: bool = False,
    ) -> int:
        calls.append(list(case_ids))
        return len(calls[-1])

    cal = _ids("calibration", 3)
    assert classes(iter(cal)) == 3 and calls[-1] == cal
    with pytest.raises(SealedCaseError):
        classes([*cal, _find(23)], thresholds_frozen=True)
    log = MemoryUnsealLog()
    tok = UnsealToken("ADR-039", "re-run")
    assert classes([*cal, _find(23)], unseal_token=tok, unseal_log=log, thresholds_frozen=True) == 4
    assert len(log.entries) == 1 and split.GUARDED["test_classes"].endswith("classes")
    with pytest.raises(TypeError, match="sealed_guard needs"):

        @sealed_guard("bad")
        def no_params(case_ids: Any) -> None: ...


@pytest.mark.db
def test_m4_t4_unseal_log_table_is_append_only(capture_db):
    log = PgUnsealLog(capture_db.conn)
    tok = UnsealToken("ADR-039", "re-run")
    sealed = _find(24)
    guard_cases([sealed], computation="classes", thresholds_frozen=True, unseal_token=tok,
                unseal_log=log, adr_registry=REGISTRY, code_commit="abc1234")  # fmt: skip
    row = capture_db.conn.execute(
        "SELECT adr_id, computation, n_cases, case_ids, case_ids_sha256 FROM holdout_unseal_log"
    ).fetchone()
    assert row[:4] == ("ADR-039", "classes", 1, [sealed])
    assert row[4] == hashlib.sha256(sealed.encode()).hexdigest()
    for stmt in ("UPDATE holdout_unseal_log SET reason = 'x'", "DELETE FROM holdout_unseal_log",
                 "TRUNCATE holdout_unseal_log"):  # fmt: skip
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            capture_db.conn.execute(stmt)

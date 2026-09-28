"""Double coding, citation validation, adjudication and per-field alpha of the pilot (PRD R7.1-R7.6,
R15.8-R15.10; codebook §10-§11; ADR-047.7, ADR-065, ADR-086).

1. **Inputs** (`build_inputs`): per case the stored items, rendered (`evidence.rendered_items`),
   in coder A's and coder B's orders. The LLM client trims nothing more and redacts the whole
   input (`alias_redact`, CB-06); the validator redacts the same text the same way, so a quote
   is checked against exactly what that pass saw (ADR-075.4).
2. **Coding** (`submit`, `collect`): coder A and coder B each as one Message Batch on the
   extraction model (job `double_coding`), both submitted before either is awaited, each gated
   by the brief's `BudgetGuard` with the cost model's per-call estimate. A batch still running
   raises `BatchPending`; the next invocation collects it by its stored id, never resubmitting.
3. **Validation** (`validate`): the flat output (schema 2.0.0) is cut into the frame's units
   and each value checked against its field's type (`frame.flatten`: outside the enum it is
   `unknown`, `schema_invalid`); then every unit through `citations.check`; dropped values
   become `unknown` (`citation_failed`). A case whose request failed is excluded for that pass
   (`coding_failed`, with the API's error type and scrubbed message in the row's detail).
4. **Adjudication** (`adjudication_items`, `finalize`): only units whose A and B values differ
   (excluded units never), one batched request per case with disagreements (job
   `adjudication`, extraction model), options in a seeded per-unit order; the decision is
   validated like a coding (a value outside the field's enum is `schema_invalid`, citations
   checked); agreed units take
   pass A's validated value. MC-12 is derived (§7.4).
5. **Alpha** (`reliability_rows`): per field on A and B after validation, before adjudication
   (§10.1); nominal with `unknown` as a value; ordinal with `unknown` missing plus the
   known/unknown companion (§10.2); `pattern.*` also pooled (C11a). Labels: `low reliability`
   below 0.70, `reliability not assessed` below the minimum units (§10.4), always
   `LLM-coded, not human-validated` and `pilot, n = <cases>`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from pigtail.forensics import alpha as ka
from pigtail.forensics.citations import PERSON_TOKEN, CitationStats, check
from pigtail.forensics.frame import (
    FIELD_BY_NAME,
    FIELDS,
    SCHEMA_INVALID,
    Adjudication,
    CaseCoding,
    CodedUnit,
    allowed_values,
    derived_units,
    field_of,
    flatten,
)
from pigtail.forensics.prompts import (
    ADJUDICATOR,
    CODER_A,
    CODER_B,
    JOB_ADJUDICATION,
    JOB_CODING,
    RenderedItem,
    adjudication_input,
    case_input,
    option_order,
    order_a,
    order_b,
)
from pigtail.forensics.store import CodingRow, PilotCase
from pigtail.llm import BatchItem, BatchPending, LLMClient
from pigtail.llm.client import BeforeSubmit
from pigtail.llm.redact import alias_redact
from pigtail.llm.types import LLMResult, PromptSpec

NAMESPACE = "github"  # the redaction namespace of @mentions in READMEs and release notes
_BLOCK = re.compile(r"^### evidence_id: (\S+)\n", re.MULTILINE)
PILOT_LABEL = "pilot, n = {n}"
LLM_ONLY = "LLM-coded, not human-validated"
MAX_REASON_CHARS = 500


@dataclass
class CaseInput:
    case: PilotCase
    items: list[RenderedItem]
    missing: list[str]
    text: dict[str, str]  # pass -> the input text sent (before the client's redaction)

    @property
    def offered(self) -> tuple[str, ...]:
        return tuple(sorted(i.evidence_id for i in self.items))


def build_inputs(
    cases: Sequence[PilotCase],
    render: Callable[[PilotCase], tuple[list[RenderedItem], list[str]]],
) -> dict[str, CaseInput]:
    out: dict[str, CaseInput] = {}
    for c in cases:
        items, missing = render(c)
        at = str(c.anchor.get("at", ""))
        prec = str(c.anchor.get("precision", "day"))
        anchor = f"{at[:16]} UTC ({prec} precision)"
        out[c.case_key] = CaseInput(
            c,
            items,
            missing,
            {
                "A": case_input(c.coding_id, anchor, order_a(items)),
                "B": case_input(c.coding_id, anchor, order_b(items)),
            },
        )
    return out


def blocks(text: str) -> dict[str, str]:
    """`evidence_id -> block text` of an input (headers included), as redacted for the model."""
    red = alias_redact(text, NAMESPACE)
    marks = list(_BLOCK.finditer(red))
    out: dict[str, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(red)
        out[m.group(1)] = red[m.end() : end]
    return out


# --- coding ------------------------------------------------------------------------------------
PASS_PROMPT: dict[str, PromptSpec] = {"A": CODER_A, "B": CODER_B}


def _items(inputs: Mapping[str, CaseInput], pass_: str) -> list[BatchItem]:
    return [
        BatchItem(
            ref=ci.case.coding_id,
            input_text=ci.text[pass_],
            namespace=NAMESPACE,
            evidence_id=ci.items[0].evidence_id if ci.items else None,
            case_ref=ci.case.coding_id,
        )
        for ci in sorted(inputs.values(), key=lambda c: c.case.coding_id)  # interleaved roles
        if ci.items
    ]


@dataclass
class PassRun:
    results: dict[str, LLMResult[Any]] = field(default_factory=dict)  # coding id -> result
    failed: dict[str, str] = field(default_factory=dict)
    errors: dict[str, dict[str, str | None]] = field(default_factory=dict)  # coding id -> error
    batch_ids: list[str] = field(default_factory=list)
    mode: str = "batch"


def run_pass(
    client: LLMClient,
    prompt: PromptSpec,
    items: Sequence[BatchItem],
    schema: type[Any],
    *,
    job: str,
    brief_run_id: str,
    before_submit: BeforeSubmit | None,
    est_usd_per_item: float | None,
    timeout_seconds: float | None,
    poll_seconds: float,
    sleep: Callable[[float], None] | None,
) -> PassRun:
    kw: dict[str, Any] = {}
    if sleep is not None:
        kw["sleep"] = sleep
    run = client.run_batch(
        prompt,
        items,
        schema,
        job=job,
        brief_run_id=brief_run_id,
        before_submit=before_submit,
        est_usd_per_item=est_usd_per_item,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        **kw,
    )
    return PassRun(
        dict(run.results), dict(run.failed), dict(run.errors), list(run.batch_ids), run.mode
    )


def submit_and_collect(
    client: LLMClient,
    inputs: Mapping[str, CaseInput],
    *,
    brief_run_id: str,
    before_submit: BeforeSubmit | None,
    est_usd_per_call: float | None,
    wait_seconds: float | None,
    poll_seconds: float,
    sleep: Callable[[float], None] | None = None,
    passes: Sequence[str] = ("A", "B"),
    on_batches: Callable[[list[str]], None] | None = None,
) -> dict[str, PassRun]:
    """Both coding batches in flight together, then collected (module docstring).
    `on_batches` receives every batch id as soon as it is known (to record it)."""
    seen: dict[str, list[str]] = {p: [] for p in passes}

    def note(p: str, ids: Sequence[str]) -> None:
        new = [i for i in ids if i not in seen[p]]
        seen[p].extend(new)
        if new and on_batches is not None:
            on_batches(list(new))

    finished: dict[str, PassRun] = {}
    for p in passes:  # submit (or find) every batch first
        try:
            r = run_pass(
                client,
                PASS_PROMPT[p],
                _items(inputs, p),
                CaseCoding,
                job=JOB_CODING,
                brief_run_id=brief_run_id,
                before_submit=before_submit,
                est_usd_per_item=est_usd_per_call,
                timeout_seconds=0.0,
                poll_seconds=poll_seconds,
                sleep=sleep,
            )
            note(p, r.batch_ids)
            # ended already: keep it (running the pass again would resubmit its failed items,
            # e.g. a request the API refused, within the same invocation; addendum 1)
            finished[p] = r
        except BatchPending as e:
            note(p, e.batch_ids)
            continue
    out: dict[str, PassRun] = {}
    for p in passes:
        if p in finished:
            out[p] = finished[p]
            out[p].batch_ids = list(seen[p])
            continue
        try:
            out[p] = run_pass(
                client,
                PASS_PROMPT[p],
                _items(inputs, p),
                CaseCoding,
                job=JOB_CODING,
                brief_run_id=brief_run_id,
                before_submit=before_submit,
                est_usd_per_item=est_usd_per_call,
                timeout_seconds=wait_seconds,
                poll_seconds=poll_seconds,
                sleep=sleep,
            )
        except BatchPending as e:
            note(p, e.batch_ids)
            raise
        note(p, out[p].batch_ids)
        out[p].batch_ids = list(seen[p])
    return out


def batch_of_cases(client: LLMClient, batch_ids: Sequence[str]) -> dict[tuple[str, str], str]:
    """(prompt id, coding id) -> the batch that coded it, from the batch store's request records
    (a result read back from the cache after its batch was collected carries no batch id)."""
    out: dict[tuple[str, str], str] = {}
    for bid in batch_ids:
        rec = client.batch_store.get(bid)
        if rec is None:
            continue
        for r in client.batch_store.requests(bid):
            if r.case_ref and r.status == "succeeded":
                out.setdefault((rec.prompt_id, r.case_ref), bid)
    return out


def _provenance(
    res: LLMResult[Any] | None, batch_of: Mapping[tuple[str, str], str] | None = None,
    coding_id: str = "",
) -> dict[str, Any]:  # fmt: skip
    if res is None:
        return {}
    prov = dict(res.provenance())
    if prov.get("batch_id") is None and batch_of:
        prov["batch_id"] = batch_of.get((res.prompt_id, coding_id))
    return prov


def validate(
    inputs: Mapping[str, CaseInput],
    runs: Mapping[str, PassRun],
    batch_of: Mapping[tuple[str, str], str] | None = None,
    per_case: dict[str, dict[str, CitationStats]] | None = None,
) -> tuple[list[CodingRow], dict[str, CitationStats]]:
    """Pass A and B rows after schema and citation validation (codebook §11.1). `per_case`, when
    given, receives the stats per coding id and pass as well."""
    rows: list[CodingRow] = []
    stats = {p: CitationStats() for p in runs}
    for ci in inputs.values():
        for p, run in runs.items():
            res = run.results.get(ci.case.coding_id)
            prov = _provenance(res, batch_of, ci.case.coding_id)
            if res is None:
                why = run.failed.get(
                    ci.case.coding_id, "no_evidence_items" if not ci.items else "no_result"
                )
                detail: dict[str, Any] = {"failure": why}
                if ci.case.coding_id in run.errors:
                    detail["error"] = dict(run.errors[ci.case.coding_id])
                for u in _all_units(ci):
                    rows.append(
                        CodingRow(
                            ci.case,
                            p,
                            u,
                            field_of(u),
                            "unknown",
                            "excluded",
                            unknown_reason="insufficient_evidence",
                            excluded="coding_failed",
                            detail=detail,
                        )
                    )
                continue
            texts = blocks(ci.text[p])
            flat = flatten(res.output, ci.offered)
            mine = CitationStats(units_ignored=len(flat.ignored))
            if per_case is not None:
                per_case.setdefault(ci.case.coding_id, {})[p] = mine
            stats[p].units_ignored += len(flat.ignored)
            for unit in flat.units:
                c = check(unit, texts)
                stats[p].add(c)
                mine.add(c)
                rows.append(
                    _row(
                        ci.case,
                        p,
                        c.unit,
                        c.status,
                        prov,
                        {"problems": list(c.problems), "excerpts_dropped": c.excerpts_dropped}
                        if c.problems or c.excerpts_dropped
                        else {},
                    )
                )
    return rows, stats


def _all_units(ci: CaseInput) -> list[str]:
    out = []
    for f in FIELDS:
        if f.unit == "case":
            out.append(f.field)
        else:
            out.extend(f"{f.field}@{ev}" for ev in ci.offered)
    return out


def _row(
    case: PilotCase,
    pass_: str,
    u: CodedUnit,
    status: str,
    prov: dict[str, Any],
    detail: dict[str, Any] | None = None,
) -> CodingRow:
    return CodingRow(
        case,
        pass_,
        u.unit,
        u.field,
        u.value,
        status,
        unknown_reason=u.unknown_reason,
        evidence_ids=u.evidence_ids,
        excerpts=u.excerpts,
        confidence=u.confidence,
        excluded=u.excluded,
        detail=detail or {},
        provenance=prov,
    )


# --- adjudication ------------------------------------------------------------------------------
def _by_unit(rows: Sequence[CodingRow], pass_: str) -> dict[tuple[str, str], CodingRow]:
    return {(r.case.case_key, r.unit): r for r in rows if r.pass_ == pass_}


def disagreements(rows: Sequence[CodingRow]) -> dict[str, list[tuple[CodingRow, CodingRow]]]:
    """Per case the units whose A and B values differ; excluded units never disagree."""
    a, b = _by_unit(rows, "A"), _by_unit(rows, "B")
    out: dict[str, list[tuple[CodingRow, CodingRow]]] = {}
    for key, ra in sorted(a.items()):
        rb = b.get(key)
        if rb is None or ra.excluded or rb.excluded:
            continue
        if ra.value != rb.value:
            out.setdefault(key[0], []).append((ra, rb))
    return out


def adjudication_items(
    inputs: Mapping[str, CaseInput], dis: Mapping[str, list[tuple[CodingRow, CodingRow]]]
) -> tuple[list[BatchItem], dict[str, str]]:
    """One request per case with disagreements; returns the items and each case's input text."""
    items: list[BatchItem] = []
    texts: dict[str, str] = {}
    for case_key, pairs in sorted(dis.items()):
        ci = inputs[case_key]
        units: list[dict[str, object]] = []
        for ra, rb in pairs:
            first, second = (rb, ra) if option_order(ra.unit, ci.case.coding_id) else (ra, rb)
            units.append(
                {
                    "unit": ra.unit,
                    "field": ra.field,
                    "allowed_values": list(allowed_values(ra.field)),
                    "options": [
                        {
                            "value": r.value,
                            "evidence_ids": list(r.evidence_ids),
                            "excerpts": [{"evidence_id": e, "quote": q} for e, q in r.excerpts],
                        }
                        for r in (first, second)
                    ],
                }
            )
        at = str(ci.case.anchor.get("at", ""))[:16] + " UTC"
        text = adjudication_input(ci.case.coding_id, at, ci.items, units)
        texts[case_key] = text
        items.append(
            BatchItem(
                ref=ci.case.coding_id,
                input_text=text,
                namespace=NAMESPACE,
                evidence_id=ci.items[0].evidence_id if ci.items else None,
                case_ref=ci.case.coding_id,
            )
        )
    return items, texts


def run_adjudication(
    client: LLMClient,
    items: Sequence[BatchItem],
    *,
    brief_run_id: str,
    before_submit: BeforeSubmit | None,
    est_usd_per_call: float | None,
    wait_seconds: float | None,
    poll_seconds: float,
    sleep: Callable[[float], None] | None = None,
) -> PassRun:
    if not items:
        return PassRun()
    return run_pass(
        client,
        ADJUDICATOR,
        items,
        Adjudication,
        job=JOB_ADJUDICATION,
        brief_run_id=brief_run_id,
        before_submit=before_submit,
        est_usd_per_item=est_usd_per_call,
        timeout_seconds=wait_seconds,
        poll_seconds=poll_seconds,
        sleep=sleep,
    )


def finalize(
    inputs: Mapping[str, CaseInput],
    rows: Sequence[CodingRow],
    dis: Mapping[str, list[tuple[CodingRow, CodingRow]]],
    adj: PassRun,
    adj_texts: Mapping[str, str],
    batch_of: Mapping[tuple[str, str], str] | None = None,
) -> list[CodingRow]:
    """The adjudicator's rows and the final value of every unit of every case."""
    out: list[CodingRow] = []
    a, b = _by_unit(rows, "A"), _by_unit(rows, "B")
    decided: dict[tuple[str, str], CodingRow] = {}
    for case_key, pairs in dis.items():
        ci = inputs[case_key]
        res = adj.results.get(ci.case.coding_id)
        prov = _provenance(res, batch_of, ci.case.coding_id)
        wanted = {ra.unit: ra.field for ra, _rb in pairs}
        got: dict[str, Any] = {}
        if res is not None:
            for d in res.output.decisions:
                if d.unit in wanted and d.unit not in got:
                    got[d.unit] = d
        texts = blocks(adj_texts[case_key]) if res is not None else {}
        for unit, fld in sorted(wanted.items()):
            d = got.get(unit)
            if d is None:
                why = (
                    "missing_decision"
                    if res is not None
                    else adj.failed.get(ci.case.coding_id, "no_result")
                )
                row = CodingRow(
                    ci.case,
                    "adjudicator",
                    unit,
                    fld,
                    "unknown",
                    "unknown",
                    unknown_reason="conflicting_evidence",
                    detail={"failure": why},
                    provenance=prov,
                )
            elif d.value not in allowed_values(fld):
                row = CodingRow(
                    ci.case,
                    "adjudicator",
                    unit,
                    fld,
                    "unknown",
                    SCHEMA_INVALID,
                    unknown_reason=SCHEMA_INVALID,
                    detail={"failure": "value_not_allowed"},
                    provenance=prov,
                )
            else:
                cu = CodedUnit(
                    unit,
                    fld,
                    d.value,
                    tuple(d.evidence_ids),
                    tuple((e.evidence_id, e.quote) for e in d.excerpts),
                    d.unknown_reason,
                    d.confidence,
                )
                c = check(cu, texts)
                reason = d.reason.strip()[:MAX_REASON_CHARS]
                if PERSON_TOKEN.search(reason):
                    reason = "[reason withheld: it named a person]"
                row = _row(
                    ci.case,
                    "adjudicator",
                    c.unit,
                    c.status,
                    prov,
                    {"problems": list(c.problems)} if c.problems else {},
                )
                row = replace(row, reason=reason)
            out.append(row)
            decided[(case_key, unit)] = row
    claims: dict[str, str] = {}
    for key, ra in sorted(a.items()):
        rb = b.get(key)
        d = decided.get(key)
        if d is not None:
            final = replace(d, pass_="final", status="adjudicated")
        else:
            final = _agreed(ra, rb)
        if final.field == "novelty_claim":
            claims[key[0]] = final.value
        out.append(final)
    # novelty kinds follow the final novelty claim (§5.1): not applicable unless present
    fixed: list[CodingRow] = []
    for r in out:
        if (
            r.pass_ == "final"
            and r.field.startswith("novelty_kind.")
            and (claims.get(r.case.case_key) != "present")
        ):
            r = CodingRow(
                r.case,
                "final",
                r.unit,
                r.field,
                "not_applicable",
                "excluded",
                excluded="not_applicable",
                provenance=r.provenance,
            )
        fixed.append(r)
    for ci in inputs.values():
        for u in derived_units(ci.case.star_anomaly_flag):
            fixed.append(
                CodingRow(
                    ci.case,
                    "final",
                    u.unit,
                    u.field,
                    u.value,
                    "derived",
                    unknown_reason=u.unknown_reason,
                    excluded="derived",
                    detail={"source": "selection star_anomaly flag (§7.4)"},
                )
            )
    return fixed


def _agreed(ra: CodingRow, rb: CodingRow | None) -> CodingRow:
    """The final value of a unit without adjudication: A's validated value when A and B agree,
    or the non-excluded pass's value when the other is excluded (e.g. not applicable)."""
    src = ra
    if ra.excluded and rb is not None and not rb.excluded:
        src = rb
    status = "excluded" if src.excluded else "agreed"
    return replace(src, pass_="final", status=status)


# --- alpha ------------------------------------------------------------------------------------
def _pairs(rows: Sequence[CodingRow], fld_match: Callable[[str], bool]) -> list[tuple[Any, Any]]:
    a, b = _by_unit(rows, "A"), _by_unit(rows, "B")
    out = []
    for key in sorted(set(a) | set(b)):
        ra, rb = a.get(key), b.get(key)
        f = (ra or rb).field  # type: ignore[union-attr]
        if not fld_match(f):
            continue
        va = None if ra is None or ra.excluded else ra.value
        vb = None if rb is None or rb.excluded else rb.value
        out.append((va, vb))
    return out


def _binary(values: set[Any]) -> bool:
    return len(values) <= 2


def reliability_rows(
    rows: Sequence[CodingRow], n_cases: int, *, resamples: int = ka.BOOTSTRAP_RESAMPLES
) -> list[dict[str, Any]]:
    """Alpha per field (and C11a pooled), with the codebook's labels (module docstring)."""
    out: list[dict[str, Any]] = []
    specs: list[tuple[str, Callable[[str], bool], str]] = [
        (f.field, (lambda name: lambda x: x == name)(f.field), f.level) for f in FIELDS
    ]
    specs.append(
        ("pattern.*", lambda x: x.startswith("pattern.") and x != "pattern.MC-12", "nominal")
    )
    cf = {p: {"units": 0, "failed": 0, "schema_invalid": 0} for p in ("A", "B")}
    for r in rows:
        if r.pass_ in cf and not r.excluded:
            cf[r.pass_]["units"] += 1
            cf[r.pass_]["failed"] += 1 if r.status == "citation_failed" else 0
            cf[r.pass_]["schema_invalid"] += 1 if r.status == SCHEMA_INVALID else 0
    for name, match, level in specs:
        pairs = _pairs(rows, match)
        excluded = sum(1 for va, vb in pairs if va is None or vb is None)
        a_vals = [va for va, _ in pairs if va is not None]
        b_vals = [vb for _, vb in pairs if vb is not None]
        rates = {
            "unknown_a": _share(sum(v == "unknown" for v in a_vals), len(a_vals)),
            "unknown_b": _share(sum(v == "unknown" for v in b_vals), len(b_vals)),
            "citation_failed_a": _share(cf["A"]["failed"], cf["A"]["units"]),
            "citation_failed_b": _share(cf["B"]["failed"], cf["B"]["units"]),
            "schema_invalid_a": _share(cf["A"]["schema_invalid"], cf["A"]["units"]),
            "schema_invalid_b": _share(cf["B"]["schema_invalid"], cf["B"]["units"]),
        }
        disagree = sum(1 for va, vb in pairs if va is not None and vb is not None and va != vb)
        stats: list[tuple[str, list[tuple[Any, Any]], Any]] = []
        if level == "ordinal":
            order = FIELD_BY_NAME[name].order
            ordinal = [
                (None if va == "unknown" else va, None if vb == "unknown" else vb)
                for va, vb in pairs
            ]
            stats.append(("ordinal", ordinal, order))
            ku = [
                (None if va is None else va != "unknown", None if vb is None else vb != "unknown")
                for va, vb in pairs
            ]
            stats.append(("known_unknown", ku, None))
        else:
            stats.append(("nominal", pairs, None))
        for stat, units, order in stats:
            lv: ka.Level = "ordinal" if stat == "ordinal" else "nominal"
            a = ka.alpha(units, lv, order)
            pairable = ka.pairable(units)
            values = {v for vals in pairable for v in vals}
            reasons = []
            if len(pairable) < ka.MIN_PAIRABLE:
                reasons.append(f"n_pairable {len(pairable)} < {ka.MIN_PAIRABLE}")
            if not ka.expected_disagreement_positive(units):
                reasons.append("expected disagreement 0 (one value only): alpha undefined")
            if _binary(values) and len(values) == 2:
                counts = [sum(v == x for vals in pairable for v in vals) for x in values]
                if min(counts) < ka.MIN_RARER_BINARY:
                    reasons.append(f"rarer value {min(counts)} < {ka.MIN_RARER_BINARY}")
            assessed = not reasons
            labels = [PILOT_LABEL.format(n=n_cases), LLM_ONLY]
            if not assessed:
                labels.append("reliability not assessed")
            if a is not None and a < ka.LOW_RELIABILITY_BELOW:
                labels.append("low reliability")
            if stat == "known_unknown" and not any(v is False for vals in pairable for v in vals):
                labels.append("1.0 (degenerate: no unknowns)")
            ci = ka.bootstrap_ci(units, lv, order, resamples=resamples) if a is not None else None
            out.append(
                {
                    "field": name,
                    "statistic": stat,
                    "alpha": None if a is None else round(a, 6),
                    "n_cases": n_cases,
                    "n_units": len(pairs),
                    "n_pairable": len(pairable),
                    "n_excluded": excluded,
                    "disagreements": disagree,
                    "raw_agreement": ka.raw_agreement(units),
                    "ci": ci.to_dict() if ci is not None else {},
                    "assessed": assessed,
                    "labels": labels,
                    "reason": "; ".join(reasons) or None,
                    "rates": rates,
                    "alpha_version": ka.ALPHA_VERSION,
                }
            )
    return out


def _share(k: int, n: int) -> float | None:
    return round(k / n, 4) if n else None


def link_cache(
    client: LLMClient,
    prompt: PromptSpec,
    schema: type[Any],
    job: str,
    run: PassRun,
    inputs: Mapping[str, CaseInput],
) -> int:
    """Link each cached coding to every evidence item of its case (CB-05): the cached output is
    purged with any of them (a repo opt-out, retention)."""
    from pigtail.llm.types import schema_hash

    by_id = {ci.case.coding_id: ci for ci in inputs.values()}
    n = 0
    for cid, res in run.results.items():
        ci = by_id.get(cid)
        if ci is None:
            continue
        key = client.cache_key(
            res.backend, client.model_for(job), prompt, schema_hash(schema), res.input_hash
        )
        for it in ci.items:
            client.store.link_evidence(key, it.evidence_id)
            n += 1
    return n

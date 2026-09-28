"""The pilot's private reports and the counts-only lines for the ops files (ADR-073.1, ADR-086).

`PIGTAIL_DATA_DIR/reports/<brief_id>/v<version>/pilot-<date>.{json,md}` (and `decay-<date>…`):
the per-case coded fields with citations, per-field alpha and disagreements, the gaps, and the
cost report with case detail. **Never in git**: `ensure_private` refuses a directory inside a
git working tree unless git ignores it (the default `data/` is ignored), writes with mode 0600
in 0700 directories, atomically.

`ops_lines` prints what the orchestrator may paste into `ops/COSTS.md` and `ops/STATUS.md`:
totals and per-case averages only; no repo, case, brief id or excerpt.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from typing import Any


class NotPrivate(ValueError):
    """The report directory could end up in a git repository."""


def _worktree(path: Path) -> Path | None:
    for p in (path, *path.parents):
        if (p / ".git").exists():
            return p
    return None


def ensure_private(directory: Path) -> Path:
    """Resolve `directory` and refuse it when it lies in a git working tree and git does not
    ignore it (a report there could be committed; the repo is public)."""
    d = directory.expanduser().resolve()
    wt = _worktree(d)
    if wt is None:
        return d
    try:
        r = subprocess.run(
            ["git", "-C", str(wt), "check-ignore", "-q", "--no-index", str(d / "x.json")],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise NotPrivate(f"{d} is inside a git working tree and git could not check it") from e
    if r.returncode != 0:
        raise NotPrivate(
            f"{d} is inside the git working tree {wt} and not ignored by git: reports stay in "
            "the instance's private data directory (ADR-073.1); set PIGTAIL_DATA_DIR outside "
            "the repository or to an ignored path"
        )
    return d


def report_dir(data_dir: Path, brief_id: str, version: int) -> Path:
    return ensure_private(Path(data_dir) / "reports" / brief_id / f"v{version}")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    for p in (path.parent, path.parent.parent, path.parent.parent.parent):
        if p.exists():
            os.chmod(p, 0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_report(
    data_dir: Path,
    brief_id: str,
    version: int,
    name: str,
    report: dict[str, Any],
    md: str,
    *,
    day: date,
) -> dict[str, str]:
    """Write `<name>-<day>.json` and `.md`; returns their paths."""
    d = report_dir(data_dir, brief_id, version)
    j = d / f"{name}-{day.isoformat()}.json"
    m = d / f"{name}-{day.isoformat()}.md"
    _write(j, json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    _write(m, md)
    return {"json": str(j), "md": str(m)}


def _usd(v: Any) -> str:
    return "unknown" if v is None else f"USD {float(v):,.4f}"


def pilot_markdown(r: dict[str, Any]) -> str:
    """A readable version of the pilot report (private)."""
    p = r["provenance"]
    lines = [
        f"# Pilot report — brief {p['brief_id']} v{p['brief_version']} ({p['date']})",
        "",
        "Private (ADR-073.1): never commit, publish or share this file.",
        "",
        "## Provenance",
        *[f"- {k}: {v}" for k, v in p.items()],
        "",
    ]
    outcome = r.get("outcome") or {}
    if outcome.get("status") == "failed":
        lines += [
            "## Outcome: FAILED — coding failed for every case",
            "",
            "No cost model, projection or alpha was computed (nothing was measured). Errors:",
            *[
                f"- {e['error']} ({e['requests']} request(s))"
                for e in (outcome.get("stop") or {}).get("errors", [])
            ],
            "",
            "Fix the cause and run the same command again: the failed coding is redone.",
            "",
        ]
    lines += ["## Cases"]
    for c in r["cases"]:
        lines += [
            "",
            f"### {c['position']}. {c['coding_id']} — view {c['view']}, role {c['role']}, "
            f"pair {c['pair_id']}: {c['repo']}",
            f"Anchor: {c['anchor'].get('at')} ({c['anchor'].get('type')}, "
            f"{c['anchor'].get('source')})",
            f"Evidence items: {len(c['evidence'])}; gaps: "
            + ", ".join(f"{g['source']} ({g['reason']})" for g in c["gaps"]),
            *[
                f"Coding failed, pass {ps}: {e.get('type')}: {e.get('message') or '-'}"
                for ps, e in sorted((c.get("coding_errors") or {}).items())
            ],
            "",
            "| unit | final | A | B | status | citation |",
            "|---|---|---|---|---|---|",
        ]
        for u in c["units"]:
            cite = "; ".join(f"{e['evidence_id']}: “{e['quote']}”" for e in u["excerpts"])
            lines.append(
                f"| {u['unit']} | {u['final']} | {u['A']} | {u['B']} | {u['status']} | "
                f"{cite.replace('|', '/')} |"
            )
    lines += [
        "",
        "## Reliability (Krippendorff's alpha, passes A and B, before adjudication)",
        "",
        "| field | statistic | alpha | 95 % CI | n pairable | disagreements | labels |",
        "|---|---|---|---|---|---|---|",
    ]
    for a in r["reliability"]:
        ci = a.get("ci") or {}
        lines.append(
            f"| {a['field']} | {a['statistic']} | "
            f"{'undefined' if a['alpha'] is None else round(a['alpha'], 3)} | "
            f"{ci.get('low')}..{ci.get('high')} | {a['n_pairable']} | {a['disagreements']} | "
            f"{', '.join(a['labels'])} |"
        )
    cost = r["cost"]
    lines += [
        "",
        "## Cost (actual, per case and stage)",
        "",
        "| case | coder A | coder B | adjudication | total | GitHub requests |",
        "|---|---|---|---|---|---|",
    ]
    for cid, c in cost["per_case"].items():
        st = c["stages"]
        lines.append(
            f"| {cid} | {_usd(st.get('coder_a', {}).get('usd'))} | "
            f"{_usd(st.get('coder_b', {}).get('usd'))} | "
            f"{_usd(st.get('adjudication', {}).get('usd'))} | {_usd(c['usd'])} | "
            f"{c['github_requests']} |"
        )
    pr = r["projection"]
    by_stage = cost.get("per_case_usd_by_stage") or {}
    model_pc = cost.get("cost_model_per_case_usd") or {}
    lines += [
        "",
        f"Total {_usd(cost['total_usd'])}; per case {_usd(cost['per_case_usd'])} = "
        + " + ".join(f"{k} {_usd(v)}" for k, v in by_stage.items())
        + f" (actual: {cost.get('basis', 'every billed ledger row')}).",
        *(
            [
                "",
                "Cost model per case (the projection's basis: calls of the current settings "
                f"only, case-cost-v3): {_usd(model_pc.get('total'))} = coder A "
                f"{_usd(model_pc.get('coder_a'))} + coder B {_usd(model_pc.get('coder_b'))} + "
                f"adjudication {_usd(model_pc.get('adjudication'))}.",
            ]
            if model_pc
            else []
        ),
        "",
        "## Projection for the full brief",
        *[f"- {k}: {v}" for k, v in pr.items()],
        "",
        (
            "**H6: the projection exceeds the brief's cap. Stop: spending above it needs the "
            "owner's approval.**"
        )
        if pr.get("h6")
        else (
            f"No projection: {pr['skipped']}." if pr.get("skipped") else "Within the brief's cap."
        ),
        "",
        "## Evidence decay",
        f"Checks scheduled at +1, +7 and +30 days: {r['decay']['scheduled']} "
        "(`pigtail brief decay <id>`; the +30-day results complete later).",
        "",
        "## Limitations",
        *[f"- {x}" for x in r["limitations"]],
        "",
    ]
    return "\n".join(lines)


def ops_lines(summary: dict[str, Any], *, label: str, month: str) -> dict[str, str]:
    """Counts-only lines for `ops/COSTS.md` and `ops/STATUS.md` (no names, ids or excerpts)."""
    c = summary.get("cost") or {}
    pr = summary.get("projection") or {}
    rel = summary.get("reliability") or {}
    n = summary.get("cases") or 0
    per = c.get("per_case_usd_by_stage") or {}

    def u(v: Any) -> str:
        return "unknown" if v is None else f"{float(v):.4f}"

    contingency = (
        f" (with ×{pr.get('contingency_factor')} contingency USD "
        f"{u(pr.get('projected_total_with_contingency_usd'))})"
        if pr.get("projected_total_with_contingency_usd") is not None
        else ""
    )
    costs = (
        f"| {month} | pilot {label}: {n} cases, API USD {u(c.get('total_usd'))} "
        f"(per case avg USD {u(c.get('per_case_usd'))}: coder A {u(per.get('coder_a'))}, "
        f"coder B {u(per.get('coder_b'))}, adjudication {u(per.get('adjudication'))}; "
        f"{c.get('mode', 'batch')}; GitHub requests avg {c.get('github_per_case', '?')}/case) | "
        + (
            "no projection (no measured cost) |"
            if pr.get("skipped")
            else f"projection {pr.get('full_brief_cases', '?')} cases: USD "
            f"{u(pr.get('projected_total_usd'))}{contingency} of cap USD {u(pr.get('cap_usd'))}"
            f"{' — H6' if pr.get('h6') else ''} |"
        )
    )
    pooled = pooled_wording(rel.get("pooled_patterns"))
    status = (
        f"- Pilot ({label}): {n} cases double-coded and adjudicated; "
        + (f"{pooled}; " if pooled else "")
        + "per-field alpha on "
        f"{rel.get('fields', 0)} fields ({rel.get('below_070', 0)} below 0.70, "
        f"{rel.get('undefined', 0)} undefined, "
        f"{rel.get('statistics', 0) - rel.get('assessed', 0)} of {rel.get('statistics', 0)} "
        f"statistics 'reliability not assessed'; all labelled 'pilot, n = {n}'); API USD "
        f"{u(c.get('total_usd'))} total, USD "
        f"{u(c.get('per_case_usd'))} per case; full-brief projection USD "
        f"{u(pr.get('projected_total_usd'))}{contingency} of the USD {u(pr.get('cap_usd'))} cap"
        + (" — **H6: stop, over the cap**" if pr.get("h6") else " (within the cap)")
        + f"; evidence decay scheduled for {summary.get('decay_scheduled', 0)} checks "
        "(+1/+7/+30 days). Case detail stays in the private report."
    )
    return {"COSTS.md": costs, "STATUS.md": status}


def pooled_wording(p: dict[str, Any] | None) -> str:
    """The pooled C11a statistic in words for STATUS: 'assessed (pooled over N cases)' when
    it is assessed, else 'not assessed' with the reason; the CI says what it resampled."""
    if not p:
        return ""
    a = p.get("alpha")
    alpha = "undefined" if a is None else f"{float(a):.2f}"
    lo, hi = p.get("ci_low"), p.get("ci_high")
    ci = (
        f"; 95 % CI {lo}..{hi}, {p.get('ci_resampled', 'units')} resampled"
        if lo is not None and hi is not None
        else ""
    )
    n_cases = p.get("n_cases")
    state = (
        f"assessed (pooled over {n_cases} cases)"
        if p.get("assessed")
        else f"reliability not assessed ({p.get('reason') or 'n too small'})"
    )
    return f"pooled pattern-seed α {alpha} (n {p.get('n_pairable')} units{ci}), {state}"

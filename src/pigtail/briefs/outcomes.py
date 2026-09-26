"""Outcome inputs of a final shortlist for the selection stage (PRD R3.1–R3.4, R4.3, R4.8,
R18.8; outcome-model v2.1 §1–§4, §5.6, §7; ADR-032.3, ADR-070, ADR-077).

Two steps, both project-level (repo names and ids, daily star counts; no identities):

1. **`fetch_outcome_data`** (network, GitHub only): for every repo on the final shortlist, fill
   missing project metadata (GitHub id, creation date, language; one GraphQL query per 50 repos,
   for repos added by URL in the review) and fetch its **star history**
   (`pigtail.capture.star_history`, ETag-conditional, 30 weeks per page, back to 60 days before
   the brief's window or the repo's creation week). Progress is checkpointed per repo; the GitHub
   request budget pauses the stage (resumable), and a repo the API can't serve (deleted,
   renamed) is recorded and left `unknown`.

2. **`load_inputs`** (database only): one `selection.CaseInput` per shortlisted repo.
   - **Anchor T** (§2.2): the first Show HN launch post the discovery stage recorded (hour
     precision, project-level signal) and the first `velocity-v0` burst on the star-history days
     inside the brief's window: a launch in `[T_burst − 30 d, T_burst]` wins; a launch with no
     burst in the 90 days after it (and before the first burst) wins; else the burst; else the
     launch; else no anchor. Without any star history the burst rule can't be checked and a
     launch is used with that note.
   - **Values** (§1, §7): `att.stars@30/@90` = raw net stars over the `k` endpoint days from the
     first day (§1.2 day mapping), `pending` until `T + k + settle_lag (3 d)` has passed,
     `unknown` when a day is missing, labelled "unfiltered, anomaly-checked";
     `att.hn_points` = the highest points of a recorded Show HN post from `T − 7 d` on (as of
     fetch). **Every other metric is `unknown` with reason `no_connector`**: registry downloads,
     dependents, PR-based community metrics and the business signals have no connector yet
     (outcome-model §7), and nothing is imputed (R18.8).
   - **Covariates** (§5.6): LSM from the first 2 endpoint days, launch quarter and half-year of
     T (UTC), repo age at T from the creation date, primary language (current, not at T), launch
     type (`show_hn` or `burst`), and the founder audience band, which is `unknown` for every
     candidate until the audience proxy is built and cleared (O14; `unknown` is matched as its
     own level).
   - **Star anomaly flag** (§4.3): `pigtail.analysis.anomaly.check_population` over the
     anchored field and reference candidates, on the endpoint days `[T − 60 d, T + k_max)` with
     the `forks` channel where per-repo event counts exist (tracked projects); issues, downloads
     and mentions have no daily source for retrospective candidates, so many spikes are
     `unchecked` and the flag `unknown`, which is reported, never read as clean.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

from pigtail.analysis.anomaly import check_population
from pigtail.analysis.bursts import segment
from pigtail.analysis.params import ANOMALY, STAR_HISTORY_DAY_TZ
from pigtail.briefs.candidates import Candidate, CandidateStore
from pigtail.briefs.model import METRICS, Brief
from pigtail.briefs.selection import (
    BUSINESS_COUNT,
    BUSINESS_SIGNALS,
    Anchor,
    AnomalyFlag,
    CaseInput,
    Covariates,
    Definition,
    SelectionError,
    Value,
)
from pigtail.briefs.shortlist import Shortlist

SETTLE_LAG_DAYS = 3  # outcome-model §1.1: a fixed measurement default for every source
BASELINE_BEFORE_DAYS = 60  # anomaly input window starts at T - 60 d (§4.2)
STAR_WEEKS_PER_PAGE = 30
DAY = timedelta(days=1)
STAR_LABEL = ANOMALY.label
NO_CONNECTOR = Value("unknown", reason="no_connector")


def shortlisted(conn: psycopg.Connection[Any], brief: Brief) -> list[Candidate]:
    """The repos on the brief version's **final** shortlist (R4.7), refused repos left out."""
    sl = Shortlist(conn, brief)
    st = sl.status()
    if st is None or st["status"] != "final":
        raise SelectionError(
            f"the shortlist of {brief.brief_id} v{brief.version} is not final: finalize it first "
            f"(pigtail brief shortlist finalize {brief.brief_id})"
        )
    dec = sl.latest()
    return [
        c
        for c in sl.candidates.all()
        if c.repo_full_name is not None
        and not sl.refused(c.repo_full_name, c.repo_host_id)
        and sl.included(c, dec.get(c.ref)) is True
    ]


# --- 1. fetch ----------------------------------------------------------------------------------
@dataclass
class FetchResult:
    repos: int = 0
    fetched: int = 0
    already_done: int = 0
    metadata_filled: int = 0
    failed: dict[str, int] = field(default_factory=dict)  # reason -> count (no names)
    pages: int = 0
    evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d.pop("evidence_ids")
        return d


def star_pages(window_start: date, as_of: date) -> int:
    """Pages of 30 weeks that reach 60 days before the window's start (the anomaly baseline)."""
    weeks = (as_of - (window_start - timedelta(days=BASELINE_BEFORE_DAYS + 1))).days // 7 + 2
    return max(1, min(100, math.ceil(weeks / STAR_WEEKS_PER_PAGE)))


def fetch_outcome_data(
    conn: psycopg.Connection[Any],
    brief: Brief,
    github: Any,
    cands: Sequence[Candidate],
    *,
    window_start: date,
    as_of: date,
    checkpoint: dict[str, Any],
    save: Callable[[dict[str, Any]], None],
    brief_run_id: str | None,
    now: datetime,
    recorder: Any = None,
) -> FetchResult:
    """Step 1 (module docstring). `BudgetExhausted` propagates: the stage pauses, resumable."""
    from pigtail.briefs.discovery import _meta_from_graphql
    from pigtail.capture.db import CaptureDB
    from pigtail.capture.star_history import fetch_star_history
    from pigtail.connectors.base import FetchError

    assert brief.version is not None
    res = FetchResult(repos=len(cands))
    if github is None or not getattr(github, "enabled", True):
        res.failed["no_github_connector"] = len(cands)
        return res
    db = CaptureDB(conn)
    store = CandidateStore(conn, brief.brief_id, brief.version)
    done: set[str] = set(checkpoint.get("star_history_done") or [])
    failed: dict[str, str] = dict(checkpoint.get("star_history_failed") or {})
    need = sorted(
        c.repo_full_name
        for c in cands
        if c.repo_full_name
        and c.ref not in done
        and (c.repo_host_id is None or not c.metadata.get("created_at"))
    )
    if need and not checkpoint.get("metadata_done"):
        meta = github.repos_metadata(need)
        for name in need:
            m = meta.get(name)
            if m is None:
                continue
            store.upsert(
                Candidate(
                    ref=f"gh:{name}",
                    repo_full_name=name,
                    repo_host_id=m.host_id,
                    metadata=_meta_from_graphql(m),
                ),
                brief_run_id=brief_run_id,
                now=now,
            )
            res.metadata_filled += 1
        checkpoint["metadata_done"] = True
        save(checkpoint)
    fresh = {c.ref: c for c in store.all()}
    pages = star_pages(window_start, as_of)
    for ref in sorted(c.ref for c in cands):
        if ref in done:
            res.already_done += 1
            continue
        c = fresh.get(ref)
        if c is None or c.repo_host_id is None or c.repo_full_name is None:
            failed[ref] = "no_repo_id"
        else:
            try:
                r = fetch_star_history(
                    github,
                    db,
                    c.repo_host_id,
                    c.repo_full_name,
                    per_page=STAR_WEEKS_PER_PAGE,
                    max_pages=pages,
                    run=recorder,
                )
            except FetchError as e:
                failed[ref] = type(e).__name__
            else:
                res.fetched += 1
                res.pages += r.pages
                res.evidence_ids.extend(r.evidence_ids)
                failed.pop(ref, None)
        done.add(ref)
        checkpoint["star_history_done"] = sorted(done)
        checkpoint["star_history_failed"] = dict(sorted(failed.items()))
        save(checkpoint)
    for reason in failed.values():
        res.failed[reason] = res.failed.get(reason, 0) + 1
    return res


# --- 2. load -----------------------------------------------------------------------------------
def endpoint_day(t: datetime, tz: str = STAR_HISTORY_DAY_TZ) -> date:
    """D(t): the star-history endpoint day containing the instant t (outcome-model §1.2)."""
    return t.astimezone(ZoneInfo(tz)).date()


def first_day(anchor: Anchor) -> date:
    """First endpoint day of `[T, T+k)`: D(T) for hour precision; T's UTC date for day
    precision (a dated launch without a time); the onset day for a burst."""
    if anchor.precision == "day" and anchor.type == "launch":
        return anchor.at.astimezone(UTC).date()
    return endpoint_day(anchor.at)


def star_series(conn: psycopg.Connection[Any], host_id: int, as_of: date) -> dict[date, int]:
    """Ended endpoint days of `repo_star_daily` up to `as_of` (partial days left out)."""
    rows = conn.execute(
        "SELECT day, stars_net FROM repo_star_daily WHERE repo_host_id = %s AND day <= %s"
        " AND NOT is_partial ORDER BY day",
        (host_id, as_of),
    ).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def fork_series(conn: psycopg.Connection[Any], host_id: int) -> dict[date, int]:
    rows = conn.execute(
        "SELECT day, forks_seen FROM repo_event_daily_agg WHERE repo_host_id = %s ORDER BY day",
        (host_id,),
    ).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def _launches(c: Candidate, start: datetime, end: datetime) -> list[tuple[datetime, int, int]]:
    """(time, hn item id, points) of the Show HN posts discovery recorded, inside the window."""
    out: list[tuple[datetime, int, int]] = []
    for s in c.sources:
        if s.get("source") != "show_hn" or not s.get("time"):
            continue
        t = datetime.fromisoformat(str(s["time"]))
        if t.tzinfo is None:
            t = t.replace(tzinfo=UTC)
        if start <= t <= end:
            out.append((t, int(s.get("hn_item_id") or 0), int(s.get("points") or 0)))
    return sorted(out)


def choose_anchor(
    launches: Sequence[datetime], bursts: Sequence[tuple[datetime, date]], has_series: bool
) -> tuple[Anchor | None, str | None]:
    """outcome-model §2.2 (module docstring). `bursts`: (onset instant, onset day), in order."""
    tb = bursts[0][0] if bursts else None
    if tb is not None:
        near = [t for t in launches if tb - timedelta(days=30) <= t <= tb]
        if near:
            return Anchor("launch", near[0], "hour", "show_hn"), None
    if launches:
        tl = launches[0]
        later = any(tl <= b < tl + timedelta(days=90) for b, _ in bursts)
        if tb is None or (tl < tb and not later):
            note = None if has_series else "no_star_history: burst rule not checked"
            return Anchor("launch", tl, "hour", "show_hn"), note
    if tb is not None:
        return Anchor("burst", tb, "day", "velocity-v0"), None
    return None, "no_anchor" if has_series else "no_anchor: no launch post and no star history"


def star_value(series: Mapping[date, int], first: date, k: int, as_of: date) -> Value:
    """`att.stars@k` (outcome-model §1.2): raw net stars over k endpoint days."""
    if as_of < first + timedelta(days=k + SETTLE_LAG_DAYS):
        return Value("pending", reason="horizon_not_reached", tag="unknown")
    days = [first + timedelta(days=i) for i in range(k)]
    if not series:
        return Value("unknown", reason="no_star_history")
    if any(d not in series for d in days):
        return Value("unknown", reason="incomplete_series")
    return Value("observed", float(sum(series[d] for d in days)), "verified", STAR_LABEL)


def _star_horizon_max(d: Definition) -> int:
    dims = {d.primary, *d.floors, *(d.weights or {})}
    ks = [int(d.metrics[x].split("@")[1]) for x in dims if d.metrics[x].startswith("att.stars@")]
    return max(ks, default=30)


def _half_year(t: datetime) -> str:
    u = t.astimezone(UTC)
    return f"{u.year}H{1 if u.month <= 6 else 2}"


def _quarter(t: datetime) -> int:
    u = t.astimezone(UTC)
    return u.year * 4 + (u.month - 1) // 3


def _created(c: Candidate) -> date | None:
    raw = c.metadata.get("created_at")
    if not raw:
        return None
    t = datetime.fromisoformat(str(raw))
    return (t if t.tzinfo else t.replace(tzinfo=UTC)).astimezone(UTC).date()


def load_inputs(
    conn: psycopg.Connection[Any],
    brief: Brief,
    cands: Sequence[Candidate],
    *,
    window: tuple[datetime, datetime],
    as_of: date,
) -> list[CaseInput]:
    """Step 2 (module docstring)."""
    definition = Definition.from_brief(brief)
    k_max = _star_horizon_max(definition)
    start, end = window
    prepared: list[dict[str, Any]] = []
    for c in sorted(cands, key=lambda x: x.ref):
        series = star_series(conn, c.repo_host_id, as_of) if c.repo_host_id is not None else {}
        created = _created(c)
        launches = _launches(c, start, end)
        bursts: list[tuple[datetime, date]] = []
        if series:
            lo = max(start.date(), min(series))
            hi = min(end.date(), as_of)
            if lo <= hi:
                seg = segment(series, lo, hi, created=created)
                bursts = [(b.onset.at, b.onset.day) for b in seg.bursts]
        anchor, reason = choose_anchor([t for t, _, _ in launches], bursts, bool(series))
        prepared.append(
            {
                "c": c,
                "series": series,
                "created": created,
                "launches": launches,
                "anchor": anchor,
                "reason": reason,
            }
        )

    # anomaly checks over the anchored field and reference candidates (§4.2)
    anomaly_in: dict[str, tuple[dict[date, int], dict[str, dict[date, int]]]] = {}
    windows: dict[str, tuple[date, date]] = {}
    for p in prepared:
        c, a = p["c"], p["anchor"]
        if a is None or c.panel == "exemplar" or not p["series"]:
            continue
        f = first_day(a)
        w0, w1 = f - timedelta(days=BASELINE_BEFORE_DAYS), f + timedelta(days=k_max - 1)
        days = [w0 + timedelta(days=i) for i in range((w1 - w0).days + 1)]
        if any(d not in p["series"] for d in days):
            continue
        stars = {d: p["series"][d] for d in days}
        forks = fork_series(conn, c.repo_host_id) if c.repo_host_id is not None else {}
        act = {"forks": {d: v for d, v in forks.items() if w0 <= d <= w1}}
        anomaly_in[c.ref] = (stars, {k: v for k, v in act.items() if v})
        windows[c.ref] = (w0, w1)
    reports = check_population(anomaly_in)

    out: list[CaseInput] = []
    for p in prepared:
        c, a, series = p["c"], p["anchor"], p["series"]
        values: dict[str, Value] = {}
        business = dict.fromkeys(BUSINESS_SIGNALS, NO_CONNECTOR)
        cov = Covariates(language=c.metadata.get("language"))
        flag: AnomalyFlag = "unknown"
        anomaly: dict[str, Any] = {"label": STAR_LABEL, "status": "not_checked"}
        if a is None:
            no = Value("unknown", reason="no_anchor")
            values = {m: no for dim in METRICS for m in METRICS[dim]}
        else:
            f = first_day(a)
            for m in (*METRICS["attention"], *METRICS["adoption"], *METRICS["community"]):
                values[m] = NO_CONNECTOR
            values[BUSINESS_COUNT] = NO_CONNECTOR
            for k in (30, 90):
                values[f"att.stars@{k}"] = star_value(series, f, k, as_of)
            pts = [pt for t, _, pt in p["launches"] if t >= a.at - timedelta(days=7)]
            values["att.hn_points"] = (
                Value("observed", float(max(pts)), "verified", "as of fetch")
                if pts
                else Value("unknown", reason="no_matched_story_captured")
            )
            lsm = None
            if f in series and f + DAY in series:
                lsm = math.log10(1 + max(0, series[f] + series[f + DAY]))
            age = None
            if p["created"] is not None:
                age = math.log10(max(1, (a.at.astimezone(UTC).date() - p["created"]).days))
            cov = Covariates(
                lsm=lsm,
                launch_quarter=_quarter(a.at),
                launch_half_year=_half_year(a.at),
                age_log10=age,
                audience_band="unknown",
                language=c.metadata.get("language"),
                launch_type="show_hn" if a.type == "launch" else "burst",
            )
            rep = reports.get(c.ref)
            if rep is not None:
                w0, w1 = windows[c.ref]
                case_from = f - timedelta(days=30)
                spikes = [s for s in rep.spikes if s.end >= case_from and s.start <= w1]
                flags = [
                    fl
                    for fl in rep.flags
                    if fl.kind == "ratio_outlier"
                    or (fl.end is not None and fl.start is not None and fl.end >= case_from)
                ]
                checked = all(s.checked for s in spikes)
                ratio_ok = (
                    rep.ratio is not None and rep.stars_total >= ANOMALY.ratio_min_stars
                ) or rep.stars_total < ANOMALY.ratio_min_stars
                flag = "true" if flags else ("false" if checked and ratio_ok else "unknown")
                anomaly = {
                    "label": STAR_LABEL,
                    "rule_version": rep.rule_version,
                    "params_version": rep.params_version,
                    "status": rep.status,
                    "flags": [
                        {"kind": fl.kind, "start": _iso(fl.start), "end": _iso(fl.end)}
                        for fl in flags
                    ],
                    "spikes_in_window": len(spikes),
                    "ratio": None if rep.ratio is None else round(rep.ratio, 4),
                    "stars_total": rep.stars_total,
                    "window": [w0.isoformat(), w1.isoformat()],
                    "channels": sorted(anomaly_in[c.ref][1]),
                }
            elif c.panel != "exemplar":
                anomaly = {"label": STAR_LABEL, "status": "no_star_history_for_window"}
        out.append(
            CaseInput(
                ref=c.ref,
                panel=c.panel,
                distance=c.distance if c.distance is not None else 0,
                named_index=c.named_index,
                anchor=a,
                anchor_reason=p["reason"],
                values=values,
                business=business,
                covariates=cov,
                star_anomaly_flag=flag,
                anomaly=anomaly,
            )
        )
    return out


def _iso(d: date | None) -> str | None:
    return None if d is None else d.isoformat()

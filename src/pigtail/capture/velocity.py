"""GH Archive star/fork velocity scan and case opening (R1.1, M1-T3).

Pipeline (`VelocityScanner.scan(start, end)`, hours are UTC, `end` exclusive):

1. The range is split into **chunks** = UTC day ∩ [start, end). For each hour of a chunk the
   hourly dump is fetched through `GHArchiveConnector` (snapshotted before parsing), or re-read
   from the snapshot store if that hour was scanned before (no network). A 404 marks the hour
   `missing`.
2. Records (already pseudonymized, bots flagged by login - see `pigtail.capture.botfilter`) are
   aggregated per repo per hour. Per-actor features for the lockstep filter are kept in memory,
   keyed by pseudonym, for the chunk only; they are never persisted.
3. The chunk's rows replace any previous rows for those hours in `repo_hourly_activity`
   (raw, bot, lockstep and filtered counts) and each hour is recorded in `gharchive_hours`.
   A chunk already scanned with the same filter window and bot-filter version is skipped
   unless `force=True`. Chunks commit atomically, so an interrupted scan resumes cleanly.
4. **Detection** runs for every hour `t` in the range. A repo fires when

   - `stars_48h` (bot-filtered stars in the 48 hourly buckets ending with `t`) ≥ `min_stars_48h`
     (default 100), **and**
   - `z = (stars_48h - baseline_mean_48h) / sigma_used ≥ sigma` (default 3).

   The baseline is the 30 days (720 hourly buckets) before the 48 h window. Only hours present
   in `gharchive_hours` with status `ok` count as observed; missing or never-scanned hours are
   *unknown*, not zero:

   - `baseline_mean_48h` = mean stars per observed hour x 48.
   - `baseline_std_48h` = sample std of the 15 non-overlapping 48 h block sums, using only blocks
     with ≥ 90 % observed hours (scaled to 48 h), and only if ≥ 3 such blocks; else 0.
   - `sigma_used = max(baseline_std_48h, sqrt(baseline_mean_48h), min_sigma)`: the Poisson
     floor stops a flat baseline (std ≈ 0) from firing on small wobbles; `min_sigma` (1.0)
     handles zero baselines.
   - `baseline_quality`: `full` (≥ 90 % of 720 h observed), `partial` (some), `none` (no
     observed hours: a new repo or a scan with no history). With `none` the rule reduces to the
     absolute threshold; the case records that so reviewers can tell.

   Case opening is idempotent: case ids are deterministic (repo, trigger, opened_at) and no
   velocity case is opened within `cooldown_hours` (default 14 days) of an existing one for the
   same repo, so re-running the same hours never duplicates cases.

**Coverage caveat.** Community reports suggest GH Archive may under-capture WatchEvents
(possibly heavily) in recent periods. All thresholds are therefore "as observed in GH Archive"
and may need recalibration; every case records a `coverage` block (source, window, observed
stars, and reference stars/ratio when a `CandidateConfirmer` - e.g. the GitHub stargazers API,
a later task - has checked the candidate). The star series itself is pluggable
(`StarSeriesSource`). GH Archive has no unstar events, so "net stars" = stars observed.
"""

from __future__ import annotations

import logging
import math
import statistics
import time
from collections.abc import Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from psycopg.types.range import Range

from pigtail.capture.botfilter import BOT_FILTER_VERSION
from pigtail.capture.db import CaptureDB
from pigtail.capture.models import (
    BaselineQuality,
    Case,
    Coverage,
    Repo,
    VelocityDetection,
    case_id,
    repo_id,
)
from pigtail.capture.runs import RunRecorder
from pigtail.capture.snapshots import SnapshotMeta, SnapshotNotFound
from pigtail.connectors.base import NotFound, Record
from pigtail.connectors.gharchive import GHArchiveConnector, hour_url

log = logging.getLogger(__name__)

RULE_VERSION = "velocity-v0"
HOUR = timedelta(hours=1)


@dataclass(frozen=True)
class VelocityConfig:
    min_stars_48h: int = 100
    sigma: float = 3.0
    window_hours: int = 48
    baseline_days: int = 30
    min_sigma: float = 1.0
    full_block_coverage: float = 0.9
    full_baseline_coverage: float = 0.9
    min_full_blocks: int = 3
    lockstep_min_stars: int = 20
    lockstep_share: float = 0.9
    cooldown_hours: int = 14 * 24

    @property
    def baseline_hours(self) -> int:
        return self.baseline_days * 24


def floor_hour(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def hours_between(start: datetime, end: datetime) -> list[datetime]:
    out, h = [], floor_hour(start)
    while h < end:
        out.append(h)
        h += HOUR
    return out


def day_chunks(start: datetime, end: datetime) -> Iterator[tuple[datetime, datetime]]:
    """Split [start, end) at UTC midnights."""
    cur = floor_hour(start)
    while cur < end:
        nxt = min(end, (cur + timedelta(days=1)).replace(hour=0))
        yield cur, nxt
        cur = nxt


# --- aggregation -----------------------------------------------------------------------------
@dataclass
class HourlyRow:
    repo_host_id: int
    hour: datetime
    repo_name: str
    stars_raw: int = 0
    stars_bot: int = 0
    stars_lockstep: int = 0
    stars_filtered: int = 0
    forks_raw: int = 0
    forks_filtered: int = 0
    lockstep_flag: bool = False


@dataclass
class _Cell:
    repo_name: str
    stars_raw: int = 0
    stars_bot: int = 0
    forks_raw: int = 0
    forks_bot: int = 0
    stargazers: set[str] = field(default_factory=set)


class WindowAggregator:
    """Per repo-hour counts plus per-pseudonym activity features for one filter window."""

    def __init__(self, cfg: VelocityConfig) -> None:
        self.cfg = cfg
        self.cells: dict[tuple[datetime, int], _Cell] = {}
        self.other_activity: set[str] = set()  # pseudonyms with any non-WatchEvent event
        self.events = 0

    def add(self, hour: datetime, rec: Record) -> None:
        self.events += 1
        etype = rec["type"]
        actor: str | None = rec.get("actor")
        if etype not in ("WatchEvent", "ForkEvent"):
            if actor is not None:
                self.other_activity.add(actor)
            return
        key = (hour, rec["repo_id"])
        cell = self.cells.get(key)
        if cell is None:
            cell = self.cells[key] = _Cell(rec["repo_name"])
        if etype == "WatchEvent":
            cell.stars_raw += 1
            if rec.get("is_bot") or actor is None:
                cell.stars_bot += 1
            else:
                cell.stargazers.add(actor)
        else:
            if actor is not None:
                self.other_activity.add(actor)
            cell.forks_raw += 1
            if rec.get("is_bot") or actor is None:
                cell.forks_bot += 1

    def rows(self) -> list[HourlyRow]:
        cfg, out = self.cfg, []
        for (hour, rid), c in self.cells.items():
            n = len(c.stargazers)  # distinct non-bot stargazers (duplicate stars count once)
            star_only = sum(1 for a in c.stargazers if a not in self.other_activity)
            flag = star_only >= cfg.lockstep_min_stars and star_only >= cfg.lockstep_share * n
            lock = star_only if flag else 0
            out.append(
                HourlyRow(
                    repo_host_id=rid,
                    hour=hour,
                    repo_name=c.repo_name,
                    stars_raw=c.stars_raw,
                    stars_bot=c.stars_bot,
                    stars_lockstep=lock,
                    stars_filtered=n - lock,
                    forks_raw=c.forks_raw,
                    forks_filtered=c.forks_raw - c.forks_bot,
                    lockstep_flag=flag,
                )
            )
        return out


# --- detection -------------------------------------------------------------------------------
@dataclass(frozen=True)
class BaselineStats:
    mean_48h: float
    std_48h: float
    sigma_used: float
    hours_covered: int
    quality: BaselineQuality


def baseline_stats(
    hourly: Mapping[datetime, int],
    covered: Collection[datetime],
    start: datetime,
    cfg: VelocityConfig,
) -> BaselineStats:
    """Baseline over [start, start + baseline_hours); `covered` = observed hours (module doc)."""
    n, w = cfg.baseline_hours, cfg.window_hours
    end = start + timedelta(hours=n)
    obs = {h for h in covered if start <= h < end}
    hc = len(obs)
    if hc == 0:
        return BaselineStats(0.0, 0.0, cfg.min_sigma, 0, "none")
    total = sum(v for h, v in hourly.items() if h in obs)
    mean = total / hc * w
    blocks: list[float] = []
    for i in range(n // w):
        bs = start + timedelta(hours=i * w)
        hs = [bs + timedelta(hours=k) for k in range(w)]
        c = sum(1 for h in hs if h in obs)
        if c and c >= cfg.full_block_coverage * w:
            blocks.append(sum(hourly.get(h, 0) for h in hs if h in obs) * w / c)
    std = statistics.stdev(blocks) if len(blocks) >= max(2, cfg.min_full_blocks) else 0.0
    quality: BaselineQuality = "full" if hc >= cfg.full_baseline_coverage * n else "partial"
    sigma = max(std, math.sqrt(mean), cfg.min_sigma)
    return BaselineStats(mean, std, sigma, hc, quality)


@dataclass(frozen=True)
class Candidate:
    repo_host_id: int
    repo_name: str
    hour: datetime
    stars_48h: int
    stars_48h_raw: int
    forks_48h: int


@dataclass(frozen=True)
class Evaluation:
    candidate: Candidate
    baseline: BaselineStats
    z: float
    fires: bool


def evaluate(c: Candidate, b: BaselineStats, cfg: VelocityConfig) -> Evaluation:
    z = (c.stars_48h - b.mean_48h) / b.sigma_used
    return Evaluation(c, b, z, c.stars_48h >= cfg.min_stars_48h and z >= cfg.sigma)


class StarSeriesSource(Protocol):
    """Pluggable star series (GH Archive today; another source later)."""

    name: str

    def candidates(self, hour: datetime, window_hours: int, min_stars: int) -> list[Candidate]:
        """Repos with ≥ min_stars filtered stars in the `window_hours` buckets ending at `hour`."""
        ...

    def hourly_stars(
        self, repo_ids: Sequence[int], start: datetime, end: datetime
    ) -> dict[int, dict[datetime, int]]: ...

    def covered_hours(self, start: datetime, end: datetime) -> set[datetime]: ...


@dataclass(frozen=True)
class Confirmation:
    coverage: Coverage
    confirmed: bool | None  # False = reject the candidate; None = could not check


class CandidateConfirmer(Protocol):
    """Hook for independent confirmation of a candidate (e.g. GitHub stargazers API, later)."""

    def confirm(
        self, ev: Evaluation, window_start: datetime, window_end: datetime
    ) -> Confirmation: ...


class PostgresStarSeries:
    name = "gharchive"

    def __init__(self, db: CaptureDB) -> None:
        self.db = db

    def candidates(self, hour: datetime, window_hours: int, min_stars: int) -> list[Candidate]:
        rows = self.db.conn.execute(
            """
            SELECT repo_host_id, (array_agg(repo_name ORDER BY hour DESC))[1],
                   sum(stars_filtered), sum(stars_raw), sum(forks_filtered)
            FROM repo_hourly_activity
            WHERE hour > %(t)s - make_interval(hours => %(w)s) AND hour <= %(t)s
            GROUP BY repo_host_id
            HAVING sum(stars_filtered) >= %(m)s
            ORDER BY repo_host_id
            """,
            {"t": hour, "w": window_hours, "m": min_stars},
        ).fetchall()
        return [Candidate(r[0], r[1], hour, int(r[2]), int(r[3]), int(r[4])) for r in rows]

    def hourly_stars(
        self, repo_ids: Sequence[int], start: datetime, end: datetime
    ) -> dict[int, dict[datetime, int]]:
        out: dict[int, dict[datetime, int]] = {r: {} for r in repo_ids}
        for rid, h, s in self.db.conn.execute(
            "SELECT repo_host_id, hour, stars_filtered FROM repo_hourly_activity "
            "WHERE repo_host_id = ANY(%s) AND hour >= %s AND hour < %s",
            (list(repo_ids), start, end),
        ):
            out[rid][h] = s
        return out

    def covered_hours(self, start: datetime, end: datetime) -> set[datetime]:
        return {
            r[0]
            for r in self.db.conn.execute(
                "SELECT hour FROM gharchive_hours WHERE status = 'ok' AND hour >= %s AND hour < %s",
                (start, end),
            )
        }


# --- scanner ---------------------------------------------------------------------------------
@dataclass
class ScanResult:
    hours_ok: int = 0
    hours_missing: int = 0
    hours_skipped: int = 0
    events: int = 0
    cases: list[Case] = field(default_factory=list)
    seconds_per_hour: list[float] = field(default_factory=list)


class VelocityScanner:
    def __init__(
        self,
        *,
        connector: GHArchiveConnector,
        db: CaptureDB,
        cfg: VelocityConfig | None = None,
        series: StarSeriesSource | None = None,
        confirmer: CandidateConfirmer | None = None,
        run: RunRecorder | None = None,
    ) -> None:
        self.connector = connector
        self.db = db
        self.cfg = cfg or VelocityConfig()
        self.series = series or PostgresStarSeries(db)
        self.confirmer = confirmer
        self.run = run

    def _incr(self, key: str, n: int | float = 1) -> None:
        if self.run is not None:
            self.run.incr(key, n)

    @property
    def _run_id(self) -> str | None:
        return self.run.id if self.run is not None else None

    def scan(self, start: datetime, end: datetime, *, force: bool = False) -> ScanResult:
        start, end = floor_hour(start), floor_hour(end)
        if end <= start:
            raise ValueError("end must be after start")
        res = ScanResult()
        for cs, ce in day_chunks(start, end):
            self._process_chunk(cs, ce, force, res)
        for h in hours_between(start, end):
            res.cases.extend(self.detect(h))
        self._incr("cases_opened", len(res.cases))
        return res

    # --- ingest ---
    def _chunk_done(self, cs: datetime, ce: datetime) -> bool:
        row = self.db.conn.execute(
            "SELECT count(*) FROM gharchive_hours WHERE hour >= %s AND hour < %s "
            "AND bot_filter_version = %s AND filter_window = %s",
            (cs, ce, BOT_FILTER_VERSION, Range(cs, ce, "[)")),
        ).fetchone()
        return row is not None and row[0] == len(hours_between(cs, ce))

    def _known_hash(self, hour: datetime) -> str | None:
        row = self.db.conn.execute(
            "SELECT content_hash FROM gharchive_hours WHERE hour = %s AND status = 'ok'", (hour,)
        ).fetchone()
        h: str | None = row[0] if row else None
        return h

    def _records(self, hour: datetime) -> tuple[str, str | None, Iterator[Record]] | None:
        """(content_hash, evidence_id or None if reused, records) or None if the hour is missing.

        A previously scanned hour is re-read from the snapshot store; if its raw bytes were
        dropped by retention (CB-04) it is re-downloaded and must match the recorded hash.
        """
        known = self._known_hash(hour)
        if known is not None:
            store = self.connector.store
            if store.exists(known):
                data = store.get(known)
            else:
                data = self.connector.refetch_verified(hour_url(hour), known)
            try:
                meta = store.meta(known)
            except SnapshotNotFound:
                meta = _meta_for(self.connector, hour)
            return known, None, self.connector.records(data, meta)
        try:
            f = self.connector.fetch(hour_url(hour))
        except NotFound:
            return None
        return f.content_hash, f.evidence.id, self.connector.records(f.data, f.meta)

    def _process_chunk(self, cs: datetime, ce: datetime, force: bool, res: ScanResult) -> None:
        hours = hours_between(cs, ce)
        if not force and self._chunk_done(cs, ce):
            log.info("chunk %s..%s already scanned; skipping ingest", cs, ce)
            res.hours_skipped += len(hours)
            self._incr("hours_skipped", len(hours))
            return
        agg = WindowAggregator(self.cfg)
        hour_rows: list[tuple[datetime, str, str | None, str | None, int]] = []
        for h in hours:
            t0 = time.perf_counter()
            got = self._records(h)
            if got is None:
                log.warning("GH Archive hour %s missing (404)", h)
                hour_rows.append((h, "missing", None, None, 0))
                res.hours_missing += 1
                self._incr("hours_missing")
                continue
            content_hash, ev_id, recs = got
            before = agg.events
            for rec in recs:
                agg.add(h, rec)
            n = agg.events - before
            dt = time.perf_counter() - t0
            res.seconds_per_hour.append(dt)
            log.info("hour %s: %d events in %.1fs", h, n, dt)
            if ev_id is None:
                row = self.db.conn.execute(
                    "SELECT evidence_id FROM gharchive_hours WHERE hour = %s", (h,)
                ).fetchone()
                ev_id = row[0] if row else None
            hour_rows.append((h, "ok", content_hash, ev_id, n))
            res.hours_ok += 1
            res.events += n
            self._incr("hours_ok")
            self._incr("events", n)
        rows = agg.rows()
        self._write_chunk(cs, ce, hours, hour_rows, rows)
        self._incr("repo_hour_rows", len(rows))

    def _write_chunk(
        self,
        cs: datetime,
        ce: datetime,
        hours: list[datetime],
        hour_rows: list[tuple[datetime, str, str | None, str | None, int]],
        rows: list[HourlyRow],
    ) -> None:
        conn = self.db.conn
        window = Range(cs, ce, "[)")
        with conn.transaction():
            conn.execute("DELETE FROM repo_hourly_activity WHERE hour = ANY(%s)", (hours,))
            with conn.cursor().copy(
                "COPY repo_hourly_activity (repo_host_id, hour, repo_name, stars_raw, stars_bot, "
                "stars_lockstep, stars_filtered, forks_raw, forks_filtered, lockstep_flag) "
                "FROM STDIN"
            ) as cp:
                for r in rows:
                    cp.write_row(
                        (
                            r.repo_host_id,
                            r.hour,
                            r.repo_name,
                            r.stars_raw,
                            r.stars_bot,
                            r.stars_lockstep,
                            r.stars_filtered,
                            r.forks_raw,
                            r.forks_filtered,
                            r.lockstep_flag,
                        )
                    )
            for h, status, content_hash, ev_id, n in hour_rows:
                conn.execute(
                    """
                    INSERT INTO gharchive_hours (hour, status, content_hash, evidence_id, events,
                        filter_window, bot_filter_version, run_id, scanned_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (hour) DO UPDATE SET status = EXCLUDED.status,
                        content_hash = EXCLUDED.content_hash,
                        evidence_id = COALESCE(EXCLUDED.evidence_id, gharchive_hours.evidence_id),
                        events = EXCLUDED.events, filter_window = EXCLUDED.filter_window,
                        bot_filter_version = EXCLUDED.bot_filter_version,
                        run_id = EXCLUDED.run_id, scanned_at = now()
                    """,
                    (h, status, content_hash, ev_id, n, window, BOT_FILTER_VERSION, self._run_id),
                )

    # --- detection ---
    def detect(self, hour: datetime) -> list[Case]:
        cfg = self.cfg
        cands = self.series.candidates(hour, cfg.window_hours, cfg.min_stars_48h)
        if not cands:
            return []
        win_start = hour - timedelta(hours=cfg.window_hours - 1)
        base_start = win_start - timedelta(hours=cfg.baseline_hours)
        hourly = self.series.hourly_stars([c.repo_host_id for c in cands], base_start, win_start)
        covered = self.series.covered_hours(base_start, win_start)
        opened: list[Case] = []
        for c in cands:
            ev = evaluate(c, baseline_stats(hourly[c.repo_host_id], covered, base_start, cfg), cfg)
            self._incr("candidates")
            if not ev.fires:
                continue
            case = self._open_case(ev, win_start, hour + HOUR)
            if case is not None:
                opened.append(case)
        return opened

    def _open_case(self, ev: Evaluation, win_start: datetime, opened_at: datetime) -> Case | None:
        c, cfg = ev.candidate, self.cfg
        rid = repo_id("github", c.repo_host_id)
        if self.db.case_near(rid, "velocity", opened_at, cfg.cooldown_hours):
            return None
        coverage = Coverage(
            source=self.series.name,
            window_start=win_start,
            window_end=opened_at,
            observed_stars=c.stars_48h_raw,
        )
        if self.confirmer is not None:
            conf = self.confirmer.confirm(ev, win_start, opened_at)
            coverage = conf.coverage
            if conf.confirmed is False:
                self._incr("candidates_rejected_by_confirmer")
                return None
        det = VelocityDetection(
            rule_version=RULE_VERSION,
            detected_hour=c.hour,
            stars_48h=c.stars_48h,
            stars_48h_raw=c.stars_48h_raw,
            forks_48h=c.forks_48h,
            baseline_mean_48h=round(ev.baseline.mean_48h, 4),
            baseline_std_48h=round(ev.baseline.std_48h, 4),
            sigma_used=round(ev.baseline.sigma_used, 4),
            z_score=round(ev.z, 4),
            baseline_hours_covered=ev.baseline.hours_covered,
            baseline_quality=ev.baseline.quality,
            threshold_min_stars=cfg.min_stars_48h,
            threshold_sigma=cfg.sigma,
            bot_filter_version=BOT_FILTER_VERSION,
            coverage=coverage,
        )
        case = Case(
            id=case_id(rid, "velocity", opened_at),
            repo_id=rid,
            opened_at=opened_at,
            trigger="velocity",
            status="live",
            run_id=self._run_id,
            detection=det,
        )
        first = self.db.conn.execute(
            "SELECT min(hour) FROM repo_hourly_activity WHERE repo_host_id = %s",
            (c.repo_host_id,),
        ).fetchone()
        first_seen = first[0] if first and first[0] else c.hour
        with self.db.conn.transaction():
            self.db.upsert_repo(
                Repo(
                    id=rid,
                    host="github",
                    host_id=c.repo_host_id,
                    full_name=c.repo_name,
                    first_seen_at=first_seen,
                )
            )
            inserted = self.db.insert_case(case)
        return case if inserted else None


def _meta_for(connector: GHArchiveConnector, hour: datetime) -> SnapshotMeta:
    """Metadata for a re-downloaded hour whose sidecar is gone (parsing does not depend on it)."""
    return SnapshotMeta(
        source=connector.name,
        url=hour_url(hour),
        fetched_at=connector.clock(),
        collector_version=connector.collector_version,
        terms_basis=connector.terms.terms_basis,
        content_type="application/gzip",
    )


def scan_config_dict(cfg: VelocityConfig, **extra: Any) -> dict[str, Any]:
    from dataclasses import asdict

    return {**asdict(cfg), "rule_version": RULE_VERSION, "bot_filter": BOT_FILTER_VERSION, **extra}

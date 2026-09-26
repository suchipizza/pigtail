"""Source connector interface (R2.1, R2.3; roles and buckets at ingest, Directive §8.1,
ADR-066.1, ADR-071; PRD R5.3, §7, §10).

Every connector gets, from this base class:

- **terms metadata** (`TermsMetadata`): terms URL, basis, clearance, commercial use, deletion
  obligation. A connector whose clearance is `gap` can never be enabled (R2.3): it raises
  `ConnectorGapError` instead of being scraped around.
- a **per-source enable flag**: `PIGTAIL_CONNECTOR_<NAME>_ENABLED=true|false`, else an optional
  group flag (`enable_env`, e.g. `PIGTAIL_ENABLE_HN`), else the class default. Fetching from a
  disabled connector raises `ConnectorDisabled`.
- the **ADR-022 hold** for person-level sources (`person_level_hold = True`): enabling such a
  connector raises `PersonSourceHold` unless the operator has set
  `PIGTAIL_ADR022_PERSON_SOURCES_OK=1`, which they do only once every ADR-022 precondition for
  person-level sources (CB-01, CB-02, CB-03, CB-06, CB-08, CB-12, CB-13) is met.
- a **token-bucket rate limiter** running at `rate_per_second * (1 - safety_margin)`.
- **retries** with exponential backoff and full jitter on 429/5xx and transport errors,
  honouring `Retry-After` (seconds or HTTP date).
- a contact **User-Agent**: `pigtail/<version> (+https://github.com/suchipizza/pigtail)`.
- **cost accounting**: each HTTP request emits a `CostEvent` to the cost hook and to the run.
- **snapshot or drop**: `fetch()` stores the raw bytes in the content-addressed snapshot store and
  builds the `evidence` record *before* anything is parsed. If the snapshot cannot be stored the
  fetch fails and nothing is parsed.
- **code, then discard identities** (Directive §8.1, ADR-066.1, ADR-071.2): a connector lists
  its handle fields in `handle_fields` (dotted paths; list values allowed). `records()` - the
  only public way to get parsed records - codes the actor **in memory** and then sets every
  handle field to None, so no record that leaves the connector carries a handle or a pseudonym.
  The coded fields added to each record are `automated_account` (bot rule, in memory) with
  `bot_rule_version`, `actor_role` and `actor_bucket` (`pigtail.privacy.roles`; follower counts
  from `follower_fields`, if the source returns them, are turned into a bucket and dropped), and
  `role_rule_version`. `actor_owns` lists the repos in the record's `repo_full_names` whose owner
  is the handle, so a capture job can code the author as `maintainer`; it is a transient hint
  and is never stored. A connector with `transient_actor_tokens` also adds `_actor_token`, a
  keyed hash under a random per-instance key, only for de-duplication within one run (it is
  unlinkable across runs and must never be stored). A connector sees raw handles only in
  `_parse()` and `_pre_code()` (e.g. to drop bots by login). Raw snapshot bytes stay in private
  storage only (R19.9).
- **refusal list at ingest** (DPIA CB-13, ADR-071.1): given a `Suppressions` snapshot,
  `records()` drops every record whose handles' opt-out fingerprints (computed in memory with
  the opt-out key) are on the list, or whose `repo_fields` name an opted-out repo
  (`<repo_host>:<id>`). Drops are counted on the run (`<name>.suppressed`). The key must be the
  one the list was loaded under (`Suppressions.check_key`, CB-25): `suppression.load()` verifies
  it against the fingerprint stored in the database and a connector refuses any other key.
  `subject_records()` yields each coded record with its fingerprints (in memory) for access and
  erasure scans of retained snapshots (CB-08).

**Unparseable pages** (CB-23b): a connector that parses inside a fetch method (HN Algolia pages,
Firebase items) reports a failed parse through `parse_failed()`, which calls the
`parse_failure_sink` the capture job set (`pigtail.privacy.deletion.drop_unparseable`: raw bytes
dropped at once, counts only in the run record) and then raises `ParseFailed`.

`check(url)` is the one network path that stores nothing: deletion sync (R1.5, CB-02) uses it to
re-check whether an item still exists upstream. It works while the connector is disabled, so
deletion duties outlive collection.

Subclasses implement `_parse(data, meta)`; the same code path serves live ingest and replay
(`pigtail.capture.replay`).
"""

from __future__ import annotations

import email.utils
import hashlib
import hmac
import os
import random
import secrets
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar

import httpx

from pigtail import __version__
from pigtail.capture.botfilter import BOT_FILTER_VERSION, is_bot_login
from pigtail.capture.models import Evidence, Reliability, RetentionClass, evidence_id
from pigtail.capture.runs import RunRecorder
from pigtail.capture.snapshots import (
    SnapshotIntegrityError,
    SnapshotMeta,
    SnapshotStore,
    sha256_hex,
)
from pigtail.privacy.roles import ROLE_RULE_VERSION, code_actor, owned_repos
from pigtail.privacy.suppression import Suppressions
from pigtail.pseudonymize import OptoutKey

USER_AGENT = f"pigtail/{__version__} (+https://github.com/suchipizza/pigtail)"

Record = dict[str, Any]


class Clearance(StrEnum):
    CLEARED = "cleared"
    CLEARED_WITH_CONDITIONS = "cleared_with_conditions"
    GAP = "gap"


@dataclass(frozen=True)
class TermsMetadata:
    terms_url: str
    terms_basis: str
    clearance: Clearance
    commercial_use: bool | None  # None = unknown
    deletion_obligation: bool | None  # None = unknown
    notes: str = ""


class ConnectorError(RuntimeError):
    pass


class ConnectorDisabled(ConnectorError):
    pass


class ConnectorGapError(ConnectorError):
    """The source is a documented gap under its terms (R2.3); it must not be used."""


ADR022_ENV = "PIGTAIL_ADR022_PERSON_SOURCES_OK"
ADR022_CONTROLS = ("CB-01", "CB-02", "CB-03", "CB-06", "CB-08", "CB-12", "CB-13")


class PersonSourceHold(ConnectorError):
    """A person-level source was enabled while ADR-022 still holds it (see `ADR022_ENV`)."""


class FetchError(ConnectorError):
    def __init__(self, url: str, status: int | None, message: str = "") -> None:
        super().__init__(f"{status} for {url} {message}".strip())
        self.url = url
        self.status = status


class NotFound(FetchError):
    pass


class ParseFailed(ConnectorError):
    """A snapshotted document could not be parsed (CB-23b). Carries no content."""

    def __init__(self, evidence_id: str, kind: str) -> None:
        super().__init__(f"unparseable document (evidence {evidence_id}, {kind})")
        self.evidence_id = evidence_id
        self.kind = kind


@dataclass(frozen=True)
class CostEvent:
    connector: str
    url: str
    status: int | None
    requests: int
    bytes: int
    usd: float


CostHook = Callable[[CostEvent], None]
ParseFailureSink = Callable[["Fetched", BaseException], None]


class TokenBucket:
    """Token bucket at `rate * (1 - margin)` tokens/s with capacity `burst`."""

    def __init__(
        self,
        rate_per_second: float,
        burst: int = 1,
        safety_margin: float = 0.2,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate_per_second <= 0 or not 0 <= safety_margin < 1 or burst < 1:
            raise ValueError("rate must be > 0, 0 <= margin < 1, burst >= 1")
        self.rate = rate_per_second * (1 - safety_margin)
        self.capacity = float(burst)
        self.tokens = float(burst)
        self.clock = clock
        self.sleep = sleep
        self.updated = clock()
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """Take one token, sleeping as needed; return seconds slept."""
        slept = 0.0
        with self._lock:
            while True:
                now = self.clock()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1 - 1e-9:  # tolerance: float drift must not stall the loop
                    self.tokens = max(0.0, self.tokens - 1)
                    return slept
                wait = (1 - self.tokens) / self.rate
                self.sleep(wait)
                slept += wait


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 5
    base_delay: float = 1.0
    max_delay: float = 120.0
    max_retry_after: float = 3600.0  # give up rather than sleep longer than this
    retry_statuses: frozenset[int] = frozenset({429, 500, 502, 503, 504})

    def backoff(self, attempt: int, rng: random.Random) -> float:
        """Full-jitter exponential backoff for retry number `attempt` (0-based)."""
        return rng.uniform(0, min(self.max_delay, self.base_delay * 2**attempt))


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        at = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return max(0.0, (at - (now or datetime.now(UTC))).total_seconds())


@dataclass(frozen=True)
class CheckResult:
    """Outcome of `Connector.check()`: status and body of a GET that was **not** snapshotted."""

    url: str
    status: int | None  # None: transport error after retries
    data: bytes


@dataclass(frozen=True)
class Fetched:
    """A fetched document: raw bytes already snapshotted, and its evidence record."""

    data: bytes
    content_hash: str
    evidence: Evidence
    meta: SnapshotMeta


def _env_flag(value: str) -> bool:
    v = value.strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off", ""):
        return False
    raise ValueError(f"bad boolean {value!r}")


class Connector(ABC):
    # --- declared by each connector -------------------------------------------------------
    name: ClassVar[str]
    version: ClassVar[str]
    terms: ClassVar[TermsMetadata]
    enabled_by_default: ClassVar[bool] = False
    enable_env: ClassVar[str | None] = None  # optional group flag, e.g. PIGTAIL_ENABLE_HN
    person_level_hold: ClassVar[bool] = False  # ADR-022: needs ADR022_ENV=1 to be enabled
    requires_env: ClassVar[tuple[str, ...]] = ()  # env vars without which no live call is made
    rate_per_second: ClassVar[float] = 1.0
    burst: ClassVar[int] = 1
    safety_margin: ClassVar[float] = 0.2
    cost_per_request_usd: ClassVar[float] = 0.0
    reliability: ClassVar[Reliability] = "high"
    retention_class: ClassVar[RetentionClass] = "person_level_24m"
    handle_fields: ClassVar[tuple[str, ...]] = ()  # dropped after coding (Directive §8.1)
    handle_namespace: ClassVar[str] = "generic"
    follower_fields: ClassVar[tuple[str, ...]] = ()  # counts -> bucket at ingest, then dropped
    transient_actor_tokens: ClassVar[bool] = False  # `_actor_token` for in-run de-duplication
    repo_fields: ClassVar[tuple[str, ...]] = ()  # numeric repo ids, checked against opt-outs
    repo_host: ClassVar[str] = "github"
    timeout_seconds: ClassVar[float] = 60.0

    def __init__(
        self,
        *,
        store: SnapshotStore,
        pseudonymizer: OptoutKey | None,
        http: httpx.Client | None = None,
        enabled: bool | None = None,
        env: Mapping[str, str] | None = None,
        run: RunRecorder | None = None,
        cost_hook: CostHook | None = None,
        evidence_sink: Callable[[Evidence], None] | None = None,
        retry: RetryPolicy | None = None,
        limiter: TokenBucket | None = None,
        rng: random.Random | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        suppression: Suppressions | None = None,
        parse_failure_sink: ParseFailureSink | None = None,
    ) -> None:
        e = os.environ if env is None else env
        self.enabled = enabled if enabled is not None else self.enabled_from_env(e)
        if self.enabled and self.terms.clearance is Clearance.GAP:
            raise ConnectorGapError(
                f"connector {self.name!r} is a documented terms gap and cannot be enabled "
                f"({self.terms.terms_url})"
            )
        if self.enabled and self.person_level_hold and e.get(ADR022_ENV, "").strip() != "1":
            raise PersonSourceHold(
                f"connector {self.name!r} collects person-level data and is held by ADR-022. "
                f"Set {ADR022_ENV}=1 only after every ADR-022 precondition for person-level "
                f"sources is met ({', '.join(ADR022_CONTROLS)}; ops/DECISIONS.md ADR-022, "
                "docs/guides/operator.md)."
            )
        if self.handle_fields and pseudonymizer is None:
            raise ValueError(
                f"connector {self.name!r} has handle fields and needs the opt-out key (OPTOUT_KEY)"
            )
        self.store = store
        self.pz = pseudonymizer
        self.http = http or httpx.Client(timeout=self.timeout_seconds, follow_redirects=True)
        self.run = run
        self.cost_hook = cost_hook
        self.evidence_sink = evidence_sink
        self.retry = retry or RetryPolicy()
        self.limiter = limiter or TokenBucket(
            self.rate_per_second, self.burst, self.safety_margin, sleep=sleep
        )
        self.rng = rng or random.Random()
        self.sleep = sleep
        self.clock = clock
        # `is None`, not `or`: an empty list is falsy but still carries the key fingerprint
        self.suppression = suppression if suppression is not None else Suppressions()
        # CB-25: the refusal list was verified against the database's key fingerprint; an
        # opt-out key other than that one would never match it (KeyFingerprintMismatch).
        self.suppression.check_key(pseudonymizer)
        self.parse_failure_sink = parse_failure_sink
        # in-run de-duplication only: a fresh random key per instance, never stored
        self._token_key = secrets.token_bytes(32)

    @classmethod
    def enabled_from_env(cls, env: Mapping[str, str]) -> bool:
        """Per-connector flag, else the group flag (`enable_env`), else the class default."""
        flag = env.get(f"PIGTAIL_CONNECTOR_{cls.name.upper()}_ENABLED")
        if flag is not None:
            return _env_flag(flag)
        if cls.enable_env is not None and (group := env.get(cls.enable_env)) is not None:
            return _env_flag(group)
        return cls.enabled_by_default

    @property
    def collector_version(self) -> str:
        return f"{self.name}/{self.version}"

    # --- fetching -------------------------------------------------------------------------
    def _account(self, url: str, status: int | None, nbytes: int) -> None:
        ev = CostEvent(self.name, url, status, 1, nbytes, self.cost_per_request_usd)
        if self.cost_hook is not None:
            self.cost_hook(ev)
        if self.run is not None:
            self.run.incr(f"{self.name}.http_requests")
            self.run.incr(f"{self.name}.bytes", nbytes)
            if ev.usd:
                self.run.incr(f"{self.name}.cost_usd", ev.usd)

    def _request(
        self, url: str, params: Mapping[str, Any] | None, headers: Mapping[str, str] | None
    ) -> httpx.Response:
        hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
        attempt = 0
        while True:
            self.limiter.acquire()
            try:
                resp = self.http.get(url, params=params, headers=hdrs)
            except httpx.TransportError as exc:
                self._account(url, None, 0)
                if attempt >= self.retry.max_retries:
                    raise FetchError(url, None, f"transport error: {exc}") from exc
                self.sleep(self.retry.backoff(attempt, self.rng))
                attempt += 1
                continue
            self._account(url, resp.status_code, len(resp.content))
            if resp.status_code not in self.retry.retry_statuses:
                return resp
            if attempt >= self.retry.max_retries:
                raise FetchError(url, resp.status_code, "retries exhausted")
            wait = parse_retry_after(resp.headers.get("Retry-After"), self.clock())
            if wait is not None and wait > self.retry.max_retry_after:
                raise FetchError(url, resp.status_code, f"Retry-After {wait:.0f}s too long")
            self.sleep(wait if wait is not None else self.retry.backoff(attempt, self.rng))
            attempt += 1

    def fetch(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        case_id: str | None = None,
        repo_id: str | None = None,
        retention_class: RetentionClass | None = None,
    ) -> Fetched:
        """GET `url`, snapshot the raw bytes, record evidence. Raises before any parsing.

        `retention_class` overrides the class default for this document (e.g. an id list that
        holds no person-level data).
        """
        if not self.enabled:
            hint = f"PIGTAIL_CONNECTOR_{self.name.upper()}_ENABLED=true"
            if self.enable_env:
                hint += f" or {self.enable_env}=1"
            raise ConnectorDisabled(f"connector {self.name!r} is disabled (set {hint})")
        resp = self._request(url, params, headers)
        if resp.status_code == 404:
            raise NotFound(url, 404)
        if not resp.is_success:
            raise FetchError(url, resp.status_code)
        return self._snapshot_response(
            resp, case_id=case_id, repo_id=repo_id, retention_class=retention_class
        )

    def _snapshot_response(
        self,
        resp: httpx.Response,
        *,
        url: str | None = None,
        case_id: str | None = None,
        repo_id: str | None = None,
        retention_class: RetentionClass | None = None,
    ) -> Fetched:
        """Snapshot a successful response's raw bytes and record evidence (before any parsing).

        `url` overrides the evidence URL (default: the request URL).
        """
        data = resp.content
        meta = SnapshotMeta(
            source=self.name,
            url=url or str(resp.request.url),
            fetched_at=self.clock(),
            collector_version=self.collector_version,
            terms_basis=self.terms.terms_basis,
            content_type=resp.headers.get("Content-Type"),
        )
        h = self.store.put(data, meta)  # snapshot or drop: raises if storage fails
        ev = Evidence(
            id=evidence_id(self.name, meta.url, h),
            source=self.name,
            url=meta.url,
            fetched_at=meta.fetched_at,
            content_hash=h,
            snapshot_ref=self.store.ref(h),
            content_type=meta.content_type,
            http_status=resp.status_code,
            reliability=self.reliability,
            terms_basis=self.terms.terms_basis,
            retention_class=retention_class or self.retention_class,
            deletion_state="present",
            collector_version=self.collector_version,
            case_id=case_id,
            repo_id=repo_id,
            run_id=self.run.id if self.run is not None else None,
        )
        if self.evidence_sink is not None:
            self.evidence_sink(ev)
        if self.run is not None:
            self.run.incr(f"{self.name}.snapshots")
        return Fetched(data=data, content_hash=h, evidence=ev, meta=meta)

    def check(self, url: str, params: Mapping[str, Any] | None = None) -> CheckResult:
        """GET without snapshotting, for deletion sync only (R1.5, CB-02).

        The body is used to read deletion flags and is never stored or parsed into records.
        Allowed while the connector is disabled: deletion duties outlive collection. Rate
        limiting, retries and cost accounting still apply. Transport errors give `status=None`.
        """
        try:
            resp = self._request(url, params, None)
        except FetchError as e:
            return CheckResult(url=url, status=e.status, data=b"")
        if self.run is not None:
            self.run.incr(f"{self.name}.checks")
        return CheckResult(url=url, status=resp.status_code, data=resp.content)

    def refetch_verified(self, url: str, content_hash: str) -> bytes:
        """Re-download a document whose raw bytes were dropped (retention) and verify its hash.

        Nothing is stored. Raises `SnapshotIntegrityError` if upstream bytes changed.
        """
        if not self.enabled:
            raise ConnectorDisabled(f"connector {self.name!r} is disabled")
        resp = self._request(url, None, None)
        if resp.status_code == 404:
            raise NotFound(url, 404)
        if not resp.is_success:
            raise FetchError(url, resp.status_code)
        if sha256_hex(resp.content) != content_hash:
            raise SnapshotIntegrityError(
                f"re-downloaded {url} does not match recorded hash {content_hash[:12]}…"
            )
        if self.run is not None:
            self.run.incr(f"{self.name}.refetched")
        return resp.content

    # --- parsing, coding and opt-out matching (Directive §8.1, ADR-066.1, ADR-071) ------------
    def parse_failed(self, f: Fetched, error: BaseException) -> ParseFailed:
        """CB-23b: hand an unparseable snapshot to `parse_failure_sink` (which drops its raw
        bytes); without a sink only the count is recorded. Returns the error to raise."""
        if self.parse_failure_sink is not None:
            self.parse_failure_sink(f, error)
        elif self.run is not None:
            self.run.incr(f"{self.name}.parse_failed")
        return ParseFailed(f.evidence.id, type(error).__name__)

    @abstractmethod
    def _parse(self, data: bytes, meta: SnapshotMeta) -> Iterable[Record]:
        """Turn raw snapshot bytes into records. May contain raw handles; never call directly."""

    def _pre_code(self, record: Record) -> Record | None:
        """Hook that sees raw handles (e.g. bot rules by login). Return None to drop. It may set
        `automated_account` itself (e.g. a missing login counts as automated)."""
        return record

    def raw_handles(self, record: Record) -> list[str]:
        """Handle values of a raw record (before coding). In memory only. A handle field must
        hold a string, a list of strings or None (TypeError otherwise)."""
        out: list[str] = []
        for path in self.handle_fields:
            for v in _get_raw(record, path.split(".")):
                for h in v if isinstance(v, list) else [v]:
                    if h is None:
                        continue
                    if not isinstance(h, str):
                        raise TypeError(
                            f"handle field must be str, list or None, got {type(h).__name__}"
                        )
                    out.append(h)
        return out

    def fingerprints(self, record: Record) -> frozenset[str]:
        """Opt-out fingerprints of the raw record's handles (in memory; ADR-071.1)."""
        if not self.handle_fields:
            return frozenset()
        assert self.pz is not None  # enforced in __init__ when handle_fields is non-empty
        return frozenset(
            self.pz.person_fingerprint(h, self.handle_namespace) for h in self.raw_handles(record)
        )

    def code(self, record: Record) -> Record:
        """Code the actor of a raw record into role, bucket and automated flag, then drop every
        handle and follower field (Directive §8.1). Records that carry none of the handle fields
        (e.g. an id list) pass as they are."""
        if not any(p.split(".")[0] in record for p in self.handle_fields):
            return dict(record)
        out = dict(record)
        handles = self.raw_handles(out)
        automated = out.get("automated_account")
        if not isinstance(automated, bool):
            automated = any(is_bot_login(h) for h in handles)
        followers = next(
            (v for p in self.follower_fields for v in _get_raw(out, p.split("."))), None
        )
        owns = sorted({r for h in handles for r in owned_repos(h, out.get("repo_full_names"))})
        actor = code_actor(automated=automated, maintainer=False, followers=followers)
        out["automated_account"] = automated
        out["bot_rule_version"] = BOT_FILTER_VERSION
        out["actor_role"] = actor.role
        out["actor_bucket"] = actor.bucket
        out["role_rule_version"] = ROLE_RULE_VERSION
        out["actor_owns"] = [] if automated else owns  # transient hint, never stored
        if self.transient_actor_tokens:
            out["_actor_token"] = (
                self._actor_token(handles[0]) if handles and not automated else None
            )
        for path in (*self.handle_fields, *self.follower_fields):
            _replace_path(out, path.split("."), lambda _v: None)
        return out

    def _actor_token(self, handle: str) -> str:
        norm = f"{self.handle_namespace}:{handle.strip().lstrip('@').lower()}".encode()
        return hmac.new(self._token_key, norm, hashlib.sha256).hexdigest()[:16]

    def repo_keys(self, record: Record) -> list[str]:
        """`<repo_host>:<id>` keys of the repo fields of a record (matches `repos.id`)."""
        return [
            f"{self.repo_host}:{v}"
            for path in self.repo_fields
            for v in _get_path(record, path.split("."))
        ]

    def is_suppressed(self, record: Record, fps: frozenset[str] | None = None) -> bool:
        """True if the raw record belongs to someone or some repo on the refusal list (CB-13).

        `fps` are the record's opt-out fingerprints if already computed."""
        s = self.suppression
        if s.persons and not (fps if fps is not None else self.fingerprints(record)).isdisjoint(
            s.persons
        ):
            return True
        return bool(s.repos) and any(k in s.repos for k in self.repo_keys(record))

    def records(self, data: bytes, meta: SnapshotMeta) -> Iterator[Record]:
        """Parsed records, coded (roles and buckets) and without handles.

        Records of people or repos on the refusal list are dropped here (CB-13)."""
        for rec in self._parse(data, meta):
            kept = self._pre_code(rec)
            if kept is None:
                continue
            if self.suppression and self.is_suppressed(kept):
                if self.run is not None:
                    self.run.incr(f"{self.name}.suppressed")
                continue
            yield self.code(kept)

    def subject_records(
        self, data: bytes, meta: SnapshotMeta
    ) -> Iterator[tuple[Record, frozenset[str]]]:
        """Every coded record with the opt-out fingerprints of its handles, ignoring the refusal
        list: for access and erasure scans of retained snapshots (CB-08) and key rotation
        (CB-26). The fingerprints live in memory only."""
        for rec in self._parse(data, meta):
            kept = self._pre_code(rec)
            if kept is None:
                continue
            yield self.code(kept), self.fingerprints(kept)

    def fetch_records(self, url: str, **kw: Any) -> tuple[Fetched, Iterator[Record]]:
        f = self.fetch(url, **kw)
        return f, self.records(f.data, f.meta)


def _get_path(obj: Any, parts: list[str]) -> list[str]:
    """Scalar values at a dotted path (lists are walked); non-None values as strings."""
    if isinstance(obj, list):
        return [v for item in obj for v in _get_path(item, parts)]
    if not isinstance(obj, dict) or parts[0] not in obj:
        return []
    val = obj[parts[0]]
    if len(parts) > 1:
        return _get_path(val, parts[1:])
    items = val if isinstance(val, list) else [val]
    return [str(v) for v in items if v is not None]


def _get_raw(obj: Any, parts: list[str]) -> list[Any]:
    """Raw values at a dotted path (lists are walked), None values skipped."""
    if isinstance(obj, list):
        return [v for item in obj for v in _get_raw(item, parts)]
    if not isinstance(obj, dict) or parts[0] not in obj:
        return []
    val = obj[parts[0]]
    if len(parts) > 1:
        return _get_raw(val, parts[1:])
    return [val] if val is not None else []


def _replace_path(obj: Any, parts: list[str], fn: Callable[[Any], Any]) -> None:
    if isinstance(obj, list):
        for item in obj:
            _replace_path(item, parts, fn)
        return
    if not isinstance(obj, dict) or parts[0] not in obj:
        return
    if len(parts) == 1:
        obj[parts[0]] = fn(obj[parts[0]])
    else:
        child = obj[parts[0]]
        if isinstance(child, dict):
            child = dict(child)
            obj[parts[0]] = child
        elif isinstance(child, list):
            child = [dict(c) if isinstance(c, dict) else c for c in child]
            obj[parts[0]] = child
        _replace_path(child, parts[1:], fn)

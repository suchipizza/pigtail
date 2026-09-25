"""Holdout split (M4-T4): calibration / H-eval / H-sealed, exactly as pre-registered.

Source: `docs/preregistration/2026-09-25-threshold-calibration.md` §1.1 (ADR-019, ADR-039.5):

    m = int(hashlib.sha256(("pigtail-outcome-holdout-v1" + case_id).encode("utf-8"))
            .hexdigest(), 16) % 100

| `m`           | split         | share |
|---------------|---------------|-------|
| `m >= 30`     | `calibration` | 70 %  |
| `m < 20`      | `h_eval`      | 20 %  |
| `20 <= m < 30`| `h_sealed`    | 10 %  |

`case_id` is used exactly as stored (`case_…`), with no whitespace or case change. "Held out" is
exactly `m < 30`. The pre-registered test vectors are in `TEST_VECTORS` and in the unit tests;
the verifier checks them.

**Guard (pre-registration §1.4).** Every function that computes an outcome class, a percentile, a
provisional label or an outcome summary must pass its case ids through `guard_cases` (or be
decorated with `sealed_guard`):

- **H-sealed** cases are refused (`SealedCaseError`) unless an `UnsealToken` naming an ADR that
  exists in `ops/DECISIONS.md` is passed **and** an `UnsealLog` is given. Every unseal is written
  to the append-only `holdout_unseal_log` table (migration 0010) *before* the computation runs:
  ADR id, reason, computation name, the case ids, code commit and time.
- **H-eval** cases are refused (`HeldOutCaseError`) until `outcome-thresholds 1.0.0` is frozen
  (`thresholds_frozen=True`), per §1.4 "before 1.0.0 … no percentile, class … is computed on a
  held-out case". No token unseals H-eval early.

No class or percentile computation exists yet (M4); this module provides the API they must use.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import re
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, ParamSpec, Protocol, TypeVar

if TYPE_CHECKING:
    import psycopg

HOLDOUT_SALT = "pigtail-outcome-holdout-v1"
HELD_OUT_BELOW = 30  # m < 30: held out (ADR-019, 30 %)
H_EVAL_BELOW = 20  # m < 20: H-eval (20 %); 20 <= m < 30: H-sealed (10 %)
PREREGISTRATION = "docs/preregistration/2026-09-25-threshold-calibration.md#11"

Split = Literal["calibration", "h_eval", "h_sealed"]
SPLITS: tuple[Split, ...] = ("calibration", "h_eval", "h_sealed")

# Pre-registered test vectors (§1.1): synthetic ids, not cases.
TEST_VECTORS: tuple[tuple[str, int, Split], ...] = (
    ("case_example_04", 5, "h_eval"),
    ("case_example_00", 20, "h_sealed"),
    ("case_example_a", 32, "calibration"),
    ("case_example_b", 58, "calibration"),
)

ADR_ID_RE = re.compile(r"^ADR-[0-9]{3,}$")
_ADR_HEADING = re.compile(r"^##\s+(ADR-[0-9]{3,})\b", re.MULTILINE)
DEFAULT_DECISIONS = Path(__file__).resolve().parents[3] / "ops" / "DECISIONS.md"


def split_m(case_id: str) -> int:
    """`m` of §1.1: SHA-256 of salt + case id (UTF-8), hex digest read as an integer, mod 100."""
    if not isinstance(case_id, str) or not case_id:
        raise TypeError("case_id must be a non-empty str (used exactly as stored)")
    digest = hashlib.sha256((HOLDOUT_SALT + case_id).encode("utf-8")).hexdigest()
    return int(digest, 16) % 100


def split_for_m(m: int) -> Split:
    if not 0 <= m < 100:
        raise ValueError(f"m must be in 0..99, got {m}")
    if m >= HELD_OUT_BELOW:
        return "calibration"
    return "h_eval" if m < H_EVAL_BELOW else "h_sealed"


def split_of(case_id: str) -> Split:
    return split_for_m(split_m(case_id))


def is_held_out(case_id: str) -> bool:
    """Held out (H-eval or H-sealed) is exactly `m < 30`."""
    return split_m(case_id) < HELD_OUT_BELOW


def calibration_only(case_ids: Iterable[str]) -> list[str]:
    """Drop held-out cases *before* any observation is read (§1.4, pre-freeze cell populations)."""
    return [c for c in case_ids if split_of(c) == "calibration"]


# --- guard ---------------------------------------------------------------------------------------
class HoldoutError(PermissionError):
    """A computation was asked to touch held-out cases it may not touch."""


class SealedCaseError(HoldoutError):
    """H-sealed cases without a valid, logged unseal (§1.4, §5.3)."""


class HeldOutCaseError(HoldoutError):
    """H-eval cases before `outcome-thresholds 1.0.0` is frozen (§1.4)."""


@dataclass(frozen=True)
class UnsealToken:
    """Authorisation to class H-sealed cases once, for the re-run an ADR decided (§5.3)."""

    adr_id: str
    reason: str

    def __post_init__(self) -> None:
        if not ADR_ID_RE.match(self.adr_id):
            raise ValueError(
                f"unseal token must reference an ADR id (ADR-NNN), got {self.adr_id!r}"
            )
        if not self.reason.strip() or len(self.reason) > 500:
            raise ValueError("unseal token needs a reason (1-500 characters)")


@dataclass(frozen=True)
class UnsealEntry:
    adr_id: str
    reason: str
    computation: str
    case_ids: tuple[str, ...]  # sorted, the H-sealed ones only
    at: datetime
    code_commit: str | None = None
    run_id: str | None = None

    @property
    def case_ids_sha256(self) -> str:
        return hashlib.sha256("\n".join(self.case_ids).encode("utf-8")).hexdigest()


class UnsealLog(Protocol):
    def record(self, entry: UnsealEntry) -> None: ...


@dataclass
class MemoryUnsealLog:
    """In-memory log (tests, dry runs). Production uses `PgUnsealLog`."""

    entries: list[UnsealEntry] = field(default_factory=list)

    def record(self, entry: UnsealEntry) -> None:
        self.entries.append(entry)


class PgUnsealLog:
    """Writes to `holdout_unseal_log` (migration 0010; append-only by trigger)."""

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self.conn = conn

    def record(self, entry: UnsealEntry) -> None:
        self.conn.execute(
            "INSERT INTO holdout_unseal_log (logged_at, adr_id, reason, computation, n_cases,"
            " case_ids, case_ids_sha256, code_commit, run_id)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                entry.at,
                entry.adr_id,
                entry.reason,
                entry.computation,
                len(entry.case_ids),
                list(entry.case_ids),
                entry.case_ids_sha256,
                entry.code_commit,
                entry.run_id,
            ),
        )


def known_adrs(path: Path = DEFAULT_DECISIONS) -> frozenset[str]:
    """ADR ids with a `## ADR-NNN` heading in the decision log."""
    return frozenset(_ADR_HEADING.findall(path.read_text(encoding="utf-8")))


def guard_cases(
    case_ids: Iterable[str],
    *,
    computation: str,
    unseal_token: UnsealToken | None = None,
    unseal_log: UnsealLog | None = None,
    thresholds_frozen: bool = False,
    adr_registry: Collection[str] | None = None,
    code_commit: str | None = None,
    run_id: str | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> list[str]:
    """Return `case_ids` (as a list) if `computation` may use them; raise `HoldoutError` if not.

    `adr_registry` defaults to the ADR headings in `ops/DECISIONS.md`; an unseal is refused if
    the ADR is not there (or the file cannot be read)."""
    if not computation.strip():
        raise ValueError("computation name required (it is written to the unseal log)")
    ids = list(case_ids)
    sealed = sorted({c for c in ids if split_of(c) == "h_sealed"})
    h_eval = [c for c in ids if split_of(c) == "h_eval"]
    if h_eval and not thresholds_frozen:
        raise HeldOutCaseError(
            f"{computation}: {len(h_eval)} H-eval case(s) before outcome-thresholds 1.0.0 is "
            f"frozen ({PREREGISTRATION} §1.4); drop them first (calibration_only)"
        )
    if not sealed:
        return ids
    if unseal_token is None:
        raise SealedCaseError(
            f"{computation}: {len(sealed)} H-sealed case(s); they are never classed or "
            f"inspected without an unseal token naming the ADR of a §5.3 re-run"
        )
    if unseal_log is None:
        raise SealedCaseError(f"{computation}: every unseal must be logged (no unseal_log given)")
    if adr_registry is None:
        try:
            adr_registry = known_adrs()
        except OSError as e:
            raise SealedCaseError(
                f"{computation}: cannot verify {unseal_token.adr_id} ({type(e).__name__})"
            ) from e
    if unseal_token.adr_id not in adr_registry:
        raise SealedCaseError(f"{computation}: {unseal_token.adr_id} is not in the decision log")
    if code_commit is None:
        from pigtail.capture.runs import git_commit

        code_commit = git_commit()
    unseal_log.record(
        UnsealEntry(
            adr_id=unseal_token.adr_id,
            reason=unseal_token.reason,
            computation=computation,
            case_ids=tuple(sealed),
            at=clock(),
            code_commit=code_commit,
            run_id=run_id,
        )
    )
    return ids


P = ParamSpec("P")
R = TypeVar("R")
GUARDED: dict[str, str] = {}  # computation name -> qualified function name
_REQUIRED_PARAMS = ("case_ids", "unseal_token", "unseal_log")


def sealed_guard(computation: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorate a class/percentile function so its `case_ids` pass `guard_cases` first.

    The function must take `case_ids`, `unseal_token` and `unseal_log` parameters (and may take
    `thresholds_frozen`, `run_id`); they are passed through to the guard."""

    def deco(fn: Callable[P, R]) -> Callable[P, R]:
        sig = inspect.signature(fn)
        missing = [p for p in _REQUIRED_PARAMS if p not in sig.parameters]
        if missing:
            raise TypeError(f"{fn.__qualname__}: sealed_guard needs parameters {missing}")

        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            a = bound.arguments
            ids = guard_cases(
                a["case_ids"],
                computation=computation,
                unseal_token=a["unseal_token"],
                unseal_log=a["unseal_log"],
                thresholds_frozen=bool(a.get("thresholds_frozen", False)),
                run_id=a.get("run_id"),
            )
            a["case_ids"] = ids  # an iterator was consumed by the guard
            return fn(*bound.args, **bound.kwargs)

        GUARDED[computation] = fn.__qualname__
        return wrapper

    return deco

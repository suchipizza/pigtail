"""Which LLM stage (and so which model) each job runs in, and how it is sent (PRD R15.8, R15.9;
Directive §6.3, ADR-064.3).

| Stage        | Default model               | Jobs                                              |
|--------------|-----------------------------|---------------------------------------------------|
| `relevance`  | `claude-haiku-4-5-20251001` | relevance filter; the `smoke` test call           |
| `extraction` | `claude-sonnet-5`           | extraction, coding (double coding), adjudication  |
| `synthesis`  | `claude-opus-5-5`           | patterns, report, plan, assets, brief expansion   |

**`brief_expansion` runs in `synthesis`** (decision M21b; ADR candidate). It is one short call
per proposal (about 3k tokens in, 1.5k out), made while the user waits, and its value is the
model's general knowledge of which comparable projects exist: the most capable model is worth
its price there (about USD 0.04 per proposal at list price), while the relevance model's
cheaper knowledge would propose more non-existent or off-field competitors. It is interactive,
so it is a standard call, never batched.

**Batch or standard (R15.9).** Every stage that isn't time-sensitive goes through the Message
Batches API on the `api` backend. Time-sensitive jobs use standard calls: interactive requests
(`brief_expansion`, `smoke`) and launch mode (any job whose name starts with `launch_`, R19.4).
"""

from __future__ import annotations

from pigtail.config import LLMStageName

JOB_STAGES: dict[str, LLMStageName] = {
    # relevance filter (Haiku)
    "relevance": "relevance",
    "relevance_filter": "relevance",
    "smoke": "relevance",
    # extraction and coding (Sonnet)
    "extraction": "extraction",
    "tier2_extraction": "extraction",
    "coding": "extraction",
    "double_coding": "extraction",
    "adjudication": "extraction",
    # synthesis, report and plan (Opus)
    "patterns": "synthesis",
    "synthesis": "synthesis",
    "report": "synthesis",
    "plan": "synthesis",
    "asset_drafting": "synthesis",
    "brief_expansion": "synthesis",
}
INTERACTIVE_JOBS = frozenset({"brief_expansion", "smoke"})
LAUNCH_PREFIX = "launch_"


class UnknownJob(ValueError):
    pass


def stage_for(job: str) -> LLMStageName:
    """The stage of `job` (`launch_<job>` is `<job>`'s stage). Unknown jobs are an error, so a
    new job can't silently run on an unintended (and differently priced) model."""
    base = job.removeprefix(LAUNCH_PREFIX)
    try:
        return JOB_STAGES[base]
    except KeyError:
        raise UnknownJob(
            f"job {job!r} has no LLM stage; add it to pigtail.llm.stages.JOB_STAGES"
        ) from None


def time_sensitive(job: str) -> bool:
    """Standard calls for interactive and launch-mode jobs; everything else may be batched."""
    return job in INTERACTIVE_JOBS or job.startswith(LAUNCH_PREFIX)

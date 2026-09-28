"""Prompts of the double coding and the adjudication (PRD R7.2, R7.4, R15.9, R15.10; codebook
§11.5-11.6, §12; ADR-047.7, ADR-065, ADR-086).

**Independence of the two passes** (codebook §11.5: "different prompts or different models, and
neither sees the other's output"). Both passes run on the extraction model and see the same
trimmed, redacted evidence items and the same frame (`CODEBOOK_CONTEXT`, the cached prefix),
but:

- **coder A** (`case-coder-a`) is told to work *field by field*, in codebook order, looking for
  the evidence each field needs; its evidence items come in a fixed **source order**
  (`ORDER_A`: metadata, launch events, README at T, current README, releases, homepage);
- **coder B** (`case-coder-b`) is told to work *evidence first*: read every item, note what it
  shows and what it doesn't, then fill the fields; its items come in **reverse date order**
  (newest first, ties by evidence id descending).

Neither prompt names the other pass, the case's role, pair, view, rank, outcome values or the
brief's success definition (§11.6); cases are identified by a random coding id. Both prompts and
the adjudicator's are fixed and fingerprinted (`PromptSpec.fingerprint`), and the fingerprints
are stored with every coded row (R7.4). Changing any text needs a version bump.

**Blind to outcome-proximal numbers** (`blind-v1`, ADR-086 item 11): `BLIND_KEYS` are dropped
from structured items and counts in text withheld before any input is rendered; the
spec (`INPUT_SPEC`) is part of the context, so it is fingerprinted with the prompts.

**Adjudication** (§11.5): the adjudicator sees both codings of each disagreeing unit, with the
two options in an order drawn per unit from a fixed seed (position bias, LR [80]), plus the
evidence, and returns one value with a reason and citations, or `unknown`.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from pigtail.forensics.frame import (
    CODEBOOK_VERSION,
    CODED_PATTERNS,
    FIELDS,
    FRAME_VERSION,
    MAX_QUOTE_CHARS,
    MODULES,
    allowed_values,
)
from pigtail.llm.types import PromptSpec

JOB_CODING = "double_coding"  # extraction stage (pigtail.llm.stages)
JOB_ADJUDICATION = "adjudication"  # extraction stage
PROMPT_A = "case-coder-a"
PROMPT_B = "case-coder-b"
PROMPT_ADJ = "case-adjudicator"
PROMPT_VERSION = "3"  # 2: input blinding (blind-v1); 3: flat unit output (ADR-086 add. 1)
ADJ_ORDER_SEED = "pigtail-adjudication-order-v1"

# Kinds of evidence items (the case-evidence stage, pigtail.forensics.evidence).
KINDS: tuple[str, ...] = (
    "repo_metadata",
    "launch_events",
    "readme_at_anchor",
    "readme_current",
    "releases",
    "homepage",
)
ORDER_A = {k: i for i, k in enumerate(KINDS)}

_PATTERN_TESTS = {
    "MC-01": "a first-party Show HN or Launch HN post is the case's launch event (the item "
    "self-identifies the maker) and something people can try existed at posting; absent: HN is "
    "covered for the case window and no first-party HN post exists",
    "MC-02": "an HN story about the repo reached the front page (Algolia front_page tag or rank "
    "evidence in the launch events); absent only with front-page observability showing none",
    "MC-03": "the earliest HN story's hour is in [12, 17) UTC; absent: it is outside that window; "
    "unknown when there is no HN story",
    "MC-04": "social posts linking the repo around a burst or launch; with X a gap and Bluesky "
    "mention text held, this is normally unknown (unobservable_channel or held)",
    "MC-05": "a launch campaign: announcements on >= 3 distinct UTC days within 7 days",
    "MC-06": "disclosed paid promotion alongside the launch; normally unknown",
    "MC-07": "active first-party promotion on >= 2 observable venues; absent: >= 2 observable "
    "venues covered and only 1 first-party venue",
    "MC-08": "early cross-community breadth (>= 3 venues) after the launch; absent: observable "
    "venues covered and fewer than 3",
    "MC-09": "the same value as novelty_claim (present, absent or unknown)",
    "MC-10": "attention tied to a release: a release at or just before the launch or a burst; "
    "absent: releases covered and none near the launch",
    "MC-11": "a launch exists and a live demo or a one-command install existed at the launch "
    "(README at T or the homepage); absent: README and site at T captured with neither",
    "MC-13": "a captured item asks for upvotes or comments on an HN story about the repo; never "
    "absent (use unknown when nothing shows it)",
}


def _field_lines() -> str:
    lines = []
    for f in FIELDS:
        values = " | ".join(allowed_values(f.field))
        lines.append(f"- {f.field} ({f.codebook}, {f.unit}, {f.level}): {values}")
    return "\n".join(lines)


# Input blinding (`blind-v1`; ADR-086, orchestrator decision 2026-09-28: coders and the
# adjudicator are blind to outcome-proximal numbers, standard blind-coding practice). Removed from
# every coder and adjudicator input before it is rendered, and listed in the fingerprinted
# context below, so a change to the list changes the prompt fingerprints:
BLIND_VERSION = "blind-v1"
# keys dropped from structured items (the launch events document, at any depth)
BLIND_KEYS: tuple[str, ...] = (
    "points",  # HN story points
    "votesCount",  # Product Hunt votes
    "commentsCount",  # Product Hunt comments
    "num_comments",
    "stargazerCount",
    "stars",
    "forkCount",
    "forks",
    "score",
    "percentile",
    "percentiles",
    "values",  # stored outcome values
    "outcome",
    "outcomes",
    "lsm",  # launch size
    "rank",
    "role",
    "pair",
    "pair_id",
    "view",
    "covariates",
    "qualification",
    "sensitivity",
    "star_anomaly",
)
# counts in free text (README, homepage, release notes, titles): replaced by `[count withheld]`
BLIND_TEXT_PATTERN = (
    r"(?i)\b\d[\d,.]*\s*[km]?\+?\s*(?:github\s+)?"
    r"(?:stars?|stargazers?|forks?|upvotes?|votes?|points?|comments?|downloads?)\b"
)
BLIND_REPLACEMENT = "[count withheld]"
INPUT_SPEC = (
    f"## Input blinding ({BLIND_VERSION})\n"
    "Outcome-proximal numbers are withheld from the evidence you see: HN points, Product Hunt "
    "votes and comments, star and fork counts, and any outcome, percentile, rank, role, pair or "
    "view. Removed fields: " + ", ".join(BLIND_KEYS) + ". Counts written in text appear as "
    f"{BLIND_REPLACEMENT}. Event kinds and times are kept. Never infer or ask for these numbers.\n"
)


CODEBOOK_CONTEXT = (
    f"""# pigtail coding frame {FRAME_VERSION} (codebook {CODEBOOK_VERSION})

You code one open-source project ("the case") from the evidence items given in the input. Each
item has an evidence id and a text. Everything you state must rest on those items.

## Output: one entry in `units` per unit
A unit is a case field or an item field of one evidence item. Case fields: category_primary;
module_active.<module> for {", ".join(MODULES)}; novelty_claim;
novelty_kind.<kind> (yes or no per kind; only when novelty_claim is present, else leave them out);
pattern.<MC-xx> for {", ".join(CODED_PATTERNS)}. Item fields, one set per evidence item offered,
written <field>@<evidence_id>: reliability@<evidence_id>, first_party@<evidence_id>,
event_type_supported@<evidence_id>. Each entry has `unit` (exactly as named here), `value` (one
of the field's values below, as text), `unknown_reason`, `evidence_ids`, `excerpts` (each with
`evidence_id` and `quote`) and `confidence`. A value outside the field's list is discarded.

## Fields and their values
{_field_lines()}

## Rules for every value (codebook §11)
1. Snapshot or drop. A value other than `unknown` must cite at least one evidence id from the
   input in `evidence_ids`, and for each cited id exactly one excerpt: a verbatim span of that
   item's text of at most {MAX_QUOTE_CHARS} characters, copied exactly (spaces may differ). At most
   one excerpt per evidence item. A value whose citation can't be verified is dropped.
2. `unknown` is a legitimate answer; use it freely, with `unknown_reason`: no_evidence,
   insufficient_evidence, coverage_gap (the source doesn't cover the date), unobservable_channel
   (a channel with no source: X, Reddit, YouTube, blogs), held (a person-level source that is not
   collected yet: HN comments and mentions, Bluesky mention text), or conflicting_evidence.
   Leave `unknown_reason` null for any other value.
3. Absence of evidence is never `no` or `absent`, unless the field's rule allows it (novelty_claim
   and the pattern tests below say when).
4. Never quote or name a person, a handle or an account; never describe personal
   characteristics (origin, gender, age, location, beliefs, health). Quote project text only.
   Aliases such as @user1 or [profile:...] must not appear in an excerpt.
5. `confidence` is low, medium or high: how sure you are of the value given the evidence.

## Definitions (codebook 0.4.0)
- T is the case's reference date, given in the input. "At T" means the state at the last
  evidence before T (the README at the last commit before T, the repo description at T).
- reliability (§2.3), per evidence item: high = observed fact captured as machine-readable
  platform data (api_json) or a project artifact at a pinned commit, time precision hour or better
  where time matters; medium = observed but rendered HTML, or day-level time, or a first-party
  statement about the project's own actions; low = a third-party report, a first-party claim about
  results, or undated. Rate the weakest link.
- first_party (C2): yes when the item is published on the project's own channels (repo, README,
  releases, the project's site) or its text identifies the author as a maker ("Show HN: I
  built", "we're launching"); no for third-party items; unknown otherwise. Never infer it by
  matching account names across platforms.
- event_type_supported (C3), per item: launch = the first public, first-party, deliberate
  announcement presenting the project (announcement language: Show HN, Launch HN, introducing,
  today we're releasing, v1.0 announcement); prep = first-party actions before the launch that
  produce launch assets or readiness (README overhaul, demo, docs site, landing page, waitlist,
  first tagged release, repo made public); relaunch = a later first-party announcement presenting
  the project as new or substantially changed, at least 30 days after the launch campaign, with a
  novelty claim ("2.0", "rewritten", "now supports"); pivot = a first-party change of category,
  target user, core use case, business model or licence; none = the item supports none of these.
  A release without announcement language is not a launch. A third party posting the repo first
  is not a launch.
- category_primary (§9), from the description, topics, language and README at T; boundary rules
  in order: lists-learning first (awesome-lists, mostly-links READMEs, prompt collections);
  crypto-web3 when the core function depends on a blockchain; ai-apps-agents when the product is
  a model-driven app or agent (even as a CLI), ml-infra when it trains, serves, runs, evaluates or
  indexes models or embeddings; function beats deployment mode; an imported library is
  backend-libs (web-frontend in the browser UI), a tool developers run is devtools, package
  managers and compilers are lang-runtime; security by primary purpose; other needs a reason.
- modules (§8), yes when any activation criterion holds on inputs frozen at T:
  ai_hype: category ai-apps-agents or ml-infra, or the description/topics/README hero names an
  ML model, LLM or agent framework as the product's core. b2b_oss_saas: a paid or hosted offering,
  a pricing page, a commercial or dual licence, or a book-a-demo / enterprise call to action (with
  evidence dated <= T + 90 days). chinese_ecosystem: the README at T is mainly Chinese or has a
  Chinese README variant (a person's location is never used). cli_devtools: category devtools, or
  the repo at T ships a command-line entry point (install one-liner, Homebrew formula, npm bin,
  console script, Cargo binary). corporate_backed: the owner is an organisation a first-party
  artifact identifies as a company or foundation, or the README at T states maintenance or
  sponsorship by an organisation. relaunch_pivot: a relaunch or pivot, or the repo was more than
  365 days old at T with evidence of an earlier launch, or a rename. no when the inputs were
  captured and no criterion holds; unknown when an input needed is missing.
- novelty_claim (§5.1): present when the repo description at T, the README at T or the launch
  item contains a first-party span with an explicit novelty marker ("the first", "the only", "a
  new kind of", "novel", "a new approach to", "a new way to", "reimagines", "for the first time";
  hedged claims count); absent when every one of those inputs was captured and none qualifies;
  otherwise unknown. Not novelty: version newness ("new in v2", "now supports"), "an alternative
  to X", performance superlatives without a marker, generic adjectives (modern, powerful,
  simple), anything a third party says. novelty_kind: new_in_kind (nothing like it, the first or
  only one), new_approach (a new method for an existing task), new_combination (novelty from
  joining existing things), other.

## Pattern presence tests (codebook §7.4, seed hypotheses 0.4.0)
present needs the test's elements supported by evidence; absent needs positive evidence that the
element was missing in an observable channel; otherwise unknown.
"""
    + "\n".join(f"- {p}: {_PATTERN_TESTS[p]}" for p in CODED_PATTERNS)
    + "\n\n"
    + INPUT_SPEC
)

SYSTEM_A = (
    "You are a careful research coder applying a published codebook to evidence about one "
    "open-source project. Work field by field, in the order the output lists them. For each "
    "field, look through the evidence items for what that field needs, then choose the value the "
    "codebook rules support, citing the items and one verbatim excerpt per cited item. Prefer "
    "unknown to a guess. Output only the JSON object."
)
TEMPLATE_A = (
    "Code the case below with the frame. Go field by field.\n\n{input}\n\n"
    "Return one `units` entry for every case field and for every item field of every evidence "
    "item listed above."
)

SYSTEM_B = (
    "You code evidence for a research method. Read every evidence item first and note, for "
    "each, what it shows about the project and what it cannot show. Only then fill in the output "
    "fields, each from the notes, following the codebook's rules exactly. Quote verbatim, cite "
    "only items you were given, and answer unknown where the evidence does not decide the value. "
    "Output only the JSON object."
)
TEMPLATE_B = (
    "Evidence items for one project follow (newest first). Read them all, then code the "
    "project with the frame.\n\n{input}\n\n"
    "Return one `units` entry for every case field, and the item fields of each evidence item "
    "above."
)

SYSTEM_ADJ = (
    "You adjudicate disagreements between two independent coders who applied the same codebook "
    "to the same evidence. For each disagreeing unit you see two options in no particular order. "
    "Decide the value the codebook rules and the evidence support: one of the options, another "
    "allowed value, or unknown. Give a short reason (one or two sentences, no names or handles) "
    "and cite the evidence with one verbatim excerpt per cited item. Output only the JSON object."
)
TEMPLATE_ADJ = "{input}\n\nReturn one decision per disagreeing unit, with `unit` exactly as given."

CODER_A = PromptSpec(PROMPT_A, PROMPT_VERSION, SYSTEM_A, TEMPLATE_A, CODEBOOK_CONTEXT)
CODER_B = PromptSpec(PROMPT_B, PROMPT_VERSION, SYSTEM_B, TEMPLATE_B, CODEBOOK_CONTEXT)
ADJUDICATOR = PromptSpec(PROMPT_ADJ, PROMPT_VERSION, SYSTEM_ADJ, TEMPLATE_ADJ, CODEBOOK_CONTEXT)
PROMPTS = {"A": CODER_A, "B": CODER_B, "adjudicator": ADJUDICATOR}


def blind_obj(obj: object) -> object:
    """`obj` without any `BLIND_KEYS` key, at any depth."""
    if isinstance(obj, dict):
        return {k: blind_obj(v) for k, v in obj.items() if k not in BLIND_KEYS}
    if isinstance(obj, list):
        return [blind_obj(v) for v in obj]
    return obj


def blind_text(text: str) -> str:
    """Counts of stars, forks, votes, points, comments and downloads in free text withheld."""
    return re.sub(BLIND_TEXT_PATTERN, BLIND_REPLACEMENT, text)


PROMPT_JOBS = {"A": JOB_CODING, "B": JOB_CODING, "adjudicator": JOB_ADJUDICATION}


def fingerprints() -> dict[str, str]:
    """`<id>@<version>#<fingerprint>+thinking:<sent>` per prompt: the thinking setting sent on
    the resolved extraction model is part of what a coding was made under (ADR-087), so a pilot
    coded under another setting is redone (ADR-086 addendum 1 item 4)."""
    import os

    from pigtail.config import stage_models
    from pigtail.llm.stages import stage_for
    from pigtail.llm.thinking import label, mode_for

    models = stage_models(dict(os.environ))
    out = {}
    for k, p in PROMPTS.items():
        job = PROMPT_JOBS[k]
        sent = label(mode_for(job), models[stage_for(job)])
        out[k] = f"{p.id}@{p.version}#{p.fingerprint}+thinking:{sent}"
    return out


# --- rendering -------------------------------------------------------------------------------
@dataclass(frozen=True)
class RenderedItem:
    """One evidence item as the coders see it (already trimmed; redacted with the case input)."""

    evidence_id: str
    kind: str
    dated: str  # ISO date (or "undated") shown to the coder, also the B order key
    header: str
    text: str

    def block(self) -> str:
        return f"### evidence_id: {self.evidence_id}\n{self.header}\n---\n{self.text}"


def order_a(items: Sequence[RenderedItem]) -> list[RenderedItem]:
    return sorted(items, key=lambda i: (ORDER_A.get(i.kind, 99), i.evidence_id))


def order_b(items: Sequence[RenderedItem]) -> list[RenderedItem]:
    return sorted(items, key=lambda i: (i.dated, i.evidence_id), reverse=True)


def case_input(coding_id: str, anchor: str, items: Sequence[RenderedItem]) -> str:
    """The per-case input (the user turn after the cached prefix). No role, pair, view, rank,
    outcome or success definition (codebook §11.6)."""
    head = (
        f"Case {coding_id}. Reference date T: {anchor}.\n"
        f"Evidence items offered: {len(items)}"
        + (f" ({', '.join(i.evidence_id for i in items)})" if items else "")
        + ". Sources not collected for this case are listed as gaps in the last item when "
        "relevant.\n"
    )
    return head + "\n\n".join(i.block() for i in items)


def option_order(unit: str, coding_id: str) -> bool:
    """True: show pass B's value first. Fixed per (case, unit) by a seeded hash (LR [80])."""
    h = hashlib.sha256(f"{ADJ_ORDER_SEED}|{coding_id}|{unit}".encode()).digest()
    return bool(h[0] & 1)


def adjudication_input(
    coding_id: str,
    anchor: str,
    items: Sequence[RenderedItem],
    disagreements: Sequence[dict[str, object]],
) -> str:
    """Evidence (by evidence id) plus the disagreeing units with both options."""
    ev = case_input(coding_id, anchor, sorted(items, key=lambda i: i.evidence_id))
    return ev + "\n\n## Disagreements\n" + json.dumps(list(disagreements), indent=1)

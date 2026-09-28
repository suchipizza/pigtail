"""Confirmation of title-only launch matches (ADR-083 E; owner decision 2026-09-27).

The launch lookup (`pigtail.briefs.outcomes.lookup_launches`, ADR-081/082) accepts a Show HN or
Launch HN post that doesn't link the repo only by its title (ADR-082 rules a-e). Verifier round
5 found that several such matches were other products with the same name. Since anchor-v4 a
title-only match counts only when:

0. the repo has **no URL-matched launch** in the brief's window (from discovery or the lookup);
   otherwise the title match is excluded (`has_url_launch`); and then it is **confirmed** by
   the first rule that holds:
1. `homepage_domain`: the post's URL domain equals the repo's homepage domain (repo metadata
   `homepage_domain`; hostnames compared lowercase without `www.` and port; a shared host such
   as github.com or medium.com never confirms, `SHARED_HOSTS`);
2. `owner_login`: the post's title or URL names the repo owner's login as a whole word
   (title: not preceded or followed by a letter, digit, `_` or `-`; URL: one of the tokens of
   host and path split on anything but letters, digits and `-`), case-insensitive; only for
   logins of at least `OWNER_MIN_CHARS` characters that are not on `KEYWORD_STOPWORDS`, and
   never when the casefolded login equals, contains or is contained in the repo name or one of
   its name parts (`preflight/preflight`, `kubeforge/kubeforge`, `acme/acme-cli`: a namesake
   post names the login too, so it says nothing; confirm-v2, verifier round 6). The login is
   already public in the stored repo name; nothing new is stored;
3. `description_keywords`: the title and the repo description share at least
   `KEYWORD_MIN_SHARED` (2) distinctive keywords (`keywords`: casefolded `[a-z0-9]+` tokens of
   at least 3 characters, not all digits, not a YC batch like `w24`, not on
   `KEYWORD_STOPWORDS`, not a part of the repo name or the owner login; a trailing `s` is
   dropped from tokens of 5+ characters not ending in `ss`);
4. `haiku`: otherwise a Haiku disambiguation check (`PROMPT`, job `title_match_check`, the
   relevance stage's model) returns `same_project: "true"`. Input: public project-level text
   only (repo name without owner, description, homepage domain; post title and URL domain; the
   owner login replaced by `[owner]`, then the LLMClient's redaction). `false` and `unsure` do
   not confirm. It is a paid step: routed through the BudgetGuard (`before_submit`, approval
   with `--approve-paid`), batched through `run_batch`, cached by the LLM cache (a cached
   verdict is served without approval), and its provenance (prompt id, version, fingerprint,
   model, backend, batch id) is stored with the match. **Fail closed:** without a client, with
   the backend refused, without approval, over a cap or on an API error the match is excluded,
   never accepted.

Unconfirmed matches are stored with `confirmed: false` and the reason (`unconfirmed:<reason>`)
and counted; confirmed ones with `confirmed: true` and the method. No title is stored; the
model's free-text reason is not stored either (it may quote the title); it stays in the LLM
cache only.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from pigtail.briefs.budget import BudgetStop
from pigtail.llm import BatchItem, BatchPending, LLMClient, LLMError, PromptSpec
from pigtail.llm.pricing import TokenUsage, cost_usd
from pigtail.llm.thinking import job_params
from pigtail.llm.types import schema_hash, sha256_text

CONFIRMATION_VERSION = "confirm-v2"  # v2: rule 2 skips logins that overlap the repo name
JOB = "title_match_check"
NAMESPACE = "hn"
OWNER_MIN_CHARS = 4
KEYWORD_MIN_CHARS = 3
KEYWORD_MIN_SHARED = 2
REASON_MAX_WORDS = 20
OUTPUT_TOKENS = 60

# Hosts many unrelated projects share: equal domains there say nothing about the project.
_SHARED_HOSTS = """
    github.com gist.github.com gitlab.com bitbucket.org codeberg.org sr.ht git.sr.ht
    sourceforge.net npmjs.com pypi.org crates.io docs.rs pkg.go.dev rubygems.org
    packagist.org hub.docker.com marketplace.visualstudio.com open-vsx.org
    chrome.google.com chromewebstore.google.com addons.mozilla.org apps.apple.com
    play.google.com medium.com dev.to substack.com hashnode.dev notion.site
    youtube.com youtu.be twitter.com x.com linkedin.com reddit.com
    news.ycombinator.com producthunt.com discord.gg discord.com t.me
    google.com docs.google.com drive.google.com huggingface.co colab.research.google.com
    readthedocs.io vercel.app netlify.app herokuapp.com pages.dev web.app
"""
SHARED_HOSTS: frozenset[str] = frozenset(_SHARED_HOSTS.split())

# English function words, launch-post boilerplate and generic product words: never distinctive.
_KEYWORD_STOPWORDS = """
    a about above after again against all also am an and any are aren as at be because been
    before being below between both but by can cannot could did do does doing don down during
    each few for from further get gets got had has have having he her here hers him his how i
    if in into is isn it its itself just let lets like made make makes me more most much must
    my no nor not now of off on once only or other our ours out over own same she should so
    some such than that the their theirs them then there these they this those through to too
    under until up upon us use used uses using very via was we were what when where which
    while who whom why will with within without would yet you your yours
    show launch hn yc ask tell new today introducing announcing released release
    open source opensource free simple fast faster easy easier better best modern lightweight
    tiny small minimal powerful first way ways built build building builds write written
    based app apps tool tools project projects library framework thing things
    alternative one two three alpha beta version v1 v2 owner people team
"""
KEYWORD_STOPWORDS: frozenset[str] = frozenset(_KEYWORD_STOPWORDS.split())

_YC_BATCH = re.compile(r"^[wsfx]\d{2}$")
_TOKEN = re.compile(r"[a-z0-9]+")
_URL_TOKEN = re.compile(r"[^a-z0-9-]+")

CONFIRMATION_RULE = (
    f"{CONFIRMATION_VERSION}: a title-only lookup match (ADR-082 a-e) counts only when the repo "
    "has no URL-matched launch in the window (discovery or lookup; else excluded, "
    "has_url_launch) and it is confirmed by the first of: (1) homepage_domain: the post's URL "
    "hostname (lowercase, no www., no port) equals the repo's homepage domain, never for a "
    "shared host (SHARED_HOSTS); (2) owner_login: the title names the owner login as a whole "
    "word (no letter, digit, _ or - on either side) or the login is a token of the URL's host "
    f"and path (split on anything but [a-z0-9-]), case-insensitive, login >= {OWNER_MIN_CHARS} "
    "characters and not a stop-word, and never when the casefolded login equals, contains or is "
    "contained in the repo name or one of its name parts (split on -, _, . and spaces; such "
    "matches fall through to rules 3 and 4); (3) description_keywords: title and description share "
    f">= {KEYWORD_MIN_SHARED} keywords (casefolded [a-z0-9]+ tokens, >= {KEYWORD_MIN_CHARS} "
    "characters, not all digits, not a YC batch, not a stop-word (KEYWORD_STOPWORDS), not a "
    "repo-name part or the owner login; a trailing s dropped from tokens of 5+ characters not "
    "ending in ss); (4) haiku: the Haiku check answers same_project 'true' ('false' and "
    "'unsure' exclude). Fail closed: no client, backend refused, no approval, a cap, or an API "
    "error exclude the match. Stored: confirmed, confirmation (method or unconfirmed:<reason>), "
    "Haiku provenance; never the title or the model's reason"
)

SYSTEM = (
    "You check whether a Hacker News launch post presents a given open-source project. You "
    "get public project-level facts only: the repository's name, its description and its "
    "homepage domain, and the post's title and link domain. Answer same_project 'true' only "
    "when the post clearly presents this same project (same product, same purpose); 'false' "
    "when it clearly presents a different product that happens to share the name; 'unsure' "
    "when the facts are not enough to tell. reason: at most 20 words, about the projects only, "
    "never about people. Reply only through the requested JSON schema."
)
TEMPLATE = "Project and post as JSON:\n\n{input}\n\nDoes the post present this project?"
PROMPT = PromptSpec(id=JOB, version="1", system=SYSTEM, template=TEMPLATE)


class TitleMatchVerdict(BaseModel):
    same_project: Literal["true", "false", "unsure"]
    reason: str = Field(description="At most 20 words, about the projects only")


# --- rules 1-3 (deterministic) --------------------------------------------------------------
def url_domain(url: str | None) -> str | None:
    """The lowercase hostname of `url` without `www.` and port (scheme optional), or None."""
    if not url or not url.strip():
        return None
    u = url.strip()
    if "://" not in u:
        u = "https://" + u
    try:
        host = (urlsplit(u).hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _owner(full_name: str) -> str:
    return full_name.split("/", 1)[0]


def _norm(tok: str) -> str:
    return tok[:-1] if len(tok) >= 5 and tok.endswith("s") and not tok.endswith("ss") else tok


def keywords(text: str | None, *, exclude: Sequence[str] = ()) -> set[str]:
    """Distinctive keywords of `text` (rule 3; module docstring)."""
    if not text:
        return set()
    drop = {_norm(x.casefold()) for x in exclude}
    out = set()
    for tok in _TOKEN.findall(text.casefold()):
        if len(tok) < KEYWORD_MIN_CHARS or tok.isdigit() or _YC_BATCH.match(tok):
            continue
        if tok in KEYWORD_STOPWORDS:
            continue
        t = _norm(tok)
        if t in KEYWORD_STOPWORDS or t in drop:
            continue
        out.add(t)
    return out


def _repo_name_parts(full_name: str) -> list[str]:
    name = full_name.split("/", 1)[-1]
    return [name, *(p for p in re.split(r"[-_.\s]+", name) if p)]


def owner_overlaps_name(full_name: str) -> bool:
    """Whether the casefolded owner login equals, contains or is contained in the repo name or
    one of its name parts (rule 2 then never confirms: a post about a namesake product names
    the login as well; verifier round 6)."""
    owner = _owner(full_name).casefold()
    for part in _repo_name_parts(full_name):
        p = part.casefold()
        if p and (owner == p or owner in p or p in owner):
            return True
    return False


def owner_named(title: str | None, url: str | None, full_name: str) -> bool:
    """Rule 2: the title or the URL names the owner login as a whole word (never for a login
    that overlaps the repo name, `owner_overlaps_name`)."""
    owner = _owner(full_name)
    if len(owner) < OWNER_MIN_CHARS or owner.casefold() in KEYWORD_STOPWORDS:
        return False
    if owner_overlaps_name(full_name):
        return False
    if title:
        pat = r"(?<![A-Za-z0-9_-])" + re.escape(owner) + r"(?![A-Za-z0-9_-])"
        if re.search(pat, title, re.IGNORECASE):
            return True
    if url:
        u = url.strip() if "://" in url else "https://" + url.strip()
        try:
            parts = urlsplit(u)
            text = f"{parts.hostname or ''} {parts.path}".casefold()
        except ValueError:
            return False
        if owner.casefold() in set(_URL_TOKEN.split(text)):
            return True
    return False


def confirm_by_rules(
    *,
    full_name: str,
    description: str | None,
    homepage_domain: str | None,
    title: str | None,
    url: str | None,
) -> str | None:
    """The first of rules 1-3 that confirms the title match, or None (then rule 4, Haiku)."""
    d = url_domain(url)
    if d and homepage_domain and d == homepage_domain and d not in SHARED_HOSTS:
        return "homepage_domain"
    if owner_named(title, url, full_name):
        return "owner_login"
    name_parts = [p for p in re.split(r"[-_.\s]+", full_name.split("/", 1)[-1]) if p]
    exclude = [*name_parts, _owner(full_name)]
    shared = keywords(title, exclude=exclude) & keywords(description, exclude=exclude)
    if len(shared) >= KEYWORD_MIN_SHARED:
        return "description_keywords"
    return None


# --- rule 4: the Haiku check ------------------------------------------------------------------
def haiku_input(
    full_name: str,
    description: str | None,
    homepage_domain: str | None,
    title: str | None,
    url: str | None,
) -> str:
    """What the model sees (public project-level text, the owner login as `[owner]`)."""
    from pigtail.briefs.candidates import strip_owner

    owner, _, name = full_name.partition("/")
    return json.dumps(
        {
            "repo_name": strip_owner(name, owner),
            "repo_description": strip_owner(description, owner),
            "repo_homepage_domain": homepage_domain or None,
            "post_title": strip_owner(title, owner),
            "post_url_domain": url_domain(url),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


@dataclass(frozen=True)
class Check:
    """One title match waiting for the Haiku check: `key` is the caller's (ref, item id); the
    item id is an HN item id or a Product Hunt post id (ADR-085)."""

    key: tuple[str, int | str]
    input_text: str


@dataclass(frozen=True)
class Outcome:
    confirmed: bool
    confirmation: str  # haiku | unconfirmed:<reason>
    provenance: dict[str, Any] | None = None


def _fail(checks: Sequence[Check], reason: str) -> dict[tuple[str, int | str], Outcome]:
    return {c.key: Outcome(False, f"unconfirmed:{reason}") for c in checks}


def est_usd_per_check(
    model: str, text: str, *, batch: bool, prompt: PromptSpec = PROMPT
) -> float | None:
    """List-price estimate of one check (chars / 4 tokens; no prompt cache: the prefix is far
    below Haiku's minimum cacheable length)."""
    tin = (len(prompt.system) + len(prompt.template) + len(text)) // 4 + 50
    return cost_usd(model, TokenUsage(input=tin, output=OUTPUT_TOKENS), batch=batch)


@dataclass
class Confirmer:
    """Runs rule 4 for a group of title matches (module docstring). `llm` None, or the backend
    refused by `check_backend`, fails closed. `prompt`, `job` and `namespace` default to the HN
    title check; the Product Hunt check (ADR-085) passes `PH_PROMPT`, `PH_JOB`, `PH_NAMESPACE`
    (`ph_confirmer`)."""

    llm: LLMClient | None
    brief_run_id: str | None = None
    before_submit: Callable[[str, int, float | None], None] | None = None
    check_backend: Callable[[str], None] | None = None
    poll_seconds: float = 60.0
    timeout_seconds: float | None = None
    sleep: Callable[[float], None] | None = None
    prompt: PromptSpec = PROMPT
    job: str = JOB
    namespace: str = NAMESPACE

    def _key(self, text: str) -> str:
        assert self.llm is not None
        backend = self.llm.backend_for(self.job).name
        model = self.llm.model_for(self.job)
        ih = sha256_text(self.llm.redact(text, self.namespace))
        return self.llm.cache_key(backend, model, self.prompt, schema_hash(TitleMatchVerdict), ih)

    def run(self, checks: Sequence[Check]) -> dict[tuple[str, int | str], Outcome]:
        """One outcome per check. `BatchPending` propagates (the stage resumes and collects
        the batch); every other failure fails closed."""
        if not checks:
            return {}
        if self.llm is None:
            return _fail(checks, "haiku_unavailable")
        llm = self.llm
        try:
            backend = llm.backend_for(self.job).name
            model = llm.model_for(self.job)
            if self.check_backend is not None:
                self.check_backend(backend)
        except (BudgetStop, ValueError):
            return _fail(checks, "haiku_unavailable")
        out: dict[tuple[str, int | str], Outcome] = {}
        cached = [c for c in checks if llm.store.cache_get(self._key(c.input_text)) is not None]
        todo = [c for c in checks if c not in cached]
        for c in cached:  # served from the cache: no money, no approval needed
            res = llm.complete(
                self.prompt,
                c.input_text,
                TitleMatchVerdict,
                job=self.job,
                namespace=self.namespace,
                brief_run_id=self.brief_run_id,
            )
            out[c.key] = _outcome(res)
        if not todo:
            return out
        batch = llm.batches_for(self.job)
        est: float | None = 0.0
        if backend == "api":
            ests = [
                est_usd_per_check(model, c.input_text, batch=batch, prompt=self.prompt)
                for c in todo
            ]
            est = None if any(e is None for e in ests) else max(e or 0.0 for e in ests)
        items = [
            BatchItem(ref=f"t{i:05d}", input_text=c.input_text, namespace=self.namespace)
            for i, c in enumerate(todo)
        ]
        by_ref = {it.ref: c for it, c in zip(items, todo, strict=True)}
        kw: dict[str, Any] = {"poll_seconds": self.poll_seconds}
        if self.sleep is not None:
            kw["sleep"] = self.sleep
        try:
            run = llm.run_batch(
                self.prompt,
                items,
                TitleMatchVerdict,
                job=self.job,
                brief_run_id=self.brief_run_id,
                before_submit=self.before_submit,
                est_usd_per_item=est,
                timeout_seconds=self.timeout_seconds,
                **kw,
            )
        except BatchPending:
            raise
        except BudgetStop as e:
            reason = "haiku_not_approved" if e.kind == "approval" else "haiku_budget_stop"
            out.update(_fail(todo, reason))
            return out
        except LLMError:
            out.update(_fail(todo, "haiku_unavailable"))
            return out
        for ref, c in by_ref.items():
            if ref in run.results:
                out[c.key] = _outcome(run.results[ref])
            else:
                out[c.key] = Outcome(False, "unconfirmed:haiku_failed")
        return out


def _outcome(res: Any) -> Outcome:
    verdict = res.output.same_project
    p = res.provenance()
    prov = {
        k: p.get(k)
        for k in ("prompt_id", "prompt_version", "prompt_fingerprint", "model", "backend")
    }
    prov["batch_id"] = p.get("batch_id")
    prov["cached"] = bool(res.cached)
    if verdict == "true":
        return Outcome(True, "haiku", prov)
    return Outcome(False, f"unconfirmed:haiku_{verdict}", prov)


def _sha(words: frozenset[str]) -> str:
    return hashlib.sha256(" ".join(sorted(words)).encode()).hexdigest()


def resolved_model(job: str) -> str:
    """The model this instance resolves for `job` (its LLM stage's model from the settings:
    `LLM_MODEL_<STAGE>` > `LLM_MODEL` > the code default, R15.8). It goes into the
    pre-registered parameters (verifier round 6), so a pre-registration binds the model id and
    a changed model changes `selection_params_sha256`."""
    import os

    from pigtail.config import stage_models
    from pigtail.llm.stages import stage_for

    return stage_models(dict(os.environ))[stage_for(job)]


def confirmation_params() -> dict[str, Any]:
    """The E rule as it goes into the pre-registered selection parameters (ADR-083, ADR-084)."""
    return {
        "version": CONFIRMATION_VERSION,
        "rule": CONFIRMATION_RULE,
        "owner_min_chars": OWNER_MIN_CHARS,
        "keyword_min_chars": KEYWORD_MIN_CHARS,
        "keyword_min_shared": KEYWORD_MIN_SHARED,
        "keyword_stopwords_sha256": _sha(KEYWORD_STOPWORDS),
        "shared_hosts_sha256": _sha(SHARED_HOSTS),
        "haiku": {
            "job": JOB,
            "stage": "relevance",
            "default_model": "claude-haiku-4-5-20251001",
            "model": resolved_model(JOB),
            "prompt_id": PROMPT.id,
            "prompt_version": PROMPT.version,
            "prompt_fingerprint": PROMPT.fingerprint,
            "schema_sha": schema_hash(TitleMatchVerdict),
            "thinking": job_params(JOB, resolved_model(JOB)),  # ADR-087
            "confirms_only": "true",
        },
    }


# --- Product Hunt (ADR-085): the E rules adapted to a launch that has no URL search ------------
# A Product Hunt post is matched to a repo by its name alone (a slug candidate, or a topic scan;
# `pigtail.briefs.launch_sources`), so every match is "name-only": it counts only when the post's
# name fills the product slot (it normalizes to the repo name) and the first of these holds:
# (1) `github_url`: the tagline or description links the repo's github.com/owner/name;
# (2) `homepage_domain`: the tagline or description names the repo's homepage domain (a whole
#     domain, `www.` optional; never a shared host);
# (3) `description_keywords`: tagline + description and the repo description share at least
#     `KEYWORD_MIN_SHARED` keywords (`keywords`, the tokenizer of rule 3 above);
# (4) `haiku`: the Product Hunt variant of the Haiku check (`PH_PROMPT`, job `ph_match_check`)
#     answers same_project "true"; fail closed exactly like the HN check.
# Rule 2 of the HN check (owner login) has no counterpart: a Product Hunt post has no link to
# compare the login with, and its makers are never requested.
PH_CONFIRMATION_VERSION = "ph-confirm-v1"
PH_JOB = "ph_match_check"
PH_NAMESPACE = "producthunt"
PH_TEXT_CHARS = 300  # the start of the post's description the model sees
PH_SYSTEM = (
    "You check whether a Product Hunt launch presents a given open-source project. You get "
    "public project-level facts only: the repository's name, its description and its homepage "
    "domain, and the launch's product name, tagline and the start of its description. Answer "
    "same_project 'true' only when the launch clearly presents this same project (same product, "
    "same purpose); 'false' when it clearly presents a different product that happens to share "
    "the name; 'unsure' when the facts are not enough to tell. reason: at most 20 words, about "
    "the projects only, never about people. Reply only through the requested JSON schema."
)
PH_TEMPLATE = (
    "Project and Product Hunt launch as JSON:\n\n{input}\n\nDoes the launch present this project?"
)
PH_PROMPT = PromptSpec(id=PH_JOB, version="1", system=PH_SYSTEM, template=PH_TEMPLATE)
PH_CONFIRMATION_RULE = (
    f"{PH_CONFIRMATION_VERSION}: a Product Hunt post found by slug or topic scan counts as the "
    "repo's launch only when its name normalizes (casefolded, every character other than a-z "
    "and 0-9 removed) to the repo's GitHub name (the product slot), and it is confirmed by the "
    "first of: (1) github_url: the tagline or description links the repo's github.com/owner/"
    "name; (2) homepage_domain: the tagline or description names the repo's homepage domain as "
    "a whole domain (www. optional), never a shared host (SHARED_HOSTS); (3) "
    f"description_keywords: tagline + description and the repo description share >= "
    f"{KEYWORD_MIN_SHARED} keywords (the rule-3 tokenizer and stop-list, repo-name parts and the "
    "owner login excluded); (4) haiku: the Product Hunt Haiku check answers same_project 'true' "
    "('false' and 'unsure' exclude; fail closed: no client, backend refused, no approval, a "
    "cap or an API error exclude the match). A repo name shorter than 5 characters or made "
    "only of stop-list words (outcomes.TITLE_STOPLIST) is confirmed by rules 1-2 only. "
    "Stored: post id, createdAt, featuredAt, votesCount, commentsCount, route, confirmed, "
    "confirmation (method or unconfirmed:<reason>) and Haiku provenance; never a name, tagline "
    "or description"
)


def domain_in_text(text: str | None, domain: str) -> bool:
    """Whether `text` names `domain` as a whole domain (`www.` optional; `sub.domain` and
    `domain.other` don't count), case-insensitive."""
    if not text or not domain:
        return False
    pat = r"(?<![A-Za-z0-9.@-])(?:www\.)?" + re.escape(domain) + r"(?![A-Za-z0-9-]|\.[A-Za-z0-9])"
    return re.search(pat, text, re.IGNORECASE) is not None


def ph_confirm_by_rules(
    *,
    full_name: str,
    description: str | None,
    homepage_domain: str | None,
    tagline: str | None,
    post_description: str | None,
    urls_only: bool = False,
) -> str | None:
    """The first of the Product Hunt rules 1-3 that confirms the match, or None (then rule 4).
    `urls_only`: a short or common repo name, confirmed by rules 1-2 only."""
    from pigtail.connectors.hn import github_repos_in_text

    text = " ".join(t for t in (tagline, post_description) if t)
    if full_name.lower() in github_repos_in_text(text):
        return "github_url"
    d = (homepage_domain or "").lower()
    if d and d not in SHARED_HOSTS and domain_in_text(text, d):
        return "homepage_domain"
    if urls_only:
        return None
    exclude = [*_repo_name_parts(full_name)[1:], _owner(full_name)]
    shared = keywords(text, exclude=exclude) & keywords(description, exclude=exclude)
    if len(shared) >= KEYWORD_MIN_SHARED:
        return "description_keywords"
    return None


def ph_haiku_input(
    full_name: str,
    description: str | None,
    homepage_domain: str | None,
    name: str | None,
    tagline: str | None,
    post_description: str | None,
) -> str:
    """What the Product Hunt check's model sees: public project-level text (the owner login as
    `[owner]`; the LLMClient's alias redaction then runs on it)."""
    from pigtail.briefs.candidates import strip_owner

    owner, _, repo = full_name.partition("/")
    head = (post_description or "")[:PH_TEXT_CHARS] or None
    return json.dumps(
        {
            "repo_name": strip_owner(repo, owner),
            "repo_description": strip_owner(description, owner),
            "repo_homepage_domain": homepage_domain or None,
            "launch_name": strip_owner(name, owner),
            "launch_tagline": strip_owner(tagline, owner),
            "launch_description_start": strip_owner(head, owner),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def ph_confirmation_params() -> dict[str, Any]:
    """The Product Hunt confirmation rule as it goes into the pre-registered parameters."""
    return {
        "version": PH_CONFIRMATION_VERSION,
        "rule": PH_CONFIRMATION_RULE,
        "keyword_min_chars": KEYWORD_MIN_CHARS,
        "keyword_min_shared": KEYWORD_MIN_SHARED,
        "keyword_stopwords_sha256": _sha(KEYWORD_STOPWORDS),
        "shared_hosts_sha256": _sha(SHARED_HOSTS),
        "haiku": {
            "job": PH_JOB,
            "stage": "relevance",
            "default_model": "claude-haiku-4-5-20251001",
            "model": resolved_model(PH_JOB),
            "prompt_id": PH_PROMPT.id,
            "prompt_version": PH_PROMPT.version,
            "prompt_fingerprint": PH_PROMPT.fingerprint,
            "schema_sha": schema_hash(TitleMatchVerdict),
            "thinking": job_params(PH_JOB, resolved_model(PH_JOB)),  # ADR-087
            "text_chars": PH_TEXT_CHARS,
            "confirms_only": "true",
        },
    }

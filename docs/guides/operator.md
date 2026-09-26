# Operator guide (draft — rewritten in M12/M14)

> **Scope since M11 (ADR-047, ADR-048, ADR-049).** pigtail no longer collects globally. You write
> a research brief about your own project; pigtail analyses that project's neighbourhood with
> **batch runs** on your own machine, plus launch-mode tracking. The 50k-repo watch list, the
> all-GitHub search sweeps, the GH Archive velocity scan and the global breakout detection
> (detection v1), the held-out split with its unseal log, and the settle-lag collection were
> removed in M11. Their code is in the git tag `archive/global-collection`; their tables were
> dropped by migration 0014 (row counts only are logged in `deletion_log`, reason
> `purpose_limitation`). Data kept in the remaining tables (repos, cases, evidence, per-repo star
> history, per-repo events, HN tables) stays as a cache briefs can reuse. Briefs arrive in M12,
> brief-scoped discovery in M13, the launchd schedule and launch mode in M14.
>
> To see what that cache holds, run `uv run pigtail report inventory` (a Markdown table) or
> `uv run pigtail report inventory --json`: per kept table, the row count, the number of distinct
> repos and the date range (UTC days), plus the data version (latest migration) and the code
> commit. It prints counts only (no names, handles, URLs or ids), writes nothing and records no
> run. After your first brief's shortlist is final, data no brief references is deleted
> (purpose limitation, ADR-047.6).

## Your duties as controller (CB-21)
If you run pigtail, you are the controller of the personal data it collects on your host, not the
pigtail maintainers. Before the first capture, read
[`docs/compliance/controller-duties.md`](../compliance/controller-duties.md). It lists what you must
adopt and do: the LIA and DPIA, a published privacy notice, the record of processing activities,
handling requests with the `pigtail privacy` commands, retention and deletion sync, encryption,
key handling and backups, the breach runbook, the subscription-vs-api scope of the LLM backend
(ADR-008, ADR-023), and EU or Swiss hosting. Related:
- [Record of processing activities](../compliance/ropa.md) (template to fill in)
- [Breach response runbook](../compliance/runbooks/breach.md)
- [Opt-out key (`OPTOUT_KEY`, formerly `PSEUDONYM_KEY`) management and
  rotation](../compliance/runbooks/key-rotation.md). Don't change the key without reading it:
  opt-outs stop matching. pigtail detects a changed key and refuses to run (see "Opt-out key
  check" under Privacy operations). To rotate the key,
  follow the runbook's §4.1, which uses `pigtail privacy rekey` ("Rotating the key" under
  Privacy operations); scheduled rotation every 24 months is allowed (runbook §4.3).

Set `PIGTAIL_ADR022_PERSON_SOURCES_OK=1` only once those duties are met.

## Your first brief (M12; PRD F18, D7)
A **research brief** describes your project, its neighbourhood and what success means to you;
every later stage (discovery, shortlist, report, plan) works from it. Plan on well under an hour:
about 15 minutes for credentials, 30 for the brief, a few for the estimate.

**1. Credentials (your own, never shared, never in git).**
- `GITHUB_TOKEN`: a fine-grained token with public-repository read access only (see "GitHub
  token and budgets" below). One token; pooling tokens is against GitHub's terms.
- Claude: log in to Claude Code on your own plan (`LLM_BACKEND=subscription`, the default) or
  set `ANTHROPIC_API_KEY` with `LLM_BACKEND=api` (see "LLM backend" below). Check with
  `uv run pigtail llm smoke`.
- Optional: a BigQuery project, only if you enable `optional_sources.bigquery` (off by default).
- Run `uv run pigtail doctor`: disk encryption must be on for the machine that holds your data.

**2. Where briefs live.** Outside git, in `PIGTAIL_BRIEFS_DIR/<brief_id>/vNNNN.yaml` (default
`~/.pigtail/briefs`; directories 0700, files 0600; ADR-071.3, R18.9). `pigtail backup create`
includes that directory (see "Backups and restore"). Every save of changed content writes a new,
immutable version; old versions stay readable and diffable. An install can hold several briefs.
If you have briefs from before M21 in `PIGTAIL_DATA_DIR/briefs`, move them once:
```bash
uv run pigtail brief migrate-store --from data/briefs --dry-run   # counts only, moves nothing
uv run pigtail brief migrate-store --from data/briefs             # moves the version files
```
It moves bytes (never reads or prints a brief), never overwrites a version that already exists
in the target (a differing copy is reported as a conflict and both are kept) and is safe to run
again. `PIGTAIL_BRIEFS_IN_DATA_DIR=1` keeps using the old location, explicitly. The repo ships
one synthetic example, [`docs/examples/brief-example.yaml`](../examples/brief-example.yaml); the
schema is [`schemas/brief/v1.2.json`](../../schemas/brief/v1.2.json). Briefs written with schema
v1 or v1.1 ([`v1.json`](../../schemas/brief/v1.json), [`v1.1.json`](../../schemas/brief/v1.1.json))
still load; the next version you save is written as v1.2 (v1: one-line reference cases become
objects, and entries labelled `distribution_exemplar | ...` move to `distribution_exemplars`;
v1.1: `budget.llm_api_usd` is added to `budget.money_usd`, now the brief's total cap). The
private-data scan (pre-commit and CI) refuses any other brief file in git, by path
(`*/briefs/*.yaml`, `*brief*.yaml`) and by content (`brief_id:` plus a `project:` mapping, in
block, flow or indented style, or a top-level `brief_id:` plus `expansion:`, i.e. an expansion
proposal file), so don't paste a brief or a proposal into an issue, doc or fixture either.

**3. Write the brief.** Either in the web app (`/briefs` → *New brief*: a guided form pre-filled
with the example, plus a YAML tab for import and export; both are validated by the same model),
or on the command line:
```bash
uv run pigtail brief new --example --id my-project     # copy of the synthetic example, as v1
uv run pigtail brief edit my-project                   # opens $EDITOR; saving makes v2, v3, ...
uv run pigtail brief edit my-project --from my.yaml    # or save an edited file as the next version
uv run pigtail brief validate my.yaml                  # every problem names its field
uv run pigtail brief show my-project [--version 1] [--json]
uv run pigtail brief list
uv run pigtail brief versions my-project
uv run pigtail brief diff my-project 1 2               # field-level changes between versions
uv run pigtail brief expand my-project --out p.yaml    # LLM expansion PROPOSAL (not saved)
uv run pigtail brief expand my-project --accept p.yaml # save your edited proposal as a version
uv run pigtail brief expand my-project --edit          # or: propose, edit in $EDITOR, save
```
**Stale edits are refused.** `brief edit --from FILE` treats the file as an edit of the version
named in its `version:` line (files from `pigtail brief show` have one) and refuses it if a newer
version exists, so an old export can't silently revert later changes; export the latest, re-apply
your edit and save again. A file without a `version:` line is refused too, unless you pass
`--force-latest` to take it as an edit of the latest version; `--base-version N` overrides both.
The web app and API refuse the same way (`409`); an API client that sends neither `base_version`
nor a `version` field must set `force_latest: true` to save over the latest version.

**LLM expansion (R18.7).** `pigtail brief expand` (or *Propose expansion* in the `/briefs`
editor) asks the model to propose a problem statement, users, keywords, topics, competitors (names
and optional URLs), GitHub topics and search queries. Only the project description, target users,
business model and the field's include/exclude lists are sent (not the name, context, seed or
reference projects, audience, budget or notes), through the same `LLMClient` as every other call
(job `brief_expansion`, synthesis model, a standard call; identifiers stripped; cached). It is one
call of about 4,500 tokens (about USD 0.04 at list price on `api`), checked against the budget
first: on the `api` backend it needs `--approve-paid` (or the approval box in the editor) and must
fit both `budget.money_usd` and the monthly cap; the `subscription` backend costs no money. The
call uses the brief's `budget.llm_backend` and is refused, never
rerouted, if `LLM_BACKEND` differs, unless you set a per-job override
(`LLM_BACKEND_OVERRIDES=brief_expansion:api`). The result is a **proposal**: nothing is saved until
you accept it, and model output is unverified, so check every competitor and URL. Accepting saves
a new version whose `expansion` block records the prompt id, version and fingerprint, the model and
backend, and whether you edited the proposal. Runs make no expansion call (the estimate shows it
separately).
What to fill in: the project and its target users; the **core field** with include/exclude
boundaries, and the adjacent fields to widen to if the core field is too small (R4.10); seed
projects (names only; discovery proposes matches you confirm) and **reference cases**, named
projects always studied whatever their outcome (R4.11, ADR-057.4): a name, URLs, an optional
`owner/repo` (without one, pigtail picks the repo the project's launch posts linked to and shows
the choice for your confirmation in the shortlist review; an unresolved one doesn't block the
run) and a note; **distribution exemplars** (ADR-057.1), projects chosen for exceptional
distribution whatever their field, each studied with 1–2 losers matched on launch type, launch
period and audience bucket (never on field), which adds deep-forensics cases to the estimate;
the report options (absolute numbers per class and panel, transferability labels on exemplar
patterns; both on by default); the window (12–18 months); the **success definition**:
one primary dimension (attention, adoption, community or business), its threshold, minimums on
the others, and the fallbacks for projects without measurable adoption or too few winners
(ADR-053.2); weights only under "advanced"; your own audience per channel as a reach band
(`none`, `r1` < 1k, `r2` < 10k, `r3` < 100k, `r4`, `unknown`; a number you type is turned into a
band and not stored); channels to use and avoid; geography; 15–25 winners and losers with the
matching rules (ADR-054); and the budget.

**4. Cost expectations and the budget.** Two money caps, both hard stops (R15.11, ADR-064.4,
ADR-072.4):
- `budget.money_usd` in the brief: the brief's **total** money cap over all its runs, API spend
  included (suggested USD 150; default 0, so nothing is spent unless you set it). Trendshift, X
  and BigQuery are off by default; each one you enable is a paid step against the same cap. The
  GitHub API and Hacker News are free with your own token.
- `BUDGET_USD_MONTH` for the instance (default USD 200): API spend in the current calendar month.

Every paid step, API calls included, is listed in the estimate and needs explicit approval
(`--approve-paid`); without it nothing starts. A run that would cross either cap stops before the
step with a resumable checkpoint; going on needs the owner's approval of more spend (H6).
pigtail **never switches backends on its own**. On the `subscription` backend product calls cost
no money and are not capped by `budget.subscription_share`, which applies only to the agents
building pigtail (ADR-064.4); usage limits there pause the queue and resume later (R15.5).

**Models and batching (R15.8, R15.9).** Each LLM stage has its own model: relevance filter
`LLM_MODEL_RELEVANCE` (default `claude-haiku-4-5-20251001`), extraction, coding and adjudication
`LLM_MODEL_EXTRACTION` (`claude-sonnet-5`), synthesis, report, plan and brief expansion
`LLM_MODEL_SYNTHESIS` (`claude-opus-5-5`); `LLM_MODEL`, if set, is the fallback for a stage without
its own variable. On `api`, every stage that isn't time-sensitive goes through the Message Batches
API (half price; results usually within an hour, at most 24 h); batch ids are stored in the
database, so a paused or restarted run collects its batches instead of paying twice. Interactive
calls (expansion) and launch mode use standard calls; `LLM_BATCH=0` sends standard calls
everywhere. The system prompt and the codebook are sent as a cached prefix (prompt caching), and
evidence is trimmed before sending (reposts deduplicated, long threads cut to their first and
last items, only relevant excerpts of long items; R15.10). Every output records its model, prompt
version and batch id.

Before every run, look at the estimate:
```bash
uv run pigtail brief estimate my-project          # or the "Cost estimate" panel on /briefs/my-project
```
It shows GitHub requests per bucket (core, GraphQL, search) and the hours they take at the
default 70 % caps, the LLM calls, tokens and **USD per stage and model** (batch or standard,
cached prefix priced at the cache rates), and the total against the brief's cap (minus what the
brief has already spent) and the monthly cap (minus this month's spend). Prices come from a dated
table in `src/pigtail/llm/pricing.py` (list prices as of 2026-06-24; check the pricing page before
a large run). **Every figure is an estimate** from the `estimate-v3` planning model; the pilot's
measured cost per case replaces the per-unit placeholders (M23). For the synthetic example brief
on `api`, expect roughly 7,700 GitHub requests (about 2 hours of API time), about 12M LLM tokens
and about USD 17 at list price with batching (about USD 30 without); of that, a `pigtail run`
of the M22 stages (discovery, relevance filter, shortlist) is about USD 0.56 (75 batch requests
of ~20 candidates on the relevance model, an upper bound of 1,500 candidates). The estimate exits with code 3
when paid steps need approval, and with code 4 (approval not recorded) when the estimate exceeds a
cap. When you re-run an edited brief, the estimate and the run record list which stages are reused
and which are recomputed (R18.4).

**5. Run it.** See "Running a brief (discovery, shortlist and selection)" below. Runs record the brief
version and content hash, the data version and the code, prompt, rubric and model versions
(`brief_runs`, R18.6), and a run that reaches its budget stops with a resumable checkpoint
(status `paused_budget`). The actual cost of every model call (tokens in and out, prompt-cache
reads and writes, batch id, USD) goes to the cost ledger with its brief run and case
(`llm_cost_ledger`), which the pilot report uses for the cost per case.

## Running a brief (discovery, shortlist and selection) (M22; PRD R4.3, R4.5–R4.11, R19.1)
`pigtail run` runs a brief's stages on your machine, with Postgres (`DATABASE_URL`) holding the
checkpoints. M22 has four stages: discovery, relevance, shortlist and, once you have finalized
the shortlist, selection (outcome sort, winners and matched losers, balance, sensitivity). Later
milestones add deep forensics.
```bash
uv run pigtail run --brief my-project --dry-run         # estimate and plan; no call, no write
uv run pigtail run --brief my-project --approve-paid    # run (or resume) discovery → relevance → shortlist
uv run pigtail brief preregister my-project --print-hashes   # after finalize: hashes to pre-register
uv run pigtail brief preregister my-project --file docs/preregistration/<file> --commit <sha>
uv run pigtail run --brief my-project                   # after finalize + pre-registration: the selection
uv run pigtail brief selection show my-project [--json] # winners, losers, balance, sensitivity
uv run pigtail run --brief my-project --stage discovery # only some stages (repeatable)
uv run pigtail run --brief my-project --incremental     # refresh a completed run
uv run pigtail run --brief my-project --wait-minutes 30 # leave a batch running after 30 min
```
**Before it starts** it prints the cost estimate (the whole brief, then "This run": GitHub and
HN requests, the relevance filter's requests, model and USD) against the brief's remaining cap
and the monthly cap. On the `api` backend it starts only with `--approve-paid` (or an approval
recorded by `pigtail brief estimate --approve-paid`); an estimate above a cap is a warning, and
the run then hard-stops at the cap. Discovery needs `GITHUB_TOKEN`.

**1. Discovery (R4.5).** From the brief's accepted expansion and field: one GitHub search per
keyword, search query and the core field (`<term> in:name,description,topics`) and one per
GitHub topic (`topic:<slug>`), each limited to repos created in the brief's window with at least
10 stars (not archived, no forks; one page of 100, sorted by stars); **Show HN** stories matching
each keyword in the window that link a GitHub repo (project-level metadata only: title, URL,
points, time; the poster is never read or stored and the raw page is dropped at once, so this
runs before the person-level hold is lifted); **awesome lists** named in the brief (a GitHub URL
whose repo name starts with `awesome`) or found through the brief's GitHub topics, whose README
links become candidates when the linked repo was created in the window. GH Archive adds activity
signals to candidates already found only if you set `PIGTAIL_DISCOVERY_GHARCHIVE_HOURS` (sampled
hourly dumps; off by default). Every **reference case** and **distribution exemplar** in the
brief is always added, with its repo chosen by the launch-link rule (ADR-054.3): the brief's
`repo`, else a GitHub URL in its `urls`, else the repo its Show HN launch posts linked to. When
that gives several repos or none, it is kept as **unresolved** with candidate matches for you to
confirm in the review; it never blocks the run. Candidates are stored per brief version,
project-level only (repo name and id, public description with identifiers and the owner login
removed, topics, stars, dates, and which sources found it), de-duplicated, capped at 1,500. Repos
on the refusal list are skipped. The GitHub request budget (70 % caps per hour, plus a per-run
cap) pauses discovery with a checkpoint; run the command again later to continue.

**2. Relevance filter (R4.6).** Every candidate is judged against a **rubric written from your
brief** (core field, include and exclude lists, widening steps, target users, problem
statement; never the project name, reference names, audience or budget) by the relevance model
(`LLM_MODEL_RELEVANCE`, Haiku) through the Message Batches API, about 20 candidates per request.
The model sees public repo metadata only: the repo name *without its owner*, the owner type,
description, topics, language and a README excerpt of at most 1,200 characters, with identifiers
replaced by per-call aliases. For each candidate it returns a verdict (`relevant`,
`not_relevant`, `uncertain`), a reason (at most 30 words), the distance from the core field
(0 core, 1–2 widening steps) and the panel. Each verdict is stored with the rubric version
(`rubric-v1-<hash>`: a new rubric means new verdicts), prompt version, model, batch id and input
hash. A batch that is still running when `--wait-minutes` runs out leaves the run
`waiting_batch` (exit code 5): run the same command again and it collects that batch; nothing is
submitted or paid twice.

**3. Shortlist review (R4.7, D7).** The run ends `awaiting_review`. Review in the web app
(`/briefs/<id>/shortlist`) or on the command line:
```bash
uv run pigtail brief shortlist show my-project [--verdict relevant] [--panel field] [--distance 0]
uv run pigtail brief shortlist accept my-project owner/name ... --reason "fits the core field"
uv run pigtail brief shortlist reject my-project --verdict uncertain --reason "..."   # bulk
uv run pigtail brief shortlist add my-project https://github.com/owner/name --reason "missed"
uv run pigtail brief shortlist add my-project <url> --resolves named:reference:0 --reason "..."
uv run pigtail brief shortlist finalize my-project [--as owner]
```
Every decision needs a reason and is logged (never edited) with the brief version, the run, the
reviewer role (`--as user|owner|verifier`, default `user`; the web app logs `user`), where it was
made and the time. Undecided model-`relevant` candidates are *proposed*; finalizing is refused
while any `relevant` or `uncertain` candidate is undecided (use the bulk actions). Named
reference cases and exemplars that resolved to a repo stay on the shortlist unless you reject
them; for an unresolved one, pick one of its candidate repos (`add … --resolves`, or *Use this
repo* in the web app). **Precision** is the share of model-`relevant` field candidates you kept
among those you decided on; the target is 80 %, a lower value is shown, not hidden, and the label
says who checked (`owner-checked`, `verifier-checked, not owner-checked`, `user-checked`). When
every decided candidate was settled by a bulk action on the filter's own verdict (`accept
--verdict relevant`), nobody looked at the items and the label is `not item-reviewed`; a mix
says how many were bulk decisions. `shortlist show` also lists the **brief fields still at their
default** (confirm them here, R4.7) and the brief's warnings. While in review, proposed and accepted repos are in the mention scope as `in_review`; finalizing writes
the final set as `final` and the rest `removed`. An added repo's metadata is filled by the next
`pigtail run --incremental`. The refusal list is checked again when you finalize and before any
selection fetch: a repo refused only by its GitHub id that you added by URL is dropped from the
brief version as soon as its id is known, and its star history is never fetched.

**4. Pre-registration (R8.2, ADR-065; outcome-model §5.8).** The outcome sort is the point of no
return, so the brief version's hypotheses and tests are pre-registered **before** it. Copy
`docs/preregistration/TEMPLATE-brief.md`, fill it in **without brief content** (it is public:
paste the SHA-256 values from `pigtail brief preregister my-project --print-hashes` instead of
the success definition and selection settings), commit and push it, then record it:
`pigtail brief preregister my-project --file docs/preregistration/<file> --commit <sha>`. That
stores the brief version and content hash, the two partial hashes, the file's path and SHA-256
and the commit (table `brief_preregistration`). Until then the selection is refused with exit
code 7, before anything is fetched, computed or stored. A file that quotes brief text is
refused; so is a pre-registration once the version already has a selection. Editing the brief
(a new version) or a new selection rule needs a new pre-registration.

**5. Selection (R4.8, R4.3, R4.9, R4.10, R4.11; ADR-077, ADR-078).** It runs only on a
**final**, **pre-registered** shortlist: after `shortlist finalize` and `brief preregister`, run
`pigtail run --brief my-project` again (or with `--stage selection`) and it continues the same
run with this stage alone. It makes no model call.
- **Outcome data.** With `GITHUB_TOKEN` set, it first fetches each shortlisted repo's **star
  history** (daily net stars, back to 60 days before the brief's window; 1–3 core requests per
  repo, conditional) and fills missing metadata (creation date, language) for repos you added by
  URL. Without a token it uses what is already stored. The GitHub budget pauses it like
  discovery (exit 4; run again to continue). **Only star-based metrics exist so far**:
  `att.stars@30/@90` (raw net stars over 30 or 90 endpoint days from the anchor, labelled
  "unfiltered, anomaly-checked") and `att.hn_points` (from the Show HN posts discovery recorded).
  Registry downloads, dependents, contributor metrics and business signals have no connector
  yet and are `unknown` (reason `no_connector`); a value is never imputed. A brief whose primary
  dimension or thresholds use those metrics therefore gets **no winners** until the connectors
  exist, and the result says how many candidates each dimension left undetermined. The
  connectors (npm and crates.io downloads, returning external contributors from GitHub pull
  requests; PyPI via BigQuery and deps.dev dependents optional) are milestone **M23b**
  (ADR-080); until then rank on attention, as the example brief does.
- **Anchor T** per candidate: its first Show HN launch post, or the first star burst
  (`velocity-v0`) in the window, by the outcome model's rule (§2.2). A value whose horizon hasn't
  passed (`T + k + 3 days`) is `pending`. A candidate without an anchor can't be sorted.
- **Outcome sort** (outcome model §3, §5): percentiles within the final shortlist (field and
  reference repos with an anchor; at least 20 observed values, else `unknown`). A candidate
  qualifies when it meets every threshold; `unknown` or `pending` never meets one and makes it
  *undetermined*, not a loser. Qualifiers are ranked on the primary dimension (or the weights),
  ties by a hash of the brief id, version and repo. The top `panel.winners` are the **winners**;
  candidates that fail a threshold on an observed value form the **loser pool**.
- **Matched losers** (ADR-054.1, ADR-078): eligible losers have the same `panel.exact_match`
  values (founder audience bucket, launch half-year; the audience bucket is `unknown` for every
  repo until its source is cleared, and `unknown` is matched as its own level) and lie within
  0.5 SD of launch-signal magnitude and one quarter. Winners in rank order each take one: a loser
  whose pair passes the headline rule first, then the nearest (distance over launch signal, repo
  age at T, quarter and language). More rounds until `panel.losers` are matched hand out
  headline-passing losers first.
- **Too few winners** (R4.10, ADR-053.2): below 15 winners or matched losers the panel widens
  one declared widening step at a time; then, below `fallbacks.too_few_winners.min_winners`
  qualifiers, the brief's fallback steps apply in order. Every step is listed with its counts.
- **Reference cases** are always in the result (`reference`), whatever their role;
  **distribution exemplars** get `losers_per_exemplar` losers each, exactly matched on
  `distribution_exemplars.match_on` and never on field.
- **Balance**: SMD per covariate before and after matching (target |SMD| < `smd_target`; a miss
  is labelled `balance_limited`), the exact-match check, and the pairs that differ by more than
  `headline_exclusion_smd` SD on any covariate (a language mismatch, or a language missing on
  either side, counts as 1), which are excluded from headline patterns (marked `*` in
  `selection show`). After matching, each matched winner counts once in the SMD even when it
  has two losers (unweighted).
- **Sensitivity** (R4.9): the winner set recomputed under each alternative in
  `success.sensitivity` (primary swap, band shift, weights, and `fake_star_filter`, which now
  means *exclude anomaly-flagged candidates*), with the Jaccard overlap and the cases flagged
  `definition_sensitive` or `sensitive_to_star_anomaly`. An alternative that doesn't apply is
  listed with the reason (for example a minimum a fallback step dropped). It never changes the
  baseline.
- **Stored** in `brief_selection` (one row per selection: brief version and content hash, run,
  data version after the fetch with the as-of date folded in (`dv1-…@YYYY-MM-DD`), as-of date,
  selection, outcome-model and analysis-params versions, code commit, inputs and result hashes)
  and `brief_selection_case` (one row per repo). The same brief version and data version give
  the same result hash. A repo opt-out removes its rows.

**Resuming and exit codes.** A run is resumable after anything: a crash, a budget stop
(`paused_budget`, exit 4; exit 3 when approval is missing), the GitHub request budget (exit 4), a
failure (exit 1) or a batch still running (exit 5); a selection without its pre-registration
stops with exit 7 and changes nothing. Running the same command again resumes the
same run: completed stages are skipped, discovery skips the queries it did, and the relevance
filter rebuilds the same requests, so answered ones come from the LLM cache and in-flight batches
are collected by their stored ids. Only one run per brief at a time (exit 6 otherwise). A version
whose run is complete is not run again (except for the selection, which the same run picks up
once the shortlist is final); `--incremental` starts a refresh run that adds repos
created since the last discovery, judges only new candidates and keeps your decisions (a final
shortlist returns to review only when new candidates need a decision). Editing the brief creates
a new version with its own candidates and review, unless you carry the shortlist forward.

**Carrying a final shortlist forward to a new version (ADR-079).** An edit that changes only
the success definition (primary dimension, threshold, minimums, fallback steps), the `panel`
settings, the `report` options or the notes doesn't change discovery, the relevance filter or
your review, so you don't need to run and review again:
```bash
uv run pigtail brief shortlist carry-forward my-project --as owner --reason "only success changed"
uv run pigtail brief shortlist carry-forward my-project --from 4 --to 5 --as owner --reason "..." [--json]
```
`--to` defaults to the latest version and `--from` to the newest earlier version with a final
shortlist. It is refused unless the source shortlist is final, the target version has no
candidates, decisions or shortlist yet, and the two versions differ only in `success.*`,
`panel.*`, `report.*`, `notes` or store metadata (anything else, such as `field.*`, `window.*`,
`expansion` or `distribution_exemplars`, is listed and refused: run the brief instead). It
copies, in one step: every candidate with its verdict, reason, distance and provenance (marked
`carried_from_version`); each candidate's latest decision as a new logged decision of the target
version that keeps the original role, channel, reason, bulk marker and time and adds your role,
reason and time and the source version; and the named-project resolutions and confirmations.
It then finalizes the target shortlist with the same members and writes its mention scope. The
precision keeps the source's label (including `not item-reviewed` or `not reviewed`) followed by
"carried from vN". The refusal list is checked again: a repo refused since then is dropped and
counted (a named project confirmed as that repo goes back to unresolved). The target gets a run
row with status `carried_forward` that points at the source's run and keeps its window end; it
counts as complete, so after you **pre-register the new version** (its content hash changed),
`pigtail run --brief my-project` runs only the selection on it. A second carry-forward to the
same version is refused.

## LLM backend (`LLM_BACKEND`, PRD F15)
**Redaction on the LLM path (CB-06; ADR-066 follow-up, M21b, ADR-074).** Before any input leaves the
process, e-mails, phone numbers, profile URLs, DIDs and @mentions are removed; people become
per-call aliases (`@user1`, `@user2`, … in order of appearance within that one input), with no
key and not stable across calls. **One-time step after upgrading to M21b:** cached outputs
written before it may quote keyed `@p_…` tokens; delete them once (they are recomputed on
demand):
```bash
uv run pigtail llm cache clear --all --dry-run   # how many rows
uv run pigtail llm cache clear --all --yes       # delete them (the usage ledger is kept)
```

| Value | What it uses | When |
|---|---|---|
| `subscription` (default) | Your locally installed, official Claude Code CLI (`claude -p`) on **your own** Claude plan | Running pigtail for yourself |
| `api` | Anthropic API with `ANTHROPIC_API_KEY` | Shared or hosted deployments, high-volume runs |

Switching needs a restart and nothing else.

**Scope of the subscription backend (R15.6).** It is only for an operator running pigtail for themselves, on their own Claude plan, within Anthropic's terms for Claude Code. Any deployment that serves other users must use `api`. Read Anthropic's current terms before deploying: https://code.claude.com/docs/en/legal-and-compliance

Subscription setup:
1. Install Claude Code and log in with your plan (`claude`, then `/login`), or on a headless host run `claude setup-token` yourself and export `CLAUDE_CODE_OAUTH_TOKEN` in the host environment. pigtail never reads, stores or transmits this token.
2. Make sure `ANTHROPIC_API_KEY` is **not** set for pigtail's processes. pigtail also strips it from the CLI subprocess, because it would override the subscription.
3. Turn off model training in your Claude account's privacy settings.
4. Check with `uv run pigtail llm smoke` and `uv run pigtail llm status`.

API mode: use zero data retention or a data processing agreement with Anthropic where available (PRD §10).

**Clearing the result cache (CB-28).** Cached outputs live in `PIGTAIL_DATA_DIR/llm.sqlite3` and
expire after `LLM_CACHE_RETENTION_DAYS` (the daily retention purge deletes them). To delete them
sooner:
```bash
uv run pigtail llm cache clear --older-than 30      # outputs created more than 30 days ago
uv run pigtail llm cache clear --all --yes          # every cached output
uv run pigtail llm cache clear --all --dry-run      # count only
```
The usage ledger and pause state are kept. `privacy rekey` clears the whole cache itself.

## Services
`docker compose up -d --wait` starts Postgres and S3-compatible object storage (SeaweedFS). Point `S3_ENDPOINT` at a private bucket in production. Default hosting region: EU or Switzerland.

## Scheduled runs (M1-T21; batch runs since M11, ADR-048, ADR-049.1)
Runs happen on the operator's machine and must not depend on an agent session (WORK_ORDER §2).
The default is a **batch run**: `pigtail scheduler run --once` runs every job in
`infra/schedule.toml` (override with `PIGTAIL_SCHEDULE`) that is due, plus the start-of-run jobs,
then exits. Nothing runs between batch runs; M14 adds the launchd schedule that calls it. The
long-running loop (`pigtail scheduler run` without `--once`) is optional: the server path
(ADR-048.6), or while a launch is followed. Either way the scheduler:
- runs each job in a child process with a timeout;
- holds one Postgres advisory lock per job, so runs never overlap, even with two schedulers on one
  database;
- retries failures with exponential backoff (`retry_base` × 2ⁿ, capped at the interval);
- writes a `run` record `scheduler.<job>` for every attempt (the command also writes its own, e.g.
  `capture.hn_ranks`). Those records are the scheduler's only state, so restarts lose nothing. A
  run left `running` by a killed scheduler is closed as failed ("abandoned") and retried;
- logs the CB-03 encryption warning at start when the snapshot store is not known to be encrypted;
- in loop mode, serves `/healthz` and `/livez` (port `PIGTAIL_HEALTH_PORT`, default 8787, bound to
  `PIGTAIL_HEALTH_BIND`, default 127.0.0.1) and evaluates alert rules every 5 minutes.

| Job | Command | Every | Notes |
|---|---|---|---|
| `hn_ranks` | `capture hn-ranks --once` | start of each run; 5 min in launch mode | project-level, on by default (ADR-031.1); see below |
| `retention_purge` | `retention purge` | 1 d | R19.9 snapshot purge (report final + 12 months), CB-01, CB-04, CB-05, CB-18, CB-33 (UI audit rows); also drops raw GH Archive dumps past retention |
| `deletion_sync` | `privacy deletion-sync` | 1 d | CB-02; needs `OPTOUT_KEY` |
| `hn_mentions` | `capture mentions --repo … --since <opened −14 d>` per live case opened in the last 48 h **whose repo is on an in-review or final shortlist** (Directive §8.3) | 3 h | person-level: **skipped and logged** unless `PIGTAIL_ENABLE_HN=1` *and* `PIGTAIL_ADR022_PERSON_SOURCES_OK=1` (ADR-022) |
| `gh_star_history` | `capture github star-history --cases` | 1 d | per-repo star history of open cases; skipped without `GITHUB_TOKEN` |
| `gh_repo_events` | `capture github repo-events` | 15 min | person-level, **off** (`enabled = false`); see "GitHub token and budgets" |
| `backup_create`, `backup_prune` | `backup create`, `backup prune` | 1 d | off by default (see "Backups and restore") |

**HN front-page ranks (ADR-049.1).** There is no always-on poller. The `hn_ranks` job has
`run_at_start = true` (one snapshot of the current front page at the start of every scheduler
run) and `launch_mode_only = true` (between starts it polls every 5 minutes only while a brief or
tracked project is in launch mode). Launch mode is read from the `launch_mode_window` table (a
stub until M14 fills it) or forced with `PIGTAIL_LAUNCH_MODE=1` (`0` forces it off). Rank history
outside those windows is not collected and is reported as a coverage gap; historical front-page
presence comes from the Algolia `front_page` tag, labelled as such. For manual continuous
polling, run `uv run pigtail capture hn-ranks --loop` yourself. The job is never marked stale for
being idle between runs; its failures still alert.

A job whose connector is disabled (`PIGTAIL_CONNECTOR_<NAME>_ENABLED=false`) is skipped, not
failed: its run record has `counts.skipped = 1` and the reason in `config.skipped`. Set
`enabled = false` in the schedule to drop a job entirely. `pigtail scheduler plan` shows launch
mode and, per job, its next due time and what it would run now.

### With Docker Compose
```bash
cp .env.example .env            # set OPTOUT_KEY, S3_*, SNAPSHOT_BACKEND=s3 for production, SMTP_URL/ALERT_EMAIL
docker compose up -d --wait db objectstore && docker compose run --rm objectstore-init
docker compose build scheduler
docker compose run --rm scheduler pigtail scheduler run --once     # one batch run
docker compose run --rm scheduler pigtail health
```
The `scheduler` service is in the Compose profile `server`, so a plain `docker compose up` does
not start it (ADR-049.1: no always-on process by default). For the optional server path, start
the loop with `docker compose --profile server up -d scheduler` and check
`curl -s http://127.0.0.1:8787/healthz`. The service uses the repo's `Dockerfile`
(python:3.12-slim + uv, locked dependencies, non-root uid 10001, read-only root filesystem, all
capabilities dropped). Its `PIGTAIL_DATA_DIR` is the `app-data` volume (`/data`): local snapshots
(if `SNAPSHOT_BACKEND=local`), the LLM cache and `alerts/`. It reads `.env`, but `DATABASE_URL`
and `S3_ENDPOINT` point at the compose services (`db`, `objectstore`); set
`COMPOSE_DATABASE_URL` / `COMPOSE_S3_ENDPOINT` to use external ones. Build with
`--build-arg PIGTAIL_CODE_COMMIT=$(git rev-parse HEAD)` (or export `PIGTAIL_CODE_COMMIT` before
`docker compose build`) so run records carry the code commit. `docker compose stop` gives
running jobs 2 minutes to finish.

The `ui` service does **not** load `.env` (CB-29). It gets only the variables listed under its
`environment:` in `docker-compose.yml` (database, snapshot store, login, retention periods),
taken from the host environment or `.env` by Compose interpolation. So it never receives
`OPTOUT_KEY` / `PSEUDONYM_KEY`, `GITHUB_TOKEN`, `SMTP_URL` or API keys; the web app doesn't use them. If you add a
UI setting to `.env`, add it to that list too.

### With systemd (no Docker; optional server path)
`infra/systemd/pigtail-scheduler.service` runs the scheduler loop from a checkout in
`/opt/pigtail` (`uv sync --locked --no-dev`), as user `pigtail`, with its environment in
`/etc/pigtail/pigtail.env` (mode 0600, root-owned) and `PIGTAIL_DATA_DIR=/var/lib/pigtail`. It
runs `pigtail db migrate` before starting, restarts on failure and is sandboxed
(`ProtectSystem=strict`, `NoNewPrivileges`, no capabilities). Install steps are in the unit
file's header. Check it with `systemctl status pigtail-scheduler`, `journalctl -u
pigtail-scheduler` and `curl -s 127.0.0.1:8787/healthz`. Set journald retention to 12 months
or less (`MaxRetentionSec=1year`, CB-18).

### Health
```bash
uv run pigtail health            # per job: last success, lag, failures; DB, S3, disk, deletion SLA, doctor
uv run pigtail health --json     # same as /healthz's body
uv run pigtail health --history 7d
```
- A job is **stale** when it has had no success for 3× its interval; that and 3 consecutive
  failures mark it FAIL. `pigtail health` exits 1 when anything is FAIL.
- `/healthz` returns the same JSON. Its HTTP status is 503 only when the scheduler loop has stopped
  ticking or the database is unreachable, because restarting the container fixes neither job
  failures nor doctor warnings. Those show in the body (`"status": "fail"`) and raise alerts.
  `/livez` only checks the loop.

**Run history.** `pigtail health --history 7d` prints one line per UTC day: HN rank polls and
scheduler runs per job (succeeded/failed/skipped); `--json` gives the same data. The GH Archive
"complete scan day" streak went with the scan (M1's 7-day criterion is replaced by ADR-048.4:
3 consecutive scheduled runs succeed with alerts working). Staleness is still judged against
each job's interval, which suits the loop; making it fit weekly batch runs is part of M14.

### External liveness check (M1-T26)
A dead scheduler can't send its own alerts, and `/healthz` answers only while the process runs.
So the scheduler writes a heartbeat at every tick (every 15 s) to
`PIGTAIL_DATA_DIR/liveness.json`. To use another path, set `PIGTAIL_LIVENESS_FILE` or pass
`pigtail scheduler run --liveness-file PATH`. The heartbeat holds only times, a tick count and
the process id. A **separate** process, on its own schedule, checks it:
```bash
uv run pigtail health --liveness-file /var/lib/pigtail/liveness.json --max-age 5m --alert
```
- Exit 0 while the heartbeat is fresh. Exit 1 when it is older than `--max-age` (default 5m), or
  missing or unreadable.
- `--alert` raises a critical `scheduler_dead` alert through the normal sink (host files and
  e-mail when `SMTP_URL`/`ALERT_EMAIL` are set). The alert repeats at most hourly and resolves
  once the heartbeat is fresh again. Its state lives in `PIGTAIL_DATA_DIR/alerts/liveness/`, so
  it never touches the scheduler's own alert state.

**systemd (same host):** install `infra/systemd/pigtail-liveness.service` and
`pigtail-liveness.timer`, then run `sudo systemctl enable --now pigtail-liveness.timer`. It runs
every 5 minutes.

**cron (same host):**
```cron
*/5 * * * * cd /opt/pigtail && PIGTAIL_DATA_DIR=/var/lib/pigtail .venv/bin/pigtail health --liveness-file /var/lib/pigtail/liveness.json --alert >/dev/null
```

**Docker Compose:** the file is in the `app-data` volume. On the host, run
`docker compose exec -T scheduler cat /data/liveness.json | pigtail health --liveness-file - --alert`.
Note that this also fails (and alerts) when the container is down.

**From a second host** (recommended: it also catches a dead host, disk or network). Install
pigtail on the second host with `uv sync --locked --no-dev`. It needs no database, only
`PIGTAIL_DATA_DIR` and the SMTP variables. Give it key-only SSH access to a read-only account on
the scheduler host that can read the heartbeat file, then run it every 5 minutes (a timer, or
cron):
```bash
ssh -o BatchMode=yes -o ConnectTimeout=20 pigtail-ro@scheduler-host cat /var/lib/pigtail/liveness.json \
  | pigtail health --liveness-file - --max-age 5m --alert
```
`--liveness-file -` reads the heartbeat from stdin. If SSH fails, nothing arrives on stdin, so
the check fails and the alert fires. The commented `ExecStart` in
`pigtail-liveness.service` is the same thing as a systemd unit. Use a `--max-age` a few minutes
longer than the check interval, so that one slow SSH connection doesn't page you.

### Alerts
Every 5 minutes the scheduler evaluates these rules (thresholds under `[alerts]` in the schedule):

| Rule | Fires when |
|---|---|
| `job_stale` | no success for more than 3× the job's interval |
| `job_failing` | 3 or more consecutive failures |
| `db_down` | database unreachable, or the run log can't be read |
| `s3_down` | snapshot bucket unreachable (`SNAPSHOT_BACKEND=s3`) |
| `disk_high` | `PIGTAIL_DATA_DIR` volume more than 80% full |
| `doctor` | any `pigtail doctor` check at WARN or FAIL |
| `deletion_sla` | deletion-sync re-checks, or detected deletions, more than 7 days overdue (CB-02) |
| `login_failures` | 10 or more failed web-app logins (rate-limited attempts included) within 1 hour, counted from `ui_audit_log` (CB-30; `login_failures`, `login_window`) |
| `snapshot_integrity` | a snapshot failed its hash check when the web app served it within the last 24 h (CB-30; `integrity_window`). Critical: the stored bytes no longer match their hash |
| `scheduler_dead` | the heartbeat is stale; raised by the **external** liveness check, not by the scheduler (M1-T26) |

Alerts go to `PIGTAIL_DATA_DIR/alerts/` on the host: `ALERTS.md` (readable, append-only),
`alerts.jsonl` (structured) and `state.json` (dedupe). The files are mode 0600 and are **never
committed**. Messages hold job names, check names, counts and times only, and are passed through
the CB-18 scrubber anyway. An alert is written when it starts firing, again at most every 6 hours
while it keeps firing (`repeat`), and once when it resolves. If `SMTP_URL` and `ALERT_EMAIL` are
set, each batch is also e-mailed:
- `SMTP_URL`: `smtp://host[:25]` (STARTTLS if offered), `smtp+starttls://user:pass@host:587`, or
  `smtps://user:pass@host:465`. Login without TLS is refused except to localhost.
- `ALERT_EMAIL`: a comma-separated list of recipients.
- `ALERT_EMAIL_FROM`: optional sender (default: the first recipient).

E-mail is best effort: the file is always written first. On a host without the scheduler, run
`pigtail alerts check` from cron.

**Rotation and retention of the alert files (CB-31).** `ALERTS.md` and `alerts.jsonl` are moved
together to `ALERTS.<first event time>.md` / `alerts.<first event time>.jsonl` when either file
reaches `file_max_bytes` (default 1 MB) or its first event is older than `file_rotate_after`
(default 30 days). Rotated files are deleted once their first event is older than
`LOG_RETENTION_DAYS` (default and maximum 365 days, CB-18), so no alert line is kept longer
than that. The liveness alerts under `alerts/liveness/` follow the same rules. `alerts export`
reads the rotated files too.

**Copying alerts into the repo.** `ops/ALERTS.md` is public. An agent session copies only a
sanitized summary there:
```bash
uv run pigtail alerts export --to ops/ALERTS.md --since 7d
```
The export keeps rule, subject (job or check name; anything else becomes `redacted`),
severity, whether the alert is still firing, counts, and first and last times. It has no message
text. Review the diff before committing.

## Web app (D1 preview)
The Forensics Explorer preview (M1-T12): `/cases` and `/cases/:id` with the **Timeline** and
**Evidence** tabs on captured data. Everything is labelled **uncoded preview**: events are raw
captures; burst/launch labels, triggers and patterns arrive with a brief's deep forensics (M15). One process serves
the read-only API (R14.2) and the built UI.

**Private by default (R13.3, DPIA CB-19).** The app refuses to start without an operator password
hash, every `/api` route needs a session, and there is no public API explorer.

1. Create the password hash (the password itself is never stored):
   ```bash
   uv run pigtail ui hash-password          # prompts twice; prints an argon2id hash
   export PIGTAIL_OPERATOR_PASSWORD_HASH='$argon2id$v=19$…'   # single quotes: the hash contains `$`
   ```
   In `.env`, also single-quote it so Compose doesn't interpolate the `$`.
2. Build the UI once (Node 22 and pnpm; `corepack enable` provides pnpm), then serve:
   ```bash
   pnpm --dir ui install --frozen-lockfile && pnpm --dir ui build
   uv run pigtail ui serve --host 127.0.0.1 --port 8080   # runs migrations, then serves
   ```
   Or with Docker: `docker compose up -d --build ui` (image `infra/ui/Dockerfile`; published on
   `127.0.0.1:8080` only; reads local snapshots from the `app-data` volume, read-only).
3. Open http://127.0.0.1:8080 and log in.

**Briefs in the container.** The `ui` service bind-mounts the host briefs directory
`${PIGTAIL_BRIEFS_DIR:-~/.pigtail/briefs}` read-write at `/briefs` and sets
`PIGTAIL_BRIEFS_DIR=/briefs` inside, so the UI reads and saves the same briefs as the CLI on the
host (keep that directory outside any git work tree, ADR-071.3). **UID and permissions:** the
container runs as UID/GID `10001` and the store keeps directories at `0700` and files at `0600`
(it also `chmod`s the store root). On **Linux**, the host directory must therefore belong to
UID 10001, or the UI cannot read or save briefs (and the `chmod` fails): create it first
(`mkdir -p ~/.pigtail/briefs && sudo chown -R 10001:10001 ~/.pigtail/briefs`); if Docker
creates a missing mount source itself it is owned by root and unusable. Changing the owner means
the host CLI then needs `sudo` or a shared group to write briefs; the alternative is to run the
CLI inside the container too. On **macOS** (Docker Desktop) file sharing maps ownership to your
user, so no `chown` is needed.

Settings (environment): `PIGTAIL_UI_SESSION_HOURS` (absolute session lifetime, default 12),
`PIGTAIL_UI_IDLE_MINUTES` (default 120), `PIGTAIL_UI_SECURE_COOKIE` (`auto` by default: the
cookie is `Secure` unless the app is reached on a loopback host; set `1` behind a TLS proxy),
`PIGTAIL_UI_DIST` (built UI directory). Database and snapshot settings are the capture ones
(`DATABASE_URL`, `SNAPSHOT_BACKEND`, `S3_*`, `PIGTAIL_DATA_DIR`).

**Remote access.** Keep the bind on loopback. To reach it from elsewhere, use an SSH tunnel
(`ssh -L 8080:127.0.0.1:8080 host`) or a TLS reverse proxy; with a proxy, start with
`--proxy-headers --forwarded-allow-ips <proxy ip>` so login rate limiting sees real clients, and
set `PIGTAIL_UI_SECURE_COOKIE=1`. Never expose it on a public address without TLS.

**What is logged (CB-19).** `ui_audit_log` records login success, failure and rate-limit events,
logouts, and every snapshot view (time, route, HTTP status, evidence id, content hash, a 16-char
session-hash prefix). It stores **no IP address**: the client is a keyed hash of the truncated
address (IPv4 /24, IPv6 /48), used only to rate-limit logins (5 failures per client network and
30 overall per 15 minutes) and to spot brute force. The key derives from the password hash, so it
rotates with the password. Rows older than `LOG_RETENTION_DAYS` (max 365) are deleted by the daily
`retention purge` (CB-33; expired sessions too) and also at each login. uvicorn access logs, which would contain IPs, are off unless `--access-log` is given.
Read the log with SQL, e.g. `SELECT at, event, evidence_id FROM ui_audit_log ORDER BY at DESC`.

**What the pages show.**
- `/cases`: filters (status, opened date range), sort by recency or by the 48 h stars recorded
  when the case was opened (older cases, opened by the detection removed in M11), and a
  "Launch mode" strip listing the tracked projects and briefs whose launch-mode window is active
  now (`launch_mode_window`, read through `GET /api/launch-mode`; each links to the project's
  open case, if any). Windows are declared or detected from M14; until then the strip is empty.
- `/cases/:id`: the detection metrics recorded for older cases (shown as recorded: the hourly
  data behind them was dropped in M11), the **coverage caveat** (stars come from the
  star-history endpoint, net of un-stars, endpoint day labels; days never fetched are unknown,
  not zero), and two tabs:
  - **Timeline**: time-aligned lanes for GitHub stars per day (star history), HN front-page rank
    (best rank per bucket; shaded band = ranks 1–30) with mention markers, and captured evidence.
    Zoom with the range buttons or by dragging across the chart. Stars stay daily whatever the
    bucket. Every point opens its evidence (the star-history page). A table view is available.
  - **Evidence**: every evidence record behind the case (case- and repo-linked items, HN stories,
    mentions and rank polls), sortable by capture time, source, reliability, retention class and
    state, with a one-click **Open snapshot**.
- `/evidence/:id`: the record, its retention rule and due date, and what references it.

**Snapshots.** "Open snapshot" streams the raw bytes after re-checking their SHA-256; a mismatch
is refused (500) and audited. Snapshots open in a new tab under a sandboxing CSP (no scripts, no
external requests); gzip dumps download. Raw snapshots can contain handles and text: they are the
private evidence itself, so treat the screen and any downloads accordingly. When the bytes are
gone the API answers **410** with the reason: `raw_dropped` (retention; hash, URL and fetch time
are kept, replay can re-fetch) or `deleted_upstream` (deletion sync, CB-02). JSON responses never
contain handles: HN authors are not returned, and titles and URLs pass the identifier scrubber
(`@handle` → `@[handle]`, `github.com/<login>` → `[profile:github]`); titles of stories deleted
upstream are hidden.

## JSONL export (M1-T20, PRD §7)
```bash
uv run pigtail export jsonl --out /srv/pigtail-export                  # project-level tables
uv run pigtail export jsonl --out /srv/pigtail-export --tables repos cases evidence
uv run pigtail export jsonl --out /srv/private/export --include-person-level
```
- The export writes one `<table>.jsonl` per table plus `manifest.json` (format, database
  migration, code commit, row count and SHA-256 per file). Each line is one record with sorted
  keys and a `schema_version`. Rows are ordered by primary key, times are UTC, and one
  read-only snapshot transaction is used. The same database state therefore gives identical
  files, which diff and merge cleanly.
- **Never inside the pigtail repository** (it is public). Such a path is refused with or without
  flags.
- **Person-level tables** (`hn_mention` — coded roles, no handles, but tied to individual posts —
  `upstream_items`, `evidence_upstream_items`, the refusal list with its opt-out fingerprints)
  are exported only with `--include-person-level`, and only to a directory outside **any** git
  working tree. UI sessions, the UI audit log and the key fingerprint are never exported.
  Files are mode 0600 in a 0700 directory. Treat a person-level export like the database:
  private, encrypted storage, covered by the retention policy. Delete it when you're done.
- A table the export doesn't know stops it. Every new migration must classify its tables in
  `pigtail.export.jsonl.TABLE_LEVELS`.

## Privacy operations
These commands implement the code side of the retention policy and the DPIA controls
(`docs/compliance/retention-policy.md`, `docs/compliance/dpia.md` §9). They need
`DATABASE_URL`; everything that matches or adds opt-outs also needs the opt-out key
(`OPTOUT_KEY`; the earlier name `PSEUDONYM_KEY` is still read — set only one of them).
Every command runs migrations first, writes a `run` record, and logs each deletion as a tombstone
in the append-only `deletion_log` table (hashes and ids only, never content).

### What is stored about people: roles and buckets (Directive §8.1, ADR-066.1, ADR-071)
Since M21a (migrations 0017–0019) pigtail **never stores a handle, a personal name or a
pseudonym** of an individual in coded data. Each actor is coded at ingest, in memory, and the
handle is then discarded:

| Stored | Values |
|---|---|
| role | `maintainer` (the account owns the repo the record is about; rule `roles-v1`), `account`, `newsletter`, `community`, `organization`, `automated_account` |
| follower bucket | `r0` unknown, `r1` < 1,000, `r2` 1,000–9,999, `r3` 10,000–99,999, `r4` ≥ 100,000 (codebook §4.5); computed from a follower count the source returns with the post, then the count is dropped. Follower **lists** are never collected. |
| automated account | `true`/`false` plus the bot-rule version (`bot-filter-v0`); bot filtering runs in memory |
| per-repo star/fork events | hourly and daily **counts** only (`repo_event_hourly_agg`, `repo_event_daily_agg`); accounts are de-duplicated within one poll in memory, and `repo_event_actor` no longer exists |

The **only person-derived value kept** is the **opt-out fingerprint** of someone who opted out or
asked for erasure (HMAC-SHA256 of the handle under `OPTOUT_KEY`, kept apart from the data;
ADR-071.1). It is used only to exclude that person at ingest and to purge them. The raw
snapshots (encrypted, private) still contain whatever the source returned until the retention
purge deletes them (R19.9, below); they are the only place a person can be found, and only by
an in-memory scan (access, erasure, opt-out).

Migration 0017 moved existing rows to this model and purged the pseudonyms: HN mention authors
became role `account` / bucket `r0` with `automated_account` unknown and rule versions
`migrated-0017`; upstream-item authors were cleared; per-actor event rows became hourly counts
and the table was dropped. Each purge wrote a count-only `deletion_log` row with reason
`directive_001_handle_purge`. **The LLM cache** (SQLite, outside Postgres) can still hold keyed
`@p_…` tokens in outputs cached before the LLM path switched to per-call, keyless aliases (M21b,
ADR-074; nothing keyed reaches the model or the cache any more). Run
`pigtail llm cache clear --all --yes` once after upgrading (see "LLM backend"); otherwise they
expire with `LLM_CACHE_RETENTION_DAYS`.

### Startup check: `pigtail doctor` (CB-03)
```bash
uv run pigtail doctor            # human-readable; exit 1 on any FAIL
uv run pigtail doctor --strict   # also exit 1 on WARN (use in deploy scripts)
uv run pigtail doctor --json
```
It checks the opt-out key and its fingerprint (below), the database and pending migrations, the
snapshot bucket's default
encryption (`GetBucketEncryption`), TLS to a remote object store, and prints the retention
settings. `scheduler run` logs the same encryption warning when it starts. Two items show as
`MANUAL` because no client can see them: Postgres volume encryption and, with
`SNAPSHOT_BACKEND=local`, the snapshot directory's disk encryption.

It also reports which sources are switched on (M1-T23):
- `adr022_person_sources`: `OK` while `PIGTAIL_ADR022_PERSON_SOURCES_OK` is unset (person-level
  sources held); `WARN` when it is `1`, as a reminder that only you can confirm every ADR-022
  precondition (for example the published notice, CB-12);
- `hn_sources`: the rank poller and the person-level HN connectors (`hn_firebase`,
  `hn_algolia`). `WARN` if the rank poller is off (rank history can't be backfilled); `FAIL` if a
  person-level connector is on without the ADR-022 flag (it would refuse to start);
- `github_events`: per-repo GitHub events. `FAIL` if on without the ADR-022 flag, `WARN` if on
  without `GITHUB_TOKEN`; otherwise it shows the retention (`GITHUB_EVENTS_RETENTION_DAYS`).

Flag values and tokens are never printed.

Backups (CB-17b):
- `backup_recipient`: `WARN` while `BACKUP_RECIPIENT` is unset (`backup create` would refuse);
- `backup_age`: the newest `pigtail-backup-*` file in `BACKUP_DIR`. `OK` up to 2 days old,
  `WARN` after 2 days, `FAIL` after 7 days or when there is no backup (or the directory is
  missing); `WARN` while `BACKUP_DIR` is unset. Under Docker Compose the backup directory usually
  lives on the host only: run `pigtail doctor` on the host with `BACKUP_DIR` set, or accept the
  `WARN` in the container.

### Opt-out key check (CB-25, ADR-043, ADR-071.1)
Opt-out entries (person fingerprints and repo-name keys) are keyed hashes of `OPTOUT_KEY` (alias
`PSEUDONYM_KEY`). With a different key they silently stop matching: people and repos that opted
out would be collected again. So the database stores a **fingerprint** of the key (`kfp1_` + 32
hex of HMAC-SHA256(key, `pigtail-key-fingerprint-v1`); it does not reveal the key, and the key
itself is never stored or printed) in table `pseudonym_key_fingerprint` (migration 0012).
- **Recorded on first use**: the first command that loads the opt-out list with a key (a
  capture, a `privacy` command, or `scheduler run` at startup) stores it.
- **Checked on every use**: every capture command (the opt-out list is loaded first and the
  connector base refuses an opt-out key other than that one), every `privacy` command that uses the
  key, `backup restore` (before anything is replaced) and `scheduler run` (it doesn't start).
  On a mismatch they **refuse** with exit code 2 and a message naming CB-25; scheduled jobs fail
  and alert.
- `pigtail doctor` reports `pseudonym_key_fingerprint`: `OK` (matches), `FAIL` (mismatch),
  `WARN` (nothing recorded yet). Doctor never records anything.

```bash
uv run pigtail privacy key-fingerprint     # status, stored + running fingerprint, history (exit 1 on mismatch)
```
If the check fails and you did **not** rotate on purpose, restore the original key from its
separate backup; don't reset. To rotate on purpose, use `privacy rekey` (below). Reset only in
the runbook's **manual fallback** (`docs/compliance/runbooks/key-rotation.md` §4.2, chiefly when
the old key is lost), with the new key in the environment:
```bash
uv run pigtail privacy key-fingerprint --reset --confirm-rotation
```
This records the running key's fingerprint as the database's key. It writes a `runs` record
(`privacy.key_fingerprint_reset`) and a `reset` event with the old and new fingerprints to the
append-only `pseudonym_key_fingerprint_log`. `--reset` alone is refused. Resetting does not
re-key existing opt-outs: use `privacy rekey` (below) instead, which re-derives them and records
the new fingerprint in the same transaction. A backup restore keeps the live database's
fingerprint (its opt-outs are keyed with the live key) and **refuses a backup taken before the
reset** (CB-35); the command reminds you to take a fresh backup.

### Rotating the key: `privacy rekey` (CB-26)
`privacy rekey` moves the database from the old opt-out key to a new one in **one
transaction**: every opt-out entry is re-derived under the new key, the LLM cache is cleared, and the key fingerprint switches to the new key last. If anything can't be
done, nothing changes. **Follow the rotation runbook, §4.1**
([`docs/compliance/runbooks/key-rotation.md`](../compliance/runbooks/key-rotation.md)), for the
full procedure: decision record, sealed old key, handles file, stopping every service, backups
before and after, and when to destroy the old key. Its §4.3 gives the schedule (every 24 months,
and after a compromise or a departure).

An opt-out fingerprint is a one-way hash of a handle, and pigtail stores no handles, so
re-deriving needs the handle again. `rekey` gets it, only in memory, from:
1. **your handles file** (`--handles-file`): the handles and repo names from the original opt-out
   and erasure requests, one per line: `github <handle>`, `hn <handle>`, `bluesky <handle>`,
   `v2ex <handle>` or `repo <owner/name>` (`#` starts a comment). It must be outside any git
   working tree and `chmod 600`. It only maps existing entries; nothing in it is added to the
   opt-out list. Delete it afterwards.
2. **repo names pigtail still holds** (HN mentions, story links, `repos`), for repo-name
   opt-outs. These names are usually purged with the opt-out, so
   the handles file is the main source.
3. **retained raw snapshots**: each connector re-parses them in memory and pairs each old
   fingerprint with the new one. `--no-snapshot-scan` skips this (it can be slow with many GH
   Archive dumps).

Rules:
- **Every opt-out must be mapped.** An opt-out (a person, or a repo by name) that can't be mapped
  would stop matching, and collection would resume for someone who objected. So `rekey`
  refuses, and no flag overrides this. The refusal lists each unmapped entry's kind, platform,
  request id and date (never the fingerprint) so you can find the original request and add its
  handle to the file. Opt-outs by repo id and legacy unkeyed name entries don't use the key and
  stay as they are.
- `--drop-unmapped` and `--purge-person-level` concern registered person tables
  (`PERSON_TABLES`), which are **empty since migration 0017**: they have nothing to do today.
- It refuses while other sessions are connected to the database. Stop the `scheduler` and `ui`
  services and any cron jobs first.

```bash
export OLD_OPTOUT_KEY=...   # the old key, only for this shell (e.g. `read -s`); never an argument
export OPTOUT_KEY=...       # the new key (unset PSEUDONYM_KEY if you still had it)
uv run pigtail privacy rekey --old-key-env OLD_OPTOUT_KEY \
  --handles-file /secure/rotation/handles.txt --dry-run            # report, then roll back
uv run pigtail privacy rekey --old-key-env OLD_OPTOUT_KEY \
  --handles-file /secure/rotation/handles.txt --confirm-rotation   # the rotation itself
unset OLD_OPTOUT_KEY
```
`--old-key-env` takes the **name** of the variable, never the key. The output is JSON counts only
(mapped and unmapped opt-outs, rows mapped or deleted per table, snapshots scanned, cache rows
deleted) with a `privacy.rekey` run record. Neither the output nor the run record contains a key,
a handle, a repo name or the file's path. Exit 2 means it refused and nothing changed.

Afterwards: `pigtail privacy key-fingerprint` shows the new key with a `rekey` event. Backups taken
before the rotation hold old-key opt-outs: `backup restore` refuses them, and they expire with
`backup prune` after 35 days. **Take a fresh backup right away.** Keep the old key sealed until
the old backups are pruned, then destroy it. Delete JSONL exports made with
`--include-person-level` before the rotation. Run `rekey` with the same `PIGTAIL_DATA_DIR` as the
scheduler (under Compose, `docker compose run --rm -e OLD_OPTOUT_KEY scheduler pigtail privacy
rekey …`), or it clears a different LLM cache; runbook §4.1 has the Compose details.

No dual-key window (CB-27) is needed: the switch is atomic, and every command started afterwards
checks the new fingerprint.

### Encryption at rest (CB-03)
Required before production capture (ADR-022).
- **Snapshot bucket.**
  - *Bundled SeaweedFS:* set `S3_SSE_KEK` to 64 hex characters (`openssl rand -hex 32`) in the
    host environment, then run `docker compose up -d objectstore && docker compose run --rm
    objectstore-init`. The init job sets the bucket's default encryption (SSE-S3, AES256). Objects
    are then stored encrypted on the volume (verified with SeaweedFS 4.47). Objects written
    **before** SSE was turned on stay unencrypted: re-capture them or copy them in place. The KEK is
    the only way to read the data. Back it up apart from the data backups and keep it out of git,
    Postgres and the volume. SeaweedFS's `-filer.encryptVolumeData` flag does **not** cover
    S3 uploads, so don't rely on it.
  - *Hosted S3 (production):* use a private bucket with default encryption (SSE-S3 or SSE-KMS)
    and TLS (`https://` endpoint). If your provider doesn't implement `GetBucketEncryption`,
    `doctor` reports `MANUAL`. In that case, confirm encryption in the provider console.
- **Postgres.** Put the `db-data` volume on an encrypted disk or volume: LUKS/dm-crypt on Linux,
  the cloud provider's volume encryption, or FileVault for a local Mac. Postgres has no built-in
  transparent data encryption, and `doctor` cannot check the volume. Record how it is encrypted in
  your deployment notes.
- **Local snapshots and the LLM cache** (`PIGTAIL_DATA_DIR`) also hold person-level data. Keep
  that directory on an encrypted disk.
- **Backups** must be encrypted too (CB-17, planned).

### Retention purge (R19.9, CB-01, CB-04, CB-05, CB-18)
```bash
uv run pigtail retention purge --dry-run   # report only; still writes a run record
uv run pigtail retention purge             # daily: the scheduler's `retention_purge` job
uv run pigtail retention report-final --brief ID --version N [--at 2026-10-01T12]
```
**Snapshot retention (R19.9, Directive §8.2, ADR-066.2).** Raw person-level snapshots are kept
until the report of the brief that used them is **final plus 12 months**, then deleted; the
coded facts and the content hash stay (the evidence row moves to `raw_dropped`), and the purge is
logged (`deletion_log`, reason `retention`, plus the `retention.purge` run record with the counts
`snapshots_dropped_report_final`, `snapshots_dropped_ceiling`, `snapshots_held_pending_report`).
- The brief pipeline records which evidence each brief run used (`brief_evidence`) and when a
  brief version's report became final (`brief_report_final`; by hand:
  `pigtail retention report-final`, which only stores the id, version and time). Re-finalizing a
  revised report moves the date.
- `SNAPSHOT_AFTER_REPORT_DAYS` (default and maximum 365) is the "+ 12 months".
- `PERSON_LEVEL_RETENTION_DAYS` (default and maximum 730, from the newest fetch of the blob) is now
  the **ceiling** for snapshots no final report anchors: those no brief used, and those whose
  brief's report is still pending (so an abandoned brief can't hold snapshots forever). When a
  snapshot is used by several briefs, it is kept until every one of their reports is final plus
  12 months, or the ceiling, whichever is later.
- The evidence view in the web app shows each snapshot's due date and which rule applies.

What the purge does, in order:
1. Drops raw GH Archive dumps after `GHARCHIVE_RAW_RETENTION_DAYS` (default and maximum 30,
   CB-32; `capture purge-raw --retention-days` has the same ceiling).
2. Drops the raw bytes of every person-level snapshot that is due (above). The evidence row keeps
   its hash, URL, source, fetch time and terms basis, and moves to `deletion_state = raw_dropped`.
   A blob a `project_level` record still needs is kept and reported as `blocked_shared`.
   Per-repo event pages (`person_level_30d`) older than `GITHUB_EVENTS_RETENTION_DAYS` are dropped
   too (normally they are dropped right after parsing).
3. Deletes rows of registered person-level tables (`PERSON_TABLES`) past the cutoff. The registry
   is **empty since migration 0017** (no table holds handles or pseudonyms).
4. Deletes LLM cache rows linked to the evidence dropped in step 2, and every cache row older than
   `LLM_CACHE_RETENTION_DAYS` (default and maximum 730). The LLM usage ledger follows the same
   period. Expired cache rows are never served, even before a purge runs.
5. Clears `runs.error` text older than `LOG_RETENTION_DAYS` (default and maximum 365).
6. Deletes UI audit rows (`ui_audit_log`) older than `LOG_RETENTION_DAYS`, and expired UI
   sessions (CB-33). The scheduler's daily `retention_purge` job runs it, so the limit holds
   even when nobody logs in.

Project-level and aggregate data are never touched. Longer periods than the policy allows are
rejected at startup. Container and system logs need their own 12-month rotation, for example
journald `MaxRetentionSec=1year` or logrotate.

### Deletion sync (CB-02, R1.5)
```bash
uv run pigtail privacy deletion-sync --dry-run          # report only; still writes a run record
uv run pigtail privacy deletion-sync --source hn        # run daily (cron or systemd timer)
```
Capture jobs register every upstream item whose content sits in a person-level snapshot
(`upstream_items`). The sync re-checks items that are due and, for each item that is gone upstream
(HN: `deleted`, `dead`, or `null` from the Firebase API):
1. drops the raw bytes of every snapshot that holds it (a search page holds many items, so the
   whole page goes) and moves all evidence with those hashes to `deletion_state =
   deleted_upstream` (hash, URL, fetch time and terms basis stay);
2. deletes its coded rows (`hn_mention`) and the LLM cache rows derived from the evidence, and clears the title and url of front-page stories stored by the rank poller
   (`hn_story`; M1-T23). The rank history and the story's repo link stay, so front-page minutes
   remain computable; a later poll never refills a cleared title;
3. writes tombstones (reason `deleted_upstream`) to `deletion_log`.

It also re-applies deletions to evidence captured after an item was found gone (a stale search
index, or a backup restore). Schedule per source (retention-policy.md §4): HN items linked to an
open case are re-checked daily, others monthly; action within 7 days of detection. The report
lists `overdue_before_run` (re-checks more than 7 days late) and `detected_not_acted`. Checks store
nothing and run even when HN collection is switched off. Items are tracked by id only (no author
since migration 0017). Bluesky (≤ 48 h, push/tombstone based) will plug into the same job before
it may be enabled; its account-level deletion signals will have to be resolved to items by the
source itself, since pigtail no longer stores who wrote what.

### Hacker News sources (M1-T4, M1-T14)
- **Show HN discovery** (`hn_showhn`, M22, enabled by default;
  `PIGTAIL_CONNECTOR_HN_SHOWHN_ENABLED=false` turns it off). Used only by a brief's discovery
  stage: Algolia `show_hn` stories matching the brief's keywords, asked for title, URL, points and
  time only. Project-level: the poster, story text and comments are never read or stored, and
  each raw page is dropped right after parsing, so it does not need the ADR-022 flag.
- **Rank poller** (`hn_ranks`, enabled by default; `PIGTAIL_CONNECTOR_HN_RANKS_ENABLED=false`
  turns it off). Project-level only: story ids, ranks, urls, titles, scores, comment counts. It
  keeps no usernames (the item's `by` is dropped, and item raw JSON is deleted right after
  parsing), so it does not need the ADR-022 flag. Rank history can't be backfilled. Scheduled
  runs take one snapshot at the start of each run and poll continuously only in launch mode
  (ADR-049.1, "Scheduled runs" above); gaps outside those windows are reported, not filled:
  ```bash
  uv run pigtail capture hn-ranks --once                          # one poll
  uv run pigtail capture hn-ranks --loop --interval-minutes 5     # manual continuous polling
  ```
  The interval can't be under 1 minute (TM-04). Ranks 1–30 are the front page. Every stored
  story is registered for deletion sync (no author is stored), so stories deleted upstream lose
  their title and url (M1-T23).
- **Front-page minutes** (M1-T22, `att.hn_frontpage_minutes`), read-only (no writes, no run
  record; the database session is read-only):
  ```bash
  uv run pigtail report hn-frontpage --repo owner/name [--since 2026-09-20T00] [--until …]
  ```
  Stories are matched by their URL (`github.com/owner/name`). Each poll's ranks count until the
  next poll; a gap between polls longer than 2 × the interval (`--interval-minutes`, default 5;
  `--gap-factor`, default 2) is not counted and is listed under `gaps` / `uncovered_minutes` (per
  story: the gaps that began while it was on the front page). Time before the first poll is
  `before_polling_minutes`. `quality` is `verified` when the window is fully covered, `estimated`
  (a lower bound) when not, and `unknown` (`minutes: null`) when nothing in the window was
  polled. Opted-out repos are refused.
- **Mention capture** (`hn_algolia`, `hn_firebase`): person-level (usernames are coded as a role
  and bucket and discarded at ingest; comment text stays in private snapshots). **Shortlisted
  projects only** (Directive §8.3, ADR-066.3): a repo must be on a brief version's shortlist with
  status `in_review` or `final`, or the command refuses before any request (`NotShortlisted`,
  exit 2) and the scheduler doesn't plan it. The brief pipeline sets the shortlist; by hand:
  ```bash
  uv run pigtail capture shortlist set --brief ID --version N --status in_review --repo owner/name
  uv run pigtail capture shortlist set --brief ID --version N --status removed --repo owner/name
  uv run pigtail capture shortlist list
  ```
  There are no searches for people or accounts and no follower lists.
  **Disabled by default** (`PIGTAIL_ENABLE_HN=0`).
  Setting `PIGTAIL_ENABLE_HN=1` fails with an error unless `PIGTAIL_ADR022_PERSON_SOURCES_OK=1`
  is also set. **Set that flag only after every ADR-022 precondition for person-level sources is
  in place on your deployment:** CB-01 (retention purge scheduled), CB-02 (deletion sync
  scheduled), CB-03 (encryption at rest; FileVault on the reference setup), CB-06 including the
  CB-06b completion (LLM-path redaction), CB-08 (request handling), CB-12 (your privacy notice
  published), CB-13 (opt-outs), and CB-22, 23, 25 and 29, per `ops/DECISIONS.md` ADR-022 as
  amended by ADR-073.2. Use by commercial operators is also pending legal question LQ-6.
  ```bash
  uv run pigtail capture mentions --repo owner/name [--since 2026-09-01] [--loose] [--no-items]
  ```
  Evidence attaches to the repo's newest open case if it has one. `--loose` also keeps hits that
  only contain the repo name (noisy for common words).

### GitHub token and budgets (M1-T24, ADR-032; per-repo only since M11)
pigtail uses the GitHub API per repo: the star-history endpoint for the repos of open cases (and,
from M13, of a brief's candidates), repository search for the queries a brief defines (M13), and,
optionally, per-repo events for open cases. The watch list, the all-GitHub search sweeps and
detection v1 were removed in M11 (ADR-047.6).

**The token.** Create one fine-grained personal access token on your own GitHub account with
access to *public repositories only* and no extra permissions, and put it in the host's `.env`
as `GITHUB_TOKEN=…` (never in git). Use one token only: GitHub's terms forbid sharing or pooling
tokens to exceed rate limits (TM-02), so don't add a second token or a GitHub App to raise them
(ADR-032.4). The token is sent only as an `Authorization` header and is never logged.
**Without `GITHUB_TOKEN` pigtail makes no GitHub API call**: the `capture github` commands exit 2,
and the scheduler skips the `gh_*` jobs with the logged reason `missing_env:GITHUB_TOKEN` (not a
failure, no alert).

**Budgets.** One token has three separate buckets. pigtail caps each at 70 % per UTC hour by
default (validation plan M7) and stops hard (no request sent) when a cap is reached; the job
records `budget_stop` and resumes on its next run.

| Bucket | GitHub limit | Default cap | Override (per hour) | Used by |
|---|---|---|---|---|
| core | 5,000 requests/h | 3,500 | `GITHUB_BUDGET_CORE_PER_HOUR` | star history, per-repo events |
| graphql | 5,000 points/h | 3,500 | `GITHUB_BUDGET_GRAPHQL_PER_HOUR` | none since M11 |
| search | 30 requests/min | 1,260 (21/min) | `GITHUB_BUDGET_SEARCH_PER_HOUR` | brief-scoped search (M13) |

- The hourly spend is shared by all pigtail processes through the `github_budget_ledger` table.
- `GITHUB_BUDGET_RESERVE_FRACTION` (default 0.30): stop when GitHub reports less than this share
  of a bucket left and the reset is more than 2 minutes away. This also leaves room for anything
  else you run with the same account.
- Each job run has its own cap too (`--max-requests`; defaults: star history 400, repo events
  1,600 core requests).
- Rate-limit answers are honoured: `Retry-After`, `X-RateLimit-Reset`, at least 60 s (doubling)
  for secondary limits; requests are serial; ETag `304` answers cost nothing; per-repo events are
  never polled faster than GitHub's `X-Poll-Interval`.
- Check actual use with `uv run pigtail capture github budget --hours 24`. A brief's cost
  estimate before each run arrives in M12.

**Jobs** (`infra/schedule.toml`; all skip until `GITHUB_TOKEN` is set):

| Job | Every | Command |
|---|---|---|
| `gh_star_history` | 1 d | `capture github star-history --cases` (open cases whose series was never fetched or is older than 20 h) |
| `gh_repo_events` | 15 min | `capture github repo-events` (**off**; see below) |

By hand: `uv run pigtail capture github star-history --repo owner/name [--full]` for a repo
already in the `repos` table (`--full` pages back to the creation week).

**Per-repo events (person-level).** `repo-events` reads `WatchEvent`/`ForkEvent` actors for
repos with an open case, to confirm the bot filter. It is off by default. To turn it on, meet
every ADR-022 precondition (see "Hacker News sources" above; ADR-036), then set
`PIGTAIL_ENABLE_GITHUB_EVENTS=1` and `PIGTAIL_ADR022_PERSON_SOURCES_OK=1` and change
`enabled = true` on `gh_repo_events`. Actors are used in memory only: the bot rule flags
automated accounts and each account counts once per poll; then the login is discarded (Directive
§8.1, ADR-071.2). Only hourly and daily counts with the bot-rule version are stored; an event is
counted once across polls thanks to an event-id watermark. Raw event pages are deleted right
after parsing (a page that fails to parse is deleted at once too, CB-23b, and counted as
`repo_events.parse_failed` in the run record); leftovers are dropped after
`GITHUB_EVENTS_RETENTION_DAYS` (default 16, maximum 30; ADR-038). pigtail never builds, stores or
exports a list of a repo's stargazers. Known limitation: an account that stars, unstars and stars
again across two polls counts twice.

**Search pages (CB-24).** `pigtail.capture.github_search.search_repos` pages one given query
(M13 builds the queries from a brief). Search result pages embed owner objects, so they are
snapshotted as person-level and their raw bytes are deleted right after parsing (hash and URL kept, tombstone in
`deletion_log`). Only repo ids, names, counts, dates and the owner *type* (User/Organization) are
kept.

**Day boundaries.** Star-history days are GitHub's own day labels, which are not UTC days
(probably US Pacific; to be confirmed around the DST change on 2026-11-01). Stored rows carry a
`day_boundary_tz` note.

**First runs once the token exists:**
```bash
uv run pigtail capture github budget --hours 1                              # ledger
uv run pigtail capture github star-history --cases --max-requests 50
```

### Opt-outs (CB-13)
```bash
uv run pigtail privacy optout add --platform github --handle -       # reads the handle from stdin
uv run pigtail privacy optout add --platform github --repo-id 123456 # a project owner opts out
uv run pigtail privacy optout add --platform github --repo owner/name   # also if not in the DB
uv run pigtail privacy optout list
uv run pigtail privacy optout remove --platform github --handle -
uv run pigtail privacy optout purge     # re-apply the whole list (backup restore does this)
uv run pigtail privacy optout rekey     # CB-13b: convert pre-0009 unkeyed name entries
```
- **Handles are never stored.** A handle is hashed at once with `OPTOUT_KEY` in the platform's
  namespace into the person's **opt-out fingerprint** (kind `person`; ADR-071.1), the value the
  connectors compute in memory at ingest. The database rejects anything that isn't a
  fingerprint. `--handle X` also works, but `--handle -` (or leaving the flag
  out) reads the handle from stdin and keeps it out of your shell history.
- **Ingest:** every capture loads the list, and connectors drop matching records before
  aggregation (counted as `<source>.suppressed` in the run record). Opted-out repos are dropped
  by repo id and, for HN data, also by normalized `owner/name` (M1-T23), so an opt-out reaches repos pigtail doesn't track yet. The name is
  stored only as a **keyed** hash (`rk_…`: HMAC-SHA256 with `OPTOUT_KEY`, CB-13b), so the list
  can't be reversed with a dictionary of public repo names; `optout list` never shows the name.
  Matching names needs `OPTOUT_KEY`: a capture that finds keyed name entries but no key stops
  (`MissingNameKey`) instead of ingesting opted-out repos. **Changing `OPTOUT_KEY`** would
  orphan these keys as it does person fingerprints, so rotate only with `privacy rekey`, which re-derives
  them from the names in your handles file (or still held locally) and refuses if one is missing.
- **Entries from before migration 0009** were unkeyed SHA-256 hashes (`rn_…`). SQL can't convert
  them, because the name isn't stored. Dropping them would resume collecting repos whose owners
  objected, so 0009 keeps them as `repo_name_unkeyed` and ingest still honours them. The migration
  logs a warning with their count, and `pigtail doctor` (and therefore an alert) warns
  (`optout_name_keys`) while any are left. To clear them:
  1. Run `pigtail privacy optout rekey`. It converts every entry whose name is found in local data
     (HN rows, `repos`).
  2. Re-add each remaining name with `optout add --repo owner/name`. This writes the keyed entry
     and deletes the unkeyed one. The source is the owner's original request, never the hash.
- **`--repo owner/name`:** if the repo is in the database, it is opted out by id *and* name. If
  not, it is opted out by name only; when it later enters the database, `optout purge` also adds
  its id.
- **Existing data:** `add` purges at once unless you pass `--no-purge`.
  - For a person, pigtail searches the retained raw snapshots for the handle **in memory** and
    deletes every snapshot that contains it (whole snapshots; replay re-downloads them and drops
    the person at ingest), plus LLM cache rows derived from those snapshots or mentioning the
    person's LLM token. Coded rows hold no handle and stay.
  - For a repo (CB-13c), it removes **every row keyed to the repo in every table**, matched by
    id, GitHub id and each `owner/name` pigtail associates with the id (renames included). The
    tables are listed in `REPO_TABLES` (`src/pigtail/privacy/deletion.py`); a test fails when a
    new table has a repo key column that isn't registered there.

    | Rows | What happens |
    |---|---|
    | Star history (daily rows and fetch log), per-repo events (polls, hourly and daily counts), launch-mode windows, shortlist entries (mention scope), ETag cache rows of its API pages | deleted |
    | HN mention rows | deleted, with their snapshots unless another repo's mention uses the same one |
    | Rank-poller stories | kept without title, url and repo link (the rank history keeps only the item id) |
    | Evidence linked to the repo, its cases or its per-repo API pages | raw bytes dropped first, then the rows and derived LLM cache rows |
    | Cases, then the `repos` row | deleted |

    Opting out by name also resolves the repo's ids (from `repos`), adds them to the list and
    purges by id. Snapshots shared with other repos are **kept**: GH Archive dumps (purged after
    30 days anyway; replay drops the repo at ingest) and HN front pages. Only the rows derived from them for this repo go.
  - Opt-outs are logged in the request log as `objection`.

### Backups and restore (CB-17)
Backups are encrypted, kept 35 days, and a restore re-applies every deletion made after the
backup was taken (retention policy §2, §5). Each `backup create` also writes an encrypted archive
of the briefs directory (`PIGTAIL_BRIEFS_DIR`) beside the database backup, with the same timestamp
and key (`pigtail-briefs-<time>.age`; ADR-071.3). `backup restore` restores it into
`PIGTAIL_BRIEFS_DIR` after the database, never overwriting a version that exists (`--briefs-in
FILE` picks another archive, `--no-briefs` skips it); `backup prune` prunes both kinds of file.
```bash
export BACKUP_RECIPIENT=age1...        # public key only: age (preferred) or a gpg fingerprint
export BACKUP_DIR=/srv/pigtail-backups    # default for --out / --dir, checked by `pigtail doctor`
uv run pigtail backup create                                       # or --out DIR
uv run pigtail backup prune                                        # deletes files > 35 days old
BACKUP_IDENTITY=/secure/age-key.txt \
  uv run pigtail backup restore --in /srv/pigtail-backups/pigtail-backup-20260925T030000Z.age --yes
```
**Setup.**
- Install `age` (`apt install age`, `brew install age`) and the PostgreSQL client tools of the
  server's major version (`postgresql-client-16`: `pg_dump`, `pg_restore`, `psql`). They are
  not in the app image. Run the commands on the host, or anywhere that can reach the database.
- Generate the key pair **off the host**: `age-keygen -o pigtail-backup.key`, and set
  `BACKUP_RECIPIENT` to the public key it prints (`age1…`). The host needs only the public key.
  Keep the identity file with the owner and in a second safe place. Without it the backups
  can't be read. gpg works too: set `BACKUP_RECIPIENT` to the full fingerprint of a key whose
  public half is in the host keyring. A value that isn't an age or SSH recipient selects gpg.
- The backup key is **not** the opt-out key (`OPTOUT_KEY`). That key is backed up separately (retention policy
  §3) and never goes into a data backup.
- Schedule `backup create` and `backup prune` daily (cron or a systemd timer). The
  object-storage replica or versioning of the snapshot bucket needs the same 35-day expiry.
- Or, on a systemd host where the scheduler runs on the host itself (not in the app image),
  set `enabled = true` for the `backup_create` and `backup_prune` jobs in `infra/schedule.toml`.
  They are off by default because they need `pg_dump` and `age`/`gpg` on that host, plus
  `BACKUP_DIR` and `BACKUP_RECIPIENT` in the scheduler's environment. If any is missing, the job
  fails and raises `job_failing`.
- `pigtail doctor` warns when the newest backup in `BACKUP_DIR` is older than 2 days and fails
  after 7 days (CB-17b), so a silently stopped backup alerts.

**`backup create`** writes one file, `pigtail-backup-<UTC time>.age` (or `.gpg`), mode 0600,
with a consistent `pg_dump` of the database and a manifest (creation time, applied migrations,
`deletion_log` size, and the hashes of every `present` snapshot). The raw snapshot bytes aren't
in it: they live in the bucket and its replicas. Nothing is written unencrypted. `pg_dump`
streams straight into `age`/`gpg`. The command refuses to run without `BACKUP_RECIPIENT`, and
refuses an output directory inside any git working tree. `pg_dump` must be at least the
server's major version. The run record (`backup.create`) holds no path and no key. The LLM
cache (`PIGTAIL_DATA_DIR/llm.sqlite3`) is not backed up; it is a cache.

**`backup restore --in FILE --yes`** *replaces* the database at `DATABASE_URL`. Stop the
scheduler first. It needs `OPTOUT_KEY` and, for age, the identity file (`--identity` or
`BACKUP_IDENTITY`; gpg uses its keyring). Steps:
1. Check that `OPTOUT_KEY` matches the live database's key fingerprint (CB-25; exit 2 on a
   mismatch, nothing changed). Read the live database's `deletion_log`, opt-out list, request
   log and key fingerprint, plus the run records they reference, before anything changes. A
   backup taken before the last `privacy rekey` (CB-26) or the last bare
   `privacy key-fingerprint --reset` (CB-35) is refused, as is one whose creation time is
   unknown: its opt-outs are keyed with the old key (runbook §4.4).
2. Decrypt and restore the dump with `pg_restore | psql` in **one transaction** that drops and
   recreates schema `public`. The transaction commits only if decryption, `pg_restore` and
   `psql` all succeed, so a wrong key or a truncated file changes nothing.
3. Apply pending migrations, then write back the carried-over tombstones, opt-outs and requests.
   Opt-outs are only added: an opt-out removed after the backup comes back and must be removed
   again.
4. Replay the tombstones. Every `raw_dropped` hash is deleted from the snapshot store again (the
   bucket may have been restored too). Evidence fetched before the tombstone moves back to
   `raw_dropped` (or `deleted_upstream`), and every `evidence_deleted` row is deleted again. A
   hash is kept only if a present capture newer than the tombstone still needs it. Upstream
   items behind a `deleted_upstream` tombstone become due at once, so the next
   `privacy deletion-sync` removes their rows.
5. Re-apply the whole opt-out list (`privacy optout purge`).
6. Run the retention purge (`retention purge`).

The JSON output reports each step (`carried`, `replay`, `refusals`, `retention`). Run records:
`backup.restore`, `privacy.optout_purge`, `retention.purge`. Run `privacy deletion-sync`
afterwards.

**If the live database is gone** (the output says `carry_over_source` is `unreachable` or
`empty`, with a warning), the tombstones and opt-outs recorded after the backup are lost with
it. Steps 4–6 still apply everything the backup itself knows, plus the time limits. Re-enter any
opt-outs and erasures received since the backup from the request channel's records (retention
policy §5), then run `privacy optout purge`. Restore the **newest** backup to keep this gap
small.

**`backup prune --dir DIR`** deletes pigtail backup files older than 35 days (`--days` can only
shorten this) and partial files older than a day. `--dry-run` only lists them. Other files in
the directory are never touched.

### Data-subject requests (CB-08)
```bash
uv run pigtail privacy request access  --platform github --handle -   # export
uv run pigtail privacy request erasure --platform github --handle -   # erase + opt out
uv run pigtail privacy requests                                       # request log
```
- **Access** writes `PIGTAIL_DATA_DIR/requests/<request id>.json` (mode 0600; override with
  `--out DIR`). pigtail's coded data holds no handles, so the requester can only be found in the
  **temporary evidence copies**: every retained raw snapshot of that platform's sources is
  re-parsed in memory and searched for the handle's opt-out fingerprint. The export contains:
  - the records found there, as pigtail codes them (role, bucket, automated flag; no handle);
  - LLM cache rows that mention the person's LLM token. Rows from LLM calls made without a
    source namespace are listed separately; review them before sending.

  Evidence whose raw bytes were already dropped has no person-level content left to search, and
  the export says how many such records exist. A retained snapshot that fails to parse is
  skipped, not fatal (CB-34): the export lists it under `unparseable_snapshots` (source, hash,
  exception type) and counts it in `snapshots_unparseable`. Access requires proof of account control
  (retention policy §5); check it before running the command. Send the file through a secure
  channel, then delete it.
- **Erasure** deletes every retained snapshot containing the handle (found in memory, as for
  access), then adds the person's opt-out fingerprint to the opt-out list so they are excluded
  at ingest from then on; see Opt-outs.
  A retained **person-level** snapshot that fails to parse is dropped too (raw bytes deleted,
  tombstone logged), because pigtail can't prove the person isn't in it (CB-34;
  `snapshots_unparseable_raw_dropped`). Project-level ones are only counted. `optout add` and
  `optout purge` behave the same way.
  Coded facts and project-level aggregates hold no person identifier and are kept (retention
  policy §5).
- **The request log** (`privacy_requests`) stores the request id, type, platform, received and
  completed times, outcome and counts. It never stores the handle or the fingerprint. The
  statutory deadline is one month (GDPR Art. 12(3)).
- Scanning raw snapshots reads every retained snapshot of the platform. With the 30-day GH
  Archive retention that is up to about 720 hourly dumps, so expect minutes to hours.

### Log hygiene (CB-18)
**Every `pigtail` command** installs `pigtail.logsafe.RedactingFilter` on the root log handlers
before it runs (CB-18b, in `pigtail.cli.main`), whether you run it by hand or the scheduler starts
it (scheduled jobs run through `python -m pigtail.scheduler.child`, which installs it before any
job code runs; the scheduler scrubs captured job output again). Python warnings go through the
same filter, and the traceback of an uncaught error is scrubbed before it reaches stderr. The
filter replaces handles, e-mails, profile URLs and DIDs with keyless placeholders (`@[handle]`,
`[profile:github]`, `[did]`) and truncates long payloads; logs never carry an alias, an opt-out
fingerprint or any other keyed token, and the opt-out key is not used to correlate log lines
(ADR-074). Pages that fail to parse are counted in run records by source and exception type only
(`<source>.parse_failed.<Type>`), never with their content. `runs.error` goes through the same
scrubber. When you add a service, call `pigtail.logsafe.configure_logging()` or `install()` on
its handlers.

# Owner Directive 001: re-scope, API backend, legal mitigations

**From:** Noémie (owner) · **Date:** 2026-09-26 · **Save as:** `ops/OWNER_DIRECTIVE_001.md`
**Public copy:** §4 redacted by owner decision (owner-specific brief content). **Status:** Binding. This supersedes any conflicting text in `docs/PRD.md`, `docs/DELIVERABLES.md`, `docs/WORK_ORDER.md` and `CLAUDE.md`. Where this directive is silent, the existing documents still apply.

## 0. How to apply this directive

1. Commit this file unchanged to `ops/OWNER_DIRECTIVE_001.md`.
2. Log one ADR per numbered section (§1–§11), using the next free ADR numbers. In each ADR, record the chat reference it replaces: CR-001 is batch runs, CR-002 is brief-based research, CR-003 is legal mitigations. The item I referred to as "ADR-004" is ADR-047.
3. Update `docs/PRD.md`, `docs/DELIVERABLES.md`, `docs/WORK_ORDER.md`, `CLAUDE.md`, `.env.example`, `ops/HUMAN_INPUTS.md` and `docs/compliance/LEGAL_REVIEW_H2.md` to match. Each change must be traceable to a section of this directive.
4. The `verifier` checks that the documents are consistent with this directive before implementation starts.
5. Then execute the milestone plan in §11.

---

## 1. Project framing

- **pigtail is a personal project for now.** It is not offered as a service, has no customers, and no findings leave the owner's instance.
- **The distribution model is the one Crawl4AI uses:** an open-source tool that each user installs and runs themselves, with their own credentials and their own data.
  - pigtail ships code, method, templates and docs. It ships **no data and no hosted service**.
  - Each user is responsible for their own instance. pigtail's job is to make the safe setup the default.
- **Versatility means anyone can run it.** Each user tailors the research to needs they state in a research brief (§2). There is no global or all-GitHub dataset.
- The owner is the first user. **Nothing in the code may be specific to her.**

## 2. Brief-based research (replaces the global universe and tiers)

**2.1 The research brief** is the core object: versioned YAML plus a guided form at `/briefs`. Several briefs can exist per install. Fields:
- `project`: description and target users.
- `field`: include and exclude boundaries, seed keywords and topics.
- `time_window`: default the last 12–18 months.
- `success`: see §3.
- `own_audience`: followers per channel, or none.
- `channels` and `geographies`: of interest, and excluded.
- `panels`: see §4.
- `reference_cases`: named projects always studied, whatever their outcome.
- `sizes`: default 20 winners and 20 losers for the field panel.
- `budget`: see §6.4.

**2.2 Flow per brief:**
1. The LLM expands the brief into keywords, topics and competitors. The user can edit the result.
2. Candidate discovery through GitHub search and topics, Show HN, awesome-lists, and GH Archive restricted to the brief (for signals only; see §5.3).
3. An LLM relevance filter, using a rubric derived from the brief. The reason for each accept or reject is logged per candidate.
4. **Shortlist review UI:** the user can accept, reject or add candidates. Every decision is logged.
5. Outcome sort using the brief's success definition.
6. Matched winners and losers (§3.3).
7. Deep forensics.
8. A brief report (the per-brief form of D2).
9. A plan (D3).

**2.3 Iteration:** the user can edit the brief and re-run. Unchanged candidates and evidence are reused from cache. Every report records the brief version, the shortlist decisions, the data version and the code commit.

**2.4 Cut** (see §5.2 for code handling):
- Tier 1 at scale, and the global universe and backfill.
- The global mechanism library and its promotion rule.
- The PRD §9.1 test suite.
- The causal toolkit beyond event studies and winner/loser contrasts.
- Any central or shared dataset.

Findings are reported as **per-brief patterns**, each showing n, the loser contrast and counterexamples.

**2.5 Kept:** snapshot or drop, matched losers, multi-metric outcomes, launch mode, and the pages D1, D5, D3, D2 (per brief) and D6. D4 remains v2.

## 3. Success definition and matching

**3.1 Success definition:** one **primary dimension**, plus **minimum thresholds** on the others. The default is adoption in the top quartile, with attention and community at or above the median. Weighted composites are available only as an advanced option.

Amend PRD §5.3 to say: a composite may be used only to rank candidates within a brief, and results are always displayed per dimension.

**3.2 Fallbacks and sensitivity:**
- If a project has no measurable adoption (it isn't a package, and has no registry downloads or dependents), community becomes its primary dimension, and the report flags this.
- If fewer than 10 winners result, relax adoption to the top third, then drop the community minimum. Log each relaxation.
- Every report includes a sensitivity check: does the winner set change under alternative definitions?

**3.3 Matching:**
- Match **exactly** on founder audience bucket and launch period (same half-year).
- Treat a standardized mean difference below 0.25 as a **target**, not a hard rule, for every other characteristic. Always show the balance for each characteristic.
- Pairs with a difference above 0.5 on any characteristic are excluded from the headline patterns and shown only in the case-level view.

## 4. First brief: the owner's panels

*Redacted in the public copy (owner decision, 2026-09-26: option (b)). §4 holds owner-specific brief content: the field panel and its widening steps, the distribution exemplars, the reference cases, and the owner's audience and channels. It lives in the owner's private brief (outside git) and in the owner's private full copy of this directive. The generic rules it relied on are public: two panels per brief and transferability labels (ADR-057), reference cases and the launch-link rule (ADR-054.3), absolute numbers per class (ADR-057.3), and "don't block on unresolved reference cases or missing brief fields; use defaults and list them in `ops/HUMAN_INPUTS.md` for confirmation in the shortlist review".*

## 5. Collection model

**5.1 Batch runs, not continuous collection.**
- `pigtail run --brief <id> [--incremental]` is idempotent and resumable from checkpoints, and runs on the owner's Mac. Docker runs only during a run.
- Default schedule for a brief: an incremental refresh every 7 days (configurable to 1–8 weeks), with a hard ceiling of 60 days between runs. Derive that ceiling from the per-source history windows (crates.io, npm, Docker Hub) and document it.
- **Launch mode** applies to tracked projects: daily for 14 days around a declared or detected launch, and every 3 h on launch day. It turns on automatically when a tracked project bursts.
- Tracked projects (D5) are refreshed weekly by default.
- Scheduling uses launchd on macOS. The server deployment path stays documented and tested through backup/restore, but it is optional.

**5.2 Global-collection code:**
- Tag the current commit `archive/global-collection` and push the tag.
- Then **delete** the 50k-repo watch list, the all-GitHub search sweeps and the global breakout detection.
- **Keep and repurpose** the HN front-page poller (used for launch mode), the scheduler (used for batch and launch-mode runs) and the per-repo collectors.
- Existing collected data is a cache that briefs may reuse. After the owner's first brief shortlist is final, **purge everything no brief references**, and log the purge.

**5.3 GitHub data:**
- Since mid-2025, GH Archive contains almost only push events. Use it **only for discovery signals** (activity, first releases), never for stars, forks, issues or PRs.
- Get stars from the GitHub API (stargazer timestamps; GraphQL sorted by star time for large repos). Get forks, issues, PRs and contributors from the GitHub API as well.
- Document the known limits: unstars can't be seen, and very large repos hit pagination caps.

**5.4 Evidence-decay study:** for the pilot cases, measure the share of key evidence that is still retrievable at 1, 7 and 30 days. If more than 10% is lost within 7 days, shorten the refresh interval for new breakouts, and report this in the pilot report.

## 6. LLM backends and cost

**6.1 Product LLM calls:** `LLM_BACKEND=api`. All of pigtail's analysis work runs on the owner's Anthropic API key under the Commercial Terms.
- Keep the `subscription` backend in the code for other users.
- Document the subscription backend as being for individual, non-commercial use only, and for the operator's own plan through the official CLI. Point readers to Anthropic's current terms.

**6.2 Agents building pigtail (Claude Code):** stay on the owner's Claude subscription (`AGENT_BACKEND=subscription`), unless she changes this.

**6.3 Model assignment and batching:**

| Stage | Model |
|---|---|
| Relevance filter | `claude-haiku-4-5-20251001` |
| Extraction and coding | `claude-sonnet-5` |
| Synthesis, report and plan | `claude-opus-5-5` |

- Use the **Batch API** for every stage that isn't time-sensitive. Launch mode may use standard calls.
- Use prompt caching for the codebook and system prompts.
- Before a run, cut the evidence down: truncate long threads, deduplicate reposts, send only relevant excerpts.
- Log the model IDs and prompt versions with every output.

**6.4 Budget** (brief schema: `budget.money_usd`, `budget.subscription_share`):
- **API:** a hard cap of USD 150 for the owner's first full brief, and a monthly cap of USD 200.
  - After the first **5 pilot cases**, report the actual cost per case and project the cost of the full brief in `ops/COSTS.md` and `ops/STATUS.md`.
  - If the projection exceeds the cap, stop and raise H6.
- **Other paid services:** USD 0.
  - BigQuery stays within the free tier.
  - Trendshift and X are off.
  - Before any step that would cost money outside the API cap, show the estimate and wait for H6 approval.
- **Subscription share for the agents:** at most about 50% of the owner's weekly usage. Pause on limits and resume. Never switch backends on your own.
- Show a cost estimate before every run in the UI and CLI.

## 7. Quality and pre-registration

- **Reliability check per brief (replaces gate G1):**
  - Deep forensics is double-coded, with adjudication.
  - The report shows Krippendorff's α **per field**.
  - Fields below 0.70 are labelled "low reliability", not dropped.
  - Findings that rest on low-reliability fields carry the same label.
- **Owner calibration (H3, optional):** if the owner codes a sample, show LLM–human agreement. Otherwise label findings "LLM-coded, not human-validated".
- **Withdraw** the existing pre-registrations for the forecasting test and the global calibration through a dated amendment explaining why. **Do not delete them.**
- Brief-level hypotheses and tests are pre-registered in `docs/preregistration/` before the outcome data they test is examined.

## 8. Privacy and legal mitigations (sections B–E)

**8.1 Code, then discard identities.**
- Spread-graph nodes are **roles and buckets**: maintainer, account by follower bucket, newsletter, community, organization.
- **Handles and personal names are never stored** in coded data. Organizations and projects may be named.
- Replace any pseudonymized-handle scheme currently in the code with this model. Migrate the existing data, then purge the handles.

**8.2 Time-limited snapshots.**
- Raw snapshots are stored locally, encrypted at rest (FileVault is required; see H1), and kept only until the brief's report is final plus 12 months. After that, delete them and keep the coded facts plus the content hash.
- Add a scheduled purge job that writes a log.
- The snapshot-or-drop principle applies at coding time. After the purge, the hash and the coded facts are the permanent record.

**8.3 Minimal collection.** Collect mentions of **shortlisted projects only**. No sweeps of individual users or accounts, and no collection of follower lists.

**8.4 Sources.**
- Official APIs only. Respect rate limits with margin, and honor deletions.
- **Reddit:** metadata only (link, title, score, timestamp) until API approval is granted.
- **Trendshift:** off by default.
- No scraping around authentication, paywalls or terms.

**8.5 Outputs.** Snapshots are never reproduced. Outputs contain at most one short attributed excerpt per source.

**8.6 Publishing (H4): disabled.** No findings, data or reports leave the instance. The git repo contains only code, docs, templates and synthetic fixtures. The CI private-data scan stays in place.

**8.7 LLM processing.** API mode only (§6.1), relying on the data processing agreement that comes with the Commercial Terms. Record in the compliance docs that inference happens in the US.

**8.8 Distribution safeguards (the Crawl4AI model).**
- Pigtail ships no data and no default targets.
- The privacy protections above are **on by default**.
- A "Responsible use" section in the README and the operator guide states that each user is the controller for their own instance, must respect platform terms, and should adapt the compliance template.
- The MIT licence stays as it is.

## 9. Compliance documents

- Rewrite `docs/compliance/` as a **template** each user adopts. The owner's completed pack is the first filled-in copy, kept outside git or with its personal details removed.
- Contents:
  - a one-page legitimate-interest assessment;
  - a short privacy notice (for the pigtail site or README);
  - a light DPIA;
  - a retention policy matching §8.2;
  - a terms memo per source.
- **Narrow `LEGAL_REVIEW_H2.md` to three questions:**
  1. Does legitimate interest (FADP; GDPR Art. 6(1)(f)) hold for collecting and coding public posts about shortlisted projects under these mitigations? Does the GDPR apply to the owner through Art. 3(2)(b) ("monitoring behaviour")?
  2. Is a public privacy notice enough (Art. 14(5)(b) and the FADP equivalent), or must individuals be informed?
  3. Optional, only if Reddit is enabled beyond metadata: Reddit's terms for this use.
- The Anthropic questions (LQ-1 to LQ-3) are **closed**, because the product runs on the API (§6.1). Record this in the file.
- **H2 does not block collection** under these mitigations. It blocks publishing only, and publishing is disabled anyway.

## 10. Human gates (updated)

| ID | What | Default if no answer within 14 days |
|---|---|---|
| H1 | Anthropic API key (Commercial Terms) with spend limits set in the Console; GitHub token; FileVault confirmed on; encrypted local backup location; Claude subscription login for the agents. Optional: BigQuery billing (free tier only), SMTP. | Continue with what is available. Record missing sources as gaps. |
| H2 | Narrowed legal consult (§9) | Collection continues under the mitigations. Nothing is published. |
| H3 | Optional human calibration coding (about 2 h) | LLM–LLM agreement only, with the label. |
| H4 | Publishing anything | **Disabled** until the owner revokes this directive. |
| H5 | Any external action on the owner's behalf | Don't act. |
| H6 | Any spend above the §6.4 caps | Don't spend. Degrade gracefully. |

## 11. Milestone re-plan (in order; tracks can overlap)

1. **Documents:** apply §0 (ADRs and document updates), with the verifier's consistency check.
2. **Cleanup and privacy:** §5.2 (tag, delete, keep and repurpose), §8.1 migration and handle purge, §8.2 purge job, `LLM_BACKEND=api` wiring, cost estimator.
3. **Brief feature:** schema, `/briefs` form, expansion, discovery, relevance filter, shortlist review UI (§2).
4. **Owner brief #1, pilot:** field panel plus exemplars. Run the first 5 cases end to end with double coding, then produce the cost report and projection (§6.4) and the evidence-decay measurement (§5.4).
5. **Owner brief #1, full run:** within the budget cap. Produce the brief report (the per-brief D2) with per-field α, the sensitivity check and transferability labels.
6. **D3 plan** for the owner's project, generated from brief #1.
7. **Launch mode and D5 tracking,** ready before the owner's first launch.
8. **Operator guide:** a new user runs their first brief in under an hour, using an example brief.

**Acceptance for this directive:**
- Every document is consistent with it (verifier sign-off).
- The global-collection code has been removed, and the archive tag exists.
- No handles remain in the stored data.
- Owner brief #1 completes within budget, with a report that shows per-field α, loser contrasts, absolute numbers and transferability labels.
- A plan exists for the owner's project.

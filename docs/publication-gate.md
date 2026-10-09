# Publication gate

Pigtail publishes a few example reports on its website and in this repository. Those are Pigtail
choosing to republish an analysis, so they go through an extra internal check, the **publication
gate**, before they are made public.

**Reports you generate yourself are not affected.** `pigtail analyze`, `pigtail render` and the
local `report.html` work exactly as before. You do not need the gate unless you maintain Pigtail's
published examples.

The gate is an engineering implementation of Pigtail's publication policy. It is not legal advice,
and passing it does not mean a report is free of risk.

## What it does

```text
research-bundle.json  (never modified)
        ↓
publication gate  ←  publication-review.yaml (human decisions, if needed)
        ↓
publication/public-report-bundle.json   minimized public projection
publication/report.html                 rendered only from that projection
publication/publication-audit.json      every change and why (internal, never published)
publication/publication-manifest.json   PASS / NEEDS_REVIEW / BLOCKED + file hashes
```

The gate is a filter, not a second research step. It removes, replaces or relabels; it never writes
new factual claims. Where new wording is needed, a person writes it.

| Rule | What happens |
|---|---|
| PUB-001 | A claim needs at least one source Pigtail read successfully. Evidence from unread sources is not shown as support. A claim with none is **blocked** until a reviewer excludes it or checks it manually. |
| PUB-002 | Contradicted, superseded or withdrawn claims are shown only inside a conflict, never as facts. |
| PUB-003 | Negative-sensitive claims and events are removed unless a person verified them **and** approved publication. Allegations are never published. Security incidents (breach, compromise, leak…) count as negative-sensitive even when the extractor missed them; to publish one, check the source and state it precisely, attributed to the company. |
| PUB-004 | Statements about a person's intentions or motives need review. Pigtail-written text that uses misconduct language is blocked. |
| PUB-005 | Personal life of identifiable people is removed, even when they shared it themselves: finances (savings, salaries, unpaid months…), family, relationships and health. Company metrics such as MRR, ARR and funding stay. Borderline cases ("pay rent", donations) need review. Excerpts with such details are not shown. |
| PUB-006 | No people list. Founders and executives (CEO, CTO, "head of"…) are named in their company role; everyone else (team members, contributors, maintainers of personal projects, investors) becomes a role ("a community contributor"). A generic "the founder" / "a co-founder" is written as the named founder when it is clear which one (the only sole founder, or a past employer or name in the text or its evidence); otherwise it stays as is. A name shared by a named and an unnamed person goes to review. *(Owner decision 2026-10-07; replaces "names in text become roles".)* |
| PUB-007 | Hacker News, Reddit, GitHub and X account names are removed ("posted by jdoe" → "posted"). Source cards show no author. URLs are left as they are, because they are the source locator. |
| PUB-008 | Source titles keep their real wording; only an unnamed person's name is replaced by their role. X and Reddit post titles get a neutral label ("Post on X"). A title that only repeats the site name ("Hatchet") needs review: `scripts/fill_source_titles.py` fills in each page's own heading. |
| PUB-009 | A company's own absolute statement ("never paid for ads") is shown as "According to the company/project". Narrative that repeats it in Pigtail's voice needs review. So does a constraint, outcome, company stage or summary that states a setback the company described about itself (incident, delay, churn, downscaling…) in Pigtail's voice: it must be attributed ("Hatchet wrote that…") or excluded; the claim itself is shown as "According to the company". A self-description that reads as a sales pitch ("forever free", "simplest") needs a neutral description or an explicit approval. |
| PUB-010 | Company-reported and third-party figures are labelled and kept in separate series. Estimate and company-database sites (GetLatka, Dealroom, Crunchbase…) are not cited at all; claims that rest only on them are removed. YC company pages are labelled "YC directory listing". *(Owner decision 2026-10-07.)* |
| PUB-011 | Placeholder-looking dates (January 1 at midnight, before 1995, after the research cutoff) are shown as a year or as undated. Wikipedia pages and listing pages (`/blog`, `/news`…) are undated. |
| PUB-012 | At most one excerpt per source, at most 15 words. Text-fragment locators are not shown. |
| PUB-013 | A source's display policy can only get stricter (the current `source-policies/` file or the bundle, whichever is stricter). |
| PUB-014 | No bundle ID, run ID, model, provider, cost, token or request counts, timings or local paths. The gate scans the output for them. |
| PUB-015 | Every report shows: "Independent analysis based on public sources. Pigtail is not affiliated with or endorsed by [Target]." |
| PUB-016 | `examples/reviewed/NOTICE.md` must state that third-party material is not relicensed by Pigtail. |
| PUB-017 | Anything that rested on a removed claim is removed too; if only some of its claims were removed (including a conflict's), it needs review. |
| PUB-018 | Gaps stay visible but never repeat removed sensitive content. |
| PUB-019 | Reddit API data (posts found with a user's own Reddit keys) is never published: the report is **blocked** and must be re-run with `--no-reddit`. *(Owner decision 2026-10-09.)* |

Also: the report header shows company-reported figures only (estimates stay in the metrics section); a
figure the company relays from someone else ("The New York Times reported…") is third-party; the
target's own description is shown as a quoted "Self-description" unless a reviewer writes a neutral
one. A figure counts as "company reported" only when it comes from the company's own site, repository
or a founder interview. Sources
that support no public claim are not listed. A source where only the title and metadata could be read
says so on every citation. Pigtail's own summaries are labelled "Summarized from cited sources", never
"Stated in sources". The target's name is written the way its sources write it.

Anything waiting for a decision is left out of the preview report, so a non-PASS output never shows
unresolved content.

## Running it

```bash
uv run python scripts/publication_gate.py pigtail-output/example-1234abcd
```

```text
Validate Research Bundle                 PASS
Check source publication policies        PASS
Check evidence eligibility               PASS
Minimize personal data                   25 removed
Check sensitive claims                   2 removed, 2 need review
Check dates                              PASS
Apply excerpt limits                     98 changed
Remove internal run metadata             PASS
Verify publication notices               PASS

Publication status: NEEDS_REVIEW
Audit: pigtail-output/example-1234abcd/publication/publication-audit.json
Review: pigtail-output/example-1234abcd/publication/publication-review.yaml
```

Exit codes: 0 `PASS`, 1 `NEEDS_REVIEW`, 2 `BLOCKED`, 3 input problem.

## Reviewing

`publication-review.yaml` lists only the items that need a person. For each one, set `decision`:

| Decision | Meaning |
|---|---|
| `approve_as_is` | Publish it unchanged. Refused where the policy requires exclusion (e.g. personal finances). |
| `approve_public_text` | Publish your wording from `public_text` instead. It is checked again. One approved text covers every finding on that same text. |
| `exclude` | Leave it out. |
| `mark_manually_verified` | You checked the source yourself (for unreadable sources and sensitive claims). The original fetch status is not changed. |

Add `rationale` and `reviewer`. A decision sticks to its object (rule + object + field), so it still
applies if the finding that prompted it is no longer raised. To change something the gate did not
flag, add your own entry with any unique `finding_id` (e.g. `manual-1`), `rule_id: PUB-REVIEW`, the
`object_ref` and `field`, and `approve_public_text` or `exclude`. To keep a person's name because it is needed to understand the
analysis, add them under `keep_identities` with a rationale. Then run the gate again. Reviewer names
are recorded in the internal audit only, never in the report. The review file is internal; never
publish it.

## Letting an AI review

The owner can let an AI make the review decisions:

```bash
uv run python scripts/ai_review.py <run-dir> --budget-usd 2     # costs API money
uv run python scripts/publication_gate.py <run-dir>
```

The model sees each item's rule, text, cited claims and, unless `--no-fetch`, a fresh read of the
source page. It may only pick the item's allowed decisions; anything else stays pending for a
person. Decisions are recorded as `AI (<model>) on behalf of the owner; human review pending` with
`[AI]` rationales, never under a person's name, and the gate re-checks every approved text. Publish
the result with `--reviewed-by ai` (recorded in `metadata.json`; the website does not show it). When a person
reviews later, they edit the same entries and put their own name in `reviewer`.

## Publishing an example

```bash
uv run python scripts/publish_example.py <run-dir> <slug> --title "..." --description "..." --reviewed-by ai|human [--featured]
```

It copies only `report.html`, `public-report-bundle.json`, `publication-manifest.json` and a new
`metadata.json`, and refuses unless the manifest is a current `PASS`: status `PASS`, no unresolved
findings, a supported policy version, and hashes that match the current Research Bundle, review
file, projection and report. Changing any of them after the gate ran invalidates the pass. There is
no force option; to make an exception, record a review decision and run the gate again.

`scripts/check_published_examples.py` (run in CI) and the website build check every folder in
`examples/reviewed/` the same way and fail if a raw `research-bundle.json`, `source-index.json`,
`run.json`, audit or review file is present.

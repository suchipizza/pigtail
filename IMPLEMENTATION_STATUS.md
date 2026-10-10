# Implementation status — Pigtail Light V1

Last updated: 2026-10-06, ~02:00 CEST (overnight autonomous session)

## Light V1 boundary (summary of the PRD)

**Build:** a public Python package and CLI that takes a GitHub repository URL or a product domain,
researches public evidence, and writes a portable **Research Bundle v0.1** plus a standalone
**HTML growth forensic** rendered only from that bundle. Repository targets add GitHub star
history with launches/posts/releases on one time axis, growth episodes, launch episodes,
post-launch windows, repeated-launch comparison and durability. Plus: source-policy mechanism,
validation, golden examples, tests, a static website/example library, and launch-ready README/docs.

**Do not build for V1:** Postgres/Supabase, Neo4j, pgvector, Pigtail Review app, CandidateChangeSet
workflow, canonical ingestion, graph projector, outbox, hosted updates, cohort pipeline, Forge,
API, MCP, CMO, auth, billing, schedulers, production monitoring. Their contracts in the spec are
frozen for compatibility only. **None of them were built.**

**Non-negotiables:** Source ≠ Claim ≠ Event ≠ Tactic ≠ GrowthEngine; MetricSnapshot ≠ Outcome;
CompanyStage ≠ StrategyPhase; timing ≠ causation; every displayed fact traces
object → Claim → EvidenceLink → Source → SourceFetch/locator; unknown is a valid answer; nothing
fabricated; Pigtail is not a content archive.

## Milestones

| Milestone | State |
|---|---|
| M0 Contract skeleton | **Done** |
| M1 First OSS vertical slice | **Done** — `pigtail https://github.com/plausible/analytics` |
| M2 Tally/general product slice | **Done** — `pigtail tally.so` finds all 10 sources of the reference page, the same MRR series, first users, PH relaunch, badge loop, AI referrals; adds origin, conflicts, gaps |
| M3 Non-curated validation (10 targets) | **Done** — [validation/2026-10-06/REPORT.md](validation/2026-10-06/REPORT.md); 4 generalizable bugs found and fixed |
| M4 Golden examples | **Published through the gate** — 5 in `examples/reviewed/` (gate output only; raw runs in gitignored `golden-runs/`). 60 review decisions made by AI on the owner's behalf (owner decision 2026-10-06); **human review pending** |
| M5 Product polish | **Mostly done** — report UX, evidence drawer, star/event chart, dark mode, doctor, clean wheel install verified |
| Publication gate (PRD 2026-10-06) | **Done** — `src/pigtail/publication/`, rules PUB-001…018, audit, file-based review, hash-bound manifest; CI + website build refuse ungated examples. all 5 examples published; review decisions made by AI on the owner's behalf (2026-10-06), human review pending |
| M6 Website + GitHub launch | **Deployed 2026-10-06** to https://suchipizza.github.io/pigtail/ (manual Website workflow). Previously: built, not deployed — static Next.js site builds from reviewed examples; Pages workflow is manual (`workflow_dispatch`) pending owner review |

## Tests

`uv run pytest` — 109 passing (unit, contract, integration, golden, 22 publication-gate tests). `ruff check`, `ruff format --check`,
`mypy`, schema drift check: clean. CI (GitHub Actions) green on `main`; CI also builds the site.

## Deviations from the specification (with reasons)

1. **Star history source.** GitHub closed stargazer *lists* to non-collaborators on 2026-06-30
   (`/stargazers` → 403 "Resource not accessible by personal access token",
   `x-accepted-github-permissions: contents=write`; GraphQL `stargazers` FORBIDDEN). Pigtail uses
   `GET /repos/{o}/{r}/stargazers/history` (weekly buckets with daily counts, no identities): works
   without a token, 1–5 requests per repo, any size, so star history is `exact` (daily net counts of
   current stargazers). No contract change needed.
2. **Narrative blocks may be `null`** when evidence cannot support them (PRD P5). Proposed as a
   0.1.x clarification; documented in docs/research-bundle.md.
3. **Config defaults.** `discovery.max_queries` default 12; default discovery provider
   `anthropic_web_search` (one API key for model + search). Provider choice remains OPEN.
4. **Extra source-policy files** `web.yaml` (required fallback) and `x.yaml`.
5. **Developer cache** `PIGTAIL_DEV_CACHE=1` (off by default, gitignored `.pigtail-cache/`).
6. **Website hosting:** GitHub Pages workflow instead of Vercel (spec allows "any equivalent static
   host"); manual trigger only.
7. **First-party feed + sitemap discovery** added to the discovery step (generic; found the Tally
   milestone posts that web search missed).

## Launch checklists (PRD §18–21)

### Demo checklist
- [x] Compelling demo — Hatchet report: repeated HN launches on the star timeline
- [x] Shows core value (repo URL → run → forensic → star timeline → events → evidence)
- [x] Understandable without narration (captions, legend, "timing only" notes)
- [x] Starts quickly (GIF opens on the CLI run, then the report)
- [x] Visually understandable; [x] technically credible (hashes, locators, quotes)
- [x] Realistic use case (maintainer comparing launches)
- [ ] Surprising/fun demo worth sharing — candidate: "unexplained spikes" in Stirling-PDF/Bruno (needs owner pick)
- [x] Segment demos: OSS (Hatchet) and product (Tally) examples
- [x] README GIF: `docs/assets/demo.gif` (generated by `scripts/make_demo_assets.py`)
- [ ] Short video for Reddit/X/LinkedIn — N/A tonight: no screen recorder/ffmpeg on this machine; GIF frames can be re-encoded
- [x] Longer technical demonstration — docs/methodology.md + docs/architecture.md (written walkthrough)

### Try-It-Now checklist
- [x] One-command install (`uv tool install git+https://github.com/suchipizza/pigtail`)
- [x] Installation documented; [x] clean-environment install tested (fresh venv from the built wheel:
  `version`, `validate`, `render` work without credentials; `doctor` reports missing key correctly)
- [x] Install from the GitHub URL tested in a fresh venv (pip); `uv tool install` uses the same package
- [x] Copy-pasteable first example; [x] useful output in ~2–6 min
- [x] No unnecessary accounts: one Anthropic key; GitHub token optional
- [x] Not ten services: 1 required, 1 optional
- [ ] Hosted playground — **N/A for V1**: runs cost real API money per target; alternative = published static example reports
- [x] Online demo — the static website with 5 example reports (once deployed)
- [ ] Sandbox — **N/A for V1** (same reason); `pigtail render` on example bundles needs no keys
- [x] Example repository/targets in README
- [x] 5-minute quick start (docs/quickstart.md); [x] common errors documented (docs/troubleshooting.md)
- [x] Dependencies obvious; [x] supported runtime stated (Python 3.12+, macOS/Linux tested, Windows untested)

### README checklist
- Above the fold: [x] name [x] one sentence [x] target user [x] problem [x] screenshot [x] install
  [x] minimal example [x] docs link [ ] live demo link (after site deploy) — star button is on the website
- Core: all sections present (why, problem, who, what, what it does not do, quick start, install,
  example, real examples, screenshots, features, environments, integrations, architecture,
  comparison, limitations, roadmap, contributing, issues, license, maintainer). Community/Discord: N/A (none yet).
- Quality: plain English, no long essay before install, examples tested, images render.
  [ ] Latest release visible — no release tagged yet (owner decision: tag `v0.1.0` after review).

### GitHub discoverability checklist
- [x] Description with search terms; [x] topics set (github-stars, star-history, product-hunt, hacker-news, …)
- [x] Ecosystem names in README; [x] issues enabled; [x] contribution guide; [x] examples directory
- [x] Docs indexed (docs/ markdown); [x] website links GitHub; [x] README links docs/examples
- [ ] Releases with notes — workflow ready (`release.yml` on `v*` tags); not tagged yet
- [ ] Discussions — **N/A for now**: no community yet to answer; enable at launch if desired
- [ ] `good first issue` labels — **N/A for now**: create 3–5 real beginner issues at launch (ideas in CONTRIBUTING.md)

## PRD acceptance tests (§24)

| Test | State |
|---|---|
| AT-L01 Clean install | Pass — `pip install git+https://github.com/suchipizza/pigtail` into a fresh Python 3.12 venv; `version`, `validate --published`, `render` work with no credentials |
| AT-L02 Repository happy path | Pass (12 repo runs) |
| AT-L03 General product path | Pass (Tally, Linear, beehiiv, Raycast, Carrd, Superhuman) |
| AT-L04 Research Bundle | Pass (validator enforces spec §21) |
| AT-L05 Standalone local report | Pass (no external requests; works from file://) |
| AT-L06 Evidence traceability | Pass (drawer shows claim → quote → source → fetch hash/locator) |
| AT-L07 OSS star/event visualization | Pass (4 OSS examples incl. 3 reviewed) |
| AT-L08 Causal discipline | Pass (code caps; audit found 0 unsupported causal claims) |
| AT-L09 Full growth analysis | Pass |
| AT-L10 Generalization (4–5 golden) | Pass (5, no product-specific code) |
| AT-L11 Non-curated validation | Pass with caveat: audited by AI reviewers, owner spot-check recommended |
| AT-L12 Source-policy compliance | Pass |
| AT-L13 Website | Built; **not deployed** (owner decision) |
| AT-L14 Launch checklists | Mostly; open items listed above |
| AT-L15 Plain-English README | Written; benefits from an unfamiliar reader's check |

## Spend tonight

Anthropic API usage ≈ **$55–60** ($52.02 recorded in 22 completed runs + 3 runs that failed before writing a bundle) (development, 12 validation runs, 5 golden runs),
plus small test calls. More than planned because product targets with large first-party blogs cost
$3–5 each. Cost reduction ideas: cap claims per page, use a smaller model for extraction
(`--model anthropic/claude-sonnet-5-5` halves cost), fewer sources by default.

## Known limitations

- Product Hunt, Reddit, X are link-only until their terms are reviewed.
- Many organic star spikes stay unexplained (Reddit/X posts, third-party posts without repo links).
- Lone popular HN posts (≥100 points) form "attention" launch episodes; content-led projects get many rows.
- Sites that block automated fetches (403) become gaps.
- Each run is ~2–6 min; extraction is the slowest stage.

## Owner decisions needed

0. **Publication review — owner read-through done (2026-10-07).** The owner reviewed all five live
   examples; every remark was applied as a generic gate rule plus reviewer edits (`manual-*`), and all
   five were republished. Decisions are still recorded as "AI (Claude Opus 5.5) on behalf of the owner;
   human review pending" in `golden-runs/runs/*/publication/publication-review.yaml` (gitignored, local
   only); sheet with every round: `~/Documents/Pigtail/Documents/publication-review-2026-10-06.md`.
   The website no longer shows review status (owner request). Optional: formal sign-off = put the
   owner's name in `reviewer`, re-run the gate, republish with `--reviewed-by human`.
   Owner policy decisions from the review (all in docs/publication-gate.md): name founders/executives,
   anonymise private people; no estimate/company-database sites (GetLatka, Dealroom…); setbacks
   attributed to the company; no family/relationship/health details; security incidents only precise
   and attributed; sales-pitch self-descriptions replaced by neutral ones.
1. ~~Publish the website~~ — done 2026-10-06: https://suchipizza.github.io/pigtail/ ; README links point there.
2. ~~License~~ — decided 2026-10-06: **MIT**.
3. ~~Default model~~ — decided 2026-10-06: **Claude Sonnet 5.5**. `--model sonnet|opus|haiku|fable` or any Claude ID (2026-10-09). First measured Sonnet runs: brag repo ≈ $0.44.
4. Legal review of `source-policies/web.yaml` (transient fetch, ≤280-char quotes) and whether to
   enable Product Hunt (a `PH_API_TOKEN` exists in the sibling project). Reddit: ~~decided 2026-10-09~~
   — official API with the user's own keys, local reports only, never published (PUB-019, `--no-reddit`).
   Verified working with the owner's keys on 2026-10-09 (brag).
5. ~~Release~~ — decided 2026-10-06: tag `v0.1.0`; the release workflow builds sdist/wheel + notes.
6. Website domain — decided 2026-10-06: GitHub Pages for now; maybe `pigtail.dev` later (it is already the schema `$id`).
   **When a domain is bought:** change `WEBSITE_URL` in `src/pigtail/__init__.py` (report logo link), README
   links, `PIGTAIL_SITE_BASE_PATH` in `.github/workflows/pages.yml`, the repo homepage, then republish examples.
7. ~~Superhuman example~~ — decided 2026-10-06: keep.

## Next tasks

Carried over from the 2026-10-09 session (start here):
- Done 2026-10-10: landing page "free" wording committed (b80b633) and deployed. Still to consider:
  mentioning optional Reddit keys / model choice on the site.
- Owner questions still open: (a) hero copy from the pig-intro script ("Pigtail / Reverse-engineer how
  products actually grow.") vs the model's copy (currently the model's); (b) make a smaller copy of the
  1 MB mascot PNG (owner asked to keep the asset unchanged, so not done).
- Published example reports predate the report header links (logo → website, GitHub button): re-run the
  gate and republish (free) when convenient.
- Consider a `v0.1.1` release: model choice, optional Reddit API, header links, Reddit fixes since v0.1.0.
- Haiku and Fable are offered by `--model` but untested with Pigtail; measure on one target if wanted (paid).
- Reddit posts that name a project only by a short name (no repo path or domain) are still missed.
- ~~Footer "checked before publication" question~~ — owner: leave it (2026-10-07).
- Plausible lost 13 GetLatka-only facts (≈3,300 customers / $23.3K MRR May 2021, 2% churn, 1,000
  trials/month); find non-estimate sources on a future refresh.
- New example runs: run the analysis with `--no-reddit --no-producthunt` (published reports never
  include Reddit or Product Hunt API data; the gate blocks them, PUB-019/PUB-020) → `publication_gate.py` → `fill_source_titles.py` (free) → `ai_review.py --budget-usd`
  (paid, ask for budget) → gate → `publish_example.py` → push → `gh workflow run pages.yml` (manual).
- Fetcher now prefers the page heading over a generic site-name title; only future runs benefit.

- Note: the earlier raw example bundles (with names, model/cost metadata) remain in git history and in
  the v0.1.0 tag. Rewriting history was not done; decide if it matters.

- Refresh Tally/Plausible golden examples after the latest prompt changes (≈$5).
- Cost reduction pass (claims cap per page; measure Sonnet 5.5 for extraction).

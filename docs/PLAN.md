# pigtail — Project Plan

> **pigtail** — evidence-based forensics of how open-source projects grow. It braids GitHub, Hacker News, Reddit and social signals into one timeline, then turns what it finds into growth mechanisms you can reuse.

## Goal

Build a continuously updated, evidence-backed library of growth and distribution mechanisms for open-source projects. Then use it to power a **growth engine** for my own open-source projects.

- **Primary user:** me and my future open-source projects. Being useful to others comes second, but the tool must be complete and reliable enough for them too.
- **Open source:** the tool, the methodology (codebook and schemas) and the UI.
- **Private:** the raw data and person-level spread graphs. Anonymized, aggregate findings may be published optionally.

## Principles

- **Evidence over assumption.** Every claim links to a stored snapshot. A claim without a snapshot is dropped.
- **Study losers too.** Every winner studied in depth gets a matched loser, and a mechanism counts only if it holds up against the losers.
- **Scale plus depth.** Automated profiles cover thousands of repos, and deep forensics covers dozens.
- **A varied panel.** Cover many categories, business models, founder audience sizes, geographies, launch types and outcomes.
- **Several success metrics.** Stars matter but can be faked, so each outcome is scored across attention, adoption, community and business signals, and each metric is tagged by how well it's verified.
- **Reuse the plumbing, build the analysis.** Existing tools such as GH Archive, OSS Insight and Trendshift show *who* is trending and *when*. pigtail explains *why* and *how*, by joining those event timelines to external evidence.
- **Keep learning.** New breakouts are detected and tracked in real time, not only examined after the fact.

## Outputs per case

| Output | What it reveals |
|---|---|
| Evidence inventory | Everything found, with its source, date, reliability and snapshot |
| Timeline | Preparation, launch, bursts, quiet periods, pivots and relaunches |
| Spread graph | Who published, who redistributed, and which connections the evidence supports |
| Asset gallery | The exact images, demos, phrases, results and links that spread |
| Mechanism cards | Why people took part and shared, the conditions required, supporting and contradicting cases, and a confidence level |
| Adaptation brief | How to test the mechanism on another product |

---

## Phase 0: Foundations

1. **Thesis and success criteria**
   - Define the decisions the growth engine must eventually make: channel, timing, asset, message and sequence.
   - Measure the system by one test: its recommendations beat a baseline on my own launches.
2. **Outcome model (multi-metric).** Each metric is tagged *verified*, *self-reported* or *estimated*.
   - **Attention:** star velocity after filtering fake stars, and reach on HN and Reddit.
   - **Adoption:** downloads from npm, PyPI, crates.io, Docker Hub and Homebrew; GitHub "used by"; dependents on deps.dev.
   - **Community:** how many contributors come back, activity on issues and PRs, and public Discord size.
   - **Business:** revenue verified through Stripe (TrustMRR, open-startup dashboards), funding (Crunchbase, YC directory, press), whether a paid cloud offering or pricing page exists, and job postings.
3. **Governance and legal**
   - Split what's public (tool, methodology, UI, aggregate findings) from what's private (raw data, person-level graphs).
   - Comply with GDPR and Switzerland's FADP, and respect each platform's terms in every collector.
   - Set retention and anonymization rules, and get a one-time legal review.

## Phase 1: Prior art and data access

4. **Literature review**
   - Academic studies of GitHub popularity, fake-star detection (StarScout), diffusion and virality research (structural virality, cascade prediction), and causal methods for observational data.
   - Go-to-market writing on open source and founder postmortems.
   - **Deliverable:** methods to borrow and hypotheses to test.
5. **Tool audit (build or reuse)**
   - Sources to assess: GH Archive/BigQuery, OSS Insight, star-history, Trendshift, the ROSS index, deps.dev, the package registries, HN Algolia, Reddit, Bluesky, YouTube, the Wayback Machine, and Chinese platforms (V2EX, Zhihu, Bilibili, Juejin).
   - For each source, record its coverage, historical depth, cost, rate limits and terms.
   - **Deliverable:** a matrix of sources plus the gaps pigtail must fill itself.

## Phase 2: Candidate universe and panel design

6. **Candidate universe**
   - Pull every repo from GH Archive that crossed velocity thresholds in the last 12 to 24 months.
   - Filter out fake stars and bots.
   - Add **losers**: repos with the same launch signals (Show HN, Product Hunt, a first burst) that plateaued, short-lived breakouts that died, and slow risers.
   - Expected scale: tens of thousands of repos.
7. **Tiered panel**
   - **Tier 1, automated profiles (thousands):** event timelines, outcome scores, and detection of launch channels.
   - **Tier 2, semi-automated evidence (hundreds):** external mentions joined to star bursts, plus classification of the assets involved.
   - **Tier 3, deep forensics (30 to 50):** a full evidence inventory, spread graph, asset gallery and mechanism cards, with human review.
   - **Stratify by** category, business model, founder audience size (none to large), geography and language, launch type, repo age, backing (VC, indie or corporate) and outcome (winner, loser, short-lived breakout).
   - Every Tier 3 winner gets a matched loser.

## Phase 3: Methodology

8. **Codebook v0**
   - Evidence types and a reliability scale.
   - Event taxonomy: prep, launch, burst, quiet, pivot and relaunch.
   - A threshold of evidence required before a spread-graph edge counts as supported.
   - Asset categories.
   - A mechanism card schema covering conditions, supporting and contradicting cases, and confidence.
9. **Adaptive modules.** One core codebook, plus modules that switch on depending on the project: AI and hype-cycle projects, B2B open-source SaaS, the Chinese ecosystem, CLI and developer tools, corporate-backed launches, and relaunches or pivots.
10. **Manual pilot on about 6 projects.** Cover winners and losers across several strata. Revise the codebook, then measure how often LLM extraction agrees with human coding; that rate sets how far the engine can run without review.
11. **Causal toolkit**
    - Event studies measuring star velocity around a front-page hit or an influencer post.
    - Matched winner and loser pairs.
    - Difference-in-differences where there's a natural comparison group.
    - Survival analysis of how momentum decays.
    - **Rule:** a mechanism is promoted into the library only if it holds against the losers.

## Phase 4: Engine v1 (open source)

12. **Data schema.** Versioned JSON schemas for evidence (including a snapshot hash), events, edges, assets, mechanisms and outcomes. Everything must be mergeable, diffable and replayable.
13. **Collectors and storage.** Connectors to the reused sources plus custom collectors for the gaps. Each collector respects platform terms and rate limits and snapshots every source automatically. The whole stack can be self-hosted, because the data isn't shared.
14. **Analysis pipeline**
    - Burst detection.
    - Matching each burst to its likely trigger.
    - LLM extraction that must cite a snapshot, or the claim is dropped.
    - A human review queue.
    - Mechanism synthesis across cases.
15. **UI.** Timelines, spread graphs, asset galleries, a mechanism browser and case comparison.

## Phase 5: Scale and continuous learning

16. **Run the tiers.** Tier 1 on thousands of repos, Tier 2 on hundreds, Tier 3 on 30 to 50. **Deliverable:** mechanism library v1, with support counts and counterexamples.
17. **Live detection**
    - A daily velocity scan opens a case automatically when a repo crosses a threshold.
    - Collection starts within 24 to 48 hours, before evidence disappears.
    - Repos that fade are also recorded as outcomes.
18. **Tracking launches before they happen.** Follow projects that have announced a launch, starting before launch day. This is the only way to capture the preparation phase properly.
19. **Updating the library.** Every new case adds to or contradicts existing mechanisms. When a mechanism's effect weakens over time, flag it as saturating.

## Phase 6: Growth engine for my projects

20. **From adaptation briefs to a launch planner.** For a given project profile, the planner ranks the mechanisms that apply, lists their preconditions and the assets required, and proposes a sequence and timing.
21. **Pre-registered experiments.** For each of my launches, write down predictions before launching ("mechanism X should produce Y within Z days"), then launch and measure.
22. **Closing the loop.** Track my own launches with pigtail and feed the results back into the library. These are the only cases where I control the intervention, which makes them the strongest causal evidence.
23. **Growth engine v1.** Execution support: generating assets, scheduling channels, monitoring, and alerts on when to amplify or pivot. It covers only mechanisms that held up through Phases 5 and 6.

## Phase 7: Open-source release

24. **Release pigtail:** collectors, pipeline, UI, codebook and schemas, with optional aggregate findings. Its own launch is a live case, planned by the growth engine and tracked by pigtail itself.

---

## Gates

- Don't start Phase 4 until the pilot codebook holds up.
- Don't start Phase 6 until the mechanism library has evidence tested against losers.
- My other products keep getting built and prepared for launch in parallel; they don't wait for pigtail.

# Literature review: prior art for pigtail (M2-T1)

Status: draft for verifier spot-check · Author role: `researcher` · Access date of every source: **2026-09-25**
Work order: `docs/WORK_ORDER.md` §4 M2 · Requirements referenced: R1.1, R3.3, R4.3, R5.3, R5.5, R7.2, R7.3, R8.1–R8.4, R9.x, R11.x, R15.7, R16.2, §9.2, §9.3

How citations work here: `[n]` points to the numbered entry in §8 ("Citation list"). Every entry there gives a URL and the access date. I checked each one with a web search or fetch on 2026-09-25. Where I couldn't confirm a claim from the source, the text says **unverified**. Practitioner and anecdotal sources are tagged **[practitioner]**. They are not peer reviewed and should not be used as effect estimates.

---

## 1. GitHub popularity and star dynamics

### 1.1 What a star means (Borges & Valente, JSS 2018)
- **What it does:** The authors surveyed 791 developers and analysed the 5,000 most-starred repositories. About three in four developers say they look at star counts before using or contributing to a project [1][2].
- **Growth patterns:** They clustered one-year star time series into four patterns [3]:

  | Pattern | Repos | Share | New stars in one year |
  |---|---|---|---|
  | slow | 2,706 | 58.2% | +19.8% |
  | moderate | 1,399 | 30.0% | +63.9% |
  | fast | 434 | 9.3% | +218.6% |
  | viral | 110 | 2.3% | +1,317.2% |

- **Why fast and viral repos grew:** In the follow-up survey:
  - 64.7% (22) of fast-growth respondents named active promotion as the main reason.
  - 72.7% (16) of viral-growth respondents linked the growth to successful social-media posts, "mostly Hacker News" [3].
- **Data needed:** Star timestamps from the GitHub API, plus surveys.
- **Validity limits (stated by the authors):**
  - The sample is the top 5,000 repos only.
  - Domain labels were manual.
  - The second survey got 115 answers (33.3% response rate).
  - Growth patterns were computed on the last year of stars only [3].
- **Reproducibility:** The paper is public on arXiv [2]. I did not locate a replication package (**unverified**).
- **pigtail use:**
  - Use the four-pattern taxonomy as a descriptive baseline for R3.5 outcome classes (`slow_riser` vs burst classes).
  - The survey attribution to HN is a prior for R5.5 trigger ranking.

### 1.2 Factors behind popularity (Borges, Hora & Valente, ICSME 2016)
- **What it does:** Studies 2,279 popular repos. Language, application domain and new releases are associated with star counts, and the authors report four growth patterns [4][5].
- **Companion paper:** A PROMISE 2016 paper predicts star counts using stars from the last six months [6]. I verified that the paper exists via the ACM DL. I did not check its accuracy figures (**unverified**).
- **pigtail use:** These are the covariates for R4.3 matching (category, language, repo age) and the baseline predictor for the §9.1 forecasting test ("star velocity + category base rate").

### 1.3 How developers promote projects (Borges & Valente, IEEE Computer 2019)
- **Channels:** In 100 popular repos, Twitter, user meetings and blogs are the most common promotion channels [7].
- **Hacker News data:** The authors found 3,019 HN posts that referenced 96 of the projects. Upvote quartiles were 2, 3 and 12. They define "successful" posts as the top 10%, meaning at least 132 upvotes [8].
- **Before vs after a successful post:** Median stars were 74 in the 3 days before and 138 in the 3 days after. The distributions differ under a one-tailed Mann-Whitney U test (p ≤ 0.05), with a medium effect size (Cliff's d = −0.372) [8].
- **Validity limits:**
  - This is a naive pre/post comparison with no control group.
  - It covers only very popular projects.
  - Upvotes are endogenous to project quality.
- **pigtail use:** This is the simplest form of the R8.1 event study. pigtail must improve on it with matched controls and pre-trend checks (§4).

### 1.4 Tweets → stars (Fang, Lamba, Herbsleb & Vasilescu, ICSE 2022, Distinguished Paper)
- **What it does:** Links 44,544 tweets to 2,370 repos [9][10].
- **Design:** Each tweeted project is compared with a matched project that was not tweeted, using before/after star changes (a difference-in-differences-style estimate). Other confounding events get explicit controls [11].
- **Effect:** About +7% stars (+1.2 stars per tweet burst), and about +2% new contributors (+1 new developer per 250 tweet bursts) [11][12].
- **Data needed:** Tweets with GitHub links (the Twitter/X API, now costly or restricted; see the source matrix), plus star and contributor timelines.
- **Validity limits:**
  - Matching only on observables.
  - Tweets are selected on project quality and momentum.
- **Reproducibility:** The NSF PAR open-access copy exists [10]. A replication package exists: slide 20 of the authors' slides says "Replication package!" [11], and the artifact repository `CMUSTRUDEL/oss-twitter-promotion-icse2022` (MIT) "accompanies our ICSE 2022 paper". It holds the CSV data behind Table 2, Table 3 and appendix Figure 2 [81].
- **pigtail use:** This is the closest published template for R8.2 and R8.3 (matched control + pre/post difference). It is also evidence that attention and contributor outcomes should be scored separately (PRD §5.3).

### 1.5 Novelty and popularity (Fang, Herbsleb & Vasilescu, ICSE 2024)
- **Finding:** In the Python ecosystem, more novel projects get more stars. The same projects have smaller teams and a higher long-run risk of abandonment [13].
- **pigtail use:** Attention and community outcomes can move in opposite directions. This supports multiple outcomes with no composite score (§5.3, R3.2) and argues against treating stars as success (see H-L6).

### 1.6 Star-history tools
- **star-history.com:** Plots cumulative star curves and calls itself "the de facto GitHub star history graph" [14][practitioner/tool].
- **Not verified:** I did not verify how it collects data (sampling, caps on large repos). pigtail should not rely on it as a data source.
- **pigtail approach:** Build star series from GH Archive `WatchEvent`s. GitHub's docs describe a WatchEvent as "a user stars a repository" [15].
  - GH Archive has archived the public timeline since 2011-02-12, in hourly files and as a BigQuery dataset [16].
  - GH Archive does **not** record un-stars. GitHub's docs say the `WatchEvent` action "Can only be `started`" [15], and GH Archive mirrors the Events API. So "net stars" cannot be computed from GH Archive alone; treat the series as gross stars. Net counts come from the star-history endpoint, which gives daily net counts (ADR-032); the stargazer lists are restricted to admins and collaborators since 2026-06-30, so the stargazers API is no longer used.

---

## 2. Fake stars

### 2.1 StarScout (He, Yang, Burckhardt, Kapravelos, Vasilescu, Kästner; ICSE 2026)
**Title history:**
- v1 (2024-12-18): "4.5 Million (Suspected) Fake Stars in GitHub: A Growing Spiral of Popularity Contests, Scams, and Malware".
- v2 (2025-09-06): renamed "Six Million (Suspected) Fake Stars in GitHub…". The paper appeared at ICSE 2026 [17][18][19].

**What it detects.** StarScout flags two signatures [20]:
1. **Low-activity signature:** A GitHub account that has only one WatchEvent (it starred one repository), plus at most one other event in the same repository on the same day [20].
2. **Lockstep signature:** Found with a version of **CopyCatch** (Beutel et al., WWW 2013 [21]), which looks for groups of accounts that star the same set of repositories within a short time window.
   - Parameters:
     - n ≥ 50 accounts
     - m ≥ 10 repositories
     - Δt = 30 days
     - ρ = 0.5 (so ≥ 25 of the accounts star each repository in the window)
   - The search starts from seed repositories and iteratively adjusts the window [20].
3. **Campaign post-processing:** A repository has a "fake star campaign" when both of these hold:
   - In at least one month it got more than 50 fake stars and fake stars were more than 50% of that month's stars.
   - Fake stars are more than 10% of its all-time stars [20].

**Results.**
- Data: GH Archive, July 2019 to December 2024 [20].
- Before post-processing: 6.0 million suspected fake stars (1.06 M low-activity, 4.93 M lockstep) [20].
- After post-processing: 18,617 repos with campaigns and 301k participating accounts (3.81 M fake stars) [20].
- Trend: activity surged in 2024. Most fake stars promote short-lived phishing or malware repos. The rest go mostly to AI/LLM, blockchain, tool and tutorial repos [17].
- Promotion effect (fixed-effects panel AR(2)):
  - A 1% rise in fake stars in month t goes with +0.07% real stars in t+1 and +0.03% in t+2. That is about 5× smaller than the real-star autocorrelation (0.36).
  - Cumulative fake stars have a negative coefficient from about t+2 onward. The authors read this as "a liability in the long term" [20].

**Validation (as reported).**
- **Recall:** Measured against the Check Point "Stargazers Ghost Network" ground truth [22]. StarScout found 688 of 847 repos (81.23%) and 11,903 of 15,672 accounts (75.95%) [20].
- **Criterion validity:** By January 2025, 90.42% of flagged repos had been deleted, against 5.03% of a random baseline. For accounts the figures were 57.07% vs 3.54% [20].
- **No direct precision estimate.** The paper says precision "is inherently harder to evaluate." It illustrates the risk with a hypothetical: even ~99% precision would leave ~180 false-positive repos [20].
- **Limitations stated by the authors:**
  - The heuristics and thresholds (notably the 50-star threshold) are ad hoc, and sensitivity testing is hard.
  - StarScout evidence alone is insufficient for disciplinary action [20].

**Data needed.**
- The full GH Archive event stream.
- Size: the paper says StarScout scanned "more than 20 TB of data from GHArchive" (§3.3 Implementation) [20]. The README estimates ≥ 20 TB processed for low-activity and ≥ 40 TB for lockstep on BigQuery (about $6.25/TB) [23].
- A **local DuckDB/Parquet mode** exists [24]:
  - "quick" (1 week, ~11 GB), "month" (~45 GB) and "year" (~300 GB) presets; "research" replicates the paper (~700 GB).
  - Needs 16+ GB RAM and 200–500 GB of temporary space for large runs.
  - Needs no cloud credentials. GitHub API enrichment is optional.

**Reproducibility.**
- Code: https://github.com/hehao98/StarScout, Apache-2.0 (licence read from the GitHub API on 2026-09-25) [23].
- Replication package: Zenodo (CC BY 4.0). It contains the code plus a 5.6 GB MongoDB dump [25]. The paper cites DOI 10.5281/zenodo.17009693, and the Zenodo record shows 10.5281/zenodo.17009694 (a concept vs version DOI, I assume).
- The README lists released outputs for detection snapshots 240701, 241001 and 250101 [23].
- **Privacy note:** Those outputs and the dump contain account-level data. pigtail must keep them in private storage only (CLAUDE.md; PRD §10).

### 2.2 Other fake-star work
- **Dagster [practitioner/industry]:**
  - Blog post by F. Marrow, 2023-03-16 [26], with code at `dagster-io/fake-star-detector` [27].
  - Heuristic: accounts created in 2022 or later with ≤ 1 follower, following ≤ 1, no gists, ≤ 4 repos, empty profile, and star date = creation date = update date. A clustering method sits on top.
  - Reported result: 98% precision and 85% recall, measured against stars **they bought themselves** (500 from one vendor and 100 from another) [26].
  - Limits: small ground truth, profile fields from a 2023 snapshot, and needs GitHub API profile lookups.
- **Check Point "Stargazers Ghost Network" (2024) [practitioner/security research]:**
  - More than 3,000 ghost accounts and more than 2,200 malicious repos were observed [22].
  - It is used as StarScout's recall ground truth.
- **Successor methods:** I found no peer-reviewed successor to StarScout as of 2026-09-25. Searches only returned re-uses, press coverage and gists. The searches re-run on 2026-09-25 were:
  - `fake GitHub stars detection 2025 2026 paper`
  - `StarScout fake stars follow-up study ICSE FSE MSR 2026`
  - `"fake stars" GitHub detection arXiv 2026`
  - `GitHub star inflation lockstep detection peer-reviewed "six million" fake stars cited by`
  - They returned the StarScout paper itself (arXiv, ACM, CMU copies), its code and forks, a CMU news item, gists, blog posts and press, and [28]. There was no new peer-reviewed detection method. A general web search is not a citation index, so this negative finding is limited. A Google Scholar "cited by" check was not done. A 2026 arXiv study of multi-agent frameworks avoids stars rather than filtering them, calling star counts a reflection of "hype cycles and inorganic activity" [28]. **StarScout is the current reference method.**

### 2.3 Recommendation for PRD R3.3: reproduce StarScout
Adopt **StarScout's two signatures plus its campaign post-processing, using the authors' public local DuckDB pipeline on GH Archive** [23][24]. Pin the commit and all parameters (n=50, m=10, Δt=30 d, ρ=0.5, and the 50 / 50% / 10% campaign thresholds) in a versioned config, and store both the raw and the filtered star series (R3.3).

Why this method and not Dagster's:
- It is peer reviewed (ICSE 2026).
- It uses **GH Archive only**, which pigtail already ingests for R1.1. No per-account profile API calls are needed.
- It is Apache-2.0 with a CC BY 4.0 replication package.
- It has external recall validation.
- The low-activity signature is cheap enough to run incrementally.

**Validation plan (to be run in M2 "fake-star method" and reported in `docs/reports/`):**
1. **Replication check (fidelity).**
   - Run the pipeline in `month` mode, then in a 6-month chunk, on a window covered by a released StarScout snapshot (e.g. the chunk ending 2024-07-01 → `data/240701`) [23].
   - Compare pigtail's flagged repo set with the released set.
   - Accept when Jaccard is ≥ 0.9 on repos and ≥ 0.85 on accounts. These thresholds are pigtail's choice, not from the paper. Explain any difference: GH Archive re-downloads, code version, chunk boundaries.
2. **Criterion validity (same logic as the paper, fresh data).**
   - For a random sample of flagged repos and accounts, and an equal random sample of unflagged ones from the same months, check existence via the GitHub API (404 = deleted) at T+90 days.
   - Report deletion rates with 95% CIs.
   - Expect a large gap; the paper reports 90% vs 5% for repos [20]. A gap under 20 percentage points triggers review.
3. **Recall on known campaigns.** Where public ground truth exists (e.g. the Check Point list [22]), report recall, as in the paper.
4. **Precision audit on pigtail's own universe.**
   - For the flagged stargazers of repos in pigtail's case universe (R4.1), a `verifier` codes a stratified random sample of ≥ 200 flagged accounts. The sample is pseudonymized, stored privately, and never committed.
   - Coding rubric: a human-plausible activity history outside the flagged window.
   - Report precision with a Wilson 95% CI.
   - This fills the gap the authors name (no direct precision estimate) [20].
5. **Sensitivity analysis.**
   - Vary the campaign thresholds (50 → 25/100 stars; 50% → 30/70%; 10% → 5/20%) and ρ (0.4/0.6).
   - Report how many pigtail cases change outcome class (R3.5) or cross the R1.1 threshold.
   - Findings that flip under reasonable settings get flagged `sensitive_to_fake_star_filter`.
6. **Impact report.** Report the share of R1.1 detections and of winners in each stratum that carry a campaign flag, and the raw-vs-filtered star delta at T+7/T+30/T+90.

Known limits to document:
- Stars bought from "aged" accounts that don't show either signature will be missed. The authors say evasion is possible (see the paper's threats section [20]).
- Short windows reduce lockstep recall, because Δt = 30 d and chunks are 6 months.
- A flag is **suspected**, never proof. Per the authors' caution [20], pigtail must not name individual repos as fake in any public output.

---

## 3. Diffusion, cascades and HN → GitHub effects

### 3.1 Structural virality (Goel, Anderson, Hofman & Watts, Management Science 62(1); listed as 2015 by the search index, volume dated 2016 — year **unverified**)
- **What it does:** Defines a measure (mean pairwise distance in the diffusion tree) that runs from pure broadcast to multi-generation viral spread. Using about a billion Twitter diffusion events, the authors find that popular items grow through broadcast, viral spread and every mix in between [29][30].
- **Data needed:** A full adoption tree (who adopted from whom). That is rarely observable for GitHub stars, but partly observable for reposts, quotes and links.
- **Validity limit:** Parent attribution in the tree is inferred.
- **pigtail use:**
  - Compute structural virality on the R5.3 spread graph (`redistributed` and `cited` edges only, at `supported` evidence level).
  - Classify bursts as broadcast-dominated or viral.
  - Report it as descriptive.

### 3.2 Cascade prediction (Cheng, Adamic, Dow, Kleinberg & Leskovec, WWW 2014)
- **Findings:** On Facebook photo reshares, cascade growth becomes more predictable as more of the cascade is observed. Temporal and structural features dominate, and early **breadth** predicts large size better than depth [31][32].
- **pigtail use:** This frames the §9.1 forecasting test (predict the T+30 class from day-7 data). It suggests early reach across communities as a candidate predictor (H-L4).

### 3.3 Self-exciting (Hawkes) popularity models: SEISMIC (Zhao et al., KDD 2015)
- **What it does:** Models reshares as a doubly stochastic self-exciting point process. It needs only timestamps and follower degrees, and reports about 15% relative error on final cascade size after one hour of observation [33][34].
- **pigtail use:** An alternative model for R8.4 and R12.3 decay: fit a self-exciting process to hourly stars after a trigger and estimate the "final size".

### 3.4 Endogenous vs exogenous bursts (Crane & Sornette, PNAS 2008)
- **Findings:** In daily views of about 5 million YouTube videos, most activity looks like a Poisson process. Hundreds of thousands of bursts are followed by power-law relaxation, and the exponents fall into three classes (distinguishing exogenous from endogenous shocks and critical from subcritical ones) [35][36].
- **pigtail use:** Directly relevant to R5.2 (`burst` vs `quiet`) and R8.4. Fit relaxation exponents after each burst. A sudden jump followed by fast decay is the signature of an exogenous trigger (e.g. an HN front page). A slow build-up is endogenous word of mouth.
- **Validity limit:** Stars are much sparser than views, so the fits will be noisy for small repos.

### 3.5 Social influence and unpredictability (Salganik, Dodds & Watts, Science 2006)
- **Findings:** In an artificial music market with 14,341 participants, stronger social influence increased both inequality and unpredictability of success. Quality only partly determined outcomes [37][38].
- **pigtail use:** Any mechanism card (R9.1) must carry wide uncertainty, and matched-loser contrasts (§9.3) are essential, because identical "moves" can yield very different outcomes by chance.

### 3.6 Hacker News as a channel
- **Borges & Valente 2019:** Median 74 → 138 stars in the 3 days before/after a top-10% HN post, with no control group (§1.3) [8].
- **Meakpaiboonwattana et al., arXiv 2025:**
  - Tracks 1,814 AI-related repos shared on HN from over 2,000 submissions.
  - At least 19% of AI developers promoted their projects on HN.
  - Forks, stars and contributors rose after posting [39].
  - Observational. I did not check the peer-review status (**unverified**).
- **Kraishan, arXiv 2025, "Launch-Day Diffusion":**
  - 138 AI/LLM repo launches on HN in 2024–25, with data from Algolia HN Search and the GitHub REST API.
  - Mean gains of +121 stars in 24 h, +189 in 48 h and +289 in 7 days.
  - OLS with HC1 errors: posting 12–17 UTC is associated with about +200 stars. The "Show HN" tag is not significant (β = −119.2 at 48 h, SE 138.3, p = 0.39).
  - The authors state that the "observational design does not allow us to establish causation."
  - Code: https://github.com/obadaKraishan/Launch-Day-Diffusion [40][41].
- **Stoddard, ICWSM 2015:** Estimates each article's "intrinsic quality" (votes under unbiased, equal exposure) with Poisson regression on vote time series. Finds that popularity on Reddit and HN is a relatively strong reflection of that quality [42].
- **pigtail use:** Stoddard's quality estimate is a candidate covariate for R4.3 (launch-signal magnitude). It separates a post's appeal from its luck with rank.
- **Gap:** I found **no peer-reviewed causal estimate** of HN front-page *position* on GitHub stars, such as a front-page vs page-2 discontinuity. Practitioner write-ups exist (§5). This gap is an opportunity for pigtail (H-L2).

---

## 4. Causal inference for observational data

### 4.1 Matching and balance diagnostics
- **Matching as preprocessing (Ho, Imai, King & Stuart 2007):** Matching before a parametric model reduces model dependence. The `MatchIt` package implements it [43][44].
- **Balance rules (Stuart 2010):**
  - For regression adjustment to be trustworthy, absolute standardized mean differences (SMD) should be < 0.25 and variance ratios between 0.5 and 2 (citing Rubin 2001).
  - Balance should **not** be judged with hypothesis tests or p-values, because balance is an in-sample property and tests confound balance with power [45][46].
- **Stricter thresholds (MatchIt vignette):** SMD < 0.1, and 0.05 for prognostically important covariates. It also recommends checking variance ratios and eCDF statistics (e.g. the maximum eCDF difference) [47].
- **Balance diagnostics for propensity-matched samples (Austin 2009):** A standard reference, which also gives the sampling distribution of the SMD under correct specification [48].
- **Data needed:** The pre-launch covariates in R4.3, measured before T.
- **Validity limit:** Matching handles observed confounding only. Unobserved founder reach and quality remain.
- **pigtail use (R4.3, §9.2):**
  - Keep the PRD gate (SMD < 0.25) as the **hard** gate, which is consistent with Stuart/Rubin [45].
  - Also report the share of covariates with SMD < 0.1 [47], plus variance ratios in [0.5, 2] [45].
  - Never use p-values as the balance criterion [45][47].

### 4.2 Event studies and staggered difference-in-differences
- **Why ordinary TWFE fails:** When treatment timing varies, the two-way fixed-effects (TWFE) DiD coefficient is a weighted average of all 2×2 comparisons. Some of those use already-treated units as controls, so the estimate can be uninterpretable (Goodman-Bacon 2021) [49].
- **Contaminated leads and lags:** In TWFE event studies, lead and lag coefficients can be contaminated by effects from other periods, and heterogeneity alone can create spurious pre-trends (Sun & Abraham 2021) [50].
- **Robust estimators:**
  - Callaway & Sant'Anna 2021: group-time ATTs with conditional parallel trends. R package `did` [51][52].
  - Borusyak, Jaravel & Spiess 2024: imputation estimator. Python and R code exist [53].
  - Sun & Abraham 2021: interaction-weighted estimator [50].
- **Violations of parallel trends:** Rambachan & Roth 2023 give sensitivity bounds for how large a violation can be. R package `HonestDiD` [54].
- **Practitioner synthesis:** Roth, Sant'Anna, Bilinski & Poe 2023 [55].
- **Data needed:** A panel of star, download and contributor velocity per repo per day; trigger dates; a never- or not-yet-treated comparison pool.
- **Validity limits:**
  - Triggers are not random. An HN post often follows an organic uptick (anticipation), so pre-periods must be inspected.
  - Spillovers occur when one post mentions several repos.
- **pigtail use:**
  - R8.1 uses Sun–Abraham or Callaway–Sant'Anna event studies around triggers, with not-yet-treated repos as controls.
  - R8.3 uses Callaway–Sant'Anna DiD with HonestDiD sensitivity.
  - The §9.3 "event-study effect whose 90% CI excludes zero" should be computed with one of these robust estimators, not with TWFE.

### 4.3 Synthetic counterfactuals for single cases
- **CausalImpact (Brodersen et al. 2015):** Bayesian structural time-series models predict the counterfactual path after an intervention. The effect can evolve over time, and covariates and seasonality are allowed. Google publishes an R package [56][57].
- **pigtail use:** Tier 3 case forensics (R5.5) for a single repo, with matched losers' series as covariates.
- **Validity limit:** Assumes the covariates are unaffected by the treatment.

### 4.4 Survival analysis for momentum decay
- **Cox proportional hazards (Cox 1972):** The hazard is modelled as a function of covariates times an unspecified baseline over time, which handles censored durations [58][59].
- **Applications in OSS:**
  - Valiev, Vasilescu & Herbsleb (ESEC/FSE 2018) model dormancy risk for 46,547 PyPI projects using ecosystem-level factors [60].
  - Avelino et al. (ESEM 2019) study abandonment and survival in 1,932 popular GitHub projects [61][62].
- **pigtail use (R8.4, R16.2, R12.3):**
  - Define the "event" as velocity falling below X% of the post-burst peak (e.g. the §8.2 `short_lived` rule of < 10% of peak by T+90).
  - Model time-to-decay with Cox or accelerated-failure-time models, with launch-cohort quarter as a covariate.
  - Saturation shows up as a hazard for a mechanism that rises across cohorts.
- **Validity limit:** Check the proportional-hazards assumption (Schoenfeld residuals; general practice, no specific source verified here).

---

## 5. OSS go-to-market and launch postmortems [practitioner — anecdotal, survivor-biased]
None of these sources has a control group. pigtail should treat them only as **candidate mechanisms** (R9.2 `candidate`) and sources of hypotheses.

- **Supabase, "How we launch" (2021-11-26) [63]:**
  - A "Launch Week" that ships one major feature per day, a minute-by-minute channel schedule, community and angel amplification, and retrospectives.
  - Claims databases under management grew "47% month-on-month for the last 18 months" (self-reported).
- **PostHog, "How we got our first 1,000 users" (J. Hawkins, 2024-06-21) [64]:**
  - An HN launch about 5 weeks into building, reaching 300 deployments within a couple of days.
  - About $2,000 of paid Twitter promotion, which "combined with the successful launch on Hacker News, got our repo trending on GitHub."
  - Note that paid promotion is a confounder of any HN-effect estimate.
- **Hacker News Show HN guidelines [65]:** Show HN must be something people can try. "Please don't ask friends to upvote or comment." This matches pigtail's non-goals (no vote manipulation) and is a codebook rule: evidence of vote rings counts as a manipulation flag.
- **Dr. Marco Maier, "Stats of being on the HN front page" (2024-01-03) [66]:** About 35–40 unique visitors per minute on the front page and about 10 per minute on page two, for a single post ranked #17 with 68 points. n = 1.
- **OpenSauced / B. Douglas, "Growth Hacking Killed GitHub Stars" (2024-04-10) [67]:**
  - Argues stars have inflated, noting GitHub grew from under 10 M to over 100 M users.
  - Cites coordinated launch events (Zed, Plane, Godot).
  - Proposes commit velocity, fork/star ratios and external PRs instead.
- **Dagster fake-stars blog (2023) [26]:** An industry experiment in which the authors bought stars (see §2.2).
- **Not verified:** A Medium post comparing HN vs Product Hunt outcomes for a developer tool came up in search. It returned HTTP 403, so it is **unverified** and excluded.

---

## 6. Content-analysis reliability and LLM coders

### 6.1 Krippendorff's alpha
- **Why alpha:** Hayes & Krippendorff (2007) propose alpha as the standard reliability measure. It works for any number of coders, any level of measurement, and missing data [68][69].
- **Computation:** Krippendorff's 2011 note (literature updated 2013) describes how to compute it [70].
- **Conventional thresholds:** α ≥ 0.800 for firm conclusions, and 0.667 ≤ α < 0.800 for tentative conclusions only. I found this via Wikipedia, which cites Krippendorff 2004 pp. 241–243 [71]. I did not check the primary book (**unverified at primary source**).
- **pigtail note:** The PRD gate α ≥ 0.70 (R7.2, §9.2, R15.7) falls in the "tentative" band. See H-L9.

### 6.2 LLMs as coders
- **Gilardi, Alizadeh & Kubli (PNAS 2023):**
  - The PNAS version [72] uses four samples of tweets and news articles (n = 6,183). Zero-shot ChatGPT beat crowd workers by about 25 percentage points on average across the four datasets. The same figures are in the PubMed abstract (PMID 37463210) [82].
  - Its intercoder agreement was higher than that of crowd workers and trained annotators [72].
  - Cost was < $0.003 per annotation [72].
  - The arXiv preprint [73] reports an earlier sample of 2,382 tweets, and says ChatGPT beat crowd workers on four of five tasks. Cite [72] for the published figures.
- **Reiss (arXiv 2023):** ChatGPT's classification consistency across repeats, temperature and prompt variants can fall short of reliability thresholds [74].
- **Pangakis, Wolken & Fasching (arXiv 2023):** Across 27 tasks and 11 datasets with GPT-4, performance varies by task. Any LLM annotation must be validated against human labels [75].
- **Ziems et al. (Computational Linguistics 2024):** A roadmap for LLMs as computational-social-science tools [76].
- **Törnberg (Sociologica 2024):** Best practices:
  - validate against human labels
  - test prompt stability
  - version prompts
  - prefer open models for reproducibility
  - document choices [77].
- **Barrie, Palaiologou & Törnberg (arXiv 2024, rev. 2026):** A "Prompt Stability Score" with the `promptstability` Python package [78].
- **Baumann et al. (arXiv 2025), "LLM hacking":**
  - 37 tasks from 21 studies, 18 models, 13 M labels and 2,361 hypotheses.
  - About 31% of hypotheses reach incorrect conclusions with state-of-the-art LLMs, and about half with small models.
  - A few prompt paraphrases can make "virtually anything" significant.
  - Human annotations and regression-estimator corrections are the most effective mitigations [79].
- **Zheng et al. (NeurIPS 2023), LLM-as-a-judge:**
  - GPT-4 judges reach over 80% agreement with human preferences.
  - Position, verbosity and self-enhancement biases are documented [80].
- **pigtail use (R7.2, R7.3, R7.4, R15.7):**
  - Double coding across different models and prompts.
  - α per field.
  - A human or verifier audit sample.
  - Prompt-stability scoring before a prompt version is frozen.
  - Randomize option order in adjudication prompts (position bias).
  - Downstream mechanism tests should use a correction for LLM-label error, as recommended by Baumann et al. [79].

---

## 7. Implications for pigtail

### 7.1 Methods to borrow
| Method | Source | pigtail requirement | Decision |
|---|---|---|---|
| StarScout low-activity + lockstep (CopyCatch) + campaign thresholds, local DuckDB mode | [20][23][24] | R3.3, R1.1 | **Adopt** as the fake-star filter; validate per §2.3 |
| Four star-growth patterns | [3] | R3.5 | Use as a descriptive baseline for outcome classes |
| Matched control + pre/post difference for promotion effects | [11] | R8.2, R8.3 | Template for loser contrasts |
| Nearest-neighbour matching, SMD < 0.25 hard gate, SMD < 0.1 target, variance ratio 0.5–2, no p-value balance tests | [45][47] | R4.3, §9.2 | Adopt |
| Sun–Abraham / Callaway–Sant'Anna / BJS event studies; HonestDiD sensitivity; no plain TWFE with staggered triggers | [49][50][51][53][54] | R8.1, R8.3, §9.3 | Adopt |
| CausalImpact synthetic counterfactual for single cases | [56] | R5.5 | Optional for Tier 3 |
| Cox / AFT models on time-to-decay; relaxation-exponent classes | [58][35] | R8.4, R16.2, R12.3 | Adopt |
| Structural virality on the spread graph | [29] | R5.3 | Descriptive metric |
| Hawkes/SEISMIC final-size forecast | [33] | §9.1 test 1, R12.3 | Candidate forecaster vs baseline |
| Krippendorff α per field; prompt-stability score; human audit; LLM-label error correction | [68][78][79] | R7.2, R7.3, R15.7, §9.2 | Adopt |

### 7.2 Hypotheses
Each hypothesis is pre-registrable (R11.1) and states what would falsify it.

- **H-L1 (R3.3, R1.1, R3.5):** After StarScout filtering, a measurable share of R1.1 velocity detections in AI/LLM categories in 2024–2026 carry a fake-star campaign flag, and the share is higher than in non-AI categories. Motivated by [17][20]: AI/LLM is among the promoted categories. *Falsified if* flag rates show no category difference at the 95% level.
- **H-L2 (R8.1, R5.5, §9.3):** Reaching the HN front page causes a positive short-run star effect (48 h) against matched HN posts that stayed off the front page with similar early votes. The effect is smaller than naive pre/post estimates (e.g. the 74 → 138 median in [8]; the +189 mean in [40]). *Falsified if* the robust event-study effect's 90% CI includes zero, or is not smaller than the naive estimate.
- **H-L3 (R8.4, R16.2, R5.2):** Exogenous triggers (HN or influencer posts) produce faster post-burst decay than endogenous word-of-mouth growth, following the Crane–Sornette classes [35]. Operationalized as a higher hazard of falling below 10% of peak velocity. *Falsified if* hazard ratios between trigger types have CIs that include 1.
- **H-L4 (§9.1 test 1, R5.3):** Early breadth, meaning the number of distinct communities or sources linking the repo in the first 72 h, predicts the T+30 outcome class better than raw early star count. This comes from the breadth finding in [31]. *Falsified if* adding breadth does not improve the Brier score over the velocity + category baseline.
- **H-L5 (R8.2, R3.2):** Promotion events move attention (stars) much more than community (returning external contributors), following Fang et al. 2022 (+7% stars vs +2% contributors) [11]. *Falsified if* the effect ratio in pigtail's matched design is not > 2.
- **H-L6 (R3.2, R3.5, §5.3):** Among winners by attention, novelty or hype-category repos show lower T+90 community retention than matched non-novel winners, following Fang et al. 2024 [13]. *Falsified if* the community difference is null.
- **H-L7 (R9.1, R4.3, §9.3):** For frequently recommended practitioner mechanisms (launch weeks [63], Show HN [65], paid amplification [64]), prevalence among winners exceeds prevalence among matched losers by less than a naive winners-only frequency suggests. This is the survivorship bias argument, with [37] on unpredictability. *Falsified if* winner-vs-loser prevalence ratios match winner-only frequencies. This directly tests §9.1 test 2.
- **H-L8 (R3.3, R8.1):** Repos with fake-star campaigns show a short-lived real-star uplift (< 2 months) and lower T+365 adoption than matched unflagged repos, consistent with [20]. *Falsified if* the adoption difference is null or positive.
- **H-L9 (R7.2, R15.7, §9.2):** LLM double-coding reaches α ≥ 0.70 on low-inference fields (dates, channel, asset type) but not on high-inference fields (e.g. "why people shared"). This follows the task-dependence findings in [74][75][79].
  - *Decision rule:* report α per field. Fields with α < 0.667 cannot feed mechanism promotion. Consider α ≥ 0.80 for fields that drive promotion, since 0.70 is only the "tentative" band [71]. Raise this with the owner as a possible ADR.
- **H-L10 (R4.3, §9.2):** Matching on the PRD covariates alone leaves SMD ≥ 0.25 on founder audience size for a non-trivial share of pairs. Adding Stoddard-style post-quality estimates [42] and pre-launch velocity improves balance. *Falsified if* the balance gate passes with the base covariates at the target yield.

### 7.3 Open issues
1. No peer-reviewed causal estimate of HN front-page position on GitHub stars was found. pigtail's R8.1 design would be new. It needs HN rank polling (PRD §8.1 "own rank polling"), which must be in place early. Rank history probably cannot be backfilled: the Algolia API's search hits carry `points`, `num_comments`, `created_at` and a `front_page` tag, but no rank or position field (observed on 2026-09-25; see source matrix §2.3), and the official Firebase API serves only the current `topstories` list [83]. This is our inference from those fields, not a documented statement.
2. StarScout has no direct precision estimate [20]. pigtail's audit (§2.3 step 4) is the only precision evidence, and it needs private storage of account-level samples. This depends on the compliance pack (M3).
3. Resolved: GH Archive does not record un-star events, because a `WatchEvent` action "Can only be `started`" [15]. "Net stars" in R1.1 ("≥ 100 net stars") cannot be computed from GH Archive. Net counts come from the star-history endpoint, which gives daily net counts (ADR-032; source matrix §2.33); the stargazers API is no longer usable, since stargazer lists are restricted to admins and collaborators since 2026-06-30.
4. The PRD α ≥ 0.70 threshold is below Krippendorff's conventional 0.80 [71] (secondary-source verification). Recommend an ADR.
5. The Fang et al. 2022 exact identification details are summarized from the authors' slides and CMU news [11][12]. The ACM full text returned HTTP 403, so a verifier with access should confirm them.
6. The Twitter/X data used in [9] may no longer be affordable. This affects replicating H-L5 on X and is deferred to the source matrix.

---

## 8. Citation list (every source, all accessed 2026-09-25)

1. Borges & Valente, "What's in a GitHub Star?…", JSS 146 (2018) — https://www.sciencedirect.com/science/article/abs/pii/S0164121218301961
2. Same, arXiv:1811.07643 — https://arxiv.org/abs/1811.07643
3. Same, full text (ar5iv) — https://ar5iv.labs.arxiv.org/html/1811.07643
4. Borges, Hora & Valente, "Understanding the Factors That Impact the Popularity of GitHub Repositories", ICSME 2016 — https://arxiv.org/abs/1606.04984
5. Same, researchr record — https://researchr.org/publication/BorgesHV16-0
6. Borges, Hora & Valente, "Predicting the Popularity of GitHub Repositories", PROMISE 2016 — https://dl.acm.org/doi/10.1145/2972958.2972966
7. Borges & Valente, "How do Developers Promote Open Source Projects?", IEEE Computer 52(8) 2019 — https://arxiv.org/abs/1908.04219
8. Same, PDF (HN section 3.4) — https://arxiv.org/pdf/1908.04219
9. Fang, Lamba, Herbsleb & Vasilescu, "'This Is Damn Slick!'…", ICSE 2022 — https://conf.researchr.org/details/icse-2022/icse-2022-papers/84/-This-Is-Damn-Slick-Estimating-the-Impact-of-Tweets-on-Open-Source-Project-Populari
10. Same, NSF PAR record — https://par.nsf.gov/biblio/10339912
11. Same, author slides — https://cmustrudel.github.io/slides/fang2022twitter.pdf
12. CMU news, "Tweeting a Help Wanted Sign" (2022) — https://www.cs.cmu.edu/news/2022/twitter-open-source
13. Fang, Herbsleb & Vasilescu, "Novelty Begets Popularity, But Curbs Participation…", ICSE 2024 — https://dx.doi.org/10.1145/3597503.3608142
14. star-history (tool) — https://github.com/star-history/star-history
15. GitHub Docs, event types (WatchEvent) — https://docs.github.com/en/rest/using-the-rest-api/github-event-types
16. GH Archive — https://www.gharchive.org/
17. He et al., arXiv:2412.13459 (abstract, v1/v2 history, ICSE'26) — https://arxiv.org/abs/2412.13459
18. Same, v1 HTML ("4.5 Million…") — https://arxiv.org/html/2412.13459v1
19. Same, ACM DL ICSE 2026 — https://dl.acm.org/doi/10.1145/3744916.3764531
20. Same, v2 full text (methods, validation, limitations) — https://arxiv.org/html/2412.13459v2
21. Beutel, Xu, Guruswami, Palow & Faloutsos, "CopyCatch…", WWW 2013 — https://dl.acm.org/doi/10.1145/2488388.2488400
22. Check Point Research, "Stargazers Ghost Network" (2024) — https://research.checkpoint.com/2024/stargazers-ghost-network/
23. StarScout repository and README — https://github.com/hehao98/StarScout
24. StarScout local (DuckDB) README — https://github.com/hehao98/StarScout/blob/main/scripts/local/README.md
25. StarScout replication package, Zenodo — https://zenodo.org/doi/10.5281/zenodo.17009693
26. Dagster, "Detecting Fake GitHub Stars with Dagster" (2023) [practitioner] — https://dagster.io/blog/fake-stars
27. dagster-io/fake-star-detector — https://github.com/dagster-io/fake-star-detector
28. Zhang et al., "Adoption and Ecosystem Health…Multi-Agent Frameworks", arXiv 2026 — https://arxiv.org/abs/2607.02453
29. Goel, Anderson, Hofman & Watts, "The Structural Virality of Online Diffusion", Management Science 62(1) — https://pubsonline.informs.org/doi/10.1287/mnsc.2015.2158
30. Same, author PDF — https://5harad.com/papers/twiral.pdf
31. Cheng, Adamic, Dow, Kleinberg & Leskovec, "Can Cascades be Predicted?", WWW 2014 — https://arxiv.org/abs/1403.4608
32. Same, PDF — https://www.cs.cornell.edu/home/kleinber/www14-cascades.pdf
33. Zhao, Erdogdu, He, Rajaraman & Leskovec, "SEISMIC…", KDD 2015 — https://arxiv.org/abs/1506.02594
34. SEISMIC project page — https://snap.stanford.edu/seismic/
35. Crane & Sornette, "Robust dynamic classes…", PNAS 105(41) 2008 — https://www.pnas.org/doi/10.1073/pnas.0803685105
36. Same, arXiv:0803.2189 — https://arxiv.org/abs/0803.2189
37. Salganik, Dodds & Watts, "Experimental Study of Inequality and Unpredictability…", Science 311 (2006) — https://www.science.org/doi/10.1126/science.1121066
38. Same, PubMed — https://pubmed.ncbi.nlm.nih.gov/16469928/
39. Meakpaiboonwattana et al., "Social Media Reactions to Open Source Promotions…", arXiv:2506.12643 — https://arxiv.org/abs/2506.12643
40. Kraishan, "Launch-Day Diffusion…", arXiv:2511.04453 — https://arxiv.org/abs/2511.04453
41. Same, HTML full text — https://arxiv.org/html/2511.04453v1
42. Stoddard, "Popularity Dynamics and Intrinsic Quality in Reddit and Hacker News", ICWSM 2015 — https://ojs.aaai.org/index.php/ICWSM/article/view/14636
43. Ho, Imai, King & Stuart, "Matching as Nonparametric Preprocessing…", Political Analysis 15(3) 2007 — https://www.cambridge.org/core/journals/political-analysis/article/matching-as-nonparametric-preprocessing-for-reducing-model-dependence-in-parametric-causal-inference/4D7E6D07C9727F5A604E5C9FCCA2DD21
44. Ho, Imai, King & Stuart, "MatchIt…", J. Stat. Software — https://www.jstatsoft.org/article/view/v042i08
45. Stuart, "Matching Methods for Causal Inference: A Review and a Look Forward", Statistical Science 25(1) 2010, arXiv PDF — https://arxiv.org/pdf/1010.5586
46. Same, JHU record — https://pure.johnshopkins.edu/en/publications/matching-methods-for-causal-inference-a-review-and-a-look-forward-5/
47. MatchIt vignette "Assessing Balance" — https://cran.r-project.org/web/packages/MatchIt/vignettes/assessing-balance.html
48. Austin, "Balance diagnostics…propensity-score matched samples", Stat. Med. 28 (2009) — https://onlinelibrary.wiley.com/doi/10.1002/sim.3697
49. Goodman-Bacon, "Difference-in-differences with variation in treatment timing", J. Econometrics 225(2) 2021 — https://www.sciencedirect.com/science/article/abs/pii/S0304407621001445
50. Sun & Abraham, "Estimating dynamic treatment effects in event studies…", J. Econometrics 225(2) 2021 — https://arxiv.org/abs/1804.05785
51. Callaway & Sant'Anna, "Difference-in-Differences with multiple time periods", J. Econometrics 225(2) 2021 — https://arxiv.org/abs/1803.09015
52. `did` R package — https://github.com/bcallaway11/did
53. Borusyak, Jaravel & Spiess, "Revisiting Event-Study Designs…", REStud 91(6) 2024 — https://academic.oup.com/restud/article/91/6/3253/7601390
54. Rambachan & Roth, "A More Credible Approach to Parallel Trends", REStud 90(5) 2023 — https://academic.oup.com/restud/article-abstract/90/5/2555/7039335
55. Roth, Sant'Anna, Bilinski & Poe, "What's Trending in Difference-in-Differences?", J. Econometrics 235(2) 2023 — https://arxiv.org/abs/2201.01194
56. Brodersen et al., "Inferring causal impact using Bayesian structural time-series models", AoAS 9(1) 2015 — https://projecteuclid.org/journals/annals-of-applied-statistics/volume-9/issue-1/Inferring-causal-impact-using-Bayesian-structural-time-series-models/10.1214/14-AOAS788.full
57. CausalImpact package — https://google.github.io/CausalImpact/CausalImpact.html
58. Cox, "Regression Models and Life-Tables", JRSS-B 34(2) 1972 — https://rss.onlinelibrary.wiley.com/doi/10.1111/j.2517-6161.1972.tb00899.x
59. Same, PDF — https://web.stanford.edu/~lutian/coursepdf/cox1972paper.pdf
60. Valiev, Vasilescu & Herbsleb, "Ecosystem-level determinants of sustained activity…PyPI", ESEC/FSE 2018 — https://dl.acm.org/doi/10.1145/3236024.3236062
61. Avelino, Constantinou, Valente & Serebrenik, "On the abandonment and survival of open source projects", ESEM 2019 — https://arxiv.org/abs/1906.08058
62. Same, TU/e record — https://research.tue.nl/en/publications/an-empirical-investigation-of-the-abandonment-and-survival-of-ope/
63. Supabase, "How we launch at Supabase" (2021) [practitioner] — https://supabase.com/blog/supabase-how-we-launch
64. PostHog, "How we got our first 1,000 users" (2024) [practitioner] — https://posthog.com/founders/first-1000-users
65. Hacker News, Show HN guidelines [platform rules] — https://news.ycombinator.com/showhn.html
66. M. Maier, "Stats of being on the Hacker News front page" (2024) [practitioner] — https://marcotm.com/articles/stats-of-being-on-the-hacker-news-front-page/
67. B. Douglas / OpenSauced, "Growth Hacking Killed GitHub Stars" (2024) [practitioner] — https://dev.to/opensauced/growth-hacking-killed-github-stars-40on
68. Hayes & Krippendorff, "Answering the Call for a Standard Reliability Measure for Coding Data", CMM 1(1) 2007 — https://www.tandfonline.com/doi/abs/10.1080/19312450709336664
69. Same, PDF — https://www.asc.upenn.edu/sites/default/files/2021-03/Answering%20the%20Call%20for%20a%20Standard%20Reliability%20Measure%20for%20Coding%20Data.pdf
70. Krippendorff, "Computing Krippendorff's Alpha-Reliability" (2011, updated 2013) — https://www.asc.upenn.edu/sites/default/files/2021-03/Computing%20Krippendorff's%20Alpha-Reliability.pdf
71. Wikipedia, "Krippendorff's alpha" (thresholds, citing Krippendorff 2004 pp. 241–243) — https://en.wikipedia.org/wiki/Krippendorff's_alpha
72. Gilardi, Alizadeh & Kubli, "ChatGPT outperforms crowd workers for text-annotation tasks", PNAS 120 (2023) — https://www.pnas.org/doi/10.1073/pnas.2305016120
73. Same, arXiv:2303.15056 — https://arxiv.org/abs/2303.15056
74. Reiss, "Testing the Reliability of ChatGPT for Text Annotation and Classification: A Cautionary Remark", arXiv 2023 — https://arxiv.org/abs/2304.11085
75. Pangakis, Wolken & Fasching, "Automated Annotation with Generative AI Requires Validation", arXiv 2023 — https://arxiv.org/abs/2306.00176
76. Ziems et al., "Can Large Language Models Transform Computational Social Science?", Computational Linguistics 50(1) 2024 — https://aclanthology.org/2024.cl-1.8/
77. Törnberg, "Best Practices for Text Annotation with Large Language Models", Sociologica 18(2) 2024 — https://sociologica.unibo.it/article/view/19461
78. Barrie, Palaiologou & Törnberg, "Prompt Stability Scoring for Text Annotation with Large Language Models", arXiv 2024 — https://arxiv.org/abs/2407.02039
79. Baumann et al., "Large Language Model Hacking: Quantifying the Hidden Risks of Using LLMs for Text Annotation", arXiv 2025 — https://arxiv.org/abs/2509.08825
80. Zheng et al., "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena", NeurIPS 2023 — https://arxiv.org/abs/2306.05685
81. Fang et al. 2022 replication artifact, `CMUSTRUDEL/oss-twitter-promotion-icse2022` (MIT) — https://github.com/CMUSTRUDEL/oss-twitter-promotion-icse2022
82. Gilardi, Alizadeh & Kubli 2023, PubMed record PMID 37463210 (abstract read through the Europe PMC API, because PubMed served a CAPTCHA) — https://pubmed.ncbi.nlm.nih.gov/37463210/
83. Hacker News official API (Firebase) README — https://github.com/HackerNews/API

## 9. Citation list for verifier spot-check
Suggested 10 for the M2 acceptance spot-check. They cover the load-bearing claims; the full list is in §8.

| # | Claim to check | URL |
|---|---|---|
| [20] | StarScout low-activity definition; CopyCatch n=50, m=10, Δt=30 d, ρ=0.5; campaign thresholds 50 / 50% / 10%; recall 81.23% / 75.95%; deletion 90.42% vs 5.03% | https://arxiv.org/html/2412.13459v2 |
| [17] | Title change v1 → v2; ICSE'26; 2019–2024 window | https://arxiv.org/abs/2412.13459 |
| [24] | Local DuckDB mode; disk and RAM requirements | https://github.com/hehao98/StarScout/blob/main/scripts/local/README.md |
| [25] | Zenodo package, CC BY 4.0, 5.6 GB MongoDB dump | https://zenodo.org/doi/10.5281/zenodo.17009693 |
| [8] | HN successful post ≥ 132 upvotes; median 74 → 138 stars; Mann-Whitney p ≤ 0.05, Cliff's d = −0.372 | https://arxiv.org/pdf/1908.04219 |
| [3] | Four growth patterns 58.2 / 30.0 / 9.3 / 2.3% | https://ar5iv.labs.arxiv.org/html/1811.07643 |
| [40] | 138 launches; +121 / +189 / +289 stars; Show HN n.s. | https://arxiv.org/abs/2511.04453 |
| [45] | SMD < 0.25 and variance ratio 0.5–2 (Rubin 2001); no hypothesis tests for balance | https://arxiv.org/pdf/1010.5586 |
| [79] | ~31% incorrect conclusions for SOTA LLMs; 37 tasks / 21 studies / 18 models | https://arxiv.org/abs/2509.08825 |
| [11] | Fang 2022 design (matched control, pre/post) and +7% stars / +2% contributors | https://cmustrudel.github.io/slides/fang2022twitter.pdf |

---

## Changelog

- 2026-09-25 — corrections after verifier spot-check M2-T4. §1.3: Mann-Whitney and Cliff's d added from [8]. §1.4: Fang et al. replication package confirmed ([11] slide 20, [81]). §1.6 and §7.3 item 3: the un-star question is resolved from [15]. §2.1: "~20 TB" now cited to [20]. §2.2: the StarScout successor search queries are recorded. §6.2: Gilardi figures attributed to [72], with PubMed [82] and the 2,382-tweet note for [73]. §7.3 item 1: the rank-backfill claim is labelled as inference and sourced. Citations [81]–[83] added.
- 2026-09-25 — fixes after verifier (ADR-036 alignment): §1.6 and §7.3 item 3 now point net star counts to the star-history endpoint (ADR-032) instead of the stargazers API (restricted since 2026-06-30).

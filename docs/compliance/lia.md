# Legitimate-interest assessment (LIA)

**Status:** draft for legal review (gate H2). This is not legal advice. The compliance agent wrote it and is not a lawyer.
**Version:** 0.1 · 2026-09-25 · Task M3-T2
**Scope:** every pigtail deployment that processes personal data taken from public platforms. The assessment is written for the owner's own deployment. A self-hoster who runs pigtail is the controller of their own deployment (see [legal-review-questions.md](legal-review-questions.md), LQ-14) and has to adopt or adapt this LIA.
**Access date:** every URL was accessed on **2026-09-25**.
**Related:** [dpia.md](dpia.md) · [retention-policy.md](retention-policy.md) · [privacy-notice.md](privacy-notice.md) · [terms-memos.md](terms-memos.md) · [legal-review-questions.md](legal-review-questions.md)

---

## 0. Legal framework and method

| Instrument | Provision relied on | Source |
|---|---|---|
| GDPR (Regulation (EU) 2016/679) | Art. 6(1)(f): lawful if "necessary for the purposes of the legitimate interests pursued by the controller or by a third party, except where such interests are overridden by the interests or fundamental rights and freedoms of the data subject … in particular where the data subject is a child." | [EUR-Lex](https://eur-lex.europa.eu/eli/reg/2016/679/oj) ¹ |
| GDPR | Art. 4(5) pseudonymisation; Recital 26 ("Personal data which have undergone pseudonymisation … should be considered to be information on an identifiable natural person") | same |
| GDPR | Art. 21(1) right to object to processing based on Art. 6(1)(f), "including profiling" | same |
| GDPR | Art. 89(1) safeguards for scientific or historical research and statistical purposes (data minimisation; "may include pseudonymisation") | same |
| GDPR | Art. 9(1)/(2)(e) special categories; "manifestly made public by the data subject" | same |
| EDPB | Guidelines 1/2024 on processing of personal data based on Article 6(1)(f) GDPR, version 1.0, adopted 8 October 2024 (public-consultation version; we found no later final version on the EDPB site) | [EDPB page](https://www.edpb.europa.eu/our-work-tools/documents/public-consultations/2024/guidelines-12024-processing-personal-data-based_en) · [PDF](https://www.edpb.europa.eu/system/files/2024-10/edpb_guidelines_202401_legitimateinterest_en.pdf) |
| Swiss FADP (SR 235.1) | Art. 6 principles; Art. 30 breaches of personality rights (incl. 30(2)(b) processing "contrary to the express wishes of the data subject" and 30(3) data the subject "makes … generally accessible and has not explicitly prohibited any processing"); Art. 31(1) justification by "an overriding private or public interest"; Art. 31(2)(e) processing "for purposes not related to specific persons, in particular for research, planning or statistics"; Art. 31(2)(f) public figures | [fedlex](https://www.fedlex.admin.ch/eli/cc/2022/491/en) ² |

¹ EUR-Lex answered automated requests with a bot challenge. We read the official text from the EU Publications Office (Cellar, CELEX 32016R0679: http://publications.europa.eu/resource/celex/32016R0679) and quote only from it.
² The fedlex page needs JavaScript. We read the English consolidated version in force from 2023-09-01 from the fedlex filestore (https://www.fedlex.admin.ch/filestore/fedlex.data.admin.ch/eli/cc/2022/491/20230901/en/html/fedlex-data-admin-ch-eli-cc-2022-491-20230901-en-html-1.html). English is not an official Swiss language; the German, French and Italian texts prevail. We did not confirm whether a later consolidation exists.

**Method.** We follow the three cumulative conditions that EDPB Guidelines 1/2024 set out: (1) pursuit of a legitimate interest, (2) necessity, and (3) a balancing test that weighs the interest against the data subjects' rights, taking into account their reasonable expectations and any additional safeguards. For Switzerland the FADP does not require a legal basis for private processing. It requires compliance with the principles (Art. 6), and any breach of personality rights (Art. 30) must be justified (Art. 31). We therefore read the three-step test as the structure for the Swiss "overriding interest" justification as well (§5).

---

## 1. Processing covered

| # | Processing activity | PRD refs | Personal data involved |
|---|---|---|---|
| A1 | **Velocity scan.** Hourly GH Archive dumps are ingested to detect star or fork bursts per repo. | R1.1, TM-01 | GitHub login of every actor (pseudonymised when parsed), event type, repo and timestamp. Raw hourly dump kept as a snapshot. |
| A1b | **Stargazer events for tracked repos (bot-filter confirmation).** The per-repo Events API is polled every 15–60 min, only for repos with an open case, tracked repos (R17.4) or repos above the pre-threshold, so that the R1.1 bot filter and the lockstep flag can run on who starred in the window (ADR-032.2). Star counts themselves come from the star-history endpoint, which holds no personal data (ADR-032.3). **Not built yet (M1-T24).** | R1.1, R3.3, R17.4, TM-33, LQ-29 | `WatchEvent.actor` (login and id) with repo and time, pseudonymised at ingest and kept as person-level rows for **at most 30 days**, then only aggregates (bot-removed counts, lockstep flags, `coverage_ratio`). The raw response also holds the repo's other recent public events (pushes, issues, PRs, with actors and payload text); only `WatchEvent`s are parsed. Never used to build, store or export a stargazer list. |
| A2 | **Mention capture for open cases.** Enabled sources are searched for mentions of a repo, and each hit is snapshotted. | R1.2, R1.3, TM-02–04, TM-06, TM-19, TM-30 | Posts, comments and replies that mention the repo, with author handle or DID, timestamp, engagement counts and the text (raw in the snapshot, pseudonymised when parsed). |
| A3 | **LLM coding.** Snapshot text is sent to Claude to extract structured, cited facts (event type, asset type, edge type). | F7, F15 | Redacted snapshot text (e-mails, phone numbers, profile URLs, DIDs and @mentions replaced) and quoted spans. |
| A4 | **Case forensics.** Timelines, spread graphs (nodes are accounts, publications or communities) and burst-to-trigger attribution. | R5.1–R5.5 | Pseudonymised account nodes, edges (`published`, `redistributed`, `cited`, `replied`) and reach. |
| A5 | **Outcome scoring and matching.** | F3, F4 | Mostly project-level. Star series come from the star-history endpoint (daily counts, no identities; TM-33, ADR-032). The filtered star series use A1b aggregates only. Returning-contributor counts are derived from pseudonymised actors. |
| A6 | **Library, planner, trends, analyzer.** | F9, F10, F16, F17 | Aggregates. Links from each claim back to evidence (private UI only). |
| A7 | **Publication of aggregate findings** (only after H2 and H4). | R13.3, §4 | None by design. Aggregate and anonymised only. |

The following are out of scope for this LIA because they are either not processed or are documented gaps: Reddit, X, YouTube, Product Hunt, Lobste.rs, dev.to, Juejin, Zhihu, Bilibili, TrustMRR, Crunchbase and YC ([terms-memos.md](terms-memos.md), ADR-010). If a gap source is ever enabled under an operator's own agreement, this LIA must be re-run.

**OpenDigger GH-event mirror (TM-32): not processed.** It is a GAP pending LQ-28 and off by default (ADR-032.1); no connector exists, and `evidence.terms_basis` may not cite TM-32. It would carry the same person-level data as GH Archive at much higher capture, so enabling it needs a new LIA entry, not just a clearance. For transparency: the M1-T18 research on 2026-09-25 streamed OpenDigger hourly files once, filtered them to `WatchEvent`s in scratch storage, used them only for per-repo counts and event-id matching, and deleted them after the analysis; no identities were recorded (`docs/research/detection-replan.md` §7).

**The GitHub stargazers API** (`/repos/{o}/{r}/stargazers`) is no longer used: GitHub restricted it to admins and collaborators on 2026-06-30, and ADR-032 superseded ADR-012, which relied on it.

**Data subjects:** people whose public activity on a platform mentions or acts on an open-source repo, including:
- GitHub users who star, fork, open issues or PRs, or push (for A1b: people who star a tracked repo, whose identity is otherwise no longer listed to third parties since GitHub's 2026-06-30 restriction);
- Hacker News, Bluesky and V2EX posters and commenters;
- the maintainers and owners of the repos themselves, whose repo names often contain a personal login (`owner/repo`);
- authors of "Who is hiring?" posts (organisation-level only, per TM-30).

---

## 2. Step 1: purpose test (is there a legitimate interest?)

EDPB Guidelines 1/2024 require the interest to be lawful, "clearly and precisely articulated", and "real and present, and not speculative".

| Interest | Whose | Lawful? | Articulated precisely? | Real and present? |
|---|---|---|---|---|
| I1 **Research and statistics** on how open-source projects grow: which mechanisms work, tested against matched losers (PRD §1–2, §9). | Controller, researchers (PRD §3) | Yes. Research and statistics are purposes the GDPR itself recognises (Art. 89) and the FADP names in Art. 31(2)(e). | Yes: the mechanism library, outcome model and matched comparisons (F3, F4, F8, F9). | Yes. The capture layer is being built (M1; it runs locally in development only, with no production capture per ADR-022), and the research purpose depends on evidence that disappears if it isn't captured. |
| I2 **Evidence-based launch planning** for any OSS maintainer (planner D3, analyzer D5). | Third parties: maintainers, DevRel teams | Yes | Yes (F10, F17) | Yes |
| I3 **Commercial interest of the operator** (e.g. a DevRel team's competitive analysis) | Controller | Yes, subject to the platform terms each connector respects (terms memos) | Yes (PRD §3, "DevRel / GTM team") | Yes when the deployment is commercial, which we assume throughout (terms-memos assumptions). |
| I4 **Freedom of information and scientific freedom:** open, reproducible methodology and aggregate findings (G5). | Controller, the public | Yes | Yes | Yes |
| I5 **Integrity and verifiability:** every claim must be traceable to a stored snapshot ("snapshot or drop", PRD §5.1), and fake-star filtering (R3.3). | Controller, the public | Yes | Yes | Yes |

**Not pursued, and excluded by design:**
- profiling or evaluating individuals as such;
- marketing to data subjects;
- selling personal data (forbidden by TM-02, TM-27 and others);
- training models on the data (forbidden by several memos);
- identity resolution across platforms.

These exclusions are part of the balancing (§4). If any of them is ever needed, a new LIA is required.

**Result of Step 1:** met.

---

## 3. Step 2: necessity test

The question is whether each category of personal data is necessary for the interests above, and whether a less intrusive means would work.

| Data | Why it is needed | Less intrusive alternative considered | Result |
|---|---|---|---|
| **Actor identity for GitHub events (A1)** | Star velocity must count distinct actors per repo-hour. The lockstep fake-star filter (R3.3) needs per-actor features within a window. Returning contributors (§8.1) need a stable actor id across months. | (a) Aggregate counts only: this defeats bot and fake-star filtering. (b) A stable keyed pseudonym instead of the login: **adopted** (`src/pigtail/pseudonymize.py`, on `main`; applied at ingest by `src/pigtail/connectors/base.py` `records()`, **I**). Per-actor features are held in memory for one window only and never persisted (`src/pigtail/capture/velocity.py`, **I**). Bot logins are dropped without hashing (`src/pigtail/connectors/gharchive.py`, **I**). | Necessary in pseudonymised form. **Raw logins in the stored hourly snapshot** go beyond what the analysis needs; they are kept only for replay (PRD §5.5) and integrity, and purged after ≤ 30 days (CB-04, partly implemented: `capture/retention.py`). See DPIA R4. |
| **Stargazer identity from per-repo events (A1b)** | R1.1 counts stars "after bot filtering", and R3.3 removes fake-star campaigns. Both need to know which stars in a window came from bot or lockstep accounts. The star-history endpoint gives counts only (ADR-032). | (a) Star-history only, no filter: R1.1's filter step and R3.3 would be unmet, and the filtered series `unknown` everywhere. (b) Drop bot logins by name in memory without storing anything: **adopted** for the name rule (as S3). The lockstep flag compares actors across hours and repos, so it needs stable pseudonyms for a while. (c) GH Archive only: ~2 % of stars in 2026 (ADR-028), too little to filter. (d) Keep pseudonymised star rows for ≤ 30 days (the lockstep window, Δt = 30 d), for tracked repos only, then aggregates: **adopted** (TM-33 conditions). | Necessary in pseudonymised, 30-day, tracked-repos-only form. **Planned, not built** (M1-T24; CB-22, CB-23 proposed in [dpia.md](dpia.md) §9). |
| **Post text and author (A2, A3)** | Snapshot-or-drop (PRD §5.1) and the citation validator (R7.1: "quoted span … found in the snapshot") need the original text. Classifying edges (R5.3) needs to know that the same account published and then redistributed. | (a) Store only coded facts: this makes claims unverifiable and breaks replay (PRD §5.5). (b) Pseudonymise the author when parsing: **adopted**. (c) Strip identifiers before the LLM call: **adopted** (`src/pigtail/llm/client.py`, ADR-006). | Necessary, with raw text limited to private storage and to 24 months. |
| **Engagement and reach of accounts (A4)** | Burst-to-trigger attribution (R5.5) ranks triggers by timing and reach. | Use reach buckets (e.g. log bands) instead of exact follower counts: **planned** (CB-10). | Necessary in coarsened form. |
| **Spread graph linking accounts (A4)** | Needed for the mechanism evidence ("who redistributed what"). | Graphs at community or publication level only: this loses the redistribution mechanism for individual influencers. The adopted compromise is pseudonymised nodes, private-only graphs, and no public release of graphs that identify people (PRD §4). | Necessary for Tier 2 and Tier 3 cases only (≈ 300 repos), not for the Tier 1 universe. |
| **Maintainer identity via `owner/repo`** | The repo is the unit of analysis. For personal accounts the owner login is part of the repo's name. | Replace names with numeric repo ids in exports: **partly adopted** (`repo.id = github:<host_id>`). `full_name` is still stored. | Necessary internally. Public outputs must not name individual-owned repos without that owner's consent or the public-figure rule (LQ-7). |
| **Retention of 24 months (raw person-level data)** | Outcomes are scored up to T+365 (R3.1). The universe covers a trailing 24 months (R4.1). Matched losers are chosen after outcomes are known. | 12 months would be shorter than T+365 plus the matching window. | Proportionate, if enforced ([retention-policy.md](retention-policy.md)). Enforcement is **implemented** (CB-01: `pigtail retention purge`, `src/pigtail/privacy/retention.py`; longer periods are rejected at startup) and must be scheduled daily by the operator. |

**Result of Step 2:** met on condition that the "planned" controls in this table and in [dpia.md](dpia.md) §7 exist before the processing they cover starts. The collection-specific conditions are listed in §6.

---

## 4. Step 3: balancing test

### 4.1 Nature of the data
- The data is public activity and posts about software. It is mostly professional or hobby activity.
- **No special categories are sought.** Free text can still incidentally reveal political views, health, religion or trade-union membership (for example a post that mentions a boycott, or a bio). Art. 9(2)(e) ("manifestly made public") is not a safe assumption for every post (LQ-9). Safeguards:
  - the codebook forbids coding any Art. 9 or FADP Art. 5(c) attribute (**implemented**, CB-11: codebook v0.1.0 §12);
  - LLM output schemas are closed (`additionalProperties: false`, `src/pigtail/llm/types.py`), so the model can only return codebook fields (**implemented**).
- **Children:** GitHub and Bluesky users may be under 18, and pigtail cannot tell. Art. 6(1)(f) gives children extra weight. Mitigation: no individual-level evaluation or outputs, pseudonymisation, and public outputs aggregated.

### 4.2 Reasonable expectations
In favour:
- GitHub public events have been archived and analysed publicly since GH Archive began, and GitHub's own AUP §7 contemplates research on public data (TM-01).
- Hacker News and Bluesky posts are public by design. Bluesky's ToS tells users that other services on the AT Protocol get deletion notices (TM-06), which signals that third-party indexing is expected.
- Research on OSS popularity is an established academic field (docs/research/literature.md).

Against:
- EDPB Guidelines 1/2024 (Example 6) warn that even data "made public by the data subjects themselves" does not mean people can reasonably expect any third-party processing.
- A *commercial* operator building spread graphs of who amplified a competitor's launch is less expected than academic aggregate statistics.
- LLM processing of posts is not something every poster anticipates.
- **Stargazer identities (A1b, LQ-29).** On 2026-06-30 GitHub limited the stargazer lists to admins and collaborators because they had "increasingly been misused to collect user data for spam activities" (https://github.blog/changelog/2026-06-30-upcoming-access-restrictions-to-public-api-endpoints-and-ui-views/, accessed 2026-09-25), and on 2026-09-04 it published a star-history endpoint that returns counts without identities (https://github.blog/changelog/2026-09-04-new-api-endpoint-provides-privacy-safe-star-history-data/, accessed 2026-09-25; TM-33). A GitHub user can now reasonably expect that third parties do not compile who starred a repo. Reading the same fact from `WatchEvent.actor` is technically public and documented, but it goes against that expectation, and it could be read as working around the restriction (ToS §H, AUP; TM-33). In its favour: pigtail's purpose is the opposite of the harm GitHub named. It looks for fake-star and spam campaigns, which protects the same users and the platform's integrity. It uses the aggregate endpoint for all counts, and identities only for filtering, pseudonymised, for at most 30 days and for a small set of tracked repos, and never contacts, lists or exports stargazers.

Conclusion: aggregate research and project-level forensics sit within reasonable expectations. Account-level spread graphs, LLM coding and stargazer identities from events (A1b) sit at the edge. They are acceptable only with the safeguards in §4.4 and a clear public notice ([privacy-notice.md](privacy-notice.md)).

### 4.3 Impact on data subjects
- **No decisions are taken about individuals.** Outputs are about projects and mechanisms. No automated decision with legal or similarly significant effect (Art. 22) is made.
- **Possible adverse impacts:**
  - re-identification of a pseudonymised account from quoted text;
  - being named or targeted as an "influencer" in a launch plan (outreach or spam);
  - loss of control over deleted posts;
  - disclosure through a leak from the public repo;
  - use of the data by the LLM provider.
- **Maintainers:** a project classed as `plateau` (matched loser) or `short_lived` is a statement about a project. For a solo maintainer it can reflect on the person (PRD §8.2). That is why public outputs don't name matched losers without consent (LQ-7), and the classification is labelled as a statistical category, not a judgment.
- **Scale:** A1 covers every public GitHub event, so millions of people. A1b covers people who star tracked repos: unknown until M1-T24 runs; on one measured day about 1,078 repos gained ≥ 30 stars (detection-replan §7.4), so polling the whole pre-threshold set would see tens of thousands of star events a day (arithmetic lower bound ≈ 32,000). A2–A4 cover people who mention roughly 300 Tier 2 repos (G2).

### 4.4 Safeguards that tip the balance
Status: **I** = implemented and on `main` (file cited), **P** = planned (backlog id in [dpia.md](dpia.md) §9).

| # | Safeguard | Status |
|---|---|---|
| S1 | Keyed HMAC-SHA256 pseudonyms, per platform namespace, at parse. The key is stored separately. | Partly I: the pseudonymiser `src/pigtail/pseudonymize.py` is on `main`. Pseudonymisation at ingest (`src/pigtail/connectors/base.py` `records()`, namespace `github` in `gharchive.py`) is **I**. |
| S2 | Direct identifiers (e-mail, phone, profile URLs, DIDs, @mentions) are stripped before **every** LLM call, on both backends. | I: `src/pigtail/llm/client.py` (`complete()`), `src/pigtail/pseudonymize.py` (`redact_identifiers()`), ADR-006. Profile URLs (GitHub, Bluesky, HN) and `did:plc`/`did:web` identifiers are **I** (CB-06 partly; `tests/unit/test_redaction_cb06.py`). Gaps: names, gist and avatar URLs, and bare handles in author fields are not stripped (CB-06, P). |
| S3 | Bot logins are dropped. Per-actor features are never persisted. | I: `src/pigtail/connectors/gharchive.py`, `src/pigtail/capture/velocity.py` |
| S4 | No public release of person-level data, raw snapshots or identifying spread graphs. The app is private by default. | I (policy): PRD §4, R13.3. The CI private-data scan is I (`scripts/private_data_scan.py`). App authentication is P (UI not built). |
| S5 | Raw person-level data is retained 24 months, then aggregated or deleted. | I: the fields (`evidence.retention_class` in `migrations/0001_capture_v0.sql`, `src/pigtail/capture/models.py`) and the purge job (CB-01: `pigtail retention purge`, `src/pigtail/privacy/retention.py`, tombstones in `deletion_log`). O: the operator schedules it daily. |
| S6 | Deletion sync where platforms require it, and as a courtesy for HN `deleted`. | P. The field `evidence.deletion_state` is I (`migrations/0001_capture_v0.sql`). The sync job is CB-02. |
| S7 | Right to object honoured unconditionally (suppression list), without asking for "grounds". | I: CB-08, CB-13 (`pigtail privacy optout add`, `pigtail privacy request erasure`; `src/pigtail/privacy/`). Rectification is not built (ADR-030.3). |
| S8 | Public privacy notice (Art. 14 / FADP Art. 19) with an opt-out route. | P: [privacy-notice.md](privacy-notice.md) drafted; publication is CB-12. |
| S9 | No cross-platform identity resolution. At ingest, the pseudonym namespace per platform makes linking harder by construction. **Limitation:** the LLM-path redactor (`build_client` in `src/pigtail/llm/client.py` → `Pseudonymizer.strip_identifiers`) falls back to a single `generic` namespace for @mentions when the caller passes no source namespace. In that case the same handle mentioned on two platforms gets the same pseudonym in LLM inputs and cached outputs, and that pseudonym differs from the ingest one. | Ingest namespaces: I. LLM-path redaction: **I** for per-source namespaces (CB-06): `LLMClient.complete(namespace=…)` pseudonymizes @mentions in the caller's source namespace, and profile URLs and DIDs always use their platform's namespace. The default is still `generic` when a caller passes none, so callers must pass the source namespace. The codebook rule is I (CB-11, codebook v0.1.0 §12). |
| S10 | No Art. 9 attributes are coded. Output schemas are closed. | I (closed schemas) / I (codebook rule, CB-11: codebook v0.1.0 §12) |
| S11 | LLM provider: `api` mode under the Commercial Terms and DPA (processor, no training). `subscription` mode requires the training opt-out (H1) and redaction first. | I (redaction, routing) / operator action (H1) / open legal question (LQ-1, LQ-2) |
| S12 | Connectors run only when their terms clearance is met. Gap sources cannot be enabled. | I: `ConnectorGapError` in `src/pigtail/connectors/base.py` |
| S13 | Explicit refusals are honoured (FADP Art. 30(2)(b); e.g. a Bluesky user-intents opt-out once adopted, TM-06). | I: CB-13 (opt-out list of pseudonyms and repo ids, checked at ingest in `src/pigtail/connectors/base.py`, purge of existing data). Consuming a Bluesky user-intents signal is P (no Bluesky connector yet). |
| S14 | Reach is stored in bands, not exact counts, for person accounts. | P: CB-10 |
| S15 | Growth-engine and planner outputs never tell users to contact a named private individual. Individuals may be named only under the public-figure rule, and only in the private UI. | P: CB-14, LQ-7 |
| S16 | **Stargazer-event limits (A1b; TM-33 conditions, LQ-29 default):** per-repo events only for repos with an open case, tracked repos or repos above the pre-threshold; polling no faster than `X-Poll-Interval` (15–60 min, ETag); one operator token; `actor` pseudonymised at ingest; only `WatchEvent`s parsed and the raw response dropped after parse; person-level star rows kept ≤ 30 days, then only aggregates; identities used only for bot and lockstep flags; no stargazer list is ever built, stored, shown or exported, and no cross-repo stargazer graph beyond the tracked set; each case records `bot_filter_basis` and `coverage_ratio`. | P: M1-T24 (connector), CB-22 (30-day retention class and purge), CB-23 (parse minimisation and no-list guard). S1 (pseudonymiser) is I and would be reused. |

### 4.5 Balancing result
With S1–S15 in place, we judge that the interests I1–I5 are **not overridden** for the activities A1, A2, A3, A5, A6 and A7.

For **A1b (stargazer events)** the balance is **narrow**, because GitHub's 2026 restriction lowers what users expect (§4.2). It holds only if S16 is implemented in full, and it is subject to LQ-29. If the lawyer answers LQ-29 against it, A1b stops, the filtered star series stay `unknown` (ADR-032.3), and nothing is imputed.

For **A4 (spread graphs of accounts)** the balance is **narrow**. It holds only if:
- (i) graphs are limited to Tier 2 and Tier 3 cases;
- (ii) nodes stay pseudonymised in storage and are shown with handles only in the private UI, to authorised operators;
- (iii) S7 (objection) and S15 (no outreach targeting of private individuals) are implemented;
- (iv) the lawyer confirms the profiling analysis (LQ-8).

**Until the ADR-022 controls exist, the conservative default is:**
- A1 may continue **running locally in development only; there is no production capture (ADR-022)**. Production capture on the host waits for the ADR-022 production controls (CB-01, CB-03, CB-04, CB-09, CB-12, CB-16, CB-17, CB-18). CB-01 is implemented; CB-03, CB-04 and CB-18 are partly implemented (ADR-030; retention-policy §1, §2); CB-09, CB-12, CB-16 and CB-17 are open. The M1 safeguards (S1 at ingest, S3, S12) are merged to `main`. H2 "does not block collection under the documented safeguards" (WORK_ORDER §5).
- **No person-level source beyond GH Archive (Bluesky, HN, V2EX, Discord) is enabled** until every pre-condition in **ADR-022** exists: CB-01 (retention purge, S5), CB-02 (deletion sync, S6), CB-03 (encryption at rest), CB-06 (identifier redaction before LLM calls, S2 extensions), CB-08 (data-subject request tooling, S7), CB-12 (published notice, S8) and CB-13 (honouring refusals, S13). ADR-022 is the single authoritative list. Status (ADR-030): CB-01, CB-08 and CB-13 implemented; CB-03 and CB-06 partly implemented; CB-02 and CB-12 open.
- **A1b (per-repo stargazer events) is not enabled** until S16 exists (M1-T24, CB-22, CB-23). The per-repo Events API is a person-level GitHub source other than GH Archive, so on a conservative reading the ADR-022 person-level hold applies to it too (every pre-condition above, including CB-02); this reading and the two new controls are proposed to the orchestrator as an ADR-022 amendment rather than assumed. LQ-29 does not block it under the TM-33 defaults, but its answer can.
- **No spread graph (A4) is built** until LQ-8 is answered or CB-14 is implemented.
- **Nothing is published (A7)** until H2 and H4.

---

## 5. Swiss FADP reasoning

1. **Principles (Art. 6).** The processing is lawful, in good faith and proportionate (§3). The purpose must be "specific" and one "that the data subject can recognise" (Art. 6(3)). Recognisability depends on the public notice (S8) and on staying within the stated purposes (§2). Data must be destroyed or anonymised "as soon as they are no longer required" (Art. 6(4)), which is the 24-month rule (S5).
2. **Is there a breach of personality rights?** Art. 30(3): "In general no breach of personality rights arises if the data subject makes the personal data generally accessible and has not explicitly prohibited any processing." Most of the data is generally accessible. Two points follow:
   - (a) a data subject's **explicit prohibition** (for example in a profile, a Bluesky user intent, or an objection sent to us) takes processing of that person outside Art. 30(3), and processing "contrary to the express wishes" is a breach under Art. 30(2)(b). This is why S7 and S13 exist.
   - (b) Art. 30(3) does not cover processing that breaches the principles (Art. 30(2)(a)).
3. **Justification (Art. 31).** Where a breach is arguable (spread graphs, LLM disclosure), we rely on an overriding private interest (Art. 31(1)). In particular we rely on Art. 31(2)(e), processing "for purposes not related to specific persons, in particular for research, planning or statistics", which comes with three conditions:
   - "anonymises the data as soon as the purpose of processing permits": the 24-month rule plus aggregation;
   - sensitive data disclosed to third parties only in non-identifiable form: we do not seek sensitive data, and the LLM disclosure question is LQ-2;
   - "results are published in such a manner that data subjects are not identifiable": PRD §4 and S4.

   Art. 31(2)(e) does not fit case forensics that are **related to specific persons**, meaning the account nodes in a spread graph. For those we rely on the general overriding-interest balance in §4, plus Art. 31(2)(f) where the node is "a public figure" and the data "relate to that person's public activities" (LQ-7).
4. **High-risk profiling** (Art. 5(g)) requires explicit consent from private controllers (Art. 6(7)(b)). pigtail does not combine data "that allow an assessment to be made of essential aspects of the personality of a natural person". We still ask the lawyer to confirm this for spread graphs (LQ-8).
5. **Foreign controllers.** A controller domiciled outside Switzerland may have to appoint a Swiss representative (Art. 14) when processing is regular, large-scale, high-risk and linked to monitoring the behaviour of people in Switzerland. The GDPR equivalent is Art. 27 for non-EU controllers. See LQ-13.

---

## 6. Conditions attached to this LIA

This LIA supports Art. 6(1)(f) GDPR and Art. 31(1) FADP **only while all of the following hold**:
1. The connector's terms memo decision is CLEARED or CLEARED-WITH-CONDITIONS and its conditions are implemented.
2. S1, S2, S3, S4 and S12 are active. S2 and S4 are on `main` today; S1 at ingest, S3 and S12 are I and must be merged before any capture beyond local development.
3. For any person-level source other than GH Archive: every ADR-022 pre-condition (CB-01, CB-02, CB-03, CB-06, CB-08, CB-12, CB-13) is implemented, and the notice published, first.
4. For spread graphs: condition 3 plus S14 and S15.
4a. For stargazer events from per-repo polling (A1b): condition 3 (conservative reading, pending the proposed ADR-022 amendment), plus S16 in full, and no contrary answer to LQ-29.
5. Any change of purpose (training, marketing, sale, identity resolution, public person-level outputs) triggers a new LIA.
6. Review this LIA at every milestone that adds a source, at H2, and at least every 12 months.

## 7. Outcome

| Activity | Outcome |
|---|---|
| A1 velocity scan | **Proceed locally in development only.** Production capture on the host only after the ADR-022 production controls (including the remaining parts of CB-04) exist. The M1 safeguards are merged. |
| A1b stargazer events (per-repo Events, tracked repos) | **Hold** until S16 exists (M1-T24, CB-22, CB-23) and the ADR-022 person-level pre-conditions are met (proposed amendment); proceed then under the TM-33 conditions, pending LQ-29. Star counts from the star-history endpoint (no personal data) **proceed**. |
| OpenDigger mirror (TM-32) | **Not processed.** GAP pending LQ-28; off by default (ADR-032). A new LIA entry is needed before it could be enabled. |
| A2 mention capture (Bluesky, HN, V2EX) | **Proceed only after** every ADR-022 pre-condition exists: CB-01, CB-02, CB-03, CB-06, CB-08, CB-12 and CB-13. |
| A3 LLM coding | **Proceed** in `api` mode with the DPA. In `subscription` mode, proceed only for the owner's own non-commercial use after the training opt-out (H1), pending LQ-1 and LQ-2. |
| A4 spread graphs | **Hold** until LQ-8 is answered or CB-14 is implemented. |
| A5, A6 | **Proceed.** |
| A7 publication | **Blocked** until H2 and H4 (unchanged). |

## Changelog
- 2026-09-25: v0.1 created (M3-T2).
- 2026-09-25 — fixes after verifier M3 round 1: uncommitted M1 controls relabelled "I (M1, pending merge)" (§3, §4.4 S1/S3/S5/S6/S12); §4.5 A1 is local development only (ADR-022); person-level-source pre-conditions aligned with ADR-022 (§4.5, §6, §7); S9 corrected (LLM-path redaction uses one `generic` namespace).
- 2026-09-25 — M1 capture core merged at `ec79762`; "I (M1, pending merge)" labels changed to "I".
- 2026-09-25 — fixes after verifier M3 round 2: CB-04 marked partly implemented (30-day purge + verified re-fetch; drop-after-parse and minimal-parse fallback still planned); stale 'pending merge' conditions removed.
- 2026-09-25 — fixes after verifier M3 round 3: CB-11 marked implemented (codebook v0.1.0 §12); LQ-25 updated for CB-04 partly implemented.
- 2026-09-25 — CB statuses updated after privacy-controls merge (ADR-030): S5, S7, S13 implemented (CB-01, CB-08, CB-13); S2 and S9 updated for CB-06 (profile URLs, DIDs, per-source namespaces; gists, avatar URLs and bare author handles open); §3 retention enforcement implemented; §4.5 status of the ADR-022 lists; stale CB-11 references in S9 and S10 corrected.
- 2026-09-25 — M3-T7, ADR-032: new activity A1b (per-repo `WatchEvent` polling for tracked repos, TM-33) in §1, §3, §4.2 (GitHub's 2026-06-30 stargazer-list restriction, LQ-29), §4.3, §4.4 (S16, planned: M1-T24, CB-22, CB-23), §4.5, §6 (condition 4a) and §7; A5 notes that star counts come from star-history (no personal data); OpenDigger (TM-32) recorded as not processed (GAP, off), with the one-off M1-T18 research measurement disclosed; the stargazers API recorded as no longer used. ADR-022 lists unchanged; an amendment is proposed to the orchestrator.

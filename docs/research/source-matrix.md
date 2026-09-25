# Source matrix: pigtail Phase 1 source audit (M2-T2)

**Status:** draft for verifier spot-check (M2 acceptance) and for legal review (gate H2).
**Authors:** researcher and compliance agents. They are not lawyers, and every conclusion here is a conservative engineering default until H2 is answered.
**Access date for all URLs:** 2026-09-25, unless a row says otherwise.
**Companion document:** `docs/compliance/terms-memos.md`. Each source's terms memo is labelled `TM-xx`, and that label goes in the `evidence.terms_basis` field (PRD §7).

## Assumptions and clearance meanings

These assumptions follow the compliance agent's rules and PRD §10:

- Operators use pigtail commercially. Pigtail itself is MIT open source, and any operator may be a company.
- Pigtail stores raw API JSON snapshots in private storage (R1.2, R1.4).
- User handles are pseudonymised when data is ingested.
- Raw data about people is kept for at most 24 months.
- Deletion sync runs wherever a platform requires it (R1.5).
- An LLM codes the stored text. It uses inference only and never trains a model (F7, F15).

The clearance levels mean:

- **CLEARED.** The terms clearly allow the planned collection, storage and processing.
- **CLEARED-WITH-CONDITIONS.** The terms allow it only if the listed conditions are enforced in the connector. Each condition becomes a connector acceptance criterion under R2.1.
- **GAP.** The terms do not allow the planned use, or it cannot be verified that they do. The source is recorded under R2.3 and must not be scraped around. A permission path is noted where one exists.

"Unverified" means we could not confirm the fact on an official page on 2026-09-25. We do not guess numbers.

---

## 1. Summary table

| # | Source | Coverage / depth | Granularity | Cost | Rate limit | Auth | Commercial | Storage | Deletion duty | AI/ML limits | Stability risk | Decision |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | GH Archive + BigQuery | Public GitHub events since 2011-02-12 | Hourly files; BigQuery day, month and year tables | BigQuery $6.25/TiB, first 1 TiB/month free | BigQuery quotas | GCP project | No dataset licence stated; maintainer points to GitHub ToS (issue #137); GitHub AUP applies | Not addressed | None stated | GitHub ToS D.9 covers *training* only | **High**: star capture reportedly collapsed in 2025–26 (likely systematic crawler loss) | CLEARED-WITH-CONDITIONS |
| 2 | GitHub REST/GraphQL | All public repos, stargazers with `starred_at` | Per event and per star | Free | REST 5,000/h (token); GraphQL 5,000 pts/h | PAT / App | Allowed within the AUP (no spam, no selling personal info) | Not restricted in §H | None stated | Training only (D.9) | Low–medium | CLEARED-WITH-CONDITIONS |
| 3 | HN Algolia API | HN from item 1 (2006-10-09), observed | Per item | Free | 10,000 req/h per IP | None | YC ToU is ambiguous | Not addressed | None stated | None found | Medium (no SLA) | CLEARED-WITH-CONDITIONS |
| 4 | HN Firebase API | All items since 2006-10-09; top/new/best lists | Per item, live lists | Free | "currently no rate limit" | None | YC ToU is ambiguous | Not addressed | None; `deleted` flag exists | None found | Medium (v0, may break) | CLEARED-WITH-CONDITIONS |
| 5 | Reddit Data API | Posts, comments, users | Per item | Commercial fees at Reddit's discretion; $0.24/1K unverified | 100 QPM per OAuth client | OAuth + **prior approval** | **Needs a separate written agreement** | Only for the approved use case | **Yes**: delete removed content; 48 h recommended | No training; no sensitive inference; no sharing with third parties | Very high | **GAP** |
| 6 | Bluesky / AT Protocol | Full public network; Jetstream live and replay | Per record | Free (replay is metered; price unverified) | PDS 3,000 per 5 min per IP; limits for the Bluesky app endpoints not published | Search needs auth in practice | No ban found | Allowed, with security measures | **Yes**: honour deletes | None in the guidelines (user-intents proposal pending) | Medium–high | CLEARED-WITH-CONDITIONS |
| 7 | PyPI BigQuery downloads | Every download; complete from 2018-07-26 | Per download row | BigQuery $6.25/TiB, 1 TiB/month free | BigQuery quotas | GCP project | Allowed under CC BY 4.0 (attribution) | Allowed | None stated for `file_downloads` | None found | Low–medium | CLEARED-WITH-CONDITIONS |
| 8 | npm downloads API | Since 2015-01-10 | Daily; at most 18 months per query | Free | Not documented; ToS: 5M req/month is "not remotely reasonable" | None | **Explicitly allowed** | Replication via public APIs allowed | None | None found | Medium | CLEARED |
| 9 | crates.io | Daily per-version downloads since Nov 2014 (archive CSV) | Daily per version | Free | API: 1 req/s; index and static.crates.io: no rate limits stated | None; User-Agent with contact required | Not explicit; bulk abuse banned | Dumps published for download | None stated | None found | Low | CLEARED-WITH-CONDITIONS |
| 10 | Homebrew analytics | Rolling 30/90/365-day installs only | Aggregate per formula | Free | Not documented | None | No data licence stated | Not addressed | None | None found | Medium | CLEARED-WITH-CONDITIONS |
| 11 | Docker Hub | Lifetime `pull_count` only | Cumulative counter | Free | Header observed at 180 req/min (not documented) | Optional | Allowed through documented APIs within limits | No mirroring for an unauthorised commercial service | None | No training of *competing* models | Medium–high | CLEARED-WITH-CONDITIONS |
| 12 | deps.dev | 7 ecosystems; dependents (v3alpha); BigQuery snapshots | Per package/version | Free (BigQuery at standard rates) | Unverified | None | CC-BY 4.0 data | Google APIs ToS limits databasing (ambiguous) | None | None found | Medium | CLEARED-WITH-CONDITIONS |
| 13 | Wayback CDX + Save Page Now | The web since 1996 (depth per URL varies) | Per capture | Free | SPN: 7 captures/min and 30k/day (authenticated) | SPN: S3 keys | ToU (2014-12-31 text): **"scholarship and research purposes only"** (currency unverified) | "not to collect or store personal data" | Removals are at IA's discretion | None found | Medium–high | CLEARED-WITH-CONDITIONS |
| 14 | YouTube Data API | Public videos | Per video | Free within quota | 10,000 units/day + 100 search calls/day | API key | Allowed in principle | **At most 30 days**; no derived metrics | Yes (30-day refresh or delete) | Derived-data ban | Medium | **GAP** |
| 15 | X API | Full archive since 2006 | Per post | Pay-per-use: $0.005 per post read | Search: 300–450 per 15 min | Bearer / OAuth | **Beyond hobbyist or prototyping, Enterprise is required** | Must keep stored content in sync with X | **Yes, within 24 h of request** | No foundation-model training | High | **GAP** |
| 16 | Product Hunt API | Posts (upcoming launches unverified) | Per post | Free | 6,250 complexity pts per 15 min | OAuth / dev token | **"must not be used for commercial purposes"** | Site ToS bans storing a "significant portion" | Not found | Not found | Medium | **GAP** |
| 17 | Lobste.rs | Since 2012; `.json` endpoints (undocumented) | Per story | Free | Code: 4/s, 30/min, 400/h per IP | None | No ToS; operator: "commercial service? … email me" | Not addressed | Not addressed | robots: `ai-input=no, ai-train=no` | Medium | **GAP** |
| 18 | dev.to (Forem API) | Articles, current counters | Per article | Free | Unverified (code default: 3/s, 30/min) | API key (optional) | **ToS: "non-commercial transitory viewing only"** | "must destroy any downloaded materials" | Yes (destroy) | Not found | Medium | **GAP** |
| 19 | V2EX | Topics and replies via API 2.0 | Per topic | Free | 600 req/h per IP | Personal access token | No ToS found | Not addressed | Not addressed | Not addressed | Medium | CLEARED-WITH-CONDITIONS (off by default until H2) |
| 20 | Juejin | No official API | — | — | — | — | ToS bans robots and commercial reuse | — | — | Bans competing models | High | **GAP** |
| 21 | Zhihu | Open platform exists; terms unverified | — | Unverified | Unverified | Access secret | ToS bans automated collection | — | — | **Explicit ban on LLM R&D/training** | High | **GAP** |
| 22 | Bilibili | Open platform covers own-account scopes only | — | Unverified | Unverified | OAuth | ToS bans crawlers without written permission | — | — | — | High | **GAP** |
| 23 | TrustMRR | More than 15,000 startups, Stripe-verified MRR | Per startup | Standard key free; premium price unverified | 10 req/min standard, 60 premium | API key | Internal only; no redistribution | **"archive" banned** | May apply | **"ground … an AI model" banned** | Medium–high | **GAP** |
| 24 | Crunchbase | Funding, firmographics | Per entity | Enterprise licence (price not published) | 200 calls/min | Licence key | Internal research only | Must stay expungeable | Destroy on termination | No training | Low (API), high (legal) | **GAP** (bring-your-own-licence path) |
| 25 | YC directory | Batch companies | Per company | — | — | No official API | ToU bans scraping and data mining | — | — | — | High | **GAP** |
| 26 | GitHub dependency graph | Dependencies: manifests (GraphQL) and SBOM (REST). **Dependents: no REST or GraphQL endpoint** | Per repo | Free | GitHub REST/GraphQL limits | PAT / App | Dependencies: as TM-02. Dependents web page: robots.txt `Disallow: /*/*/network` | As TM-02 | None stated | As TM-02 | Medium (SBOM export endpoint closes 2026-11-13) | Dependencies: CLEARED-WITH-CONDITIONS. Dependents page: **GAP** (use deps.dev, TM-12) |
| 27 | Discord invite API | Server-level `approximate_member_count` and `approximate_presence_count` for a public invite code | Point-in-time counter | Free | Not documented for this endpoint | None observed; registered app recommended | Developer Policy §18 bars commercialising "API Data"; §20 bars mining or scraping | Only for the app's stated functionality | **Yes**: on Discord's or the user's request, and on termination | No ML training on message content (Policy §21) | Medium | CLEARED-WITH-CONDITIONS (off by default until H2 Q12) |
| 28 | Slack (invite links) | No documented public endpoint returns a member count for a shared invite link | — | — | — | Workspace token only | — | — | — | — | — | **GAP** (metric `unknown`) |
| 29 | Careers pages (project sites) | Per site | Per page capture | Free | Self-imposed | None | Per-site terms | Per-site terms | Per site | Per site | Per site | CLEARED-WITH-CONDITIONS (per-site check; Wayback first) |
| 30 | HN "Who is hiring?" threads | Monthly threads by the `whoishiring` account since 2011 (observed) | Per comment | Free | As TM-03/TM-04 | None | As TM-03/TM-04 | As TM-03/TM-04 | None; `deleted` flag | None found | Medium | CLEARED-WITH-CONDITIONS (as HN, pending Q4) |
| 31 | Press (funding announcements) | Whatever the operator enters | Per announcement | — | — | — | Operator-entered citation only | URL, date and a short quote only | — | — | Low | CLEARED-WITH-CONDITIONS (manual, `self_reported`) |

---

## 2. Per-source sections

### 2.1 GH Archive (+ BigQuery `githubarchive`), TM-01

**Coverage and granularity**
- Public GitHub events from 2011-02-12.
- Events up to 2014-12-31 come from the deprecated Timeline API; events from 2015-01-01 come from the Events API.
- Data is published as hourly `.json.gz` files, and as BigQuery tables per year, month and day.
- Source: https://www.gharchive.org/ (2026-09-25).

**Stars**
- A `WatchEvent` action "Can only be `started`", so un-stars are never recorded (https://docs.github.com/en/rest/using-the-rest-api/github-event-types, 2026-09-25).
- Net stars from GH Archive therefore over-state growth.

**Data-quality alarm (high stability risk).** None of the following issues has a maintainer reply (checked with `gh issue view` and the GitHub API on 2026-09-25). They are community reports, not official statements.
- Issue #310 (2025-07-16, open) reports that event volume is "drastically lower" after 2025-05-23 (https://github.com/igrigorik/gharchive.org/issues/310).
  - A comment on 2025-10-11 (user andrewortman) gives a likely root cause: "The crawler.rb script only fetches the first page of events", while the Events API serves 3 pages that "change together". Events that appear only on pages 2–3 are never captured.
  - If this is right, the loss is **systematic, not random**. It depends on how busy the public event stream is, so capture rates will vary by hour, event type and period. A single uniform correction factor is not valid.
  - An unmerged fix, PR #317 (opened 2026-02-19, still open), says an earlier commit "changed PAGE_LIMIT from 500 to 100 … but only fetched a single page", and adds multi-page fetching (https://github.com/igrigorik/gharchive.org/pull/317).
- Issue #312 (opened 2025-10-12, closed 2025-10-15 by its reporter) reports a further ~100× drop from 2025-10-09. The BigQuery day table fell from 2,769,429 rows (2025-10-08) to 18,906 rows (2025-10-09). Comments show volume recovering from 2025-10-14 (https://github.com/igrigorik/gharchive.org/issues/312). Treat 2025-10-09 to about 2025-10-14 as near-empty.
- Issue #320 (2026-05-14, open) reports that star capture fell to about 60–70% in June 2025 and to about 10–20% from February 2026 (https://github.com/igrigorik/gharchive.org/issues/320). **It analyses only 2 repositories** (`google/osv.dev` and `facebook/stylex`), comparing stargazers-API timestamps with `WatchEvent` rows. Its figures are indicative, not a population estimate.
- Issue #323 (2026-09-09) announces a compatible community mirror, OpenDigger, running since 2026-09-06 (https://github.com/igrigorik/gharchive.org/issues/323). The mirror's terms are unverified.

**Upstream changes**
- GitHub shortened the Events API window to 30 days (https://github.blog/changelog/2024-11-08-upcoming-changes-to-data-retention-for-events-api-atom-feed-timeline-and-dashboard-feed-features/).
- GitHub trimmed push and PR payloads from 2025-10-07 (https://github.blog/changelog/2025-08-08-upcoming-changes-to-github-events-api-payloads/).
- A specific 2015–2016 gap: unverified.

**Cost:** BigQuery on-demand costs $6.25 per TiB, and the first 1 TiB per month is free (https://cloud.google.com/bigquery/pricing, 2026-09-25).

**Auth:** a Google Cloud project. Hourly files need no auth.

**Terms**
- The GH Archive repository is MIT for code and CC-BY-4.0 for site content. The dataset itself has **no stated licence**.
- Issue #137 ("What's the license of GitHub Archive Data?", 2016-03-22) was answered by the maintainer (igrigorik, 2016-04-04) with an "Official answer from GitHub" and then closed (https://github.com/igrigorik/gharchive.org/issues/137). The answer says:
  - use of API data is governed by the GitHub Terms of Service, in particular the spam ban;
  - content pulled from repositories "may be subject to the terms of the repo's project";
  - republishing personal information is subject to GitHub's privacy policy.
  - It states no licence for the dataset.
- The repository warns that the data "may be subject to third party rights" (https://github.com/igrigorik/gharchive.org).
- The governing rules on reuse are therefore GitHub's Acceptable Use Policies §7:
  - research on "public, non-personal information" is allowed, provided publications are open access;
  - no spamming and no selling personal information;
  - use must comply with the GitHub Privacy Statement.
  - Source: https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies.
- ToS D.9 "Access Reciprocity" (GitHub Terms of Service, "Effective date: April 27, 2026", https://docs.github.com/en/site-policy/github-terms/github-terms-of-service) covers automated access "for the purpose of developing or training any commercially available artificial intelligence model". Pigtail runs inference only, so D.9 is not triggered on our reading. That reading is unverified; see H2 question Q3.

**Decision: CLEARED-WITH-CONDITIONS**
- (a) Pseudonymise `actor.login` and `actor.id` at ingest. Keep no emails from push payloads.
- (b) Never sell, export or publish person-level data (AUP §7; PRD non-goals).
- (c) Keep public outputs aggregate and open access (R13.3).
- (d) **Do not treat GH Archive star counts as ground truth after 2025-05.** Cross-check with the GitHub stargazers API (source 2), and store a `coverage_ratio` per case. The loss is likely systematic (#310), so do not apply one global correction factor. Flag 2025-10-09 to 2025-10-14 as a near-empty window (#312).
- (e) Hard stops on BigQuery budget (§10 cost control).

### 2.2 GitHub REST and GraphQL APIs, TM-02

**Rate limits**
- REST: 60 requests per hour unauthenticated, 5,000 per hour with a PAT or OAuth token, and 5,000 to 12,500 per hour for a GitHub App installation.
- REST secondary limits: 100 concurrent requests and 900 points per minute.
- Source: https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api (2026-09-25).
- GraphQL: 5,000 points per hour, 2,000 points per minute secondary, and 1–100 nodes per connection.
- Source: https://docs.github.com/en/graphql/overview/rate-limits-and-query-limits-for-the-graphql-api.

**Stargazers**
- The `application/vnd.github.star+json` media type returns `starred_at` timestamps (https://docs.github.com/en/rest/activity/starring).
- A cap of 400 pages (40,000 stars) is widely reported but is **not in the official docs**, so it is unverified. The connector must detect truncation.

**Terms**
- ToS §H: abuse can lead to suspension; "You may not share API tokens to exceed GitHub's rate limitations"; no downloading of data for spamming or for selling personal information (https://docs.github.com/en/site-policy/github-terms/github-terms-of-service).
- §H states no caching limit and no deletion duty; this is unverified beyond what the section says.

**Stability:** low to medium.

**Decision: CLEARED-WITH-CONDITIONS**
- The operator's own token only. Tokens are never pooled or shared.
- Respect the primary and secondary limits.
- The same pseudonymisation and no-sale conditions as TM-01.

### 2.3 Hacker News: Algolia Search API, TM-03

**Endpoints:** `search`, `search_by_date`, `items/:id` and `users/:username`, with tags such as `show_hn` and `front_page` (https://hn.algolia.com/api, 2026-09-25).

**Rate limit and auth:** "10,000 per hour" per IP. No auth.

**Depth and pagination**
- Items go back to item 1 (2006-10-09). The research agent observed this on 2026-09-25: `/api/v1/items/1` returns 2006-10-09T18:21:51Z, and a `search` filtered to `created_at_i` before 2006-10-10 returns items 1 and 2. The API page states no start date.
- Search hits carry `points`, `num_comments`, `created_at` and tags such as `front_page`, but no rank or position field (observed). Historical front-page *rank* therefore cannot be rebuilt from this API. This is our inference from the returned fields, not a documented statement.
- The research agent observed that results stop after about 1,000 hits per query. That cap is not documented, so the backfill must window by `created_at_i`.

**Terms**
- The API page has none.
- The content falls under the Y Combinator Terms of Use (https://www.ycombinator.com/legal, "Last Updated September 2026"). They forbid commercial reproduction and "data mining, robots, scraping". Both bans are qualified by "Unless otherwise expressly authorized herein or in the Site".
- Whether a public API counts as "expressly authorized" is a question for H2 (Q4).

**Stability:** free service with no SLA (unverified).

**Decision: CLEARED-WITH-CONDITIONS**
- Use the API only. Never scrape news.ycombinator.com HTML.
- Stay at or below 10,000 requests per hour.
- Snapshots stay private, and there is no public republication of comment text.
- Pseudonymise usernames.
- Use by commercial operators depends on the H2 answer to Q4.

### 2.4 Hacker News: official Firebase API, TM-04

**Coverage**
- All items since item 1 (2006-10-09).
- `topstories` and `newstories` return up to 500 items; `ask`, `show` and `job` stories up to 200; `/v0/updates` lists recent changes.
- Items carry `deleted` and `dead` flags.
- The API says: "There is currently no rate limit."
- Source: https://github.com/HackerNews/API (2026-09-25).
- This API is the basis for "own rank polling" of front-page minutes (§8.1).

**Terms**
- The README repository is MIT. It is unverified whether that licence covers the content.
- The YC ToU applies as in TM-03.
- The website's robots.txt sets `Crawl-delay: 30` (https://news.ycombinator.com/robots.txt). This applies to the website, not to the API.

**Stability:** v0. The API warns that "changes won't always be backward compatible".

**Decision: CLEARED-WITH-CONDITIONS**
- Same conditions as TM-03.
- Poll at a modest fixed rate: at most 1 `topstories` poll per minute plus item fetches. This is a self-imposed courtesy limit.
- Propagate the `deleted` flag into `deletion_state` (R1.5), as a courtesy even though no duty was found.

### 2.5 Reddit Data API, TM-05 (priority check)

**Pages read (2026-09-25)**
- Data API Terms, revised 2026-07-20: https://redditinc.com/policies/data-api-terms
- Developer Terms, revised 2026-03-24: https://redditinc.com/policies/developer-terms
- Data API Wiki: https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki
- Responsible Builder Policy (RBP): https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy
- "Developer Platform & Accessing Reddit Data": https://support.reddithelp.com/hc/en-us/articles/14945211791892-Developer-Platform-Accessing-Reddit-Data

**Access and approval**
- Registered OAuth is mandatory, with 100 queries per minute per client, averaged over 10 minutes (Wiki).
- Prior approval is required: "You must request access and get explicit approval before accessing any Reddit data through our API" (RBP).
- Reddit may require App Review at any time (Developer Terms §3.1).

**Commercial use and cost**
- Commercial use needs a separate agreement (Data API Terms §3.1).
- Developer Terms §4.1 bars access "by or on behalf of a business or as part of a service or product that is monetized".
- The RBP requires "explicit written approval".
- Fees are at Reddit's discretion. The widely cited **$0.24 per 1,000 calls is unverified**; it appears on no official page we could read.

**AI/ML use**
- No model training without permission (Developer Terms §4.2).
- No inference of sensitive characteristics (RBP).

**Sharing with third parties:** "You will not share Reddit Services and Data with any third party" (Developer Terms §7.2). This conflicts with sending snapshot text to an external LLM provider in `api` mode, and possibly in `subscription` mode.

**Retention and deletion**
- Keep data only for the approved use case (Data API Terms §3.2; Developer Terms §7.3).
- Deleted content must be removed. The Wiki "strongly recommend[s]" deleting stored data within 48 hours.
- Retaining deleted content "even if disassociated, de-identified or anonymized" is a violation (Wiki). Pigtail's pseudonymisation therefore does not cure it, and keeping the hash plus coded facts (R1.5) may itself need Reddit's agreement.

**Research access:** the only authorised research route is Reddit for Researchers, which is "solely for non-commercial purposes" (RBP). Pushshift is limited to moderators (https://support.reddithelp.com/hc/en-us/articles/16470271632404-Pushshift-Access-Request).

**Stability: very high risk**
- Self-service app creation reportedly closed in November 2025 (secondary source: https://replydaddy.com/blog/reddit-api-pre-approval-2025-personal-projects-crackdown). Unverified on reddit.com.
- An August 2026 announcement reportedly restricts all new requests in favour of Devvit (secondary source: https://www.redditapis.com/blogs/reddit-developer-platform-migration-2026). Unverified.

**Decision: GAP (R2.3)**
- Commercial use, sharing with an LLM provider, and retention up to 24 months are each incompatible with the default terms.
- Permission path: an operator with a signed Reddit commercial agreement that expressly covers LLM processing and retention could enable a connector. That connector would need 48-hour deletion sync.
- Until then, the "Reddit reach" metric in §8.1 is `unknown`.
- Reddit links that appear inside other sources (HN, Bluesky) may be *recorded as URLs only*. They may not be fetched.

### 2.6 Bluesky / AT Protocol, TM-06

**Documentation:** the docs moved to bsky.network on 2026-08-13 (https://atproto.com/blog/introducing-bluesky-protocol-services).

**Coverage:** posts, likes, reposts and profiles through the Bluesky app's HTTP endpoints ("Bluesky app endpoints: profiles, posts, threads, feeds, and interactions over HTTP"), plus Jetstream and the Relay firehose (https://bsky.network/docs/protocol-services).

**Search auth**
- The docs say the public endpoints need no auth.
- The lexicon, however, says search "may require authentication" (https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/app/bsky/feed/searchPosts.json).
- In a live test by the research agent, unauthenticated `searchPosts` mostly returned 403. There is an open docs issue on this: https://github.com/bluesky-social/bsky-docs/issues/332.
- Plan on authenticated search with the operator's own app password or OAuth. That authenticated search works was not tested by us, so it is unverified.
- How far back search reaches is unverified.

**Firehose and Jetstream**
- The live firehose and Jetstream need no auth (https://bsky.network/docs/jetstream).
- Replay needs an API key. It is metered in bytes with no published price; the live lookback is 36 hours (https://bsky.network/docs/jetstream-replay).

**Rate limits:** 3,000 requests per 5 minutes per IP at the PDS. The Bluesky app endpoints have "generous" limits with no numbers published (https://bsky.network/docs/rate-limits).

**Terms** (https://bsky.network/docs/developer-guidelines)
- Services must be able to delete content a user asks to have deleted.
- Services must use reasonable security measures.
- No ban on commercial use or AI was found.
- Bluesky's ToS, last updated 2025-08-14, says other services are notified of account deletions (https://bsky.social/about/support/tos).
- The "User Intents" AI opt-out is only a proposal (https://github.com/bluesky-social/proposals/tree/main/0008-user-intents).

**Decision: CLEARED-WITH-CONDITIONS**
- (a) Subscribe to Jetstream delete and account events. Propagate them to `deletion_state` and drop the raw copy within 48 hours. The 48-hour window is our own conservative default.
- (b) Pseudonymise DIDs and handles.
- (c) Encrypt the private snapshot store.
- (d) Watch the User Intents proposal. If it is adopted, honour AI opt-outs before LLM coding.

### 2.7 PyPI downloads via BigQuery, TM-07

**Coverage and granularity**
- Table `bigquery-public-data.pypi.file_downloads`, fed by Linehaul (https://docs.pypi.org/api/bigquery/).
- Complete from 2018-07-26. Rows before that are under-counted by about 10x.
- One row per download. Counts are distorted by caches and mirrors.
- Source: https://packaging.python.org/en/latest/guides/analyzing-pypi-package-downloads/.

**Cost:** BigQuery pricing as in TM-01. The cost of a typical query is unverified.

**Terms**
- "The tables and its pertaining data are licensed under the Creative Commons License." The "Creative Commons License" link points to https://creativecommons.org/licenses/by/4.0/, so the licence is **CC BY 4.0**, and attribution is required (https://docs.pypi.org/api/bigquery/, 2026-09-25).
- The docs say "the rows in this BigQuery table are immutable and are not removed even if a release or project is deleted". That sentence is about the `distribution_metadata` table, not `file_downloads`. We found no deletion or immutability statement for `file_downloads`, so we claim none.

**Alternative:** pypistats.org keeps only 180 days and asks users not to bulk-query it (https://pypistats.org/api/).

**Decision: CLEARED-WITH-CONDITIONS**
- Attribute "PyPI / Linehaul (PSF)".
- Query by partition only, with a BigQuery budget cap.
- Do not bulk-query pypistats.org.

### 2.8 npm downloads API, TM-08

**Data** (https://github.com/npm/registry/blob/main/docs/download-counts.md)
- Daily counts from 2015-01-10.
- At most 18 months per query. The research agent observed that longer ranges are silently truncated, so the connector must chunk.
- Bulk queries take up to 128 packages over 365 days, and do not accept scoped packages.
- No auth.
- Rate limit: undocumented.

**Terms** (https://docs.npmjs.com/policies/open-source-terms)
- Commercial use is explicitly allowed.
- Crawling the website is banned, but "You may replicate data from the Public Registry using the Public APIs".
- Five million requests per month is "not remotely reasonable".

**Decision: CLEARED**
- Operational limits only: under 5 million requests per month (in practice well below), chunked ranges, and a descriptive User-Agent.

### 2.9 crates.io, TM-09

**Policy:** crates.io lists its access methods and says "Please try them in the order below" (https://crates.io/data-access, verified from its source at https://github.com/rust-lang/crates.io/blob/main/svelte/src/routes/data-access/+page.svelte):
1. the crate index (the sparse index, or the legacy Git index on GitHub, where GitHub's AUP applies);
2. crate content from static.crates.io;
3. RSS feeds;
4. database dumps;
5. the crates.io API.

The page says "No rate limits are required" for the sparse index, and "No rate limits apply to static.crates.io at present".

**API rules:** "A maximum of 1 request per second", and a User-Agent that identifies the app, ideally with a contact.

**Downloads**
- The daily dump includes `version_downloads`, but only for the last 90 days.
- Daily history back to November 2014 is at `static.crates.io/archive/version-downloads/` (https://github.com/rust-lang/crates.io/blob/main/crates/crates_io_database_dump/src/readme_for_tarball.md).

**Terms:** "excessive automated bulk activity" is banned (https://crates.io/policies). No licence for the dump data was found, so that is unverified.

**Decision: CLEARED-WITH-CONDITIONS**
- Follow the crates.io order: the index first, then crate content, RSS and the dumps or archive CSVs. Use the API only as a last resort.
- Keep API calls at or below 1 per second, with a User-Agent that includes a contact.
- Drop the dump's `users` table (personal data) at ingest.

### 2.10 Homebrew analytics, TM-10

**Data** (https://formulae.brew.sh/docs/api/)
- Rolling 30, 90 and 365-day install counts only, with no time series.
- Homebrew keeps events for 365 days (https://docs.brew.sh/Analytics).
- The data contains no user identifiers.
- Served from GitHub Pages behind a CDN.
- Rate limits and a data licence: none found (unverified). The code is BSD-2-Clause (https://github.com/Homebrew/formulae.brew.sh).

**Decision: CLEARED-WITH-CONDITIONS**
- Fetch once a day, using the bulk files rather than per-formula calls.
- Aggregate, non-personal data only.
- Attribute Homebrew.
- Build our own daily time series from snapshots, with the coverage window recorded (R17.3).
- The lack of a data licence goes to H2 (Q7).

### 2.11 Docker Hub, TM-11

**Data**
- `pull_count` is a lifetime cumulative total with no history (https://docs.docker.com/reference/api/hub/latest.yaml).
- The research agent observed an `x-ratelimit-limit: 180` per-minute header. This is not documented.
- The abuse limits cover all Hub requests (https://docs.docker.com/docker-hub/usage/).

**Terms** (https://www.docker.com/legal/docker-terms-service/)
- Automated access is permitted "through documented APIs and within published limits".
- No training of competing AI or ML models.
- No mirroring "for an unauthorized commercial service".

**Decision: CLEARED-WITH-CONDITIONS**
- Use the documented v2 API only, at a low daily rate.
- Diff our own snapshots to derive velocity.
- No training.
- No mirroring of Hub content. We store counters only.

### 2.12 deps.dev, TM-12

**Coverage**
- Seven ecosystems (https://docs.deps.dev/api/v3/).
- Dependents only in v3alpha, and only for npm, Cargo, Maven and PyPI (https://docs.deps.dev/api/v3alpha/).
- A BigQuery dataset `deps_dev_v1` with a Snapshots table (https://docs.deps.dev/bigquery/v1/). Its start date and snapshot frequency are unverified.
- Rate limits: unverified.

**Terms**
- The data is "available under a CC-BY 4.0 license".
- API use is subject to the Google APIs Terms, which forbid building databases or keeping permanent copies unless expressly permitted (https://developers.google.com/terms).
- Whether CC-BY counts as that express permission is Q6 for H2.

**Decision: CLEARED-WITH-CONDITIONS**
- Prefer the BigQuery dataset, which is under CC-BY, for history.
- Use the API for point lookups only.
- CC-BY attribution in data cards and reports.

### 2.13 Internet Archive Wayback Machine (CDX + Save Page Now), TM-13

**CDX** (https://github.com/internetarchive/wayback/tree/master/wayback-cdx-server)
- No auth.
- A default maximum of 150,000 results per query.
- Rate limits: unverified.

**Save Page Now 2** (official document dated 2026-07-22: https://docs.google.com/document/d/1Nsv52MvSjbLb2PCpHlat0gkzw0EvtSgpKHu4mk0MnrA)
- S3 keys preferred.
- Authenticated: 7 captures per minute, 30,000 per day and 5 GB per day. Anonymous: 3 per minute and 200 per day.
- The per-URL daily cap is stated inconsistently (5 in one place, 10 in another). Use 5.

**Terms**
- The live page could not be rendered. The text below is from the Terms of Use dated "31 Dec 2014", as saved in archive.org item `05132021` (https://archive.org/download/05132021/Internet%20Archive%20Terms%20of%20Use.mhtml, re-read 2026-09-25). Whether this is still the current text is unverified.
- Access is "for scholarship and research purposes only".
- Users agree "not to collect or store personal data about anyone".
- Use must be non-infringing or fair use.

**Decision: CLEARED-WITH-CONDITIONS**
- (a) Only project-level URLs may be captured or looked up: landing, pricing, docs and careers pages, and READMEs. Never person profile pages.
- (b) Wayback-derived records carry no person-level fields.
- (c) Use SPN only with the operator's own keys, at or below 5 captures per minute (below the published 7) and 5 per URL per day.
- (d) Snapshots are used for research and evidence, not redistribution.
- (e) **Commercial operators: the connector is off by default until H2 answers Q5.** Non-commercial or research deployments may enable it.

### 2.14 YouTube Data API v3 (stretch), TM-14

**Quota**
- The default is 10,000 units per day, plus a separate bucket of 100 `search.list` calls per day. This came from the June 2026 quota change (https://developers.google.com/youtube/v3/getting-started; https://developers.google.com/youtube/v3/revision_history).
- The old "search costs 100 units" figure is outdated.

**Terms** (https://developers.google.com/youtube/terms/developer-policies, updated 2026-09-14)
- Non-authorised data may be stored for "not longer than 30 calendar days", and statistics likewise.
- Clients must not "create new or derived data or metrics". This is relaxed only for audited analytics developers.
- No aggregation "to gain insights into YouTube's usage".
- YouTube may audit clients.

**Decision: GAP**
- Snapshot-or-drop storage, 24-month retention and LLM-derived coding all conflict with these terms.
- Permission path: YouTube's audited analytics-developer status (§L of the policies). Its requirements are unverified.
- Meanwhile, YouTube URLs found in other sources are recorded as links only.

### 2.15 X (Twitter) API (stretch), TM-15

**Pricing** (https://docs.x.com/x-api/getting-started/pricing)
- Self-serve is pay-per-use only since 2026-02-06 (https://devcommunity.x.com/t/announcing-the-launch-of-x-api-pay-per-use-pricing/256476).
- Basic was retired after 2026-06-01, and Pro after 2026-09-01.
- Post read: $0.005. User read: $0.010. Counts: $0.010 per request.
- A cap of 3 million post reads per month.
- Enterprise pricing is not published.

**Search**
- Full-archive search goes back to March 2006, with up to 500 posts per request (https://docs.x.com/x-api/posts/search/introduction).
- Rate limits: `search/all` 1 request per second and 300 per 15 minutes; `search/recent` 450 per 15 minutes per app (https://docs.x.com/x-api/fundamentals/rate-limits).

**Terms**
- Developer Agreement, 2026-04-27 (https://docs.x.com/developer-terms/agreement):
  - Use beyond hobbyist projects, commercial prototyping or initial development requires an Enterprise plan (§III.M).
  - Content must be deleted within 24 hours of a written request (§IV.B).
  - No fine-tuning or training of foundation or frontier models (§III.A.k).
  - Bans on tracking users and surveillance (§XIV.B).
- Developer Policy (https://docs.x.com/developer-terms/policy):
  - Stored content must be kept up to date.
  - Aggregate metrics may not be used for benchmarking or commercial purposes.

**Decision: GAP**
- Commercial operators need Enterprise, whose price is unpublished.
- Pay-per-use would cost about $0.005 × posts read, and its commercial scope is limited to prototyping.
- Case forensics that follow who spread what may count as "tracking X users"; this is Q9 for H2.
- Permission path: an operator's own Enterprise agreement, plus a 24-hour deletion-sync connector.

### 2.16 Product Hunt API v2 (stretch), TM-16

**Access**
- A token is required.
- 6,250 complexity points per 15 minutes (https://api.producthunt.com/v2/docs/rate_limits/headers).
- Whether the live schema exposes upcoming launches is unverified; the published schema is from 2019.

**Terms**
- "The Product Hunt API must not be used for commercial purposes. If you would like to use it for your business, please contact us" (https://api.producthunt.com/v2/docs).
- Attribution is requested.
- The site ToS bans storing a "significant portion" of content and bans crawling (https://www.producthunt.com/legal).

**Decision: GAP**
- Permission path: written permission from hello@producthunt.com.
- Non-commercial research deployments could enable it under the API terms, but it stays off by default because R1.3 is used commercially too.
- **Impact on R1.3:** the announced-launch watchlist must rely on HN, Bluesky and GitHub signals, plus manual entry by operators.

### 2.17 Lobste.rs (stretch), TM-17

**Access**
- `.json` endpoints exist but are undocumented.
- robots.txt disallows `/` for generic agents and sends `Content-Signal: ai-input=no, ai-train=no` (https://lobste.rs/robots.txt).
- Rate limits in the code: 4 per second, 30 per minute and 400 per hour per IP (https://github.com/lobsters/lobsters/blob/main/config/initializers/rack_attack.rb).

**Operator stance:** the same file says: "You're a commercial service? Slow down or email me."

**Decision: GAP**
- robots.txt and the AI-input signal conflict with automated collection and LLM coding.
- Permission path: email the operator, or ask for a database query as offered at https://lobste.rs/about.

### 2.18 dev.to (Forem API) (stretch), TM-18

**Access**
- An API key is optional for public endpoints (https://developers.forem.com/api/v1).
- Rate limits are not documented. The Forem code has 3 per second and 30 per minute, but whether production uses those values is unverified.

**Terms** (https://dev.to/terms)
- A licence for "personal, non-commercial transitory viewing only".
- No copying or mirroring.
- Users must "destroy any downloaded materials".

**Decision: GAP**
- The literal terms conflict with snapshots and commercial use.
- Permission path: written permission from DEV/Forem. Q10 for H2 asks whether the published API implies a licence.

### 2.19 V2EX (stretch), TM-19

**Access** (https://www.v2ex.com/help/api)
- API 2.0 uses a personal access token and is in beta.
- The limit is 600 requests per hour per IP. The 120 per hour in older examples is outdated.
- robots.txt does not block `/api/` (https://www.v2ex.com/robots.txt).

**Terms**
- No terms of service were found; `/terms` resolves to a dictionary page.
- The community rules ask members not to repost full articles and not to make AI-generated posts (https://www.v2ex.com/about).

**PIPL:** posts are largely by people in China. China's PIPL Art. 3 reaches processing abroad that analyses the behaviour of people in China. Art. 53 then requires the processor to appoint a representative in China (https://www.cac.gov.cn/2021-08/20/c_1631050028355286.htm).

**Decision: CLEARED-WITH-CONDITIONS, disabled by default until H2 (Q8)**
- The operator's own token.
- At or below 600 requests per hour, and in practice at or below 300.
- Pseudonymise usernames.
- Project-mention searches only (R1.2), with no profiling of members.

### 2.20 Juejin (stretch), TM-20

- No official public API was found (unverified as a firm negative).
- robots.txt disallows `/search` and some other paths (https://juejin.cn/robots.txt).
- User agreement, in force 2025-12-24 (https://juejin.cn/terms):
  - §10.1 bans use via robots or spiders without the company's permission.
  - §6.1 bars commercial reuse without written permission.
  - §2.8 limits the user licence to non-commercial use.

**Decision: GAP.** Permission path: written permission from Juejin (ByteDance).

### 2.21 Zhihu (stretch), TM-21

- robots.txt: `User-Agent: *` gets `Disallow: /` (https://www.zhihu.com/robots.txt).
- Zhihu Agreement, in force 2025-03-25 (https://www.zhihu.com/term/zhihu-terms), bans:
  - automated collection;
  - using scraped content for LLM or other AI R&D or training.
- An official data open platform exists (https://developer.zhihu.com). Its terms, quota and price are unverified.

**Decision: GAP.** Re-audit the open platform's terms when they can be read.

### 2.22 Bilibili (stretch), TM-22

- The open platform's scopes cover an authorised user's own account only. This is a secondary source (https://github.com/oomol-lab/open-connector/pull/579); the official docs are unverified.
- The current user agreement (2025-04-30) could not be rendered.
- Official copies from 2020 ban crawlers and automated programs without prior written permission:
  - https://game.bilibili.com/licence/h5/ §4.2.11
  - https://www.bilibili.com/protocal/international.html §4.3.15

**Decision: GAP.** Re-verify the current agreement text before any change.

### 2.23 TrustMRR (§8.1 business metric), TM-23

**Access**
- API at `trustmrr.com/api/v1` with an API key.
- 10 requests per minute on a standard key, 60 on a premium key (https://trustmrr.com/docs/api).
- The premium price is unverified.

**Terms** (https://trustmrr.com/terms, 2026-08-26)
- §9.2: no redistribution.
- §9.3: no use to "train, fine-tune, ground, evaluate, or populate an AI model, dataset, search index" without written permission.
- §9.3: no "archive" or "systematically reconstruct" of the database.
- llms.txt says not to scrape (https://trustmrr.com/llms.txt).

**Decision: GAP**
- Storing snapshots (archiving) and populating pigtail's dataset both need written permission.
- Permission path: written permission from TrustMRR that covers private snapshots and aggregate use.
- Meanwhile, MRR is `unknown`, or `self_reported` when a founder states it publicly in another cleared source.

### 2.24 Crunchbase (§8.1 business metric), TM-24

**Access** (https://data.crunchbase.com/docs/using-the-api)
- The API needs an Enterprise or Applications licence.
- The Basic API is discontinued (https://data.crunchbase.com/docs/crunchbase-basic-getting-started).
- 200 calls per minute.
- The price is not published.

**Terms**
- Data may be used for "internal research and analysis" only, and attribution must be a followed link (https://data.crunchbase.com/docs/license-agreement).
- No model training, and data must stay expungeable. On termination, copies must be destroyed and destruction certified (https://data.crunchbase.com/docs/terms).
- The website ToS bans scraping (https://about.crunchbase.com/terms-of-service/).

**Decision: GAP by default**
- Bring-your-own-licence path: an operator who holds a licence may enable it for internal use. Crunchbase data would then:
  - never appear in public mode (R13.3);
  - be kept in a separately deletable store;
  - never be used for training.

### 2.25 YC company directory (§8.1 business metric), TM-25

- There is no official API. The unofficial `yc-oss/api` README says it "uses the Algolia search index to fetch the companies in a GitHub Actions workflow that runs every day" (https://github.com/yc-oss/api). That this is YC's own site-search index is our inference. YC has not authorised this use, so it is not an authorised source.
- robots.txt disallows `/companies?*` (https://www.ycombinator.com/robots.txt).
- The YC ToU bans scraping, data mining and derivative works (https://www.ycombinator.com/legal).

**Decision: GAP**
- YC backing can be recorded as `self_reported` when stated in a cleared source, for example a Launch HN title found through the HN API.
- Manual, human-cited lookups in the evidence are out of automated scope.

### 2.26 GitHub dependency graph (dependencies and dependents, §8.1 adoption), TM-26

**What the APIs offer**
- The REST "Dependency graph" section has three parts: dependency review, dependency submission and SBOM export (https://docs.github.com/en/rest/dependency-graph, 2026-09-25).
- SBOM export: `GET /repos/{owner}/{repo}/dependency-graph/sbom` returns an SPDX JSON of a repo's *dependencies*. The docs say it "is closing down and will not be accessible after November 13, 2026". The replacement is `sbom/generate-report` plus `sbom/fetch-report/{sbom_uuid}` (https://docs.github.com/en/rest/dependency-graph/sboms).
- GraphQL: `Repository.dependencyGraphManifests` is described as "A list of dependency manifests contained in the repository". Its `DependencyGraphDependency` objects list `packageName`, `packageManager`, `requirements` and `repository` of each *dependency* (schema introspected with `gh api graphql` on 2026-09-25).
- **Dependents ("Used by"): no REST or GraphQL endpoint was found** in the REST docs or the GraphQL schema. The docs say "GitHub currently only determines dependents for public repositories" and "The dependent counts are approximate and may not always match the dependents listed" (https://docs.github.com/en/code-security/supply-chain-security/understanding-your-software-supply-chain/exploring-the-dependencies-of-a-repository).
- The dependents list is served only as a web page under `/{owner}/{repo}/network/dependents`. GitHub's robots.txt has `Disallow: /*/*/network` for `User-agent: *` (https://github.com/robots.txt).

**Terms**
- API use: GitHub ToS §H and AUP §7, as in TM-02.
- AUP §7 defines scraping as "extracting information from our Service via an automated process, such as a bot or webcrawler". It allows researchers to use "public, non-personal information … only if any publications resulting from that research are open access" (https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies). It does not address commercial use.

**Decision**
- **Dependencies via the APIs: CLEARED-WITH-CONDITIONS.** The conditions of TM-02 apply. Move off the deprecated SBOM export endpoint before 2026-11-13.
- **Dependents page: GAP.** There is no API, robots.txt disallows the path, and the AUP research allowance does not clearly cover commercial operators. Take dependent counts from deps.dev (TM-12) instead. Its coverage is narrower (npm, Cargo, Maven and PyPI in v3alpha).
- Permission path: GitHub's robots.txt invites crawl requests through https://support.github.com?tags=dotcom-robots.

### 2.27 Discord invite API (§8.1 community size), TM-27

**Endpoint**
- `GET /invites/{invite.code}` with the query parameter `with_counts` ("whether the invite should contain approximate member counts"). It returns `approximate_member_count` ("approximate count of total members") and `approximate_presence_count` ("approximate count of online members") (https://docs.discord.com/developers/resources/invite, 2026-09-25; the old discord.com/developers/docs URL redirects there).
- The docs do not say whether auth is needed. On 2026-09-25 the research agent observed that an unauthenticated `GET https://discord.com/api/v10/invites/discord-developers?with_counts=true` returned HTTP 200 with both counts. Rate limits for this endpoint are not documented (unverified).
- The data is a server-level aggregate. The response can also contain an `inviter` user object and other server metadata, which we must not store.

**Terms**
- Discord Developer Terms of Service (effective 2024-07-08, last updated 2024-06-06; https://support-dev.discord.com/hc/en-us/articles/8562894815383-Discord-Developer-Terms-of-Service, read through the Help Center API because the page returns 403 to automated fetches):
  - "API Data" means "any data, information, or other content you obtain through the APIs", so invite counts are API Data.
  - Section 5.b: API Data must be deleted when no longer necessary for the app's stated functionality, "we request you delete it", or "the applicable user requests you delete it".
  - On termination, developers must "delete any cached or stored API Data".
- Discord Developer Policy (same dates; https://support-dev.discord.com/hc/en-us/articles/8563934450327-Discord-Developer-Policy):
  - Item 15: "Do not use API Data for any purpose outside of what is necessary to provide your stated functionality."
  - Item 18: "Do not sell, license, or otherwise commercialize API Data".
  - Item 20: "Do not mine or scrape any data, content, or information available on or through Discord services".
  - Item 21 bans training ML or AI models on message content, which we do not collect.
- Discord Terms of Service (last updated 2025-08-29; https://discord.com/terms) ban "scraping our services without our written consent, including by using any robot, spider, crawler, scraper, or other automatic device, process, or software", and "otherwise commercializing content or data obtained from our services".

**Decision: CLEARED-WITH-CONDITIONS, disabled by default until H2 answers Q12**
- It is not clear whether low-rate polling of a documented endpoint counts as "mining or scraping", or whether commercial analytics counts as "commercializ[ing]" API Data. Until H2 answers, commercial operators keep this connector off. Non-commercial research deployments may enable it.
- (a) Use only invite codes that the project itself publishes (README, website). Never enumerate or guess codes.
- (b) Use the operator's own registered Discord application, with a stated functionality and a privacy policy as the Developer Terms require (§1, §5.a).
- (c) Store only the server id, the two approximate counts and the fetch time. Drop `inviter` and all user fields at ingest.
- (d) At most one request per invite per day.
- (e) Delete on a request from Discord, the server owner or the project. Delete all stored API Data if access ends.
- (f) Never sell or license the counts. Public outputs are aggregate only.

### 2.28 Slack invite links (§8.1 community size), TM-28

- No documented Slack API returns a member count for a workspace that the caller's token is not installed in. `team.info` needs a `team:read` token and returns id, name, domain, email domain, icon and enterprise fields, with no member count. Cross-workspace lookup by domain "only works for domains in the same enterprise as the querying team token" (https://docs.slack.dev/reference/methods/team.info, 2026-09-25).
- A web search for a public member-count endpoint for `join.slack.com` invite links found nothing (2026-09-25). Whether the join page itself shows a count is **unverified**. Parsing it would be HTML scraping outside any API, which R2.3 rules out.
- Slack API Terms of Service (effective 2025-10-10; https://slack.com/terms-of-service/api) license API access only to apps installed in the Services, and forbid you to "sell, rent, lease, sublicense, redistribute, or syndicate access to any of our APIs".

**Decision: GAP.** Slack community size is `unknown`. Permission path: the project's own workspace admin installs the operator's app and shares counts (`self_reported` unless the admin grants API access for this use).

### 2.29 Careers pages (§8.1 business and hiring signal), TM-29

- These are ordinary web pages on each project's own site, or on a hosted job board. No single set of terms applies. Each host's robots.txt (RFC 9309, https://www.rfc-editor.org/rfc/rfc9309) and site terms must be checked per site.
- A hosted job board (for example an applicant-tracking system's public board) is a separate host with its own terms. None was audited in this pass (unverified).

**Decision: CLEARED-WITH-CONDITIONS, per site**
- (a) Prefer a Wayback lookup or a Save Page Now capture of the careers URL, under TM-13 and its conditions. This means that for commercial operators it is off by default until Q5 is answered.
- (b) A direct fetch is allowed only when that host's robots.txt allows the path for our User-Agent and the site's terms, read by the operator or an agent when the case is created, do not bar automated access. Record that check (URL, date, result) in `evidence.terms_basis` as `TM-29:<host>:<date>`.
- (c) At most one fetch per page per day, with a descriptive User-Agent.
- (d) Extract only project-level facts: whether open roles exist, the number of roles, role titles and locations. Drop names and contact details of recruiters or staff.
- (e) If robots.txt or the terms disallow automated access, record the page as a gap for that case. Manual operator entry is still possible and is marked `self_reported`.

### 2.30 HN "Who is hiring?" threads (§8.1 hiring signal), TM-30

- The monthly threads are posted by the HN account `whoishiring`. Through the Firebase API, the research agent observed that the account was created on 2010-10-20, has 549 submissions, and has an oldest submission dated 2011-03-31. The latest is "Ask HN: Who is hiring? (September 2026)" with 300 top-level comments (https://hacker-news.firebaseio.com/v0/user/whoishiring.json, 2026-09-25).
- The threads can also be found through Algolia with `tags=story,author_whoishiring` (observed).
- The account also posts "Who wants to be hired?" threads. Those contain job seekers' personal details and are **out of scope**.

**Decision: CLEARED-WITH-CONDITIONS, the same as TM-03 and TM-04, pending Q4**
- (a) Use the APIs only.
- (b) Use only "Who is hiring?" threads. Never ingest "Who wants to be hired?" or "Freelancer?" threads.
- (c) Match comments to tracked projects. Store the project, month and role count. Pseudonymise the commenter's handle and drop email addresses and names in the text.
- (d) Do not republish comment text.

### 2.31 Press as a funding source (§8.1 business), TM-31

- There is no automated collector. The operator enters a funding claim by hand, with the article URL, publication date and a short quote of no more than 25 words.
- The data is always labelled `self_reported`, because press reports of funding usually repeat the company's own announcement. It becomes `verified` only if a licensed source confirms it, such as Crunchbase under the operator's own licence (TM-24).
- No platform API terms apply. Copyright limits what may be copied, so store the citation and the extracted facts (amount, date, round, and investors as organisations), not the article text.
- If a copy is needed as evidence, request a Wayback capture of the article under TM-13 instead of storing the page privately.

**Decision: CLEARED-WITH-CONDITIONS (manual only).**

---

## 3. Gap list (R2.3)

| Source | Reason | Permission path | What this costs pigtail |
|---|---|---|---|
| Reddit | Commercial use needs a separate agreement and prior approval. No sharing with third parties. Deletion within 48 h, and anonymisation does not cure it. (TM-05) | Operator's own written commercial agreement covering LLM processing and retention | The "Reddit reach" metric (§8.1); Reddit-driven cases; the Stage 2 backfill (R17.1) |
| YouTube | 30-day storage cap and a ban on derived data (TM-14) | Audited analytics-developer status | Demo-video spread (R5.4); YouTube triggers (R5.5) |
| X | Commercial use beyond prototyping needs Enterprise; 24 h deletion; tracking restrictions (TM-15) | Operator's own Enterprise agreement | Influencer triggers (R8.1, R5.5); a large part of the spread graph (R5.3) |
| Product Hunt | API "must not be used for commercial purposes" (TM-16) | Email hello@producthunt.com | R1.3 announced-launch watchlist; PH launch type (R4.2) |
| Lobste.rs | robots disallow; `ai-input=no` (TM-17) | Email the operator; database query on request | A small devtools channel |
| dev.to | Non-commercial viewing only; destroy downloaded materials (TM-18) | Written permission from Forem | Blog-post triggers |
| Juejin | Automated access and commercial reuse banned (TM-20) | Written permission | Chinese-ecosystem module (R6.2) |
| Zhihu | Automated access and AI use banned; open-platform terms unverified (TM-21) | Open-platform agreement, after re-audit | R6.2 |
| Bilibili | Crawler ban (2020 text; current text unverified) (TM-22) | Written permission | R6.2 |
| TrustMRR | No archiving, dataset population or AI grounding (TM-23) | Written permission | Verified MRR (§8.1 business) |
| Crunchbase | Licensed, internal-only, expungeable (TM-24) | Operator brings their own licence | Funding (§8.1) |
| YC directory | ToU bans scraping; no API (TM-25) | Written permission from YC | "Backing" stratum (R4.2) |
| GitHub dependents page | No REST or GraphQL endpoint; robots.txt `Disallow: /*/*/network` (TM-26) | Crawl permission from GitHub support; meanwhile use deps.dev (TM-12) | GitHub-native dependents counts (§8.1 adoption); ecosystems outside deps.dev |
| Slack invite links | No public member-count endpoint (TM-28) | The project's workspace admin shares counts | Slack community size (§8.1) is `unknown` |

**Not audited in this pass:**
- the OpenDigger GH Archive mirror (issue #323)
- hosted job boards used by careers pages (TM-29 asks for a per-host check)

The Discord invite API, Slack invite links, careers pages, HN "Who is hiring?" threads, press as a funding source, and the GitHub dependency graph were audited on 2026-09-25 (§2.26–§2.31, TM-26 to TM-31).

---

## 4. Connector priority order (drives R2.2 build order)

The order is ranked by value to R1.1, R1.2 and §8.1, weighted by clearance certainty and build cost. Only cleared sources are ranked.

1. **GH Archive / BigQuery** (TM-01): R1.1 detection and community metrics. Treat stars with caution.
2. **GitHub REST/GraphQL** (TM-02): star ground truth and backfill, to compensate for the GH Archive star loss; also R17.1 Stage 1.
3. **HN Firebase** (TM-04): live front-page rank polling for front-page minutes (§8.1). Poll data can only be collected live, so this must start early.
4. **HN Algolia** (TM-03): mention search and backfill (R1.2, R17.1).
5. **Bluesky** (TM-06): Jetstream for live mentions plus the delete stream, and authenticated search. The first person-level source, so R1.5 deletion sync is built here.
6. **npm downloads** (TM-08): the adoption metric (the only fully CLEARED source).
7. **PyPI BigQuery** (TM-07): adoption.
8. **crates.io dumps** (TM-09): adoption.
9. **deps.dev** (BigQuery first) (TM-12): dependents.
10. **Wayback CDX + SPN** (TM-13): R1.2 archival and the pricing-page business signal. Off by default for commercial operators until H2.
11. **Docker Hub** (TM-11): daily pull-count snapshots. Start early because there is no history.
12. **Homebrew** (TM-10): daily snapshots of rolling windows. Start early because there is no history.
13. **V2EX** (TM-19): off by default until the PIPL question (Q8) is answered.
14. **HN "Who is hiring?"** (TM-30): a monthly job on top of the HN connectors (items 3–4).
15. **GitHub dependency graph, dependencies only** (TM-26): adoption context. Build it on the non-deprecated SBOM report endpoints.
16. **Careers pages** (TM-29): per-site checks, Wayback first. Commercial operators wait for Q5.
17. **Discord invite counts** (TM-27): daily snapshots once H2 answers Q12. Non-commercial deployments may start early, because there is no history.
18. **Press funding entries** (TM-31): an operator form, not a connector. Build it with the evidence UI.

Items 11 and 12 are cheap and lose data every day they are not collected, so they may run in parallel with items 6–9. Item 17 has the same property.

Gap sources are not built. Their connector stubs carry only the enable flag, set to off, and the TM reference (R2.1).

---

## 5. Implications for pigtail

1. **R1.1 and R3.3: GH Archive can no longer be the only star signal.**
   - Reported star capture of about 10–20% since February 2026 would break both the ≥ 3σ threshold and fake-star filtering.
   - Decision: detection uses GH Archive for candidates, then confirms with GitHub stargazers API counts.
   - Store a per-case `gharchive_coverage_ratio`.
   - Log an ADR, and add a verifier check of star capture on 20 random repos per month.
   - Hypothesis H-S1: GH Archive under-counts stars after 2025-05 by more than 50% for most repos.
2. **R1.5, §10: deletion sync.**
   - It is needed first for Bluesky (and for Reddit and X if they are ever enabled).
   - Reddit's position that de-identified retention still violates its terms means "keep hash plus coded facts" may not suffice for Reddit. Q2 asks about this.
   - The schema field `deletion_state` is required for every person-level source.
3. **§7, `evidence.terms_basis`: use the memo IDs TM-01 to TM-31 plus the access date.**
   - The connector interface (R2.1) must refuse to run when the enable flag is on but the clearance is GAP, unless the operator records a permission reference.
4. **§8.1 outcome model (feeds M3 `docs/specs/outcome-model.md`).**
   - "Reddit reach" becomes `unknown` by default.
   - MRR (TrustMRR) and funding (Crunchbase, YC) cannot be verified automatically, so the business dimension falls back to `self_reported` from cleared sources, or `unknown`.
   - "Pricing page exists" depends on the Wayback H2 answer. As a fallback, a direct fetch of the project's own site is allowed under the per-site conditions of TM-29 (robots.txt and site terms checked and recorded).
   - Funding from the press is operator-entered and `self_reported` (TM-31). Slack community size is `unknown` (TM-28). Discord community size is available only once Q12 is answered (TM-27). GitHub-native dependents are replaced by deps.dev dependents (TM-26, TM-12).
   - M3 must reflect this and not promise verified business outcomes.
5. **R1.3: the announced-launch watchlist loses Product Hunt.** Replace it with Show/Launch HN, Bluesky "launching" posts, GitHub release and discussion signals, and operator-entered launches. The prospective test (§9.1.4) needs 20 or more launches, so this should be monitored.
6. **R6.2 Chinese-ecosystem module:** the only possible source is V2EX, and only after H2. The module should expect to report "insufficient evidence" (R10.2) and state the platform gap in its coverage (R17.3).
7. **R17.3 coverage score:**
   - Homebrew, Docker Hub and Discord invite counts have no history, so their coverage window starts on the first snapshot.
   - npm has data from 2015-01-10, PyPI complete from 2018-07-26, crates.io from November 2014, and HN from 2006-10-09.
   - GH Archive has a near-empty window from 2025-10-09 to about 2025-10-14 (#312), and likely systematic under-capture since 2025-05 (#310).
   - Hard-code these as coverage metadata for each source.
8. **R5.3 spread graph and R9 mechanism evidence:** with X, Reddit and YouTube as gaps, the graph under-represents those channels. Mechanism cards (R9.1) must list channels that are "unobservable under current terms" so that missing data is not read as a null effect, and §9.3 loser contrasts must use the same channels on both sides.
9. **F15 / §10 LLM processing:** among the cleared sources, none bans LLM inference. However:
   - Reddit (sharing with third parties), YouTube (derived data), Zhihu, TrustMRR ("ground") and Lobste.rs (`ai-input=no`) would each be a problem if ever enabled.
   - Keep a per-source `llm_allowed` flag in the terms metadata (R2.1).
10. **R13.3 public mode:** Crunchbase data (licensed internal use) and all person-level data from any source are barred from public outputs. GitHub AUP §7 also requires research publications to be open access, which the MIT/CC BY plan satisfies.
11. **§10 cost control:** BigQuery (GH Archive, PyPI, deps.dev) is the only material cost among cleared sources: $6.25 per TiB after 1 TiB free each month. Per-source budgets must include BigQuery byte caps (`maximum_bytes_billed`).

---

## 6. Open items and verification notes

- These facts were collected by parallel research passes on 2026-09-25. Items marked "secondary" or "observed" are not official statements.
- Recommended citations for the verifier's 10-citation spot-check (M2 acceptance):
  - TM-05 Reddit (Wiki deletion clause)
  - TM-15 X (§III.M)
  - TM-16 Product Hunt
  - TM-14 YouTube 30-day storage
  - TM-01 GH Archive issue #320
  - TM-08 npm commercial clause
  - TM-13 Internet Archive ToU
  - TM-21 Zhihu AI clause
  - TM-23 TrustMRR §9.3
  - TM-06 Bluesky deletion guideline
- Re-audit cadence: every 6 months, or on any terms-change notice. Reddit, X and Bluesky each changed terms or documentation in 2026.

---

## Changelog

- 2026-09-25 — corrections after verifier spot-check M2-T4. GH Archive: issue #137 answer by the maintainer, #310 root cause and PR #317, #312 drop and recovery, #320 scope of 2 repos. PyPI licence is CC BY 4.0, and the immutability quote applies to `distribution_metadata` only. crates.io access order. "AppView" wording replaced. Internet Archive citation URL. HN Algolia depth and rank-field observation. GitHub ToS D.9 URL. X Developer Agreement section labels. yc-oss/api quote. New audits §2.26–§2.31 (TM-26 to TM-31), with the summary table, gap list, priority order, "Not audited" and implications updated.

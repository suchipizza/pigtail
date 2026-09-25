# Breakout detection re-plan (M1-T18)

**Status:** draft, to be checked by the verifier. **Author role:** `researcher`, with a `compliance` review of terms. Neither author is a lawyer; the terms conclusions are conservative engineering defaults until H2 answers.
**Access date for every URL:** 2026-09-25. **Measurements:** run by the research agent on 2026-09-25 between 13:00 and 15:30 UTC (details in §7).
**Inputs:** ADR-009, ADR-012, ADR-022, ADR-027, ADR-028, ADR-031; PRD R1.1, R1.3, G3, R4.1, R4.4, R17.4; `docs/research/source-matrix.md` §2.1–2.4 (TM-01…TM-04); `docs/specs/outcome-model.md` §2.
**Requirements referenced:** G3, R1.1, R1.3, R3.3, R4.1, R4.4, R17.1, R17.4, §10 (Terms, Cost control).

"Unverified" means we couldn't confirm the fact on an official page or by our own measurement. Numbers we measured ourselves are labelled **measured** and are small samples, not population estimates.

---

## 0. Two findings that change the brief

1. **The stargazers API is closed to pigtail. ADR-012 and the M1-T16 method depend on it.**
   - GitHub announced on 2026-06-30 that "Access to the following public API endpoints will be limited to admins and collaborators": the List stargazers endpoint `/repos/{owner}/{repo}/stargazers` and the List watchers endpoint (https://github.blog/changelog/2026-06-30-upcoming-access-restrictions-to-public-api-endpoints-and-ui-views/). The stated reason is that these lists "increasingly [were] misused to collect user data for spam activities".
   - **Measured:** an authenticated REST call to `/repos/{o}/{r}/stargazers` for a repo we don't administer returns `404 Not Found`. The GraphQL `Repository.stargazers` connection returns `totalCount: 0` and no edges, including for `facebook/react`. The maintainer of Daily Stars Explorer reports the same GraphQL restriction (https://github.com/emanuelef/daily-stars-explorer, via its README as surfaced by search).
   - So per-star `starred_at` timestamps can no longer be fetched for third-party repos. This removes the stargazer-identity basis for the StarScout-style filter (R3.3) and for ADR-012's `starscout_filtered` series.
   - **GitHub's replacement** (changelog 2026-09-04, https://github.blog/changelog/2026-09-04-new-api-endpoint-provides-privacy-safe-star-history-data/) is `GET /repos/{owner}/{repo}/stargazers/history`. It "Returns repository stars grouped by calendar weeks, most recent first". `per_page` max 30 weeks, `page` max 100. Each item has `week`, `total` and `days[7]` (https://docs.github.com/en/rest/activity/starring?apiVersion=2026-03-10#get-repository-star-history).
   - **Measured on that endpoint:**
     - Summing all weeks gives exactly the current `stargazers_count` (`dream-num/univer` 18,174 = 18,174; `facebook/react` 250,717 = 250,717). It is therefore the **current** stargazers bucketed by star date: net of un-stars and survivor-biased, the same semantics ADR-012 assumed for the old API.
     - It returned full history for a 250k-star repo, so the old undocumented 40,000-star cap no longer matters.
     - The current day is filled in during the day (196 stars for Friday at 14:19 UTC), and responses carry `Cache-Control: max-age=60`.
     - **Day buckets seem to be US-Pacific days, not UTC days.** For 13 repos we compared per-repo `WatchEvent`s with the endpoint's count for 2026-09-24. The total absolute error was 289 stars with UTC midnight boundaries and 76 with boundaries at 07:00 UTC (midnight PDT). 11 of the 13 repos matched within ±7. This is **inferred, unverified**; the DST switch on 2026-11-01 will confirm whether the zone is America/Los_Angeles.
     - One request = one core REST call (rate-limit headers show `X-RateLimit-Resource: core`).
2. **A public mirror with near-complete event capture now exists, and our own compliant poller can't match it.**
   - OpenDigger has run a GH Archive-compatible hourly archive since 2026-09-06 (https://github.com/igrigorik/gharchive.org/issues/323).
   - **Measured, 2026-09-24:** 14,641,819 events and 347,966 `WatchEvent`s in OpenDigger's 24 hourly files. The GH Archive copy in ClickHouse holds 6,091 `WatchEvent`s for the same day (§7.1), so OpenDigger has about 57× as many.
   - On hour 12, 98.3% of the `WatchEvent`s that GitHub's per-repo Events API returned for 17 repos were also in OpenDigger (237 of 241 by event id).
   - **Against GitHub's own daily counts** (100 repos in 4 velocity strata, Pacific day 2026-09-24), OpenDigger's star count was **1.013×** the star-history net count, with a per-repo median of 1.000 (§7.4). In other words, near-complete coverage on that day.
   - The catch: OpenDigger polls about every 1.5 s (maintainer comment, 2026-09-13, same issue). GitHub's `X-Poll-Interval` header is 60 s. See §1 and §5.

---

## 1. Option (b): pigtail's own GitHub event collection (Events API)

### 1.1 What GitHub documents
Source: https://docs.github.com/en/rest/activity/events
- `GET /events` lists public events. `per_page` defaults to 15, max 100.
- "The timeline will include up to 300 events. Only events created within the past 30 days will be included." **Measured:** page 4 at `per_page=100` returns `422`, so one poll sees at most 3 × 100 events.
- Polling: "'X-Poll-Interval' header that specifies how often (in seconds) you are allowed to poll". An ETag `304` leaves "your current rate limit … untouched". **Measured:** `X-Poll-Interval: 60` on `/events` and on `/repos/{o}/{r}/events`.
- Latency: "This API is not built to serve real-time use cases. Depending on the time of day, event latency can be anywhere from 30s to 6h."
- Since 2018, public events have been delayed by 5 minutes; the API "continues to support pagination and fetching of up to 300 events" (https://github.blog/changelog/2018-08-01-new-delay-public-events-api/).
- **Is `/events` a sample?** GitHub doesn't say. What we can say:
  - The feed is a rolling window of the latest 300 events, with no cursor or replay.
  - A community answer (no GitHub staff reply) says receiving an event at time T doesn't guarantee that earlier events have all been delivered (https://github.com/orgs/community/discussions/147048).
  - Completeness at any polling rate is therefore **unverified**.
- REST rate limits (https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api):
  - 5,000 requests per hour for a user token.
  - Secondary limits: at most 100 concurrent requests, 900 points per minute, and 90 s of CPU per 60 s. A GET costs 1 point.
- Terms: ToS §H says "Abuse or excessively frequent requests to GitHub via the API may result in the temporary or permanent suspension…" and "You may not share API tokens to exceed GitHub's rate limitations" (TM-02, https://docs.github.com/en/site-policy/github-terms/github-terms-of-service). AUP §4 covers "automated excessive bulk activity" (https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies).

### 1.2 Achievable coverage
- **Stream volume (measured, OpenDigger manifests, 2026-09-24):**
  - 557,804 to 671,525 events per hour; 14.64 M in the day.
  - That is about 155 to 187 events per second.
  - `WatchEvent`s were 2.4% of events (347,966).
- **Our own poller that honours `X-Poll-Interval`:** 3 pages every 60 s gives at most 300 events per minute, or 18,000 per hour. That is **≤ 2.7–3.2% of the stream**, whatever the event type, because `/events` can't be filtered by type. This is no better than GH Archive today (about 2.1% of stars, §7.2).
  - Independent confirmation: RepoRadar (July 2026) ran one poller at "three pages per 60-second cycle" and published an estimate of 2.08%. It later retracted the figure because the estimator had an unexplained 8.6× error (https://github.com/deeason7/reporadar). Treat that figure as indicative only.
- **A poller that captures most of the stream** has to fetch the 300-event window before it rolls over, which takes about 1.6–1.9 s at the measured rate.
  - OpenDigger reports an interval of about 1.5 s between requests at a 20% overlap target. GH Archive's crawler used a fixed 0.75 s (issue #323 comment).
  - One page every 1.5 s is 2,400 requests per hour. All 3 pages every 1.6 s is about 6,750 requests per hour, which is more than one token's 5,000.
  - Either way the poller runs at roughly 40× the rate `X-Poll-Interval` allows.
- **Conclusion (b):** within the documented cadence, own collection gives about 3% coverage. Near-complete coverage means ignoring `X-Poll-Interval`, and possibly pooling tokens, which ToS §H forbids. The PRD non-goal "No circumvention of rate limits … or platform terms" applies. **Rejected.**

### 1.3 What does work: per-repo Events API for repos we already track
- `GET /repos/{owner}/{repo}/events` has the same 300-event window and the same 60 s `X-Poll-Interval`, but it covers one repo.
- It still includes `WatchEvent`s with `actor` (**measured**, 2026-09-25).
- **Measured completeness:**
  - 13 repos, with the window covering 2026-09-24 (Pacific day): 1,290 `WatchEvent`s against 1,285 stars from the history endpoint. The per-repo difference is small once Pacific day boundaries are used (§0).
  - 17 repos for hour 12 UTC: 241 `WatchEvent`s, 98.3% of them also present in OpenDigger.
- Limit: a repo with more than 300 events between polls overflows the window. At 4 polls per hour that is more than about 1,200 events per hour, which only the very largest bursts reach. This is a known truncation to detect and record.
- Use: identity-level star data (for the R1.1 "after bot filtering" step and ADR-027's lockstep flag) for **tracked** repos only, polled every 15–60 min, which stays within `X-Poll-Interval`.
- Terms caveat: see §5, LQ-new-2.

---

## 2. Option (c): candidate screening plus per-candidate measurement

### 2.1 Screens (discovery)

| Screen | What it finds | Limits (cited) | Terms | Notes |
|---|---|---|---|---|
| **GitHub Search API** | New repos (`created:>D stars:>=N`) and active repos (`pushed:>D stars:a..b`), `sort=stars` | 30 requests/min authenticated; "up to 1,000 results for each search"; 100 per page; query ≤ 256 chars, ≤ 5 boolean operators; may return `incomplete_results` (https://docs.github.com/en/rest/search/search). Separate `search` bucket (**measured**: `X-RateLimit-Resource: search`, limit 30) | TM-02 | No "starred-since" qualifier exists (https://docs.github.com/en/search-github/searching-on-github/searching-for-repositories), so Search can find repos but not measure velocity. **Measured** 2026-09-25: `stars:>=100` = 480,837 repos; `created:>2026-09-11 stars:>100` = 456; `created:>2026-09-11 stars:>=20` = 2,095; `pushed:>2026-09-24 stars:50..5000` = 16,124 |
| **GitHub Trending (HTML)** | Daily, weekly and monthly lists with "stars today" | No API. robots.txt doesn't disallow `/trending` for `User-agent: *`, but its preamble says "If you would like to crawl GitHub contact us" (https://github.com/robots.txt). AUP §7 limits reuse of scraped data to research or archival, with no spam and no sale of personal information | **GAP** for commercial operators (conservative) | A licensed copy is available through Trendshift Signal (§3) |
| **HN rank poller (ADR-031)** | `github.com/...` URLs on the front page and Show HN, within minutes | Firebase API "currently no rate limit"; `showstories` holds up to 200 (TM-04) | Project-level; running by default under ADR-031 | Add `showstories` and `newstories` URL extraction to the existing project-level poller. No person-level fields are kept |
| **Bluesky** | Launch posts that link to GitHub | Jetstream needs no auth (TM-06) | **Held** by ADR-022 until CB-02 and related controls exist | Revisit when the hold is lifted |
| **Registries** (npm TM-08, crates TM-09, PyPI TM-07) | Download spikes for packages that map to a repo | Daily granularity | Cleared / cleared with conditions | Mostly lagging and useful for adoption; low value for G3 latency |
| **Product Hunt, launch watchlist (R1.3)** | Announced launches | — | Product Hunt is a **GAP** (TM-16) | R1.3 relies on HN, Bluesky (held) and operator entry |
| **OpenDigger mirror** | Repos with any stars or forks in the last hour, near-complete (§0) | Hourly files of about 300–410 MB compressed (**measured** manifests), so about 9 GB/day of transfer | See §5 | Drop-in for the existing GH Archive scanner (same file format; only the host changes) |
| **GH Archive** (status quo) | About 2% of stars | Hourly | TM-01 | Keep as a fallback and as a free control |

### 2.2 Measurement per candidate: what is still possible

| Method | Resolution | Cost (one token) | Net of un-stars? | Identities (for bot filter)? | Backfill? |
|---|---|---|---|---|---|
| Stargazers API `starred_at` | Per star | — | — | — | **Closed** since 2026-06-30 (§0) |
| **Star-history endpoint** | Day (Pacific day, inferred) | 1 core request per 30 weeks of history; `per_page=5` covers 35 days in 1 request | Yes (sum = current count) | No | **Yes, back to repo creation** (docs: pagination "walks backward toward the repository's creation week") |
| **GraphQL batched `stargazerCount`** | Whatever cadence we poll at (hourly) | **Measured**: 100 aliased `repository{stargazerCount forkCount pushedAt}` lookups cost **1 point**. Limit 5,000 points/h, secondary 2,000 points/min (https://docs.github.com/en/graphql/overview/rate-limits-and-query-limits-for-the-graphql-api) | Yes (public counter) | No | No (live only) |
| **Per-repo Events API** | Per event | 1–3 core requests per poll; ETag `304`s are free (https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api) | No (starts only) | Yes (`actor`) | Last 300 events / 30 days |
| OpenDigger / GH Archive hourly | Per event | Bandwidth only | No | Yes | OpenDigger only from 2026-09-06 |

**Can star history be rebuilt after the fact?**
- **Yes, at daily resolution**, for any public repo, back to its creation, through the star-history endpoint. The series is net of un-stars and counts only current stargazers.
- **Not at hourly resolution, and without identities.** `starred_at` per stargazer is no longer available to non-collaborators.
- Hourly history exists only where pigtail polled counts live, or in event archives: OpenDigger from 2026-09-06, and GH Archive for earlier periods at its degraded coverage.

### 2.3 Budget for tracking 2,000 candidates at ≥ 1 h resolution (one token)
- **Hourly counts:** GraphQL, 2,000 ÷ 100 = **20 points/h** (0.4% of 5,000).
- **Daily truth and baseline:** star-history, 2,000 requests/day ≈ **83 core requests/h** (1.7%). Unchanged responses are cheaper with ETag. The 30-day baseline on entry costs 1 request each.
- **Identity-level stars for bot filtering:** per-repo events, page 1 hourly with ETag.
  - Worst case (every repo changed every hour) is **2,000 core requests/h** (40%).
  - Realistic: only repos above a pre-threshold (say ≥ 30 stars in 24 h, typically a few hundred) need this, polled every 15–30 min. That is about **400–1,200 core requests/h**.
- **Total:** about 1,300 core requests/h and 20 points/h. This fits one token with room to spare.

---

## 3. Option (d): third-party services

| Service | Data source | Coverage for 2025–26 stars | API / limits | Terms (commercial, storage) | Cost | Verdict |
|---|---|---|---|---|---|---|
| **OSS Insight** (PingCAP) | "All the data we use … sources from GH Archive" (https://ossinsight.io/blog/how-it-works). A PingCAP blog also mentions combining GH Archive with the GitHub events API for real-time updates (https://www.pingcap.com/blog/build-a-better-github-insight-tool-in-a-week-a-true-story/); whether that is still true is unverified | Inherits GH Archive's loss (the real-time part's coverage is unverified) | Public API `v1beta`: no auth, 600 requests/h per IP, 1,000/min globally; trending and "stargazers history" endpoints (https://ossinsight.io/docs/api) | No API terms found (unverified); the repo is Apache-2.0 code, which is not a data licence | Free | **GAP** (no terms; same data-quality problem) |
| **Trendshift** / Signal API | Not disclosed (unverified). Signal offers "engagement spike detection", Trendshift rankings, and "GitHub's trending list captured daily" (https://trendshift.io/signal) | Unverified | API key; limits not published (https://api.trendshift.io/docs was empty when fetched) | ToS effective 2026-07-19: raw data "licensed for use within your own products and analysis"; "may not re-distribute, resell, or publish the raw API data" (https://trendshift.io/tos) | $9/month starter (https://trendshift.io/signal) | **Optional screen, CLEARED-WITH-CONDITIONS** (private use only, no republication). Needs an owner budget decision |
| **star-history.com** | Now uses GitHub's star-history endpoint; before that, the stargazers API (https://www.star-history.com/blog/new-github-star-history-api/, https://www.star-history.com/blog/github-stargazer-api-restriction/) | Same as the GitHub endpoint | No API of its own documented (unverified) | — | Free | Not needed; call GitHub directly |
| **Daily Stars Explorer** | Open-source tool; used GraphQL stargazers, which the 2026 restriction broke (README, https://github.com/emanuelef/daily-stars-explorer) | — | Self-hosted, with the user's own PAT | Tool, not a data service | Free | Not a source |
| **ClickHouse `github_events`** | "contains a copy of the GH Archive", reloaded every 10 min (https://clickhouse.com/docs/getting-started/example-datasets/github-events). The site warns: "Since the middle of 2025 that feed reports almost only PushEvent events … heavily undercounted for 2025–2026" (https://ghe.clickhouse.tech/) | Same as GH Archive. **Measured**: monthly `WatchEvent`s 5.5–7.1 M through 2025-05, then 2.0–3.8 M (2025-06 to 2026-03), 79,895 in 2026-07 and 69,168 in 2026-08, with bursts in early September (§7.1) | Public playground, SQL over HTTP | "research purposes"; no licence stated | Free | Useful only as a query tool for measuring GH Archive; **not a better source** |
| **Libraries.io** | Package managers plus current repo metadata | Current `stars` only, no history (https://libraries.io/api) | 60 requests/min per API key | No data licence stated on the API page | Free | Not useful for velocity |
| **ecosyste.ms** | Timeline index: "Data updated hourly from GH Archive" (https://timeline.ecosyste.ms/). The repos service has current metadata | Same as GH Archive | Free 300 requests/h; Develop $200/month (1,000/h); Scale $1,000/month (5,000/h) (https://ecosyste.ms/pricing) | Data CC BY-SA 4.0; commercial licences on request (https://ecosyste.ms/) | Free or paid | Not better for stars; share-alike clashes with pigtail's MIT outputs if reused |
| **OpenDigger mirror** | Its own Events API collector (§0) | Near-complete (measured) | Anonymous HTTPS, hourly files plus manifest | No licence or terms published; "publicly available for anyone to download and use" (issue #323) | Free (bandwidth about 9 GB/day) | **Best public event source; see §5 for conditions** |

**Is there any other public GitHub event mirror with better coverage?**
- ClickHouse, OSS Insight and ecosyste.ms all take their data from GH Archive.
- OpenDigger (#323) is the only independent mirror we found, and its coverage is much better (§0, §7).
- The BigQuery `githubarchive` dataset is also GH Archive (TM-01).

---

## 4. Comparison

Coverage is the share of true stars seen. G3 needs a case within 24 h. Tier 1 is ≥ 5,000 repos (R4.4).

| Option | Star coverage | Latency vs G3 | Cost | Rate-limit feasibility (Tier 1 + live) | Terms clearance | Effort |
|---|---|---|---|---|---|---|
| GH Archive (status quo) | **~2.1%** measured (§7.2) | About 1–2 h, but misses almost all bursts | Free | n/a | TM-01, CwC | Done |
| (b) own `/events` at `X-Poll-Interval` | ≤ ~3% (arithmetic, §1.2) | 5 min + queue (30 s–6 h) | Free | Fine (60 requests/h) | TM-02, CwC | M |
| (b′) own `/events` at ~1.5 s | Near-complete (as OpenDigger) | Minutes | Free | 2,400–6,750 requests/h: over one token for all pages | **Not cleared** (ignores X-Poll-Interval; ToS §H) | M |
| **OpenDigger mirror** | **~100%**: 1.013× star-history net stars (100 repos, 1 day, §7.4); 98% of per-repo `WatchEvent`s on a sampled hour; 57× GH Archive | About 1 h 5 min (hour file plus 5-min API delay; manifest written at +2 min) | Free, about 9 GB/day transfer | No GitHub API cost | **Provisional CwC**, screen-only (§5, LQ-new-1) | **S** (drop-in for the GH Archive scanner) |
| **GraphQL batched counts** (watch universe) | 100% of *net* change for repos in the universe, 0% outside it | ≤ 1 h | Free | 50,000 repos hourly = 500 points/h (10%) | TM-02, CwC, no personal data | S |
| **Star-history endpoint** | 100% net, daily | Same day (current-day bucket updates; about 60 s cache) | Free | Tier 1 full histories: about 5,000 × 1–24 pages; ≈ 3 h of core budget | TM-02, CwC, no personal data | S |
| **Search API** | Discovery only | ≤ 30 min for new repos | Free | 1,800 requests/h in its own bucket | TM-02, CwC | S |
| **Per-repo Events** (tracked repos) | ~100% of star events within the window (§1.3) | 5 min + queue | Free | 400–2,000 core requests/h for 2,000 candidates | TM-02, CwC; LQ-new-2 | S |
| HN rank poller | Discovery (HN-launched repos) | Minutes | Free | n/a | ADR-031 | XS |
| Trendshift Signal | Unverified | Daily lists | $9/month | n/a | CwC (own use) | S |
| OSS Insight | GH Archive-based | — | Free | 600 requests/h per IP | GAP | — |
| ClickHouse / ecosyste.ms | GH Archive-based | — | Free / $200+ | — | Research-only / CC BY-SA | — |

---

## 5. Terms and compliance review (compliance agent)

- **Star-history endpoint and GraphQL counts: CLEARED-WITH-CONDITIONS (TM-02).** They contain no personal data, which makes them the cleanest sources pigtail has. Conditions: the operator's own token; respect primary and secondary limits; serial requests; ETag; a fixed schedule (best-practices page).
- **Search API: CLEARED-WITH-CONDITIONS (TM-02).** Results include owner logins. ADR-022's ban on naming personal-account repos in outputs still applies.
- **Own `/events` firehose faster than `X-Poll-Interval`: NOT CLEARED.**
  - The docs call the header how often "you are allowed to poll".
  - ToS §H prohibits "excessively frequent requests" and sharing tokens to exceed limits.
  - At the permitted cadence it is useless (≤ 3%).
- **Per-repo Events API for tracked repos: CLEARED-WITH-CONDITIONS (TM-02), with a new legal question (LQ-new-2).** The ≥ 60 s cadence is respected.
  - Risk: GitHub closed stargazer *lists* in 2026 to stop spam harvesting. `WatchEvent.actor` still exposes who starred. Collecting it for many repos could be read as working around the intent of that restriction, although the endpoint is public and documented.
  - Conservative conditions:
    - pseudonymise `actor` at ingest (keyed hash, per TM-01);
    - use identities only for aggregate bot and lockstep flags;
    - never rebuild or export stargazer lists;
    - keep person-level event rows at most 30 days, then only aggregates;
    - collect only for repos with an open case or above the pre-threshold.
- **OpenDigger mirror: PROVISIONALLY CLEARED-WITH-CONDITIONS, screen-only (new TM-32), pending LQ-new-1.**
  - The content is the same kind of data as GH Archive (TM-01, already a permitted person-level source under ADR-022).
  - There is no licence or terms, the same as GH Archive; GitHub's AUP §7 governs reuse.
  - Extra risks:
    - (i) the collector appears to poll about 40× faster than `X-Poll-Interval`, so pigtail would build on data gathered in a way pigtail itself wouldn't do;
    - (ii) it is a three-week-old service with a single operator (Aliyun OSS in China), so stability risk is high;
    - (iii) the files are about 9 GB/day.
  - Conditions:
    - stream-filter to `WatchEvent` and `ForkEvent`, pseudonymise actors in memory, and store only repo-hour aggregates plus pseudonymised actor sets for bot filtering;
    - do **not** snapshot whole raw files (CB-04; this changes ADR-027 item 4 for this source: store hash plus manifest instead);
    - use it for screening and bot filtering only, never as the scoring series;
    - attribute "OpenDigger";
    - stop if the operator publishes terms that forbid this.
- **GitHub Trending HTML: GAP** for commercial operators (no API; AUP §7 allowance is for research and archiving). Don't scrape. Use Trendshift if a trending screen is wanted.
- **OSS Insight: GAP** (no terms found). **ecosyste.ms:** CC BY-SA 4.0; not needed. **Libraries.io:** not needed.

New legal-review questions (for `docs/compliance/legal-review-questions.md`; not added here because this task edits only this file):
- **LQ-new-1:** May a commercial operator ingest a third-party mirror (OpenDigger) of GitHub public events whose collection cadence appears to exceed GitHub's `X-Poll-Interval`, given that the mirror publishes no licence?
- **LQ-new-2:** After GitHub restricted stargazer lists (2026-06-30) to curb spam harvesting, may pigtail process `WatchEvent.actor` from the Events API (pseudonymised at ingest, used only for aggregate bot filtering)? Does the same apply to GH Archive and OpenDigger `WatchEvent`s?

---

## 6. Recommended architecture (hybrid) and API budget

### 6.1 Layers
1. **Discovery (candidate intake, continuous).**
   - (a) OpenDigger hourly scan, using the existing `velocity.py` code with the host switched, as the primary screen.
   - (b) GH Archive scan kept as a free control and fallback.
   - (c) Search API sweeps: new repos every 30 min; active repos daily.
   - (d) GitHub URLs from the HN rank poller, including `showstories`.
   - (e) Optional: Trendshift Signal daily.
   - (f) Registries and Bluesky later, once cleared.
2. **Watch universe `U` (hourly net counts).** GraphQL batched `stargazerCount` and `forkCount`, 100 repos per query.
   - `U` = every repo any screen produced in the last 30 days, plus open cases, plus tracked repos (R17.4), plus a rolling slice of the wide universe (`stars ≥ 100`, about 481k, swept once a day).
   - Cap at 50,000 repos hourly.
3. **Baseline and confirmation (daily truth).** Star-history endpoint:
   - fetched on entry to `U` (35 days in one call, which gives the R1.1 30-day baseline);
   - refreshed daily for repos above the pre-threshold and for cases.
4. **Case tracking (identity-level).** Per-repo Events polling, every 15 min for open cases and every 60 min for pre-threshold repos, with ETag. Provides the bot and lockstep filter (ADR-027 item 2), forks, and early community events.

### 6.2 Budget with one `GITHUB_TOKEN` (fine-grained PAT, public read)
Budgets are 5,000 core requests/h, 5,000 GraphQL points/h, and 30 search requests/min (1,800/h). Each is a separate bucket (**measured** headers).

| Job | Bucket | Volume | Per hour |
|---|---|---|---|
| Hourly counts for `U` = 50,000 | GraphQL | 500 queries × 1 point | 500 points (10%) |
| Daily wide sweep, 481k repos, spread over 24 h | GraphQL | 4,810 points/day | ≈ 200 points (4%) |
| Star-history on entry (≈ 5,000 new repos/day, assumed) | core | 1 request each | ≈ 210 (4%) |
| Daily star-history refresh, 2,000 candidates | core | 1 request each (ETag) | ≈ 85 (2%) |
| Per-repo events: 300 cases × 4 polls × ~1.3 pages | core | worst case | ≈ 1,560 (31%) |
| Per-repo events: 1,000 pre-threshold repos hourly, page 1 | core | 304s are free | ≤ 1,000 (20%) |
| Repo metadata / misc | core | — | ≈ 200 (4%) |
| Search: new-repo sweep every 30 min (≤ 10 queries × ≤ 10 pages) | search | — | ≤ 200 (11%) |
| Search: daily active-repo partitions | search | ≈ 170/day | ≈ 7 |
| **Totals** | | | **core ≈ 3,055 (61%) · GraphQL ≈ 700 (14%) · search ≈ 210 (12%)** |

One-off jobs:
- Build the wide-universe list through Search: about 481 star-range partitions × 10 pages ≈ 4,810 search requests ≈ 2.7 h.
- Tier 1 full histories (R4.4): 5,000 repos × 1–24 pages; ≈ 15k–30k core requests, or 3–6 h at full budget.
- R4.1 trailing-24-month backfill: star-history for about 481k repos at 1–4 pages (a 24-month window needs 4 pages of 30 weeks). ≈ 0.5–2 M requests, or 4–17 days at 100% of one token, and about twice that at 50%. **Feasible as a background job.**

Secondary limits to respect: ≤ 900 REST points/min, ≤ 2,000 GraphQL points/min, ≤ 100 concurrent, serial queue. The large-alias GraphQL query's CPU and timeout behaviour at scale is **unverified**; see M3.

**GitHub App instead of a PAT?**
- An installation token starts at 5,000 REST requests/h and grows by 50/h per repository beyond 20 and per user beyond 20 org members, up to 12,500 (15,000 on Enterprise Cloud).
- GraphQL for an installation is 5,000 points/h (10,000 on Enterprise Cloud).
- `GITHUB_TOKEN` in Actions gets 1,000/h per repository (rate-limit pages above).
- An App installed on the owner's account gains nothing. Running a PAT and an App side by side to double the limits comes close to ToS §H's token-sharing clause, so **don't**. One identity is enough (§6.2 totals).

### 6.3 What R1.1 means under this design
- **"≥ 100 net stars within 48 h"** is computed on the **public stargazer count**: hourly GraphQL snapshots, so truly net of un-stars, with star-history as daily confirmation.
  - This is *closer* to R1.1's wording than GH Archive ever was, because GH Archive has no un-star events (ADR-027 item 2).
  - Where only daily data exists (backfill), "48 h" becomes "the last 2 complete Pacific days plus today so far", and precision is recorded as `day`.
- **"≥ 3σ above its own 30-day baseline"**:
  - baseline = star-history daily counts for the 30 days before the window (converted to 48 h sums);
  - σ uses ADR-027 item 3's √μ floor;
  - for repos under 30 days old, `baseline_quality: partial/none` as today.
- **"after bot filtering"** moves to a **confirmation stage**:
  - the screen triggers on raw net counts;
  - the case opens only after the bot and lockstep filter has run on identity-level star events (per-repo Events, or OpenDigger actors) for the window;
  - the case records `bot_filter_basis` ∈ {`repo_events`, `opendigger`, `gharchive`, `none`}, and `coverage_ratio` = identity-level stars seen ÷ star-history net stars (ADR-009 field; the reference changes from the stargazers API to star-history).
- **Onset hour** (outcome-model §2.1) is computed from hourly count snapshots or OpenDigger events when present. Otherwise onset precision is `day`.
- **G3:** a repo in `U` is detected within about 1 h of crossing and confirmed within one more poll cycle. A repo not yet in `U` is found by OpenDigger within about 1–2 h, by Search within 30 min once it has ≥ 20 stars, or by HN within minutes. The expected worst case is well inside 24 h, *if* OpenDigger stays up. Without it, G3 depends on Search and HN, and recall must be measured (M4).
- **R17.4:** "Track this project" = add to `U` and to the per-repo Events poller. This is cheap and fits within 1 h.

---

## 7. Measurements run for this document (2026-09-25)

The agent's own `gh` CLI login was used, because no project `GITHUB_TOKEN` exists yet (H1). About 2,100 core requests, 5 search requests and under 60 GraphQL points were used. About 1,000 of the core requests were wasted by a script bug that looped on 404s; this was within limits. About 11 GB of OpenDigger files were streamed.
- The raw OpenDigger files held actor logins. They were stream-filtered to `WatchEvent`s in the scratchpad, used only for per-repo counts and event-id matching, and **deleted after analysis**. No identities are recorded here. Repo names below are organisation or project repos taken from public aggregates.

### 7.1 GH Archive via ClickHouse
- Monthly `WatchEvent`s: 2024 average ≈ 6.2 M; 2025-05 5.49 M; 2025-06 3.81 M; 2026-02 2.22 M; 2026-05 0.99 M; 2026-06 0.31 M; 2026-07 79,895; 2026-08 69,168.
- 2026-09 daily counts ranged from 2,564 to 129,036 (for example, 2026-09-06 129,036 and 2026-09-24 6,091). Coverage is wildly unstable, which supports ADR-009's "no global correction factor".

### 7.2 GH Archive vs the star-history endpoint
- Top 25 GH Archive repos for 2026-09-24 (23 usable): **417 GH Archive stars against 19,940 star-history stars = 2.09%** (median per-repo 2.1%). The day-boundary mismatch (§0) adds noise but can't explain a 48× gap.
- Two outliers show why confirmation matters:
  - one repo had 29 GH Archive stars and **0** net stars that day; its 190 total suggests the stars were removed or un-starred;
  - one repo returns 404 today (deleted).

### 7.3 OpenDigger vs GH Archive vs per-repo Events
- Hour 2026-09-24T12:
  - OpenDigger 14,924 `WatchEvent`s against GH Archive 215; 201 of the 215 appear in OpenDigger by id.
  - For 17 repos with a covering per-repo Events window: OpenDigger 249 against repo-events 241; 237 of the 241 are in OpenDigger (**98.3%**).
- Day 2026-09-24 (UTC): OpenDigger 14,641,819 events, 347,966 `WatchEvent`s; GH Archive 6,091.
- Pacific-day comparison of OpenDigger against star-history for a larger sample: **see §7.4**.

### 7.4 OpenDigger vs the star-history endpoint (Pacific day 2026-09-24)
- **Method:**
  - Take OpenDigger `WatchEvent`s with `created_at` in [2026-09-24T07:00Z, 2026-09-25T07:00Z). That uses 31 hourly files, de-duplicated by id: 0 duplicates, 0 malformed lines.
  - The last file ends at 06:54:57Z, so about 5 minutes (about 0.3% of the day) are missing.
  - The result was 326,255 stars across 122,960 repos.
  - We drew a random sample (seed 42) of 25 repos per stratum of OpenDigger daily stars and compared each with the star-history day count.

| Stratum (OpenDigger stars/day) | Repos in stratum | n | OpenDigger | Star-history | Ratio | Median per repo | p10–p90 |
|---|---|---|---|---|---|---|---|
| ≥ 200 | 104 | 25 | 12,757 | 12,549 | 1.017 | 1.006 | 0.979–1.055 |
| 50–199 | 488 | 25 | 2,348 | 2,357 | 0.996 | 1.000 | 0.967–1.035 |
| 10–49 | 3,213 | 25 | 528 | 521 | 1.013 | 1.000 | 0.976–1.067 |
| 3–9 | 10,710 | 25 | 123 | 121 | 1.017 | 1.000 | 0.956–1.350 |
| **All** | | 100 | **15,756** | **15,548** | **1.013** | **1.000** | |

- **Reading:**
  - OpenDigger saw essentially every star that day in every velocity stratum, including the burst stratum.
  - A ratio slightly above 1 is expected: OpenDigger counts gross star events, while star-history counts net current stargazers.
  - The fit on Pacific-day boundaries also supports the time-zone inference in §0.
  - This is **one day**, measured three weeks after the mirror started. M1 in §8 makes it a daily check.
- **Sizing (OpenDigger, same Pacific day):** repos with ≥ 30 stars: 1,078; ≥ 50: 592; ≥ 100: 282; ≥ 200: 104; ≥ 500: 28; ≥ 1,000: 9. So about 300 repos a day pass R1.1's absolute leg on one day alone, and about 1,000 pass a 30-star pre-threshold. This matches the per-repo Events budget in §6.2.

---

## 8. Validation plan once the project token exists (H1)
- **M1 — Daily coverage audit (replaces M1-T16's method).**
  - Every day, sample 200 repos stratified by velocity (from `U`).
  - Compare (i) OpenDigger and (ii) GH Archive `WatchEvent` counts per Pacific day with the star-history day count.
  - Store per-source daily coverage.
  - Alarm if OpenDigger falls below 0.90 or a file or manifest is missing; fall back to Search, HN and `U`.
- **M2 — Confirm the day-boundary time zone.** Check before and after the DST change on 2026-11-01. If it isn't Pacific, change the aggregation.
- **M3 — GraphQL sweep limits.**
  - Run 100-alias queries at `U` = 10k, then 50k.
  - Record point cost, latency, timeouts, partial `errors`, and secondary-limit responses.
  - Confirm that hourly count deltas summed over a Pacific day equal the star-history day count, within the un-star noise.
- **M4 — Discovery recall and G3 lag (14 days).**
  - Ground truth: repos crossing R1.1 on daily star-history, from the daily wide sweep.
  - Measure which screens found each one first, and the lag from onset to case.
  - Target: ≥ 95% of crossings opened within 24 h.
- **M5 — Per-repo Events completeness.** For tracked repos, compare `WatchEvent`s with star-history per day; record window overflows.
- **M6 — Bot-filter agreement.** Compare lockstep flags computed from repo-events actors with flags from OpenDigger actors on the same repos.
- **M7 — Budget ledger.** Log `X-RateLimit-*` per bucket per hour; keep the steady state ≤ 70% per bucket.

---

## 9. Implications for pigtail

1. **G3, R1.1:** Replace GH Archive as the primary screen with the hybrid in §6. G3 becomes achievable (≤ ~2 h detection for repos in `U` or in OpenDigger) and is measured by M4.
2. **R1.1:** Evaluate the thresholds on the public net stargazer count (hourly GraphQL, daily star-history). Bot filtering becomes a confirmation step on identity-level events. Record `bot_filter_basis` and `coverage_ratio`. R1.1's thresholds are unchanged, and no recalibration for GH Archive coverage is needed any more.
3. **ADR-012 is superseded:** the stargazers API is closed. The scoring star series comes from the star-history endpoint (daily, net, survivor-biased, back to creation). Update `outcome-model.md` §2.1 (onset from the hourly series where available, otherwise day precision) and M3-T3 (costs are about 1–24 requests per repo, with no 40k cap).
4. **R3.3 (fake-star filter):** StarScout-style filtering needs stargazer identities. They are now available only from events (OpenDigger from 2026-09-06, per-repo Events live, GH Archive at about 2% before that). The `starscout_filtered` series is `unknown` for historical windows before 2026-09-06, and the R3.3 reproduction must report this.
5. **R4.1:** The 24-month universe can be backfilled at daily resolution from star-history in about 1–3 weeks of background budget on one token. R4.4 Tier 1 (≥ 5,000) histories cost about 3–6 h.
6. **R17.1 Stage 1 / R17.4:** GitHub history for any URL = star-history (1–24 requests). "Track" = add to `U` and to the repo-events poller. Both fit the ≤ 10 min and ≤ 1 h targets.
7. **R1.3:** Discovery of announced launches depends on HN (ADR-031 poller: add `showstories`), operator entry, and Bluesky after the ADR-022 hold is lifted. Product Hunt stays a gap.
8. **§10 Terms / ADR-022:** Add TM-32 (OpenDigger, provisional, screen-only) and LQ-new-1 and LQ-new-2. Don't collect `/events` faster than `X-Poll-Interval`. Don't scrape Trending. Owner decisions: the Trendshift $9/month option; the OpenDigger bandwidth of about 9 GB/day on the host.
9. **ADR-027 item 4:** Don't snapshot whole OpenDigger hourly files (about 300–410 MB each). Store hash plus manifest plus the filtered subset instead (CB-04).
10. **M1-T16:** Change its method to the star-history comparison (§8 M1). The ADR-009/012 reverse conditions ("coverage ≥ 0.95") now apply to OpenDigger as well as to GH Archive.

### Proposed ADR text

> **ADR-032 — Breakout detection: hybrid screen plus GitHub net-count measurement; stargazers API replaced by the star-history endpoint (2026-09-25)**
> Context: GH Archive captured about 2.1% of stars on 2026-09-24 (M1-T18 §7.2). GitHub restricted `/stargazers` (REST and GraphQL) to admins and collaborators on 2026-06-30 and on 2026-09-04 added `GET /repos/{o}/{r}/stargazers/history` (daily, net counts, back to creation, no identities). Pigtail's own `/events` collection at the permitted `X-Poll-Interval` (60 s) would see ≤ 3% of the public stream (~650k events/h). The OpenDigger mirror (#323) matched GitHub's daily net star counts (1.013×, 100 repos, one day) but publishes no licence and polls faster than GitHub's interval.
> Options: (a) GH Archive only; (b) own `/events` firehose; (c) screens plus per-candidate measurement; (d) third-party services; (e) hybrid of (c) with the OpenDigger screen.
> Decision: (e).
> (1) Screens: OpenDigger hourly (screen and bot filter only, aggregates plus pseudonymised actors, no raw snapshots; provisional under TM-32 pending LQ-new-1), GH Archive (control), Search API sweeps, HN rank poller URLs (add `showstories`).
> (2) Watch universe `U` ≤ 50k repos: hourly GraphQL batched `stargazerCount`/`forkCount`.
> (3) Star-history endpoint for the 30-day baseline, daily confirmation, backfill and the scoring series (supersedes ADR-012's stargazers-API source; three series become `raw_net` (star-history), `bot_filtered` and `starscout_filtered` (event-based, `unknown` where no identity-level events exist)).
> (4) Per-repo Events API at ≥ 15 min intervals for cases and pre-threshold repos, for the R1.1 bot filter (pseudonymised at ingest, 30-day person-level retention, pending LQ-new-2).
> R1.1 is evaluated on net public counts, with bot filtering as a confirmation step; the case records `bot_filter_basis` and `coverage_ratio`. Pigtail never polls `/events` faster than `X-Poll-Interval`, never pools tokens, never scrapes Trending, and runs on one token within ≤ 70% of each bucket.
> How to reverse: If OpenDigger coverage falls below 0.90 for 7 days, or its terms or LQ-new-1 forbid use, drop it and rely on Search, HN and `U` (M4 recall decides whether G3 still holds). If GH Archive coverage is restored to ≥ 0.95 (M1-T16), it can replace OpenDigger. If GitHub restores `starred_at`, revisit (3).

---

## 10. Open issues
- Whether `/events` is complete at any polling rate is unverified. The OpenDigger comparison is against per-repo Events, which come from the same system.
- The Pacific-day bucketing of star-history is inferred from 13 repos (§0) and is consistent with the 100-repo fit in §7.4. It is not documented (M2).
- GraphQL 100-alias queries were measured once (cost 1 point). CPU and timeout behaviour at 50k repos per hour is unverified (M3).
- OpenDigger has no terms and a single operator, and its bandwidth is about 9 GB/day. It is 19 days old, and its coverage was validated on one day only (§7.4).
- Trendshift's data source and API limits are unverified.
- Whether star-history excludes stars from spam-flagged or suspended accounts is unverified (the net-zero outlier in §7.2).
- LQ-new-1 and LQ-new-2 need to go into the legal-review list, and TM-32 into the terms memos. That is outside this task's file scope.

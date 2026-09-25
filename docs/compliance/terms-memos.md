# Per-source terms memos (TM-01 … TM-33)

Part of the compliance pack: see [README.md](README.md).

**Prepared by:** the compliance agent. It is not a lawyer. These memos prepare material for legal review at gate H2, and until that review happens pigtail enforces conservative defaults.

**Access date:** every URL was accessed on **2026-09-25** unless noted otherwise.

**Quotes:** each quote is verbatim and no longer than 25 words. An ellipsis (…) marks text we left out.

**Assumptions** (from `docs/research/source-matrix.md`):
- The deployment is used commercially.
- Raw snapshots are stored in private storage.
- Handles are pseudonymised.
- Person-level data is kept for at most 24 months.
- Text is coded by an LLM through inference only.

**Referencing:** each memo ID goes into `evidence.terms_basis` (PRD §7). A connector may run only while its memo's decision is CLEARED or CLEARED-WITH-CONDITIONS and its conditions are implemented (R2.1, R2.3).

---

### TM-01: GH Archive (+ BigQuery)

**Decision: CLEARED-WITH-CONDITIONS**

**Clauses relied on**
- From https://github.com/igrigorik/gharchive.org:
  > "Note this repository does _not_ contain the GH Archive dataset (event archives/data)."
  > "The dataset includes material that may be subject to third party rights."
  - The dataset itself has no stated licence. Issue #137 (2016) asked for the licence. The maintainer (igrigorik) answered on 2016-04-04 with an "Official answer from GitHub" and closed the issue (https://github.com/igrigorik/gharchive.org/issues/137):
    > "The API is free to use, but you need to follow our Terms of Service."
    - The answer also says repository content "may be subject to the terms of the repo's project", and points to GitHub's privacy policy for republishing personal information.
    - It states no dataset licence.
- From GitHub Acceptable Use Policies §7 (https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies):
  > "Researchers may use public, non-personal information from the Service for research purposes, only if any publications resulting from that research are open access."
  > "Your use of information from the Service must comply with the GitHub Privacy Statement."

**Conditions**
- Pseudonymise actors when data is ingested.
- Never sell or export personal information.
- Public outputs are aggregate only and open access.
- Enforce a BigQuery byte cap.
- Cross-check star counts against the GitHub API, because the data-quality issues are open (https://github.com/igrigorik/gharchive.org/issues/310, https://github.com/igrigorik/gharchive.org/issues/320). The loss is likely systematic (the crawler reads only page 1 of the Events API; unmerged fix PR #317), so no single correction factor applies. Treat 2025-10-09 to about 2025-10-14 as near-empty (https://github.com/igrigorik/gharchive.org/issues/312).

**Risk:** the data has no explicit licence, and the AUP research allowance covers only *non-personal* information (Q1, Q3).

### TM-02: GitHub REST / GraphQL API

**Decision: CLEARED-WITH-CONDITIONS**

**Clauses relied on:** GitHub Terms of Service §H (https://docs.github.com/en/site-policy/github-terms/github-terms-of-service):
> "Abuse or excessively frequent requests to GitHub via the API may result in the temporary or permanent suspension of your Account's access to the API."
> "You may not share API tokens to exceed GitHub's rate limitations."
> "…for spamming purposes, including for the purposes of selling GitHub users' personal information."

AUP §7 applies as in TM-01.

**Conditions**
- Each operator uses only their own token. Tokens are never pooled.
- Stay within the documented rate limits (https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api).
- Pseudonymise user data.

### TM-03: Hacker News Algolia API

**Decision: CLEARED-WITH-CONDITIONS, pending Q4**

**Clauses relied on**
- From https://hn.algolia.com/api:
  > "We are limiting the number of API requests from a single IP to 10,000 per hour."
- From the Y Combinator Terms of Use (https://www.ycombinator.com/legal, last updated September 2026):
  > "you will not engage in or use any data mining, robots, scraping or similar data gathering or extraction methods."
  > "Unless otherwise expressly authorized herein or in the Site"

**Conditions**
- Use the API only; never scrape the HTML site.
- Stay at or below 10,000 requests per hour.
- Keep snapshots private and do not republish comment text.
- Pseudonymise usernames.

### TM-04: Hacker News Firebase API

**Decision: CLEARED-WITH-CONDITIONS, pending Q4**

**Clauses relied on**
- From https://github.com/HackerNews/API:
  > "There is currently no rate limit."
  > "The changes won't always be backward compatible"
- The YC Terms of Use apply as in TM-03.
- From https://news.ycombinator.com/robots.txt: `Crawl-delay: 30`. This applies to the website, not the API.

**Conditions**
- The same conditions as TM-03.
- Keep polling modest: at most one `topstories` poll per minute.
- Propagate the `deleted` flag.

### TM-05: Reddit Data API

**Decision: GAP**

**Clauses relied on**
- Data API Terms §3.1 (https://redditinc.com/policies/data-api-terms, revised 2026-07-20):
  > "If you are interested in using the Data APIs for commercial purposes … you will need to enter into a separate agreement with Reddit."
- Developer Terms (https://redditinc.com/policies/developer-terms, revised 2026-03-24):
  - §4.1 bars access:
    > "by or on behalf of a business or as part of a service or product that is monetized"
  - §7.2:
    > "You will not share Reddit Services and Data with any third party, except … to the extent required under applicable law or regulation."
  - §4.2 bars use:
    > "to train large language, artificial intelligence, or other algorithmic models or related services without our permission"
- Data API Wiki (https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki):
  > "You must remove any user content in your possession that has been deleted from Reddit."
  > "retention of content and data that has been deleted–-even if disassociated, de-identified or anonymized–-is a violation of our terms"
  > "100 queries per minute (QPM) per OAuth client id"
- Responsible Builder Policy (https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy):
  > "You must request access and get explicit approval before accessing any Reddit data through our API"

**Why this is a gap**
- Commercial use requires a separate agreement.
- Sending data to a third-party LLM provider would break §7.2.
- The deletion rules defeat pseudonymisation.
- Prior approval is required.
- The $0.24 per 1,000 calls price is **unverified**.

**Path forward:** an operator's own written agreement with Reddit (Q2).

### TM-06: Bluesky / AT Protocol

**Decision: CLEARED-WITH-CONDITIONS**

**Clauses relied on**
- Developer Guidelines (https://bsky.network/docs/developer-guidelines):
  > "All services must have a method for deleting content a user has requested to be deleted."
  > "Developers should maintain reasonable security measures to protect against unauthorized access or disclosure of any end-user information"
- Terms of Service (https://bsky.social/about/support/tos, 2025-08-14):
  > "We also will notify other services and Developer Applications on the AT Protocol that you have deleted your account."

**Conditions**
- Consume Jetstream delete and account events. Drop the raw copy within 48 hours; this window is our own default.
- Pseudonymise DIDs and handles.
- Encrypt the snapshot store.
- Use authenticated search with the operator's own credentials.
- Re-check if the User Intents opt-out proposal (https://github.com/bluesky-social/proposals/tree/main/0008-user-intents) is adopted.

### TM-07: PyPI downloads (BigQuery)

**Decision: CLEARED-WITH-CONDITIONS**

**Clauses relied on:** https://docs.pypi.org/api/bigquery/
> "The tables and its pertaining data are licensed under the Creative Commons License."
- The "Creative Commons License" link points to https://creativecommons.org/licenses/by/4.0/, so the licence is **CC BY 4.0**.
- The page also says "the rows in this BigQuery table are immutable and are not removed even if a release or project is deleted". That sentence describes the `distribution_metadata` table, not `file_downloads`, which pigtail uses. No deletion statement was found for `file_downloads`.

**Conditions**
- Attribute under CC BY 4.0: "PyPI / Linehaul (PSF), CC BY 4.0", with a link to the licence.
- Query by partition only, under a byte cap.
- Do not bulk-query pypistats.org (https://pypistats.org/api/).

### TM-08: npm downloads API

**Decision: CLEARED**

**Clauses relied on:** npm Open Source Terms (https://docs.npmjs.com/policies/open-source-terms):
> "You are free to use npm Open Source for commercial projects, to advance your career, and for other business purposes."
> "You may replicate data from the Public Registry using the Public APIs"
> "…five million requests to npm Services in a single month-long period… remotely reasonable."

**Operating limits:** stay far below 5 million requests a month, use chunked ranges and send a descriptive User-Agent.

### TM-09: crates.io

**Decision: CLEARED-WITH-CONDITIONS**

**Clauses relied on**
- From https://crates.io/data-access (source: https://github.com/rust-lang/crates.io/blob/main/svelte/src/routes/data-access/+page.svelte):
  > "A maximum of 1 request per second"
  > "A user-agent header that identifies your application. We strongly suggest providing a way for us to contact you"
- From https://crates.io/policies, which prohibits:
  > "using our servers for any form of excessive automated bulk activity, to place undue burden on our servers"

- The same page lists access methods to "try … in the order below": the crate index, crate content, RSS feeds, database dumps, and then the API.

**Conditions**
- Follow that order. Use the index, crate content, RSS and dumps or archive CSVs before the API.
- Keep API use to 1 request per second or less, with a contact in the User-Agent.
- Drop the `users` table from dumps.
- No licence for the dump data was found (Q6).

### TM-10: Homebrew analytics

**Decision: CLEARED-WITH-CONDITIONS**

**Clauses relied on**
- From https://docs.brew.sh/Analytics:
  > "Homebrew retains analytics events in InfluxDB for 365 days."
  > "does not contain a user identifier or an IP-address field"
- No terms and no data licence were found. The repository is BSD-2-Clause (https://github.com/Homebrew/formulae.brew.sh), which covers code only.

**Conditions**
- Fetch the bulk JSON once a day.
- Store aggregate counts only.
- Credit Homebrew.
- Open question: Q7.

### TM-11: Docker Hub

**Decision: CLEARED-WITH-CONDITIONS**

**Clauses relied on:** Docker Terms of Service (https://www.docker.com/legal/docker-terms-service/), which prohibit:
> "Use automated means… to access the Website or Services except as permitted by Docker (for example, through documented APIs and within published limits)."
> "Train competing artificial intelligence or machine learning models without our express written consent"
> "mirror or replicate content for an unauthorized commercial service."

**Conditions**
- Use the documented v2 API only, at a low daily rate. Usage limits are published at https://docs.docker.com/docker-hub/usage/.
- Store counters only.
- No training.

### TM-12: deps.dev

**Decision: CLEARED-WITH-CONDITIONS**

**Clauses relied on**
- From https://docs.deps.dev/api/v3/:
  > "This generated data is available under a CC-BY 4.0 license."
  > "Use of the deps.dev API is subject to the Google API Terms of Service."
- Google APIs Terms (https://developers.google.com/terms) prohibit, unless expressly permitted:
  > "Scrape, build databases, or otherwise create permanent copies of such content, or keep cached copies longer than permitted"

**Conditions**
- Use the BigQuery dataset for history. Use the API for point lookups only.
- Attribute under CC-BY 4.0.
- Open question: Q6.

### TM-13: Internet Archive Wayback Machine (CDX + Save Page Now)

**Decision: CLEARED-WITH-CONDITIONS; off by default for commercial operators until Q5 is answered**

**Clauses relied on**
- Terms of Use dated "31 Dec 2014". We verified them through the copy saved in archive.org item `05132021` (https://archive.org/download/05132021/Internet%20Archive%20Terms%20of%20Use.mhtml, re-read 2026-09-25). The live page at https://archive.org/about/terms.php could not be rendered, so whether this is still the current text is unverified.
  > "Access to the Archive's Collections is provided at no cost to you and is granted for scholarship and research purposes only."
  > "not to collect or store personal data about anyone"
- Save Page Now 2 API document, 2026-07-22 (https://docs.google.com/document/d/1Nsv52MvSjbLb2PCpHlat0gkzw0EvtSgpKHu4mk0MnrA): authenticated users get 7 captures per minute and 30,000 per day.

**Conditions**
- Look up and capture project-level URLs only. Never capture person profile pages.
- Wayback records carry no person-level fields.
- Use the operator's own S3 keys.
- Stay at or below 5 captures per minute and 5 per URL per day.

### TM-14: YouTube Data API

**Decision: GAP**

**Clauses relied on:** Developer Policies (https://developers.google.com/youtube/terms/developer-policies, updated 2026-09-14):
> "API Clients may temporarily store limited amounts of Non-Authorized Data … not longer than 30 calendar days."
> "must not … access or use API Data to create new or derived data or metrics."
> "Do not aggregate API Data or otherwise use API Data … to gain insights into YouTube's usage, revenue"

**Why this is a gap:** it conflicts with snapshot-or-drop, with the 24-month retention period and with LLM coding.

**Path forward:** audited analytics-developer status.

### TM-15: X API

**Decision: GAP**

**Clauses relied on**
- Developer Agreement (https://docs.x.com/developer-terms/agreement, 2026-04-27):
  - §III.M:
    > "beyond the scope of hobbyist projects, commercial prototyping, initial development … you must apply (or already subscribe to) an Enterprise plan"
  - §IV.B:
    > "delete or modify that X Content … within twenty four (24) hours after a written request"
  - §III.A.k:
    > "use the X API or X Content to fine-tune or train a foundation or frontier model"
  - §XIV.B:
    > "conducting or providing surveillance or gathering intelligence, including but not limited to investigating or tracking X users"
- Developer Policy (https://docs.x.com/developer-terms/policy):
  > "If you store X Content offline, you must keep it up to date with the current state of that content on X."
- Pricing (https://docs.x.com/x-api/getting-started/pricing): pay-per-use at $0.005 per post read, capped at 3 million reads a month. Enterprise pricing is not published.

**Why this is a gap:** commercial production use requires Enterprise. Q9 asks about the rules on tracking users.

### TM-16: Product Hunt API

**Decision: GAP**

**Clauses relied on**
- From https://api.producthunt.com/v2/docs:
  > "The Product Hunt API must not be used for commercial purposes. If you would like to use it for your business, please contact us at hello@producthunt.com."
- The site terms (https://www.producthunt.com/legal) bar anyone who:
  > "Copies or stores any significant portion of the Content"

**Path forward:** written permission from hello@producthunt.com.

### TM-17: Lobste.rs

**Decision: GAP**

**Clauses relied on**
- From https://lobste.rs/robots.txt: `User-agent: *` with `Disallow: /`, and:
  > "Content-Signal: ai-input=no, ai-train=no, search=yes"
- From https://github.com/lobsters/lobsters/blob/main/config/initializers/rack_attack.rb:
  > "You're a commercial service? Slow down or email me."
- No terms of service were found.

**Path forward:** email the operator, or use the database-query offer at https://lobste.rs/about.

### TM-18: dev.to (Forem API)

**Decision: GAP**

**Clauses relied on:** https://dev.to/terms
> "Permission is granted to temporarily download one copy of the materials… for personal, non-commercial transitory viewing only."
> "you may not: modify or copy the materials; use the materials for any commercial purpose…"
> "you must destroy any downloaded materials"

**Path forward:** written permission from Forem. Open question: Q10.

### TM-19: V2EX

**Decision: CLEARED-WITH-CONDITIONS; disabled by default until Q8 is answered**

**Clauses relied on**
- From https://www.v2ex.com/help/api:
  > "默认情况下，每个 IP 每小时可以发起的 API 请求数被限制在 600 次"
  - Translation: "by default each IP may make 600 API requests per hour".
- https://www.v2ex.com/robots.txt disallows only `/backstage/`, `/signin`, `/signout` and `/settings`.
- No terms of service were found.
- PIPL Art. 3 and Art. 53 (https://www.cac.gov.cn/2021-08/20/c_1631050028355286.htm) may apply to processing done abroad.

**Conditions**
- Each operator uses their own token.
- Stay at or below 300 requests per hour.
- Pseudonymise users.
- Search for project mentions only. Do not profile members.

### TM-20: Juejin

**Decision: GAP**

**Clauses relied on:** user agreement (https://juejin.cn/terms, in force 2025-12-24)
- §10.1:
  > "未经公司许可，任何人不得擅自使用（包括但不限于通过任何机器人、蜘蛛等程序或设备监视、复制…）"
  - Translation: "no one may use [content] without permission, including monitoring or copying via robots, spiders or similar programs".
- §2.8 grants the user a licence limited to:
  > "个人的、不可转让的、非独占的和非商业的合法使用"
  - Translation: "personal, non-transferable, non-exclusive, non-commercial lawful use".

### TM-21: Zhihu

**Decision: GAP**

**Clauses relied on**
- Zhihu agreement (https://www.zhihu.com/term/zhihu-terms, in force 2025-03-25), which bars:
  > "使用任何自动化程序、软件或类似工具接入知乎，收集或处理其中信息、内容"
  - Translation: "using any automated program, software or similar tool to access Zhihu and collect or process its information or content".
  > "不得抓取知乎上的内容或将抓取后的内容用于…大语言模型、其他人工智能…研发或数据训练"
  - Translation: "must not scrape Zhihu content or use scraped content for LLM or other AI R&D or training".
- https://www.zhihu.com/robots.txt sets `User-Agent: *` with `Disallow: /`.

**Path forward:** re-audit the official open platform (https://developer.zhihu.com). Its terms are unverified.

### TM-22: Bilibili

**Decision: GAP**

**Clauses relied on:** an official copy of the 2020-09-01 terms (https://game.bilibili.com/licence/h5/, §4.2.11):
> "未经哔哩哔哩事先明确书面许可，以任何方式（包括但不限于机器人软件、蜘蛛软件、爬虫软件等任何自动程序、脚本、软件）…获取平台的服务、内容、数据"
- Translation: "without Bilibili's prior express written permission, obtaining platform services, content or data by any means, including robot, spider or crawler software".
- A newer version dated 2025-04-30 (https://www.bilibili.com/protocal/licence.html) could not be rendered. Whether this clause is still current is unverified.

### TM-23: TrustMRR

**Decision: GAP**

**Clauses relied on:** https://trustmrr.com/terms (2026-08-26)
- §9.2:
  > "An API key does not grant a license to publish, index, sublicense, sell, redistribute, or make TrustMRR data available to third parties."
- §9.3 prohibits:
  > "Use API data to train, fine-tune, ground, evaluate, or populate an AI model, dataset, search index… without prior written permission."
  > "Scrape, harvest, archive, or systematically reconstruct TrustMRR's database"

**Path forward:** written permission covering private snapshots and aggregate use.

### TM-24: Crunchbase

**Decision: GAP (an operator can bring their own licence)**

**Clauses relied on**
- From https://data.crunchbase.com/docs/license-agreement:
  > "Licensee may only use the Crunchbase data for internal research and analysis."
- Data Access Terms §2 (https://data.crunchbase.com/docs/terms) prohibit:
  > "use or allow the Crunchbase Materials to be used to train models (including generative artificial intelligence technologies)"
  > "…in a manner that makes it impossible for the Crunchbase Materials to be expunged"
- Website terms (https://about.crunchbase.com/terms-of-service/) prohibit anything that:
  > "'Crawls,' 'scrapes,' or 'spiders' any page, data, or portion of… the Service or Content"

**Conditions if an operator brings their own licence**
- Internal use only.
- Keep the data in a separate store that can be deleted.
- Never use it in public mode.
- Never use it for training.

### TM-25: YC company directory

**Decision: GAP**

**Clauses relied on**
- Y Combinator Terms of Use (https://www.ycombinator.com/legal):
  > "you agree not to modify, copy, frame, scrape, rent, lease, loan, sell, distribute or create derivative works based on the Site"
- https://www.ycombinator.com/robots.txt: `Disallow: /companies?*`
- There is no official API. The unofficial `yc-oss/api` says it "uses the Algolia search index to fetch the companies in a GitHub Actions workflow that runs every day" (https://github.com/yc-oss/api). That this is YC's own site-search index is our inference. YC has not authorised it, so it is not an authorised source.

### TM-26: GitHub dependency graph (dependencies and dependents)

**Decision: dependencies through the APIs are CLEARED-WITH-CONDITIONS; the dependents page is a GAP**

**Clauses and facts relied on**
- The REST dependency-graph section covers dependency review, dependency submission and SBOM export (https://docs.github.com/en/rest/dependency-graph). No REST or GraphQL endpoint lists dependents. The GraphQL `Repository.dependencyGraphManifests` field lists *dependencies* only (schema introspected on 2026-09-25).
- The SBOM export endpoint "is closing down and will not be accessible after November 13, 2026" (https://docs.github.com/en/rest/dependency-graph/sboms).
- Dependents docs (https://docs.github.com/en/code-security/supply-chain-security/understanding-your-software-supply-chain/exploring-the-dependencies-of-a-repository):
  > "The dependent counts are approximate and may not always match the dependents listed."
- https://github.com/robots.txt, `User-agent: *`: `Disallow: /*/*/network`. This covers `/network/dependents`.
- AUP §7 (https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies):
  > "Scraping refers to extracting information from our Service via an automated process, such as a bot or webcrawler."
- API use falls under ToS §H, as in TM-02.

**Conditions (dependencies)**
- The same as TM-02.
- Migrate to the SBOM `generate-report` and `fetch-report` endpoints before 2026-11-13.

**Why dependents are a gap:** there is no API, robots.txt disallows the path, and the AUP research allowance does not clearly extend to commercial operators. Use deps.dev dependents (TM-12) instead.

### TM-27: Discord invite API (member counts)

**Decision: CLEARED-WITH-CONDITIONS; disabled by default for commercial operators until Q12 is answered**

**Clauses relied on**
- Endpoint docs (https://docs.discord.com/developers/resources/invite):
  - `with_counts`: "whether the invite should contain approximate member counts"
  - `approximate_member_count`: "approximate count of total members, returned from the `GET /invites/<code>`"
- Discord Developer Terms of Service, effective 2024-07-08 (https://support-dev.discord.com/hc/en-us/articles/8562894815383-Discord-Developer-Terms-of-Service):
  - §13:
    > "“API Data” means any data, information, or other content you obtain through the APIs (including personal data)."
  - §5.b: API Data must be deleted promptly when, among other cases,
    > "we request you delete it; (d) the applicable user requests you delete it"
- Discord Developer Policy, effective 2024-07-08 (https://support-dev.discord.com/hc/en-us/articles/8563934450327-Discord-Developer-Policy):
  - Item 15:
    > "Do not use API Data for any purpose outside of what is necessary to provide your stated functionality."
  - Item 18:
    > "Do not sell, license, or otherwise commercialize API Data"
  - Item 20:
    > "Do not mine or scrape any data, content, or information available on or through Discord services"
- Discord Terms of Service, last updated 2025-08-29 (https://discord.com/terms), ban:
  > "scraping our services without our written consent, including by using any robot, spider, crawler, scraper, or other automatic device, process, or software"
- Both developer pages return HTTP 403 to automated fetches. The text was read through the Help Center API (`/api/v2/help_center/en-us/articles/{id}.json`) on 2026-09-25.

**Conditions**
- Use only invite codes that the project itself publishes. Never enumerate codes.
- Use the operator's own registered Discord application, with a stated functionality and a privacy policy.
- Store only the server id, the approximate counts and the fetch time. Drop `inviter` and all user fields.
- At most one request per invite per day.
- Delete on a request from Discord, the server owner or the project, and delete everything if API access ends.
- Never sell or license the counts. Public outputs are aggregate only.

**Risk:** whether low-rate polling of a documented endpoint is "mining or scraping", and whether commercial analytics "commercialize[s]" API Data (Q12).

### TM-28: Slack invite links

**Decision: GAP (the metric is `unknown`)**

**Facts relied on**
- `team.info` needs a `team:read` token and returns no member count. Cross-workspace lookup "only works for domains in the same enterprise as the querying team token" (https://docs.slack.dev/reference/methods/team.info).
- No public endpoint for invite-link member counts was found (web search, 2026-09-25).
- Slack API Terms of Service, effective 2025-10-10 (https://slack.com/terms-of-service/api):
  > "you may not sell, rent, lease, sublicense, redistribute, or syndicate access to any of our APIs"

**Path forward:** the project's own workspace admin shares counts. These are `self_reported` unless the admin installs the operator's app for this purpose.

### TM-29: Careers pages (project websites)

**Decision: CLEARED-WITH-CONDITIONS, per site**

**Basis**
- There is no single platform. Each host's robots.txt (RFC 9309, https://www.rfc-editor.org/rfc/rfc9309) and site terms govern.
- Wayback lookups and captures fall under TM-13.

**Conditions**
- Prefer Wayback, under TM-13 (off by default for commercial operators until Q5 is answered).
- Fetch directly only if robots.txt allows the path and the site terms do not bar automated access. Record the check as `TM-29:<host>:<date>`.
- At most one fetch per page per day.
- Extract project-level facts only. Drop names and contacts of people.
- If the site disallows automated access, record a gap for that case. An operator may still enter the facts manually as `self_reported`.

### TM-30: HN "Who is hiring?" threads

**Decision: CLEARED-WITH-CONDITIONS, pending Q4 (the same basis as TM-03 and TM-04)**

**Basis**
- The threads are ordinary HN items posted by the `whoishiring` account (https://hacker-news.firebaseio.com/v0/user/whoishiring.json, observed 2026-09-25).
- They are retrieved through the Firebase and Algolia APIs only. The YC Terms of Use apply as in TM-03.

**Conditions**
- Use only "Who is hiring?" threads. Exclude "Who wants to be hired?" and "Freelancer?" threads, which hold job seekers' personal data.
- Pseudonymise commenters, and strip emails and names from the text.
- Store only the project, month and role counts.
- Do not republish comment text.

### TM-31: Press as a funding source

**Decision: CLEARED-WITH-CONDITIONS (manual entry only)**

**Basis:** there is no automated collection, so no platform API terms apply. Copyright limits copying, so pigtail stores only the citation (URL, date, a quote of no more than 25 words) and the extracted facts.

**Conditions**
- The operator enters every record.
- Label the data `self_reported`. It becomes `verified` only when a licensed source confirms it (for example TM-24 under the operator's own licence).
- Name investors as organisations only.
- Evidence copies are Wayback captures under TM-13, not private copies of articles.

### TM-32: OpenDigger GH-event mirror (gharchive issue #323)

**Decision: GAP, pending LQ-28.** Off by default (ADR-032). The research case for it is in `docs/research/detection-replan.md` §0, §5 and §7.

**What it is**
- A GH Archive-compatible archive of hourly `YYYY-MM-DD-H.json.gz` files with per-hour manifests (event counts, size, SHA-256), at `https://gharchive.open-digger.cn/`, starting on 2026-09-06 UTC. It was announced in https://github.com/igrigorik/gharchive.org/issues/323 (opened 2026-09-09 by an OpenDigger maintainer on its behalf).
- Coverage: much higher than GH Archive (57× the `WatchEvent`s on 2026-09-24). Its counts agree with GitHub's star-history for the repos it saw (1.013×), but its misses are unmeasured (detection-replan §7.4).

**Clauses and facts relied on**
- Issue #323 says the data is:
  > "publicly available for anyone to download and use"
- **Licence or terms:** none published, in the issue or found elsewhere (unverified beyond the issue and the file host). "Available to use" is not a licence, and it names no conditions, attribution or warranty.
- **Operator:** a single operator, OpenDigger (open-digger.cn), represented by one maintainer in the issue. No SLA, no contact for data-protection requests, no deletion process stated.
- **Hosting (measured 2026-09-25):** the files are served with `Server: AliyunOSS` and `x-oss-*` headers, i.e. Alibaba Cloud Object Storage Service. The region is not stated.
- **Collection rate:** the maintainer wrote on 2026-09-13 (same issue):
  > "maintaining a target overlap rate of 20% results in an interval of approximately 1.5 seconds between consecutive requests"
  and that the collector "automatically shortens the polling interval" when overlap falls.
- **GitHub's rule for the upstream API** (https://docs.github.com/en/rest/activity/events):
  > "There is also an "X-Poll-Interval" header that specifies how often (in seconds) you are allowed to poll."
  We measured `X-Poll-Interval: 60` on `/events` (detection-replan §1.1). About 1.5 s is roughly 40× that rate.
- **GitHub terms upstream:** ToS §H ("Abuse or excessively frequent requests to GitHub via the API may result in the temporary or permanent suspension…", "You may not share API tokens to exceed GitHub's rate limitations"; TM-02), and AUP §7 on reuse of information from the Service (TM-01). The data is the same kind as GH Archive's (actor logins, repo names, payloads) and is personal data about GitHub users.

**Analysis (not legal advice)**
- *Does the consumer inherit the collector's problem?* GitHub's terms bind whoever calls the API. Pigtail would not call `/events` at all for this source, so pigtail would not itself break §H. But: (a) pigtail would knowingly build on data that, on the operator's own account, was collected faster than GitHub says clients are "allowed to poll"; (b) GitHub could stop the collector at any time, so the source may vanish; (c) AUP §7 and the GitHub Privacy Statement apply to *use* of information from the Service, whoever collected it; (d) knowingly using data gathered in breach of a platform's terms may weigh against pigtail in the GDPR balancing test and in any contract or unfair-competition claim. Whether any of this exposes a commercial consumer is LQ-28.
- *Database rights:* if OpenDigger (or GitHub) has an EU sui generis database right (Directive 96/9/EC, Art. 7; text not fetched on 2026-09-25, **unverified quote**) in the compiled archive, extracting substantial parts without a licence could infringe. With no licence published, the default is "no licence granted". LQ-28.
- *Personal data:* the same as TM-01 (pseudonymise at ingest, 24-month cap, aggregates only in outputs). The files are large (≈ 300–410 MB compressed per hour), so CB-04 minimisation matters more here.
- *Stability:* high risk: three weeks old at the access date, single operator, no terms.

**Conditions if LQ-28 clears it** (from detection-replan §5)
- Stream-filter to `WatchEvent` and `ForkEvent`, pseudonymise actors in memory, store only repo-hour aggregates plus pseudonymised actor sets for bot filtering.
- Do not snapshot whole raw files; store hash plus manifest plus the filtered subset (CB-04; changes ADR-027 item 4 for this source).
- Screening and bot filtering only; never the scoring series.
- Attribute "OpenDigger". Stop if the operator publishes terms that forbid this, or if GitHub objects to the collector.
- Coverage is audited against a sample drawn from `U` or star-history, not from the mirror itself (detection-replan §8 M1).

**Until then:** the connector stays off. `evidence.terms_basis` must not cite TM-32 for any stored record.

### TM-33: GitHub star-history endpoint and per-repo Events API

**Decision: CLEARED-WITH-CONDITIONS (under TM-02).** The per-repo `WatchEvent.actor` use is pending LQ-29; the conservative conditions below apply meanwhile.

**Endpoints**
- `GET /repos/{owner}/{repo}/stargazers/history`: weekly and daily star counts back to the repo's creation, no identities (https://docs.github.com/en/rest/activity/starring?apiVersion=2026-03-10#get-repository-star-history; announced https://github.blog/changelog/2026-09-04-new-api-endpoint-provides-privacy-safe-star-history-data/). It contains no personal data.
- `GET /repos/{owner}/{repo}/events`: the latest 300 public events for one repo, including `WatchEvent`s with `actor` (https://docs.github.com/en/rest/activity/events). This is personal data.

**Clauses relied on**
- TM-02 (ToS §H, rate limits) and TM-01 (AUP §7).
- The Events API docs: "'X-Poll-Interval' header that specifies how often (in seconds) you are allowed to poll" (measured 60 s on per-repo events).
- GitHub restricted the stargazer *lists* on 2026-06-30 because they had "increasingly been misused to collect user data for spam activities" (https://github.blog/changelog/2026-06-30-upcoming-access-restrictions-to-public-api-endpoints-and-ui-views/). The per-repo Events API was not in the restricted list.

**Conditions**
- One operator token; no pooling; no App-plus-PAT doubling (ADR-032 item 4).
- Poll per-repo events no faster than `X-Poll-Interval` (ADR-032: every 15–60 min), with ETag; star-history at most daily per repo, serial queue, within primary and secondary limits.
- Per-repo events only for repos with an open case, tracked repos (R17.4) or repos above the pre-threshold.
- Pseudonymise `actor` at ingest; use identities only for aggregate bot and lockstep flags; never rebuild, store or export a stargazer list; keep person-level event rows at most 30 days, then aggregates only.

---

## Questions for the owner's lawyer (gate H2)

- **Q1 (GitHub / GH Archive):** Can a commercial operator lawfully store and analyse pseudonymised GitHub event data about individuals (actor logins, contribution timelines)? Consider GitHub AUP §7, which limits research use to "public, non-personal information", and GDPR/FADP legitimate interest. GH Archive gives no licence for its data. Does that matter?
- **Q2 (Reddit):** Reddit's Wiki says that keeping deleted content, "even if … de-identified or anonymized", violates its terms. If a Reddit commercial agreement is ever signed, could pigtail still keep a content hash plus coded, non-verbatim facts about deleted content (R1.5)? Does sending text to an LLM provider under a data processing agreement count as "sharing with a third party" under Developer Terms §7.2?
- **Q3 (GitHub ToS D.9 "Access Reciprocity"):** Is LLM *inference* on GitHub content outside this clause, which covers developing or training commercially available AI models?
- **Q4 (Hacker News):** Do YC's public API invitations (the HackerNews/API README and the Algolia API run with HN) count as "expressly authorized" under the YC Terms of Use? If so, commercial operators may use the API despite the ToU's bans on commercial reproduction and scraping. Is storing snapshots privately "reproduc[ing] … for commercial purposes"?
- **Q5 (Internet Archive):** Can a commercial operator use CDX lookups and Save Page Now for project-level pages, given the ToU's "scholarship and research purposes only" wording? Does requesting a public capture differ legally from using the collections?
- **Q6 (Open-data licences):** The PyPI BigQuery tables are CC BY 4.0 (link target on https://docs.pypi.org/api/bigquery/). Is that grant enough for commercial storage and analysis? Does deps.dev's CC-BY 4.0 grant override the Google APIs Terms clause against building databases? Are the crates.io dumps under any licence?
- **Q7 (No stated terms):** For sources with no terms or data licence (Homebrew analytics, V2EX, Lobste.rs apart from robots.txt), is low-rate API access to aggregate or public data acceptable? What attribution should we give?
- **Q8 (China PIPL):** Does pseudonymised processing in the EU or Switzerland of public posts by users in China (from V2EX, and any future Chinese source) trigger PIPL Art. 3(2)? If it does, it would also trigger the Art. 53 duty to appoint a representative in China. Is aggregate, project-mention-only use a safe harbour?
- **Q9 (X):** If an operator had an X Enterprise agreement, would case forensics that link accounts in a spread graph (R5.3) breach X's rules on "tracking X users" and surveillance (Developer Agreement §XIV.B)?
- **Q10 (dev.to):** Does publishing the Forem API imply a licence that overrides the site terms' "non-commercial transitory viewing only" wording for API data?
- **Q11 (General):** For people whose public posts appear in private snapshots, is our 24-month retention for person-level data, together with pseudonymisation at ingest, adequate under GDPR and the Swiss FADP? Is it compatible with each CLEARED-WITH-CONDITIONS source? The LIA and DPIA will be drafted in M3.
- **Q12 (Discord):** Under the Discord Developer Policy (items 18 and 20) and the Discord ToS, is a commercial operator allowed to poll the documented `GET /invites/{code}?with_counts=true` endpoint once a day for invite codes that projects publish themselves, and to store only the aggregate member counts for internal analysis? Does that count as "mining or scraping" or "commercializ[ing]" API Data?

New questions from TM-32 and TM-33 go straight into `legal-review-questions.md`: LQ-28 (OpenDigger mirror) and LQ-29 (stargazer identities from events after GitHub's list restriction).

---

## Changelog

- 2026-09-25 — corrections after verifier spot-check M2-T4. TM-01: issue #137 answer by the maintainer; #310, #312 and PR #317 added. TM-07: CC BY 4.0, and the immutability quote reassigned to `distribution_metadata`. TM-09: access order. TM-13: citation URL. TM-15: section labels §III.A.k and §XIV.B. TM-25: yc-oss/api quote. New memos TM-26 to TM-31. Q6 and Q9 updated; Q12 added.
- 2026-09-25 — new memos TM-32 (OpenDigger GH-event mirror: GAP pending LQ-28, off by default per ADR-032) and TM-33 (GitHub star-history endpoint and per-repo Events API: cleared with conditions under TM-02; actor use pending LQ-29). Research basis: `docs/research/detection-replan.md`.

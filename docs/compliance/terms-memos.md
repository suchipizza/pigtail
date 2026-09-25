# Per-source terms memos (TM-01 … TM-25)

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
  - The dataset itself has no stated licence (issue #137, https://github.com/igrigorik/gharchive.org/issues/137).
- From GitHub Acceptable Use Policies §7 (https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies):
  > "Researchers may use public, non-personal information from the Service for research purposes, only if any publications resulting from that research are open access."
  > "Your use of information from the Service must comply with the GitHub Privacy Statement."

**Conditions**
- Pseudonymise actors when data is ingested.
- Never sell or export personal information.
- Public outputs are aggregate only and open access.
- Enforce a BigQuery byte cap.
- Cross-check star counts against the GitHub API, because the data-quality issue is open (https://github.com/igrigorik/gharchive.org/issues/320).

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
> "the rows in this BigQuery table are immutable and are not removed even if a release or project is deleted."

**Conditions**
- Credit PyPI/PSF. The exact Creative Commons variant is **unverified** (Q6).
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

**Conditions**
- Prefer the database dumps and archive CSVs over the API.
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
- Terms of Use dated 2014-12-31. We verified them through a 2021 archived copy (https://ia801705.us.archive.org/26/items/05132021/Internet%20Archive%20Terms%20of%20Use.mhtml). The live page at https://archive.org/about/terms.php could not be rendered, so whether this is still the current text is unverified.
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
  > "use the X API or X Content to fine-tune or train a foundation or frontier model"
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
- There is no official API. The unofficial `yc-oss/api` is not an authorised source.

---

## Questions for the owner's lawyer (gate H2)

- **Q1 (GitHub / GH Archive):** Can a commercial operator lawfully store and analyse pseudonymised GitHub event data about individuals (actor logins, contribution timelines)? Consider GitHub AUP §7, which limits research use to "public, non-personal information", and GDPR/FADP legitimate interest. GH Archive gives no licence for its data. Does that matter?
- **Q2 (Reddit):** Reddit's Wiki says that keeping deleted content, "even if … de-identified or anonymized", violates its terms. If a Reddit commercial agreement is ever signed, could pigtail still keep a content hash plus coded, non-verbatim facts about deleted content (R1.5)? Does sending text to an LLM provider under a data processing agreement count as "sharing with a third party" under Developer Terms §7.2?
- **Q3 (GitHub ToS D.9 "Access Reciprocity"):** Is LLM *inference* on GitHub content outside this clause, which covers developing or training commercially available AI models?
- **Q4 (Hacker News):** Do YC's public API invitations (the HackerNews/API README and the Algolia API run with HN) count as "expressly authorized" under the YC Terms of Use? If so, commercial operators may use the API despite the ToU's bans on commercial reproduction and scraping. Is storing snapshots privately "reproduc[ing] … for commercial purposes"?
- **Q5 (Internet Archive):** Can a commercial operator use CDX lookups and Save Page Now for project-level pages, given the ToU's "scholarship and research purposes only" wording? Does requesting a public capture differ legally from using the collections?
- **Q6 (Open-data licences):** Which licence variant covers the PyPI BigQuery tables? Does deps.dev's CC-BY 4.0 grant override the Google APIs Terms clause against building databases? Are the crates.io dumps under any licence?
- **Q7 (No stated terms):** For sources with no terms or data licence (Homebrew analytics, V2EX, Lobste.rs apart from robots.txt), is low-rate API access to aggregate or public data acceptable? What attribution should we give?
- **Q8 (China PIPL):** Does pseudonymised processing in the EU or Switzerland of public posts by users in China (from V2EX, and any future Chinese source) trigger PIPL Art. 3(2)? If it does, it would also trigger the Art. 53 duty to appoint a representative in China. Is aggregate, project-mention-only use a safe harbour?
- **Q9 (X):** If an operator had an X Enterprise agreement, would case forensics that link accounts in a spread graph (R5.3) breach X's rules on "tracking X users" and surveillance?
- **Q10 (dev.to):** Does publishing the Forem API imply a licence that overrides the site terms' "non-commercial transitory viewing only" wording for API data?
- **Q11 (General):** For people whose public posts appear in private snapshots, is our 24-month retention for person-level data, together with pseudonymisation at ingest, adequate under GDPR and the Swiss FADP? Is it compatible with each CLEARED-WITH-CONDITIONS source? The LIA and DPIA will be drafted in M3.

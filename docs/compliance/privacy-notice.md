# Privacy notice: pigtail research on open-source project growth

> **Template status:** draft for legal review (gate H2). It is not legal advice and not yet published. Each operator must fill in the `[bracketed]` fields and publish the notice at a stable public URL before collecting any person-level data beyond GH Archive (CB-12). Delete this box when publishing.

**Last updated:** [date] · **Version:** 0.1 (draft 2026-09-25)

## Who we are

This deployment of pigtail is run by **[operator]** ("we"), [address], [country].
Contact for privacy questions: **[privacy contact e-mail or form]**.
[If applicable: our representative in the EU / Switzerland: [name, address]. Our data protection officer: [name, contact].]

pigtail is open-source software (https://github.com/suchipizza/pigtail). Each organisation that runs pigtail decides what it collects and is responsible for its own copy. This notice covers **our** deployment only.

## What this is about, in short

We study **how open-source software projects grow**: which launches, posts and communities help a project gain users, and which don't. To do that we look at **public** activity around software projects on a few platforms.

- We are interested in **projects**, not in you as a person.
- We replace usernames with random-looking codes (pseudonyms) as soon as we process the data.
- We never publish information that identifies you. We don't sell data or use it to advertise to you, and we don't use it to train AI models.

## What data we collect, and from where

| Source | What we may collect about you | When |
|---|---|---|
| **GitHub**, via the public GH Archive dataset and the GitHub API | Your public username and account id, and public actions on repositories (starring, forking, issues, pull requests, comments) with their times. The raw public event files also contain the text of public issues and comments. | When your public activity touches a repository we study |
| **Hacker News** (official APIs) | Your username, and the public posts and comments that mention a project we study, with times and points | Only for projects we analyse in depth |
| **Bluesky** (AT Protocol) | Your handle and DID, and public posts that mention a project we study, with times and engagement counts | Only for projects we analyse in depth |
| **V2EX** (official API) | Your username, and public topics and replies that mention a project | Off by default |
| Package registries, Wayback Machine, Discord invite counts | Project-level figures only (downloads, archived project pages, member counts). Nothing about you. | — |

We do **not** collect data from Reddit, X/Twitter, YouTube, Product Hunt, Lobste.rs, dev.to, Juejin, Zhihu or Bilibili.

We do not try to collect sensitive information (for example health, political or religious views). If a public post happens to contain some, we do not extract or use it.

If your username is part of a project's name (e.g. `yourname/project`), that project name is kept as project data.

## Why we use it, and on what legal basis

| Purpose | Legal basis (EU/EEA: GDPR) | Switzerland (FADP) |
|---|---|---|
| Research and statistics on how open-source projects grow, and the evidence behind each finding | Legitimate interests (Art. 6(1)(f) GDPR): scientific and statistical research, freedom of information, and helping maintainers launch projects on evidence | Overriding interest (Art. 31 FADP), in particular research and statistics not related to specific persons (Art. 31(2)(e)) |
| Checking that each claim is backed by the original public source ("snapshot") | Same | Same |
| Filtering fake stars and bots | Same | Same |
| [If the operator is a company:] Competitive and market analysis about software projects | Legitimate interests (Art. 6(1)(f)) | Overriding private interest (Art. 31(1)) |

We have written a legitimate-interest assessment and a data protection impact assessment. You can ask us for a copy [or read them at: link].

We do **not** make decisions about you based on this data, and we do not build profiles of individuals.

## How we protect it

- Usernames are replaced with **keyed pseudonyms** when processed. The key is stored separately.
- Original public copies ("snapshots") are kept in **private storage** only, [encrypted at rest].
- Before any text is sent to an AI model, we remove e-mail addresses and phone numbers and replace @mentions with pseudonyms.
- Only [operator]'s authorised staff can access the data, through a private, password-protected interface.
- Anything we publish is **aggregated and anonymous**: statistics and patterns about projects, never your name, posts or connections.

## Who receives it

- **Hosting and storage providers** that work for us: [provider names, location, e.g. "Hetzner, Germany" / "Exoscale, Switzerland"].
- **AI model provider (Anthropic).** We use Claude models to classify public posts, for example "is this post a launch announcement?". What is sent is the post text with identifiers removed as described above. How Anthropic handles it depends on how we connect:
  - [Keep one or both, depending on the deployment's `LLM_BACKEND`.]
  - **Through the Anthropic API (our business account).** Anthropic processes the text **on our behalf** under a data processing agreement, does not use it to train models, and by default deletes it within 30 days [or: does not store it, under a zero-data-retention agreement]. Anthropic may keep content flagged for safety review for up to 2 years.
  - **Through our own Claude subscription (Claude Code).** Anthropic Ireland Limited receives the text under Anthropic's consumer terms and privacy policy (https://www.anthropic.com/legal/privacy). We have switched off the use of our data for model training. Anthropic keeps it for up to 30 days, longer if it is flagged for safety review (up to 2 years). Content that is flagged for safety review may also be used for training even with training switched off.
- **Authorities**, only if the law requires it.

We never sell your data or give it to advertisers or data brokers.

## International transfers

Our storage is located in **[EU / Switzerland]**. Anthropic processes data in the **United States**. These transfers are covered by:
- **API:** the EU Standard Contractual Clauses and the Swiss addendum in Anthropic's data processing agreement.
- **Subscription:** the safeguards described in Anthropic's own privacy policy (standard contractual clauses and adequacy decisions).

You can ask us for a copy of the relevant safeguards.

## How long we keep it

- **Original public copies and anything linked to your pseudonym:** at most **24 months** after we collected it. After that we delete it or keep only anonymous totals. [Raw GitHub event files: at most 30 days.]
- **If you delete a post or your account on Bluesky:** we delete our copy within **48 hours** of the platform telling us. For Hacker News deletions: within 7 days of our next check. We keep only a fingerprint (hash) and anonymous facts such as "a post appeared on this date", never the text or your pseudonym.
- **Project-level data** (such as star counts, downloads and project names) is kept without a time limit.
- **Backups** expire after 35 days, and deletions are re-applied if a backup is ever restored.

## Your rights

You can, free of charge:
- **Object** to our use of your data. We will stop and delete it, and we won't ask you to justify your request. We will also stop collecting your future public activity.
- **Ask for access** to the data we hold about you and a copy of it.
- **Ask for erasure** or **correction**, or ask us to **restrict** processing.
- **Complain** to a data protection authority:
  - in the EU/EEA, the authority where you live or work, or [lead authority];
  - in Switzerland, the Federal Data Protection and Information Commissioner (FDPIC, https://www.edoeb.admin.ch).

**How to exercise them:** write to **[privacy contact]** with the platform and your username (e.g. "Bluesky: @name.bsky.social"). For an access request we may ask you to prove the account is yours, for example by posting a code we send you. For objection or erasure we don't need proof. We reply within **one month**.

We cannot find you by your real name or e-mail address alone, because we don't store those. Please tell us your username.

## Why you weren't told directly

We collect public data about a very large number of people and we don't have their contact details, so writing to everyone individually is impossible or would take disproportionate effort. That is why we publish this notice instead (GDPR Art. 14(5)(b); FADP Art. 20(2)).

## Changes

We will post changes here with a new date. Significant changes will also be noted in [the project's changelog / our website].

# How Pigtail researches a product

Pigtail turns public evidence into a growth history. This page explains each step and the rules
that keep the result honest.

## The pipeline

```text
target (repository URL or domain)
  → resolve identity            which product/repository is this, exactly?
  → repository history          (repositories only) GitHub data, star history, releases, Hacker News posts
  → discover sources            web search + the product's own blog feed and sitemap + your --source URLs
  → choose sources              the model keeps sources about this exact product and drops look-alikes
  → fetch evidence              pages are read in memory, following each site's robots.txt and Pigtail's source policies
  → extract claims              small factual statements, each with a word-for-word quote from the page
  → verify claims               a claim is dropped if its quote cannot be found in the page
  → reconstruct the timeline    events, metrics, people, conflicts
  → interpret growth            stages, strategy phases, tactics, growth engines, outcomes
  → analyze repository growth   growth episodes, launch episodes, post-launch windows
  → write the narrative         summary, origin, first users, mechanism, takeaways
  → record gaps                 what is unknown or could not be checked
  → validate + write bundle     the Research Bundle must pass every integrity check
  → render report               HTML built only from the bundle
```

## Rules that cannot be switched off

**Every displayed fact traces to a source.** A report item points to one or more *claims*. Each
claim has *evidence links*, each pointing to a *source* (the URL) and a *source fetch* (when Pigtail
read it, the HTTP status, a content hash) and a *locator* (the quote or JSON path). Click any small
number in the report to see that chain.

**Quotes are verified.** When the model extracts a claim, it must copy a supporting quote from the
page. Pigtail checks that the quote is really in the page text; if not, the claim is discarded.

**Numbers are verified.** A metric (for example "MRR $20,700 in June 2022") is kept only if the
number appears in a claim it cites.

**Dates are not invented.** If a source says "March 2021", the date has month precision; Pigtail
does not pick a day. Dates that disagree with the cited claims are replaced by the claim's date.

**Timing is not causation.** If a Show HN post and a jump in stars happen in the same week, the
report says they were *observed together*. An outcome is marked as caused by something only when
the company or founder says so in a cited first-party source ("company-attributed") or it was
directly measured. Everything else is at most "weakly associated".

**Conflicts are kept.** If two sources give different numbers or dates, both are kept and shown as
a conflict. Pigtail never averages them or silently picks one.

**Unknown is a valid answer.** Missing information is recorded as a *gap*, never as a negative fact.
A growth spike with no matching public event is shown as *"No high-confidence public event found."*
If the evidence is too thin, the report says *"Insufficient public evidence for a useful Pigtail
forensic."* instead of filling the page.

**No allegations as facts.** Claims that only report an unproven allegation are dropped at
extraction. Pigtail never infers misconduct, fraud, intention or motive. Negative events
(security incidents, reversals) are flagged as sensitive; published examples show them only after
human review.

## Repository analysis

- **Star history** comes from GitHub's star-history endpoint, which returns daily counts of the
  current stargazers (no identities). It is marked `exact`. People who un-starred are not counted,
  and GitHub's day boundaries may not match UTC exactly.
- **Growth episodes** are days where new stars far exceed the previous four weeks' average (at
  least 4× plus a minimum size). Detection looks only at the star numbers, never at events.
- **Related events** are those in the three days before or during an episode. This is timing only.
- **Launch episodes** group launch-like events within four days of each other (Show HN, Product
  Hunt, an announced launch, a Reddit post, or a Hacker News post with 100+ points; releases join a
  group but never form one alone).
- **Post-launch windows** report stars the day before and +24h, +48h, +7 days, +30 days and +90 days
  later when those dates have passed.

## What the model does and does not do

The model (Claude by default) is used to choose sources, extract claims, group claims into
events/tactics/engines, and write the narrative. It sees claims by reference (`c12`) and can only
cite those references; anything citing an unknown reference is dropped. It never sees the bundle's
IDs and cannot add URLs: search results come from the search tool's own result blocks, and the
model's selection is by index.

The narrative marks how much is interpretation: *stated in sources*, *strong*, *moderate* or *weak
inference*.

## Known blind spots

- Product Hunt, Reddit and X are **link-only** (see [source-policies.md](source-policies.md)).
  Launches there are shown only when another permitted source, such as the maker's blog, states them.
- Hacker News posts are found by URL and name; posts that mention neither are missed.
- Private or deleted information, internal metrics and unpublished experiments are unknown.
- Company-reported numbers are shown as reported; they are not audited.

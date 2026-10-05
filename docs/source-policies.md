# Source policies

Pigtail reads public sources to support its own analysis. It does not republish or archive them.
What it may do with each platform is written down in version-controlled files in
[`source-policies/`](../source-policies). The engine reads these files at runtime and obeys them.

## Current policies

| Platform | File | Coverage tier | Read automatically? | Kept | Shown in reports |
|---|---|---|---|---|---|
| GitHub | `github.yaml` | A | Yes, via the API | metadata, counts, ≤500-char excerpt | paraphrase, link, short excerpt |
| Hacker News | `hacker-news.yaml` | B | Yes, via the HN Search API by Algolia | title, author, date, points, comments | paraphrase, link, title |
| Other web pages (blogs, news, docs) | `web.yaml` | C | Yes, one request per page, robots.txt respected | metadata, hash, ≤280-char quote | paraphrase, link, short quote |
| Reddit | `reddit.yaml` | C | **No** — link only | link and search-result title | link only |
| Product Hunt | `product-hunt.yaml` | C | **No** — link only | link and search-result title | link only |
| X (Twitter) | `x.yaml` | C | **No** — link only | link and search-result title | link only |

**Coverage tiers**

- **A** — structured, historical data Pigtail can rely on (GitHub star history, releases).
- **B** — useful discovery, but not a complete record (Hacker News posts are found by URL and name).
- **C** — supporting evidence or link-only.

Pigtail never claims complete coverage of a platform it cannot fully search.

## Why Reddit, Product Hunt and X are link-only

Their APIs need registration and have terms that have not yet been reviewed for this use. Until
they are, Pigtail does not fetch their content. If a web search finds a Reddit thread or a Product
Hunt page, the report lists the link and records a gap saying it was not read. A Product Hunt
launch can still appear on the timeline when a permitted source, such as the maker's own blog post,
describes it.

## Policy file format

```yaml
key: github
version: "1"
display_name: GitHub
coverage_tier: A
access:
  discovery_allowed: true
  automated_retrieval_allowed: true
  method: api
  authentication: optional
  rate_limit_notes: "..."
retention:
  mode: metadata_and_excerpt        # metadata_only | metadata_and_excerpt | full_permitted
  allow_full_artifact: false
  max_excerpt_chars: 500
public_display:
  mode: paraphrase_link_excerpt     # link_only | paraphrase_and_link | paraphrase_link_excerpt
  attribution_required: true
  notes: null
obligations:
  deletion_refresh: null
  terms_url: "https://..."
  reviewed_at: "YYYY-MM-DD"
implementation:
  adapter: github
  enabled_by_default: true
  notes: null
```

The values in these files are conservative engineering defaults. They are **not legal advice** and
are marked as pending legal review where that applies. Changes to a policy go through a pull request
like any other change.

## How the engine enforces them

- The fetcher refuses to download a source whose policy does not allow automated retrieval and
  records the fetch as `skipped_policy`.
- Excerpts are clipped to `max_excerpt_chars`; link-only sources get no excerpt at all. The bundle
  validator rejects a bundle that contains an excerpt from a link-only source.
- `retained_artifact_path` is always empty: no policy currently permits keeping full copies, and the
  validator rejects a bundle that claims to keep one when the policy forbids it.

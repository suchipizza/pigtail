# Changelog

All notable changes to this project are listed here. Versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Added
- Choosing the Claude model is easier: `--model` takes a short name (`sonnet`, `opus`, `haiku`,
  `fable`) or a plain model ID (`claude-opus-5-5`); the old `anthropic/<id>` form still works. The
  default stays Claude Sonnet 5.5. New `pigtail models` lists the choices with prices; `pigtail doctor
  --model <name>` checks the model is available to your key (free); an unknown name stops before any
  paid work; each run prints the model it uses. Documented in the README, quick start and troubleshooting.
- Publication gate for Pigtail-hosted reports (`src/pigtail/publication/`, `scripts/publication_gate.py`,
  [docs/publication-gate.md](docs/publication-gate.md)): turns a Research Bundle into a minimized public
  projection with an audit, a file-based human review and a hash-bound manifest. Rules PUB-001…PUB-018.
- `scripts/publish_example.py` now copies only gate output with a current PASS manifest;
  `scripts/check_published_examples.py` and the website build refuse anything else.
- `examples/reviewed/NOTICE.md` (independence and third-party content notice).
- `scripts/ai_review.py`: owner-authorized AI reviewer for publication-review items, recorded as AI
  decisions; examples carry `reviewed_by` in their metadata (not shown on the website).
- Names shared by people with different roles (e.g. a surname) go to review instead of being guessed.
- Owner review of Superhuman (2026-10-07): founders and executives are named in their role, private
  individuals anonymised; real source titles kept; header shows company-reported figures only; relayed
  third-party figures labelled as such; self-descriptions quoted; Wikipedia pages undated.
- Owner review of Tally (2026-10-07): personal-life details (family, relationships, health) are removed
  like personal finances, including in excerpts; security incidents count as negative-sensitive; setbacks
  in outcomes, company stages and summaries stay attributed, and first-party setback claims are labelled
  "According to the company"; sales-pitch self-descriptions need a neutral description; "hiring X as
  marketing manager" reads "hiring a marketing manager"; "Filip and a co-founder" names the other founder.
- Owner review of Hatchet (2026-10-07): setbacks a company described about itself stay attributed
  (PUB-009); a generic "the founder" is written as the named founder when it is clear which one;
  "introduced X as its maintainer" no longer turns into "the maintainer … as its maintainer"; estimate and
  company-database sites (GetLatka, Dealroom…) are not cited at all; YC pages are a "YC directory listing";
  titles that only repeat the site name need review, and `scripts/fill_source_titles.py` fills in each
  page's heading; listing pages are undated; conflicts that lost a claim need review.
- The web fetcher takes a post's own heading when the page metadata title is only the site name.
- From the first owner review (2026-10-07): reviewer edits persist by object and survive finding changes
  (and can be added by hand); company-reported figures require a first-party source (estimate sites are
  "third-party estimate"); uncited sources are not listed; title-only sources are labelled; summaries are
  never "Stated in sources"; better role wording; the target name follows its sources' spelling; the
  footer says "automated publication checks".

### Fixed
- Reddit posts about a project were missed in growth analysis. Pigtail now also searches Reddit by
  repository path (`site:reddit.com "owner/repo"`), keeps maker posts whose titles omit the project's
  name when the search that found them names the repository, and puts dated Reddit posts on the
  timeline (title, link and search-result date only; posts are still not read) so they line up with
  growth episodes and launches. "N weeks ago" dates are kept as a one-week range.
- Launch rows before a repository had any stars show "not public yet" instead of +0 (also in local reports).

### Changed
- `examples/reviewed/` holds publication-gated output only. All five examples are republished through
  the gate; their review decisions were made by an AI on the owner's behalf (human review pending).

`pigtail analyze` and locally generated reports are unchanged.

## [0.1.0] — 2026-10-06

First public release (Light V1).

### Added
- `pigtail <github-url>` and `pigtail <domain>`: research a repository or product and write a
  Research Bundle (schema 0.1.0) plus a standalone HTML growth report.
- GitHub star history with launches, posts and releases on one time axis; growth episodes, launch
  episodes, post-launch windows and repeated-launch comparison.
- Claim extraction with verbatim-quote verification; conflicts and gaps preserved.
- `pigtail render`, `pigtail validate`, `pigtail doctor`, `pigtail version`.
- Version-controlled source policies (GitHub, Hacker News, web enabled; Reddit, Product Hunt, X link-only).
- Default model: Anthropic Claude Sonnet 5.5 (`--model anthropic/claude-opus-5-5` for the larger model).
- Five reviewed example reports in `examples/reviewed/` and a static website in `site/`.

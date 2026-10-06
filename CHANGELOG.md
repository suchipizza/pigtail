# Changelog

All notable changes to this project are listed here. Versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Added
- Publication gate for Pigtail-hosted reports (`src/pigtail/publication/`, `scripts/publication_gate.py`,
  [docs/publication-gate.md](docs/publication-gate.md)): turns a Research Bundle into a minimized public
  projection with an audit, a file-based human review and a hash-bound manifest. Rules PUB-001…PUB-018.
- `scripts/publish_example.py` now copies only gate output with a current PASS manifest;
  `scripts/check_published_examples.py` and the website build refuse anything else.
- `examples/reviewed/NOTICE.md` (independence and third-party content notice).

### Changed
- `examples/reviewed/` holds publication-gated output only. Hatchet is published; Plausible, PocketBase,
  Tally and Superhuman were removed until their publication review is done.

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

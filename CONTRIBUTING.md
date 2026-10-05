# Contributing to Pigtail

Thanks for helping. Pigtail is small enough to understand in an afternoon; start with
[docs/architecture.md](docs/architecture.md).

## Set up

```bash
git clone https://github.com/suchipizza/pigtail && cd pigtail
uv sync --all-groups          # installs Python 3.12+ deps and dev tools
uv run pytest                 # no API keys needed
```

To run real analyses you need `ANTHROPIC_API_KEY` (and optionally `GITHUB_TOKEN`) in your
environment or a `.env` file. While iterating on prompts or the renderer, `PIGTAIL_DEV_CACHE=1`
caches model responses locally in `.pigtail-cache/` so repeated runs are free.

## Before opening a pull request

```bash
uv run ruff check src tests && uv run ruff format src tests
uv run mypy
uv run pytest
uv run python -m pigtail.bundle.schema --check
python scripts/secret_scan.py
```

## Ground rules

- **Evidence first.** New features must keep the chain report → claim → evidence → source → fetch.
  Never add a code path that lets the model introduce URLs, numbers or dates that are not in a source.
- **Timing is not causation.** Do not add wording, styling or logic that implies an event caused a
  metric change unless a cited source says so.
- **No hidden archives.** Do not store page bodies or screenshots of third-party content. Excerpts
  must go through the source policy.
- **Bundle changes are contract changes.** If you change `src/pigtail/bundle/models.py`, regenerate
  the schema (`python -m pigtail.bundle.schema --write`) and explain why in the PR. Changing the
  meaning of an existing field needs a new schema version.
- **No product-specific hacks.** Fixes should help every target, not one company.
- **Source policies need care.** Enabling a new platform requires a policy file with its access,
  retention and display rules, and a note on how they were reviewed.

## Good first contributions

- Better parsing for a blog platform's feed or sitemap.
- Clearer wording in the report or docs.
- Test cases for growth-episode detection with real star histories.
- Reporting a wrong claim in an example report (include the source link).

## Reporting a bad claim

If a report states something the linked source does not support, please open an issue with the
claim text and the source URL. These reports are the most valuable feedback the project gets.

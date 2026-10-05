# Quick start (about 5 minutes)

This guide takes you from nothing to your first growth report.

## 1. What you need

- **Python 3.12 or newer.** Check with `python3 --version`.
- **An Anthropic API key.** Pigtail uses Claude to read sources and to search the web.
  Create a key at <https://console.anthropic.com/>. A typical run costs about **$1–3** in API usage.
- **Optional: a GitHub token.** Without one, GitHub allows 60 requests per hour, which is enough
  for one or two repositories. With one, you get 5,000 per hour. A token with no extra permissions
  is enough: <https://github.com/settings/tokens>.

You do **not** need a Pigtail account, a database, Docker, or any other service.

## 2. Install

The easiest way is [uv](https://docs.astral.sh/uv/) or [pipx](https://pipx.pypa.io/), which keep
Pigtail in its own environment:

```bash
uv tool install git+https://github.com/suchipizza/pigtail
# or
pipx install git+https://github.com/suchipizza/pigtail
```

Plain pip also works inside a virtual environment:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install git+https://github.com/suchipizza/pigtail
```

## 3. Add your keys

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export GITHUB_TOKEN="ghp_..."        # optional
```

Pigtail also reads a `.env` file in the folder you run it from. Never put keys in `pigtail.toml`.

## 4. Check your setup

```bash
pigtail doctor
```

Every line should say `OK` (a `WARN` for the GitHub token is fine). Doctor does not spend money.

## 5. Run your first analysis

```bash
pigtail https://github.com/plausible/analytics
```

You will see the stages as they run:

```text
› Resolve target…
› Repository history…
› Discover sources…
› Fetch evidence…
› Extract claims…
› Reconstruct timeline…
› Analyze growth…
› Validate evidence and gaps…
› Write Research Bundle…
› Render report…
```

This takes about 3–5 minutes. When it finishes, the report opens in your browser. The files are in
`./pigtail-output/<name>-<id>/`:

| File | What it is |
|---|---|
| `report.html` | The growth report. Open it in any browser; it works offline. |
| `research-bundle.json` | Everything Pigtail found, as structured data with sources. |
| `source-index.json` | A simple list of the sources, for convenience. |
| `run.json` | Cost, time and counts for this run. |

A product website works the same way:

```bash
pigtail tally.so
```

## 6. Useful options

```bash
pigtail <target> --model anthropic/claude-sonnet-5-5   # pick a different model
pigtail <target> --output ./reports                    # choose where reports go
pigtail <target> --source https://example.com/post     # add a source you know about (repeatable)
pigtail <target> --no-open                             # don't open the browser at the end
```

Sources you add with `--source` are checked like any other source. Pigtail does not assume they
are true.

## 7. Re-render or check a bundle

```bash
pigtail render pigtail-output/plausible-1234abcd/research-bundle.json
pigtail validate pigtail-output/plausible-1234abcd/research-bundle.json
```

Neither command needs API keys or a network connection.

Something not working? See [troubleshooting.md](troubleshooting.md).

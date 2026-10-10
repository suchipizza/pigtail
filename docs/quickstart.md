# Quick start (about 5 minutes)

This guide takes you from nothing to your first growth report.

## 1. What you need

- **Python 3.12 or newer.** Check with `python3 --version`.
- **An Anthropic API key.** Pigtail uses Claude to read sources and to search the web.
  Create a key at <https://console.anthropic.com/>. A run with the default model (Claude Sonnet 5.5) should cost roughly $0.50–2.50; with Claude Opus 5.5 we measured $1–3 for a repository and $2–5 for a product website.
- **Optional: a GitHub token.** Without one, GitHub allows 60 requests per hour, which is enough
  for one or two repositories. With one, you get 5,000 per hour. A token with no extra permissions
  is enough: <https://github.com/settings/tokens>.

- **Optional: Reddit app keys (free).** Without them the report has **no Reddit data**: Reddit posts and
  launches are missing, and growth explanations rest on Hacker News, GitHub and the web only. See
  step 3.

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

**Optional: Reddit API.** By default, Reddit posts appear in a report only when web search finds them
(rare). If Reddit has approved API access for you, Pigtail can also search Reddit through its official
API with your own app keys, keeping only each post's title, link, subreddit, date, upvotes and comment
count. New API access needs Reddit's approval: see its
[Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy).
Creating an app at <https://www.reddit.com/prefs/apps> is not enough on its own. With approved keys:

```bash
export REDDIT_CLIENT_ID="..."      # the short string under your app's name
export REDDIT_CLIENT_SECRET="..."
pigtail doctor                     # the "Reddit API" line should say OK
```

**Optional: Product Hunt API.** With your own free developer token, reports add each Product Hunt
launch with its exact date, upvotes and comments. Get it at
<https://www.producthunt.com/v2/oauth/applications>: **Add an application** (any name, `https://localhost`
as redirect URI), then **Create Token** and copy the developer token:

```bash
export PRODUCTHUNT_TOKEN="..."
```

Their terms apply to you, including limits on commercial use; the data stays on your computer. Each
run starts by saying whether the Reddit API is on. Without keys, the report notes that Reddit
coverage is incomplete.

## 4. Check your setup

```bash
pigtail doctor
```

Every line should say `OK` (a `WARN` for the GitHub token or the optional Reddit keys is fine). Doctor
does not spend money.

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

This takes about 2–6 minutes. When it finishes, the report opens in your browser. The files are in
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
pigtail <target> --model opus                         # use the larger model (about twice the cost)
pigtail <target> --output ./reports                    # choose where reports go
pigtail <target> --source https://example.com/post     # add a source you know about (repeatable)
pigtail <target> --no-open                             # don't open the browser at the end
pigtail <target> --no-reddit                           # skip Reddit even if your Reddit keys are set
pigtail <target> --no-producthunt                      # skip Product Hunt even if your token is set
```

### Choosing the Claude model

Without `--model`, Pigtail uses Claude Sonnet 5.5. To use another model, pass a short name or a
full Claude model ID. `pigtail models` lists the choices, and `pigtail doctor --model <name>` checks
that the model works with your key without spending anything.

| `--model` | Model | $ per 1M tokens (in / out) | Notes |
|---|---|---|---|
| `sonnet` (default) | Claude Sonnet 5.5 (`claude-sonnet-5-5`) | $2 / $10 | Used when you don't pass `--model`. Balanced cost and quality. |
| `opus` | Claude Opus 5.5 (`claude-opus-5-5`) | $4 / $20 | About twice the cost. Measured $1–3 per repository, $2–5 per product website. |
| `haiku` | Claude Haiku 4.5 (`claude-haiku-4-5`) | $1 / $5 | Cheapest. Not tested with Pigtail yet; reports may be thinner. |
| `fable` | Claude Fable 5.1 (`claude-fable-5-1`) | $10 / $50 | Most capable, about 5x the default cost. Not tested with Pigtail yet. |

Sources you add with `--source` are checked like any other source. Pigtail does not assume they
are true.

## 7. Re-render or check a bundle

```bash
pigtail render pigtail-output/plausible-1234abcd/research-bundle.json
pigtail validate pigtail-output/plausible-1234abcd/research-bundle.json
```

Neither command needs API keys or a network connection.

Something not working? See [troubleshooting.md](troubleshooting.md).

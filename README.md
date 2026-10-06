<h1 align="center">pigtail<span>.</span></h1>

<p align="center"><b>See how a product actually grew — from public evidence, with every fact linked to its source.</b></p>

<p align="center">
  <a href="https://suchipizza.github.io/pigtail/">Website</a> ·
  <a href="https://suchipizza.github.io/pigtail/examples/">Example reports</a> ·
  <a href="docs/quickstart.md">5-minute quick start</a> ·
  <a href="docs/methodology.md">How it works</a> ·
  <a href="docs/">Docs</a>
</p>

Pigtail is a command-line tool. You give it a **GitHub repository** or a **product website**. It
reads public evidence — the repository's history, launch posts, Hacker News threads, the founders'
blog posts, interviews and articles — and writes a **growth report**: how the product started, how
it found its first users, what it launched and when, which numbers moved, which tactics it used,
and what is still unknown.

For open-source projects, the report shows the **GitHub star history with launches, posts and
releases on the same timeline**, so you can see what was happening around every jump in stars:

![GitHub stars and public events on one timeline](docs/assets/stars-and-events.png)

Click any marker or any small citation number to see the evidence behind it: the claim, the quote,
the source link and when Pigtail read it.

A 20-second walkthrough (run → report → star timeline → evidence): [docs/assets/demo.gif](docs/assets/demo.gif)

**Who it is for:** open-source maintainers planning a launch, founders and growth people studying
how comparable products grew, and anyone who wants a sourced history instead of a generic summary.

## Install

Python 3.12 or newer. Using [uv](https://docs.astral.sh/uv/):

```bash
uv tool install git+https://github.com/suchipizza/pigtail
```

(or `pipx install git+https://github.com/suchipizza/pigtail`, or `pip install` it inside a virtual
environment.)

## Run

```bash
export ANTHROPIC_API_KEY="sk-ant-..."   # required: Pigtail uses Claude to read sources and search the web
export GITHUB_TOKEN="ghp_..."           # optional: raises GitHub's limit from 60 to 5,000 requests/hour

pigtail doctor                                    # checks your setup, costs nothing
pigtail https://github.com/plausible/analytics    # an open-source project
pigtail tally.so                                  # a product website
```

A run takes a few minutes. With the larger Claude Opus 5.5 model it cost **$1–3** for a repository and **$2–5** for a product website, measured on 10 targets ([validation report](validation/2026-10-06/REPORT.md)). The default model, Claude Sonnet 5.5, costs half as much per token, so expect roughly half that; this has not been measured yet. When it
finishes, the report opens in your browser.

## What you get

Each run writes a folder like `pigtail-output/plausible-1a2b3c4d/`:

| File | What it is |
|---|---|
| `report.html` | The growth report. One file; opens offline in any browser; easy to share. |
| `research-bundle.json` | Everything Pigtail found as structured data: sources, claims, events, metrics, tactics, gaps. The report is drawn entirely from this file. |
| `source-index.json` | A plain list of the sources used. |
| `run.json` | How long the run took, what it cost, how many requests it made. |

A report contains, where the evidence exists:

- a **30-second summary** of the growth story, labelled as fact or interpretation;
- **origin** and **first users** — who they were and how they were reached;
- a **timeline** of launches, posts, releases, pricing changes, partnerships and strategy shifts;
- **historical metrics** (users, revenue, stars) as reported, with conflicting numbers shown side by side;
- **growth engines** (mechanisms that kept bringing users) and **reusable tactics**;
- **how the strategy changed** over time;
- for GitHub projects: **star history with events**, **growth episodes** (unusual jumps in stars and
  what was happening at the time), and **launches compared** (stars before, +24h, +7 days, +30 days,
  +90 days);
- **research gaps** — what could not be established;
- **every source**, linked.

## How Pigtail stays honest

Pigtail uses a language model to read sources, but the rules that matter are enforced in code:

- **Every fact has a source.** Each statement in the report is a *claim* with evidence: the source
  URL, a short quote or data path, and when Pigtail read it.
- **Quotes are checked.** A claim whose quote cannot be found word for word in the page is dropped.
  Numbers must appear in the cited claims; dates must match them.
- **Timing is not cause.** If a Show HN post and a jump in stars happen in the same week, the report
  says they were *observed near the same period*. It says one caused the other only when the company
  itself says so in a cited source.
- **Conflicts are kept, not averaged.** When sources disagree, you see both.
- **Unknown is an answer.** A spike with no public explanation is shown as *"No high-confidence
  public event found."* If there is too little evidence, the report says *"Insufficient public
  evidence for a useful Pigtail forensic."* instead of filling the page.
- **Not an archive.** Pigtail keeps links, metadata and short quotes — never copies of pages.
  Each platform's rules are written down in [`source-policies/`](source-policies).

More in [docs/methodology.md](docs/methodology.md) and [docs/evidence.md](docs/evidence.md).

## What Pigtail does not do

- It does not know anything that is not public. Private metrics, internal experiments and
  unannounced decisions are invisible to it.
- It does not prove causes. It shows what happened when, and what the company said about it.
- It does not read Reddit, Product Hunt or X directly yet (link-only until their terms are
  reviewed — see [source policies](docs/source-policies.md)). Launches there appear when another
  source, such as the maker's blog, mentions them.
- It does not accept bare product names (`"Notion"`), only a domain (`notion.so`) or a GitHub URL,
  because names are ambiguous.
- It is not a hosted service: there is no account, dashboard or API. You run it on your computer.

## Examples

Browse the example reports on the [Pigtail website](https://suchipizza.github.io/pigtail/examples/). The same files are in
[`examples/reviewed/`](examples/reviewed); download a folder's `report.html` and open it in your
browser — no installation needed:

| Example | What it shows |
|---|---|
| [Hatchet](https://suchipizza.github.io/pigtail/examples/hatchet/) | Open-source task queue with repeated Hacker News launches (Show HN ×3, Launch HN), compared side by side |
| [Plausible Analytics](https://suchipizza.github.io/pigtail/examples/plausible/) | Open-source analytics grown by opinionated blog posts that reached Hacker News; bootstrapped revenue history |
| [PocketBase](https://suchipizza.github.io/pigtail/examples/pocketbase/) | One-developer open-source backend; growth in bursts, several with no public explanation |
| [Tally](https://suchipizza.github.io/pigtail/examples/tally/) | Bootstrapped form builder: cold outreach → free-product loop → search and AI discovery, with MRR history |
| [Superhuman](https://suchipizza.github.io/pigtail/examples/superhuman/) | Paid email app: waitlist, concierge onboarding, product-market-fit survey; later acquired and renamed by Grammarly |

The publication-review decisions for these examples were made by an AI on the maintainer's
behalf; a human review is pending.

Every published example went through Pigtail's [publication gate](docs/publication-gate.md):
names and account handles are replaced by roles, personal financial details and unreviewed
sensitive claims are removed, excerpts are capped at 15 words, and internal run data (model, cost,
IDs) is left out. Reports you generate yourself are not changed by any of this. Each folder holds
the report, the public report data, a publication manifest and a metadata file.

## More commands

```bash
pigtail analyze <target>                          # same as `pigtail <target>`
pigtail <target> --model anthropic/claude-opus-5-5
pigtail <target> --output ./reports
pigtail <target> --source https://example.com/launch-post   # add a source you know (checked like any other)
pigtail render research-bundle.json               # rebuild report.html, no keys or network needed
pigtail validate research-bundle.json             # check a bundle against the schema and integrity rules
pigtail version
```

Configuration (`pigtail.toml`), exit codes and common errors: [docs/troubleshooting.md](docs/troubleshooting.md).

## Supported environments and integrations

- Python 3.12 or newer. Tested on macOS and on Linux (CI). Windows should work but is not tested yet.
- **Model:** Anthropic Claude (default `claude-sonnet-5-5`; use `--model anthropic/claude-opus-5-5` for the larger model). Pigtail never switches models silently.
- **Web search:** Anthropic's web-search tool (same API key). Can be turned off in config.
- **Sources read directly:** GitHub API (repository, star history, releases, README), Hacker News
  (HN Search by Algolia), and public web pages that allow it in `robots.txt`.

## How it compares

- **Star-history charts** (e.g. star-history.com) draw the star curve. Pigtail adds the events
  around it, the evidence, and the wider product story.
- **Asking a chatbot "how did X grow?"** gives fluent prose with no way to check it. Pigtail's
  output is a set of sourced, quoted, dated claims, and it tells you what it could not find.
- **Doing it by hand** takes hours of searching. Pigtail takes minutes and leaves you a file you can
  check and edit.

## Architecture

```text
target → resolve → GitHub history → discover sources → fetch → extract claims (quote-checked)
       → reconstruct timeline → interpret growth → record gaps → Research Bundle → report.html
```

The report is rendered only from the Research Bundle, a versioned JSON format
([docs/research-bundle.md](docs/research-bundle.md), schema in
[`schemas/`](schemas/research-bundle/0.1.0.schema.json)). Contributor overview:
[docs/architecture.md](docs/architecture.md).

## Known limitations

- Quality depends on what is public. Well-documented products give rich reports; quiet ones give
  short reports with many gaps — by design.
- Company-reported numbers are shown as reported, not audited.
- Hacker News posts are matched by the project's URL and name, so some are missed.
- Star history counts current stargazers by the day they starred; people who later un-starred are
  not counted.
- Machine extraction can still misread a source. Every claim links to its source so you can check.

## Roadmap

Near term: Product Hunt and Reddit adapters once their terms are reviewed; package-download
history (npm, PyPI); incremental `pigtail update` of an existing bundle; more reviewed examples.
Later: a reviewed, cross-product dataset built from Research Bundles.

## Contributing

Bug reports, wrong-claim reports and pull requests are welcome. Start with
[CONTRIBUTING.md](CONTRIBUTING.md). Found a claim the source does not support?
[Open a "wrong claim" issue](https://github.com/suchipizza/pigtail/issues/new?template=bad-claim.yml).

Security issues: see [SECURITY.md](SECURITY.md).

## License

MIT — see [LICENSE](LICENSE). Maintained by [@suchipizza](https://github.com/suchipizza).

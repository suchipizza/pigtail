# The Research Bundle

`research-bundle.json` is the main output of a Pigtail run. The HTML report is drawn entirely from
it. You can keep it, check it into git, diff two runs, or build your own tools on it.

- Schema version: **0.1.0**
- JSON Schema: [`schemas/research-bundle/0.1.0.schema.json`](../schemas/research-bundle/0.1.0.schema.json)
- Schema ID: `https://pigtail.dev/schemas/research-bundle/0.1.0`
- All object IDs are UUIDv7 strings, unique within and across bundles.

## Why a bundle and not just a report?

A report is for reading. A bundle is for checking and reusing: every statement in it is a separate,
typed object with its evidence attached, so another program (or a person) can verify it, compare
products, or re-render it without re-running the research. It needs no Pigtail server or database.

## Top-level structure

```json
{
  "schema_version": "0.1.0",
  "bundle_id": "…",
  "generated_at": "2026-10-06T08:00:00Z",
  "run": { "status": "completed", "model": {…}, "cost": {…}, "usage": {…}, … },
  "target": { "kind": "repository", "name": "…", "canonical_url": "…", … },
  "people": [], "repositories": [], "sources": [], "source_fetches": [], "claims": [], "evidence_links": [],
  "events": [], "launch_episodes": [], "growth_episodes": [], "metric_snapshots": [],
  "company_stages": [], "strategy_phases": [], "surfaces": [], "surface_presences": [],
  "tactics": [], "tactic_occurrences": [], "growth_engines": [], "growth_engine_occurrences": [],
  "outcomes": [], "prerequisites": [], "constraints": [], "conflicts": [], "gaps": [],
  "narrative": { "thirty_second": {…}, "origin": {…}, "first_users": null, … }
}
```

Every array is always present, even when empty.

## The concepts, and why they are kept apart

| Object | What it is | Example |
|---|---|---|
| **Source** | A document or API endpoint | `https://blog.tally.so/product-hunt-launch-results` |
| **SourceFetch** | One time Pigtail read that source | retrieved 2026-10-06, HTTP 200, `sha256:…` |
| **Claim** | One small statement extracted from evidence | "Tally 2.0 launched on Product Hunt on Sep 19, 2023." |
| **EvidenceLink** | How a claim (or a higher object) is supported by a source | documented, first-party, quote "…" |
| **Event** | Something that happened at a time | Product Hunt relaunch |
| **MetricSnapshot** | A measured value at a time | MRR $75,000, Sep 2023 |
| **Outcome** | An interpretation of a result, with an attribution level | "Weekly signups roughly doubled for two weeks (company-attributed)" |
| **Tactic** / **TacticOccurrence** | A reusable action / where this product used it | "Relaunch on Product Hunt with a major version" |
| **GrowthEngine** / **…Occurrence** | A persistent mechanism / when it was active | "Made with Tally badge loop" |
| **CompanyStage** | The business situation | "Bootstrapped, profitable, team of 4" |
| **StrategyPhase** | How it tried to grow in a period | "Founder-led outreach" |
| **GrowthEpisode** | (repositories) A period of unusually fast star growth | +947 stars, Mar 7–10 2021 |
| **LaunchEpisode** | Related launch events grouped together | Show HN + release v1.0.0 |
| **Conflict** | Claims that disagree | "$6/month vs $4/month starting price" |
| **Gap** | Something that could not be established | "No public event found for the May 2026 spike" |

Keeping these apart is what lets the report say "this happened" separately from "this is what
Pigtail thinks it means".

## Integrity rules

`pigtail validate` rejects a bundle when, among other things:

- the schema version is unsupported, an ID is not a UUIDv7, or IDs are duplicated;
- a reference points to an object that does not exist;
- an active claim has no evidence, or evidence points to a missing source or fetch;
- an event, metric, tactic, engine, stage, phase or outcome has no supporting claim;
- a time range ends before it starts;
- a metric has both or neither of a numeric and a text value;
- a growth episode is not tied to a repository, or a launch episode groups no events;
- an excerpt comes from a link-only source, or a full copy is kept where policy forbids it;
- the narrative cites a claim that is not in the bundle;
- (with `--published`) a sensitive negative claim or event has not been human-verified.

It warns (but accepts) when optional sections are empty, star history is sampled, sources failed,
or important gaps exist.

## Versioning

Pre-1.0 rules: `0.1.x` may add clarifications; changing a required field or meaning needs a new
minor version. Pigtail refuses to read bundle versions it does not support and never converts
between versions silently.

## Clarification in this release

`narrative` blocks may be `null` when the evidence does not support them (for example, no public
information about the first users). `key_takeaways` is always an array.

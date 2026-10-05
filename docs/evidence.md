# Reading the evidence in a report

Every factual statement in a Pigtail report can be traced back to where it came from. This page
explains the labels you will see.

## The evidence chain

```text
what you see in the report (an event, a metric, a sentence in the summary)
  → claim        one small factual statement, e.g. "Tally reached $20,700 MRR in June 2022."
  → evidence     which source supports it, and how
  → source       the URL, title, author and publication date
  → fetch        when Pigtail read it, the HTTP status, and a fingerprint (hash) of the page
  → locator      where in the source: a short quote, or a JSON path for API data
```

Click a small number next to any statement (for example <sup>3</sup>) or click an event in the
timeline to open this chain.

## How sure is a claim? — evidence labels

| Label | Meaning |
|---|---|
| **Documented** | Stated by the company or founder in their own material (blog, changelog, docs). |
| **Company-reported metric** | A number the company published about itself (revenue, users). Not audited. |
| **Third-party measurement** | Measured by an independent system, e.g. star counts from the GitHub API or points on Hacker News. |
| **Third-party report** | A journalist, analyst or other outside source says it. |
| **Pigtail inference** | Pigtail's interpretation of several claims. Always marked as such. |
| **Unknown** | Pigtail could not classify the evidence. |

The evidence drawer also shows *where the source stands* (first-party direct, first-party quoted in
an interview, independent, secondary synthesis, community) and whether several independent
sources agree ("multi source consistent") or only one says it ("single source").

## Cause and effect — attribution labels

| Label | Meaning |
|---|---|
| **Observed near this period (timing only)** | Happened at about the same time. This is not evidence of cause. |
| **Strongly associated** | Closely linked by more than timing, but cause not proven. Pigtail never assigns this automatically. |
| **Attributed by the company** | The company or founder says this caused the result, in a cited source. |
| **Directly measured effect** | The effect was measured directly (e.g. referral analytics published by the company). |
| **No attribution** | Nothing connects this to an outcome. |

## Interpretation levels in the narrative

Summary paragraphs are labelled *Stated in sources*, *Strong inference*, *Moderate inference* or
*Weak inference*. The citations show which claims the paragraph rests on.

## Gaps and conflicts

The **Research gaps and uncertainty** section lists what Pigtail could not establish: unknown first
users, missing metrics, sources it could not read, link-only platforms, growth spikes with no
public explanation. **Conflicting sources** shows claims that disagree, side by side.

## What Pigtail keeps from a source

Pigtail is not an archive. It keeps the link, title, author, dates, a hash of the page and at most
a short quote (limit set per platform in [source-policies.md](source-policies.md)). The page text
itself is read in memory to extract claims and then discarded.

"""AI reviewer for publication-review.yaml (owner-authorized; human review stays possible later).

For each pending item the model sees the rule, the text, the cited claims with their sources and,
when allowed, a fresh read of the source page. It must pick one of the item's allowed decisions.
Decisions are recorded with an AI reviewer label, never under a person's name, so the audit always
shows who checked what. The gate then re-checks everything as usual.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import yaml
from pydantic import BaseModel

from pigtail.policies.models import SourcePolicy
from pigtail.providers.base import ModelProvider, StructuredModelRequest
from pigtail.publication.runner import OUT_DIR, REVIEW_FILE, REVIEW_HEADER

SOURCE_CHARS = 12_000


class AIDecision(BaseModel):
    decision: Literal["approve_as_is", "approve_public_text", "exclude", "mark_manually_verified"]
    public_text: str
    rationale: str


class PageFetcher(Protocol):
    async def fetch(self, url: str, policy: SourcePolicy): ...  # returns FetchedPage


SYSTEM = """You review items flagged by Pigtail's publication gate before a growth report about a \
company or open-source project is published on Pigtail's website.

Pick exactly one of the item's allowed decisions:
- approve_as_is: the text is supported by the cited sources, fairly attributed, and fine to publish.
- approve_public_text: publish your corrected wording instead (put it in public_text).
- exclude: leave it out. Choose this whenever you are unsure.
- mark_manually_verified: only if a fresh source text is given and it clearly supports the claim.

Rules for any wording you write:
- Use only facts present in the cited claims or source text. Never add facts, numbers or dates.
- Do not name people. Use roles: "the founder", "a co-founder", "the maintainer", "the CEO".
- No personal finances of individuals (savings, salaries, unpaid months). Company metrics are fine.
- No accusations or misconduct. Do not state a person's motives or plans as fact; attribute them
  ("the founder wrote that ...").
- A company's own claims stay attributed ("the company says ...").
- Keep the original language and length roughly; write plain English.
public_text must be "" unless the decision is approve_public_text. rationale: one short sentence."""


@dataclass
class AIReviewSummary:
    decided: int
    skipped: int
    cost_usd: float


def _item_context(item: dict, bundle: dict, page_texts: dict[str, str]) -> str:
    ref = item["object_ref"]
    claims = {c["id"]: c for c in bundle["claims"]}
    sources = {s["id"]: s for s in bundle["sources"]}
    links: dict[str, list[dict]] = {}
    for el in bundle["evidence_links"]:
        links.setdefault(el["claim_id"], []).append(el)
    cited: list[str] = []
    if ref["type"] == "claim":
        cited = [ref["id"]]
    elif ref["type"] == "narrative":
        n = bundle["narrative"]
        blk = n["key_takeaways"][int(ref["id"].split("[")[1].rstrip("]"))] if "[" in ref["id"] else n.get(ref["id"])
        cited = list((blk or {}).get("claim_ids", []))
    else:
        for objs in bundle.values():
            if isinstance(objs, list):
                for o in objs:
                    if isinstance(o, dict) and o.get("id") == ref["id"]:
                        cited = list(o.get("claim_ids", []))
    parts = [
        f"Rule: {item['rule_id']}",
        f"Why it was flagged: {item['reason']}",
        f"Item ({ref['type']}{'.' + item['field'] if item.get('field') else ''}): {item.get('context') or ''}",
        f"Allowed decisions: {', '.join(item['allowed_decisions'])}",
        "",
        "Cited claims and evidence:",
    ]
    for cid in cited[:12]:
        c = claims.get(cid)
        if not c:
            continue
        parts.append(f"- Claim: {c['statement']}")
        for el in links.get(cid, [])[:2]:
            s = sources.get(el["source_id"], {})
            parts.append(f"  Source: {s.get('url')} ({el['source_directness']}, {el['evidence_class']})")
            if el.get("excerpt"):
                parts.append(f"  Excerpt: {el['excerpt']}")
            if s.get("url") in page_texts:
                parts.append(f"  Fresh source text (truncated):\n{page_texts[s['url']]}")
    return "\n".join(parts)


async def _fetch_pages(item: dict, bundle: dict, fetcher: PageFetcher, policies: dict[str, SourcePolicy]) -> dict:
    if item["object_ref"]["type"] != "claim":
        return {}
    sources = {s["id"]: s for s in bundle["sources"]}
    out: dict[str, str] = {}
    for el in bundle["evidence_links"]:
        if el["claim_id"] != item["object_ref"]["id"]:
            continue
        s = sources[el["source_id"]]
        pol = policies.get(s["policy"]["policy_key"])
        if pol is None or s["url"] in out:
            continue
        page = await fetcher.fetch(s["url"], pol)
        if page.status == "success" and page.text:
            out[s["url"]] = page.text[:SOURCE_CHARS]
    return out


async def ai_review(
    run_dir: Path,
    model: ModelProvider,
    reviewer_label: str,
    fetcher: PageFetcher | None = None,
    policies: dict[str, SourcePolicy] | None = None,
    max_items: int = 200,
    budget_usd: float | None = None,
    meter=None,
) -> AIReviewSummary:
    """Fill pending decisions in <run>/publication/publication-review.yaml. Run the gate afterwards."""
    path = run_dir / OUT_DIR / REVIEW_FILE
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    bundle = json.loads((run_dir / "research-bundle.json").read_text(encoding="utf-8"))
    decided = skipped = 0
    for item in data.get("items", []):
        if item.get("decision") != "pending" or decided + skipped >= max_items:
            continue
        if budget_usd is not None and meter is not None and meter.total_cost >= budget_usd:
            skipped += 1
            continue
        pages = await _fetch_pages(item, bundle, fetcher, policies or {}) if fetcher else {}
        result = await model.structured(
            StructuredModelRequest(
                system=SYSTEM,
                prompt=_item_context(item, bundle, pages),
                output_type=AIDecision,
                purpose="publication review",
                max_tokens=4000,
                effort="medium",
            )
        )
        allowed = item["allowed_decisions"]
        if result.decision not in allowed or (result.decision == "mark_manually_verified" and not pages):
            skipped += 1  # leave it pending for a person
            continue
        if result.decision == "approve_public_text" and not result.public_text.strip():
            skipped += 1
            continue
        item["decision"] = result.decision
        item["public_text"] = result.public_text.strip() if result.decision == "approve_public_text" else None
        item["rationale"] = f"[AI] {result.rationale.strip()}"
        item["reviewer"] = reviewer_label
        decided += 1
    path.write_text(
        REVIEW_HEADER + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=110), encoding="utf-8"
    )
    cost = float(getattr(meter, "total_cost", 0.0) or 0.0) if meter is not None else 0.0
    return AIReviewSummary(decided=decided, skipped=skipped, cost_usd=cost)


def run_ai_review_sync(*args, **kwargs) -> AIReviewSummary:
    return asyncio.run(ai_review(*args, **kwargs))


async def fill_source_titles(run_dir: Path, fetcher: PageFetcher, policies: dict[str, SourcePolicy]) -> int:
    """Answer pending 'generic source title' items (PUB-008) with the page's own heading. No model involved:
    the heading is read from the page (JSON-LD headline, <h1>, or the specific part of <title>)."""
    path = run_dir / OUT_DIR / REVIEW_FILE
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    bundle = json.loads((run_dir / "research-bundle.json").read_text(encoding="utf-8"))
    sources = {s["id"]: s for s in bundle["sources"]}
    filled = 0
    for item in data.get("items", []):
        if item.get("decision") != "pending" or item["rule_id"] != "PUB-008" or item.get("field") != "title":
            continue
        s = sources.get(item["object_ref"]["id"])
        pol = policies.get(s["policy"]["policy_key"]) if s else None
        if s is None or pol is None:
            continue
        page = await fetcher.fetch(s["url"], pol)
        heading = (page.title or "").strip()
        if page.status != "success" or not heading or heading.lower() == (item.get("context") or "").lower():
            continue
        item["decision"] = "approve_public_text"
        item["public_text"] = heading
        item["rationale"] = "Page heading read from the source page."
        item["reviewer"] = "Pigtail title lookup (automatic); human review pending"
        filled += 1
    path.write_text(
        REVIEW_HEADER + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=110), encoding="utf-8"
    )
    return filled

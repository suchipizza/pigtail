"""Claim normalization: merge near-duplicate claims across sources without averaging values."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from pigtail.research.builder import BundleBuilder

_NUM = re.compile(r"\d+(?:[.,]\d+)*")


def _key(s: str) -> str:
    return re.sub(r"\W+", " ", s.lower()).strip()


def merge_duplicate_claims(b: BundleBuilder, threshold: float = 0.88) -> int:
    """Merge claims with the same kind, same date and near-identical wording AND identical numbers.

    Claims whose numbers differ are never merged (they may be a conflict to preserve).
    Returns the number of merged claims.
    """
    claims = [c for c in b.c["claims"] if c["claim_kind"] not in ("star_history", "star_growth_episode")]
    removed: set[str] = set()
    merged = 0
    for i, a in enumerate(claims):
        if a["id"] in removed:
            continue
        ka, na = _key(a["statement"]), sorted(_NUM.findall(a["statement"]))
        for c in claims[i + 1 :]:
            if c["id"] in removed or c["claim_kind"] != a["claim_kind"]:
                continue
            if (a["time"]["start"] or "")[:10] != (c["time"]["start"] or "")[:10]:
                continue
            if sorted(_NUM.findall(c["statement"])) != na:
                continue
            if SequenceMatcher(None, ka, _key(c["statement"])).ratio() < threshold:
                continue
            a_sources = {e["source_id"] for e in b.evidence_for(a["id"])}
            for link in b.evidence_for(c["id"]):
                if link["source_id"] in a_sources:
                    continue
                link["claim_id"] = a["id"]
                link["target_ref"] = {"type": "claim", "id": a["id"]}
                b._evidence_by_claim.setdefault(a["id"], []).append(link)
            b.c["evidence_links"] = [x for x in b.c["evidence_links"] if x["claim_id"] != c["id"]]
            b._evidence_by_claim.pop(c["id"], None)
            removed.add(c["id"])
            merged += 1
    b.c["claims"] = [c for c in b.c["claims"] if c["id"] not in removed]
    for cid in removed:
        b._claims.pop(cid, None)
    return merged

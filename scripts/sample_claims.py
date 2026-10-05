#!/usr/bin/env python3
"""Sample extracted (web) claims from bundles for manual citation audit (PRD §13.2).

Usage: python scripts/sample_claims.py <runs-dir> [per_bundle] > sample.json
"""

import glob
import json
import random
import sys

runs, per = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 5
rng = random.Random(20261006)
out = []
for path in sorted(glob.glob(f"{runs}/*/research-bundle.json")):
    b = json.load(open(path))
    src = {s["id"]: s for s in b["sources"]}
    links = {}
    for el in b["evidence_links"]:
        if el["target_ref"]["type"] == "claim":
            links.setdefault(el["claim_id"], []).append(el)
    pool = [c for c in b["claims"] if any(src[e["source_id"]]["surface_key"] == "web" for e in links.get(c["id"], []))]
    for c in rng.sample(pool, min(per, len(pool))):
        el = next(e for e in links[c["id"]] if src[e["source_id"]]["surface_key"] == "web")
        out.append(
            {
                "target": b["target"]["name"],
                "claim_id": c["id"],
                "statement": c["statement"],
                "time": c["time"],
                "source_url": src[el["source_id"]]["url"],
                "quote": el["excerpt"],
                "evidence_class": el["evidence_class"],
            }
        )
json.dump(out, sys.stdout, indent=1)

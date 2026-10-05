"""Deterministic conflict detection: same metric, same period, different values → preserved conflict."""

from __future__ import annotations

from pigtail.research.builder import BundleBuilder


def detect_metric_conflicts(b: BundleBuilder) -> int:
    groups: dict[tuple[str, str], list[dict]] = {}
    for m in b.c["metric_snapshots"]:
        if m["metric_key"].startswith("github_") or m["value_numeric"] is None or not m["time"]["start"]:
            continue
        key = (
            m["metric_key"],
            m["time"]["start"][:7] if m["time"]["precision"] != "year" else m["time"]["start"][:4],
        )
        groups.setdefault(key, []).append(m)
    n = 0
    for (_mk, period), ms in groups.items():
        vals = {round(m["value_numeric"], 2) for m in ms}
        if len(vals) < 2:
            continue
        lo, hi = min(vals), max(vals)
        if hi and (hi - lo) / hi < 0.03:
            continue
        claims = list(dict.fromkeys(c for m in ms for c in m["claim_ids"]))
        if len(claims) < 2:
            continue
        b.add(
            "conflicts",
            {
                "summary": f"Sources report different values for {ms[0]['label']} around {period}: "
                + ", ".join(f"{v:g}" for v in sorted(vals))
                + ". Pigtail keeps both and does not average them.",
                "claim_ids": claims,
                "related_refs": [{"type": "metric_snapshot", "id": m["id"]} for m in ms],
                "status": "unresolved",
            },
        )
        n += 1
    return n

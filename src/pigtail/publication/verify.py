"""Mechanical checks: public projection invariants, manifests, published example directories."""

from __future__ import annotations

import json
from pathlib import Path

from pigtail.publication import (
    FORBIDDEN_EXAMPLE_FILES,
    NOTICE_FILE,
    PUBLIC_EXAMPLE_FILES,
    PUBLIC_EXCERPT_MAX_WORDS,
    SUPPORTED_POLICY_VERSIONS,
    THIRD_PARTY_NOTICE,
)
from pigtail.publication.models import PublicationManifest

# Keys that only exist in a raw Research Bundle; their presence means the file is not a projection.
INTERNAL_KEYS = ("bundle_id", "run", "people")


def _sha(path: Path) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def check_public_bundle(p: dict) -> list[str]:
    """Invariants every public projection must meet. Returns problems (empty = ok)."""
    problems: list[str] = []
    for k in INTERNAL_KEYS:
        if k in p:
            problems.append(f"internal field '{k}' is present")
    claims = {c["id"]: c for c in p.get("claims", [])}
    sources = {s["id"]: s for s in p.get("sources", [])}
    fetches = {f["id"]: f for f in p.get("source_fetches", [])}
    linked: dict[str, list[dict]] = {}
    excerpt_sources: dict[str, int] = {}
    for el in p.get("evidence_links", []):
        if el["claim_id"] not in claims:
            problems.append(f"evidence link {el['id']} points to a missing claim")
        if el["source_id"] not in sources:
            problems.append(f"evidence link {el['id']} points to a missing source")
        if el["source_fetch_id"] not in fetches:
            problems.append(f"evidence link {el['id']} points to a missing fetch")
        linked.setdefault(el["claim_id"], []).append(el)
        if el.get("excerpt"):
            s = sources.get(el["source_id"], {})
            if s.get("policy", {}).get("public_display_mode") != "paraphrase_link_excerpt":
                problems.append(f"evidence link {el['id']} shows an excerpt its source policy does not allow")
            if len(el["excerpt"].split()) > PUBLIC_EXCERPT_MAX_WORDS:
                problems.append(f"evidence link {el['id']} excerpt is longer than {PUBLIC_EXCERPT_MAX_WORDS} words")
            excerpt_sources[el["source_id"]] = excerpt_sources.get(el["source_id"], 0) + 1
        if el["locator"]["kind"] == "text_fragment" and el["locator"]["value"]:
            problems.append(f"evidence link {el['id']} exposes a text-fragment locator")
    for sid, n in excerpt_sources.items():
        if n > 1:
            problems.append(f"source {sid} shows {n} excerpts (max 1)")
    for cid, c in claims.items():
        links = linked.get(cid, [])
        ok = any(fetches.get(el["source_fetch_id"], {}).get("status") == "success" for el in links)
        if not links:
            problems.append(f"claim {cid} has no evidence")
        elif not ok and not c.get("manually_verified"):
            problems.append(f"claim {cid} has no successfully read evidence and is not manually verified")
    for s in sources.values():
        if s.get("author"):
            problems.append(f"source {s['id']} shows an author")
    for coll, objs in p.items():
        if not isinstance(objs, list):
            continue
        for o in objs:
            if isinstance(o, dict):
                for cid in o.get("claim_ids", []) or []:
                    if cid not in claims:
                        problems.append(f"{coll} {o.get('id')} cites a missing claim")
    narrative = p.get("narrative") or {}
    blocks = [v for k, v in narrative.items() if k != "key_takeaways" and v] + list(narrative.get("key_takeaways", []))
    for blk in blocks:
        for cid in blk["claim_ids"]:
            if cid not in claims:
                problems.append("narrative cites a missing claim")
    return problems


def check_manifest(manifest: dict, files: dict[str, Path]) -> list[str]:
    """Check a manifest against current files. `files` maps 'public_bundle'/'report'/'input'/'review'."""
    problems: list[str] = []
    try:
        m = PublicationManifest.model_validate(manifest)
    except Exception as exc:
        return [f"manifest is malformed: {str(exc).splitlines()[0]}"]
    if m.status != "PASS":
        problems.append(f"publication status is {m.status}, not PASS")
    if m.unresolved_findings != 0:
        problems.append(f"{m.unresolved_findings} unresolved finding(s)")
    if m.publication_policy_version not in SUPPORTED_POLICY_VERSIONS:
        problems.append(f"publication policy {m.publication_policy_version} is not supported/current")
    expected = {
        "public_bundle": m.public_bundle_hash,
        "report": m.report_hash,
        "input": m.input_bundle_hash,
        "review": m.review_hash,
    }
    for key, path in files.items():
        want = expected[key]
        if want is None:
            if path.exists():
                problems.append(f"{path.name} exists but the manifest was generated without it; re-run the gate")
            continue
        if not path.exists():
            problems.append(f"{path.name} is missing")
        elif _sha(path) != want:
            problems.append(f"{path.name} changed after the publication gate ran; re-run the gate")
    return problems


def verify_example_dir(d: Path) -> list[str]:
    """Rules for a published example directory (PRD §14). Returns problems (empty = ok)."""
    problems = [f"{d.name}: {f} must not be published" for f in FORBIDDEN_EXAMPLE_FILES if (d / f).exists()]
    for f in PUBLIC_EXAMPLE_FILES:
        if not (d / f).exists():
            problems.append(f"{d.name}: {f} is missing")
    if problems:
        return problems
    manifest = json.loads((d / "publication-manifest.json").read_text(encoding="utf-8"))
    problems += [
        f"{d.name}: {p}"
        for p in check_manifest(
            manifest, {"public_bundle": d / "public-report-bundle.json", "report": d / "report.html"}
        )
    ]
    public = json.loads((d / "public-report-bundle.json").read_text(encoding="utf-8"))
    problems += [f"{d.name}: {p}" for p in check_public_bundle(public)[:10]]
    return problems


def verify_examples_root(root: Path) -> list[str]:
    """All examples under `root` plus the third-party-content notice (PUB-016)."""
    problems: list[str] = []
    notice = root / NOTICE_FILE
    if not notice.exists():
        problems.append(f"{notice} is missing (third-party content notice, PUB-016)")
    elif " ".join(THIRD_PARTY_NOTICE.split()) not in " ".join(notice.read_text(encoding="utf-8").split()):
        problems.append(f"{notice} does not contain the required third-party content notice")
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        problems += verify_example_dir(d)
    return problems

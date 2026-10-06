"""File-level entry points: run the gate on a run directory; promote a passed result (PRD §10, §14, §15)."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from pigtail import ENGINE_VERSION
from pigtail.bundle.writer import dump_json
from pigtail.publication import POLICY_VERSION, SANITIZER_VERSION
from pigtail.publication.gate import GateResult, PublicationGate, sha256_bytes, sha256_file
from pigtail.publication.models import PublicationManifest, ReviewFile
from pigtail.publication.verify import check_manifest, verify_example_dir

OUT_DIR = "publication"
REVIEW_FILE = "publication-review.yaml"
REVIEW_HEADER = """\
# Pigtail publication review. Edit `decision` (and `public_text`, `rationale`, `reviewer`), then
# re-run the publication gate. Decisions:
#   approve_as_is          publish the item unchanged (not allowed where the policy requires exclusion)
#   approve_public_text    publish your wording in `public_text` instead (it is checked again)
#   exclude                leave the item out of the public report
#   mark_manually_verified you checked the source yourself (for unreadable sources / sensitive claims)
# Only the decisions listed in `allowed_decisions` are accepted. `keep_identities` lists people or
# handles whose identity is necessary to understand the analysis (needs a rationale).
# This file is internal: never publish it.
"""


class GateInputError(Exception):
    pass


@dataclass
class GateRun:
    result: GateResult
    out_dir: Path
    manifest: PublicationManifest


def load_review(path: Path) -> ReviewFile | None:
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return ReviewFile.model_validate(data)


def _review_yaml(result: GateResult) -> str:
    data = {
        "policy_version": POLICY_VERSION,
        "keep_identities": [k.model_dump() for k in result.keep_identities],
        "items": [i.model_dump(mode="json") for i in result.review_items],
    }
    return REVIEW_HEADER + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=110)


def run_gate(run_dir: Path, review_path: Path | None = None) -> GateRun:
    bundle_path = run_dir / "research-bundle.json"
    for name in ("research-bundle.json", "source-index.json", "run.json"):
        if not (run_dir / name).exists():
            raise GateInputError(f"{run_dir / name} is missing; the gate needs a complete Pigtail output directory")
    out = run_dir / OUT_DIR
    out.mkdir(exist_ok=True)
    review_path = review_path or out / REVIEW_FILE
    try:
        review = load_review(review_path)
    except Exception as exc:
        raise GateInputError(f"{review_path} is not a valid review file: {exc}") from exc

    raw = bundle_path.read_bytes()
    bundle = json.loads(raw)
    result = PublicationGate(bundle, review=review, input_hash=sha256_bytes(raw)).run()

    if result.review_items or review is not None:
        text = _review_yaml(result)
        if not review_path.exists() or review_path.read_text(encoding="utf-8") != text:
            review_path.write_text(text, encoding="utf-8")
    review_hash = sha256_file(review_path) if review_path.exists() else None
    result.audit.review_hash = review_hash

    for stale in ("public-report-bundle.json", "report.html", "publication-manifest.json"):
        (out / stale).unlink(missing_ok=True)
    pub_hash = rep_hash = ""
    if result.public_bundle is not None and result.report_html is not None:
        (out / "public-report-bundle.json").write_text(dump_json(result.public_bundle), encoding="utf-8")
        (out / "report.html").write_text(result.report_html, encoding="utf-8")
        pub_hash = sha256_file(out / "public-report-bundle.json")
        rep_hash = sha256_file(out / "report.html")
    (out / "publication-audit.json").write_text(dump_json(result.audit.model_dump(mode="json")), encoding="utf-8")
    manifest = PublicationManifest(
        publication_policy_version=POLICY_VERSION,
        sanitizer_version=SANITIZER_VERSION,
        engine_version=ENGINE_VERSION,
        target_name=bundle["target"]["name"],
        input_bundle_hash=result.audit.input_bundle_hash,
        review_hash=review_hash,
        public_bundle_hash=pub_hash,
        report_hash=rep_hash,
        status=result.status,
        unresolved_findings=result.unresolved,
        generated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    (out / "publication-manifest.json").write_text(dump_json(manifest.model_dump(mode="json")), encoding="utf-8")
    return GateRun(result=result, out_dir=out, manifest=manifest)


def check_run_publication(run_dir: Path) -> list[str]:
    """Is the gate result in `run_dir/publication` a current PASS for the current inputs?"""
    out = run_dir / OUT_DIR
    mpath = out / "publication-manifest.json"
    if not mpath.exists():
        return [f"{mpath} is missing; run the publication gate first"]
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    return check_manifest(
        manifest,
        {
            "input": run_dir / "research-bundle.json",
            "review": out / REVIEW_FILE,
            "public_bundle": out / "public-report-bundle.json",
            "report": out / "report.html",
        },
    )


def promote(run_dir: Path, dest: Path, metadata: dict) -> list[str]:
    """Copy a passed publication into a public example directory. There is no force option."""
    problems = check_run_publication(run_dir)
    if problems:
        return problems
    out = run_dir / OUT_DIR
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for name in ("report.html", "public-report-bundle.json", "publication-manifest.json"):
        shutil.copyfile(out / name, dest / name)
    (dest / "metadata.json").write_text(dump_json(metadata), encoding="utf-8")
    return verify_example_dir(dest)

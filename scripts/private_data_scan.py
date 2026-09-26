#!/usr/bin/env python3
"""Private-data and secret scan for the public repo (PRD §10, WORK_ORDER §6).

Blocks: secrets/tokens, snapshot/raw-data paths and formats, personal e-mail addresses,
unlisted test fixtures (fixtures must be synthetic or pseudonymized and listed in
tests/fixtures/MANIFEST.md), research briefs (PRD R18.9: briefs live only in the instance's data
directory; the one synthetic example is allowlisted by path), and oversized files. Scans
git-tracked + staged files by default, or the paths given on the command line (pre-commit
passes staged files).

Exit 0 = clean, 1 = findings.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SECRET_PATTERNS = {
    "anthropic key": re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "claude oauth token": re.compile(r"sk-ant-oat[A-Za-z0-9_\-]{10,}"),
    "github token": re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    "aws access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "google api key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "credential url": re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s@/]{6,}@(?!db:|localhost)"),
}

FORBIDDEN_PATH = re.compile(
    r"(^|/)(data|snapshots|raw|ops/sessions)/|\.(jsonl\.gz|warc(\.gz)?|sqlite3?|db|parquet|har)$"
    r"|(^|/)\.env$"
)

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
EMAIL_ALLOW = re.compile(
    r"@(example\.(com|org|net)|[a-z0-9.-]*\.example|users\.noreply\.github\.com)$"
    r"|^noreply@anthropic\.com$"
    # Role/organizational addresses published by platforms are not personal data.
    r"|^(noreply|no-reply|security|privacy|hello|info|support|contact|legal|api|press|abuse"
    r"|dmca|copyright|help|feedback|partners|developers?)@",
    re.IGNORECASE,
)

# Research briefs (PRD R18.9, D7): a file under a `briefs/` directory or named like a brief, or
# any data file whose content has a brief's top-level structure (`brief_id` + `project`).
# Code files are exempt from the content rule (tests build synthetic briefs in memory); the
# explicit allowlist holds the synthetic example the repo ships (docs/examples).
BRIEF_PATH = re.compile(r"(^|/)briefs/.+\.(ya?ml|json)$|(^|/)[^/]*brief[^/]*\.ya?ml$", re.I)
# A key at the start of a line (any indentation, optionally a list item or quoted) or inside a
# flow mapping (after `{` or `,`), so indented and flow-style briefs (`project: {name: …}`) match
# as well as block style. `project` must open a mapping: end of line, a comment, or `{`.
_KEY = r"(?:^[ \t]*(?:-[ \t]+)?|[{,][ \t]*)[\"']?"
BRIEF_YAML = (
    re.compile(_KEY + r"brief_id[\"']?[ \t]*:[ \t]*[^\s#]", re.M),
    re.compile(_KEY + r"project[\"']?[ \t]*:[ \t]*(?:$|#|\{)", re.M),
)
BRIEF_JSON = (
    re.compile(r'"brief_id"\s*:\s*"'),
    re.compile(r'"project"\s*:\s*\{\s*"(name|description|target_users)"'),
)
BRIEF_ALLOW = {"docs/examples/brief-example.yaml"}
CODE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".sql", ".sh"}

MAX_BYTES = 1_000_000
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".woff", ".woff2", ".zip"}
SKIP_CONTENT = {"scripts/private_data_scan.py", "tests/unit/test_private_data_scan.py"}
FIXTURE_DIR = "tests/fixtures/"
MANIFEST = "tests/fixtures/MANIFEST.md"


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line for line in out.splitlines() if line]


def manifest_entries(root: Path) -> set[str]:
    path = root / MANIFEST
    if not path.exists():
        return set()
    return set(re.findall(r"`(tests/fixtures/[^`]+)`", path.read_text()))


def looks_like_brief(rel: str, text: str) -> bool:
    suffix = Path(rel).suffix.lower()
    if suffix in CODE_EXT:
        return False
    pats = BRIEF_JSON if suffix == ".json" else BRIEF_YAML
    return all(p.search(text) for p in pats)


def scan(paths: list[str], root: Path = ROOT) -> list[str]:
    findings: list[str] = []
    listed = manifest_entries(root)
    for rel in paths:
        p = root / rel
        if FORBIDDEN_PATH.search(rel):
            findings.append(f"{rel}: forbidden path/format (raw data, snapshots or .env)")
            continue
        if BRIEF_PATH.search(rel) and rel not in BRIEF_ALLOW:
            findings.append(f"{rel}: research brief file (briefs stay in PIGTAIL_DATA_DIR)")
            continue
        if not p.is_file():
            continue
        if rel.startswith(FIXTURE_DIR) and rel != MANIFEST and rel not in listed:
            findings.append(f"{rel}: fixture not listed in {MANIFEST} (must be synthetic)")
        if p.stat().st_size > MAX_BYTES:
            findings.append(f"{rel}: file larger than {MAX_BYTES} bytes")
        if p.suffix.lower() in BINARY_EXT or rel in SKIP_CONTENT:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if rel not in BRIEF_ALLOW and looks_like_brief(rel, text):
            findings.append(f"{rel}: content has a research brief's structure (brief_id + project)")
        for name, pat in SECRET_PATTERNS.items():
            if pat.search(text):
                findings.append(f"{rel}: possible {name}")
        for m in EMAIL.finditer(text):
            if not EMAIL_ALLOW.search(m.group(0)):
                findings.append(f"{rel}: e-mail address {m.group(0)[:3]}***")
    return findings


def main(argv: list[str]) -> int:
    paths = argv or tracked_files()
    findings = scan(paths)
    for f in findings:
        print(f"PRIVATE-DATA SCAN: {f}", file=sys.stderr)
    if findings:
        print(
            f"{len(findings)} finding(s). Nothing private may enter the public repo.",
            file=sys.stderr,
        )
        return 1
    print(f"private-data scan: {len(paths)} file(s) clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

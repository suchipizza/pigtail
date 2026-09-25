"""The CI private-data scan blocks secrets, raw data and unlisted fixtures (PRD §10)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "private_data_scan", Path(__file__).parents[2] / "scripts" / "private_data_scan.py"
)
assert spec and spec.loader
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)


def write(root: Path, rel: str, text: str) -> str:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return rel


def test_clean_file_passes(tmp_path):
    rel = write(tmp_path, "src/a.py", "x = 1  # contact security@example.com\n")
    assert scanner.scan([rel], tmp_path) == []


def test_secrets_blocked(tmp_path):
    rel = write(tmp_path, "a.txt", "key=sk-ant-api03-" + "A" * 40)
    assert any("anthropic key" in f for f in scanner.scan([rel], tmp_path))
    rel2 = write(tmp_path, "b.txt", "token ghp_" + "a" * 36)
    assert any("github token" in f for f in scanner.scan([rel2], tmp_path))


def test_personal_email_blocked(tmp_path):
    rel = write(tmp_path, "ops/RUNLOG.md", "reported by jane.doe@gmail.com")
    assert scanner.scan([rel], tmp_path)


def test_raw_data_paths_blocked(tmp_path):
    for rel in ("data/x.json", "snapshots/ab/cd", "dump.jsonl.gz", ".env", "x.sqlite3"):
        assert scanner.scan([rel], tmp_path), rel


def test_unlisted_fixture_blocked(tmp_path):
    rel = write(tmp_path, "tests/fixtures/hn/item.json", "{}")
    assert scanner.scan([rel], tmp_path)
    write(tmp_path, "tests/fixtures/MANIFEST.md", "| `tests/fixtures/hn/item.json` | synthetic |")
    assert scanner.scan([rel], tmp_path) == []


def test_role_addresses_allowed(tmp_path):
    rel = write(tmp_path, "docs/x.md", "contact hello@platform.example.io or legal@corp.io")
    assert scanner.scan([rel], tmp_path) == []

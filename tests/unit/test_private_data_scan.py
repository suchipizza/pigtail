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


# --- M12 (PRD R18.9, D7): research briefs never enter git -------------------------------------
BRIEF_YAML = """brief_id: synthetic-private
project:
  name: Synthetic
"""


def test_m12_brief_files_blocked_by_path(tmp_path):
    for rel in ("briefs/x.yaml", "some/dir/briefs/proj/v0001.yaml", "notes/my-brief.yml",
                "project.brief.yaml", "briefs/x.json"):  # fmt: skip
        rel = write(tmp_path, rel, "a: 1\n")
        assert any("research brief" in f for f in scanner.scan([rel], tmp_path)), rel


def test_m12_brief_content_blocked_anywhere(tmp_path):
    for rel in ("docs/pasted.md", "ops/notes.yaml", "config.yml", "x.txt"):
        rel = write(tmp_path, rel, BRIEF_YAML)
        assert any("brief's structure" in f for f in scanner.scan([rel], tmp_path)), rel
    js = '{"brief_id": "synthetic-private", "project": {"name": "Synthetic"}}'
    rel = write(tmp_path, "export/b.json", js)
    assert scanner.scan([rel], tmp_path)


def test_m12_only_the_synthetic_example_is_allowlisted(tmp_path):
    rel = write(tmp_path, "docs/examples/brief-example.yaml", BRIEF_YAML)
    assert scanner.scan([rel], tmp_path) == []
    other = write(tmp_path, "docs/examples/brief-other.yaml", BRIEF_YAML)
    assert scanner.scan([other], tmp_path)


def test_m12_schema_and_code_are_not_briefs(tmp_path):
    props = '{"properties": {"brief_id": {"type": "string"}, "project": {"$ref": "#"}}}'
    schema = write(tmp_path, "schemas/brief/v1.json", props)
    code = write(tmp_path, "tests/unit/test_x.py", BRIEF_YAML)
    assert scanner.scan([schema, code], tmp_path) == []


def test_m12_the_real_repo_example_passes_and_data_dir_is_blocked():
    root = Path(__file__).parents[2]
    assert scanner.scan(["docs/examples/brief-example.yaml", "schemas/brief/v1.json"], root) == []
    assert scanner.scan(["data/briefs/anything/v0001.yaml"], root)

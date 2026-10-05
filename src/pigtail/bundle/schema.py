"""Generate / check the normative Research Bundle JSON Schema.

python -m pigtail.bundle.schema --write   # regenerate the checked-in schema
python -m pigtail.bundle.schema --check   # exit 1 if it diverges from the models
"""

from __future__ import annotations

import json
import sys
from functools import cache
from importlib import resources
from pathlib import Path

from pigtail.bundle.models import SCHEMA_ID, ResearchBundle

REPO_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schemas" / "research-bundle" / "0.1.0.schema.json"


def generate_schema() -> dict:
    schema = ResearchBundle.model_json_schema(mode="validation")
    out = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": SCHEMA_ID}
    out.update({k: v for k, v in schema.items() if k not in ("$id",)})
    return out


def dumps(schema: dict) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


@cache
def load_checked_in_schema() -> dict:
    """Load the normative schema: repo checkout first, then the packaged copy."""
    if REPO_SCHEMA_PATH.exists():
        return json.loads(REPO_SCHEMA_PATH.read_text())
    data = resources.files("pigtail").joinpath("_data/schemas/research-bundle/0.1.0.schema.json")
    return json.loads(data.read_text())


def main(argv: list[str]) -> int:
    generated = dumps(generate_schema())
    if "--write" in argv:
        REPO_SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPO_SCHEMA_PATH.write_text(generated)
        print(f"wrote {REPO_SCHEMA_PATH}")
        return 0
    current = REPO_SCHEMA_PATH.read_text() if REPO_SCHEMA_PATH.exists() else ""
    if current != generated:
        print("Checked-in schema differs from the Pydantic models. Run: python -m pigtail.bundle.schema --write")
        return 1
    print("schema up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

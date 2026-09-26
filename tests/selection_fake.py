"""Synthetic selection inputs for the M22 selection tests (R4.3, R4.8, R4.9, R4.10; ADR-077).

Every repo name is made up (`org-q/repo-NN`, `org-e/exemplar-N`); values come from a seeded
`random.Random`, so the fixture is the same on every run. Nothing here is real data.
"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pigtail.briefs.model import Brief, load_brief_text
from pigtail.briefs.selection import Anchor, CaseInput, Covariates, Value

EXAMPLE = Path(__file__).resolve().parents[1] / "docs" / "examples" / "brief-example.yaml"
LANGS = ("go", "rust", "python")
H2_2025 = datetime(2025, 7, 10, 15, tzinfo=UTC)
H1_2026 = datetime(2026, 1, 12, 15, tzinfo=UTC)


def brief(**success: Any) -> Brief:
    """The synthetic example brief (stored as version 1), with `success` fields overridden.
    Default here: attention primary, top quartile, community at least the median."""
    b = load_brief_text(EXAMPLE.read_text())
    data = b.model_dump(mode="json")
    default_steps = "primary_threshold" not in success and "minimums" not in success
    data["success"] = {
        "primary": "attention",
        "primary_threshold": "top_quartile",
        "minimums": {"community": "at_least_median"},
        "fallbacks": {
            "too_few_winners": {
                "min_winners": 10,
                "steps": ["relax_primary_to_top_third", "drop_community_minimum"]
                if default_steps
                else [],
            }
        },
        **success,
    }
    data["version"] = 1
    return Brief.model_validate(data)


def obs(x: float, group: str | None = None) -> Value:
    return Value("observed", float(x), "verified", group=group)


def case(
    i: int,
    *,
    stars: float | None,
    community: float | None = 5,
    downloads: float | None = None,
    half: int = 0,
    lsm: float = 2.0,
    lang: str = "go",
    distance: int = 0,
    panel: str = "field",
    flag: str = "false",
    launch_type: str = "show_hn",
    ref: str | None = None,
    named_index: int | None = None,
    anchor: bool = True,
) -> CaseInput:
    at = (H2_2025 if half == 0 else H1_2026) + timedelta(days=i % 60)
    values: dict[str, Value] = {
        "att.stars@30": obs(stars) if stars is not None else Value("unknown", reason="x"),
        "comm.returning_external_contributors@90": (
            obs(community) if community is not None else Value("pending", reason="horizon")
        ),
        "adopt.downloads@90": (
            obs(downloads, "pypi") if downloads is not None else Value("unknown", reason="nc")
        ),
    }
    return CaseInput(
        ref=ref or f"gh:org-q/repo-{i:02d}",
        panel=panel,  # type: ignore[arg-type]
        distance=distance,
        named_index=named_index,
        anchor=Anchor("launch", at, "hour", "show_hn") if anchor else None,
        anchor_reason=None if anchor else "no_anchor",
        values=values,
        covariates=Covariates(
            lsm=lsm,
            launch_quarter=at.year * 4 + (at.month - 1) // 3,
            launch_half_year=f"{at.year}H{1 if at.month <= 6 else 2}",
            age_log10=math.log10(30 + i),
            audience_band="unknown",
            language=lang,
            launch_type=launch_type,
        ),
        star_anomaly_flag=flag,  # type: ignore[arg-type]
    )


def population(n: int = 60, seed: int = 7) -> list[CaseInput]:
    """`n` field candidates in two half-years with seeded stars, community counts and LSM."""
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        stars = rnd.choice([0, *range(5, 3000, 7)])
        out.append(
            case(
                i,
                stars=stars,
                community=rnd.randint(0, 12),
                half=i % 2,
                lsm=round(1.0 + rnd.random() * 2.0, 3),
                lang=LANGS[rnd.randrange(3)],
                flag="true" if i in (3, 17) else "false",
            )
        )
    return out

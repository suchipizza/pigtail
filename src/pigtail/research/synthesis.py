"""Narrative synthesis. Narrative blocks may only cite claims present in the bundle (spec §20)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from pigtail.domain.time import human_label
from pigtail.providers.base import ModelProvider, StructuredModelRequest
from pigtail.research.builder import BundleBuilder

Strength = Literal["explicit", "strong_inference", "moderate_inference", "weak_inference"]


class Block(BaseModel):
    text: str
    claims: list[str]
    inference_strength: Strength


class NarrativeOut(BaseModel):
    thirty_second: Block | None = Field(description="3–4 sentences: the observable growth system")
    origin: Block | None = Field(description="Why/how it started; null if claims do not say")
    first_users: Block | None = Field(description="Who the first users were and how they were reached; null if unknown")
    flywheel: Block | None = Field(description="How usage and distribution reinforce each other; null if unsupported")
    did_differently: Block | None = Field(
        description="What it did differently; clearly interpretive; null if unsupported"
    )
    key_takeaways: list[Block] = Field(description="3–6 evidence-backed lessons, no prescriptive certainty")


SYSTEM = (
    "You write the narrative layer of an evidence-backed growth forensic. Use only the supplied claims and "
    "objects; cite claim refs for every block. Plain, concrete English. No hype, no buzzwords. "
    "Never present timing as causation: say 'around the same time' or 'followed by' unless a cited claim "
    "states the cause. Mark inference_strength honestly: 'explicit' only if the block restates sourced facts; "
    "otherwise strong/moderate/weak inference. Return null for any block the evidence cannot support. "
    "Do not infer misconduct, motive or intent."
)


async def synthesize(model: ModelProvider, b: BundleBuilder, target_desc: str, extra_context: list[str]) -> int:
    refs: dict[str, str] = {}
    lines = []
    for i, c in enumerate(b.c["claims"], 1):
        ref = f"c{i}"
        refs[ref] = c["id"]
        lines.append(f"{ref} [{human_label(c['time'])}] {c['statement']}")
    objects = []
    for e in sorted(b.c["events"], key=lambda e: e["time"]["start"] or "9999"):
        objects.append(f"EVENT {human_label(e['time'])}: {e['title']} — {e['summary']}")
    for coll, label in (("strategy_phases", "PHASE"), ("company_stages", "STAGE")):
        for p in b.c[coll]:
            objects.append(f"{label} {human_label(p['time'])}: {p['label']} — {p['summary']}")
    for t in b.c["tactics"]:
        objects.append(f"TACTIC: {t['name']} — {t['mechanism']}")
    for g in b.c["growth_engines"]:
        objects.append(f"ENGINE: {g['name']} — {g['mechanism']}")
    for g in b.c["growth_episodes"]:
        objects.append(f"STAR EPISODE {human_label(g['time'])}: {g['summary']}")
    prompt = (
        f"Target: {target_desc}\n\nClaims:\n"
        + "\n".join(lines)
        + "\n\nReconstructed objects:\n"
        + "\n".join(objects[:200])
        + ("\n\nContext:\n" + "\n".join(extra_context) if extra_context else "")
        + "\n\nWrite the narrative blocks."
    )
    out = await model.structured(
        StructuredModelRequest(
            system=SYSTEM,
            prompt=prompt,
            output_type=NarrativeOut,
            purpose="narrative synthesis",
            max_tokens=12000,
            effort="medium",
        )
    )
    dropped = 0

    def conv(block: Block | None) -> dict | None:
        nonlocal dropped
        if not block:
            return None
        cids = list(dict.fromkeys(refs[r] for r in block.claims if r in refs))
        if not cids:
            dropped += 1
            return None
        return {"text": block.text.strip(), "claim_ids": cids, "inference_strength": block.inference_strength}

    for k in ("thirty_second", "origin", "first_users", "flywheel", "did_differently"):
        b.narrative[k] = conv(getattr(out, k))
    b.narrative["key_takeaways"] = [x for x in (conv(t) for t in out.key_takeaways) if x]
    return dropped

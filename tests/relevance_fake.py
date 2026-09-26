"""Batch-capable fake `api` backend for the M22 relevance filter (no network, no key).

It reads the candidates JSON of each request and answers per candidate: a name with
`framework` is `not_relevant`, one with `maybe` is `uncertain`, anything else `relevant`; the
panel echoes `named_in_brief`. `long_reason` names get a reason longer than 30 words. Every
request's parameters are kept, so tests can check what reached the "model".
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from pigtail.llm.api import BatchItemResult, BatchStatus, build_params
from pigtail.llm.types import BackendResponse


def answer(prompt_text: str) -> dict[str, Any]:
    start, end = prompt_text.index("["), prompt_text.rindex("]") + 1
    cands = json.loads(prompt_text[start:end])
    out = []
    for c in cands:
        name = (c.get("name") or "").lower()
        verdict = "relevant"
        if "framework" in name:
            verdict = "not_relevant"
        elif "maybe" in name:
            verdict = "uncertain"
        reason = "A command-line tool that checks configuration files."
        if "yaml-guard" in name:
            reason = " ".join(["word"] * 45)  # too long: cut to 30 words
        out.append(
            {
                "id": c["id"],
                "verdict": verdict,
                "reason": reason,
                "distance": 0 if verdict == "relevant" else 2,
                "panel": c.get("named_in_brief") or "field",
            }
        )
    return {"verdicts": out}


@dataclass
class RelevanceBatchBackend:
    name: str = "api"
    supports_batch: bool = True
    polls_until_end: int = 0
    submitted: list[list[tuple[str, dict[str, Any]]]] = field(default_factory=list)
    polls: dict[str, int] = field(default_factory=dict)
    standard_calls: list[dict[str, Any]] = field(default_factory=list)
    drop_ids: set[str] = field(default_factory=set)  # local ids the "model" forgets once

    def params(self, **kw: Any) -> dict[str, Any]:
        return build_params(max_tokens=4000, **kw)

    def prompts(self) -> list[str]:
        return [p["messages"][0]["content"] for b in self.submitted for _, p in b]

    def complete(self, **kw: Any) -> BackendResponse:
        self.standard_calls.append(kw)
        return BackendResponse(
            data=answer(kw["prompt"]),
            model=kw["model"],
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.0002,
        )

    def submit_batch(self, requests: list[tuple[str, dict[str, Any]]]) -> str:
        self.submitted.append(list(requests))
        return f"msgbatch_fake{len(self.submitted)}"

    def batch_status(self, batch_id: str) -> BatchStatus:
        self.polls[batch_id] = self.polls.get(batch_id, 0) + 1
        ended = self.polls[batch_id] > self.polls_until_end
        return BatchStatus(batch_id, "ended" if ended else "in_progress", {"succeeded": 1})

    def batch_results(self, batch_id: str) -> Iterator[BatchItemResult]:
        n = int(batch_id.removeprefix("msgbatch_fake"))
        for cid, params in reversed(self.submitted[n - 1]):
            data = answer(params["messages"][0]["content"])
            if self.drop_ids:
                data["verdicts"] = [v for v in data["verdicts"] if v["id"] not in self.drop_ids]
                self.drop_ids = set()
            yield BatchItemResult(
                cid,
                "succeeded",
                response=BackendResponse(
                    data=data,
                    model=params["model"],
                    input_tokens=3000,
                    output_tokens=1400,
                    cost_usd=0.0024,
                    batch_id=batch_id,
                ),
            )

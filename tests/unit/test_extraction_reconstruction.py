from datetime import date

from pigtail.domain.ids import new_id
from pigtail.domain.time import day_range
from pigtail.policies.loader import default_registry
from pigtail.research.builder import BundleBuilder, EvidenceSpec
from pigtail.research.extraction import _norm, quote_in_text
from pigtail.research.normalization import merge_duplicate_claims
from pigtail.research.reconstruction import (
    Reconstruction,
    RXEvent,
    RXMetric,
    RXOutcome,
    apply_reconstruction,
    build_input,
    number_supported,
)


def test_quote_verification():
    text = _norm("We launched on Product Hunt in March 2021 and doubled our users — from 1,500 to 3,000.")
    assert quote_in_text("launched on Product Hunt in March 2021", text)
    assert quote_in_text("doubled our users - from 1,500 to 3,000", text)
    assert not quote_in_text("launched on Hacker News in March 2021", text)
    assert not quote_in_text("short", text)


def test_number_support():
    assert number_supported(20700, ["Revenue reached $20.7K MRR"])
    assert number_supported(1500000, ["1.5M users"])
    assert number_supported(36560, ["36,560 users"])
    assert not number_supported(40000, ["36,560 users"])


def _builder():
    pol = default_registry()
    t = {
        "id": new_id(),
        "kind": "product",
        "name": "T",
        "canonical_url": "https://t.example",
        "domain": "t.example",
        "description": None,
        "primary_repository_id": None,
        "external_ids": [],
        "aliases": [],
    }
    b = BundleBuilder(target=t)
    s = b.add_source(
        "https://t.example/blog",
        surface_key="web",
        source_type="first_party",
        policy=pol.policy_for("https://t.example/blog", "web"),
    )
    f = b.add_fetch(s, status="success")
    return b, s, f


def test_reconstruction_drops_unsupported_objects_and_caps_attribution():
    b, s, f = _builder()
    c1 = b.add_claim(
        "T reached 1,000 users in May 2022.",
        kind="metric",
        time=day_range(date(2022, 5, 1)),
        evidence=[EvidenceSpec(s["id"], f["id"], "company_measured", "primary_direct", "text_fragment", "1,000 users")],
    )
    c2 = b.add_claim(
        "T launched on Product Hunt on May 3, 2022.",
        kind="launch",
        time=day_range(date(2022, 5, 3)),
        evidence=[EvidenceSpec(s["id"], f["id"], "documented", "primary_direct", "text_fragment", "launched")],
    )
    inp = build_input(b, "T", [])
    ref = {v: k for k, v in inp.claim_refs.items()}
    rx = Reconstruction(
        events=[
            RXEvent(
                event_type="product_hunt_launch",
                title="PH launch",
                summary="s",
                date="2022-05-03",
                date_end="",
                date_label="",
                claims=[ref[c2]],
                is_negative_sensitive=False,
            ),
            RXEvent(
                event_type="launch",
                title="Invented",
                summary="s",
                date="2022-01-01",
                date_end="",
                date_label="",
                claims=["c999"],
                is_negative_sensitive=False,
            ),
        ],
        metrics=[
            RXMetric(
                metric_key="users",
                label="Users",
                value=1000,
                value_text="",
                unit="count",
                currency="",
                date="2022-05",
                date_label="",
                claims=[ref[c1]],
            ),
            RXMetric(
                metric_key="users",
                label="Users",
                value=5000,
                value_text="",
                unit="count",
                currency="",
                date="2022-05",
                date_label="",
                claims=[ref[c1]],
            ),
        ],
        people=[],
        company_stages=[],
        strategy_phases=[],
        tactics=[],
        growth_engines=[],
        outcomes=[
            RXOutcome(summary="PH caused growth", event_ref="n1", attribution="company_attributed", claims=[ref[c2]])
        ],
        constraints=[],
        conflicts=[],
        missing=[],
    )
    dropped = apply_reconstruction(b, rx, inp)
    assert len(b.c["events"]) == 1
    assert len(b.c["metric_snapshots"]) == 1 and b.c["metric_snapshots"][0]["value_numeric"] == 1000
    assert dropped["metric_value_not_in_claims"] == 1
    assert b.c["outcomes"][0]["causal_attribution"] == "weakly_associated"
    assert b.c["outcomes"][0]["event_id"] == b.c["events"][0]["id"]


def test_duplicate_claims_merge_but_conflicting_numbers_do_not():
    b, s, f = _builder()
    s2 = b.add_source(
        "https://news.example/x",
        surface_key="web",
        source_type="news",
        policy=default_registry().policy_for("https://news.example/x", "web"),
    )
    f2 = b.add_fetch(s2, status="success")
    t = day_range(date(2022, 5, 1))
    b.add_claim(
        "T reached 1,000 users in May 2022.",
        kind="metric",
        time=t,
        evidence=[EvidenceSpec(s["id"], f["id"], "company_measured", "primary_direct", "text_fragment", "a")],
    )
    b.add_claim(
        "T reached 1,000 users in May 2022",
        kind="metric",
        time=t,
        evidence=[EvidenceSpec(s2["id"], f2["id"], "third_party_reported", "independent_direct", "text_fragment", "b")],
    )
    b.add_claim(
        "T reached 1,200 users in May 2022.",
        kind="metric",
        time=t,
        evidence=[EvidenceSpec(s2["id"], f2["id"], "third_party_reported", "independent_direct", "text_fragment", "c")],
    )
    assert merge_duplicate_claims(b) == 1
    assert len(b.c["claims"]) == 2
    merged = b.c["claims"][0]
    assert len({e["source_id"] for e in b.evidence_for(merged["id"])}) == 2


def test_merge_remaps_references():
    b, s, f = _builder()
    t = day_range(date(2022, 5, 1))
    ev = EvidenceSpec(s["id"], f["id"], "documented", "primary_direct", "text_fragment", "a")
    s2 = b.add_source(
        "https://other.example/x",
        surface_key="web",
        source_type="news",
        policy=default_registry().policy_for("https://other.example/x", "web"),
    )
    f2 = b.add_fetch(s2, status="success")
    c1 = b.add_claim("T launched on Show HN in May 2022.", kind="launch", time=t, evidence=[ev])
    c2 = b.add_claim(
        "T launched on Show HN in May 2022",
        kind="launch",
        time=t,
        evidence=[EvidenceSpec(s2["id"], f2["id"], "documented", "primary_direct", "text_fragment", "b")],
    )
    obj = b.add("events", {"claim_ids": [c2]})
    assert merge_duplicate_claims(b) == 1
    assert obj["claim_ids"] == [c1]


def test_metrics_come_from_their_own_call_on_claims_with_numbers():
    import asyncio

    from pigtail.research.reconstruction import Interpretation, Metrics, Timeline, build_input, reconstruct

    b, s, f = _builder()
    b.add_claim(
        "T reached 1,000 users in May 2022.",
        kind="metric",
        time=day_range(date(2022, 5, 1)),
        evidence=[EvidenceSpec(s["id"], f["id"], "company_measured", "primary_direct", "text_fragment", "1,000 users")],
    )
    b.add_claim(
        "T is loved by its founders.",
        kind="positioning",
        time=day_range(date(2022, 5, 1)),
        evidence=[EvidenceSpec(s["id"], f["id"], "documented", "primary_direct", "text_fragment", "loved")],
    )
    inp = build_input(b, "T", [])
    assert len(inp.metric_lines) == 1 and "1,000 users" in inp.metric_lines[0]
    metric = RXMetric(
        metric_key="users",
        label="Users",
        value=1000,
        value_text="",
        unit="count",
        currency="",
        date="2022-05",
        date_label="",
        claims=["c1"],
    )
    prompts: dict[str, str] = {}

    class Fake:
        async def structured(self, request):
            prompts[request.purpose] = request.prompt
            if request.output_type is Metrics:
                return Metrics(metrics=[metric])
            if request.output_type is Timeline:
                return Timeline(events=[], people=[], conflicts=[], missing=[])
            assert request.output_type is Interpretation
            return Interpretation(
                company_stages=[], strategy_phases=[], tactics=[], growth_engines=[], outcomes=[], constraints=[]
            )

    rx = asyncio.run(reconstruct(Fake(), inp))  # type: ignore[arg-type]
    assert rx.metrics == [metric]
    assert "Completeness matters" in prompts["metric reconstruction"]
    assert "loved by its founders" not in prompts["metric reconstruction"]  # only claims that state a number

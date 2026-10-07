"""Publication gate acceptance tests (PRD "Public Report Sanitizer / Publication Gate", §16 AT-PUB-01..22)."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest
import yaml

from pigtail.bundle.validator import validate_data
from pigtail.bundle.writer import dump_json
from pigtail.domain.ids import new_id
from pigtail.policies.loader import load_policies
from pigtail.publication import THIRD_PARTY_NOTICE
from pigtail.publication.gate import PublicationGate
from pigtail.publication.models import ReviewEntry, ReviewFile
from pigtail.publication.runner import check_run_publication, promote, run_gate
from pigtail.publication.verify import verify_example_dir, verify_examples_root
from pigtail.renderer.view_model import build_view_model

# ---- fixture helpers --------------------------------------------------------------------------------


def add_source(b, url, *, surface="web", stype="first_party", key="web", mode="paraphrase_link_excerpt", **kw):
    sid, fid = new_id(), new_id()
    b["sources"].append(
        {
            "id": sid,
            "url": url,
            "canonical_url": url,
            "surface_key": surface,
            "source_type": stype,
            "title": kw.get("title", "A page"),
            "author": kw.get("author"),
            "published_at": kw.get("published_at"),
            "discovered_at": "2026-10-01T00:00:00Z",
            "policy": {
                "policy_key": key,
                "policy_version": "1",
                "coverage_tier": "B",
                "retention_mode": "metadata_and_excerpt",
                "public_display_mode": mode,
            },
        }
    )
    status = kw.get("status", "success")
    b["source_fetches"].append(
        {
            "id": fid,
            "source_id": sid,
            "retrieved_at": "2026-10-01T00:00:00Z",
            "status": status,
            "http_status": 200 if status == "success" else None,
            "content_hash": "sha256:" + "a" * 64 if status == "success" else None,
            "etag": None,
            "last_modified": None,
            "parser_version": "pigtail-0.1.0",
            "retention_mode": "metadata_and_excerpt",
            "retained_artifact_path": None,
            "error_code": None,
        }
    )
    return sid, fid


def add_claim(b, text, evidence, *, kind="other", negative=False, review="machine_extracted", status="active", **kw):
    cid = new_id()
    b["claims"].append(
        {
            "id": cid,
            "statement": text,
            "claim_kind": kind,
            "subject_ref": kw.get("subject", {"type": "target", "id": b["target"]["id"]}),
            "time": kw.get("time", {"start": None, "end": None, "precision": "unknown", "label": None}),
            "review_state": review,
            "extraction_certainty": 0.9,
            "is_negative_sensitive": negative,
            "status": status,
        }
    )
    for sid, fid, *rest in evidence:
        opts = rest[0] if rest else {}
        b["evidence_links"].append(
            {
                "id": new_id(),
                "target_ref": {"type": "claim", "id": cid},
                "claim_id": cid,
                "source_id": sid,
                "source_fetch_id": fid,
                "evidence_class": opts.get("cls", "documented"),
                "source_directness": opts.get("direct", "primary_direct"),
                "corroboration": "single_source",
                "inference_strength": "explicit",
                "causal_attribution": "unknown",
                "locator": {"kind": "text_fragment", "value": opts.get("loc", text[:40])},
                "excerpt": opts.get("excerpt"),
                "notes": None,
            }
        )
    return cid


def add_metric(b, key, label, value, cid, when="2024-03-01T00:00:00Z"):
    b["metric_snapshots"].append(
        {
            "id": new_id(),
            "metric_key": key,
            "label": label,
            "value_numeric": value,
            "value_text": None,
            "unit": "currency",
            "currency": "USD",
            "time": {"start": when, "end": "2024-03-31T23:59:59Z", "precision": "month", "label": None},
            "repository_id": None,
            "claim_ids": [cid],
            "review_state": "machine_extracted",
        }
    )


def gate(b, review=None, policies=None):
    assert validate_data(b).ok, validate_data(b).errors
    return PublicationGate(copy.deepcopy(b), review=review, policies=policies, input_hash="sha256:test").run()


def review_of(result, **decisions):
    """Review file approving findings by rule: decisions = {finding_id or rule_id: (decision, public_text)}."""
    items = []
    for i in result.review_items:
        d = decisions.get(i.finding_id) or decisions.get(i.rule_id)
        if d:
            dec, text = d if isinstance(d, tuple) else (d, None)
            items.append(i.model_copy(update={"decision": dec, "public_text": text, "reviewer": "tester"}))
    return ReviewFile(policy_version="0.1.0", items=items)


def public_text(p: dict) -> str:
    """All visible-ish text of a projection (URLs removed)."""
    return re.sub(r"https?://\S+", " ", json.dumps(p, ensure_ascii=False))


def visible(html: str) -> str:
    return re.sub(r"https?://[^\s\"'<>]+", " ", html)


def claim_ids(result) -> set[str]:
    return {c["id"] for c in result.public_bundle["claims"]}


@pytest.fixture
def b(bundle_dict):
    return bundle_dict


@pytest.fixture
def blog(b):
    return add_source(b, "https://example.dev/blog/story", title="Our story")


# ---- AT-PUB-01 ----------------------------------------------------------------------------------------


def test_at01_local_output_unchanged_and_inputs_untouched(bundle, tmp_path):
    from pigtail.renderer.render import ForensicRenderer, render_html

    html = render_html(bundle)
    assert "not affiliated" not in html and "Research Bundle <code>" in html
    run = tmp_path / "run"
    ForensicRenderer().render(bundle, run)
    (run / "research-bundle.json").write_text(dump_json(bundle.model_dump(mode="json")))
    (run / "run.json").write_text("{}")
    before = {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()}
    run_gate(run)
    after = {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()}
    assert before == after
    # The analysis pipeline does not depend on the publication package.
    src = Path(__file__).resolve().parents[2] / "src" / "pigtail"
    for f in [*src.glob("research/*.py"), src / "cli.py"]:
        assert "pigtail.publication" not in f.read_text(), f


# ---- AT-PUB-02, AT-PUB-18, AT-PUB-20, AT-PUB-22 -------------------------------------------------------


def _passing_run(bundle, tmp_path) -> Path:
    from pigtail.renderer.render import ForensicRenderer

    run = tmp_path / "run"
    ForensicRenderer().render(bundle, run)
    (run / "research-bundle.json").write_text(dump_json(bundle.model_dump(mode="json")))
    (run / "run.json").write_text("{}")
    res = run_gate(run)
    assert res.result.status == "PASS", [f.reason for f in res.result.audit.findings if f.resolution is None]
    return run


def test_at02_raw_report_rejected_by_example_checks(bundle, tmp_path):
    raw = tmp_path / "examples" / "raw"
    raw.mkdir(parents=True)
    from pigtail.renderer.render import ForensicRenderer

    ForensicRenderer().render(bundle, raw)
    (raw / "research-bundle.json").write_text(dump_json(bundle.model_dump(mode="json")))
    (raw / "metadata.json").write_text("{}")
    problems = verify_example_dir(raw)
    assert any("research-bundle.json must not be published" in p for p in problems)
    assert any("publication-manifest.json is missing" in p for p in problems)


def test_at18_notice_required_and_at20_manifest_integrity(bundle, tmp_path):
    run = _passing_run(bundle, tmp_path)
    root = tmp_path / "examples"
    dest = root / "example"
    assert promote(run, dest, {"slug": "example"}) == []
    assert any("NOTICE.md is missing" in p for p in verify_examples_root(root))
    (root / "NOTICE.md").write_text("# Notice\n\n" + THIRD_PARTY_NOTICE + "\n")
    assert verify_examples_root(root) == []
    # Editing a passed report invalidates it, both in the run and in the published copy.
    (dest / "report.html").write_text((dest / "report.html").read_text() + "<!-- edit -->")
    assert any("changed after the publication gate" in p for p in verify_example_dir(dest))
    (run / "publication" / "report.html").write_text("tampered")
    assert any("changed after" in p for p in check_run_publication(run))
    assert promote(run, tmp_path / "other", {}) != []
    # Changing the research bundle invalidates the pass too.
    run2 = _passing_run(bundle, tmp_path / "second")
    (run2 / "research-bundle.json").write_text((run2 / "research-bundle.json").read_text() + " ")
    assert any("research-bundle.json changed" in p for p in check_run_publication(run2))


def test_at22_no_force_bypass():
    root = Path(__file__).resolve().parents[2]
    for f in [*(root / "src" / "pigtail" / "publication").glob("*.py"), root / "scripts" / "publication_gate.py"]:
        assert not re.search(r"--force|force[_-]?publish|\bforce\s*[:=]", f.read_text(), re.I), f
    for f in (root / "scripts" / "publish_example.py",):
        assert "--force" not in f.read_text()


# ---- AT-PUB-03, AT-PUB-04 -----------------------------------------------------------------------------


def test_at03_failed_source_only_claim(b):
    sid, fid = add_source(b, "https://news.example.org/a", stype="news", status="network_failed")
    cid = add_claim(b, "Example signed a large customer.", [(sid, fid, {"direct": "independent_direct"})])
    res = gate(b)
    assert cid not in claim_ids(res)
    assert res.status == "BLOCKED"
    assert gate(b, review_of(res, **{"PUB-001": "exclude"})).status == "PASS"
    ok = gate(b, review_of(res, **{"PUB-001": "mark_manually_verified"}))
    assert ok.status == "PASS"
    c = next(c for c in ok.public_bundle["claims"] if c["id"] == cid)
    assert c["manually_verified"] is True


def test_at04_failed_source_does_not_poison_corroborated_claim(b, blog):
    bad = add_source(b, "https://gone.example.org/x", stype="news", status="network_failed")
    cid = add_claim(b, "Example opened an office in Ghent.", [blog, bad])
    res = gate(b)
    assert res.status == "PASS"
    assert cid in claim_ids(res)
    links = [el for el in res.public_bundle["evidence_links"] if el["claim_id"] == cid]
    assert [el["source_id"] for el in links] == [blog[0]]


# ---- AT-PUB-05, AT-PUB-06 -----------------------------------------------------------------------------


def test_at05_negative_machine_claim_excluded(b, blog):
    cid = add_claim(b, "Example had a data breach.", [blog], negative=True)
    res = gate(b)
    assert cid not in claim_ids(res)
    assert "data breach" not in public_text(res.public_bundle)
    assert res.status == "PASS"


def test_at06_human_verified_negative_needs_publication_approval(b, blog):
    cid = add_claim(b, "Example had a data breach in 2025.", [blog], negative=True, review="human_verified")
    res = gate(b)
    assert res.status == "NEEDS_REVIEW" and cid not in claim_ids(res)
    ok = gate(b, review_of(res, **{"PUB-003": "approve_as_is"}))
    assert ok.status == "PASS" and cid in claim_ids(ok)


# ---- AT-PUB-07 ----------------------------------------------------------------------------------------


def test_at07_personal_finance_removed_company_metrics_kept(b, blog):
    s1 = add_claim(b, "The founders spent $50,000 of personal savings.", [blog])
    s2 = add_claim(b, "The founders went without a salary for nine months.", [blog])
    mrr = add_claim(b, "Example reached $10,000 MRR in March 2024.", [blog], kind="metric")
    add_metric(b, "mrr", "MRR", 10000, mrr)
    res = gate(b)
    ids = claim_ids(res)
    assert s1 not in ids and s2 not in ids and mrr in ids
    text = public_text(res.public_bundle) + visible(res.report_html)
    assert "savings" not in text and "salary" not in text
    assert any(m["metric_key"] == "mrr" for m in res.public_bundle["metric_snapshots"])
    # approve_as_is is not accepted for a required exclusion.
    finding = next(f for f in res.audit.findings if f.rule_id == "PUB-005" and f.object_ref.id == s1)
    entry = ReviewEntry(
        finding_id=finding.finding_id, rule_id="PUB-005", object_ref=finding.object_ref, decision="approve_as_is"
    )
    bad = gate(b, ReviewFile(policy_version="0.1.0", items=[entry]))
    assert bad.status == "BLOCKED" and s1 not in claim_ids(bad)


# ---- AT-PUB-08, AT-PUB-09 -----------------------------------------------------------------------------


def test_at08_usernames_and_at09_people_minimized(b):
    """Owner decision 2026-10-07: founders and executives are named in their company role; private
    individuals (contributors, team members, posters) are anonymised; no people list is published."""
    pid = new_id()
    b["people"] += [
        {"id": pid, "name": "Jane Doe", "role": "Founder", "external_ids": [], "claim_ids": []},
        {"id": new_id(), "name": "Chandra Patel", "role": "Community contributor", "external_ids": [], "claim_ids": []},
        {"id": new_id(), "name": "Sam Lee", "role": "Support engineer", "external_ids": [], "claim_ids": []},
    ]
    hn = add_source(
        b,
        "https://news.ycombinator.com/item?id=1",
        surface="hacker_news",
        stype="hn_story",
        key="hacker_news",
        title="Show HN: Example",
        author="throwaway42",
    )
    founder_post = add_source(
        b,
        "https://news.ycombinator.com/item?id=2",
        surface="hacker_news",
        stype="hn_story",
        key="hacker_news",
        title="Launch HN: Example",
        author="jdoe",
    )
    interview = add_source(b, "https://press.example.org/jane", stype="founder_interview", title="Jane Doe on Example")
    team = add_source(b, "https://example.dev/blog/team", title="Meet Sam Lee, our support engineer")
    c1 = add_claim(b, "A Show HN post by throwaway42 reached 300 points.", [hn], kind="hn_post")
    c2 = add_claim(
        b,
        "Jane Doe, founder of Example, said Chandra Patel added Docker support.",
        [interview],
        subject={"type": "person", "id": pid},
    )
    c3 = add_claim(b, "A Launch HN post by jdoe reached 500 points.", [founder_post], kind="hn_post")
    c4 = add_claim(b, "Sam Lee answers support tickets within an hour.", [team])
    res = gate(b)
    assert res.status == "PASS", [f.reason for f in res.audit.findings if f.resolution is None]
    text = public_text(res.public_bundle) + visible(res.report_html)
    for name in ("Chandra", "Sam Lee", "throwaway42", "jdoe"):
        assert name not in text, name
    assert "people" not in res.public_bundle
    assert all(s["author"] is None for s in res.public_bundle["sources"])
    stmts = {c["id"]: c["statement"] for c in res.public_bundle["claims"]}
    assert stmts[c1] == "A Show HN post reached 300 points."
    assert stmts[c2] == "Jane Doe, founder of Example, said a community contributor added Docker support."
    assert stmts[c3] == "A Launch HN post by Jane Doe reached 500 points."
    assert stmts[c4] == "A support team member answers support tickets within an hour."
    titles = {s["id"]: s["title"] for s in res.public_bundle["sources"]}
    assert titles[interview[0]] == "Jane Doe on Example"  # real titles stay traceable
    assert titles[team[0]] == "Meet a support team member, our support engineer"


# ---- AT-PUB-10, AT-PUB-11, AT-PUB-12 ------------------------------------------------------------------


def test_at10_attribution_not_strengthened(b, blog):
    cid = add_claim(b, "Example has never paid for ads.", [blog])
    b["narrative"]["flywheel"] = {
        "text": "Example never paid for ads.",
        "claim_ids": [cid],
        "inference_strength": "explicit",
    }
    res = gate(b)
    c = next(c for c in res.public_bundle["claims"] if c["id"] == cid)
    assert c["public_attribution"] == "According to the project"
    assert res.status == "NEEDS_REVIEW"  # the narrative states it in Pigtail's voice
    assert res.public_bundle["narrative"]["flywheel"] is None
    text = "The company says it has never paid for ads."
    ok = gate(b, review_of(res, **{"PUB-009": ("approve_public_text", text)}))
    assert ok.status == "PASS" and ok.public_bundle["narrative"]["flywheel"]["text"] == text
    assert "According to the project" in ok.report_html  # shown with the claim in the evidence drawer


def test_at11_third_party_metrics_labelled_and_at12_conflicts_kept(b, blog):
    press = add_source(b, "https://news.example.org/revenue", stype="news", title="Example revenue")
    own = add_claim(b, "Example reported $1M ARR in March 2024.", [blog], kind="metric")
    third = add_claim(
        b,
        "A newspaper reported Example at $3M ARR in March 2024.",
        [(*press, {"cls": "third_party_reported", "direct": "independent_direct"})],
        kind="metric",
    )
    add_metric(b, "arr", "ARR", 1_000_000, own)
    add_metric(b, "arr", "ARR", 3_000_000, third)
    b["conflicts"].append(
        {
            "id": new_id(),
            "summary": "ARR figures differ.",
            "claim_ids": [own, third],
            "related_refs": [],
            "status": "unresolved",
        }
    )
    res = gate(b)
    vm = build_view_model(res.public_bundle, public=True)
    arr = {m["attribution"]: m for m in vm["metric_cards"] if m["key"].startswith("arr")}
    assert set(arr) == {"company reported", "third-party reported"}
    assert [r["value"] for r in arr["company reported"]["rows"]] == ["$1M"]
    assert [r["value"] for r in arr["third-party reported"]["rows"]] == ["$3M"]
    assert len(res.public_bundle["conflicts"]) == 1 and len(res.public_bundle["conflicts"][0]["claim_ids"]) == 2
    assert "third-party reported" in res.report_html


# ---- AT-PUB-13, AT-PUB-14, AT-PUB-15 ------------------------------------------------------------------


def test_at13_suspicious_dates(b):
    sid, fid = add_source(b, "https://example.dev/blog/new-year", published_at="2024-01-01T00:00:00Z")
    cid = add_claim(
        b,
        "Example shipped v2.",
        [(sid, fid)],
        time={"start": "2024-01-01T00:00:00Z", "end": "2024-01-01T23:59:59Z", "precision": "day", "label": None},
    )
    res = gate(b)
    s = next(s for s in res.public_bundle["sources"] if s["id"] == sid)
    c = next(c for c in res.public_bundle["claims"] if c["id"] == cid)
    assert s["published_at"] is None
    assert c["time"]["precision"] == "year"
    assert "Jan 01, 2024" not in res.report_html


def test_at14_excerpt_cap_and_one_per_source(b):
    sid, fid = add_source(b, "https://example.dev/blog/long")
    long = " ".join(f"word{i}" for i in range(45))
    c1 = add_claim(b, "Example wrote a long post.", [(sid, fid, {"excerpt": long})])
    c2 = add_claim(b, "Example wrote a second thing.", [(sid, fid, {"excerpt": "second excerpt here"})])
    res = gate(b)
    exs = [el["excerpt"] for el in res.public_bundle["evidence_links"] if el["source_id"] == sid and el["excerpt"]]
    assert len(exs) == 1 and len(exs[0].split()) <= 15
    assert "word15" not in res.report_html and "second excerpt here" not in res.report_html
    assert {c1, c2} <= claim_ids(res)
    assert not [
        el
        for el in res.public_bundle["evidence_links"]
        if el["locator"]["value"] and el["locator"]["kind"] == "text_fragment"
    ]


def test_at15_source_policy_never_upgraded(b):
    policies = load_policies()
    strict = policies["web"].model_copy(deep=True)
    strict.public_display.mode = "link_only"
    policies = {**policies, "web": strict}
    sid, fid = add_source(b, "https://example.dev/blog/quoted")
    add_claim(b, "Example quoted itself.", [(sid, fid, {"excerpt": "a quotable line"})])
    unknown = add_source(b, "https://example.dev/blog/unknown", key="no_such_policy")
    add_claim(b, "Example has an unknown policy source.", [(*unknown, {"excerpt": "another quote"})])
    res = gate(b, policies=policies)
    assert "a quotable line" not in res.report_html and "another quote" not in res.report_html
    modes = {s["id"]: s["policy"]["public_display_mode"] for s in res.public_bundle["sources"]}
    assert modes[sid] == "link_only" and modes[unknown[0]] == "link_only"


# ---- AT-PUB-16, AT-PUB-17 -----------------------------------------------------------------------------


def test_at16_internal_metadata_removed_and_at17_notice(b):
    res = gate(b)
    blob = res.report_html + json.dumps(res.public_bundle)
    for needle in (
        b["bundle_id"],
        b["run"]["run_id"],
        b["run"]["model"]["model"],
        "total_cost",
        "model_input_tokens",
        "github_api_requests",
    ):
        assert needle not in blob, needle
    assert "bundle_id" not in res.public_bundle and "run" not in res.public_bundle
    assert f"Pigtail is not affiliated with or endorsed by {b['target']['name']}." in res.report_html


# ---- AT-PUB-19, AT-PUB-21, determinism, review round trip ---------------------------------------------


def test_at19_narrative_dependency_pruning(b, blog):
    keep = add_claim(b, "Example grew through its blog.", [blog])
    gone = add_claim(b, "The founders lived on savings for a year.", [blog])
    b["narrative"]["did_differently"] = {
        "text": "Example grew through its blog while the team bootstrapped.",
        "claim_ids": [keep, gone],
        "inference_strength": "moderate_inference",
    }
    b["narrative"]["key_takeaways"].append(
        {"text": "Bootstrapping is hard.", "claim_ids": [gone], "inference_strength": "weak_inference"}
    )
    res = gate(b)
    assert res.status == "NEEDS_REVIEW"
    assert res.public_bundle["narrative"]["did_differently"] is None
    assert all("Bootstrapping is hard" not in t["text"] for t in res.public_bundle["narrative"]["key_takeaways"])
    ok = gate(b, review_of(res, **{"PUB-017": "approve_as_is"}))
    assert ok.status == "PASS"
    assert ok.public_bundle["narrative"]["did_differently"]["claim_ids"] == [keep]


def test_at21_every_change_is_audited(b, blog):
    add_claim(b, "The founders lived on savings.", [blog])
    add_claim(b, "Example had an outage.", [blog], negative=True)
    res = gate(b)
    found = {(f.object_ref.type, f.object_ref.id) for f in res.audit.findings}
    public_claims = claim_ids(res)
    for c in b["claims"]:
        if c["id"] not in public_claims:
            assert ("claim", c["id"]) in found
    pub = {c["id"]: c for c in res.public_bundle["claims"]}
    for c in b["claims"]:
        if c["id"] in pub and pub[c["id"]]["statement"] != c["statement"]:
            assert ("claim", c["id"]) in found
    for p in b["people"]:
        assert ("person", p["id"]) in found
    assert all(f.rule_id and f.reason for f in res.audit.findings)


def test_projection_is_deterministic(b, blog):
    add_claim(b, "Example never paid for ads.", [blog])
    one, two = gate(b), gate(b)
    assert dump_json(one.public_bundle) == dump_json(two.public_bundle)
    assert one.report_html == two.report_html


def test_review_file_round_trip(bundle_dict, tmp_path):
    from pigtail.bundle.models import ResearchBundle
    from pigtail.renderer.render import ForensicRenderer

    b = bundle_dict
    blog = add_source(b, "https://example.dev/blog/rent")
    add_claim(b, "The founder could finally pay rent from Example.", [blog])
    add_claim(b, "The founder said he may move the project elsewhere when time permits.", [blog])
    run = tmp_path / "run"
    ForensicRenderer().render(ResearchBundle.model_validate(b), run)
    (run / "research-bundle.json").write_text(dump_json(b))
    (run / "run.json").write_text("{}")
    first = run_gate(run)
    assert first.result.status == "NEEDS_REVIEW"
    path = run / "publication" / "publication-review.yaml"
    data = yaml.safe_load(path.read_text())
    assert all(i["decision"] == "pending" for i in data["items"])
    for i in data["items"]:
        i["decision"] = "exclude"
        i["reviewer"] = "tester"
    path.write_text(yaml.safe_dump(data))
    second = run_gate(run)
    assert second.result.status == "PASS"
    assert second.result.audit.reviewers == ["tester"]
    assert "tester" not in (run / "publication" / "report.html").read_text()
    assert check_run_publication(run) == []


def test_shared_surname_is_never_guessed(b, blog):
    b["people"] += [
        {"id": new_id(), "name": "Rahul Roe", "role": "Founder and CEO", "external_ids": [], "claim_ids": []},
        {
            "id": new_id(),
            "name": "Gaurav Roe",
            "role": "Founding team member, growth",
            "external_ids": [],
            "claim_ids": [],
        },
    ]
    cid = add_claim(b, "Roe conceived Example while working elsewhere.", [blog])
    res = gate(b)
    assert res.status == "NEEDS_REVIEW" and cid not in claim_ids(res)
    item = next(i for i in res.review_items if i.object_ref.id == cid)
    assert "more than one person" in item.reason
    text = "The founder conceived Example while working elsewhere."
    ok = gate(b, review_of(res, **{item.finding_id: ("approve_public_text", text)}))
    assert next(c for c in ok.public_bundle["claims"] if c["id"] == cid)["statement"] == text


def test_ai_review_fills_only_allowed_decisions(bundle_dict, tmp_path):
    import asyncio

    from pigtail.bundle.models import ResearchBundle
    from pigtail.publication.ai_review import AIDecision, ai_review
    from pigtail.renderer.render import ForensicRenderer

    b = bundle_dict
    blog = add_source(b, "https://example.dev/blog/plans")
    add_claim(b, "The founder planned to move the project elsewhere.", [blog])
    add_claim(b, "The founder could finally pay rent from Example.", [blog])
    run = tmp_path / "run"
    ForensicRenderer().render(ResearchBundle.model_validate(b), run)
    (run / "research-bundle.json").write_text(dump_json(b))
    (run / "run.json").write_text("{}")
    assert run_gate(run).result.status == "NEEDS_REVIEW"

    class FakeModel:
        provider_key, model_id = "fake", "fake"

        def __init__(self):
            self.calls = 0

        async def structured(self, request):
            self.calls += 1
            if "pay rent" in request.prompt:  # an option the item does not allow: must stay pending
                return AIDecision(decision="mark_manually_verified", public_text="", rationale="checked")
            return AIDecision(
                decision="approve_public_text",
                public_text="The founder wrote that the project may move elsewhere.",
                rationale="attributed",
            )

    model = FakeModel()
    s = asyncio.run(ai_review(run, model, "AI (fake) on behalf of the owner; human review pending"))
    assert (s.decided, s.skipped) == (1, 1)
    data = yaml.safe_load((run / "publication" / "publication-review.yaml").read_text())
    done = [i for i in data["items"] if i["decision"] != "pending"]
    assert done[0]["reviewer"].startswith("AI (fake)") and done[0]["rationale"].startswith("[AI]")
    again = run_gate(run).result
    assert again.status == "NEEDS_REVIEW" and again.unresolved == 1


def test_estimate_sites_are_never_company_reported(b, blog):
    latka = add_source(b, "https://getlatka.com/companies/example", stype="third_party_analysis")
    cid = add_claim(
        b,
        "As of May 2021, Example had MRR of roughly $23,300.",
        [(*latka, {"cls": "company_measured", "direct": "primary_indirect"})],
        kind="metric",
    )
    add_metric(b, "mrr", "MRR", 23300, cid)
    res = gate(b)
    m = next(
        m for m in res.public_bundle["metric_snapshots"] if m["metric_key"] == "mrr" and m["value_numeric"] == 23300
    )
    assert m["public_attribution"] == "third-party estimate"
    c = next(c for c in res.public_bundle["claims"] if c["id"] == cid)
    assert c["public_attribution"] is None  # not "According to the company"


def test_uncited_sources_are_not_listed_and_name_casing(b, blog):
    comment = add_source(
        b,
        "https://news.ycombinator.com/item?id=9",
        surface="hacker_news",
        stype="hn_comment",
        key="hacker_news",
        title="Interesting! I haven't made anything serious yet",
    )
    b["target"]["name"] = "example"
    add_claim(b, "Example shipped its first release.", [blog])
    res = gate(b)
    assert comment[0] not in {s["id"] for s in res.public_bundle["sources"]}
    assert "Interesting!" not in res.report_html
    assert res.public_bundle["target"]["name"] == "Example"
    assert "endorsed by Example." in res.report_html


def test_reviewer_edit_survives_when_its_finding_disappears(b, blog):
    cid = add_claim(b, "Example shipped v1 to customers.", [blog])
    orphan = ReviewEntry(
        finding_id="manual-1",
        rule_id="PUB-REVIEW",
        object_ref={"type": "claim", "id": cid},
        field="statement",
        decision="approve_public_text",
        public_text="Example says it shipped v1 to customers.",
        reviewer="tester",
    )
    res = gate(b, ReviewFile(policy_version="0.1.0", items=[orphan]))
    assert next(c for c in res.public_bundle["claims"] if c["id"] == cid)["statement"] == orphan.public_text
    assert any(f.rule_id == "PUB-REVIEW" and f.object_ref.id == cid for f in res.audit.findings)
    assert any(i.finding_id == "manual-1" for i in res.review_items)  # kept in the review file


def test_relayed_figures_wiki_dates_and_header_cards(b, blog):
    pod = add_source(b, "https://podcast.example.org/ep1", stype="founder_interview", title="Founder podcast")
    cid = add_claim(
        b, "The New York Times reported Example had fewer than 15,000 customers in 2019.", [pod], kind="metric"
    )
    b["metric_snapshots"].append(
        {
            **b["metric_snapshots"][-1],
            "id": new_id(),
            "metric_key": "customers_nyt",
            "label": "Customers (NYT report)",
            "value_numeric": 15000,
            "currency": None,
            "unit": "customers",
            "claim_ids": [cid],
        }
    )
    wiki = add_source(
        b, "https://en.wikipedia.org/wiki/Example", stype="third_party_analysis", published_at="2012-12-24T10:00:00Z"
    )
    add_claim(b, "Example is a software company.", [wiki])
    res = gate(b)
    m = next(m for m in res.public_bundle["metric_snapshots"] if m["metric_key"] == "customers_nyt")
    assert m["public_attribution"] == "third-party reported"
    assert next(s for s in res.public_bundle["sources"] if s["id"] == wiki[0])["published_at"] is None
    vm = build_view_model(res.public_bundle, public=True)
    assert all("third-party" not in s["label"] for s in vm["stats"])
    assert "Self-description" in res.report_html

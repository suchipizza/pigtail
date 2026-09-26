"""M22 pure parts: rubric and prompt versioning (R4.6), model inputs without handles (R4.6,
Directive §8.1), README trimming (R15.10), reason cut, chunking, discovery queries and window
(R4.5), Show HN parsing without authors, candidate refs, and the run-scope estimate (R18.5)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pigtail.briefs.candidates import (
    Candidate,
    clean_description,
    gh_ref,
    merge_signals,
    normalize_ref,
    parse_named,
    strip_owner,
)
from pigtail.briefs.discovery import DiscoveryConfig, github_queries, search_terms, window_bounds
from pigtail.briefs.estimate import estimate, render_scope_text, run_scope
from pigtail.briefs.model import load_brief_text
from pigtail.briefs.relevance import (
    CHUNK,
    SYSTEM,
    RelevanceOutput,
    build_rubric,
    candidate_input,
    chunk_refs,
    clean_readme,
    cut_reason,
    prompt_for,
    readme_excerpt,
    requests_for,
)
from pigtail.connectors.github import parse_repo_node, parse_search_page, repo_meta_query
from pigtail.connectors.hn import parse_show_hn_page
from pigtail.llm.redact import alias_redact
from pigtail.llm.stages import stage_for, time_sensitive
from pigtail.llm.types import schema_of

EXAMPLE = Path(__file__).resolve().parents[2] / "docs" / "examples" / "brief-example.yaml"


def brief():  # type: ignore[no-untyped-def]
    return load_brief_text(EXAMPLE.read_text()).model_copy(update={"version": 1})


def test_r4_6_rubric_from_brief_fields_only_and_versioned():
    b = brief()
    rubric = build_rubric(b)
    for part in (
        b.field.core_field,
        *b.field.include,
        *b.field.exclude,
        *b.field.widening_steps,
        b.project.target_users.primary,
    ):
        assert part in rubric
    assert b.expansion is not None and b.expansion.problem_statement is not None
    assert " ".join(b.expansion.problem_statement.split()) in rubric
    for private in (b.project.name, b.field.reference_cases[0].name, "150", "own_audience"):
        assert private not in rubric
    p1, v1 = prompt_for(b)
    p2, v2 = prompt_for(b)
    assert v1 == v2 and v1.startswith("rubric-v1-") and p1.fingerprint == p2.fingerprint
    edited = b.model_copy(update={"field": b.field.model_copy(update={"exclude": ["x tools"]})})
    p3, v3 = prompt_for(edited)
    assert v3 != v1 and p3.fingerprint != p1.fingerprint  # a new rubric is a new cache key
    assert p1.id == "relevance_filter" and p1.version == "1" and v1 in p1.context


def test_r4_6_structured_output_schema_is_closed_with_enums():
    s = schema_of(RelevanceOutput)
    item = s["$defs"]["CandidateVerdict"]
    assert item["additionalProperties"] is False
    assert item["properties"]["verdict"]["enum"] == ["relevant", "not_relevant", "uncertain"]
    assert item["properties"]["distance"]["enum"] == [0, 1, 2]
    assert item["properties"]["panel"]["enum"] == ["field", "exemplar", "reference"]
    assert "30 words" in SYSTEM


def test_r15_8_relevance_job_runs_on_the_relevance_stage_in_batches():
    assert stage_for("relevance_filter") == "relevance" and not time_sensitive("relevance_filter")


def test_r4_6_model_input_has_no_owner_login_and_alias_redaction_removes_handles():
    c = Candidate(
        ref="gh:ghuser042/tomlcheck",
        repo_full_name="ghuser042/tomlcheck",
        metadata={
            "description": "TOML linter by GhUser042",
            "topics": ["toml"],
            "owner_type": "User",
        },
    )
    raw = (
        b"# tomlcheck\n![badge](https://img.shields.io/x.svg)\nBy ghuser042, @ghuser001 and "
        b"https://github.com/ghuser007 (mail ghuser042@example.com)."
    )
    excerpt, tv = readme_excerpt(raw, ["toml"])
    assert tv and excerpt and "img.shields" not in excerpt
    item = candidate_input(c, "c01", excerpt)
    assert item["name"] == "tomlcheck" and item["owner_type"] == "User"
    text = alias_redact(json.dumps(item), "github")
    for handle in ("ghuser042", "GhUser042", "ghuser001", "ghuser007", "example.com"):
        assert handle not in text, handle
    assert "[owner]" in text and "[email]" in text
    assert "@user" in text and "[profile:github:user" in text  # per-call aliases (alias-v1)
    assert strip_owner("org-x-tools by org-x", "org-x") == "org-x-tools by [owner]"


def test_r15_10_readme_is_cleaned_and_cut():
    raw = (
        "<p align=center><img src=x></p>\n[![b](u)](l)\n## Install\n" + "config lint " * 400
    ).encode()
    assert "<img" not in clean_readme(raw) and "[![" not in clean_readme(raw)
    excerpt, _ = readme_excerpt(raw, ["config"])
    assert excerpt is not None and len(excerpt) <= 1_200
    assert readme_excerpt(None, []) == (None, None)


def test_reason_cut_to_30_words_and_chunks_of_20():
    short, cut = cut_reason("  fits   the core  field ")
    assert (short, cut) == ("fits the core field", False)
    long, cut = cut_reason(" ".join(str(i) for i in range(40)))
    assert cut and len(long.replace(" …", "").split()) == 30
    chunks = chunk_refs([f"gh:o/r{i}" for i in range(45)])
    assert CHUNK == 20 and [len(c) for c in chunks] == [20, 20, 5]
    assert requests_for(0) == 0 and requests_for(41) == 3


def test_r4_5_queries_window_terms():
    b = brief()
    start, end = window_bounds(b, date(2026, 9, 25))
    assert (start.date(), end.date()) == (date(2025, 3, 25), date(2026, 9, 25))
    terms = search_terms(b)
    assert "config linter" in terms and b.field.core_field in terms
    assert len(terms) == len({t.lower() for t in terms})  # de-duplicated
    qs = github_queries(b, start, end, DiscoveryConfig(min_stars=10))
    kinds = {k for _, k, _ in qs}
    assert kinds == {"keyword", "topic"}
    assert all(
        "created:2025-03-25T00:00:00Z..2026-09-25T23:59:59Z stars:10..*" in q for _, _, q in qs
    )
    assert next((k, kd) for k, kd, _ in qs if k == "topic:yaml") == ("topic:yaml", "topic")
    assert len({k for k, _, _ in qs}) == len(qs)  # stable, unique checkpoint keys


def test_r4_5_show_hn_parse_keeps_project_fields_only():
    body = {
        "hits": [
            {
                "objectID": "1",
                "title": "Show HN: X &amp; Y",
                "url": "https://github.com/Org-S/X",
                "points": 5,
                "created_at_i": 1760000000,
                "author": "hnuser701",
                "_tags": ["author_hnuser701"],
                "story_text": "by hnuser701",
            }
        ],
        "nbHits": 1,
    }
    stories, n = parse_show_hn_page(json.dumps(body).encode())
    assert (
        n == 1 and stories[0].repo_full_name == "org-s/x" and stories[0].title == "Show HN: X & Y"
    )
    assert "hnuser701" not in repr(stories)


def test_github_search_and_graphql_metadata_are_project_level():
    page = {
        "total_count": 1,
        "items": [
            {
                "id": 1,
                "full_name": "ghuser1/a",
                "description": "d",
                "topics": ["t"],
                "language": "Go",
                "owner": {"login": "ghuser1", "type": "User"},
            }
        ],
    }
    sp = parse_search_page(json.dumps(page).encode())
    assert sp.items[0].description == "d" and sp.items[0].topics == ("t",)
    assert "login" not in repr(sp.items[0])
    q, v = repo_meta_query(["org-a/x", "org-b/y"])
    assert "r1: repository(owner: $o1, name: $n1)" in q and v == {
        "o0": "org-a",
        "n0": "x",
        "o1": "org-b",
        "n1": "y",
    }
    node = {
        "databaseId": 7,
        "nameWithOwner": "org-a/x",
        "stargazerCount": 3,
        "repositoryTopics": {"nodes": [{"topic": {"name": "cli"}}]},
        "owner": {"__typename": "Organization", "login": "org-a"},
    }
    m = parse_repo_node(node)
    assert m is not None and m.topics == ("cli",) and m.owner_type == "Organization"
    assert parse_repo_node(None) is None


def test_candidate_refs_signals_and_descriptions():
    assert normalize_ref("https://github.com/Org-S/Yaml-Guard/tree/main") == "gh:org-s/yaml-guard"
    assert normalize_ref("Org-S/Yaml-Guard") == gh_ref("org-s/yaml-guard")
    assert normalize_ref("named:reference:3") == "named:reference:3"
    assert parse_named("named:exemplar:0") == ("exemplar", 0) and parse_named("gh:a/b") is None
    merged = merge_signals(
        [{"source": "github_topic", "term": "yaml", "rank": 1}],
        [
            {"source": "github_topic", "term": "yaml", "rank": 9},
            {"source": "show_hn", "hn_item_id": 7},
        ],
    )
    assert len(merged) == 2 and merged[0]["rank"] == 1
    d = clean_description(
        "Linter by ghuser9, mail ghuser9@example.com, see @someone " + "x" * 400, "ghuser9"
    )
    assert d is not None and len(d) <= 300 and "ghuser9" not in d and "@someone" not in d


def test_r18_5_run_scope_estimate_for_the_m22_stages():
    b = brief()
    e = estimate(b)
    s = run_scope(e, ["discovery", "relevance", "shortlist"])
    assert s["label"] == "estimate" and s["stages"] == ["discovery", "relevance", "shortlist"]
    assert s["llm"]["model"] == "claude-haiku-4-5-20251001" and s["llm"]["mode"] == "batch"
    assert s["llm"]["llm_calls"] == requests_for(e.candidates)
    assert s["api_usd"] > 0 and s["requires_approval"] and s["within_caps"] is True
    assert s["github_requests"]["core"] == e.candidates  # one README per candidate
    only = run_scope(e, ["shortlist"])
    assert only["llm"] is None and only["api_usd"] == 0 and not only["requires_approval"]
    assert "relevance filter:" in render_scope_text(s)
    capped = estimate(b, brief_spent_usd=149.99)
    assert run_scope(capped, ["relevance"])["within_caps"] is False

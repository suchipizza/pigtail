import json
import re

from typer.testing import CliRunner

from pigtail.cli import _rewrite_argv, app
from pigtail.renderer.render import render_html

runner = CliRunner()


def test_shorthand_rewrites_to_analyze():
    assert _rewrite_argv(["https://github.com/a/b"]) == ["analyze", "https://github.com/a/b"]
    assert _rewrite_argv(["--no-open", "tally.so"]) == ["analyze", "--no-open", "tally.so"]
    assert _rewrite_argv(["validate", "x.json"]) == ["validate", "x.json"]
    assert _rewrite_argv(["--help"]) == ["--help"]


def test_version():
    r = runner.invoke(app, ["version"])
    assert r.exit_code == 0 and "bundle_schema_version   0.1.0" in r.stdout


def test_validate_and_render_without_credentials(tmp_path, bundle, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    p = tmp_path / "research-bundle.json"
    p.write_text(bundle.model_dump_json())
    r = runner.invoke(app, ["validate", str(p)])
    assert r.exit_code == 0, r.stdout
    r = runner.invoke(app, ["render", str(p), "--output", str(tmp_path / "out")])
    assert r.exit_code == 0, r.stdout
    html = (tmp_path / "out" / "report.html").read_text()
    assert "Example" in html
    idx = json.loads((tmp_path / "out" / "source-index.json").read_text())
    assert len(idx["sources"]) == len(bundle.sources)


def test_validate_exit_code_on_invalid(tmp_path, bundle_dict):
    bundle_dict["events"][0]["claim_ids"] = []
    p = tmp_path / "b.json"
    p.write_text(json.dumps(bundle_dict))
    r = runner.invoke(app, ["validate", str(p)])
    assert r.exit_code == 6


def test_validate_rejects_other_version(tmp_path, bundle_dict):
    bundle_dict["schema_version"] = "9.9.9"
    p = tmp_path / "b.json"
    p.write_text(json.dumps(bundle_dict))
    assert runner.invoke(app, ["validate", str(p)]).exit_code == 6


def test_report_is_self_contained(bundle):
    html = render_html(bundle)
    # No external scripts, stylesheets, fonts or images: the report must work from file:// offline.
    assert not re.search(r"<script[^>]+src=", html)
    assert not re.search(r"<link[^>]+href=", html)
    assert not re.search(r"<img[^>]+src=[\"']https?:", html)
    assert "@import" not in html
    assert "Observed event near this growth period" in html  # tooltip wording (spec §22.1)
    assert "Cause of spike" not in html


def test_report_shows_star_chart_and_unknowns(bundle):
    html = render_html(bundle)
    assert 'id="starChart"' in html
    assert "Research gaps and uncertainty" in html
    assert "Example launched on Show HN." in html


def test_hostile_strings_are_escaped(bundle_dict):
    from pigtail.bundle.models import ResearchBundle

    bundle_dict["sources"][0]["url"] = "javascript:alert(1)"
    bundle_dict["sources"][0]["title"] = "<script>alert(2)</script>"
    bundle_dict["claims"][0]["statement"] = "</script><script>alert(3)</script>"
    html = render_html(ResearchBundle.model_validate(bundle_dict))
    assert 'href="javascript:' not in html and '"url":"javascript:' not in html
    assert "<script>alert(2)</script>" not in html
    assert "</script><script>alert(3)" not in html

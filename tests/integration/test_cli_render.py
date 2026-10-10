import json
import re

import pytest
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
    assert 'class="brand" href="https://suchipizza.github.io/pigtail/"' in html  # logo links to the website
    assert 'class="pill gh-link" href="https://github.com/suchipizza/pigtail"' in html
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


def test_private_report_says_who_generated_it(bundle):
    from pigtail import CONTACT_EMAIL, LEGAL_URL

    html = render_html(bundle)
    assert html.index("AI-assisted analysis based on public sources.") > html.index('<footer class="footer">')
    assert "Machine-extracted research." not in html  # the banner replaced the old footer disclaimer
    assert "Not reviewed or endorsed by Pigtail's maintainers." in html
    assert LEGAL_URL in html
    assert CONTACT_EMAIL not in html  # the correction address is for published reports only


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


def test_model_short_names_and_ids_resolve():
    from pigtail.providers.models.anthropic import DEFAULT_MODEL, resolve_model

    assert DEFAULT_MODEL == "claude-sonnet-5-5"
    assert resolve_model("opus") == "claude-opus-5-5"
    assert resolve_model("Sonnet") == "claude-sonnet-5-5"
    assert resolve_model("claude-haiku-4-5") == "claude-haiku-4-5"
    assert resolve_model("claude-future-9") == "claude-future-9"  # new models are not blocked


def test_choose_model_keeps_default_and_accepts_all_forms():
    from pigtail.cli import _choose_model
    from pigtail.config import Config
    from pigtail.errors import UsageError

    cfg = Config()
    _choose_model(cfg, None)
    assert (cfg.model.provider, cfg.model.model) == ("anthropic", "claude-sonnet-5-5")
    for choice in ("opus", "claude-opus-5-5", "anthropic/claude-opus-5-5", "anthropic/opus"):
        cfg = Config()
        _choose_model(cfg, choice)
        assert (cfg.model.provider, cfg.model.model) == ("anthropic", "claude-opus-5-5"), choice
    cfg = Config()
    cfg.model.model = "haiku"  # short names work in pigtail.toml too
    _choose_model(cfg, None)
    assert cfg.model.model == "claude-haiku-4-5"
    with pytest.raises(UsageError):
        _choose_model(Config(), "gpt-5")


def test_unknown_model_fails_before_any_paid_work(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")  # gitleaks:allow
    r = runner.invoke(app, ["analyze", "tally.so", "--model", "gpt-5", "--no-open"])
    assert r.exit_code == 2
    assert "Unknown model" in r.output and "pigtail models" in r.output


def test_models_command_lists_choices_and_default():
    r = runner.invoke(app, ["models"])
    assert r.exit_code == 0
    for name in ("sonnet (default)", "opus", "haiku", "fable", "claude-sonnet-5-5", "claude-opus-5-5"):
        assert name in r.stdout
    assert _rewrite_argv(["models"]) == ["models"]

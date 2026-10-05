import pytest

from pigtail.config import Config, load_config
from pigtail.errors import UsageError
from pigtail.policies.loader import default_registry, load_policies, surface_for_url


def test_policies_load_and_cover_required_fields():
    pols = load_policies()
    assert {"github", "hacker_news", "reddit", "product_hunt", "web"} <= set(pols)
    for p in pols.values():
        assert p.coverage_tier in "ABC"
        assert p.obligations.reviewed_at


def test_link_only_surfaces_never_allow_excerpts():
    reg = default_registry()
    for key in ("reddit", "product_hunt", "x"):
        p = reg.policies[key]
        assert not p.implementation.enabled_by_default
        assert not p.access.automated_retrieval_allowed
        assert p.clip_excerpt("some long text") is None


def test_excerpt_is_clipped_to_policy():
    p = default_registry().policies["web"]
    out = p.clip_excerpt("x" * 1000)
    assert out is not None and len(out) <= p.retention.max_excerpt_chars


@pytest.mark.parametrize(
    "url,key",
    [
        ("https://github.com/a/b", "github"),
        ("https://news.ycombinator.com/item?id=1", "hacker_news"),
        ("https://old.reddit.com/r/x", "reddit"),
        ("https://www.producthunt.com/posts/x", "product_hunt"),
        ("https://x.com/a/status/1", "x"),
        ("https://blog.example.com/p", "web"),
    ],
)
def test_surface_mapping(url, key):
    assert surface_for_url(url) == key


def test_config_rejects_literal_secret(tmp_path):
    p = tmp_path / "pigtail.toml"
    p.write_text('[model]\napi_key = "sk-123"\n')
    with pytest.raises(UsageError):
        load_config(str(p))


def test_config_rejects_unknown_section(tmp_path):
    p = tmp_path / "pigtail.toml"
    p.write_text("[enginee]\nmax_sources = 3\n")
    with pytest.raises(UsageError):
        load_config(str(p))


def test_config_defaults_and_overrides(tmp_path):
    assert Config().model.provider == "anthropic"
    p = tmp_path / "pigtail.toml"
    p.write_text('[engine]\nmax_sources = 7\n[model]\nmodel = "claude-sonnet-5-5"\n')
    cfg = load_config(str(p))
    assert cfg.engine.max_sources == 7 and cfg.model.model == "claude-sonnet-5-5"

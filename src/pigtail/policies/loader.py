"""Load version-controlled source policies and map URLs to surfaces."""

from __future__ import annotations

from functools import cache
from importlib import resources
from pathlib import Path
from urllib.parse import urlparse

import yaml
from pydantic import ValidationError

from pigtail import SOURCE_POLICY_VERSION
from pigtail.errors import SourcePolicyError
from pigtail.policies.models import SourcePolicy

REPO_POLICY_DIR = Path(__file__).resolve().parents[3] / "source-policies"

# Host suffix -> surface key. Anything else is the generic `web` surface.
HOST_SURFACES: tuple[tuple[str, str], ...] = (
    ("github.com", "github"),
    ("api.github.com", "github"),
    ("news.ycombinator.com", "hacker_news"),
    ("hn.algolia.com", "hacker_news"),
    ("reddit.com", "reddit"),
    ("redd.it", "reddit"),
    ("producthunt.com", "product_hunt"),
    ("x.com", "x"),
    ("twitter.com", "x"),
)


def default_policy_dir() -> Path:
    if REPO_POLICY_DIR.is_dir():
        return REPO_POLICY_DIR
    return Path(str(resources.files("pigtail").joinpath("_data/source-policies")))


def load_policies(directory: Path | None = None) -> dict[str, SourcePolicy]:
    directory = directory or default_policy_dir()
    if not directory.is_dir():
        raise SourcePolicyError(f"Source-policy directory not found: {directory}")
    policies: dict[str, SourcePolicy] = {}
    for path in sorted(directory.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            policy = SourcePolicy.model_validate(data)
        except (yaml.YAMLError, ValidationError) as exc:
            raise SourcePolicyError(f"Invalid source policy {path.name}: {exc}") from exc
        if policy.key in policies:
            raise SourcePolicyError(f"Duplicate source policy key {policy.key!r} in {path.name}")
        policies[policy.key] = policy
    if "web" not in policies:
        raise SourcePolicyError("A `web` policy is required as the fallback for unknown hosts.")
    return policies


def surface_for_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    for suffix, key in HOST_SURFACES:
        if host == suffix or host.endswith("." + suffix):
            return key
    return "web"


class PolicyRegistry:
    """Implements the SourcePolicyResolver protocol (spec §29.3)."""

    version = SOURCE_POLICY_VERSION

    def __init__(self, policies: dict[str, SourcePolicy]):
        self.policies = policies

    def policy_for(self, url: str, surface_key: str | None = None) -> SourcePolicy:
        key = surface_key or surface_for_url(url)
        return self.policies.get(key) or self.policies["web"]

    def enabled(self, key: str) -> bool:
        p = self.policies.get(key)
        return bool(p and p.implementation.enabled_by_default)


@cache
def default_registry() -> PolicyRegistry:
    return PolicyRegistry(load_policies())

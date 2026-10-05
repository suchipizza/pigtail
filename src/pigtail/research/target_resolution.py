"""Resolve a raw CLI target into a product/repository identity (PRD §8.1)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from urllib.parse import urlparse

import httpx

from pigtail.errors import TargetResolutionError
from pigtail.logging import get_logger
from pigtail.providers.fetchers.web import USER_AGENT
from pigtail.providers.github.client import GitHubClient, GitHubError, RepositorySnapshot
from pigtail.repository.identity import parse_github, parse_short

log = get_logger("target")

SUPPORTED_HELP = (
    "Pigtail accepts a GitHub repository URL (https://github.com/owner/repo) or a product domain/URL "
    "(example.com or https://example.com)."
)
GENERIC_REPO_NAMES = {
    "ui",
    "app",
    "web",
    "core",
    "main",
    "docs",
    "site",
    "server",
    "cli",
    "sdk",
    "api",
    "monorepo",
}
_DOMAIN = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")


@dataclass
class ResolvedTarget:
    kind: str  # repository | product
    name: str
    canonical_url: str
    domain: str | None
    description: str | None = None
    aliases: list[str] = field(default_factory=list)
    repo: RepositorySnapshot | None = None
    homepage_html_title: str | None = None
    linked_repos: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def classify(raw: str) -> tuple[str, object]:
    raw = raw.strip()
    if not raw:
        raise TargetResolutionError("No target given.", hint=SUPPORTED_HELP)
    gh = parse_github(raw)
    if gh:
        return "repository", gh
    if "github.com" in raw.lower():
        raise TargetResolutionError(f"{raw!r} is not a GitHub repository URL.", hint=SUPPORTED_HELP)
    if not raw.startswith(("http://", "https://")):
        short = parse_short(raw)
        if short and "." not in raw:
            return "repository", short
    url = raw if raw.startswith(("http://", "https://")) else "https://" + raw
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if not _DOMAIN.match(host):
        raise TargetResolutionError(f"{raw!r} is not a supported target.", hint=SUPPORTED_HELP)
    return "product", url


def _meta(html: str, *names: str) -> str | None:
    for n in names:
        m = re.search(
            rf'<meta[^>]+(?:property|name)=["\']{re.escape(n)}["\'][^>]+content=["\']([^"\']+)["\']',
            html,
            re.I,
        ) or re.search(
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{re.escape(n)}["\']',
            html,
            re.I,
        )
        if m:
            return unescape(m.group(1)).strip()
    return None


def _product_name(html: str, host: str) -> str:
    site = _meta(html, "og:site_name", "application-name")
    if site and len(site) <= 40:
        return site
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if m:
        title = unescape(re.sub(r"\s+", " ", m.group(1))).strip()
        for sep in (" | ", " – ", " — ", " - ", ": ", " · "):
            if sep in title:
                parts = [p.strip() for p in title.split(sep) if p.strip()]
                label = host.split(".")[0].lower()
                for p in parts:
                    if label in p.lower().replace(" ", ""):
                        return p if len(p) <= 40 else p[:40]
                return parts[0][:40]
        if len(title) <= 30:
            return title
    label = host.split(".")[0]
    return label[:1].upper() + label[1:]


async def resolve(raw: str, github: GitHubClient) -> ResolvedTarget:
    kind, value = classify(raw)
    if kind == "repository":
        assert isinstance(value, tuple)
        owner, repo = value
        try:
            snap = await github.repository(owner, repo)
        except GitHubError as exc:
            if exc.status == 404:
                raise TargetResolutionError(
                    f"GitHub repository {owner}/{repo} was not found (or is private).", hint=SUPPORTED_HELP
                ) from exc
            raise TargetResolutionError(
                f"Could not read {owner}/{repo} from GitHub: {exc}",
                hint="Set GITHUB_TOKEN to raise the rate limit; run `pigtail doctor`.",
            ) from exc
        domain = None
        if snap.homepage:
            hp = snap.homepage if snap.homepage.startswith("http") else "https://" + snap.homepage
            domain = (urlparse(hp).hostname or "").removeprefix("www.") or None
            if domain and "github" in domain:
                domain = None
        name = snap.name
        owner_brand = snap.owner if snap.owner != snap.owner.lower() else snap.owner.capitalize()
        label = domain.split(".")[0].lower() if domain else ""
        if label == snap.owner.lower() and snap.name.lower() not in label:
            name = owner_brand  # e.g. plausible/analytics with homepage plausible.io -> "Plausible"
        elif name.lower() in GENERIC_REPO_NAMES:
            name = f"{snap.owner}/{snap.name}"
        aliases = sorted({snap.full_name, snap.name} - {name})
        if domain:
            aliases.append(domain)
        return ResolvedTarget(
            kind="repository",
            name=name,
            canonical_url=snap.url,
            domain=domain,
            description=snap.description,
            aliases=aliases,
            repo=snap,
        )

    url = str(value)
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    html = ""
    final = f"https://{host}"
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as http:
            r = await http.get(f"https://{host}/")
            if r.status_code < 400:
                html = r.text[:400_000]
                final = str(r.url).split("?")[0].rstrip("/")
    except httpx.HTTPError as exc:
        raise TargetResolutionError(
            f"Could not reach {host}: {type(exc).__name__}.", hint="Check the domain and your connection."
        ) from exc
    if not html:
        raise TargetResolutionError(f"{host} did not return a web page.", hint=SUPPORTED_HELP)
    final_host = (urlparse(final).hostname or host).removeprefix("www.")
    name = _product_name(html, final_host)
    desc = _meta(html, "og:description", "description", "twitter:description")
    linked = []
    for o, rp in re.findall(r"https?://(?:www\.)?github\.com/([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100})", html):
        rp = rp.removesuffix(".git")
        if o.lower() in ("sponsors", "orgs", "features", "about", "topics", "marketplace", "login"):
            continue
        if (o, rp) not in linked:
            linked.append((o, rp))
    t = ResolvedTarget(
        kind="product",
        name=name,
        canonical_url=f"https://{final_host}",
        domain=final_host,
        description=desc,
        aliases=[final_host],
        homepage_html_title=name,
        linked_repos=linked[:5],
    )
    # A product site that links to a repository whose homepage points back is the same project.
    for o, rp in linked[:3]:
        try:
            snap = await github.repository(o, rp)
        except GitHubError:
            continue
        hp = (snap.homepage or "").lower()
        if final_host in hp and not snap.is_fork:
            t.repo = snap
            t.aliases.append(snap.full_name)
            break
    return t

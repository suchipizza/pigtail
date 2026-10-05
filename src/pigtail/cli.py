"""Pigtail command-line interface (spec §25)."""

from __future__ import annotations

import asyncio
import json
import os
import platform
import sys
import webbrowser
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from pigtail import (
    BUNDLE_SCHEMA_VERSION,
    ENGINE_VERSION,
    ONTOLOGY_VERSION,
    RESEARCH_POLICY_VERSION,
    SOURCE_POLICY_VERSION,
)
from pigtail.config import load_config, load_dotenv
from pigtail.errors import DOCS_URL, BundleValidationError, PigtailError
from pigtail.logging import setup_logging

RESERVED = {"analyze", "render", "validate", "doctor", "version"}

app = typer.Typer(
    name="pigtail",
    help="Reconstruct how a product or GitHub repository grew, from public evidence.",
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode=None,
)
console = Console(stderr=True, highlight=False)
out = Console(highlight=False)


def _fail(err: PigtailError) -> None:
    console.print(f"[bold red]Error:[/] {err.message}")
    for e in getattr(err, "errors", [])[:25]:
        console.print(f"  • {e}")
    if err.hint:
        console.print(f"[dim]{err.hint}[/]")
    raise typer.Exit(err.exit_code)


@app.command()
def analyze(
    target: Annotated[
        str,
        typer.Argument(help="GitHub repository URL (https://github.com/owner/repo) or a product domain (example.com)."),
    ],
    model: Annotated[
        str | None, typer.Option("--model", help="Model as provider/model, e.g. anthropic/claude-opus-5-5.")
    ] = None,
    output: Annotated[
        Path | None, typer.Option("--output", help="Output root directory (default ./pigtail-output).")
    ] = None,
    source: Annotated[
        list[str] | None,
        typer.Option("--source", help="A known source URL to include. Repeatable. Verified like any other source."),
    ] = None,
    config: Annotated[str | None, typer.Option("--config", help="Path to a pigtail.toml config file.")] = None,
    no_open: Annotated[bool, typer.Option("--no-open", help="Do not open the report in a browser when done.")] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", help="Show more operational detail (never model reasoning).")
    ] = False,
) -> None:
    """Research a target and write a Research Bundle plus a local HTML report."""
    from pigtail.research.orchestrator import run_analysis

    load_dotenv()
    try:
        cfg = load_config(config)
        setup_logging(
            "INFO" if verbose else cfg.logging.level if cfg.logging.level != "INFO" else "WARNING",
            cfg.logging.format,
        )
        if model:
            if "/" not in model:
                raise PigtailError("--model must look like provider/model, e.g. anthropic/claude-opus-5-5")
            cfg.model.provider, cfg.model.model = model.split("/", 1)
        if output:
            cfg.engine.output_root = str(output)
        result = asyncio.run(run_analysis(target, cfg, extra_sources=source or [], console=console, verbose=verbose))
    except PigtailError as err:
        _fail(err)
        return
    except KeyboardInterrupt:
        console.print("Interrupted.")
        raise typer.Exit(130) from None

    out.print(f"\n[bold]Done:[/] {result.status.replace('_', ' ')}")
    out.print(f"  Report:          {result.report_path}")
    out.print(f"  Research Bundle: {result.bundle_path}")
    out.print(f"  Cost: ${result.cost:.2f} · {result.duration_s / 60:.1f} min")
    if result.report_path and (cfg.engine.open_report_on_success and not no_open) and sys.stdout.isatty():
        webbrowser.open(result.report_path.resolve().as_uri())


@app.command()
def render(
    bundle: Annotated[Path, typer.Argument(help="Path to research-bundle.json")],
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Directory to write report.html into (default: next to the bundle)."),
    ] = None,
) -> None:
    """Render report.html from an existing Research Bundle. No credentials or network needed."""
    from pigtail.bundle.reader import read_bundle
    from pigtail.renderer.render import ForensicRenderer

    try:
        b, report = read_bundle(bundle)
        result = ForensicRenderer().render(b, output or bundle.parent)
    except PigtailError as err:
        _fail(err)
        return
    for w in report.warnings[:10]:
        console.print(f"[yellow]warning:[/] {w}")
    out.print(f"Wrote {result.report_path}")


@app.command()
def validate(
    bundle: Annotated[Path, typer.Argument(help="Path to research-bundle.json")],
    published: Annotated[
        bool, typer.Option("--published", help="Also apply the stricter rules for published examples.")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print the validation report as JSON.")] = False,
) -> None:
    """Check a Research Bundle against schema 0.1.0 and the integrity rules. Exit 0 only if valid."""
    from pigtail.bundle.migrations import check_version
    from pigtail.bundle.reader import load_raw
    from pigtail.bundle.validator import validate_data

    try:
        data = load_raw(bundle)
        check_version(data)
    except PigtailError as err:
        _fail(err)
        return
    rep = validate_data(data, published=published)
    if as_json:
        out.print_json(json.dumps({"ok": rep.ok, "errors": rep.errors, "warnings": rep.warnings}))
    else:
        for w in rep.warnings:
            out.print(f"[yellow]warning:[/] {w}")
        if rep.ok:
            out.print(f"[green]valid[/] Research Bundle {data.get('bundle_id')} (schema {data.get('schema_version')})")
    if not rep.ok:
        _fail(BundleValidationError(f"{bundle} is not a valid Research Bundle", errors=rep.errors))


@app.command()
def doctor(
    config: Annotated[str | None, typer.Option("--config", help="Path to a pigtail.toml config file.")] = None,
    offline: Annotated[bool, typer.Option("--offline", help="Skip network connectivity checks.")] = False,
) -> None:
    """Check your setup: Python, config, credentials, output folder, source policies, connectivity."""
    import httpx

    from pigtail.policies.loader import load_policies

    load_dotenv()
    ok = True

    def line(status: str, name: str, detail: str) -> None:
        color = {"ok": "green", "warn": "yellow", "fail": "red"}[status]
        out.print(f"[{color}]{status.upper():>4}[/]  {name}: {detail}")

    py = sys.version_info
    if py >= (3, 12):
        line("ok", "Python", f"{platform.python_version()}")
    else:
        ok = False
        line("fail", "Python", f"{platform.python_version()} (3.12 or newer required)")

    try:
        cfg = load_config(config)
        line("ok", "Config", cfg.source_path or "built-in defaults (no pigtail.toml found)")
    except PigtailError as err:
        line("fail", "Config", err.message)
        raise typer.Exit(2) from None

    from pigtail.providers.models.registry import SUPPORTED_MODEL_PROVIDERS
    from pigtail.providers.search.registry import SUPPORTED_SEARCH_PROVIDERS

    if cfg.model.provider not in SUPPORTED_MODEL_PROVIDERS:
        ok = False
        line(
            "fail",
            "Model provider",
            f"{cfg.model.provider!r} is not supported ({', '.join(SUPPORTED_MODEL_PROVIDERS)})",
        )
    elif cfg.secret(cfg.model.api_key_env):
        line(
            "ok",
            "Model credentials",
            f"{cfg.model.api_key_env} is set · model {cfg.model.provider}/{cfg.model.model}",
        )
    else:
        ok = False
        line("fail", "Model credentials", f"{cfg.model.api_key_env} is not set. See {DOCS_URL}/quickstart.md")

    if cfg.discovery.provider not in SUPPORTED_SEARCH_PROVIDERS:
        ok = False
        line("fail", "Discovery provider", f"{cfg.discovery.provider!r} is not supported")
    elif cfg.discovery.provider == "none":
        line("warn", "Discovery", "web search disabled; only GitHub and Hacker News discovery will run")
    elif cfg.secret(cfg.discovery.api_key_env):
        line("ok", "Discovery credentials", f"{cfg.discovery.provider} via {cfg.discovery.api_key_env}")
    else:
        ok = False
        line("fail", "Discovery credentials", f"{cfg.discovery.api_key_env} is not set")

    gh = cfg.secret(cfg.github.token_env)
    line(
        "ok" if gh else "warn",
        "GitHub token",
        f"{cfg.github.token_env} is set (5,000 requests/hour)"
        if gh
        else f"{cfg.github.token_env} not set: 60 requests/hour, star history limited to small repositories",
    )

    root = Path(cfg.engine.output_root)
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".pigtail-write-test"
        probe.write_text("ok")
        probe.unlink()
        line("ok", "Output folder", f"{root.resolve()} is writable")
    except OSError as exc:
        ok = False
        line("fail", "Output folder", f"{root} is not writable: {exc}")

    try:
        pols = load_policies()
        enabled = [k for k, p in pols.items() if p.implementation.enabled_by_default]
        line("ok", "Source policies", f"{len(pols)} loaded; enabled by default: {', '.join(sorted(enabled))}")
    except PigtailError as err:
        ok = False
        line("fail", "Source policies", err.message)

    if not offline:
        checks = [
            (
                "GitHub API",
                "https://api.github.com/rate_limit",
                {"Authorization": f"Bearer {gh}"} if gh else {},
            ),
            ("Hacker News search", "https://hn.algolia.com/api/v1/search?query=pigtail&hitsPerPage=1", {}),
        ]
        if cfg.model.provider == "anthropic":
            checks.append(
                (
                    "Anthropic API",
                    "https://api.anthropic.com/v1/models?limit=1",
                    {"x-api-key": cfg.secret(cfg.model.api_key_env) or "", "anthropic-version": "2023-06-01"},
                )
            )
        for name, url, headers in checks:
            try:
                r = httpx.get(url, headers=headers, timeout=10)
                if r.status_code == 200:
                    detail = "reachable"
                    if name == "GitHub API":
                        core = r.json().get("resources", {}).get("core", {})
                        detail += f" ({core.get('remaining')}/{core.get('limit')} requests left this hour)"
                    line("ok", name, detail)
                else:
                    if name == "Anthropic API":
                        ok = False
                    line("warn" if name != "Anthropic API" else "fail", name, f"HTTP {r.status_code}")
            except httpx.HTTPError as exc:
                line("warn", name, f"unreachable ({type(exc).__name__})")

    out.print(
        "\nReady to run `pigtail <target>`." if ok else f"\nSome checks failed. See {DOCS_URL}/troubleshooting.md"
    )
    raise typer.Exit(0 if ok else 3)


@app.command()
def version() -> None:
    """Print engine and contract versions."""
    out.print(f"engine_version          {ENGINE_VERSION}")
    out.print(f"bundle_schema_version   {BUNDLE_SCHEMA_VERSION}")
    out.print(f"research_policy_version {RESEARCH_POLICY_VERSION}")
    out.print(f"source_policy_version   {SOURCE_POLICY_VERSION}")
    out.print(f"ontology_version        {ONTOLOGY_VERSION}")


def _rewrite_argv(argv: list[str]) -> list[str]:
    """`pigtail <target>` is shorthand for `pigtail analyze <target>` (spec §25.2)."""
    if not argv:
        return argv
    first = next((i for i, a in enumerate(argv) if not a.startswith("-")), None)
    if first is None:
        return argv
    if argv[first] in RESERVED:
        return argv
    return [*argv[:first], "analyze", *argv[first:]] if first == 0 else ["analyze", *argv]


def main() -> None:
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    argv = _rewrite_argv(sys.argv[1:])
    app(args=argv, prog_name="pigtail")


if __name__ == "__main__":
    main()

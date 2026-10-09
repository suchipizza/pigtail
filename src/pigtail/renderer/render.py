"""Standalone HTML forensic renderer (spec §22.1). Bundle in, one self-contained report.html out."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from jinja2 import Environment, StrictUndefined, select_autoescape
from markupsafe import Markup, escape

from pigtail import REPO_URL, WEBSITE_URL
from pigtail.bundle.models import ResearchBundle
from pigtail.bundle.writer import dump_json, source_index, validate_bundle
from pigtail.errors import BundleValidationError, RenderError
from pigtail.renderer.view_model import build_view_model


@dataclass
class RenderResult:
    report_path: Path
    source_index_path: Path


def _asset(name: str) -> str:
    return resources.files("pigtail.renderer").joinpath(f"templates/assets/{name}").read_text(encoding="utf-8")


def _cites(cites: list[dict]) -> Markup:
    if not cites:
        return Markup("")
    parts = [
        f'<button type="button" class="cite" data-claims="{escape(",".join(c["claim_ids"]))}" '
        f'title="{escape(c["title"])}" aria-label="Evidence from source {c["n"]}">{c["n"]}</button>'
        for c in cites[:6]
    ]
    return Markup('<span class="cites">' + "".join(parts) + "</span>")


def _legend_inference(block: dict | None) -> str:
    if not block:
        return ""
    return f"Evidence level: {block['inference'] or 'stated in sources'}."


def _json_for_script(data: object) -> Markup:
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    text = text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return Markup(text)


def render_html(bundle: ResearchBundle) -> str:
    return _render(build_view_model(bundle))


def render_public_html(public: dict) -> str:
    """Render a publication-gate projection (pigtail.publication). Never takes a raw Research Bundle."""
    from pigtail.publication.models import PublicReportBundle

    PublicReportBundle.model_validate(public)
    return _render(build_view_model(public, public=True))


def _render(vm: dict) -> str:
    env = Environment(
        autoescape=select_autoescape(["html", "j2"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals.update(cites=_cites, legend_inference=_legend_inference)
    template = env.from_string(
        resources.files("pigtail.renderer").joinpath("templates/report.html.j2").read_text(encoding="utf-8")
    )
    client_data = {
        "events": vm["events"],
        "claims": vm["claims"],
        "chart": vm["chart"],
        "metric_series": vm["metric_series"],
    }
    return template.render(
        vm=vm,
        links={"website": WEBSITE_URL, "repo": REPO_URL},
        css=Markup(_asset("report.css")),
        js=Markup(_asset("report.js")),
        data_json=_json_for_script(client_data),
    )


class ForensicRenderer:
    """Implements the ForensicRenderer protocol (spec §29.13)."""

    def render(self, bundle: ResearchBundle, output_dir: Path) -> RenderResult:
        report = validate_bundle(bundle)
        if not report.ok:
            raise BundleValidationError("Refusing to render an invalid bundle", errors=report.errors)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "assets").mkdir(exist_ok=True)
        try:
            html = render_html(bundle)
        except Exception as exc:
            raise RenderError(f"Rendering failed: {exc}") from exc
        report_path = output_dir / "report.html"
        report_path.write_text(html, encoding="utf-8")
        idx_path = output_dir / "source-index.json"
        idx_path.write_text(dump_json(source_index(bundle)), encoding="utf-8")
        return RenderResult(report_path=report_path, source_index_path=idx_path)

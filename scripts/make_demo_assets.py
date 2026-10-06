#!/usr/bin/env python3
"""Generate README/site screenshots and the demo GIF from a reviewed example report.

    uv run --with playwright --with pillow python scripts/make_demo_assets.py hatchet

Uses the locally installed Google Chrome through Playwright (no browser download).
"""

from __future__ import annotations

import html
import io
import json
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "assets"
SITE_PUBLIC = ROOT / "site" / "public"

TERMINAL = """<!doctype html><html><head><meta charset="utf-8"><style>
body{{margin:0;background:#16151a;color:#e9e6ef;font:15px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;padding:34px 40px}}
.p{{color:#ef6f9c}} .d{{color:#8f8b98}} b{{color:#fff}}
</style></head><body>{body}</body></html>"""


def terminal_html(target: str, lines: list[str]) -> str:
    body = f'<div><span class="p">$</span> <b>pigtail {html.escape(target)}</b></div>'
    body += "".join(f"<div>{line}</div>" for line in lines)
    return TERMINAL.format(body=body)


def shot(page, **kw) -> Image.Image:
    return Image.open(io.BytesIO(page.screenshot(**kw))).convert("RGB")


def main(slug: str) -> None:
    ex = ROOT / "examples" / "reviewed" / slug
    bundle = json.loads((ex / "public-report-bundle.json").read_text())
    target = bundle["target"]["canonical_url"]
    counts = (len(bundle["sources"]), len(bundle["claims"]), len(bundle["events"]))
    ASSETS.mkdir(parents=True, exist_ok=True)
    report = (ex / "report.html").resolve().as_uri()
    frames: list[tuple[Image.Image, int]] = []
    W, H = 1200, 760
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        stages = [
            "Resolve target",
            "Repository history",
            "Discover sources",
            "Fetch evidence",
            "Extract claims",
            "Reconstruct timeline",
            "Analyze growth",
            "Validate evidence and gaps",
            "Write Research Bundle",
            "Render report",
        ]
        shown: list[str] = []
        for i, st in enumerate(stages):
            shown.append(f'<span class="p">›</span> {st}…')
            if i in (2, 5, 9):
                page.set_content(terminal_html(target, shown))
                frames.append((shot(page), 700))
        shown.append(f"<br><b>Done:</b> {counts[0]} sources · {counts[1]} verified claims · {counts[2]} events")
        shown.append('<span class="d">  Report: pigtail-output/…/report.html</span>')
        page.set_content(terminal_html(target, shown))
        frames.append((shot(page), 1600))

        page.goto(report)
        page.wait_for_timeout(400)
        frames.append((shot(page), 1800))
        page.locator("#stars").scroll_into_view_if_needed()
        page.evaluate("document.querySelector('#stars').scrollIntoView({block: 'start'})")
        page.wait_for_timeout(300)
        frames.append((shot(page), 1800))
        markers = page.locator("#starChart g.marker")
        n = markers.count()
        preferred = page.locator(
            '#starChart g.marker[aria-label^="Show HN"], #starChart g.marker[aria-label^="Launch HN"]'
        )
        target_marker = preferred.nth(min(1, preferred.count() - 1)) if preferred.count() else markers.nth(n // 3)
        page.evaluate("window.scrollBy(0, -70)")
        target_marker.hover(force=True)
        page.wait_for_timeout(300)
        frames.append((shot(page), 2000))
        target_marker.click(force=True)
        page.wait_for_timeout(500)
        frames.append((shot(page), 2600))

        # Static README / site images at 2x.
        hi = browser.new_page(viewport={"width": 1280, "height": 900}, device_scale_factor=2)
        hi.goto(report)
        hi.wait_for_timeout(400)
        hi.locator("#stars .chart-panel").screenshot(path=str(ASSETS / "stars-and-events.png"))
        hi.locator("#stars .chart-panel").screenshot(path=str(SITE_PUBLIC / "demo-chart.png"))
        pref = hi.locator('#starChart g.marker[aria-label^="Show HN"], #starChart g.marker[aria-label^="Launch HN"]')
        (pref.nth(min(1, pref.count() - 1)) if pref.count() else hi.locator("#starChart g.marker").nth(n // 3)).click(
            force=True
        )
        hi.wait_for_timeout(500)
        hi.keyboard.press("Escape")
        hi.goto(report)
        hi.wait_for_timeout(300)
        hi.screenshot(path=str(ASSETS / "report-top.png"))
        browser.close()

    imgs = [f.resize((960, int(960 * H / W)), Image.LANCZOS) for f, _ in frames]
    pal = [im.quantize(colors=128, method=Image.Quantize.MEDIANCUT) for im in imgs]
    pal[0].save(
        ASSETS / "demo.gif",
        save_all=True,
        append_images=pal[1:],
        duration=[d for _, d in frames],
        loop=0,
        optimize=True,
    )
    print("wrote", sorted(x.name for x in ASSETS.iterdir()))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "hatchet")

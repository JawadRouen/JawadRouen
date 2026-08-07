"""Render generated profile cards at desktop and mobile widths with Playwright."""

from __future__ import annotations

import base64
import pathlib

from playwright.sync_api import sync_playwright


ROOT = pathlib.Path(__file__).parents[1]
RESULTS = ROOT / "test-results"


def svg_data(path: pathlib.Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def main() -> None:
    RESULTS.mkdir(exist_ok=True)
    cards = [svg_data(ROOT / "github-activity.svg"), svg_data(ROOT / "github-depth.svg")]
    mobile_cards = [
        svg_data(ROOT / "github-activity-mobile.svg"),
        svg_data(ROOT / "github-depth-mobile.svg"),
    ]
    html = f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>
          * {{ box-sizing: border-box; }}
          html, body {{ margin: 0; background: #0d1117; color: #f0f6fc; }}
          main {{ width: min(100%, 1016px); margin: 0 auto; padding: 24px; }}
          img {{ display: block; width: 100%; height: auto; margin: 0 0 20px; }}
          @media (max-width: 560px) {{ main {{ padding: 12px; }} }}
        </style>
      </head>
      <body>
        <main>
          <picture>
            <source media="(max-width: 600px)" srcset="{mobile_cards[0]}">
            <img alt="Engineering activity" src="{cards[0]}">
          </picture>
          <picture>
            <source media="(max-width: 600px)" srcset="{mobile_cards[1]}">
            <img alt="Five-year engineering depth" src="{cards[1]}">
          </picture>
        </main>
      </body>
    </html>
    """

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for name, width in (("desktop", 1100), ("mobile", 390)):
            page = browser.new_page(viewport={"width": width, "height": 1200}, device_scale_factor=1)
            page.set_content(html, wait_until="networkidle")
            page.screenshot(path=RESULTS / f"profile-{name}.png", full_page=True)
            overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
            if overflow:
                raise AssertionError(f"Horizontal overflow at {width}px")
            for image in page.locator("img").all():
                if image.bounding_box()["width"] > page.locator("main").bounding_box()["width"] + 0.5:
                    raise AssertionError(f"Card exceeds its container at {width}px")
            page.close()
        browser.close()


if __name__ == "__main__":
    main()

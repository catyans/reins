"""Optional local Playwright validation; requires a running simulated demo."""

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser(description="Browser checks for examples/practical_controls.py")
parser.add_argument("--demo-directory", default="output/practical-demo")
parser.add_argument("--url", default="http://127.0.0.1:8796")
args = parser.parse_args()
root = Path(args.demo_directory)
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1050}, device_scale_factor=1)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(args.url)
    page.locator("#token").fill((root / "operator.token").read_text())
    page.get_by_role("button", name="Connect", exact=True).click()
    page.get_by_text("Connected · 3 workflows", exact=False).wait_for()
    assert page.locator("#token").input_value() == ""
    loop = page.locator("article").filter(
        has=page.get_by_role("heading", name="Simulated repeated search")
    )
    page.on("dialog", lambda d: d.accept("Browser validation"))
    loop.get_by_role("button", name="Pause", exact=True).click()
    loop.get_by_role("button", name="Resume", exact=True).wait_for()
    loop.get_by_role("button", name="Resume", exact=True).click()
    loop.get_by_role("button", name="Pause", exact=True).wait_for()
    page.get_by_text("Approve a registered tool call", exact=True).click()
    page.locator("[name=task]").fill("demo-critical")
    page.locator("[name=tool]").fill("publish")
    page.locator("[name=arguments]").fill('{"record":"reviewed"}')
    page.get_by_role("button", name="Approve exact call", exact=True).click()
    page.get_by_text("Approved for 5 minutes, one use:", exact=False).wait_for()
    page.get_by_text("Review a regression report", exact=True).click()
    page.locator("#regression-file").set_input_files(str(root / "regression.json"))
    page.get_by_text("1 / 1 cases passed", exact=False).wait_for()
    page.screenshot(path=str(root / "desktop.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=str(root / "mobile.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
        "Page horizontal overflow"
    )
    assert not errors, errors
    print(
        "Desktop/mobile; live pause/resume; exact approval; report import; "
        "no JS errors or horizontal overflow: passed"
    )
    browser.close()

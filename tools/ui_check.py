#!/usr/bin/env python3
"""Open the web UI headless, run a few named steps, save one screenshot and print page errors.

    python3 tools/ui_check.py --port 8841 --out out/check.png routes
    python3 tools/ui_check.py --port 8841 --out out/map.png map generate-map paint-town paint-route
    python3 tools/ui_check.py --port 8841 --out out/route.png map generate-map open-first-route

Exit code 1 when the page logged errors. Start a server first:
    PORT=8841 IMPROVE_WORKERS=0 python3 webui/server.py
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from playwright.async_api import async_playwright

PAGE_SETTLE_IN_MILLISECONDS = 5000
STEP_SETTLE_IN_MILLISECONDS = 2500


async def step_routes(page):
    await page.get_by_role("tab", name="Routes").first.click()


async def step_map(page):
    await page.get_by_role("tab", name="Map").first.click()


async def step_geography(page):
    await page.get_by_role("tab", name="Geography").first.click()


async def step_generate(page):
    await page.get_by_role("button", name="Generate").first.click()


async def step_generate_map(page):
    await page.get_by_role("button", name="Generate map").click()


async def drag_across(page, start, end):
    await page.mouse.move(*start)
    await page.mouse.down()
    await page.mouse.move(*end, steps=8)
    await page.mouse.up()


async def step_paint_town(page):
    await page.keyboard.press("t")
    await drag_across(page, (700, 450), (740, 450))


async def step_paint_route(page):
    await page.keyboard.press("d")
    await drag_across(page, (740, 450), (740, 600))


async def step_open_first_route(page):
    await page.get_by_text("Route 1", exact=True).first.click()
    await page.get_by_role("button", name="Open in Routes").click()


async def step_playtest(page):
    await page.evaluate("Playtest.open()")


STEPS = {
    "routes": step_routes,
    "map": step_map,
    "geography": step_geography,
    "generate": step_generate,
    "generate-map": step_generate_map,
    "paint-town": step_paint_town,
    "paint-route": step_paint_route,
    "open-first-route": step_open_first_route,
    "playtest": step_playtest,
}


async def run_check(port: int, screenshot_path: str, step_names: list) -> list:
    page_errors = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1500, "height": 900})
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on("console", lambda message: message.type == "error" and page_errors.append(message.text))
        await page.goto(f"http://127.0.0.1:{port}/")
        await page.wait_for_timeout(PAGE_SETTLE_IN_MILLISECONDS)
        for step_name in step_names:
            await STEPS[step_name](page)
            await page.wait_for_timeout(STEP_SETTLE_IN_MILLISECONDS)
        await page.screenshot(path=screenshot_path)
        await browser.close()
    return page_errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8841)
    parser.add_argument("--out", default="out/ui-check.png")
    parser.add_argument("steps", nargs="*", choices=sorted(STEPS))
    arguments = parser.parse_args()
    page_errors = asyncio.run(run_check(arguments.port, arguments.out, arguments.steps))
    print(f"screenshot: {arguments.out}")
    print(f"page errors: {len(page_errors)}")
    for page_error in sorted(set(page_errors))[:10]:
        print("  " + page_error)
    return 1 if page_errors else 0


if __name__ == "__main__":
    sys.exit(main())

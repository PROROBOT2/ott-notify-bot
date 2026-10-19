"""JioHotstar probe: kya asli browser (Chromium) se GitHub pe episodes ka data khulta hai?

Ye bot nahi hai, sirf test hai. Kuch Telegram pe nahi bhejta, sirf log me result print karta hai.
"""
import asyncio
import json
import re

from playwright.async_api import async_playwright

URL = "https://www.hotstar.com/in/shows/yeh-rishta/586"


def summarize(body):
    """start-response se Episodes tray ka summary nikalta hai."""
    d = json.loads(body)
    widgets = d["success"]["page"]["spaces"]["tray"]["widget_wrappers"]
    for x in widgets:
        w = x.get("widget", x)
        dt = w.get("data", {})
        if isinstance(dt, dict) and dt.get("title") == "Episodes":
            tabs = []
            for t in dt.get("category_picker", {}).get("data", {}).get("tabs", []):
                td = t["tab"]["data"]
                tabs.append(f"{td.get('title')} ({td.get('sub_title')})")
            items = dt.get("tray_items", {}).get("data", {}).get("items", [])
            first = []
            for it in items[:3]:
                pc = it.get("playable_content", {}).get("data", {})
                tags = [t.get("value") for t in pc.get("tags", [])]
                first.append(f"{pc.get('title')} | {tags}")
            return tabs, len(items), first
    return None, 0, []


async def main():
    seen = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(locale="en-IN", viewport={"width": 1366, "height": 900})
        page = await ctx.new_page()

        async def on_response(resp):
            u = resp.url
            if "/api/internal/bff/v2/" in u or "/api/internal/fingerprint" in u:
                try:
                    body = await resp.text()
                except Exception:
                    body = ""
                print("RESP", resp.status, u.split("?")[0], "bytes:", len(body))
                if u.split("?")[0].endswith("/bff/v2/start") and resp.status == 200 and body:
                    seen["start"] = body

        page.on("response", on_response)
        try:
            r = await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            print("PAGE status:", r.status if r else None)
        except Exception as e:
            print("goto error:", e)
        await page.wait_for_timeout(20000)
        print("FINAL URL:", page.url)
        print("TITLE:", await page.title())
        text = ""
        try:
            text = (await page.inner_text("body"))[:400].replace("\n", " | ")
        except Exception:
            pass
        print("BODY START:", text)
        await browser.close()

    if "start" not in seen:
        print("RESULT: episodes ka data NAHI mila (page ya API block/khali)")
        return
    try:
        tabs, n, first = summarize(seen["start"])
    except Exception as e:
        print("RESULT: data mila par parse nahi hua:", e)
        return
    if tabs is None:
        print("RESULT: page khula par Episodes tray nahi mila")
        return
    print("RESULT: OK, episodes ka data mil gaya")
    print("  Season tabs:", tabs)
    print("  First page items:", n)
    for line in first:
        print("   -", line)


if __name__ == "__main__":
    asyncio.run(main())

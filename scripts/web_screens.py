"""Log in to a running VV88 web UI and screenshot pages (desktop + phone width).

    python scripts/web_screens.py --base http://127.0.0.1:8091 --password demo-password --out /tmp/shots / /jobs
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8091")
    ap.add_argument("--password", default="demo-password")
    ap.add_argument("--out", default="/tmp/vv88shots")
    ap.add_argument("--chrome", default="/opt/pw-browsers/chromium")
    ap.add_argument("paths", nargs="+")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chrome)
        for label, viewport in (("desktop", {"width": 1440, "height": 900}), ("phone", {"width": 390, "height": 844})):
            page = browser.new_page(viewport=viewport)
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.goto(f"{args.base}/login")
            page.fill("input[name=password]", args.password)
            page.click("button[type=submit], button.primary")
            page.wait_for_load_state("networkidle")
            for path in args.paths:
                resp = page.goto(f"{args.base}{path}")
                page.wait_for_load_state("networkidle")
                name = (path.strip("/").replace("/", "_") or "dashboard") + f"_{label}.png"
                page.screenshot(path=str(out / name), full_page=True)
                print(f"{resp.status if resp else '?'} {path} -> {out / name}")
            if errors:
                print("console errors:", errors[:10])
            page.close()
        browser.close()


if __name__ == "__main__":
    main()

"""`pumpscan` / `python -m pumpscan`: start the dashboard."""

from __future__ import annotations

import argparse
import logging
import threading
import webbrowser

from .config import Settings


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="pumpscan", description="25-agent pump.fun scanner with a Start/Stop dashboard.")
    p.add_argument("--host", help="bind address (default 127.0.0.1; use 0.0.0.0 to open it to your LAN)")
    p.add_argument("--port", type=int)
    p.add_argument("--demo", action="store_true", help="simulated market instead of live pump.fun data")
    p.add_argument("--demo-speed", type=float, help="demo clock speed-up (default 10x)")
    p.add_argument("--min-agree", type=int, help="BUY votes out of 25 needed for a signal (default 20; 25 = unanimous)")
    p.add_argument("--window", type=float, help="minutes a signal stays actionable (default 10)")
    p.add_argument("--no-browser", action="store_true", help="do not open the dashboard in a browser")
    a = p.parse_args(argv)

    s = Settings.from_env()
    s.host = a.host or s.host
    s.port = a.port or s.port
    s.demo = a.demo or s.demo
    s.demo_speed = a.demo_speed or s.demo_speed
    if a.min_agree:
        s.rules.min_buy_votes = max(1, min(25, a.min_agree))
    if a.window:
        s.rules.window_minutes = a.window

    if not s.is_local_only() and not s.password:
        raise SystemExit(
            "Refusing to serve on " + s.host + " without a password: anyone who finds the URL could stop the scan.\n"
            "Set PUMPSCAN_PASSWORD (or use the default --host 127.0.0.1)."
        )

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request would flood hosted logs
    import uvicorn

    from .server import create_app

    url = f"http://{'127.0.0.1' if s.host in ('0.0.0.0', '::') else s.host}:{s.port}/"
    print(f"pumpscan dashboard: {url}" + ("  [DEMO: simulated market]" if s.demo else ""))
    if not a.no_browser and s.is_local_only():
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(s), host=s.host, port=s.port, log_level="warning")


if __name__ == "__main__":
    main()

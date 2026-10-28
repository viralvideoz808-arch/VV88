"""Command line: `carlos run` starts the website, the console and the agent scheduler together."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from carlos.config import ensure_secrets, load_settings
from carlos.db import Database
from carlos.seed import seed_sample_inventory


def boot(args) -> tuple:
    settings = load_settings(args.env)
    created = ensure_secrets(settings, args.env)
    db = Database(settings.db_path, url=settings.database_url)
    if not getattr(args, "no_sample", False):
        n = seed_sample_inventory(db)
        if n:
            print(f"Added {n} sample cars so the site isn't empty — replace them in the console (Inventory).")
    return settings, db, created


def banner(settings, created: bool, site_port: int | None, ops_port: int | None) -> None:
    print("\n  CAR-LOS Rentals is running")
    if site_port:
        print(f"  • Website:  http://localhost:{site_port}   (public URL: {settings.site_url})")
    if ops_port:
        print(f"  • Console:  http://localhost:{ops_port}")
        if created:
            print(f"    First-run console password: {settings.ops_password}  (saved in .env as CARLOS_OPS_PASSWORD)")
        else:
            print("    Console password: CARLOS_OPS_PASSWORD in .env")
    mode = "DEMO (no social accounts connected — sends go to a sandbox)" if settings.demo_mode else "LIVE"
    print(f"  • Mode:     {mode}")
    print(f"  • Claude:   {'on (' + settings.model + ')' if settings.llm_enabled else 'off — agents use built-in rules'}\n")


async def serve(apps: list[tuple], log_level: str) -> None:
    import uvicorn
    servers = [uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level=log_level, proxy_headers=True,
                                             forwarded_allow_ips="127.0.0.1"))
               for app, host, port in apps]
    await asyncio.gather(*(s.serve() for s in servers))


def cmd_run(args) -> None:
    from carlos.web.ops import create_ops_app
    from carlos.web.site import create_site_app
    settings, db, created = boot(args)
    apps = []
    if args.what in ("all", "site"):
        apps.append((create_site_app(db, settings), args.host, args.site_port))
    if args.what in ("all", "ops"):
        apps.append((create_ops_app(db, settings, run_scheduler=not args.no_agents), args.host, args.ops_port))
    banner(settings, created, args.site_port if args.what in ("all", "site") else None,
           args.ops_port if args.what in ("all", "ops") else None)
    try:
        asyncio.run(serve(apps, args.log_level))
    except KeyboardInterrupt:
        pass


def cmd_agents(args) -> None:
    from carlos.agents import AGENTS, PIPELINE, run_agent
    settings, db, _ = boot(args)
    names = PIPELINE if args.name == "all" else [args.name]
    for name in names:
        if name not in AGENTS:
            sys.exit(f"unknown agent {name}; choose from: {', '.join(PIPELINE)}")
        r = run_agent(db, settings, name, trigger="cli")
        print(f"{AGENTS[name].title:28} {r['status']:5}  {r['summary']}")


def cmd_seed(args) -> None:
    settings = load_settings(args.env)
    db = Database(settings.db_path, url=settings.database_url)
    print(f"{seed_sample_inventory(db, force=args.force)} sample cars added")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="carlos", description="CAR-LOS Rentals: website, console and agents")
    p.add_argument("--env", default=".env", help="path to the .env file (default: ./.env)")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="start the website + console + agents (default), or one of them")
    r.add_argument("what", nargs="?", choices=["all", "site", "ops"], default="all")
    r.add_argument("--host", default="0.0.0.0")
    r.add_argument("--site-port", type=int, default=8080)
    r.add_argument("--ops-port", type=int, default=8081)
    r.add_argument("--no-agents", action="store_true", help="console without the background scheduler")
    r.add_argument("--no-sample", action="store_true", help="don't add sample cars to an empty database")
    r.add_argument("--log-level", default="info")
    r.set_defaults(func=cmd_run)

    a = sub.add_parser("agents", help="run agents once from the command line")
    a.add_argument("name", nargs="?", default="all")
    a.add_argument("--no-sample", action="store_true")
    a.set_defaults(func=cmd_agents)

    s = sub.add_parser("seed", help="add the sample inventory")
    s.add_argument("--force", action="store_true", help="add even if cars already exist")
    s.set_defaults(func=cmd_seed)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args.func(args)


if __name__ == "__main__":
    main()

"""Entry point for Vercel: both the website and the console, chosen by hostname (see carlos/hosted.py)."""

from carlos.hosted import build_app

app = build_app()

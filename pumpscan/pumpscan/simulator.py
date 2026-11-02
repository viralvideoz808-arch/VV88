"""A simulated pump.fun market for `--demo`: try the dashboard without touching real data.

Every coin gets a hidden fate (rug, dud, runner, moon) that shapes its chart, order flow and
on-chain report, so the agents have something realistic to disagree about. Time runs `speed`
times faster than the wall clock so a 10-minute signal window resolves in about a minute.
"""

from __future__ import annotations

import asyncio
import math
import random
import string
import time
from dataclasses import dataclass

from .models import RugReport, Snapshot, Token

WORDS = ["kimchi", "pepe", "doge", "cat", "frog", "wif", "bonk", "moon", "chad", "goblin", "trump", "elon",
         "ai", "agent", "grok", "pengu", "shiba", "hamster", "monkey", "banana", "sushi", "ramen", "taco",
         "wizard", "ninja", "rocket", "based", "giga", "turbo", "baby", "king", "lord", "space", "pixel"]
METAS = ["kimchi", "ai", "hamster", "wizard", "pengu"]


@dataclass
class _Hidden:
    fate: str
    drift: float
    vol: float
    mcap: float
    peak_at: float
    rug_at: float


class Simulator:
    def __init__(self, engine, speed: float = 10.0, seed: int | None = None):
        self.engine = engine
        self.speed = speed
        self.rng = random.Random(seed)
        self._t0_wall = time.time()
        self._t0_sim = time.time()
        self.hidden: dict[str, _Hidden] = {}
        self.meta = self.rng.choice(METAS)
        self.sol_usd = 180.0

    def clock(self) -> float:
        return self._t0_sim + (time.time() - self._t0_wall) * self.speed

    async def run(self) -> None:
        self.engine.ctx.sol_usd = self.sol_usd
        self.engine.status["simulator"] = "running (SIMULATED market, not real coins)"
        for _ in range(60):  # a market already in progress
            self.launch(age_min=self.rng.uniform(2, 50))
        while True:
            for _ in range(self.rng.randint(1, 3)):
                self.launch()
            self.tick()
            if self.rng.random() < 0.01:
                self.meta = self.rng.choice(METAS)
            await asyncio.sleep(1.0)

    # ------------------------------------------------------------ coins

    def launch(self, age_min: float = 0.0) -> Token:
        rng = self.rng
        now = self.clock()
        fate = rng.choices(["rug", "dud", "runner", "moon"], [0.3, 0.45, 0.2, 0.05])[0]
        w1 = self.meta if rng.random() < 0.3 else rng.choice(WORDS)
        w2 = rng.choice(WORDS)
        name = f"{w1.title()} {w2.title()}" if rng.random() < 0.5 else w1.title()
        symbol = (w1[:5] if rng.random() < 0.7 else w1[:3] + w2[:2]).upper()
        mint = "".join(rng.choices(string.ascii_letters + string.digits, k=40)) + "pump"
        tok = Token(
            mint=mint, name=name, symbol=symbol, created_at=now - age_min * 60, first_seen=now,
            creator=rng.choice(self._creators()), dev_buy_sol=round(rng.uniform(0, 3), 2),
        )
        good = fate in ("runner", "moon")
        tok.description = (f"The {w1} coin the internet has been waiting for. Community first, no presale."
                           if good or rng.random() < 0.4 else "")
        tok.twitter = "https://x.com/" + w1 if good or rng.random() < 0.3 else ""
        tok.telegram = "https://t.me/" + w1 if good and rng.random() < 0.7 else ""
        tok.website = f"https://{w1}{w2}.fun" if good and rng.random() < 0.5 else ""
        tok.image = "" if rng.random() < 0.1 else f"https://placehold.co/64x64/101820/3ddc84?text={symbol[:3]}"
        tok.metadata_loaded = True
        if good and rng.random() < 0.3:
            tok.boosts = rng.randint(1, 5)
            tok.has_profile = True
        drift = {"rug": 0.02, "dud": -0.01, "runner": 0.05, "moon": 0.09}[fate]
        self.hidden[mint] = _Hidden(
            fate=fate, drift=drift, vol=rng.uniform(0.04, 0.12),
            mcap=rng.uniform(4500, 7000) * math.exp(drift * age_min * 0.5),
            peak_at=tok.created_at + rng.uniform(8, 45) * 60,
            rug_at=tok.created_at + rng.uniform(4, 20) * 60,
        )
        self.engine.add_token(tok)
        self.engine.launches_seen += 1
        return tok

    def _creators(self) -> list[str]:
        if not hasattr(self, "_creator_pool"):
            self._creator_pool = ["".join(self.rng.choices(string.ascii_letters, k=44)) for _ in range(400)]
        return self._creator_pool

    def tick(self) -> None:
        now = self.clock()
        rng = self.rng
        dt_min = self.speed / 60.0
        for mint, h in list(self.hidden.items()):
            tok = self.engine.tokens.get(mint)
            if tok is None:
                del self.hidden[mint]
                continue
            drift = h.drift
            if now > h.peak_at:
                drift = -abs(h.drift) * 0.8
            if self.meta.lower() in tok.name.lower():
                drift += 0.02
            shock = rng.gauss(0, h.vol) * math.sqrt(dt_min)
            h.mcap *= math.exp(drift * dt_min + shock)
            if h.fate == "rug" and now > h.rug_at:
                h.mcap *= 0.25
                h.fate = "rugged"
            h.mcap = max(2500.0, h.mcap)
            prev = tok.snapshot_ago(300, now)
            change = (h.mcap / prev.mcap_usd - 1) * 100 if prev else 0.0
            activity = max(0.0, change) * 2 + h.mcap / 2000
            buys = int(max(0, rng.gauss(activity * (1.3 if change > 0 else 0.7), 5)))
            sells = int(max(0, rng.gauss(activity * (0.7 if change > 0 else 1.3), 5)))
            vol5 = (buys + sells) * rng.uniform(40, 220)
            last_h1 = tok.snap.vol_h1 if tok.snap else 0.0
            tok.add_snapshot(Snapshot(
                t=now, price_usd=h.mcap / 1e9, mcap_usd=h.mcap, buys_m5=buys, sells_m5=sells,
                buys_h1=buys * 6, sells_h1=sells * 6, vol_m5=vol5, vol_h1=max(vol5, last_h1 * 0.95 + vol5 * 0.2),
                change_m5=change, change_h1=change * 2,
                liquidity_usd=None,
            ))
            if tok.rug is None and tok.snapshots and len(tok.snapshots) > 3 and rng.random() < 0.2:
                tok.rug = self._report(tok, h, now)
            if tok.rug and tok.rug.total_holders is not None:
                grow = int(max(0, buys - sells) * rng.uniform(0.2, 0.6))
                tok.rug.total_holders += grow
                tok.holder_history.append((now, tok.rug.total_holders))

    def _report(self, tok: Token, h: _Hidden, now: float) -> RugReport:
        rng = self.rng
        bad = h.fate in ("rug", "rugged")
        return RugReport(
            fetched_at=now,
            score_normalised=rng.uniform(20, 80) if bad else rng.uniform(1, 10),
            mint_authority=bad and rng.random() < 0.05,
            total_holders=int(h.mcap / rng.uniform(80, 200)),
            top10_pct=rng.uniform(30, 60) if bad and rng.random() < 0.6 else rng.uniform(10, 26),
            max_holder_pct=rng.uniform(8, 20) if bad and rng.random() < 0.5 else rng.uniform(1.5, 5),
            insider_pct=rng.uniform(5, 20) if bad and rng.random() < 0.5 else 0.0,
            insiders_detected=rng.randint(3, 9) if bad and rng.random() < 0.5 else 0,
            creator_pct=rng.uniform(6, 15) if bad and rng.random() < 0.4 else rng.uniform(0, 4),
            creator_token_count=rng.randint(3, 12) if bad and rng.random() < 0.3 else 1,
            danger_risks=["Single holder ownership"] if bad and rng.random() < 0.3 else [],
            warn_risks=["Low amount of LP Providers"] if rng.random() < 0.3 else [],
        )

"""The engine behind the Start / Stop button.

Start launches concurrent loops:
  stream    PumpPortal websocket: every new pump.fun launch, migrations, (optional) live trades
  seed      DexScreener profiles/boosts and pump.fun's API: coins already running
  dex       DexScreener market data for every watched coin, 30 coins per request
  meta      IPFS metadata (description, socials) for new coins
  deep      Rugcheck on-chain report for the coins the council likes
  council   all 25 agents vote on every coin every pass; consensus -> BUY signal
  prune     drop dead coins so the scan stays fast
Stop cancels them all.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from .agents import AGENTS, AGENTS_BY_ID, AVOID
from .config import Settings
from .council import build_signal, rank_key, run_council, score_outcome, votes_json
from .models import MarketContext, Token, curve_progress_from_mcap_sol
from .sources import (
    Http,
    apply_rug_report,
    parse_create_event,
    parse_dex_pair,
    parse_metadata,
    parse_pumpfun_coin,
    parse_rug_report,
    parse_trade_event,
    pick_pair,
    pumpportal_stream,
)

log = logging.getLogger("pumpscan.engine")


class Engine:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.rules = settings.rules
        self.running = False
        self.started_at: float | None = None
        self.tokens: dict[str, Token] = {}
        self.ctx = MarketContext()
        self.signals: list[dict] = []
        self.status: dict[str, str] = {}
        self.analyzed: set[str] = set()
        self.launches_seen = 0
        self.passes = 0
        self.last_pass_ms = 0.0
        self.agent_tally: dict[str, Counter] = {a.id: Counter() for a in AGENTS}
        self._tasks: list[asyncio.Task] = []
        self._http: Http | None = None
        self._meta_q: asyncio.Queue[str] = asyncio.Queue()
        self._subs_q: asyncio.Queue[dict] = asyncio.Queue()
        self._trade_subs: set[str] = set()
        self._sim = None
        self._load_signals()

    # ------------------------------------------------------------ clock

    def clock(self) -> float:
        return self._sim.clock() if self._sim else time.time()

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.started_at = self.clock()
        self.tokens.clear()
        self.ctx = MarketContext()
        self.analyzed.clear()
        self.launches_seen = 0
        self.passes = 0
        self._meta_q = asyncio.Queue()
        self._subs_q = asyncio.Queue()
        self._trade_subs.clear()
        loops = [self._council_loop(), self._prune_loop()]
        if self.settings.demo:
            from .simulator import Simulator

            self._sim = Simulator(self, speed=self.settings.demo_speed)
            self.started_at = self.clock()
            loops.append(self._sim.run())
            self.status = {"simulator": "running (SIMULATED market, not real coins)"}
        else:
            self._sim = None
            self._http = Http()
            self.status = {"pumpportal": "connecting"}
            loops += [
                pumpportal_stream(self._on_stream, self._subs_q, self.settings.pumpportal_api_key, self.status),
                self._seed_loop(),
                self._dex_loop(),
                self._deep_loop(),
            ]
            loops += [self._meta_worker() for _ in range(8)]
            if self.settings.pumpportal_api_key:
                loops.append(self._trade_sub_loop())
        self._tasks = [asyncio.create_task(c) for c in loops]
        for t in self._tasks:
            t.add_done_callback(self._task_done)

    async def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        if self._http:
            await self._http.close()
            self._http = None
        self.status = {k: "stopped" for k in self.status}
        self._save_signals()

    def _task_done(self, t: asyncio.Task) -> None:
        if not t.cancelled() and t.exception():
            log.error("loop crashed", exc_info=t.exception())
            self.status["engine"] = f"a loop crashed: {t.exception()!r}"

    # ------------------------------------------------------------ token intake

    def add_token(self, tok: Token) -> Token:
        existing = self.tokens.get(tok.mint)
        if existing:
            return existing
        if len(self.tokens) >= self.settings.watch_cap:
            self._evict(1)
        self.tokens[tok.mint] = tok
        self.ctx.tokens = self.tokens
        self.ctx.now = self.clock()
        self.ctx.note_launch(tok)
        return tok

    async def _on_stream(self, msg: dict) -> None:
        now = self.clock()
        tx = msg.get("txType")
        if tx == "create":
            tok = parse_create_event(msg, now)
            if tok:
                self.launches_seen += 1
                self.add_token(tok)
                if tok.uri:
                    self._meta_q.put_nowait(tok.mint)
        elif tx == "migrate":
            mint = msg.get("mint")
            if mint:
                tok = self.add_token(Token(mint=mint, first_seen=now))
                tok.migrated = True
        else:
            parsed = parse_trade_event(msg, now)
            if parsed and parsed[0] in self.tokens:
                self.tokens[parsed[0]].trades.append(parsed[1])

    # ------------------------------------------------------------ live loops

    async def _seed_loop(self) -> None:
        while True:
            now = self.clock()
            for item in await self._http.dex_listings():
                tok = self.add_token(Token(mint=item["mint"], first_seen=now))
                tok.has_profile = True
                tok.cto = tok.cto or item["cto"]
                tok.boosts = max(tok.boosts, item["boosts"])
                if item["description"] and not tok.metadata_loaded:
                    tok.description = item["description"][:500]
                    tok.metadata_loaded = True
                for link in item["links"]:
                    url = link.get("url", "")
                    kind = (link.get("type") or link.get("label") or "").lower()
                    if kind == "twitter":
                        tok.twitter = tok.twitter or url
                    elif kind == "telegram":
                        tok.telegram = tok.telegram or url
                    elif kind == "website":
                        tok.website = tok.website or url
            for c in await self._http.pumpfun_active():
                parsed = parse_pumpfun_coin(c)
                if parsed:
                    self.add_token(parsed)
            self.status["seed"] = f"ok ({len(self.tokens)} watched)"
            await asyncio.sleep(60)

    async def _dex_loop(self) -> None:
        while True:
            t0 = time.monotonic()
            now = self.clock()

            def due(tok: Token) -> float:
                hot = 3.0 if (tok.buy_votes >= 14 and not tok.vetoed) or self._is_signal_live(tok.mint) else 1.0
                return (now - tok.last_dex) * hot

            mints = [t.mint for t in sorted(self.tokens.values(), key=due, reverse=True)
                     if now - t.last_dex >= 6][: 30 * 30]
            batches = [mints[i:i + 30] for i in range(0, len(mints), 30)]
            results = await asyncio.gather(*(self._http.dex_tokens(b) for b in batches))
            now = self.clock()
            for batch, pairs in zip(batches, results):
                by_mint: dict[str, list[dict]] = {}
                for p in pairs:
                    by_mint.setdefault((p.get("baseToken") or {}).get("address", ""), []).append(p)
                for mint in batch:
                    tok = self.tokens.get(mint)
                    if not tok:
                        continue
                    tok.last_dex = now
                    pair = pick_pair(by_mint.get(mint, []))
                    if pair is None:
                        tok.dex_misses += 1
                        continue
                    sol = parse_dex_pair(tok, pair, now)
                    if sol and 10 < sol < 10000:
                        self.ctx.sol_usd = sol
            err = self._http.errors.get("dexscreener")
            self.status["dexscreener"] = f"error: {err}" if err else f"ok ({len(mints)} coins/pass)"
            await asyncio.sleep(max(1.0, 5 - (time.monotonic() - t0)))

    async def _meta_worker(self) -> None:
        while True:
            mint = await self._meta_q.get()
            tok = self.tokens.get(mint)
            if tok and not tok.metadata_loaded:
                meta = await self._http.metadata(tok.uri)
                if meta:
                    parse_metadata(tok, meta)
                else:
                    tok.metadata_loaded = True  # unreachable metadata counts as empty

    async def _deep_loop(self) -> None:
        """Rugcheck is rate limited, so it goes to the coins closest to a signal first."""
        while True:
            now = self.clock()
            cands = [t for t in self.tokens.values()
                     if t.snap and not t.vetoed and t.buy_votes >= 11 and now - t.rug_attempt > 60
                     and (t.rug is None or now - t.rug.fetched_at > 180)]
            cands.sort(key=lambda t: (t.rug is None, t.buy_votes, t.conviction), reverse=True)
            if not cands:
                await asyncio.sleep(1)
                continue
            tok = cands[0]
            tok.rug_attempt = now
            data = await self._http.rug_report(tok.mint)
            now = self.clock()
            if data:
                if apply_rug_report(tok, parse_rug_report(data, tok, now)):
                    self._meta_q.put_nowait(tok.mint)
            else:
                await asyncio.sleep(2)
            err = self._http.errors.get("rugcheck")
            self.status["rugcheck"] = f"error: {err}" if err else "ok"

    async def _trade_sub_loop(self) -> None:
        """With a PumpPortal key: stream every trade of the 40 strongest candidates (metered by PumpPortal)."""
        while True:
            top = {t.mint for t in sorted(self.tokens.values(), key=rank_key, reverse=True)[:40]
                   if t.snap and not t.vetoed}
            add, drop = top - self._trade_subs, self._trade_subs - top
            if add:
                self._subs_q.put_nowait({"method": "subscribeTokenTrade", "keys": sorted(add)})
            if drop:
                self._subs_q.put_nowait({"method": "unsubscribeTokenTrade", "keys": sorted(drop)})
            self._trade_subs = top
            await asyncio.sleep(15)

    # ------------------------------------------------------------ council

    async def _council_loop(self) -> None:
        while True:
            self.council_pass()
            await asyncio.sleep(2)

    def council_pass(self) -> list[dict]:
        """Every agent votes on every coin with market data. Returns new signals."""
        t0 = time.perf_counter()
        now = self.clock()
        self.ctx.now = now
        self.ctx.tokens = self.tokens
        self.ctx.symbol_index.clear()
        for tok in self.tokens.values():
            if tok.symbol:
                self.ctx.symbol_index[tok.symbol.lower()].add(tok.mint)
        self.ctx.refresh_hot_words()
        tally = {a.id: Counter() for a in AGENTS}
        new = []
        for tok in list(self.tokens.values()):
            if not tok.snapshots:
                continue
            verdict = run_council(tok, self.ctx, self.rules)
            self.analyzed.add(tok.mint)
            for v in verdict.votes:
                tally[v.agent][v.stance] += 1
            if verdict.is_signal and self._cooldown_ok(tok, now):
                sig = build_signal(tok, verdict, self.rules, now)
                tok.signaled_at = now
                self.signals.append(sig)
                new.append(sig)
        self._update_signals(now)
        self.agent_tally = tally
        self.passes += 1
        self.last_pass_ms = (time.perf_counter() - t0) * 1000
        if new:
            self._save_signals()
        return new

    def _cooldown_ok(self, tok: Token, now: float) -> bool:
        return tok.signaled_at is None or now - tok.signaled_at >= self.rules.cooldown_minutes * 60

    def _is_signal_live(self, mint: str) -> bool:
        return any(s["mint"] == mint and s.get("state", "active") == "active" for s in self.signals[-20:])

    def _update_signals(self, now: float) -> None:
        changed = False
        for sig in self.signals:
            if sig.get("outcome"):
                continue
            tok = self.tokens.get(sig["mint"])
            state = sig.get("state", "active")
            if now >= sig["expires"]:
                if state == "active":
                    sig["state"] = "expired"
                out = score_outcome(sig, tok, now)
                if out is None and not self.running:
                    out = {"status": "no_data"}
                if out:
                    sig["outcome"] = out
                    changed = True
                continue
            if tok is None or state != "active":
                continue
            s = tok.snap
            if s and s.mcap_usd > sig["max_entry_mcap_usd"]:
                sig["state"] = "void"
                sig["state_reason"] = "price ran past the max entry; do not chase"
                changed = True
            elif tok.vetoed:
                who = [AGENTS_BY_ID[v.agent].name for v in tok.last_votes
                       if v.stance == AVOID and AGENTS_BY_ID[v.agent].veto]
                sig["state"] = "cancelled"
                sig["state_reason"] = f"{', '.join(who)} now votes AVOID"
                changed = True
        if changed:
            self._save_signals()

    # ------------------------------------------------------------ housekeeping

    async def _prune_loop(self) -> None:
        while True:
            await asyncio.sleep(20)
            self.prune()

    def prune(self) -> None:
        now = self.clock()
        live = {s["mint"] for s in self.signals if not s.get("outcome")}
        dead = []
        for m, t in self.tokens.items():
            if m in live:
                continue
            age = t.age_min(now)
            s = t.snap
            if s is None and age > 25 and t.dex_misses >= 3:
                dead.append(m)
            elif s and age > 30 and s.buys_m5 + s.sells_m5 < 5:
                dead.append(m)
            elif s and age > 20 and s.mcap_usd < 4500 and not t.migrated:
                dead.append(m)
        for m in dead:
            self.tokens.pop(m, None)

    def _evict(self, n: int) -> None:
        live = {s["mint"] for s in self.signals if not s.get("outcome")}
        victims = sorted((t for t in self.tokens.values() if t.mint not in live), key=rank_key)[:n]
        for t in victims:
            self.tokens.pop(t.mint, None)

    # ------------------------------------------------------------ persistence

    def _signals_path(self) -> Path:
        return Path(self.settings.data_dir).expanduser() / ("signals-demo.json" if self.settings.demo else "signals.json")

    def _load_signals(self) -> None:
        try:
            self.signals = json.loads(self._signals_path().read_text())[-500:]
        except (OSError, ValueError):
            self.signals = []

    def _save_signals(self) -> None:
        try:
            p = self._signals_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.signals[-500:], indent=1))
            tmp.replace(p)
        except OSError as exc:
            log.warning("could not save signals: %s", exc)

    # ------------------------------------------------------------ UI state

    def track_record(self) -> dict:
        scored = [s for s in self.signals if s.get("outcome") and s["outcome"].get("status") != "no_data"]
        wins = sum(1 for s in scored if s["outcome"]["status"] == "win")
        stopped = sum(1 for s in scored if s["outcome"]["status"] == "stopped")
        ends = [s["outcome"]["end_pct"] for s in scored]
        peaks = [s["outcome"]["peak_pct"] for s in scored]
        return {
            "signals": len(self.signals),
            "scored": len(scored),
            "wins": wins,
            "stopped": stopped,
            "win_rate": round(100 * wins / len(scored), 1) if scored else None,
            "avg_end_pct": round(sum(ends) / len(ends), 1) if ends else None,
            "avg_peak_pct": round(sum(peaks) / len(peaks), 1) if peaks else None,
        }

    def _coin_row(self, t: Token, now: float) -> dict:
        s = t.snap
        progress = None
        if s and not t.migrated and self.ctx.sol_usd > 0:
            progress = round(100 * curve_progress_from_mcap_sol(s.mcap_usd / self.ctx.sol_usd))
        vetoes = [v.reason for v in t.last_votes if v.stance == AVOID and AGENTS_BY_ID[v.agent].veto]
        return {
            "mint": t.mint, "name": t.name, "symbol": t.symbol, "image": t.image,
            "mcap": s.mcap_usd if s else None, "change_m5": s.change_m5 if s else None,
            "buys_m5": s.buys_m5 if s else None, "sells_m5": s.sells_m5 if s else None,
            "vol_m5": s.vol_m5 if s else None, "age_min": round(t.age_min(now), 1),
            "curve_pct": progress, "migrated": t.migrated,
            "buy_votes": t.buy_votes, "avoid_votes": t.avoid_votes, "vetoed": t.vetoed,
            "conviction": round(t.conviction, 1), "veto_reason": vetoes[0] if vetoes else "",
            "deep_checked": t.rug is not None,
            "votes": votes_json(t.last_votes),
        }

    def state(self) -> dict:
        now = self.clock()
        ranked = sorted((t for t in self.tokens.values() if t.last_votes), key=rank_key, reverse=True)
        active = None
        for sig in reversed(self.signals):
            if sig.get("state", "active") in ("active", "void", "cancelled") and now < sig["expires"]:
                active = dict(sig)
                tok = self.tokens.get(sig["mint"])
                if tok and tok.snap:
                    active["now_mcap_usd"] = tok.snap.mcap_usd
                    active["since_entry_pct"] = round((tok.snap.mcap_usd / sig["entry_mcap_usd"] - 1) * 100, 1)
                active["remaining_s"] = max(0, int(sig["expires"] - now))
                active.setdefault("state", "active")
                break
        return {
            "running": self.running,
            "mode": "demo" if self.settings.demo else "live",
            "now": now,
            "uptime_s": int(now - self.started_at) if self.running and self.started_at else 0,
            "stats": {
                "watched": len(self.tokens),
                "with_market_data": sum(1 for t in self.tokens.values() if t.snapshots),
                "deep_checked": sum(1 for t in self.tokens.values() if t.rug),
                "analyzed_total": len(self.analyzed),
                "launches_seen": self.launches_seen,
                "passes": self.passes,
                "last_pass_ms": round(self.last_pass_ms, 1),
                "sol_usd": round(self.ctx.sol_usd, 2),
                "hot_words": sorted(self.ctx.hot_words, key=self.ctx.hot_words.get, reverse=True)[:8],
            },
            "sources": dict(self.status),
            "rules": asdict(self.rules),
            "agents": [
                {"id": a.id, "name": a.name, "group": a.group, "description": a.description,
                 "veto": a.veto, "tally": dict(self.agent_tally.get(a.id, {}))}
                for a in AGENTS
            ],
            "candidates": [self._coin_row(t, now) for t in ranked[:25]],
            "active_signal": active,
            "signals": list(reversed(self.signals[-40:])),
            "track_record": self.track_record(),
        }

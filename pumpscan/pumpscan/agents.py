"""The 25 analysis agents.

Each agent looks at one aspect of a coin and casts a vote:
  BUY     - its evidence supports buying now (strength 0..1 = how strongly)
  HOLD    - neutral / not convinced yet
  AVOID   - its evidence argues against buying
  ABSTAIN - it does not have the data to judge yet

Agents in the "safety" group are veto agents: one AVOID from any of them blocks a signal,
whatever the other agents say.
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass
from typing import Callable

from .models import MarketContext, Token, curve_progress_from_mcap_sol

BUY, HOLD, AVOID, ABSTAIN = "BUY", "HOLD", "AVOID", "ABSTAIN"


@dataclass
class Vote:
    agent: str
    stance: str
    strength: float
    reason: str


@dataclass
class Agent:
    id: str
    name: str
    group: str
    description: str
    veto: bool
    fn: Callable[[Token, MarketContext], tuple[str, float, str]]

    def vote(self, tok: Token, ctx: MarketContext) -> Vote:
        try:
            stance, strength, reason = self.fn(tok, ctx)
        except Exception as exc:  # one broken input must not take the council down
            stance, strength, reason = ABSTAIN, 0.0, f"error: {exc}"
        return Vote(self.id, stance, round(max(0.0, min(1.0, strength)), 3), reason)


AGENTS: list[Agent] = []


def agent(id: str, name: str, group: str, description: str, veto: bool = False):
    def wrap(fn):
        AGENTS.append(Agent(id, name, group, description, veto, fn))
        return fn

    return wrap


def _pct(x: float) -> str:
    return f"{x:+.0f}%"


def _usd(x: float) -> str:
    if x >= 1_000_000:
        return f"${x / 1_000_000:.2f}M"
    if x >= 1_000:
        return f"${x / 1_000:.1f}k"
    return f"${x:.0f}"


def _ramp(x: float, lo: float, hi: float) -> float:
    """0 at lo, 1 at hi, clamped."""
    if hi == lo:
        return 1.0
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


# ---------------------------------------------------------------- safety (veto) agents


@agent("mint_auth", "Mint Authority Sentinel", "safety",
       "Mint and freeze authority must be revoked, or the dev can print tokens or freeze your wallet.", veto=True)
def _mint_auth(tok, ctx):
    r = tok.rug
    if r is None:
        return ABSTAIN, 0, "waiting for on-chain report"
    if r.mint_authority:
        return AVOID, 1, "mint authority NOT revoked: dev can print more supply"
    if r.freeze_authority:
        return AVOID, 1, "freeze authority NOT revoked: your tokens can be frozen"
    return BUY, 0.6, "mint & freeze authority revoked"


@agent("holders", "Holder Concentration Auditor", "safety",
       "Top-10 wallets (excluding the curve/pool) must not control the supply.", veto=True)
def _holders(tok, ctx):
    r = tok.rug
    if r is None or r.top10_pct is None:
        return ABSTAIN, 0, "waiting for holder list"
    if (r.max_holder_pct or 0) > 12:
        return AVOID, 1, f"one wallet holds {r.max_holder_pct:.1f}% of supply"
    if r.top10_pct > 40:
        return AVOID, 0.9, f"top 10 wallets hold {r.top10_pct:.0f}%: dump risk"
    if r.top10_pct > 28:
        return HOLD, 0.3, f"top 10 hold {r.top10_pct:.0f}% (borderline)"
    return BUY, 0.5 + 0.5 * _ramp(28 - r.top10_pct, 0, 18), f"well distributed: top 10 hold {r.top10_pct:.0f}%"


@agent("dev_wallet", "Dev Wallet Watcher", "safety",
       "How much the creator still holds and whether they are dumping.", veto=True)
def _dev_wallet(tok, ctx):
    dev_sells = [t for t in tok.trades if t.trader == tok.creator and not t.is_buy] if tok.creator else []
    r = tok.rug
    if dev_sells:
        recent = [t for t in dev_sells if ctx.now - t.t < 300]
        if recent and sum(t.sol for t in recent) > 1:
            return AVOID, 0.9, f"dev sold {sum(t.sol for t in recent):.1f} SOL in the last 5 min"
    if r is not None and r.creator_pct is not None:
        if r.creator_pct > 10:
            return AVOID, 0.9, f"dev still holds {r.creator_pct:.1f}%: can crash the chart"
        if r.creator_pct > 5:
            return HOLD, 0.3, f"dev holds {r.creator_pct:.1f}%"
        if r.creator_pct == 0 and dev_sells:
            return HOLD, 0.2, "dev fully exited: community has to carry it"
        return BUY, 0.6, f"dev holds only {r.creator_pct:.1f}%"
    if tok.dev_buy_sol > 0 and not dev_sells and r is None:
        if tok.dev_buy_sol > 5:
            return HOLD, 0.3, f"dev bought {tok.dev_buy_sol:.1f} SOL at launch (big bag)"
        return ABSTAIN, 0, "waiting for dev balance"
    return ABSTAIN, 0, "waiting for dev balance"


@agent("insiders", "Insider & Bundle Detector", "safety",
       "Bundled launches and linked insider wallets that sell into buyers.", veto=True)
def _insiders(tok, ctx):
    r = tok.rug
    if tok.trades and tok.created_at:
        early = {t.trader for t in tok.trades if t.is_buy and t.t - tok.created_at <= 2 and t.trader != tok.creator}
        if len(early) >= 4:
            return AVOID, 0.9, f"{len(early)} wallets bought within 2s of launch (bundled)"
    if r is None:
        return ABSTAIN, 0, "waiting for insider graph"
    if r.insider_pct > 8 or r.insiders_detected >= 5:
        return AVOID, 0.9, f"insider network detected ({r.insiders_detected} wallets, {r.insider_pct:.0f}%)"
    if r.insiders_detected > 0:
        return HOLD, 0.3, f"{r.insiders_detected} linked insider wallets"
    return BUY, 0.6, "no insider network or bundle found"


@agent("serial_dev", "Serial Launcher Profiler", "safety",
       "Creators that launch coin after coin are farming buyers.", veto=True)
def _serial_dev(tok, ctx):
    n = ctx.creator_counts.get(tok.creator, 0) if tok.creator else 0
    if tok.rug and tok.rug.creator_token_count is not None:
        n = max(n, tok.rug.creator_token_count)
    if not tok.creator and not (tok.rug and tok.rug.creator_token_count is not None):
        return ABSTAIN, 0, "creator unknown"
    if n >= 3:
        return AVOID, 1, f"creator launched {n} coins: serial rugger pattern"
    if n == 2:
        return HOLD, 0.3, "creator's 2nd coin"
    return BUY, 0.5, "no other launches seen from this creator"


@agent("rugcheck", "Rugcheck Risk Scorer", "safety",
       "Rugcheck.xyz risk flags.", veto=True)
def _rugcheck(tok, ctx):
    r = tok.rug
    if r is None:
        return ABSTAIN, 0, "waiting for Rugcheck"
    if r.rugged:
        return AVOID, 1, "Rugcheck marks it RUGGED"
    if r.danger_risks:
        return AVOID, 0.9, "danger: " + "; ".join(r.danger_risks[:2])
    if len(r.warn_risks) >= 3:
        return HOLD, 0.3, "warnings: " + "; ".join(r.warn_risks[:2])
    return BUY, 0.7 if not r.warn_risks else 0.5, "no danger flags" + (f" ({len(r.warn_risks)} warnings)" if r.warn_risks else "")


@agent("exit_liq", "Exit Liquidity Guard", "safety",
       "Can you actually get out? Sell flow, pool depth, distance from the peak.", veto=True)
def _exit_liq(tok, ctx):
    s = tok.snap
    if s is None:
        return ABSTAIN, 0, "no market data yet"
    if tok.migrated and s.liquidity_usd is not None and s.liquidity_usd < 8000:
        return AVOID, 0.9, f"pool liquidity only {_usd(s.liquidity_usd)}"
    if tok.ath_mcap > 0:
        dd = 1 - s.mcap_usd / tok.ath_mcap
        if dd > 0.55:
            return AVOID, 0.9, f"down {dd * 100:.0f}% from its peak: buyers already trapped"
    if s.sells_m5 == 0 and s.buys_m5 > 20:
        return HOLD, 0.2, "no sells seen yet: cannot confirm exits"
    return BUY, 0.5, "sells are clearing normally"


@agent("rug_pattern", "Rug Pattern Detector", "safety",
       "Sudden crashes and liquidity pulls in the live chart.", veto=True)
def _rug_pattern(tok, ctx):
    s = tok.snap
    if s is None:
        return ABSTAIN, 0, "no market data yet"
    if s.change_m5 < -30:
        return AVOID, 1, f"crashed {_pct(s.change_m5)} in 5 min"
    prev = tok.snapshot_ago(45, ctx.now)
    if prev and prev.mcap_usd > 0 and ctx.now - prev.t < 150:
        drop = 1 - s.mcap_usd / prev.mcap_usd
        if drop > 0.35:
            return AVOID, 1, f"market cap fell {drop * 100:.0f}% in under 2 min"
    return BUY, 0.5, "no crash pattern"


# ---------------------------------------------------------------- momentum & order flow


@agent("momentum", "Momentum Scout", "momentum", "5-minute price trend.")
def _momentum(tok, ctx):
    s = tok.snap
    if s is None:
        return ABSTAIN, 0, "no market data yet"
    c = s.change_m5
    if c < -15:
        return AVOID, 0.6, f"falling {_pct(c)} (5m)"
    if c < 3:
        return HOLD, 0.2, f"{'flat' if c > -3 else 'dipping'} {_pct(c)} (5m)"
    if c <= 80:
        return BUY, 0.5 + 0.5 * (1 - abs(c - 30) / 50), f"climbing {_pct(c)} in 5m"
    if c <= 200:
        return HOLD, 0.3, f"already {_pct(c)} in 5m: chase risk"
    return AVOID, 0.5, f"parabolic {_pct(c)} in 5m: late entry"


@agent("volume", "Volume Surge Analyst", "momentum", "Is 5-minute volume accelerating versus the hourly pace?")
def _volume(tok, ctx):
    s = tok.snap
    if s is None:
        return ABSTAIN, 0, "no market data yet"
    if s.vol_m5 < 1500:
        return HOLD, 0.2, f"thin volume {_usd(s.vol_m5)} (5m)"
    # vol_h1 covers the last hour or the pair's whole life, whichever is shorter
    born = tok.pair_created_at or tok.created_at
    window_min = max(5.0, min(60.0, (ctx.now - born) / 60)) if born else 60.0
    pace = s.vol_h1 / (window_min / 5.0) if s.vol_h1 else 0
    ratio = s.vol_m5 / pace if pace > 0 else 1.0
    if ratio >= 1.8:
        return BUY, 0.9, f"volume surging {ratio:.1f}x the hourly pace ({_usd(s.vol_m5)} in 5m)"
    if ratio >= 1.1:
        return BUY, 0.6, f"volume rising {ratio:.1f}x ({_usd(s.vol_m5)} in 5m)"
    if ratio >= 0.6:
        return HOLD, 0.3, f"volume steady ({ratio:.1f}x)"
    return AVOID, 0.5, f"volume fading ({ratio:.1f}x)"


@agent("buy_pressure", "Buy Pressure Analyst", "momentum", "Buyers versus sellers in the last 5 minutes.")
def _buy_pressure(tok, ctx):
    s = tok.snap
    if s is None:
        return ABSTAIN, 0, "no market data yet"
    r = s.buys_m5 / (s.sells_m5 + 1)
    if s.buys_m5 >= 25 and r >= 1.4:
        return BUY, 0.5 + 0.5 * _ramp(r, 1.4, 3), f"{s.buys_m5} buys vs {s.sells_m5} sells ({r:.1f}x)"
    if r >= 1.0:
        return HOLD, 0.3, f"balanced flow {s.buys_m5}/{s.sells_m5}"
    if r < 0.8:
        return AVOID, 0.6, f"sellers dominate {s.buys_m5}/{s.sells_m5}"
    return HOLD, 0.2, f"slightly more sellers {s.buys_m5}/{s.sells_m5}"


@agent("velocity", "Transaction Velocity Tracker", "momentum", "Trade count per 5 minutes and whether it is rising.")
def _velocity(tok, ctx):
    s = tok.snap
    if s is None:
        return ABSTAIN, 0, "no market data yet"
    n = s.buys_m5 + s.sells_m5
    prev = tok.snapshot_ago(60, ctx.now)
    trend = ""
    slowing = False
    if prev:
        pn = prev.buys_m5 + prev.sells_m5
        if pn > 0:
            slowing = n < pn * 0.7
            trend = f" ({'slowing' if slowing else 'up' if n > pn else 'steady'} from {pn})"
    if n < 15:
        return AVOID, 0.5, f"only {n} trades in 5m: dead"
    if n < 40:
        return HOLD, 0.2, f"{n} trades in 5m{trend}"
    if slowing:
        return HOLD, 0.3, f"{n} trades in 5m{trend}"
    return BUY, 0.5 + 0.5 * _ramp(n, 40, 250), f"{n} trades in 5m{trend}"


@agent("accel", "Trend Acceleration Engine", "momentum", "Is the market-cap slope steepening (second derivative)?")
def _accel(tok, ctx):
    pts = [s for s in tok.snapshots if ctx.now - s.t <= 240 and s.mcap_usd > 0]
    if len(pts) < 4 or pts[-1].t - pts[0].t < 60:
        return ABSTAIN, 0, "need ~1 min of chart history"
    mid = len(pts) // 2

    def slope(seg):
        if len(seg) < 2 or seg[-1].t == seg[0].t:
            return 0.0
        return (math.log(seg[-1].mcap_usd) - math.log(seg[0].mcap_usd)) / ((seg[-1].t - seg[0].t) / 60)

    a, b = slope(pts[: mid + 1]), slope(pts[mid:])
    if b > 0 and b >= a:
        return BUY, 0.5 + 0.5 * _ramp(b, 0, 0.15), f"trend accelerating ({b * 100:+.1f}%/min vs {a * 100:+.1f}%/min)"
    if b > 0:
        return HOLD, 0.3, f"still rising but slowing ({b * 100:+.1f}%/min)"
    return AVOID, 0.5, f"trend rolling over ({b * 100:+.1f}%/min)"


@agent("holder_growth", "Holder Growth Monitor", "momentum", "How fast new wallets are joining.")
def _holder_growth(tok, ctx):
    if tok.trades:
        buyers = {t.trader for t in tok.trades if t.is_buy and ctx.now - t.t <= 300}
        if len(buyers) >= 30:
            return BUY, 0.5 + 0.5 * _ramp(len(buyers), 30, 120), f"{len(buyers)} unique buyers in 5m"
        if len(buyers) >= 10:
            return HOLD, 0.3, f"{len(buyers)} unique buyers in 5m"
        return AVOID, 0.4, f"only {len(buyers)} unique buyers in 5m"
    h = list(tok.holder_history)
    if not h:
        return ABSTAIN, 0, "waiting for holder count"
    holders = h[-1][1]
    if len(h) >= 2 and h[-1][0] - h[0][0] >= 60:
        rate = (h[-1][1] - h[0][1]) / ((h[-1][0] - h[0][0]) / 60)
        if rate >= 3:
            return BUY, 0.5 + 0.5 * _ramp(rate, 3, 20), f"{holders} holders, +{rate:.0f}/min"
        if rate < 0:
            return AVOID, 0.5, f"holders shrinking ({rate:.0f}/min)"
        return HOLD, 0.3, f"{holders} holders, +{rate:.1f}/min"
    per_min = holders / max(1.0, tok.age_min(ctx.now))
    if holders >= 100 and per_min >= 2:
        return BUY, 0.5 + 0.5 * _ramp(per_min, 2, 15), f"{holders} holders ({per_min:.0f}/min since launch)"
    if holders < 40:
        return HOLD, 0.2, f"only {holders} holders"
    return HOLD, 0.3, f"{holders} holders"


@agent("whales", "Whale Flow Tracker", "momentum", "Organic retail flow versus one whale or micro-trade bots.")
def _whales(tok, ctx):
    s = tok.snap
    if s is None:
        return ABSTAIN, 0, "no market data yet"
    recent = [t for t in tok.trades if t.is_buy and ctx.now - t.t <= 300]
    if len(recent) >= 10:
        total = sum(t.sol for t in recent)
        top = max(t.sol for t in recent)
        if total > 0 and top / total > 0.4:
            return HOLD, 0.3, f"one buy is {top / total * 100:.0f}% of 5m inflow: whale-driven"
    n = s.buys_m5 + s.sells_m5
    if n == 0:
        return ABSTAIN, 0, "no trades"
    avg = s.vol_m5 / n
    if avg < 15:
        return AVOID, 0.5, f"avg trade {_usd(avg)}: bot wash trading"
    if avg > 1500:
        return HOLD, 0.3, f"avg trade {_usd(avg)}: a few whales"
    return BUY, 0.4 + 0.4 * (1 - abs(math.log10(avg) - 2) / 1.2), f"organic flow, avg trade {_usd(avg)}"


# ---------------------------------------------------------------- market structure


@agent("curve", "Bonding Curve Analyst", "structure", "Where the coin sits on pump.fun's bonding curve.")
def _curve(tok, ctx):
    s = tok.snap
    if tok.migrated:
        if s and s.liquidity_usd and s.liquidity_usd >= 20000 and tok.age_min(ctx.now) < 360:
            return BUY, 0.5, f"graduated to PumpSwap with {_usd(s.liquidity_usd)} liquidity"
        return HOLD, 0.3, "graduated; past the curve catalyst"
    if s is None or ctx.sol_usd <= 0:
        return ABSTAIN, 0, "need market cap and SOL price"
    p = curve_progress_from_mcap_sol(s.mcap_usd / ctx.sol_usd)
    if p < 0.1:
        return HOLD, 0.2, f"curve {p * 100:.0f}%: very early"
    if p < 0.25:
        return HOLD, 0.3, f"curve {p * 100:.0f}%"
    if p <= 0.88:
        return BUY, 0.5 + 0.5 * _ramp(p, 0.25, 0.75), f"curve {p * 100:.0f}% filled: graduation in sight"
    return HOLD, 0.3, f"curve {p * 100:.0f}%: graduation sell-off risk"


@agent("mcap", "Market Cap Positioner", "structure", "Is the market cap in a range that can still multiply?")
def _mcap(tok, ctx):
    s = tok.snap
    if s is None:
        return ABSTAIN, 0, "no market data yet"
    m = s.mcap_usd
    if not tok.migrated:
        if m < 6000:
            return AVOID, 0.4, f"{_usd(m)} mcap: no traction"
        if m < 10000:
            return HOLD, 0.2, f"{_usd(m)} mcap: unproven"
        return BUY, 0.5 + 0.4 * (1 - _ramp(m, 40000, 90000)), f"{_usd(m)} mcap on the curve"
    if m < 60000:
        return AVOID, 0.4, f"{_usd(m)} after graduating: weak"
    if m <= 3_000_000:
        return BUY, 0.5, f"{_usd(m)} mcap after graduating"
    return HOLD, 0.3, f"{_usd(m)} mcap: big already"


@agent("timing", "Launch Timing Agent", "structure", "Age of the coin: not too fresh to judge, not stale.")
def _timing(tok, ctx):
    age = tok.age_min(ctx.now)
    if tok.created_at is None and age < 2:
        return ABSTAIN, 0, "launch time unknown"
    if age < 2:
        return HOLD, 0.2, f"{age:.1f} min old: too fresh to judge"
    if age <= 60:
        return BUY, 0.7, f"{age:.0f} min old: prime window"
    if age <= 240:
        return BUY, 0.4, f"{age / 60:.1f} h old and still trading"
    return HOLD, 0.3, f"{age / 60:.1f} h old"


@agent("volatility", "Volatility Risk Agent", "structure", "Minute-to-minute swings: tradeable or a coin flip?")
def _volatility(tok, ctx):
    pts = [s for s in tok.snapshots if ctx.now - s.t <= 300 and s.mcap_usd > 0]
    if len(pts) < 4:
        return ABSTAIN, 0, "need more chart history"
    rets = []
    for a, b in zip(pts, pts[1:]):
        dt = max(1.0, b.t - a.t) / 60
        rets.append(math.log(b.mcap_usd / a.mcap_usd) / math.sqrt(dt))
    vol = statistics.pstdev(rets) * 100
    if vol > 25:
        return HOLD, 0.2, f"{vol:.0f}%/min swings: coin flip"
    if vol < 0.5:
        return HOLD, 0.2, "barely moving"
    return BUY, 0.6, f"{vol:.1f}%/min volatility: tradeable"


@agent("entry", "Entry Timing Agent", "structure", "Breakout to new highs, or a healthy pullback with a higher low.")
def _entry(tok, ctx):
    s = tok.snap
    if s is None or tok.ath_mcap <= 0:
        return ABSTAIN, 0, "no market data yet"
    dd = 1 - s.mcap_usd / tok.ath_mcap
    prev = tok.snapshot_ago(30, ctx.now)
    rising = prev is not None and s.mcap_usd > prev.mcap_usd
    if dd < 0.03 and s.change_m5 > 0:
        return BUY, 0.7, "breaking out to new highs"
    if 0.08 <= dd <= 0.35 and rising:
        return BUY, 0.6, f"bouncing {dd * 100:.0f}% below the high (higher low)"
    if dd <= 0.35:
        return HOLD, 0.3, f"{dd * 100:.0f}% below the high, no bounce yet"
    if dd <= 0.55:
        return HOLD, 0.2, f"{dd * 100:.0f}% below the high"
    return AVOID, 0.6, f"{dd * 100:.0f}% below the high"


# ---------------------------------------------------------------- narrative & social


@agent("socials", "Social Presence Verifier", "narrative", "Twitter / Telegram / website attached to the coin.")
def _socials(tok, ctx):
    if not tok.metadata_loaded and not tok.has_profile:
        return ABSTAIN, 0, "metadata loading"
    links = [x for x in (tok.twitter, tok.telegram, tok.website) if x]
    if len(links) >= 2:
        return BUY, 0.5 + 0.2 * (len(links) - 2), f"{len(links)} social links"
    if tok.twitter:
        return BUY, 0.4, "has an X/Twitter link"
    if links:
        return HOLD, 0.3, "one social link"
    return AVOID, 0.5, "no socials at all"


SPAM_WORDS = ("airdrop", "presale", "giveaway", "guaranteed", "100x", "1000x", "free money", "send sol")


@agent("metadata", "Metadata Quality Inspector", "narrative", "Description, image and ticker quality; spam phrasing.")
def _metadata(tok, ctx):
    if not tok.metadata_loaded:
        return ABSTAIN, 0, "metadata loading"
    text = f"{tok.name} {tok.symbol} {tok.description}".lower()
    text = re.sub(r"\b(no|zero|without)\s+(presale|airdrop)s?", " ", text)  # "no presale" is a selling point
    spam = [w for w in SPAM_WORDS if w in text]
    if spam:
        return AVOID, 0.6, f"spam phrasing: {', '.join(spam)}"
    if not tok.symbol or len(tok.symbol) > 12:
        return HOLD, 0.2, "odd ticker"
    if len(tok.description.strip()) < 15:
        return HOLD, 0.3, "no real description"
    return BUY, 0.5 if tok.image else 0.3, "clean metadata and description"


@agent("narrative", "Narrative & Meta Detector", "narrative", "Is the coin riding the meta that is moving right now, and is it the leader?")
def _narrative(tok, ctx):
    words = tok.words()
    hot = {w: ctx.hot_words[w] for w in words if w in ctx.hot_words}
    if not hot:
        return HOLD, 0.25, "not part of a hot narrative"
    w = max(hot, key=hot.get)
    s = tok.snap
    my = s.mcap_usd if s else 0
    n, best = ctx.word_coins.get(w, (1, my))
    if my >= best:
        return BUY, 0.5 + 0.5 * _ramp(hot[w], 3, 20), f"leads the hot '{w}' narrative ({n - 1} rivals)"
    return HOLD, 0.3, f"follower in the '{w}' narrative"


@agent("copycat", "Copycat Detector", "narrative", "Is this a clone of a bigger coin with the same ticker?")
def _copycat(tok, ctx):
    if not tok.symbol:
        return ABSTAIN, 0, "no ticker"
    same = [ctx.tokens[m] for m in ctx.symbol_index.get(tok.symbol.lower(), ()) if m != tok.mint and m in ctx.tokens]
    s = tok.snap
    my = s.mcap_usd if s else 0
    bigger = [t for t in same if t.snap and t.snap.mcap_usd > max(my, 1) * 2]
    if bigger:
        return AVOID, 0.7, f"copy of a bigger ${tok.symbol} ({len(same) + 1} with this ticker)"
    if len(same) >= 2:
        return BUY, 0.6, f"the original/leading ${tok.symbol} ({len(same)} copies chasing it)"
    return BUY, 0.4, "unique ticker"


@agent("attention", "DexScreener Attention Agent", "narrative", "Paid profile, boosts or community takeover: someone is spending to market it.")
def _attention(tok, ctx):
    if tok.cto:
        return BUY, 0.6, "community takeover on DexScreener"
    if tok.boosts > 0:
        return BUY, 0.5 + 0.1 * min(tok.boosts, 5), f"{tok.boosts} DexScreener boosts"
    if tok.has_profile:
        return BUY, 0.5, "paid DexScreener profile"
    if not tok.snapshots:
        return ABSTAIN, 0, "not on DexScreener yet"
    return HOLD, 0.3, "no paid attention yet"


assert len(AGENTS) == 25, len(AGENTS)
AGENTS_BY_ID = {a.id: a for a in AGENTS}

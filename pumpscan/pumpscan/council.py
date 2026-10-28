"""The council: runs all 25 agents on a coin and decides whether the vote is a BUY signal."""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass, field

from .agents import ABSTAIN, AGENTS, AGENTS_BY_ID, AVOID, BUY, Vote
from .models import MarketContext, Token


@dataclass
class Rules:
    # BUY votes needed out of 25. 25 = unanimous: every agent must have data and agree.
    min_buy_votes: int = 20
    # AVOID votes tolerated from non-safety agents. Safety agents always veto.
    max_avoid_votes: int = 0
    # How long a signal stays actionable.
    window_minutes: float = 10.0
    # Do not chase: the signal is void once price is this far above the entry.
    max_chase_pct: float = 15.0
    # Do not repeat a signal on the same coin within this many minutes.
    cooldown_minutes: float = 30.0


@dataclass
class Verdict:
    mint: str
    buy_votes: int
    hold_votes: int
    avoid_votes: int
    abstain_votes: int
    vetoed_by: list[str]
    conviction: float  # 0..100, mean BUY strength among BUY voters
    is_signal: bool
    votes: list[Vote] = field(default_factory=list)


def run_council(tok: Token, ctx: MarketContext, rules: Rules) -> Verdict:
    votes = [a.vote(tok, ctx) for a in AGENTS]
    counts = {BUY: 0, "HOLD": 0, AVOID: 0, ABSTAIN: 0}
    for v in votes:
        counts[v.stance] += 1
    vetoed_by = [v.agent for v in votes if v.stance == AVOID and AGENTS_BY_ID[v.agent].veto]
    buy_strengths = [v.strength for v in votes if v.stance == BUY]
    conviction = 100 * statistics.fmean(buy_strengths) if buy_strengths else 0.0
    is_signal = (
        counts[BUY] >= rules.min_buy_votes
        and not vetoed_by
        and counts[AVOID] <= rules.max_avoid_votes
    )
    tok.last_votes = votes
    tok.buy_votes = counts[BUY]
    tok.avoid_votes = counts[AVOID]
    tok.vetoed = bool(vetoed_by)
    tok.conviction = conviction
    return Verdict(tok.mint, counts[BUY], counts["HOLD"], counts[AVOID], counts[ABSTAIN],
                   vetoed_by, round(conviction, 1), is_signal, votes)


def rank_key(tok: Token) -> tuple:
    """Leaderboard order: unvetoed first, then BUY votes, then conviction."""
    return (not tok.vetoed, tok.buy_votes - tok.avoid_votes, tok.conviction)


def realised_volatility(tok: Token, now: float) -> float:
    """Per-minute volatility (fraction) over the last 5 minutes; 0.1 when unknown."""
    pts = [s for s in tok.snapshots if now - s.t <= 300 and s.mcap_usd > 0]
    if len(pts) < 4:
        return 0.10
    rets = [math.log(b.mcap_usd / a.mcap_usd) / math.sqrt(max(1.0, b.t - a.t) / 60) for a, b in zip(pts, pts[1:])]
    return statistics.pstdev(rets)


def build_signal(tok: Token, verdict: Verdict, rules: Rules, now: float) -> dict:
    """The actionable verdict shown to the user."""
    s = tok.snap
    vol = realised_volatility(tok, now)
    stop = min(0.30, max(0.12, 2.5 * vol))
    reasons = sorted((v for v in verdict.votes if v.stance == BUY), key=lambda v: v.strength, reverse=True)
    cautions = [v for v in verdict.votes if v.stance in ("HOLD", AVOID)]
    entry_mcap = s.mcap_usd
    return {
        "id": f"{tok.mint}:{int(now)}",
        "mint": tok.mint,
        "name": tok.name,
        "symbol": tok.symbol,
        "image": tok.image,
        "created": now,
        "expires": now + rules.window_minutes * 60,
        "window_minutes": rules.window_minutes,
        "headline": f"BUY NOW: {tok.name or tok.symbol} (${tok.symbol}) within the next {rules.window_minutes:g} minutes",
        "entry_price_usd": s.price_usd,
        "entry_mcap_usd": entry_mcap,
        "max_entry_mcap_usd": entry_mcap * (1 + rules.max_chase_pct / 100),
        "stop_loss_pct": round(-stop * 100, 1),
        "take_profit_1_pct": round(stop * 1.5 * 100, 1),
        "take_profit_2_pct": round(stop * 3 * 100, 1),
        "agree": verdict.buy_votes,
        "total_agents": len(AGENTS),
        "conviction": verdict.conviction,
        "reasons": [{"agent": AGENTS_BY_ID[v.agent].name, "reason": v.reason, "strength": v.strength} for v in reasons],
        "cautions": [{"agent": AGENTS_BY_ID[v.agent].name, "stance": v.stance, "reason": v.reason} for v in cautions],
        "links": {
            "pumpfun": f"https://pump.fun/coin/{tok.mint}",
            "dexscreener": f"https://dexscreener.com/solana/{tok.mint}",
            "rugcheck": f"https://rugcheck.xyz/tokens/{tok.mint}",
        },
        "outcome": None,
    }


def score_outcome(sig: dict, tok: Token | None, now: float) -> dict | None:
    """After the window closes: what actually happened to the price."""
    if now < sig["expires"] or tok is None:
        return None
    pts = [x for x in tok.snapshots if sig["created"] <= x.t <= sig["expires"] + 30 and x.mcap_usd > 0]
    if not pts:
        return {"status": "no_data"}
    entry = sig["entry_mcap_usd"]
    peak = max(x.mcap_usd for x in pts)
    low = min(x.mcap_usd for x in pts)
    end = pts[-1].mcap_usd
    tp1 = sig["take_profit_1_pct"] / 100
    sl = sig["stop_loss_pct"] / 100
    hit_tp = peak >= entry * (1 + tp1)
    hit_sl = low <= entry * (1 + sl)
    # If both happened we cannot know the order from snapshots; count the stop first (conservative).
    if hit_sl:
        status = "stopped"
    elif hit_tp:
        status = "win"
    else:
        status = "up" if end > entry else "down"
    return {
        "status": status,
        "end_pct": round((end / entry - 1) * 100, 1),
        "peak_pct": round((peak / entry - 1) * 100, 1),
        "low_pct": round((low / entry - 1) * 100, 1),
    }


def votes_json(votes: list[Vote]) -> list[dict]:
    return [asdict(v) for v in votes]

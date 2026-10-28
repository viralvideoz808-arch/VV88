"""Data the agents read: one Token per coin, plus the market-wide context."""

from __future__ import annotations

import math
import re
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field

# pump.fun bonding curve constants (1B supply, 6 decimals).
CURVE_START_VSOL = 30.0
CURVE_START_VTOKENS = 1_073_000_000.0
CURVE_REAL_SOL_AT_GRADUATION = 85.0
TOTAL_SUPPLY = 1_000_000_000.0


def curve_progress_from_mcap_sol(mcap_sol: float) -> float:
    """Fraction of the bonding curve sold (0..1), from a market cap in SOL.

    On the curve vSol * vTokens = k and price = vSol / vTokens, so vSol = sqrt(k * price).
    """
    if mcap_sol <= 0:
        return 0.0
    k = CURVE_START_VSOL * CURVE_START_VTOKENS
    price = mcap_sol / TOTAL_SUPPLY
    vsol = math.sqrt(k * price)
    return max(0.0, min(1.0, (vsol - CURVE_START_VSOL) / CURVE_REAL_SOL_AT_GRADUATION))


@dataclass
class Snapshot:
    """One DexScreener (or simulated) reading of a coin's pair."""

    t: float
    price_usd: float
    mcap_usd: float
    buys_m5: int = 0
    sells_m5: int = 0
    buys_h1: int = 0
    sells_h1: int = 0
    vol_m5: float = 0.0
    vol_h1: float = 0.0
    change_m5: float = 0.0
    change_h1: float = 0.0
    liquidity_usd: float | None = None


@dataclass
class Trade:
    """A single swap from the PumpPortal trade stream (only with an API key)."""

    t: float
    trader: str
    is_buy: bool
    sol: float
    mcap_sol: float


@dataclass
class RugReport:
    """The parts of a Rugcheck report the agents use."""

    fetched_at: float
    score_normalised: float | None = None
    mint_authority: bool = False
    freeze_authority: bool = False
    rugged: bool = False
    total_holders: int | None = None
    top10_pct: float | None = None  # excluding the bonding curve / AMM pool
    max_holder_pct: float | None = None
    insider_pct: float = 0.0
    insiders_detected: int = 0
    creator_pct: float | None = None
    creator_token_count: int | None = None
    danger_risks: list[str] = field(default_factory=list)
    warn_risks: list[str] = field(default_factory=list)
    # token facts Rugcheck knows that the scan may not (coins first seen on DexScreener)
    creator: str = ""
    detected_at: float | None = None
    name: str = ""
    symbol: str = ""
    uri: str = ""
    description: str = ""
    image: str = ""


@dataclass
class Token:
    mint: str
    name: str = ""
    symbol: str = ""
    created_at: float | None = None  # unix seconds; None when seen only on DexScreener
    first_seen: float = field(default_factory=time.time)
    creator: str = ""
    pool: str = "pump"
    dev_buy_sol: float = 0.0
    dev_tokens: float = 0.0
    uri: str = ""
    # metadata (IPFS json and/or DexScreener info)
    description: str = ""
    twitter: str = ""
    telegram: str = ""
    website: str = ""
    image: str = ""
    metadata_loaded: bool = False
    # DexScreener
    dex_id: str = ""
    pair_address: str = ""
    pair_created_at: float | None = None
    boosts: int = 0
    has_profile: bool = False
    cto: bool = False
    migrated: bool = False
    snapshots: deque = field(default_factory=lambda: deque(maxlen=360))
    trades: deque = field(default_factory=lambda: deque(maxlen=3000))
    rug: RugReport | None = None
    holder_history: deque = field(default_factory=lambda: deque(maxlen=20))  # (t, holders)
    ath_mcap: float = 0.0
    ath_t: float = 0.0
    # council output
    last_votes: list = field(default_factory=list)
    buy_votes: int = 0
    avoid_votes: int = 0
    vetoed: bool = False
    conviction: float = 0.0
    signaled_at: float | None = None
    last_dex: float = 0.0  # last DexScreener refresh (engine clock)
    dex_misses: int = 0
    rug_attempt: float = 0.0  # last Rugcheck request (engine clock)

    @property
    def snap(self) -> Snapshot | None:
        return self.snapshots[-1] if self.snapshots else None

    def age_min(self, now: float) -> float:
        start = self.created_at or self.first_seen
        return max(0.0, (now - start) / 60.0)

    def add_snapshot(self, s: Snapshot) -> None:
        if self.snapshots and s.t <= self.snapshots[-1].t:
            return
        self.snapshots.append(s)
        if s.mcap_usd > self.ath_mcap:
            self.ath_mcap = s.mcap_usd
            self.ath_t = s.t

    def snapshot_ago(self, seconds: float, now: float) -> Snapshot | None:
        """The newest snapshot at least `seconds` old, if any."""
        for s in reversed(self.snapshots):
            if now - s.t >= seconds:
                return s
        return None

    def words(self) -> set[str]:
        return name_words(f"{self.name} {self.symbol}")


_STOP = {"the", "coin", "token", "sol", "solana", "pump", "fun", "inu", "official", "and", "of", "on", "for", "meme"}


def name_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 3 and w not in _STOP}


@dataclass
class MarketContext:
    """Market-wide state shared by all agents in one evaluation pass."""

    now: float = field(default_factory=time.time)
    sol_usd: float = 0.0
    creator_counts: Counter = field(default_factory=Counter)
    launch_words: deque = field(default_factory=lambda: deque(maxlen=20000))  # (t, word)
    symbol_index: dict = field(default_factory=lambda: defaultdict(set))  # symbol -> mints
    tokens: dict = field(default_factory=dict)  # mint -> Token
    hot_words: dict = field(default_factory=dict)  # word -> heat (computed per pass)
    word_coins: dict = field(default_factory=dict)  # hot word -> (coin count, leader's mcap)

    def note_launch(self, tok: Token) -> None:
        if tok.creator:
            self.creator_counts[tok.creator] += 1
        t = tok.created_at or self.now
        for w in tok.words():
            self.launch_words.append((t, w))
        if tok.symbol:
            self.symbol_index[tok.symbol.lower()].add(tok.mint)

    def refresh_hot_words(self) -> None:
        """Heat = recent launches using the word + coins with that word that are actually moving."""
        cutoff = self.now - 20 * 60
        recent = Counter(w for t, w in self.launch_words if t >= cutoff)
        movers: Counter = Counter()
        for tok in self.tokens.values():
            s = tok.snap
            if s and s.change_m5 > 10 and s.buys_m5 + s.sells_m5 > 30:
                for w in tok.words():
                    movers[w] += 1
        heat = {}
        for w in set(recent) | set(movers):
            score = movers[w] * 2 + (recent[w] if recent[w] >= 3 else 0)
            if score >= 3:
                heat[w] = score
        self.hot_words = heat
        coins: dict = {}
        for tok in self.tokens.values():
            m = tok.snap.mcap_usd if tok.snap else 0.0
            for w in tok.words() & heat.keys():
                n, best = coins.get(w, (0, 0.0))
                coins[w] = (n + 1, max(best, m))
        self.word_coins = coins

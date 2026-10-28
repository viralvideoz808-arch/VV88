// The data model, the 25 agents and the council: a line-by-line port of pumpscan/models.py,
// agents.py and council.py so the browser version votes exactly like the Python one.

export const BUY = "BUY", HOLD = "HOLD", AVOID = "AVOID", ABSTAIN = "ABSTAIN";

// pump.fun bonding curve constants (1B supply, 6 decimals).
const CURVE_START_VSOL = 30.0;
const CURVE_START_VTOKENS = 1_073_000_000.0;
const CURVE_REAL_SOL_AT_GRADUATION = 85.0;
const TOTAL_SUPPLY = 1_000_000_000.0;

const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));

// Fraction of the bonding curve sold (0..1), from a market cap in SOL.
// On the curve vSol * vTokens = k and price = vSol / vTokens, so vSol = sqrt(k * price).
export function curveProgressFromMcapSol(mcapSol) {
  if (mcapSol <= 0) return 0;
  const k = CURVE_START_VSOL * CURVE_START_VTOKENS;
  const vsol = Math.sqrt(k * (mcapSol / TOTAL_SUPPLY));
  return clamp((vsol - CURVE_START_VSOL) / CURVE_REAL_SOL_AT_GRADUATION, 0, 1);
}

const STOP = new Set(["the", "coin", "token", "sol", "solana", "pump", "fun", "inu", "official", "and", "of", "on", "for", "meme"]);

export function nameWords(text) {
  const out = new Set();
  for (const w of (text.toLowerCase().match(/[a-z0-9]+/g) || [])) {
    if (w.length >= 3 && !STOP.has(w)) out.add(w);
  }
  return out;
}

export function makeSnapshot(f) {
  return {
    t: f.t, price_usd: f.price_usd, mcap_usd: f.mcap_usd,
    buys_m5: f.buys_m5 ?? 0, sells_m5: f.sells_m5 ?? 0, buys_h1: f.buys_h1 ?? 0, sells_h1: f.sells_h1 ?? 0,
    vol_m5: f.vol_m5 ?? 0, vol_h1: f.vol_h1 ?? 0, change_m5: f.change_m5 ?? 0, change_h1: f.change_h1 ?? 0,
    liquidity_usd: f.liquidity_usd ?? null,
  };
}

export function makeRugReport(f) {
  return {
    fetched_at: f.fetched_at, score_normalised: null, mint_authority: false, freeze_authority: false,
    rugged: false, total_holders: null, top10_pct: null, max_holder_pct: null, insider_pct: 0,
    insiders_detected: 0, creator_pct: null, creator_token_count: null, danger_risks: [], warn_risks: [],
    creator: "", detected_at: null, name: "", symbol: "", uri: "", description: "", image: "",
    ...f,
  };
}

const MAX_SNAPSHOTS = 360, MAX_TRADES = 3000, MAX_HOLDER_HISTORY = 20;

export class Token {
  constructor(f) {
    Object.assign(this, {
      mint: "", name: "", symbol: "", created_at: null, first_seen: Date.now() / 1000, creator: "",
      pool: "pump", dev_buy_sol: 0, dev_tokens: 0, uri: "",
      description: "", twitter: "", telegram: "", website: "", image: "", metadata_loaded: false, meta_attempts: 0,
      metadata_unreachable: false, // browser-only: the metadata host blocks cross-origin reads
      dex_id: "", pair_address: "", pair_created_at: null, boosts: 0, has_profile: false, cto: false, migrated: false,
      snapshots: [], trades: [], rug: null, holder_history: [], ath_mcap: 0, ath_t: 0,
      last_votes: [], buy_votes: 0, avoid_votes: 0, vetoed: false, conviction: 0, signaled_at: null,
      last_dex: 0, dex_misses: 0, rug_attempt: 0,
    }, f);
  }

  get snap() { return this.snapshots.length ? this.snapshots[this.snapshots.length - 1] : null; }

  ageMin(now) { return Math.max(0, (now - (this.created_at ?? this.first_seen)) / 60); }

  addSnapshot(s) {
    if (this.snapshots.length && s.t <= this.snap.t) return;
    this.snapshots.push(s);
    if (this.snapshots.length > MAX_SNAPSHOTS) this.snapshots.shift();
    if (s.mcap_usd > this.ath_mcap) { this.ath_mcap = s.mcap_usd; this.ath_t = s.t; }
  }

  addTrade(tr) {
    this.trades.push(tr);
    if (this.trades.length > MAX_TRADES) this.trades.shift();
  }

  addHolders(t, n) {
    this.holder_history.push([t, n]);
    if (this.holder_history.length > MAX_HOLDER_HISTORY) this.holder_history.shift();
  }

  // The newest snapshot at least `seconds` old, if any.
  snapshotAgo(seconds, now) {
    for (let i = this.snapshots.length - 1; i >= 0; i--) {
      if (now - this.snapshots[i].t >= seconds) return this.snapshots[i];
    }
    return null;
  }

  words() { return nameWords(`${this.name} ${this.symbol}`); }
}

// Market-wide state shared by all agents in one evaluation pass.
export class MarketContext {
  constructor() {
    this.now = Date.now() / 1000;
    this.sol_usd = 0;
    this.creator_counts = new Map();
    this.launch_words = []; // [t, word]
    this.symbol_index = new Map(); // symbol -> Set(mints)
    this.tokens = new Map(); // mint -> Token
    this.hot_words = new Map(); // word -> heat
    this.word_coins = new Map(); // hot word -> [coin count, leader's mcap]
  }

  noteLaunch(tok) {
    if (tok.creator) this.creator_counts.set(tok.creator, (this.creator_counts.get(tok.creator) || 0) + 1);
    const t = tok.created_at ?? this.now;
    for (const w of tok.words()) this.launch_words.push([t, w]);
    if (this.launch_words.length > 20000) this.launch_words.splice(0, this.launch_words.length - 20000);
    if (tok.symbol) this.indexSymbol(tok);
  }

  indexSymbol(tok) {
    const k = tok.symbol.toLowerCase();
    if (!this.symbol_index.has(k)) this.symbol_index.set(k, new Set());
    this.symbol_index.get(k).add(tok.mint);
  }

  // Heat = recent launches using the word + coins with that word that are actually moving.
  refreshHotWords() {
    const cutoff = this.now - 20 * 60;
    const recent = new Map(), movers = new Map();
    for (const [t, w] of this.launch_words) if (t >= cutoff) recent.set(w, (recent.get(w) || 0) + 1);
    for (const tok of this.tokens.values()) {
      const s = tok.snap;
      if (s && s.change_m5 > 10 && s.buys_m5 + s.sells_m5 > 30) {
        for (const w of tok.words()) movers.set(w, (movers.get(w) || 0) + 1);
      }
    }
    const heat = new Map();
    for (const w of new Set([...recent.keys(), ...movers.keys()])) {
      const r = recent.get(w) || 0;
      const score = (movers.get(w) || 0) * 2 + (r >= 3 ? r : 0);
      if (score >= 3) heat.set(w, score);
    }
    this.hot_words = heat;
    const coins = new Map();
    for (const tok of this.tokens.values()) {
      const m = tok.snap ? tok.snap.mcap_usd : 0;
      for (const w of tok.words()) {
        if (!heat.has(w)) continue;
        const [n, best] = coins.get(w) || [0, 0];
        coins.set(w, [n + 1, Math.max(best, m)]);
      }
    }
    this.word_coins = coins;
  }
}

// ------------------------------------------------------------------ agents

const pct = (x) => `${x >= 0 ? "+" : ""}${x.toFixed(0)}%`;
const usd = (x) => x >= 1e6 ? `$${(x / 1e6).toFixed(2)}M` : x >= 1e3 ? `$${(x / 1e3).toFixed(1)}k` : `$${x.toFixed(0)}`;
const ramp = (x, lo, hi) => hi === lo ? 1 : clamp((x - lo) / (hi - lo), 0, 1); // 0 at lo, 1 at hi
const sum = (xs) => xs.reduce((a, b) => a + b, 0);
const signed = (x, d) => `${x >= 0 ? "+" : ""}${x.toFixed(d)}`;

function pstdev(xs) {
  const m = sum(xs) / xs.length;
  return Math.sqrt(sum(xs.map((x) => (x - m) ** 2)) / xs.length);
}

export const AGENTS = [];
function agent(id, name, group, description, veto, fn) {
  AGENTS.push({ id, name, group, description, veto, fn });
}

// ---- safety (veto) agents

agent("mint_auth", "Mint Authority Sentinel", "safety",
  "Mint and freeze authority must be revoked, or the dev can print tokens or freeze your wallet.", true, (tok) => {
    const r = tok.rug;
    if (!r) return [ABSTAIN, 0, "waiting for on-chain report"];
    if (r.mint_authority) return [AVOID, 1, "mint authority NOT revoked: dev can print more supply"];
    if (r.freeze_authority) return [AVOID, 1, "freeze authority NOT revoked: your tokens can be frozen"];
    return [BUY, 0.6, "mint & freeze authority revoked"];
  });

agent("holders", "Holder Concentration Auditor", "safety",
  "Top-10 wallets (excluding the curve/pool) must not control the supply.", true, (tok) => {
    const r = tok.rug;
    if (!r || r.top10_pct == null) return [ABSTAIN, 0, "waiting for holder list"];
    if ((r.max_holder_pct || 0) > 12) return [AVOID, 1, `one wallet holds ${r.max_holder_pct.toFixed(1)}% of supply`];
    if (r.top10_pct > 40) return [AVOID, 0.9, `top 10 wallets hold ${r.top10_pct.toFixed(0)}%: dump risk`];
    if (r.top10_pct > 28) return [HOLD, 0.3, `top 10 hold ${r.top10_pct.toFixed(0)}% (borderline)`];
    return [BUY, 0.5 + 0.5 * ramp(28 - r.top10_pct, 0, 18), `well distributed: top 10 hold ${r.top10_pct.toFixed(0)}%`];
  });

agent("dev_wallet", "Dev Wallet Watcher", "safety",
  "How much the creator still holds and whether they are dumping.", true, (tok, ctx) => {
    const devSells = tok.creator ? tok.trades.filter((t) => t.trader === tok.creator && !t.is_buy) : [];
    const r = tok.rug;
    if (devSells.length) {
      const recent = devSells.filter((t) => ctx.now - t.t < 300);
      const sol = sum(recent.map((t) => t.sol));
      if (recent.length && sol > 1) return [AVOID, 0.9, `dev sold ${sol.toFixed(1)} SOL in the last 5 min`];
    }
    if (r && r.creator_pct != null) {
      if (r.creator_pct > 10) return [AVOID, 0.9, `dev still holds ${r.creator_pct.toFixed(1)}%: can crash the chart`];
      if (r.creator_pct > 5) return [HOLD, 0.3, `dev holds ${r.creator_pct.toFixed(1)}%`];
      if (r.creator_pct === 0 && devSells.length) return [HOLD, 0.2, "dev fully exited: community has to carry it"];
      return [BUY, 0.6, `dev holds only ${r.creator_pct.toFixed(1)}%`];
    }
    if (tok.dev_buy_sol > 0 && !devSells.length && !r && tok.dev_buy_sol > 5) {
      return [HOLD, 0.3, `dev bought ${tok.dev_buy_sol.toFixed(1)} SOL at launch (big bag)`];
    }
    return [ABSTAIN, 0, "waiting for dev balance"];
  });

agent("insiders", "Insider & Bundle Detector", "safety",
  "Bundled launches and linked insider wallets that sell into buyers.", true, (tok) => {
    const r = tok.rug;
    if (tok.trades.length && tok.created_at) {
      const early = new Set(tok.trades.filter((t) => t.is_buy && t.t - tok.created_at <= 2 && t.trader !== tok.creator).map((t) => t.trader));
      if (early.size >= 4) return [AVOID, 0.9, `${early.size} wallets bought within 2s of launch (bundled)`];
    }
    if (!r) return [ABSTAIN, 0, "waiting for insider graph"];
    if (r.insider_pct > 8 || r.insiders_detected >= 5) {
      return [AVOID, 0.9, `insider network detected (${r.insiders_detected} wallets, ${r.insider_pct.toFixed(0)}%)`];
    }
    if (r.insiders_detected > 0) return [HOLD, 0.3, `${r.insiders_detected} linked insider wallets`];
    return [BUY, 0.6, "no insider network or bundle found"];
  });

agent("serial_dev", "Serial Launcher Profiler", "safety",
  "Creators that launch coin after coin are farming buyers.", true, (tok, ctx) => {
    let n = tok.creator ? (ctx.creator_counts.get(tok.creator) || 0) : 0;
    const known = tok.rug && tok.rug.creator_token_count != null;
    if (known) n = Math.max(n, tok.rug.creator_token_count);
    if (!tok.creator && !known) return [ABSTAIN, 0, "creator unknown"];
    if (n >= 3) return [AVOID, 1, `creator launched ${n} coins: serial rugger pattern`];
    if (n === 2) return [HOLD, 0.3, "creator's 2nd coin"];
    return [BUY, 0.5, "no other launches seen from this creator"];
  });

agent("rugcheck", "Rugcheck Risk Scorer", "safety", "Rugcheck.xyz risk flags.", true, (tok) => {
  const r = tok.rug;
  if (!r) return [ABSTAIN, 0, "waiting for Rugcheck"];
  if (r.rugged) return [AVOID, 1, "Rugcheck marks it RUGGED"];
  if (r.danger_risks.length) return [AVOID, 0.9, "danger: " + r.danger_risks.slice(0, 2).join("; ")];
  if (r.warn_risks.length >= 3) return [HOLD, 0.3, "warnings: " + r.warn_risks.slice(0, 2).join("; ")];
  const w = r.warn_risks.length;
  return [BUY, w ? 0.5 : 0.7, "no danger flags" + (w ? ` (${w} warnings)` : "")];
});

agent("exit_liq", "Exit Liquidity Guard", "safety",
  "Can you actually get out? Sell flow, pool depth, distance from the peak.", true, (tok) => {
    const s = tok.snap;
    if (!s) return [ABSTAIN, 0, "no market data yet"];
    if (tok.migrated && s.liquidity_usd != null && s.liquidity_usd < 8000) {
      return [AVOID, 0.9, `pool liquidity only ${usd(s.liquidity_usd)}`];
    }
    if (tok.ath_mcap > 0) {
      const dd = 1 - s.mcap_usd / tok.ath_mcap;
      if (dd > 0.55) return [AVOID, 0.9, `down ${(dd * 100).toFixed(0)}% from its peak: buyers already trapped`];
    }
    if (s.sells_m5 === 0 && s.buys_m5 > 20) return [HOLD, 0.2, "no sells seen yet: cannot confirm exits"];
    return [BUY, 0.5, "sells are clearing normally"];
  });

agent("rug_pattern", "Rug Pattern Detector", "safety",
  "Sudden crashes and liquidity pulls in the live chart.", true, (tok, ctx) => {
    const s = tok.snap;
    if (!s) return [ABSTAIN, 0, "no market data yet"];
    if (s.change_m5 < -30) return [AVOID, 1, `crashed ${pct(s.change_m5)} in 5 min`];
    const prev = tok.snapshotAgo(45, ctx.now);
    if (prev && prev.mcap_usd > 0 && ctx.now - prev.t < 150) {
      const drop = 1 - s.mcap_usd / prev.mcap_usd;
      if (drop > 0.35) return [AVOID, 1, `market cap fell ${(drop * 100).toFixed(0)}% in under 2 min`];
    }
    return [BUY, 0.5, "no crash pattern"];
  });

// ---- momentum & order flow

agent("momentum", "Momentum Scout", "momentum", "5-minute price trend.", false, (tok) => {
  const s = tok.snap;
  if (!s) return [ABSTAIN, 0, "no market data yet"];
  const c = s.change_m5;
  if (c < -15) return [AVOID, 0.6, `falling ${pct(c)} (5m)`];
  if (c < 3) return [HOLD, 0.2, `${c > -3 ? "flat" : "dipping"} ${pct(c)} (5m)`];
  if (c <= 80) return [BUY, 0.5 + 0.5 * (1 - Math.abs(c - 30) / 50), `climbing ${pct(c)} in 5m`];
  if (c <= 200) return [HOLD, 0.3, `already ${pct(c)} in 5m: chase risk`];
  return [AVOID, 0.5, `parabolic ${pct(c)} in 5m: late entry`];
});

agent("volume", "Volume Surge Analyst", "momentum", "Is 5-minute volume accelerating versus the hourly pace?", false, (tok, ctx) => {
  const s = tok.snap;
  if (!s) return [ABSTAIN, 0, "no market data yet"];
  if (s.vol_m5 < 1500) return [HOLD, 0.2, `thin volume ${usd(s.vol_m5)} (5m)`];
  // vol_h1 covers the last hour or the pair's whole life, whichever is shorter
  const born = tok.pair_created_at || tok.created_at;
  const windowMin = born ? Math.max(5, Math.min(60, (ctx.now - born) / 60)) : 60;
  const pace = s.vol_h1 ? s.vol_h1 / (windowMin / 5) : 0;
  const ratio = pace > 0 ? s.vol_m5 / pace : 1;
  if (ratio >= 1.8) return [BUY, 0.9, `volume surging ${ratio.toFixed(1)}x the hourly pace (${usd(s.vol_m5)} in 5m)`];
  if (ratio >= 1.1) return [BUY, 0.6, `volume rising ${ratio.toFixed(1)}x (${usd(s.vol_m5)} in 5m)`];
  if (ratio >= 0.6) return [HOLD, 0.3, `volume steady (${ratio.toFixed(1)}x)`];
  return [AVOID, 0.5, `volume fading (${ratio.toFixed(1)}x)`];
});

agent("buy_pressure", "Buy Pressure Analyst", "momentum", "Buyers versus sellers in the last 5 minutes.", false, (tok) => {
  const s = tok.snap;
  if (!s) return [ABSTAIN, 0, "no market data yet"];
  const r = s.buys_m5 / (s.sells_m5 + 1);
  if (s.buys_m5 >= 25 && r >= 1.4) {
    return [BUY, 0.5 + 0.5 * ramp(r, 1.4, 3), `${s.buys_m5} buys vs ${s.sells_m5} sells (${r.toFixed(1)}x)`];
  }
  if (r >= 1.0) return [HOLD, 0.3, `balanced flow ${s.buys_m5}/${s.sells_m5}`];
  if (r < 0.8) return [AVOID, 0.6, `sellers dominate ${s.buys_m5}/${s.sells_m5}`];
  return [HOLD, 0.2, `slightly more sellers ${s.buys_m5}/${s.sells_m5}`];
});

agent("velocity", "Transaction Velocity Tracker", "momentum", "Trade count per 5 minutes and whether it is rising.", false, (tok, ctx) => {
  const s = tok.snap;
  if (!s) return [ABSTAIN, 0, "no market data yet"];
  const n = s.buys_m5 + s.sells_m5;
  const prev = tok.snapshotAgo(60, ctx.now);
  let trend = "", slowing = false;
  if (prev) {
    const pn = prev.buys_m5 + prev.sells_m5;
    if (pn > 0) {
      slowing = n < pn * 0.7;
      trend = ` (${slowing ? "slowing" : n > pn ? "up" : "steady"} from ${pn})`;
    }
  }
  if (n < 15) return [AVOID, 0.5, `only ${n} trades in 5m: dead`];
  if (n < 40) return [HOLD, 0.2, `${n} trades in 5m${trend}`];
  if (slowing) return [HOLD, 0.3, `${n} trades in 5m${trend}`];
  return [BUY, 0.5 + 0.5 * ramp(n, 40, 250), `${n} trades in 5m${trend}`];
});

agent("accel", "Trend Acceleration Engine", "momentum", "Is the market-cap slope steepening (second derivative)?", false, (tok, ctx) => {
  const pts = tok.snapshots.filter((s) => ctx.now - s.t <= 240 && s.mcap_usd > 0);
  if (pts.length < 4 || pts[pts.length - 1].t - pts[0].t < 60) return [ABSTAIN, 0, "need ~1 min of chart history"];
  const mid = Math.floor(pts.length / 2);
  const slope = (seg) => {
    const a = seg[0], b = seg[seg.length - 1];
    if (seg.length < 2 || a.t === b.t) return 0;
    return (Math.log(b.mcap_usd) - Math.log(a.mcap_usd)) / ((b.t - a.t) / 60);
  };
  const a = slope(pts.slice(0, mid + 1)), b = slope(pts.slice(mid));
  if (b > 0 && b >= a) {
    return [BUY, 0.5 + 0.5 * ramp(b, 0, 0.15), `trend accelerating (${signed(b * 100, 1)}%/min vs ${signed(a * 100, 1)}%/min)`];
  }
  if (b > 0) return [HOLD, 0.3, `still rising but slowing (${signed(b * 100, 1)}%/min)`];
  return [AVOID, 0.5, `trend rolling over (${signed(b * 100, 1)}%/min)`];
});

agent("holder_growth", "Holder Growth Monitor", "momentum", "How fast new wallets are joining.", false, (tok, ctx) => {
  if (tok.trades.length) {
    const buyers = new Set(tok.trades.filter((t) => t.is_buy && ctx.now - t.t <= 300).map((t) => t.trader)).size;
    if (buyers >= 30) return [BUY, 0.5 + 0.5 * ramp(buyers, 30, 120), `${buyers} unique buyers in 5m`];
    if (buyers >= 10) return [HOLD, 0.3, `${buyers} unique buyers in 5m`];
    return [AVOID, 0.4, `only ${buyers} unique buyers in 5m`];
  }
  const h = tok.holder_history;
  if (!h.length) return [ABSTAIN, 0, "waiting for holder count"];
  const holders = h[h.length - 1][1];
  if (h.length >= 2 && h[h.length - 1][0] - h[0][0] >= 60) {
    const rate = (h[h.length - 1][1] - h[0][1]) / ((h[h.length - 1][0] - h[0][0]) / 60);
    if (rate >= 3) return [BUY, 0.5 + 0.5 * ramp(rate, 3, 20), `${holders} holders, +${rate.toFixed(0)}/min`];
    if (rate < 0) return [AVOID, 0.5, `holders shrinking (${rate.toFixed(0)}/min)`];
    return [HOLD, 0.3, `${holders} holders, +${rate.toFixed(1)}/min`];
  }
  const perMin = holders / Math.max(1, tok.ageMin(ctx.now));
  if (holders >= 100 && perMin >= 2) {
    return [BUY, 0.5 + 0.5 * ramp(perMin, 2, 15), `${holders} holders (${perMin.toFixed(0)}/min since launch)`];
  }
  if (holders < 40) return [HOLD, 0.2, `only ${holders} holders`];
  return [HOLD, 0.3, `${holders} holders`];
});

agent("whales", "Whale Flow Tracker", "momentum", "Organic retail flow versus one whale or micro-trade bots.", false, (tok, ctx) => {
  const s = tok.snap;
  if (!s) return [ABSTAIN, 0, "no market data yet"];
  const recent = tok.trades.filter((t) => t.is_buy && ctx.now - t.t <= 300);
  if (recent.length >= 10) {
    const total = sum(recent.map((t) => t.sol));
    const top = Math.max(...recent.map((t) => t.sol));
    if (total > 0 && top / total > 0.4) {
      return [HOLD, 0.3, `one buy is ${(top / total * 100).toFixed(0)}% of 5m inflow: whale-driven`];
    }
  }
  const n = s.buys_m5 + s.sells_m5;
  if (n === 0) return [ABSTAIN, 0, "no trades"];
  const avg = s.vol_m5 / n;
  if (avg < 15) return [AVOID, 0.5, `avg trade ${usd(avg)}: bot wash trading`];
  if (avg > 1500) return [HOLD, 0.3, `avg trade ${usd(avg)}: a few whales`];
  return [BUY, 0.4 + 0.4 * (1 - Math.abs(Math.log10(avg) - 2) / 1.2), `organic flow, avg trade ${usd(avg)}`];
});

// ---- market structure

agent("curve", "Bonding Curve Analyst", "structure", "Where the coin sits on pump.fun's bonding curve.", false, (tok, ctx) => {
  const s = tok.snap;
  if (tok.migrated) {
    if (s && s.liquidity_usd && s.liquidity_usd >= 20000 && tok.ageMin(ctx.now) < 360) {
      return [BUY, 0.5, `graduated to PumpSwap with ${usd(s.liquidity_usd)} liquidity`];
    }
    return [HOLD, 0.3, "graduated; past the curve catalyst"];
  }
  if (!s || ctx.sol_usd <= 0) return [ABSTAIN, 0, "need market cap and SOL price"];
  const p = curveProgressFromMcapSol(s.mcap_usd / ctx.sol_usd);
  const pc = (p * 100).toFixed(0);
  if (p < 0.1) return [HOLD, 0.2, `curve ${pc}%: very early`];
  if (p < 0.25) return [HOLD, 0.3, `curve ${pc}%`];
  if (p <= 0.88) return [BUY, 0.5 + 0.5 * ramp(p, 0.25, 0.75), `curve ${pc}% filled: graduation in sight`];
  return [HOLD, 0.3, `curve ${pc}%: graduation sell-off risk`];
});

agent("mcap", "Market Cap Positioner", "structure", "Is the market cap in a range that can still multiply?", false, (tok) => {
  const s = tok.snap;
  if (!s) return [ABSTAIN, 0, "no market data yet"];
  const m = s.mcap_usd;
  if (!tok.migrated) {
    if (m < 6000) return [AVOID, 0.4, `${usd(m)} mcap: no traction`];
    if (m < 10000) return [HOLD, 0.2, `${usd(m)} mcap: unproven`];
    return [BUY, 0.5 + 0.4 * (1 - ramp(m, 40000, 90000)), `${usd(m)} mcap on the curve`];
  }
  if (m < 60000) return [AVOID, 0.4, `${usd(m)} after graduating: weak`];
  if (m <= 3_000_000) return [BUY, 0.5, `${usd(m)} mcap after graduating`];
  return [HOLD, 0.3, `${usd(m)} mcap: big already`];
});

agent("timing", "Launch Timing Agent", "structure", "Age of the coin: not too fresh to judge, not stale.", false, (tok, ctx) => {
  const age = tok.ageMin(ctx.now);
  if (tok.created_at == null && age < 2) return [ABSTAIN, 0, "launch time unknown"];
  if (age < 2) return [HOLD, 0.2, `${age.toFixed(1)} min old: too fresh to judge`];
  if (age <= 60) return [BUY, 0.7, `${age.toFixed(0)} min old: prime window`];
  if (age <= 240) return [BUY, 0.4, `${(age / 60).toFixed(1)} h old and still trading`];
  return [HOLD, 0.3, `${(age / 60).toFixed(1)} h old`];
});

agent("volatility", "Volatility Risk Agent", "structure", "Minute-to-minute swings: tradeable or a coin flip?", false, (tok, ctx) => {
  const pts = tok.snapshots.filter((s) => ctx.now - s.t <= 300 && s.mcap_usd > 0);
  if (pts.length < 4) return [ABSTAIN, 0, "need more chart history"];
  const rets = [];
  for (let i = 1; i < pts.length; i++) {
    const dt = Math.max(1, pts[i].t - pts[i - 1].t) / 60;
    rets.push(Math.log(pts[i].mcap_usd / pts[i - 1].mcap_usd) / Math.sqrt(dt));
  }
  const vol = pstdev(rets) * 100;
  if (vol > 25) return [HOLD, 0.2, `${vol.toFixed(0)}%/min swings: coin flip`];
  if (vol < 0.5) return [HOLD, 0.2, "barely moving"];
  return [BUY, 0.6, `${vol.toFixed(1)}%/min volatility: tradeable`];
});

agent("entry", "Entry Timing Agent", "structure", "Breakout to new highs, or a healthy pullback with a higher low.", false, (tok, ctx) => {
  const s = tok.snap;
  if (!s || tok.ath_mcap <= 0) return [ABSTAIN, 0, "no market data yet"];
  const dd = 1 - s.mcap_usd / tok.ath_mcap;
  const prev = tok.snapshotAgo(30, ctx.now);
  const rising = prev != null && s.mcap_usd > prev.mcap_usd;
  const d = (dd * 100).toFixed(0);
  if (dd < 0.03 && s.change_m5 > 0) return [BUY, 0.7, "breaking out to new highs"];
  if (dd >= 0.08 && dd <= 0.35 && rising) return [BUY, 0.6, `bouncing ${d}% below the high (higher low)`];
  if (dd <= 0.35) return [HOLD, 0.3, `${d}% below the high, no bounce yet`];
  if (dd <= 0.55) return [HOLD, 0.2, `${d}% below the high`];
  return [AVOID, 0.6, `${d}% below the high`];
});

// ---- narrative & social

agent("socials", "Social Presence Verifier", "narrative", "Twitter / Telegram / website attached to the coin.", false, (tok) => {
  if (!tok.metadata_loaded && !tok.has_profile) return [ABSTAIN, 0, "metadata loading"];
  const links = [tok.twitter, tok.telegram, tok.website].filter(Boolean);
  if (tok.metadata_unreachable && !links.length) return [ABSTAIN, 0, "metadata host blocks browser access"];
  if (links.length >= 2) return [BUY, 0.5 + 0.2 * (links.length - 2), `${links.length} social links`];
  if (tok.twitter) return [BUY, 0.4, "has an X/Twitter link"];
  if (links.length) return [HOLD, 0.3, "one social link"];
  return [AVOID, 0.5, "no socials at all"];
});

const SPAM_WORDS = ["airdrop", "presale", "giveaway", "guaranteed", "100x", "1000x", "free money", "send sol"];

agent("metadata", "Metadata Quality Inspector", "narrative", "Description, image and ticker quality; spam phrasing.", false, (tok) => {
  if (!tok.metadata_loaded) return [ABSTAIN, 0, "metadata loading"];
  if (tok.metadata_unreachable && tok.description.trim().length < 15) return [ABSTAIN, 0, "metadata host blocks browser access"];
  let text = `${tok.name} ${tok.symbol} ${tok.description}`.toLowerCase();
  text = text.replace(/\b(no|zero|without)\s+(presale|airdrop)s?/g, " "); // "no presale" is a selling point
  const spam = SPAM_WORDS.filter((w) => text.includes(w));
  if (spam.length) return [AVOID, 0.6, `spam phrasing: ${spam.join(", ")}`];
  if (!tok.symbol || tok.symbol.length > 12) return [HOLD, 0.2, "odd ticker"];
  if (tok.description.trim().length < 15) return [HOLD, 0.3, "no real description"];
  return [BUY, tok.image ? 0.5 : 0.3, "clean metadata and description"];
});

agent("narrative", "Narrative & Meta Detector", "narrative", "Is the coin riding the meta that is moving right now, and is it the leader?", false, (tok, ctx) => {
  let w = null, heat = -1;
  for (const word of tok.words()) {
    const h = ctx.hot_words.get(word);
    if (h != null && h > heat) { w = word; heat = h; }
  }
  if (w == null) return [HOLD, 0.25, "not part of a hot narrative"];
  const my = tok.snap ? tok.snap.mcap_usd : 0;
  const [n, best] = ctx.word_coins.get(w) || [1, my];
  if (my >= best) return [BUY, 0.5 + 0.5 * ramp(heat, 3, 20), `leads the hot '${w}' narrative (${n - 1} rivals)`];
  return [HOLD, 0.3, `follower in the '${w}' narrative`];
});

agent("copycat", "Copycat Detector", "narrative", "Is this a clone of a bigger coin with the same ticker?", false, (tok, ctx) => {
  if (!tok.symbol) return [ABSTAIN, 0, "no ticker"];
  const same = [...(ctx.symbol_index.get(tok.symbol.toLowerCase()) || [])]
    .filter((m) => m !== tok.mint && ctx.tokens.has(m)).map((m) => ctx.tokens.get(m));
  const my = tok.snap ? tok.snap.mcap_usd : 0;
  const bigger = same.filter((t) => t.snap && t.snap.mcap_usd > Math.max(my, 1) * 2);
  if (bigger.length) return [AVOID, 0.7, `copy of a bigger $${tok.symbol} (${same.length + 1} with this ticker)`];
  if (same.length >= 2) return [BUY, 0.6, `the original/leading $${tok.symbol} (${same.length} copies chasing it)`];
  return [BUY, 0.4, "unique ticker"];
});

agent("attention", "DexScreener Attention Agent", "narrative", "Paid profile, boosts or community takeover: someone is spending to market it.", false, (tok) => {
  if (tok.cto) return [BUY, 0.6, "community takeover on DexScreener"];
  if (tok.boosts > 0) return [BUY, 0.5 + 0.1 * Math.min(tok.boosts, 5), `${tok.boosts} DexScreener boosts`];
  if (tok.has_profile) return [BUY, 0.5, "paid DexScreener profile"];
  if (!tok.snapshots.length) return [ABSTAIN, 0, "not on DexScreener yet"];
  return [HOLD, 0.3, "no paid attention yet"];
});

if (AGENTS.length !== 25) throw new Error(`expected 25 agents, got ${AGENTS.length}`);
export const AGENTS_BY_ID = Object.fromEntries(AGENTS.map((a) => [a.id, a]));

export function vote(a, tok, ctx) {
  let stance, strength, reason;
  try {
    [stance, strength, reason] = a.fn(tok, ctx);
  } catch (e) { // one broken input must not take the council down
    [stance, strength, reason] = [ABSTAIN, 0, `error: ${e.message}`];
  }
  if (!Number.isFinite(strength)) strength = 0;
  return { agent: a.id, stance, strength: Math.round(clamp(strength, 0, 1) * 1000) / 1000, reason };
}

// ------------------------------------------------------------------ council

export function defaultRules() {
  return {
    min_buy_votes: 20,      // BUY votes needed out of 25; 25 = unanimous
    max_avoid_votes: 0,     // AVOID votes tolerated from non-safety agents; safety agents always veto
    window_minutes: 10,     // how long a signal stays actionable
    max_chase_pct: 15,      // the signal is void once price is this far above the entry
    cooldown_minutes: 30,   // no repeat signal on the same coin within this many minutes
  };
}

export function runCouncil(tok, ctx, rules) {
  const votes = AGENTS.map((a) => vote(a, tok, ctx));
  const counts = { BUY: 0, HOLD: 0, AVOID: 0, ABSTAIN: 0 };
  for (const v of votes) counts[v.stance]++;
  const vetoedBy = votes.filter((v) => v.stance === AVOID && AGENTS_BY_ID[v.agent].veto).map((v) => v.agent);
  const buyStrengths = votes.filter((v) => v.stance === BUY).map((v) => v.strength);
  const conviction = buyStrengths.length ? 100 * sum(buyStrengths) / buyStrengths.length : 0;
  const isSignal = counts.BUY >= rules.min_buy_votes && !vetoedBy.length && counts.AVOID <= rules.max_avoid_votes;
  tok.last_votes = votes;
  tok.buy_votes = counts.BUY;
  tok.avoid_votes = counts.AVOID;
  tok.vetoed = vetoedBy.length > 0;
  tok.conviction = conviction;
  return {
    mint: tok.mint, buy_votes: counts.BUY, hold_votes: counts.HOLD, avoid_votes: counts.AVOID,
    abstain_votes: counts.ABSTAIN, vetoed_by: vetoedBy, conviction: Math.round(conviction * 10) / 10,
    is_signal: isSignal, votes,
  };
}

// Leaderboard order: unvetoed first, then BUY minus AVOID votes, then conviction.
export function compareRank(a, b) {
  return (a.vetoed - b.vetoed) || ((b.buy_votes - b.avoid_votes) - (a.buy_votes - a.avoid_votes)) || (b.conviction - a.conviction);
}

// Per-minute volatility (fraction) over the last 5 minutes; 0.1 when unknown.
export function realisedVolatility(tok, now) {
  const pts = tok.snapshots.filter((s) => now - s.t <= 300 && s.mcap_usd > 0);
  if (pts.length < 4) return 0.1;
  const rets = [];
  for (let i = 1; i < pts.length; i++) {
    rets.push(Math.log(pts[i].mcap_usd / pts[i - 1].mcap_usd) / Math.sqrt(Math.max(1, pts[i].t - pts[i - 1].t) / 60));
  }
  return pstdev(rets);
}

const round1 = (x) => Math.round(x * 10) / 10;

// The actionable verdict shown to the user.
export function buildSignal(tok, verdict, rules, now) {
  const s = tok.snap;
  const stop = Math.min(0.3, Math.max(0.12, 2.5 * realisedVolatility(tok, now)));
  const reasons = verdict.votes.filter((v) => v.stance === BUY).sort((a, b) => b.strength - a.strength);
  const cautions = verdict.votes.filter((v) => v.stance === HOLD || v.stance === AVOID);
  return {
    id: `${tok.mint}:${Math.floor(now)}`,
    mint: tok.mint, name: tok.name, symbol: tok.symbol, image: tok.image,
    created: now, expires: now + rules.window_minutes * 60, window_minutes: rules.window_minutes,
    headline: `BUY NOW: ${tok.name || tok.symbol} ($${tok.symbol}) within the next ${rules.window_minutes} minutes`,
    entry_price_usd: s.price_usd, entry_mcap_usd: s.mcap_usd,
    max_entry_mcap_usd: s.mcap_usd * (1 + rules.max_chase_pct / 100),
    stop_loss_pct: round1(-stop * 100), take_profit_1_pct: round1(stop * 1.5 * 100), take_profit_2_pct: round1(stop * 3 * 100),
    agree: verdict.buy_votes, total_agents: AGENTS.length, conviction: verdict.conviction,
    reasons: reasons.map((v) => ({ agent: AGENTS_BY_ID[v.agent].name, reason: v.reason, strength: v.strength })),
    cautions: cautions.map((v) => ({ agent: AGENTS_BY_ID[v.agent].name, stance: v.stance, reason: v.reason })),
    links: {
      pumpfun: `https://pump.fun/coin/${tok.mint}`,
      dexscreener: `https://dexscreener.com/solana/${tok.mint}`,
      rugcheck: `https://rugcheck.xyz/tokens/${tok.mint}`,
    },
    outcome: null,
  };
}

// After the window closes: what actually happened to the price.
export function scoreOutcome(sig, tok, now) {
  if (now < sig.expires || !tok) return null;
  const pts = tok.snapshots.filter((x) => x.t >= sig.created && x.t <= sig.expires + 30 && x.mcap_usd > 0);
  if (!pts.length) return { status: "no_data" };
  const entry = sig.entry_mcap_usd;
  const peak = Math.max(...pts.map((x) => x.mcap_usd));
  const low = Math.min(...pts.map((x) => x.mcap_usd));
  const end = pts[pts.length - 1].mcap_usd;
  const hitTp = peak >= entry * (1 + sig.take_profit_1_pct / 100);
  const hitSl = low <= entry * (1 + sig.stop_loss_pct / 100);
  // If both happened we cannot know the order from snapshots; count the stop first (conservative).
  const status = hitSl ? "stopped" : hitTp ? "win" : end > entry ? "up" : "down";
  return {
    status, end_pct: round1((end / entry - 1) * 100), peak_pct: round1((peak / entry - 1) * 100), low_pct: round1((low / entry - 1) * 100),
  };
}

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
      dex_id: "", pair_address: "", pool_address: "", pair_created_at: null, boosts: 0, has_profile: false, cto: false, migrated: false,
      snapshots: [], trades: [], rug: null, holder_history: [], ath_mcap: 0, ath_t: 0,
      last_votes: [], buy_votes: 0, avoid_votes: 0, vetoed: false, conviction: 0, signaled_at: null,
      last_dex: 0, dex_misses: 0, rug_attempt: 0,
      chain: [], chain_dead: false, // on-chain bonding-curve series (flow.js) and "never got going" flag
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

// ------------------------------------------------------------------ on-chain flow
// High-resolution on-chain flow for bonding-curve coins.
//
// DexScreener aggregates (5-minute buckets, refreshed every few seconds and only after a coin is indexed) are too
// coarse and too late for sniper entries. Every ~2 s this module reads the exact bonding-curve account of every
// young coin, every promising candidate and every held position (100 accounts per RPC call), and keeps a short
// series per coin. From the curve's own reserves we know, exactly:
//   - the price at each poll (virtual SOL / virtual tokens),
//   - the net SOL that entered or left the curve between polls (real SOL reserves),
//   - the launch price (30 SOL / 1.073B tokens), so even a 40-second-old coin has an exact "change since launch".

export const LAUNCH_PRICE = 30 / 1_073_000_000; // SOL per token at creation
const MAX_POINTS = 400; // ~13 min at 2 s

export function pushPoint(tok, p) {
  const s = tok.chain || (tok.chain = []);
  if (s.length && p.t <= s[s.length - 1].t) return;
  s.push(p);
  if (s.length > MAX_POINTS) s.splice(0, s.length - MAX_POINTS);
}

const priceOf = (p) => (p.vSol / 1e9) / (p.vTok / 1e6);

// The newest point at least `sec` seconds old (or null).
function pointAgo(s, now, sec) {
  for (let i = s.length - 1; i >= 0; i--) if (now - s[i].t >= sec) return s[i];
  return null;
}

// Everything the agents need from the on-chain series, or null when there is no fresh series.
export function chainView(tok, now, maxAge = 20) {
  const s = tok.chain;
  if (!s || !s.length) return null;
  const last = s[s.length - 1];
  if (now - last.t > maxAge) return null; // stale: polling stopped or RPC trouble
  const price = priceOf(last);
  const born = tok.created_at;
  const change = (sec) => {
    const p = pointAgo(s, last.t, sec);
    if (p) return price / priceOf(p) - 1;
    if (born != null && last.t - born <= sec + 3) return price / LAUNCH_PRICE - 1; // younger than the window: since launch
    return null;
  };
  const inflow = (sec) => {
    const p = pointAgo(s, last.t, sec);
    if (p) return (last.realSol - p.realSol) / 1e9;
    if (born != null && last.t - born <= sec + 3) return last.realSol / 1e9; // since launch
    return null;
  };
  // Gross flow and activity over a window, from poll-to-poll deltas (netting inside one 2 s gap is unavoidable).
  const flows = (sec) => {
    let gin = 0, gout = 0, active = 0, pairs = 0, maxIn = 0, maxOut = 0, maxOutFrac = 0;
    for (let i = s.length - 1; i > 0 && last.t - s[i - 1].t <= sec; i--) {
      const d = (s[i].realSol - s[i - 1].realSol) / 1e9;
      pairs++;
      if (d > 0) { gin += d; active++; maxIn = Math.max(maxIn, d); }
      if (d < 0) {
        gout -= d; active++; maxOut = Math.max(maxOut, -d);
        if (s[i - 1].realSol > 0) maxOutFrac = Math.max(maxOutFrac, -d / (s[i - 1].realSol / 1e9));
      }
    }
    return { gin, gout, active, pairs, maxIn, maxOut, maxOutFrac };
  };
  let high = price, highT = last.t;
  for (let i = s.length - 1; i >= 0 && last.t - s[i].t <= 300; i--) {
    const p = priceOf(s[i]);
    if (p > high) { high = p; highT = s[i].t; }
  }
  const span = last.t - s[0].t;
  return {
    t: last.t, span, points: s.length, price, realSol: last.realSol / 1e9,
    progress: Math.max(0, Math.min(1, (last.vSol / 1e9 - 30) / 85)), complete: last.complete,
    change, inflow, flows, high5m: high, highAgo: last.t - highT, drawdown: 1 - price / high,
  };
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

agent("curve", "Bonding Curve Analyst", "structure", "Stage on pump.fun's bonding curve (SOL already in it), weighted by measured odds.", false, (tok, ctx) => {
  const s = tok.snap;
  if (tok.migrated) {
    if (s && s.liquidity_usd && s.liquidity_usd >= 20000 && tok.ageMin(ctx.now) < 360) {
      return [BUY, 0.5, `graduated to PumpSwap with ${usd(s.liquidity_usd)} liquidity`];
    }
    return [HOLD, 0.3, "graduated; past the curve catalyst"];
  }
  if (!s || ctx.sol_usd <= 0) return [ABSTAIN, 0, "need market cap and SOL price"];
  return curveStageVote(curveProgressFromMcapSol(s.mcap_usd / ctx.sol_usd) * 85);
});

agent("mcap", "Market Cap Positioner", "structure", "Is the market cap in a range that can still multiply? Judged in SOL, so it does not drift with the SOL price.", false, (tok, ctx) => {
  const s = tok.snap;
  if (!s) return [ABSTAIN, 0, "no market data yet"];
  const m = s.mcap_usd;
  if (!tok.migrated) {
    if (!(ctx.sol_usd > 0)) return [ABSTAIN, 0, "need the SOL price"];
    const mcapSol = m / ctx.sol_usd;
    const sol = curveProgressFromMcapSol(mcapSol) * 85;
    if (sol < TUNING.stageMinSol) return [AVOID, 0.4, `${mcapSol.toFixed(0)} SOL mcap (${usd(m)}): no traction yet`];
    return [BUY, 0.5 + 0.3 * (1 - ramp(mcapSol, 150, 400)), `${mcapSol.toFixed(0)} SOL mcap (${usd(m)}) on the curve`];
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

agent("socials", "Social & Marketing Verifier", "narrative", "Twitter / Telegram / website, and paid DexScreener marketing (profile, boosts, takeover).", false, (tok) => {
  if (!tok.metadata_loaded && !tok.has_profile) return [ABSTAIN, 0, "metadata loading"];
  const links = [tok.twitter, tok.telegram, tok.website].filter(Boolean);
  // someone paying to market it (DexScreener profile, boosts, community takeover) counts like a social link
  if (tok.cto) return [BUY, 0.6, `community takeover on DexScreener${links.length ? ` + ${links.length} social link(s)` : ""}`];
  if (tok.boosts > 0 || tok.has_profile) {
    return [BUY, Math.min(0.8, 0.5 + 0.05 * Math.min(tok.boosts, 4) + 0.05 * links.length),
      `${tok.boosts ? `${tok.boosts} DexScreener boosts` : "paid DexScreener profile"}${links.length ? ` + ${links.length} social link(s)` : ""}`];
  }
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

agent("cost", "Cost Hurdle Agent", "structure",
  "Round-trip costs (pump.fun fees both ways, network fees, price impact) at your trade size versus how far the coin actually moves.", false, (tok, ctx) => {
    const { hurdle, size } = roundTripCost(tok, ctx);
    const sigma1 = ctx.view ? chainVolatility(tok, ctx.now) : (tok.snapshots.length >= 4 ? realisedVolatility(tok, ctx.now) : null);
    if (hurdle > TUNING.maxHurdle) {
      return [AVOID, 0.7, `costs are ${(hurdle * 100).toFixed(1)}% of a ${size.toFixed(3)} SOL round trip: raise the size or lower the priority fee`];
    }
    if (sigma1 == null) return [ABSTAIN, 0, `costs ${(hurdle * 100).toFixed(1)}% per round trip; need price history`];
    const sigma10 = sigma1 * Math.sqrt(10);
    if (sigma10 < TUNING.minMoveToCost * hurdle) {
      return [HOLD, 0.3, `moves ~${(sigma10 * 100).toFixed(0)}% per 10 min: too little to beat ${(hurdle * 100).toFixed(1)}% costs`];
    }
    return [BUY, 0.4 + 0.5 * ramp(sigma10 / hurdle, TUNING.minMoveToCost, 8), `moves ~${(sigma10 * 100).toFixed(0)}% per 10 min vs ${(hurdle * 100).toFixed(1)}% round-trip costs`];
  });

if (AGENTS.length !== 25) throw new Error(`expected 25 agents, got ${AGENTS.length}`);
export const AGENTS_BY_ID = Object.fromEntries(AGENTS.map((a) => [a.id, a]));

export function vote(a, tok, ctx) {
  let stance, strength, reason;
  try {
    const rule = ctx.view ? CHAIN_RULES[a.id] : null;
    const out = rule ? rule(tok, ctx, ctx.view) : null;
    [stance, strength, reason] = out || a.fn(tok, ctx);
  } catch (e) { // one broken input must not take the council down
    [stance, strength, reason] = [ABSTAIN, 0, `error: ${e.message}`];
  }
  if (!Number.isFinite(strength)) strength = 0;
  return { agent: a.id, stance, strength: Math.round(clamp(strength, 0, 1) * 1000) / 1000, reason };
}

// ------------------------------------------------------------------ on-chain rules
// When a coin has a fresh on-chain series (flow.js), these rules replace the DexScreener-based logic of the
// matching agent: exact prices every 2 s instead of 5-minute buckets that arrive late. Thresholds live in TUNING;
// they were checked against recorded live launches (see README "How the entry rules were tuned").

export const TUNING = {
  stageMinSol: 2,        // < 2 SOL in the curve: only 15% of recorded coins rose 20% before falling 15%
  stageBestLo: 5,        // 2-5 SOL: 24%; from 5 SOL on: 31-43%
  dumpFrac: 0.15,        // one 2 s window removing this share of the curve's SOL = a dump
  crash30: -0.25,        // -25% in 30 s
  drainDD: 0.45,         // 45% below the 5-minute on-chain high
  verticalChg10: 0.12,   // +12% in 10 s: a vertical candle, wait
  verticalChg30: 0.30,   // +30% in 30 s
  pullbackMin: 0.05, pullbackMax: 0.30, // healthy pullback from the 5-minute high
  minInflow60: 0.3,      // SOL net in over 60 s to count as real demand
  strongInflow60: 1.0,
  minActive: 0.35,       // share of 2 s windows with trading
  maxHurdle: 0.12,       // round-trip cost above this = no trade
  minMoveToCost: 2.0,    // 10-minute typical move must be at least this many times the costs
};

// Round-trip cost of one trade as a fraction of its size: pump.fun fees both ways (1.25% each on the curve; PumpSwap
// tiers are at most the same), two network fees, and the price impact of our own buy and sell on the curve.
export function roundTripCost(tok, ctx) {
  const trade = ctx.trade || { size_sol: 0.03, network_fee_sol: 0.000505 };
  const size = Math.max(trade.size_sol, 1e-6);
  let vsol = null;
  if (ctx.view) vsol = 30 + ctx.view.progress * 85;
  else if (!tok.migrated && tok.snap && ctx.sol_usd > 0) vsol = 30 + curveProgressFromMcapSol(tok.snap.mcap_usd / ctx.sol_usd) * 85;
  const impact = vsol ? 2 * size / vsol : 0.004;
  return { hurdle: 0.025 + 2 * trade.network_fee_sol / size + impact, size };
}

// Per-minute volatility from the 2 s on-chain series (fraction); null when too short.
export function chainVolatility(tok, now) {
  const s = (tok.chain || []).filter((p) => now - p.t <= 300);
  if (s.length < 8) return null;
  const rets = [];
  for (let i = 1; i < s.length; i++) {
    const a = (s[i - 1].vSol / s[i - 1].vTok), b = (s[i].vSol / s[i].vTok);
    rets.push(Math.log(b / a) / Math.sqrt(Math.max(1, s[i].t - s[i - 1].t) / 60));
  }
  return pstdev(rets);
}

const fmtPct = (x) => `${x >= 0 ? "+" : ""}${(x * 100).toFixed(0)}%`;
const fmtSol = (x) => `${x >= 0 ? "" : "-"}${Math.abs(x).toFixed(2)} SOL`;

// Curve stage from the SOL in the bonding curve, as measured on 555 recorded coins (README: "How the entry rules were
// tuned"). Odds of +20% before -15% within 10 minutes: 15% at 0.5-2 SOL, 24% at 2-5, 31-33% from 5 to 40, 43% at
// 40-85 (n=30). Only "the very first stage is worst" held up in both halves of the data, so that is all this encodes.
export function curveStageVote(sol) {
  if (sol < TUNING.stageMinSol) return [AVOID, 0.5, `${sol.toFixed(1)} SOL in the curve: too early (worst stage in recorded launches)`];
  if (sol < TUNING.stageBestLo) return [HOLD, 0.3, `${sol.toFixed(1)} SOL in the curve: still early`];
  if (sol < 85) return [BUY, 0.6, `${sol.toFixed(1)} SOL in the curve: past the early stage`];
  return [HOLD, 0.2, "curve full: graduation"];
}

export const CHAIN_RULES = {
  rug_pattern: (tok, ctx, v) => {
    const f = v.flows(60);
    if (f.maxOutFrac >= TUNING.dumpFrac && f.maxOut >= 0.3) {
      return [AVOID, 1, `${fmtSol(f.maxOut)} pulled from the curve in one 2-second window (${(f.maxOutFrac * 100).toFixed(0)}% of it)`];
    }
    const c30 = v.change(30);
    if (c30 != null && c30 <= TUNING.crash30) return [AVOID, 1, `crashed ${fmtPct(c30)} in 30 s (on-chain)`];
    return [BUY, 0.5, "no dump in the on-chain flow"];
  },
  exit_liq: (tok, ctx, v) => {
    if (v.drawdown >= TUNING.drainDD) return [AVOID, 0.9, `${(v.drawdown * 100).toFixed(0)}% below its 5-minute high: buyers already trapped`];
    const f = v.flows(120);
    if (f.gout > 1 && f.gout > 2 * f.gin) return [AVOID, 0.8, `sellers draining the curve: ${fmtSol(f.gout)} out vs ${fmtSol(f.gin)} in (2 min)`];
    return [BUY, 0.5, `exits clear: ${v.realSol.toFixed(1)} SOL in the curve`];
  },
  // Recorded data: requiring strong up-moves selects chase entries (+3..40%/min alone: -7.3% per trade vs -6.5%
  // for any moment). These agents now only demand that the coin is not deteriorating.
  momentum: (tok, ctx, v) => {
    const c1 = v.change(60), c5 = v.change(300);
    if (c1 == null) return null;
    if (c5 != null && c5 > 2.5) return [AVOID, 0.5, `parabolic ${fmtPct(c5)} in 5 min: late`];
    if (c1 < -0.10) return [AVOID, 0.6, `falling ${fmtPct(c1)} in the last minute`];
    if (c1 > 0.40) return [HOLD, 0.3, `${fmtPct(c1)} in one minute: chase risk`];
    if (c1 >= -0.05) return [BUY, 0.5, `holding up: ${fmtPct(c1)} in the last minute (on-chain)`];
    return [HOLD, 0.2, `slipping ${fmtPct(c1)} in the last minute`];
  },
  volume: (tok, ctx, v) => {
    const in60 = v.inflow(60);
    if (in60 == null) return null;
    if (in60 <= -0.3) return [AVOID, 0.6, `${fmtSol(in60)} net left the curve in the last minute`];
    if (in60 >= 0) return [BUY, 0.5 + 0.3 * ramp(in60, 0, TUNING.strongInflow60), `${fmtSol(in60)} net inflow in the last minute`];
    return [HOLD, 0.3, `slight outflow: ${fmtSol(in60)} in the last minute`];
  },
  buy_pressure: (tok, ctx, v) => {
    const f = v.flows(60);
    if (f.pairs < 5) return null;
    const r = f.gin / (f.gout + 0.05);
    if (r < 0.8 && f.gout >= 0.3) return [AVOID, 0.6, `sellers dominate: ${fmtSol(f.gout)} out vs ${fmtSol(f.gin)} in (60 s)`];
    if (r >= 1.0) return [BUY, 0.5 + 0.3 * ramp(r, 1, 4), `${fmtSol(f.gin)} bought vs ${fmtSol(f.gout)} sold in 60 s (on-chain)`];
    return [HOLD, 0.3, `balanced flow: ${fmtSol(f.gin)} in / ${fmtSol(f.gout)} out (60 s)`];
  },
  velocity: (tok, ctx, v) => {
    const f = v.flows(60);
    if (f.pairs < 8) return null;
    const a = f.active / f.pairs;
    if (a >= 0.6) return [BUY, 0.5 + 0.5 * ramp(a, 0.6, 0.95), `trading in ${(a * 100).toFixed(0)}% of 2-second windows`];
    // Recorded data: requiring activity filtered out good quiet entries in the best curve stage, so quiet is HOLD, not AVOID.
    return [HOLD, 0.3, `trading in ${(a * 100).toFixed(0)}% of 2-second windows`];
  },
  accel: (tok, ctx, v) => {
    const c30 = v.change(30), c90 = v.change(90);
    if (c30 == null || c90 == null || v.span < 60) return null;
    const recent = c30 / 0.5, before = ((1 + c90) / (1 + c30) - 1) / 1.0; // per-minute rates: last 30 s vs the minute before
    if (recent < -0.05) return [AVOID, 0.5, `rolling over: ${fmtPct(recent)}/min`];
    return [BUY, 0.4 + 0.3 * ramp(recent - before, 0, 0.2), `trend intact: ${fmtPct(recent)}/min now vs ${fmtPct(before)}/min before`];
  },
  whales: (tok, ctx, v) => {
    const f = v.flows(120);
    if (f.gin < 0.5) return null;
    const share = f.maxIn / f.gin;
    if (share > 0.5) return [HOLD, 0.3, `one buy-sized burst is ${(share * 100).toFixed(0)}% of 2-minute inflow: whale-driven`];
    return [BUY, 0.4 + 0.4 * (1 - share), `spread-out buying: largest burst ${(share * 100).toFixed(0)}% of inflow`];
  },
  curve: (tok, ctx, v) => curveStageVote(v.realSol),
  mcap: (tok, ctx, v) => {
    const mcapSol = v.price * 1e9;
    const usdTxt = ctx.sol_usd > 0 ? ` (${usd(mcapSol * ctx.sol_usd)})` : "";
    if (v.realSol < TUNING.stageMinSol) return [AVOID, 0.4, `${mcapSol.toFixed(0)} SOL mcap${usdTxt}: no traction yet`];
    return [BUY, 0.5 + 0.3 * (1 - ramp(mcapSol, 150, 400)), `${mcapSol.toFixed(0)} SOL mcap${usdTxt} on the curve`];
  },
  volatility: (tok, ctx, v) => {
    const sig = chainVolatility(tok, ctx.now);
    if (sig == null) return null;
    const vol = sig * 100;
    if (vol > 25) return [HOLD, 0.2, `${vol.toFixed(0)}%/min swings: coin flip`];
    if (vol < 0.5) return [HOLD, 0.2, "barely moving"];
    return [BUY, 0.6, `${vol.toFixed(1)}%/min volatility: tradeable`];
  },
  // Entry timing as a chase filter. Recorded data: the earlier "buy the pullback bounce / breakout on inflow" rule
  // did worse than buying at any moment (-8.8% vs -6.5% per trade), so it no longer adds buy pressure; it only
  // keeps the council from buying a vertical candle or a coin far below its recent high.
  entry: (tok, ctx, v) => {
    const c10 = v.change(10), c30 = v.change(30);
    if (c10 == null || c30 == null) return null;
    if (c10 >= TUNING.verticalChg10 || c30 >= TUNING.verticalChg30) {
      return [HOLD, 0.3, `vertical candle (${fmtPct(c10)} in 10 s, ${fmtPct(c30)} in 30 s): wait for it to settle`];
    }
    if (v.drawdown > TUNING.pullbackMax) return [HOLD, 0.2, `${(v.drawdown * 100).toFixed(0)}% below the 5-minute high: no base yet`];
    return [BUY, 0.5, `no chase: ${fmtPct(c30)} in 30 s, ${(v.drawdown * 100).toFixed(0)}% off the high`];
  },
};

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
  ctx.view = tok.migrated ? null : chainView(tok, ctx.now);
  const votes = AGENTS.map((a) => vote(a, tok, ctx));
  ctx.view = null;
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
export function buildSignal(tok, verdict, rules, now, ctx = null) {
  const view = !tok.migrated ? chainView(tok, now) : null;
  // Snapshot for the plan: DexScreener if present, else the exact on-chain price.
  const s = tok.snap && (!view || tok.snap.t >= view.t - 10) ? tok.snap
    : { price_usd: view.price * (ctx && ctx.sol_usd || 0), mcap_usd: view.price * 1e9 * (ctx && ctx.sol_usd || 0) };
  // Plan from recorded launches (README "Why the account was losing"): pump.fun coins gap down between checks, so
  // short exposure beats wide stops. Exit within 60 s; stop -10%; half at +20%, rest at +40%. Take-profit 1 must
  // still clear the round-trip costs at least twice over, or even a "win" loses money.
  const stop = 0.10;
  const hurdle = ctx ? roundTripCost(tok, { ...ctx, view }).hurdle : 0.06;
  const tp1 = Math.max(0.20, hurdle * 2);
  const reasons = verdict.votes.filter((v) => v.stance === BUY).sort((a, b) => b.strength - a.strength);
  const cautions = verdict.votes.filter((v) => v.stance === HOLD || v.stance === AVOID);
  return {
    id: `${tok.mint}:${Math.floor(now)}`,
    mint: tok.mint, name: tok.name, symbol: tok.symbol, image: tok.image,
    created: now, expires: now + rules.window_minutes * 60, window_minutes: rules.window_minutes,
    headline: `BUY NOW: ${tok.name || tok.symbol} ($${tok.symbol}) within the next ${rules.window_minutes} minutes`,
    entry_price_usd: s.price_usd, entry_mcap_usd: s.mcap_usd,
    max_entry_mcap_usd: s.mcap_usd * (1 + rules.max_chase_pct / 100),
    stop_loss_pct: round1(-stop * 100), take_profit_1_pct: round1(tp1 * 100), take_profit_2_pct: round1(Math.max(0.40, tp1 * 2) * 100),
    max_hold_s: 60,
    cost_hurdle_pct: round1(hurdle * 100),
    entry_price_sol: view ? view.price : null, // exact on-chain entry; outcomes are scored on the chain when present
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
// Market cap path of a coin, from the exact on-chain series when the signal recorded an on-chain entry price.
function mcapPath(sig, tok) {
  if (sig.entry_price_sol && tok.chain && tok.chain.length) {
    const scale = sig.entry_mcap_usd / sig.entry_price_sol;
    return tok.chain.map((p) => ({ t: p.t, mcap_usd: (p.vSol / 1e9) / (p.vTok / 1e6) * scale }));
  }
  return tok.snapshots;
}

export function currentMcap(sig, tok) {
  const path = mcapPath(sig, tok);
  return path.length ? path[path.length - 1].mcap_usd : null;
}

export function scoreOutcome(sig, tok, now) {
  if (now < sig.expires || !tok) return null;
  const pts = mcapPath(sig, tok).filter((x) => x.t >= sig.created && x.t <= sig.expires + 30 && x.mcap_usd > 0);
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

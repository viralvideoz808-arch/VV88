// Minute markets: the model, the strike ladder, and the 25-agent council for Polymarket's crypto "Up or Down" windows.
//
// What settles these markets (verified on 2,065 resolved windows):
//   * Each window ("Bitcoin Up or Down - 9:40-9:45AM ET") resolves Up when Chainlink's BTC/USD 60-second TWAP stream
//     reads at least as high at the end of the window as it did at the start. Ties go to Up.
//   * The value at a boundary is one number. The previous window's final price IS the next window's price to beat
//     (2,001 of 2,037 consecutive pairs identical), and every window ending at the same moment — 5-minute, 15-minute,
//     4-hour — settles on the very same final number (447 of 447 identical).
//   * Chainlink's value at a boundary tracks the average Coinbase price over the minute before it to ~1-4 basis points.
//
// That gives the council two genuine handles on these markets:
//   1. Fair value from physics. With the start price, the live spot price, the time left and realised volatility, the
//      chance the final 60s average ends above the start is a normal-distribution calculation. The council buys a
//      side only when the order book sells it well below that probability, after Polymarket's taker fee.
//   2. The strike ladder. Windows that share an end time are bets on one number at different strikes, so the cheaper
//      strike's Up must be worth at least the dearer strike's Up. When prices break that ordering by more than the
//      fees, buying Up on the low strike and Down on the high strike pays at least $1 whatever happens (and $2 if the
//      final lands between the strikes). That is arbitrage by arithmetic, not a forecast.
//
// Everything here is a pure function of its inputs so it can be tested offline.

import { ABSTAIN, AVOID, BUY, HOLD, feePerShare, walkAsks } from "./core.js";

export const ASSETS = {
  btc: { name: "Bitcoin", cb: "BTC-USD", alt: "BTCUSDT" },
  eth: { name: "Ethereum", cb: "ETH-USD", alt: "ETHUSDT" },
  sol: { name: "Solana", cb: "SOL-USD", alt: "SOLUSDT" },
  xrp: { name: "XRP", cb: "XRP-USD", alt: "XRPUSDT" },
  doge: { name: "Dogecoin", cb: "DOGE-USD", alt: "DOGEUSDT" },
  bnb: { name: "BNB", cb: "BNB-USD", alt: "BNBUSDT" },
  hype: { name: "Hyperliquid", cb: "HYPE-USD", alt: "HYPEUSDT" },
  zec: { name: "Zcash", cb: "ZEC-USD", alt: "ZECUSDT" },
};
const NAME_TO_ASSET = Object.fromEntries(Object.entries(ASSETS).map(([k, v]) => [v.name.toLowerCase(), k]));
NAME_TO_ASSET.zcash = "zec"; NAME_TO_ASSET.hyperliquid = "hype";

export const TWAP_SEC = 60;            // the oracle stream averages the last 60 seconds
export const STRIKE_SD_BP = { recorded: 1.5, oracle: 2.0, candle: 4.0 }; // measured strike uncertainty by source
export const MODEL_FLOOR_BP = 2.0;     // irreducible oracle-vs-exchange noise (fitted on resolved windows)

const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
const bp = (x) => x * 1e4;
const fmtBp = (x) => `${x >= 0 ? "+" : ""}${x.toFixed(1)}bp`;
const c = (p) => p == null ? "–" : `${(p * 100).toFixed(1)}¢`;
const secs = (s) => s >= 90 ? `${Math.floor(s / 60)}m${String(Math.round(s % 60)).padStart(2, "0")}s` : `${Math.max(0, Math.round(s))}s`;

// Standard normal CDF (Abramowitz-Stegun 7.1.26 on erf; max error ~1.5e-7).
export function Phi(z) {
  if (!Number.isFinite(z)) return z > 0 ? 1 : 0;
  const t = 1 / (1 + 0.3275911 * Math.abs(z) / Math.SQRT2);
  const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-(z * z) / 2);
  return z >= 0 ? 0.5 * (1 + y) : 0.5 * (1 - y);
}

// ------------------------------------------------------------------ windows

// Recognise a Gamma event as a Chainlink-settled crypto Up/Down window. Returns {asset, periodMin} or null.
// Hourly "candle" markets settle on Binance's 1h candle instead, a different number, so they are left out.
export function classifyWindow(ev, mk) {
  const src = String((mk && mk.resolutionSource) || ev.resolutionSource || "");
  if (!/chain\.link/i.test(src)) return null;
  const title = String(ev.title || "");
  if (!/up or down/i.test(title)) return null;
  const series = String(ev.seriesSlug || "");
  let asset = series.split("-")[0];
  if (!ASSETS[asset]) {
    const name = title.split(/\s+up or down/i)[0].trim().toLowerCase();
    asset = NAME_TO_ASSET[name];
  }
  if (!asset || !ASSETS[asset]) return null;
  const startMs = Date.parse(mk.eventStartTime || ev.startTime || "");
  const endMs = Date.parse(mk.endDate || ev.endDate || "");
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) return null;
  return { asset, periodMin: Math.round((endMs - startMs) / 60000), startMs, endMs };
}

export const periodLabel = (min) => min >= 60 ? `${Math.round(min / 60)}h` : `${min}m`;

// ------------------------------------------------------------------ the settlement model

// Probability that the window settles Up.
//   ref0       the start price, in the same (exchange) units as spot
//   spot       the live exchange price
//   sigma      per-second volatility of log price
//   remSec     seconds until the window ends
//   tailAvg    the time-weighted average of spot over [end-60s, now] when we are inside the final minute
//   strikeSdBp uncertainty of ref0 in basis points
// The final number is the average of the last 60 seconds. With r seconds left and r >= 60, it is the mean of a
// random walk that starts in r-60 seconds, whose variance is sigma^2 (r - 60 + 60/3) = sigma^2 (r - 40). Inside the
// final minute part of the average is already known: final = w*avg_seen + (1-w)*avg_future, w = (60-r)/60, and the
// unknown part has variance ((r/60)^2) * sigma^2 * r/3.
export function modelUp({ ref0, spot, sigma, remSec, tailAvg = null, strikeSdBp = STRIKE_SD_BP.candle, floorBp = MODEL_FLOOR_BP }) {
  if (!(ref0 > 0) || !(spot > 0) || !(sigma > 0) || remSec == null) return null;
  const r = Math.max(0, remSec);
  let center = spot, varPrice;
  if (r >= TWAP_SEC) varPrice = sigma * sigma * (r - 40);
  else {
    const w = (TWAP_SEC - r) / TWAP_SEC;
    const seen = tailAvg > 0 ? tailAvg : spot;
    center = w * seen + (1 - w) * spot;
    const f = r / TWAP_SEC;
    varPrice = f * f * sigma * sigma * r / 3;
  }
  const x = Math.log(center / ref0);
  const sd = Math.sqrt(varPrice + (strikeSdBp * 1e-4) ** 2 + (floorBp * 1e-4) ** 2);
  const z = x / sd;
  return { pUp: clamp(Phi(z), 0.01, 0.99), x, sd, z, center };
}

// Realised per-second volatility from 1-minute closes (most recent last).
export function sigmaFromCloses(closes) {
  const r = [];
  for (let i = 1; i < closes.length; i++) if (closes[i] > 0 && closes[i - 1] > 0) r.push(Math.log(closes[i] / closes[i - 1]));
  if (r.length < 10) return null;
  const v = r.reduce((s, x) => s + x * x, 0) / r.length;
  return Math.sqrt(v / 60);
}

// ------------------------------------------------------------------ the strike ladder

// Windows of one asset that end at the same moment settle on the same number. For each ordered pair (low strike L,
// high strike H) with both strikes known well enough to be sure of the order, buying Up on L and Down on H returns
// at least $1 in every outcome. Returns the pairs, cheapest first, with their cost per pair including taker fees.
export function ladderPairs(group, { minGapSd = 3 } = {}) {
  const out = [];
  const live = group.filter((w) => w.ref && w.ref.value > 0 && w.m && w.m.ask(0) != null && w.m.ask(1) != null && !w.m.crossed(0) && !w.m.crossed(1));
  for (const L of live) for (const H of live) {
    if (L === H) continue;
    // Two published Chainlink strikes order exactly; otherwise the gap must clear the strikes' own uncertainty.
    const exact = L.ref.oracle > 0 && H.ref.oracle > 0;
    const gap = exact ? Math.log(H.ref.oracle / L.ref.oracle) : Math.log(H.ref.value / L.ref.value);
    const sdGap = Math.hypot(L.ref.sdBp, H.ref.sdBp) * 1e-4;
    if (exact ? gap < 0 : gap < minGapSd * sdGap) continue;  // H must be the higher strike, beyond doubt
    const aUp = L.m.ask(0), aDown = H.m.ask(1);
    const fUp = feePerShare(L.m, aUp), fDown = feePerShare(H.m, aDown);
    const cost = aUp + fUp + aDown + fDown;
    out.push({ low: L, high: H, cost, edge: 1 - cost, gapBp: bp(gap), legs: [{ w: L, side: 0, ask: aUp, fee: fUp }, { w: H, side: 1, ask: aDown, fee: fDown }] });
  }
  return out.sort((a, b) => a.cost - b.cost);
}

// Up ask + Down ask (+ fees) inside one window: under $1 is a complete set for less than it pays.
export function complementCost(m) {
  const a0 = m.ask(0), a1 = m.ask(1);
  if (a0 == null || a1 == null || m.crossed(0) || m.crossed(1)) return null;
  return a0 + feePerShare(m, a0) + a1 + feePerShare(m, a1);
}

// ------------------------------------------------------------------ the council

export const SPRINT_AGENTS = [];
function agent(id, name, desk, description, veto, fn) { SPRINT_AGENTS.push({ id, name, group: desk, description, veto, fn }); }
export const SPRINT_AGENTS_BY_ID = () => Object.fromEntries(SPRINT_AGENTS.map((a) => [a.id, a]));

export function defaultSprintRules() {
  return {
    min_edge: 0.05,        // directional: model probability - ask - fee, per share
    min_votes: 17,         // of 25
    explore_edge: 0.04,    // aggressive demo mode: smaller edge, fewer votes
    explore_votes: 14,
    min_arb_edge: 0.01,    // ladder / complement: $ per $1 pair after fees
    min_sec_left: 10,
    max_spread: 0.06,
    horizon_min: 20,       // windows ending within this many minutes are watched
    kelly_frac: 0.5,       // half-Kelly
    max_stake_pct: 35,     // % of equity on one directional trade
  };
}

// Half-Kelly stake for a binary bet paying $1 at all-in cost `cost` with win probability P.
export function kellyStake(P, cost, equity, rules) {
  if (!(cost > 0 && cost < 1)) return 0;
  const f = (P - cost) / (1 - cost);
  if (f <= 0) return 0;
  return Math.min(equity * f * rules.kelly_frac, equity * rules.max_stake_pct / 100);
}

// ---- oracle desk: what will this window settle on?
agent("strike", "Strike Agent", "oracle", "Knows the window's price to beat: Chainlink's own value when published, else the live feed's average over the minute before the start.", true, (w, x) => {
  if (!x.ref) return [AVOID, 1, x.started ? "no start price captured for this window yet" : "window hasn't opened; no price to beat yet"];
  const src = { oracle: "Chainlink's published value", recorded: "live feed, averaged over the start minute", candle: "Coinbase 1-minute candle" }[x.ref.src];
  if (x.ref.src !== "oracle" && x.model && Math.abs(bp(x.model.x)) < 2 * x.ref.sdBp) return [HOLD, 0.2, `strike ${x.ref.display} (${src}, ±${x.ref.sdBp}bp) — price is within the strike's own uncertainty`];
  return [BUY, x.ref.src === "oracle" ? 0.9 : x.ref.src === "recorded" ? 0.75 : 0.5, `price to beat ${x.ref.display} from ${src} (±${x.ref.sdBp}bp)`];
});
agent("feed", "Spot Feed Agent", "oracle", "Live exchange price must be fresh; a stale feed means the model is guessing.", true, (w, x) => {
  if (!x.spot) return [AVOID, 1, "no live spot price"];
  const age = x.spot.ageMs / 1000;
  if (age > 15) return [AVOID, 1, `spot is ${age.toFixed(0)}s old`];
  return [BUY, clamp(1 - age / 15, 0.2, 1), `${ASSETS[w.asset].cb} ${x.spot.price} · ${age.toFixed(1)}s old (${x.spot.src})`];
});
agent("cross", "Cross-Exchange Agent", "oracle", "A second exchange must show the same move since the start; disagreement means a bad print or basis risk.", true, (w, x) => {
  if (x.altMoveBp == null || x.model == null) return [ABSTAIN, 0, "no second-exchange reading yet"];
  const m1 = bp(x.model.x), m2 = x.altMoveBp, diff = Math.abs(m1 - m2);
  const tol = Math.max(8, 0.5 * Math.abs(m1));
  if (Math.sign(m1) !== Math.sign(m2) && Math.abs(m1) > 5 && Math.abs(m2) > 5) return [AVOID, 0.9, `Coinbase ${fmtBp(m1)} vs Binance.US ${fmtBp(m2)} since the start — feeds disagree`];
  if (diff > tol * 2) return [HOLD, 0.3, `moves differ: Coinbase ${fmtBp(m1)}, Binance.US ${fmtBp(m2)}`];
  return [BUY, clamp(1 - diff / (tol * 2), 0.2, 1), `Coinbase ${fmtBp(m1)} and Binance.US ${fmtBp(m2)} agree`];
});
agent("vol", "Volatility Agent", "oracle", "Measures how far the price typically moves per second; the model needs it, and a sudden regime change makes it unreliable.", true, (w, x) => {
  if (!x.sigma) return [AVOID, 1, "volatility unknown (candles not loaded)"];
  const perMin = bp(x.sigma * Math.sqrt(60));
  if (x.sigma10 && x.sigma10 > 2.5 * x.sigma) return [HOLD, 0.4, `last 10 min ${bp(x.sigma10 * Math.sqrt(60)).toFixed(1)}bp/min vs ${perMin.toFixed(1)}bp/min hourly — regime shift`];
  return [BUY, 0.6, `${perMin.toFixed(1)}bp per minute (60-min realised)`];
});
agent("clock", "Settlement Clock", "oracle", "Only trades open windows with time to fill; reports how much of the final average is already locked in.", true, (w, x) => {
  if (!x.started) return [AVOID, 1, `opens in ${secs((w.startMs - x.now) / 1000)}`];
  if (x.remSec < x.rules.min_sec_left) return [AVOID, 1, `${x.remSec.toFixed(0)}s left — too late to fill`];
  const locked = x.remSec < TWAP_SEC ? `, ${Math.round((1 - x.remSec / TWAP_SEC) * 100)}% of the final average already set` : "";
  return [BUY, clamp(x.elapsedFrac, 0.2, 1), `${secs(x.remSec)} left of ${periodLabel(w.periodMin)}${locked}`];
});

// ---- value desk: is the price wrong?
agent("model", "Settlement Model", "value", "P(side) from the start price, live spot, time left and volatility — compared with what the book implies.", false, (w, x) => {
  if (!x.model) return [ABSTAIN, 0, "model needs strike, spot and volatility"];
  const mid = x.mid;
  if (mid == null) return [HOLD, 0.2, `model ${c(x.P)}; no book mid`];
  if (x.P > mid + 0.02) return [BUY, clamp((x.P - mid) * 5, 0.2, 1), `model ${c(x.P)} vs book ${c(mid)} for ${x.sideName}`];
  if (x.P < mid - 0.05) return [AVOID, clamp((mid - x.P) * 5, 0.2, 1), `model ${c(x.P)} is below the book's ${c(mid)}`];
  return [HOLD, 0.2, `model ${c(x.P)} ≈ book ${c(mid)}`];
});
agent("distance", "Distance-to-Strike", "value", "How many standard deviations the price sits from the strike — how settled the outcome already is.", false, (w, x) => {
  if (!x.model) return [ABSTAIN, 0, "no model"];
  const zs = x.side === 0 ? x.model.z : -x.model.z;
  if (zs >= 0.5) return [BUY, clamp(zs / 2.5, 0.2, 1), `${zs.toFixed(2)}σ on ${x.sideName}'s side of the strike`];
  if (zs <= -0.5) return [HOLD, 0.3, `${(-zs).toFixed(2)}σ against ${x.sideName} — only worth it at a cheap price`];
  return [HOLD, 0.2, `coin-flip zone (${zs.toFixed(2)}σ)`];
});
agent("edge", "Edge Agent", "value", "Net edge per share = model probability - ask - taker fee. The decisive vote.", false, (w, x) => {
  if (x.edge == null) return [ABSTAIN, 0, "no ask or no model"];
  if (x.edge >= x.rules.min_edge) return [BUY, clamp(x.edge * 8, 0.3, 1), `${c(x.P)} fair vs ${c(x.ask)} ask + ${c(x.fee)} fee = ${(x.edge * 100).toFixed(1)}¢ edge`];
  if (x.edge > 0) return [HOLD, 0.3, `${(x.edge * 100).toFixed(1)}¢ edge — under the ${(x.rules.min_edge * 100).toFixed(0)}¢ bar`];
  return [AVOID, clamp(-x.edge * 8, 0.2, 1), `${(x.edge * 100).toFixed(1)}¢ — the ask is above fair value`];
});
agent("kelly", "Kelly Sizer", "value", "Sizes the bet at half-Kelly from the edge; no positive Kelly, no trade.", false, (w, x) => {
  if (x.edge == null) return [ABSTAIN, 0, "no edge estimate"];
  if (!(x.stake > 0)) return [AVOID, 0.6, "Kelly says bet nothing at this price"];
  return [BUY, clamp(x.stake / Math.max(x.equity, 1) * 3, 0.2, 1), `half-Kelly stake $${x.stake.toFixed(2)} of $${x.equity.toFixed(2)}`];
});
agent("consensus", "Market Consensus", "value", "Sanity check: a huge model-vs-market gap is more often a feed error than free money.", false, (w, x) => {
  if (x.mid == null || x.P == null) return [ABSTAIN, 0, "no mid or model"];
  const gap = Math.abs(x.P - x.mid);
  if (gap > 0.35) return [HOLD, 0.5, `model and market ${(gap * 100).toFixed(0)}¢ apart — suspicious`];
  if (gap < 0.2) return [BUY, 0.4, `model and market within ${(gap * 100).toFixed(0)}¢ — the edge isn't a data glitch`];
  return [HOLD, 0.3, `${(gap * 100).toFixed(0)}¢ apart`];
});

// ---- structure desk: does the price break arithmetic?
agent("ladder", "Strike Ladder Agent", "structure", "Windows ending together settle on one number: Up on the lower strike + Down on the higher strike pays at least $1.", false, (w, x) => {
  if (!x.siblings) return [ABSTAIN, 0, "no other window ends at the same moment"];
  const mine = x.ladder.filter((p) => p.legs.some((l) => l.w === w && l.side === x.side));
  if (mine.length && mine[0].edge >= x.rules.min_arb_edge) return [BUY, 1, `ladder pair costs ${c(mine[0].cost)} for a guaranteed $1+ (+${(mine[0].edge * 100).toFixed(1)}¢)`];
  const best = x.ladder[0];
  return [HOLD, 0.2, best ? `ladder priced coherently (best pair ${c(best.cost)})` : `${x.siblings} sibling window(s), strikes too close to order`];
});
agent("complement", "Binary Complement", "structure", "Up ask + Down ask + fees below $1 is a whole set for less than it pays.", false, (w, x) => {
  if (x.complement == null) return [ABSTAIN, 0, "need both books"];
  if (x.complement < 1 - x.rules.min_arb_edge) return [BUY, 1, `Up+Down cost ${c(x.complement)} for a $1 payout`];
  return [x.complement < 1.04 ? BUY : HOLD, 0.25, `Up+Down = ${c(x.complement)} (overround ${((x.complement - 1) * 100).toFixed(1)}¢)`];
});
agent("bound", "Ladder Bound Agent", "structure", "Sibling windows bound this side's fair price; buying inside the bounds is consistent, outside is either a gift or a trap.", false, (w, x) => {
  if (!x.bounds) return [ABSTAIN, 0, "no ordered siblings"];
  const { lo, hi } = x.bounds;
  if (lo != null && x.ask != null && x.ask < lo - 0.01) return [BUY, 0.8, `siblings imply ≥ ${c(lo)}; ask ${c(x.ask)} is below`];
  if (hi != null && x.ask != null && x.ask > hi + 0.02) return [AVOID, 0.6, `siblings imply ≤ ${c(hi)}; ask ${c(x.ask)} is above`];
  return [HOLD, 0.2, `inside sibling bounds ${c(lo)}–${c(hi)}`];
});
agent("extreme", "Extreme Price Guard", "structure", "Refuses to risk 95¢ to make 5¢ against oracle tail risk, and refuses lottery tickets.", false, (w, x) => {
  if (x.ask == null) return [ABSTAIN, 0, "no ask"];
  if (x.ask > 0.93) return [AVOID, 0.8, `ask ${c(x.ask)}: risking ${c(x.ask)} to make ${c(1 - x.ask)}`];
  if (x.ask < 0.04) return [AVOID, 0.5, `ask ${c(x.ask)}: lottery ticket`];
  return [BUY, 0.3, `ask ${c(x.ask)} is in the tradeable band`];
});
agent("fee", "Fee Agent", "structure", "Polymarket's crypto taker fee is 7% x p(1-p) per share; it must not eat the edge.", false, (w, x) => {
  if (x.edge == null) return [ABSTAIN, 0, "no edge"];
  const gross = x.edge + x.fee;
  if (gross <= 0 || x.fee >= gross) return [AVOID, 0.6, `fee ${c(x.fee)} ≥ gross edge ${c(gross)}`];
  if (x.fee < 0.3 * gross) return [BUY, 0.5, `fee ${c(x.fee)} is ${Math.round(x.fee / gross * 100)}% of the gross edge`];
  return [HOLD, 0.3, `fee ${c(x.fee)} takes ${Math.round(x.fee / gross * 100)}% of the edge`];
});

// ---- execution desk: can it actually be bought?
agent("spread", "Spread Sentinel", "execution", "Wide spreads mean the quoted price is not a price you get.", true, (w, x) => {
  const s = x.spread;
  if (s == null) return [AVOID, 1, "no two-sided book"];
  if (s <= 0) return [AVOID, 1, `crossed book (bid ${c(x.bid)} ≥ ask ${c(x.ask)}): matching paused, prices not real`];
  if (s > x.rules.max_spread) return [AVOID, 1, `spread ${c(s)}`];
  return [s <= 0.02 ? BUY : HOLD, clamp(1 - s / x.rules.max_spread, 0.2, 1), `spread ${c(s)}`];
});
agent("depth", "Order Book Depth", "execution", "The stake must fill at least the 5-share minimum within 2¢ of the best ask.", true, (w, x) => {
  const asks = x.m.asks(x.side);
  if (!asks.length) return [AVOID, 1, "no asks"];
  const budget = Math.max(x.stake || 0, (x.m.minOrder || 5) * asks[0].price);
  const near = asks.filter((l) => l.price <= asks[0].price + 0.02);
  const fill = walkAsks(near, budget);
  if (fill.shares < (x.m.minOrder || 5)) return [AVOID, 1, `only ${fill.shares.toFixed(1)} shares within 2¢ of the ask`];
  return [BUY, clamp(fill.spent / budget, 0.3, 1), `${fill.shares.toFixed(0)} shares fillable at avg ${c(fill.avgPrice)}`];
});
agent("imbalance", "Book Imbalance", "execution", "Who is leaning on this token's book: heavier bids than asks near the touch is support.", false, (w, x) => {
  const b = x.m.bids(x.side), a = x.m.asks(x.side);
  if (!b.length || !a.length) return [ABSTAIN, 0, "one-sided book"];
  const mid = (b[0].price + a[0].price) / 2;
  const sum = (ls, keep) => ls.reduce((s, l) => keep(l.price) ? s + l.price * l.size : s, 0);
  const bd = sum(b, (p) => p >= mid - 0.05), ad = sum(a, (p) => p <= mid + 0.05);
  const imb = bd + ad > 0 ? (bd - ad) / (bd + ad) : 0;
  if (imb > 0.2) return [BUY, clamp(imb, 0.2, 1), `bids outweigh asks ${Math.round(imb * 100)}%`];
  return [HOLD, 0.2, `book balance ${imb >= 0 ? "+" : ""}${Math.round(imb * 100)}%`];
});
agent("lag", "Quote Lag Agent", "execution", "Has the book kept up with spot? If the model moved our way and the book hasn't yet, that is the window to buy.", false, (w, x) => {
  if (x.lag == null) return [ABSTAIN, 0, "not enough history yet"];
  const { dModel, dBook } = x.lag; // change in P(side) over ~20s
  if (dModel - dBook > 0.03) return [BUY, clamp((dModel - dBook) * 10, 0.3, 1), `model moved ${(dModel * 100).toFixed(1)}¢ our way in 20s, book only ${(dBook * 100).toFixed(1)}¢`];
  return [HOLD, 0.2, `book kept pace (model ${(dModel * 100).toFixed(1)}¢, book ${(dBook * 100).toFixed(1)}¢)`];
});
agent("liquidity", "Liquidity Floor", "execution", "Skips windows with almost nobody quoting.", true, (w, x) => {
  const liq = x.m.liq || 0;
  if (liq < 100) return [AVOID, 1, `$${liq.toFixed(0)} liquidity`];
  return [liq >= 1000 ? BUY : HOLD, clamp(Math.log10(liq) / 4.5, 0.2, 1), `$${liq >= 1000 ? (liq / 1000).toFixed(1) + "k" : liq.toFixed(0)} liquidity`];
});

// ---- risk desk: should the account take it?
agent("momentum", "Spot Momentum", "risk", "Direction of the last 30 seconds of spot — informational, the model is drift-free.", false, (w, x) => {
  if (x.mom30Bp == null) return [ABSTAIN, 0, "need 30s of samples"];
  const m = x.side === 0 ? x.mom30Bp : -x.mom30Bp;
  if (m > 1) return [BUY, clamp(m / 10, 0.2, 1), `spot ${fmtBp(x.mom30Bp)} in 30s, toward ${x.sideName}`];
  return [HOLD, 0.2, `spot ${fmtBp(x.mom30Bp)} in 30s`];
});
agent("jump", "Jump Guard", "risk", "A sudden spot jump is a moment when books reprice and the oracle's 60s average lags — wait it out. Also holds trading until the live feed has warmed up.", true, (w, x) => {
  if (x.jumpSigma == null) return [AVOID, 1, "warming up: needs 10s of live ticks to judge the tape"];
  if (x.jumpSigma > 3) return [AVOID, 0.7, `spot jumped ${x.jumpSigma.toFixed(1)}σ in 10s`];
  return [BUY, 0.4, `calm tape (${x.jumpSigma.toFixed(1)}σ in 10s)`];
});
agent("exposure", "Exposure Agent", "risk", "One position per window, and a cap on open positions.", true, (w, x) => {
  if (x.holdsWindow) return [AVOID, 1, "already holding this window"];
  if (x.openPositions >= x.maxPositions) return [AVOID, 1, `${x.openPositions} positions open (max ${x.maxPositions})`];
  return [BUY, 0.5, `${x.openPositions}/${x.maxPositions} positions open`];
});
agent("bankroll", "Bankroll Agent", "risk", "The stake must fit the cash and clear the 5-share minimum order.", true, (w, x) => {
  const minCost = (x.m.minOrder || 5) * (x.ask || 1);
  if (x.cash < minCost) return [AVOID, 1, `$${x.cash.toFixed(2)} cash < $${minCost.toFixed(2)} minimum order`];
  if (x.stake != null && x.stake > 0 && x.stake < minCost) return [HOLD, 0.3, `Kelly stake $${x.stake.toFixed(2)} below the $${minCost.toFixed(2)} minimum — would size up to the minimum`];
  return [BUY, 0.5, `$${x.cash.toFixed(2)} cash covers it`];
});
agent("track", "Track-Record Agent", "risk", "Scores the model against the market on this session's resolved windows; if the model is losing, it says so.", false, (w, x) => {
  const t = x.track;
  if (!t || t.n < 12) return [HOLD, 0.2, `building a record (${t ? t.n : 0} windows scored)`];
  if (t.model > t.market + 0.01) return [AVOID, 0.7, `model Brier ${t.model.toFixed(3)} worse than market ${t.market.toFixed(3)} over ${t.n} windows`];
  return [BUY, clamp((t.market - t.model) * 50 + 0.3, 0.3, 1), `model Brier ${t.model.toFixed(3)} vs market ${t.market.toFixed(3)} over ${t.n} windows`];
});

// Run all 25 agents on one side of one window. `x` is the per-side context built by sideContext().
export function runSprintCouncil(w, x) {
  const votes = SPRINT_AGENTS.map((a) => {
    let stance, strength, reason;
    try { [stance, strength, reason] = a.fn(w, x); } catch (e) { [stance, strength, reason] = [ABSTAIN, 0, `error: ${e.message}`]; }
    return { agent: a.id, name: a.name, stance, strength: Math.round(clamp(strength || 0, 0, 1) * 100) / 100, reason, veto: a.veto };
  });
  const count = (s) => votes.filter((v) => v.stance === s).length;
  const vetoedBy = votes.filter((v) => v.veto && v.stance === AVOID).map((v) => v.agent);
  return { votes, buy: count(BUY), avoid: count(AVOID), hold: count(HOLD), vetoedBy };
}

// Per-side context from the per-window analysis `a` (see SprintEngine.analyze) and the account.
export function sideContext(w, a, side, acct, rules, now) {
  const m = w.m;
  const P = a.model ? (side === 0 ? a.model.pUp : 1 - a.model.pUp) : null;
  const ask = m.ask(side), bid = m.bid(side);
  const crossed = m.crossed(side);
  const fee = ask != null ? feePerShare(m, ask) : 0;
  const edge = P != null && ask != null && !crossed ? P - ask - fee : null;
  const mid = m.mid(side);
  const equity = acct.equity;
  const stake = P != null && ask != null ? kellyStake(P, ask + fee, equity, rules) : null;
  let bounds = null;
  if (a.ladderGroup && a.ladderGroup.length > 1 && w.ref) {
    // Up(this) >= Up(higher strike) and <= Up(lower strike); Down mirrors it.
    let lo = null, hi = null;
    for (const s of a.ladderGroup) {
      if (s === w || !s.ref || !s.m) continue;
      const gap = Math.log(s.ref.value / w.ref.value), sd = Math.hypot(s.ref.sdBp, w.ref.sdBp) * 1e-4;
      if (Math.abs(gap) < 3 * sd) continue;
      const sMid = s.m.mid(side);
      if (sMid == null) continue;
      const sHigher = gap > 0;
      // For Up: a higher-strike sibling's Up is a floor; lower-strike sibling's Up a ceiling. For Down the reverse.
      const isFloor = side === 0 ? sHigher : !sHigher;
      if (isFloor) lo = lo == null ? sMid : Math.max(lo, sMid); else hi = hi == null ? sMid : Math.min(hi, sMid);
    }
    if (lo != null || hi != null) bounds = { lo, hi };
  }
  let lag = null;
  if (a.trail && a.trail.length >= 2 && a.model) {
    const old = a.trail.find((t) => now - t.t >= 15000 && now - t.t <= 40000);
    if (old && old.pUp != null && old.mid0 != null && mid != null) {
      const pOld = side === 0 ? old.pUp : 1 - old.pUp, mOld = side === 0 ? old.mid0 : 1 - old.mid0;
      lag = { dModel: P - pOld, dBook: mid - mOld };
    }
  }
  return {
    now, rules, side, sideName: side === 0 ? "Up" : "Down", m,
    started: a.started, remSec: a.remSec, elapsedFrac: a.elapsedFrac,
    ref: w.ref, spot: a.spot, altMoveBp: a.altMoveBp, sigma: a.sigma, sigma10: a.sigma10, model: a.model,
    mom30Bp: a.mom30Bp, jumpSigma: a.jumpSigma,
    P, ask, bid, mid, fee, edge, spread: ask != null && bid != null ? ask - bid : null, stake,
    ladder: a.ladder || [], siblings: a.ladderGroup ? a.ladderGroup.length - 1 : 0, bounds,
    complement: complementCost(m), lag,
    equity, cash: acct.cash, openPositions: acct.open, maxPositions: acct.maxPositions,
    holdsWindow: acct.heldMarkets.has(m.id), track: a.track,
  };
}

// Decide what a council result means. Returns "arbitrage" | "snipe" | "exploratory" | null.
export function decide(res, x, rules, { explore = false } = {}) {
  if (res.vetoedBy.length) return null;
  if (x.edge == null) return null;
  if (x.edge >= rules.min_edge && res.buy >= rules.min_votes) return "snipe";
  if (explore && x.edge >= rules.explore_edge && res.buy >= rules.explore_votes) return "exploratory";
  return null;
}

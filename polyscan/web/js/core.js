// The market model, the 25 analysis agents, the council, and the money math for Polymarket.
//
// Polymarket is a prediction market, not a memecoin exchange. Every market is binary at the token level: two
// outcome tokens whose shares each pay exactly $1 if that outcome wins and $0 if it loses. Multi-outcome questions
// ("who wins the election") are groups of such binary markets inside one event. Prices live in [0,1] and read as
// probabilities. So the honest, mathematically grounded edges are:
//   - Dutch-book arbitrage: on a complete, mutually-exclusive-and-exhaustive outcome set, buying one share of every
//     outcome costs Σ(ask) and pays exactly $1 at resolution. If Σ(ask + fee) < 1 that is risk-free profit.
//   - Binary complement arbitrage: within one market, YES ask + NO ask + fees < 1 is the same thing.
//   - Documented price biases (favorite-longshot) and order-flow, which are weak and clearly labelled speculative.
// There is no pretense of forecasting elections better than the crowd; the market mid IS the crowd's forecast.

export const BUY = "BUY", HOLD = "HOLD", AVOID = "AVOID", ABSTAIN = "ABSTAIN";

const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
const num = (x) => { const n = Number(x); return Number.isFinite(n) ? n : 0; };
export const usd = (x) => x == null ? "–" : (x < 0 ? "-$" : "$") + Math.abs(x).toFixed(2);
export const cents = (p) => p == null ? "–" : `${(p * 100).toFixed(1)}¢`;
const pctStr = (x) => `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%`;

// ------------------------------------------------------------------ fee math
// Taker fee per Polymarket's schedule: rate * shares * (p*(1-p))^exponent. Makers (limit orders) pay nothing.
// rate and feesEnabled are read per market; 0 for geopolitics and many politics markets. Matches the published
// "$1.25 per 100 shares at 50c" for a 0.05 rate.
export function feePerShare(m, price) {
  if (!m || !m.feesEnabled || !(m.feeRate > 0)) return 0;
  const base = price * (1 - price);
  return m.feeRate * Math.pow(base, m.feeExp || 1);
}

// Walk a sorted ask ladder (ascending price) spending up to `usdBudget`. Returns the fill.
export function walkAsks(asks, usdBudget) {
  let spent = 0, shares = 0;
  for (const lvl of asks || []) {
    const p = num(lvl.price), avail = num(lvl.size);
    if (p <= 0 || avail <= 0) continue;
    const canSpend = usdBudget - spent;
    if (canSpend <= 1e-9) break;
    const wantShares = canSpend / p;
    const take = Math.min(wantShares, avail);
    shares += take; spent += take * p;
    if (take < avail) break;
  }
  return { shares, spent, avgPrice: shares > 0 ? spent / shares : null, filled: spent };
}

// Walk a bid ladder (descending price) selling `shares`. Returns proceeds before fees.
export function walkBids(bids, shares) {
  let left = shares, proceeds = 0, sold = 0;
  for (const lvl of bids || []) {
    const p = num(lvl.price), avail = num(lvl.size);
    if (p <= 0 || avail <= 0 || left <= 1e-9) continue;
    const take = Math.min(left, avail);
    proceeds += take * p; sold += take; left -= take;
  }
  return { proceeds, sold, avgPrice: sold > 0 ? proceeds / sold : null, remaining: left };
}

// ------------------------------------------------------------------ market model

export class Market {
  constructor(f) {
    Object.assign(this, {
      id: "", question: "", slug: "", conditionId: "", eventId: "", eventTitle: "", groupTitle: "",
      outcomes: ["Yes", "No"], tokenIds: ["", ""], image: "",
      // prices are for outcome 0 (the YES side); outcome 1 is the complement
      yesMid: null, bestBidYes: null, bestAskYes: null, bestBidNo: null, bestAskNo: null,
      lastTrade: null, spread: null,
      vol24: 0, liq: 0, chg1h: 0, chg24h: 0, openInterest: 0,
      endMs: null, createdMs: null,
      feeRate: 0, feesEnabled: false, feeExp: 1, tickSize: 0.001, minOrder: 5,
      negRisk: false, negRiskAugmented: false, enableOrderBook: true, acceptingOrders: true,
      active: true, closed: false, restricted: false, umaStatuses: [],
      resolutionSource: "", description: "",
      // filled in for candidates
      books: null,        // {0:{bids,asks},1:{bids,asks}} from the CLOB
      history: null,      // [{t,p}] for outcome 0
      booksAt: 0, historyAt: 0,
      // resolution (when closed)
      resolved: false, winner: null, // index 0/1 of the winning outcome
      // per-pass council output, keyed by side
      verdicts: {},
    }, f);
  }

  ask(side) {
    if (this.books && this.books[side] && this.books[side].asks && this.books[side].asks.length) return num(this.books[side].asks[0].price);
    return side === 0 ? this.bestAskYes : this.bestAskNo;
  }
  bid(side) {
    if (this.books && this.books[side] && this.books[side].bids && this.books[side].bids.length) return num(this.books[side].bids[0].price);
    return side === 0 ? this.bestBidYes : this.bestBidNo;
  }
  asks(side) { return this.books && this.books[side] ? this.books[side].asks || [] : []; }
  bids(side) { return this.books && this.books[side] ? this.books[side].bids || [] : []; }
  mid(side) {
    const a = this.ask(side), b = this.bid(side);
    if (a != null && b != null) return (a + b) / 2;
    if (side === 0 && this.yesMid != null) return this.yesMid;
    if (side === 1 && this.yesMid != null) return 1 - this.yesMid;
    return null;
  }
  spreadOf(side) {
    const a = this.ask(side), b = this.bid(side);
    return a != null && b != null ? a - b : (this.spread != null ? this.spread : null);
  }
  hoursToEnd(now) { return this.endMs ? (this.endMs - now) / 3.6e6 : null; }
  depthUsd(side) { // $ of asks within 3 cents of best
    const asks = this.asks(side);
    if (!asks.length) return null;
    const best = num(asks[0].price);
    let d = 0;
    for (const l of asks) { if (num(l.price) > best + 0.03) break; d += num(l.price) * num(l.size); }
    return d;
  }
  bookImbalance() { // (bid$ - ask$)/(bid$+ask$) within 5c of mid, outcome 0
    const asks = this.asks(0), bids = this.bids(0);
    if (!asks.length || !bids.length) return null;
    const m = this.mid(0);
    const sum = (ls, lo, hi) => ls.reduce((s, l) => (num(l.price) >= lo && num(l.price) <= hi ? s + num(l.price) * num(l.size) : s), 0);
    const bd = sum(bids, m - 0.05, m), ad = sum(asks, m, m + 0.05);
    return bd + ad > 0 ? (bd - ad) / (bd + ad) : null;
  }
}

// ------------------------------------------------------------------ agents

const strengthClamp = (x) => Math.round(clamp(Number.isFinite(x) ? x : 0, 0, 1) * 1000) / 1000;
export const AGENTS = [];
function agent(id, name, group, description, veto, fn) { AGENTS.push({ id, name, group, description, veto, fn }); }

// ctx: { now, side, event (siblings + arb), totalDepthFloorUsd, tradeUsd }
export function vote(a, m, ctx) {
  let stance, strength, reason;
  try { [stance, strength, reason] = a.fn(m, ctx); }
  catch (e) { [stance, strength, reason] = [ABSTAIN, 0, `error: ${e.message}`]; }
  return { agent: a.id, stance, strength: strengthClamp(strength), reason };
}

const sideName = (m, ctx) => m.outcomes[ctx.side] || (ctx.side === 0 ? "Yes" : "No");

// ---- tradeability (veto group): can this be traded well at all?

agent("tradable", "Market Status Sentinel", "tradeability",
  "The market must be open, accepting orders, on the order book, and not in dispute.", true, (m) => {
    if (m.closed || !m.active) return [AVOID, 1, "market is closed or inactive"];
    if (!m.enableOrderBook) return [AVOID, 1, "no order book (not CLOB-traded)"];
    if (!m.acceptingOrders) return [AVOID, 1, "not accepting orders right now"];
    if ((m.umaStatuses || []).some((s) => s && s !== "" && s !== "proposed")) return [AVOID, 0.9, `in UMA resolution (${m.umaStatuses.join(",")})`];
    // `restricted` only means geo-restricted on Polymarket's own UI (e.g. US persons). The CLOB market is real and
    // priced; for an analysis/paper tool it is not a reason to veto. Noted, not blocked.
    return [BUY, 0.5, m.restricted ? "open (geo-restricted on polymarket.com)" : "open and accepting orders"];
  });

agent("spread", "Spread Sentinel", "tradeability",
  "The bid/ask spread must be tight, or you start the trade already down.", true, (m, ctx) => {
    const s = m.spreadOf(ctx.side);
    if (s == null) return [ABSTAIN, 0, "no two-sided quote yet"];
    if (s > 0.08) return [AVOID, 0.9, `spread ${cents(s)}: too wide to trade`];
    if (s > 0.04) return [HOLD, 0.3, `spread ${cents(s)}: wide`];
    return [BUY, 0.5 + 0.5 * (1 - s / 0.04), `spread ${cents(s)}`];
  });

agent("depth", "Order Book Depth Auditor", "tradeability",
  "There must be enough size near the ask to fill the intended order.", true, (m, ctx) => {
    const d = m.depthUsd(ctx.side);
    const need = ctx.tradeUsd || 20;
    if (d == null) return [ABSTAIN, 0, "order book not loaded"];
    if (d < need) return [AVOID, 0.9, `only ${usd(d)} of asks within 3¢: can't fill ${usd(need)}`];
    if (d < need * 3) return [HOLD, 0.3, `thin book: ${usd(d)} within 3¢`];
    return [BUY, 0.5 + 0.4 * clamp(d / (need * 10), 0, 1), `${usd(d)} of asks within 3¢`];
  });

agent("liquidity", "Liquidity Floor", "tradeability",
  "Total market liquidity and recent volume must clear a floor.", true, (m) => {
    if (m.liq < 2000 && m.vol24 < 2000) return [AVOID, 0.8, `thin market: ${usd(m.liq)} liquidity, ${usd(m.vol24)} 24h volume`];
    if (m.liq < 10000) return [HOLD, 0.3, `${usd(m.liq)} liquidity`];
    return [BUY, 0.5 + 0.3 * clamp(Math.log10(m.liq / 10000), 0, 1), `${usd(m.liq)} liquidity, ${usd(m.vol24)} 24h`];
  });

agent("resolution_time", "Resolution Clock", "tradeability",
  "Avoid markets about to resolve (no time for a thesis) or far from any catalyst.", true, (m, ctx) => {
    const h = m.hoursToEnd(ctx.now);
    if (h == null) return [HOLD, 0.3, "no end date"];
    if (h < 0) return [AVOID, 1, "past its end date"];
    if (h < 0.5 && ctx.event && !ctx.event.arb) return [AVOID, 0.6, `resolves in ${(h * 60).toFixed(0)} min: no room for a view`];
    if (h > 24 * 180) return [HOLD, 0.3, `${(h / 24).toFixed(0)} days out: capital tied up a long time`];
    return [BUY, 0.5, `${h < 48 ? h.toFixed(0) + "h" : (h / 24).toFixed(0) + "d"} to resolution`];
  });

agent("extreme_price", "Extreme Price Guard", "tradeability",
  "Near 0 or 1 the upside is tiny and the tail risk is lopsided.", true, (m, ctx) => {
    const a = m.ask(ctx.side);
    if (a == null) return [ABSTAIN, 0, "no ask"];
    if (a >= 0.97) return [AVOID, 0.8, `ask ${cents(a)}: at most ${cents(1 - a)} of upside, full downside`];
    if (a <= 0.03) return [HOLD, 0.3, `ask ${cents(a)}: deep longshot`];
    if (a >= 0.9) return [HOLD, 0.3, `ask ${cents(a)}: little room up`];
    return [BUY, 0.5, `ask ${cents(a)}: room both ways`];
  });

agent("stale_quote", "Stale Quote Detector", "tradeability",
  "An ask far from the last trade with no recent volume is a quote you can't trust.", true, (m, ctx) => {
    const a = m.ask(ctx.side);
    if (a == null || m.lastTrade == null) return [ABSTAIN, 0, "no reference price"];
    const lastForSide = ctx.side === 0 ? m.lastTrade : 1 - m.lastTrade;
    const gap = Math.abs(a - lastForSide);
    if (gap > 0.12 && m.vol24 < 5000) return [AVOID, 0.7, `ask ${cents(a)} vs last ${cents(lastForSide)} on thin volume: stale`];
    if (gap > 0.15) return [HOLD, 0.3, `ask ${cents(a)} far from last ${cents(lastForSide)}`];
    return [BUY, 0.4, "quote near the last trade"];
  });

agent("round_trip", "Round-Trip Cost Agent", "tradeability",
  "Fees plus the spread plus exit slippage must be small next to the edge you expect.", true, (m, ctx) => {
    const a = m.ask(ctx.side), b = m.bid(ctx.side);
    if (a == null || b == null) return [ABSTAIN, 0, "no two-sided quote"];
    const fee = feePerShare(m, a) + feePerShare(m, b);
    const cost = (a - b) + fee; // enter at ask, exit at bid, pay fees both ways (per share)
    const costFrac = cost / a;
    if (costFrac > 0.25) return [AVOID, 0.7, `round-trip costs ${(costFrac * 100).toFixed(0)}% of the stake`];
    if (costFrac > 0.12) return [HOLD, 0.3, `round-trip costs ${(costFrac * 100).toFixed(0)}%`];
    return [BUY, 0.4 + 0.4 * (1 - costFrac / 0.12), `round-trip costs ${(costFrac * 100).toFixed(0)}% (fee ${cents(fee)}/sh)`];
  });

// ---- mispricing / value group: is there an edge?

// Dutch-book arbitrage across a complete negRisk set. The engine attaches ctx.event.arb when it applies.
agent("arbitrage", "Dutch-Book Arbitrage Agent", "value",
  "On a complete, exhaustive outcome set, buying one share of every outcome for less than $1 (after fees) is risk-free.", false, (m, ctx) => {
    const arb = ctx.event && ctx.event.arb;
    if (!arb) return [HOLD, 0.2, "not part of a risk-free set"];
    if (arb.edge > 0) return [BUY, clamp(0.7 + arb.edge * 10, 0.7, 1), `risk-free set: pay ${usd(arb.cost)} to get $1 (edge ${(arb.edge * 100).toFixed(1)}%)`];
    return [HOLD, 0.2, `full set costs ${usd(arb.cost)} ≥ $1: no arb`];
  });

agent("complement", "Binary Complement Agent", "value",
  "Within one market, YES ask + NO ask + fees below $1 is a risk-free pair.", false, (m) => {
    const ay = m.ask(0), an = m.ask(1);
    if (ay == null || an == null) return [ABSTAIN, 0, "need both sides' asks"];
    const cost = ay + an + feePerShare(m, ay) + feePerShare(m, an);
    if (cost < 0.995) return [BUY, clamp(0.7 + (1 - cost) * 10, 0.7, 1), `YES+NO cost ${usd(cost)} < $1: risk-free ${((1 - cost) * 100).toFixed(1)}%`];
    if (cost > 1.03) return [HOLD, 0.2, `YES+NO cost ${usd(cost)}: normal overround`];
    return [HOLD, 0.3, `YES+NO cost ${usd(cost)}`];
  });

agent("overround", "Overround Detector", "value",
  "When a complete field's prices sum well above $1, the favorites are collectively overpriced.", false, (m, ctx) => {
    const ev = ctx.event;
    if (!ev || ev.augmented || ev.n < 3 || ev.sumMid == null) return [HOLD, 0.25, "not a complete multi-outcome field"];
    const over = ev.sumMid - 1;
    if (over > 0.04 && ctx.side === 1) return [BUY, clamp(0.5 + over * 5, 0.5, 0.85), `field sums to ${ev.sumMid.toFixed(2)}: NO side has the overround edge`];
    if (over > 0.04 && ctx.side === 0) return [HOLD, 0.2, `field overpriced (${ev.sumMid.toFixed(2)}): YES side pays the overround`];
    return [HOLD, 0.3, `field sums to ${ev.sumMid.toFixed(2)}`];
  });

agent("favorite_longshot", "Favorite-Longshot Bias", "value",
  "A documented bias: longshots are overbet and favorites underbet. Mild, price-dependent.", false, (m, ctx) => {
    const a = m.ask(ctx.side);
    if (a == null) return [ABSTAIN, 0, "no ask"];
    if (a >= 0.55 && a <= 0.9) return [BUY, 0.4 + 0.3 * ((a - 0.55) / 0.35), `${cents(a)} favorite: the underbet side historically`];
    if (a <= 0.15) return [AVOID, 0.4, `${cents(a)} longshot: overbet historically`];
    return [HOLD, 0.25, `${cents(a)}: no clear bias`];
  });

agent("fair_value", "Fair-Value Band", "value",
  "Compares the ask to a blend of the mid, the last trade and the book's weighted price.", false, (m, ctx) => {
    const a = m.ask(ctx.side), mid = m.mid(ctx.side);
    if (a == null || mid == null) return [ABSTAIN, 0, "no quote"];
    const lastForSide = m.lastTrade != null ? (ctx.side === 0 ? m.lastTrade : 1 - m.lastTrade) : mid;
    const fair = 0.6 * mid + 0.4 * lastForSide;
    const edge = fair - a; // paying below fair is good
    if (edge > 0.01) return [BUY, clamp(0.5 + edge * 15, 0.5, 0.9), `ask ${cents(a)} below fair ${cents(fair)} (edge ${cents(edge)})`];
    if (edge < -0.03) return [AVOID, 0.5, `ask ${cents(a)} above fair ${cents(fair)}`];
    return [HOLD, 0.3, `ask ${cents(a)} near fair ${cents(fair)}`];
  });

// ---- momentum / flow group

agent("momentum", "Momentum Scout", "flow",
  "Recent price change for this side, from the 1h and 24h moves.", false, (m, ctx) => {
    const sign = ctx.side === 0 ? 1 : -1;
    const h1 = (m.chg1h || 0) * sign, d1 = (m.chg24h || 0) * sign;
    if (h1 === 0 && d1 === 0) return [HOLD, 0.25, "no recent move"];
    if (h1 > 0.02 && d1 > 0) return [BUY, clamp(0.5 + h1 * 6, 0.5, 0.85), `drifting toward this side (+${(h1 * 100).toFixed(0)}% 1h)`];
    if (h1 < -0.05) return [AVOID, 0.5, `moving away (${(h1 * 100).toFixed(0)}% 1h)`];
    return [HOLD, 0.3, `${pctStr(h1)} 1h`];
  });

agent("trend", "Price-History Trend", "flow",
  "Direction and steadiness of the price path for this side.", false, (m, ctx) => {
    const h = m.history;
    if (!h || h.length < 6) return [ABSTAIN, 0, "price history not loaded"];
    const sign = ctx.side === 0 ? 1 : -1;
    const ps = h.map((x) => (ctx.side === 0 ? x.p : 1 - x.p));
    const first = ps[0], last = ps[ps.length - 1];
    const ups = ps.slice(1).filter((p, i) => p > ps[i]).length / (ps.length - 1);
    const move = last - first;
    if (move > 0.03 && ups > 0.5) return [BUY, clamp(0.5 + move * 4, 0.5, 0.85), `steady climb (${pctStr(move)} over the window)`];
    if (move < -0.05) return [AVOID, 0.4, `trending down (${pctStr(move)})`];
    return [HOLD, 0.3, `flat/choppy (${pctStr(move)})`];
  });

agent("volume_surge", "Volume Surge Analyst", "flow",
  "Is 24h volume large next to liquidity? Attention and tradability.", false, (m) => {
    if (m.liq <= 0) return [ABSTAIN, 0, "no liquidity figure"];
    const turn = m.vol24 / m.liq;
    if (turn > 2) return [BUY, clamp(0.5 + turn / 10, 0.5, 0.8), `${turn.toFixed(1)}x turnover (24h vol vs liquidity)`];
    if (turn < 0.2) return [HOLD, 0.25, `quiet: ${turn.toFixed(2)}x turnover`];
    return [BUY, 0.45, `${turn.toFixed(1)}x turnover`];
  });

agent("book_imbalance", "Book Imbalance Tracker", "flow",
  "More resting bids than asks near the mid is buy pressure for outcome 0.", false, (m, ctx) => {
    const imb = m.bookImbalance();
    if (imb == null) return [ABSTAIN, 0, "order book not loaded"];
    const sideImb = ctx.side === 0 ? imb : -imb;
    if (sideImb > 0.3) return [BUY, clamp(0.5 + sideImb / 2, 0.5, 0.8), `book leans to this side (${(sideImb * 100).toFixed(0)}% imbalance)`];
    if (sideImb < -0.3) return [AVOID, 0.4, `book leans against (${(sideImb * 100).toFixed(0)}%)`];
    return [HOLD, 0.3, `balanced book (${(sideImb * 100).toFixed(0)}%)`];
  });

agent("bid_support", "Bid Support Agent", "flow",
  "Resting bids below the ask are the demand you would exit into; thin bids mean you are stuck.", false, (m, ctx) => {
    const bids = m.bids(ctx.side);
    if (!bids.length) return [ABSTAIN, 0, "order book not loaded"];
    const need = ctx.tradeUsd || 20;
    const top = bids[0].price;
    let support = 0;
    for (const l of bids) { if (l.price < top - 0.05) break; support += l.price * l.size; }
    if (support < need) return [HOLD, 0.3, `only ${usd(support)} of bids within 5¢ to exit into`];
    return [BUY, 0.4 + 0.3 * clamp(support / (need * 8), 0, 1), `${usd(support)} of bid support within 5¢`];
  });

agent("open_interest", "Open Interest Monitor", "flow",
  "Standing money in the market; rising interest means a live question.", false, (m) => {
    if (!m.openInterest) return [HOLD, 0.25, "no open-interest figure"];
    if (m.openInterest > 50000) return [BUY, 0.5, `${usd(m.openInterest)} open interest`];
    if (m.openInterest < 2000) return [HOLD, 0.25, `${usd(m.openInterest)} open interest: small`];
    return [BUY, 0.4, `${usd(m.openInterest)} open interest`];
  });

// ---- structure / context group

agent("resolution_clarity", "Resolution Clarity", "structure",
  "A clear resolution source and no dispute make the payoff trustworthy.", false, (m) => {
    if (!m.resolutionSource && !(m.description && m.description.length > 40)) return [HOLD, 0.25, "no stated resolution source"];
    if ((m.umaStatuses || []).some((s) => s === "disputed")) return [AVOID, 0.6, "resolution disputed"];
    return [BUY, 0.45, m.resolutionSource ? "clear resolution source" : "resolution described"];
  });

agent("event_consistency", "Event Consistency Check", "structure",
  "Within one event, sibling markets should price consistently; a contradiction is a flag or an edge.", false, (m, ctx) => {
    const ev = ctx.event;
    if (!ev || !ev.siblings || ev.siblings.length < 1) return [HOLD, 0.3, "no sibling markets"];
    if (ev.augmented === false && ev.sumMid != null) {
      if (Math.abs(ev.sumMid - 1) <= 0.02) return [BUY, 0.5, `field prices are consistent (sum ${ev.sumMid.toFixed(2)})`];
      return [HOLD, 0.3, `field sum ${ev.sumMid.toFixed(2)} off $1`];
    }
    return [HOLD, 0.35, `${ev.siblings.length} sibling markets`];
  });

agent("time_value", "Time-Decay Value", "structure",
  "A directional view needs time to play out but not so long that capital is dead.", false, (m, ctx) => {
    const h = m.hoursToEnd(ctx.now);
    if (h == null || h < 0) return [ABSTAIN, 0, "no valid end date"];
    if (h >= 12 && h <= 24 * 60) return [BUY, 0.5, `${h < 48 ? h.toFixed(0) + "h" : (h / 24).toFixed(0) + "d"}: room for the view`];
    if (h < 12) return [HOLD, 0.3, `${h.toFixed(0)}h: little time`];
    return [HOLD, 0.3, `${(h / 24).toFixed(0)}d: slow`];
  });

agent("price_level", "Probability Band", "structure",
  "Mid-range probabilities have two-way risk and the most room to be wrong about.", false, (m, ctx) => {
    const a = m.ask(ctx.side);
    if (a == null) return [ABSTAIN, 0, "no ask"];
    if (a >= 0.3 && a <= 0.75) return [BUY, 0.5, `${cents(a)}: balanced odds`];
    if (a > 0.9 || a < 0.1) return [HOLD, 0.25, `${cents(a)}: lopsided`];
    return [BUY, 0.4, `${cents(a)}`];
  });

agent("category_fit", "Category & Fee Fit", "structure",
  "Zero-fee categories (geopolitics, many politics) and maker-friendly books keep more of any edge.", false, (m, ctx) => {
    const fee = feePerShare(m, m.ask(ctx.side) || 0.5);
    if (!m.feesEnabled || m.feeRate === 0) return [BUY, 0.55, "zero trading fees on this market"];
    if (fee > 0.015) return [HOLD, 0.25, `${cents(fee)}/share taker fee`];
    return [BUY, 0.4, `${cents(fee)}/share taker fee`];
  });

// The aggregator: net expected value of buying this side at the ask, after fees.
agent("expected_value", "Expected Value Engine", "value",
  "Blends a fair-probability estimate against the ask, minus fees, into a net edge. Only a positive net edge votes BUY.", false, (m, ctx) => {
    const a = m.ask(ctx.side);
    if (a == null) return [ABSTAIN, 0, "no ask"];
    if (ctx.event && ctx.event.arb && ctx.event.arb.edge > 0) return [BUY, 0.9, `part of a risk-free set (+${(ctx.event.arb.edge * 100).toFixed(1)}%)`];
    const fair = estimateFair(m, ctx);
    const fee = feePerShare(m, a);
    const edge = fair - a - fee; // expected $ per share held to resolution
    if (edge > 0.005) return [BUY, clamp(0.5 + edge * 12, 0.5, 0.95), `net edge ${cents(edge)}/share (fair ${cents(fair)} vs ask ${cents(a)}+fee)`];
    if (edge < -0.02) return [AVOID, 0.5, `negative edge ${cents(edge)}/share after fees`];
    return [HOLD, 0.3, `edge ${cents(edge)}/share: thin`];
  });

// A deliberately modest fair-value estimate: the market mid, nudged by the documented favorite-longshot bias and
// by short-term momentum. It does NOT claim to forecast the event; it only tilts a few cents off the crowd price.
export function estimateFair(m, ctx) {
  const mid = m.mid(ctx.side);
  if (mid == null) return m.ask(ctx.side) || 0.5;
  let fair = mid;
  if (mid >= 0.55 && mid <= 0.9) fair += 0.01 * ((mid - 0.55) / 0.35); // favorites a touch underpriced
  if (mid <= 0.15) fair -= 0.01; // longshots a touch overpriced
  const momSign = ctx.side === 0 ? 1 : -1;
  fair += clamp((m.chg1h || 0) * momSign, -0.02, 0.02) * 0.5; // small momentum tilt
  return clamp(fair, 0.01, 0.99);
}

if (AGENTS.length !== 25) throw new Error(`expected 25 agents, got ${AGENTS.length}`);
export const AGENTS_BY_ID = Object.fromEntries(AGENTS.map((a) => [a.id, a]));

// ------------------------------------------------------------------ council

export function defaultRules() {
  return {
    min_buy_votes: 18,     // of 25
    max_avoid_votes: 0,
    min_edge: 0.02,        // directional signals need >= 2c/share net expected edge
    min_arb_edge: 0.005,   // risk-free sets need >= 0.5c/share after fees
  };
}

export function runCouncil(m, ctx, rules) {
  const votes = AGENTS.map((a) => vote(a, m, ctx));
  const counts = { BUY: 0, HOLD: 0, AVOID: 0, ABSTAIN: 0 };
  for (const v of votes) counts[v.stance]++;
  const vetoedBy = votes.filter((v) => v.stance === AVOID && AGENTS_BY_ID[v.agent].veto).map((v) => v.agent);
  const buyStrengths = votes.filter((v) => v.stance === BUY).map((v) => v.strength);
  const conviction = buyStrengths.length ? 100 * buyStrengths.reduce((a, b) => a + b, 0) / buyStrengths.length : 0;
  const arb = ctx.event && ctx.event.arb && ctx.event.arb.edge >= rules.min_arb_edge;
  const fair = estimateFair(m, ctx);
  const a = m.ask(ctx.side);
  const edge = a != null ? fair - a - feePerShare(m, a) : -1;
  // A risk-free set needs the safety vetoes clear and the edge real, not a chorus of momentum agents. A directional
  // bet needs both the vote threshold and a positive net edge, because there it IS a judgement call.
  const isSignal = !vetoedBy.length && counts.AVOID <= rules.max_avoid_votes
    && (arb ? true : (counts.BUY >= rules.min_buy_votes && edge >= rules.min_edge));
  return {
    marketId: m.id, side: ctx.side, buy_votes: counts.BUY, hold_votes: counts.HOLD, avoid_votes: counts.AVOID,
    abstain_votes: counts.ABSTAIN, vetoed_by: vetoedBy, conviction: Math.round(conviction * 10) / 10,
    edge, arb: !!arb, is_signal: isSignal, votes,
  };
}

// Arbitrage math for a complete negRisk event: cost of one share of every outcome, after fees, vs the $1 payout.
export function arbForEvent(ev) {
  if (!ev || ev.augmented || !ev.members || ev.members.length < 2) return null;
  let cost = 0, fillable = true;
  for (const mm of ev.members) {
    const a = mm.ask(0);
    if (a == null || a <= 0) { fillable = false; break; }
    cost += a + feePerShare(mm, a);
  }
  if (!fillable) return null;
  return { cost, edge: 1 - cost, members: ev.members.length };
}

export function compareRank(a, b) {
  if (a.arb !== b.arb) return a.arb ? -1 : 1;
  return (b.edge - a.edge) || (b.conviction - a.conviction) || (b.buy_votes - a.buy_votes);
}

export function buildSignal(m, verdict, rules, now, ctx) {
  const side = verdict.side;
  const a = m.ask(side);
  const reasons = verdict.votes.filter((v) => v.stance === BUY).sort((x, y) => y.strength - x.strength);
  const cautions = verdict.votes.filter((v) => v.stance === HOLD || v.stance === AVOID);
  return {
    id: `${m.id}:${side}:${Math.floor(now / 1000)}`,
    marketId: m.id, tokenId: m.tokenIds[side], side, eventId: m.eventId,
    question: m.question, eventTitle: m.eventTitle, outcome: m.outcomes[side], image: m.image,
    created: now,
    kind: verdict.arb ? "arbitrage" : "directional",
    arb: verdict.arb && ctx.event && ctx.event.arb ? {
      cost: ctx.event.arb.cost, edge: ctx.event.arb.edge, members: ctx.event.arb.members,
      memberIds: (ctx.event.members || []).map((x) => x.id),
      sets_payout: 1,
    } : null,
    headline: verdict.arb
      ? `RISK-FREE SET: ${m.eventTitle || m.question}`
      : `BUY ${m.outcomes[side]} @ ${cents(a)} — ${m.question}`,
    ask: a, mid: m.mid(side), bid: m.bid(side), fair: estimateFair(m, ctx),
    edge: verdict.edge, fee_per_share: feePerShare(m, a),
    agree: verdict.buy_votes, total_agents: AGENTS.length, conviction: verdict.conviction,
    end_ms: m.endMs,
    reasons: reasons.map((v) => ({ agent: AGENTS_BY_ID[v.agent].name, reason: v.reason, strength: v.strength })),
    cautions: cautions.map((v) => ({ agent: AGENTS_BY_ID[v.agent].name, stance: v.stance, reason: v.reason })),
    links: {
      polymarket: m.slug ? `https://polymarket.com/market/${m.slug}` : (m.eventId ? `https://polymarket.com/event/${m.eventId}` : "https://polymarket.com"),
    },
    outcome_result: null,
  };
}

// Score a directional signal once the market resolves: did that outcome win?
export function scoreSignal(sig, m) {
  if (!m || !m.resolved || m.winner == null) return null;
  const won = m.winner === sig.side;
  const payout = won ? 1 : 0;
  const net = payout - sig.ask - (sig.fee_per_share || 0); // per share, held to resolution
  return { status: won ? "win" : "loss", net_per_share: Math.round(net * 1000) / 1000, resolved_to: m.outcomes[m.winner] };
}

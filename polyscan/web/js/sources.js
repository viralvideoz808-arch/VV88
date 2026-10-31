// Live Polymarket data, straight from the browser. Both APIs send `access-control-allow-origin: *`, so no server is
// needed. Gamma serves market and event metadata; the CLOB serves order books and price history. Parsing is kept in
// plain functions so it can be tested offline against recorded fixtures.

import { Market } from "./core.js";

export const GAMMA = "https://gamma-api.polymarket.com";
export const CLOB = "https://clob.polymarket.com";

const toNum = (x) => { const n = Number(x); return Number.isFinite(n) ? n : 0; };
const toMs = (iso) => { const t = Date.parse(iso || ""); return Number.isFinite(t) ? t : null; };
function parseJsonArray(s, fallback) {
  if (Array.isArray(s)) return s;
  try { const v = JSON.parse(s); return Array.isArray(v) ? v : fallback; } catch { return fallback; }
}

export function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(signal.reason);
    const t = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => { clearTimeout(t); reject(signal.reason); }, { once: true });
  });
}

export class RateLimiter {
  constructor(rate, perSec) { this.interval = (perSec * 1000) / rate; this.next = 0; }
  async wait(signal) {
    const now = performance.now?.() ?? Date.now();
    const delay = this.next - now;
    this.next = Math.max(now, this.next) + this.interval;
    if (delay > 0) await sleep(delay, signal);
  }
}

// ------------------------------------------------------------------ parsing

// One Gamma market object -> Market. `ev` is the parent event (for fee/negRisk context) when known.
export function parseMarket(mk, ev = null) {
  const outcomes = parseJsonArray(mk.outcomes, ["Yes", "No"]);
  const tokenIds = parseJsonArray(mk.clobTokenIds, ["", ""]);
  const prices = parseJsonArray(mk.outcomePrices, []).map(toNum);
  const fs = mk.feeSchedule || {};
  const closed = !!mk.closed;
  let resolved = false, winner = null;
  if (closed && prices.length === 2 && (prices[0] === 1 || prices[1] === 1) && (prices[0] === 0 || prices[1] === 0)) {
    resolved = true; winner = prices[0] === 1 ? 0 : 1;
  }
  const yesMid = prices.length ? prices[0] : null;
  const bestAskYes = mk.bestAsk != null ? toNum(mk.bestAsk) : null;
  const bestBidYes = mk.bestBid != null ? toNum(mk.bestBid) : null;
  return new Market({
    id: String(mk.id || mk.conditionId || ""), question: mk.question || "", slug: mk.slug || "",
    conditionId: mk.conditionId || "", eventId: ev ? String(ev.id || "") : String((mk.events && mk.events[0] && mk.events[0].id) || ""),
    eventTitle: ev ? ev.title || "" : ((mk.events && mk.events[0] && mk.events[0].title) || ""),
    groupTitle: mk.groupItemTitle || "", outcomes, tokenIds, image: mk.image || mk.icon || "",
    yesMid, bestAskYes, bestBidYes,
    bestAskNo: bestBidYes != null ? 1 - bestBidYes : null, // NO ask ≈ 1 - YES bid (book-confirmed later)
    bestBidNo: bestAskYes != null ? 1 - bestAskYes : null,
    lastTrade: mk.lastTradePrice != null ? toNum(mk.lastTradePrice) : null,
    spread: mk.spread != null ? toNum(mk.spread) : null,
    vol24: toNum(mk.volume24hr), liq: toNum(mk.liquidityNum || mk.liquidity), openInterest: toNum(mk.openInterest),
    chg1h: toNum(mk.oneHourPriceChange), chg24h: toNum(mk.oneDayPriceChange),
    endMs: toMs(mk.endDate || mk.endDateIso), createdMs: toMs(mk.createdAt),
    feeRate: toNum(fs.rate), feesEnabled: !!mk.feesEnabled && toNum(fs.rate) > 0, feeExp: toNum(fs.exponent) || 1,
    tickSize: toNum(mk.orderPriceMinTickSize) || 0.001, minOrder: toNum(mk.orderMinSize) || 5,
    negRisk: !!mk.negRisk, negRiskAugmented: ev ? !!ev.negRiskAugmented : !!mk.negRiskAugmented,
    enableOrderBook: mk.enableOrderBook !== false, acceptingOrders: mk.acceptingOrders !== false,
    active: mk.active !== false, closed, restricted: !!mk.restricted,
    umaStatuses: parseJsonArray(mk.umaResolutionStatuses, []),
    resolutionSource: mk.resolutionSource || "", description: mk.description || "",
    resolved, winner,
  });
}

// A Gamma event -> { id, title, negRisk, augmented, markets:[Market], members:[Market used in the arb set], sumMid, n }.
export function parseEvent(ev) {
  const markets = (ev.markets || []).map((mk) => parseMarket(mk, ev));
  const complete = !!ev.negRisk && !ev.negRiskAugmented;
  const tradable = markets.filter((m) => m.active && !m.closed && m.enableOrderBook && m.yesMid != null);
  const members = complete ? tradable : [];
  const sumMid = members.length >= 2 ? members.reduce((s, m) => s + (m.yesMid || 0), 0) : null;
  return {
    id: String(ev.id || ""), title: ev.title || "", slug: ev.slug || "",
    negRisk: !!ev.negRisk, augmented: !!ev.negRiskAugmented, complete,
    markets, members, sumMid, n: members.length,
    vol24: toNum(ev.volume24hr), liq: toNum(ev.liquidity),
  };
}

// A CLOB /book response -> {bids:[{price,size}], asks:[{price,size}]}, both sorted best-first.
export function parseBook(b) {
  const lvl = (x) => ({ price: toNum(x.price), size: toNum(x.size) });
  const bids = (b.bids || []).map(lvl).filter((l) => l.size > 0).sort((a, c) => c.price - a.price);
  const asks = (b.asks || []).map(lvl).filter((l) => l.size > 0).sort((a, c) => a.price - c.price);
  return { bids, asks };
}

// Attach both outcome books to a market. booksByToken maps tokenId -> parsed book.
export function attachBooks(m, booksByToken, now) {
  const b0 = booksByToken[m.tokenIds[0]], b1 = booksByToken[m.tokenIds[1]];
  if (!b0 && !b1) return false;
  m.books = { 0: b0 || { bids: [], asks: [] }, 1: b1 || { bids: [], asks: [] } };
  m.booksAt = now;
  if (b0 && b0.asks.length) m.bestAskYes = b0.asks[0].price;
  if (b0 && b0.bids.length) m.bestBidYes = b0.bids[0].price;
  if (b1 && b1.asks.length) m.bestAskNo = b1.asks[0].price;
  if (b1 && b1.bids.length) m.bestBidNo = b1.bids[0].price;
  return true;
}

export function parseHistory(d) {
  const h = (d && d.history) || [];
  return h.map((x) => ({ t: toNum(x.t), p: toNum(x.p) })).filter((x) => x.p > 0);
}

// ------------------------------------------------------------------ network

export class PolyClient {
  constructor(signal, fetchImpl = (...a) => fetch(...a)) {
    this.signal = signal;
    this.fetch = fetchImpl;
    this.gammaLimit = new RateLimiter(8, 1);
    this.clobLimit = new RateLimiter(10, 1);
    this.errors = {};
  }

  async getJson(url, source, limiter) {
    if (limiter) await limiter.wait(this.signal);
    const ctl = new AbortController();
    const onAbort = () => ctl.abort();
    this.signal.addEventListener("abort", onAbort, { once: true });
    const timer = setTimeout(() => ctl.abort(), 15000);
    try {
      const r = await this.fetch(url, { signal: ctl.signal });
      if (r.status === 429) { this.errors[source] = "rate limited (429)"; await sleep(3000, this.signal); return null; }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      delete this.errors[source];
      return d;
    } catch (e) {
      if (this.signal.aborted) throw e;
      this.errors[source] = String(e.message || e).slice(0, 120);
      return null;
    } finally {
      clearTimeout(timer);
      this.signal.removeEventListener("abort", onAbort);
    }
  }

  // A page of active events. With endMaxIso, only events resolving on or before that time (soonest first) — the
  // fast-market universe; otherwise the highest-volume events.
  async events(limit = 100, offset = 0, endMaxIso = null) {
    const order = endMaxIso ? "endDate&ascending=true" : "volume24hr&ascending=false";
    const nowIso = new Date().toISOString();
    const window = endMaxIso ? `&end_date_min=${encodeURIComponent(nowIso)}&end_date_max=${encodeURIComponent(endMaxIso)}` : "";
    const url = `${GAMMA}/events?closed=false&active=true&archived=false&limit=${limit}&offset=${offset}&order=${order}${window}`;
    const d = await this.getJson(url, "gamma", this.gammaLimit);
    return Array.isArray(d) ? d.map(parseEvent) : [];
  }

  // Order books for a batch of token ids (CLOB accepts one at a time here; we batch the requests).
  async books(tokenIds) {
    const out = {};
    const uniq = [...new Set(tokenIds.filter(Boolean))];
    const results = await Promise.all(uniq.map((id) =>
      this.getJson(`${CLOB}/book?token_id=${id}`, "clob", this.clobLimit).then((b) => [id, b]).catch(() => [id, null])));
    for (const [id, b] of results) if (b) out[id] = parseBook(b);
    return out;
  }

  async history(tokenId, interval = "6h", fidelity = 10) {
    const d = await this.getJson(`${CLOB}/prices-history?market=${tokenId}&interval=${interval}&fidelity=${fidelity}`, "clob", this.clobLimit);
    return parseHistory(d);
  }

  // Re-read a single market (used to check resolution / refresh a held position).
  async market(id) {
    const d = await this.getJson(`${GAMMA}/markets?id=${id}`, "gamma", this.gammaLimit);
    return Array.isArray(d) && d.length ? parseMarket(d[0]) : null;
  }
}

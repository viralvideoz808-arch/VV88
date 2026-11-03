// Live crypto spot prices for the minute-market model, straight from exchanges that allow browser access.
//   Coinbase Exchange   websocket ticker (every trade), REST ticker as a fallback, 1-minute candles for volatility
//   Binance.US          one batched REST call every few seconds, used only to cross-check Coinbase's move
// Chainlink's BTC/USD stream (what settles the markets) tracks Coinbase's average over the same minute to ~1-4bp.

import { ASSETS, sigmaFromCloses } from "./sprint.js";
import { RateLimiter, sleep } from "./sources.js";

export const CB_REST = "https://api.exchange.coinbase.com";
export const CB_WS = "wss://ws-feed.exchange.coinbase.com";
export const ALT_REST = "https://api.binance.us";

const KEEP_MS = 25 * 60 * 1000;   // keep 25 minutes of ticks per asset
const MIN_GAP_MS = 250;           // store at most 4 ticks a second per asset

// Time-weighted average of a step series [{t,p}] (each price holds until the next tick) over [a, b].
// Returns {avg, n, covered} where covered says the series starts at or before `a + slackMs`.
export function twap(samples, a, b, slackMs = 5000) {
  if (!samples.length || b <= a) return null;
  let i = 0;
  while (i < samples.length && samples[i].t <= a) i++;
  const startIdx = Math.max(0, i - 1);
  const first = samples[startIdx];
  if (first.t > a + slackMs) return { avg: null, n: 0, covered: false };
  let sum = 0, span = 0, n = 0, t = a, p = first.p;
  for (let j = startIdx + 1; j < samples.length && samples[j].t < b; j++) {
    const tj = samples[j].t;
    if (tj > t) { sum += p * (tj - t); span += tj - t; t = tj; }
    p = samples[j].p; n++;
  }
  sum += p * (b - t); span += b - t;
  return { avg: span > 0 ? sum / span : null, n: n + 1, covered: true };
}

export class SpotFeed {
  constructor({ signal, assets = Object.keys(ASSETS), fetchImpl = (...a) => fetch(...a), WebSocketImpl = globalThis.WebSocket, now = () => Date.now() } = {}) {
    this.signal = signal;
    this.assets = assets;
    this.fetch = fetchImpl;
    this.WS = WebSocketImpl;
    this.now = now;
    this.ticks = Object.fromEntries(assets.map((a) => [a, []]));
    this.alt = Object.fromEntries(assets.map((a) => [a, []]));
    this.candles = Object.fromEntries(assets.map((a) => [a, new Map()])); // minute start ms -> [t, low, high, open, close, vol]
    this.status = { coinbase: "connecting", "binance.us": "connecting", candles: "connecting" };
    this.wsOpen = false;
    this.ws = null;
    this.cbLimit = new RateLimiter(6, 1);
  }

  start() {
    const loops = [this.wsLoop(), this.restLoop(), this.altLoop(), this.candleLoop()];
    for (const p of loops) p.catch((e) => { if (!this.signal.aborted) console.error("spot loop", e); });
    this.signal.addEventListener("abort", () => { try { this.ws && this.ws.close(); } catch { /* ignore */ } }, { once: true });
    return this;
  }

  record(asset, p, t = this.now(), into = this.ticks) {
    if (!(p > 0)) return;
    const s = into[asset];
    if (!s) return;
    const last = s[s.length - 1];
    if (last && t - last.t < MIN_GAP_MS) { last.p = p; return; }
    if (last && t < last.t) return;
    s.push({ t, p });
    const cut = t - KEEP_MS;
    let k = 0;
    while (k < s.length && s[k].t < cut) k++;
    if (k > 0) s.splice(0, k);
  }

  // ------------------------------------------------------------ readings

  last(asset) {
    const s = this.ticks[asset];
    if (!s || !s.length) return null;
    const x = s[s.length - 1];
    return { price: x.p, t: x.t, ageMs: this.now() - x.t, src: this.wsOpen ? "Coinbase live" : "Coinbase REST" };
  }
  avg(asset, a, b) { return twap(this.ticks[asset] || [], a, b); }
  priceAt(asset, t, series = this.ticks) {
    const s = series[asset] || [];
    let best = null;
    for (const x of s) { if (x.t <= t) best = x; else break; }
    return best;
  }
  altMoveBp(asset, sinceMs) {
    const s = this.alt[asset] || [];
    if (s.length < 2) return null;
    const a = this.priceAt(asset, sinceMs, this.alt), b = s[s.length - 1];
    if (!a || this.now() - b.t > 20000 || sinceMs - a.t > 20000) return null;
    return Math.log(b.p / a.p) * 1e4;
  }
  // Log move over the last `ms`, in bp. The price at a past moment is the last tick before it, however sparse the
  // trading — but only once the feed has been recording since before that moment.
  moveBp(asset, ms) {
    const s = this.ticks[asset] || [];
    if (!s.length) return null;
    const t = this.now() - ms;
    if (s[0].t > t) return null;
    const then = this.priceAt(asset, t);
    return then ? Math.log(s[s.length - 1].p / then.p) * 1e4 : null;
  }
  // The start-minute average from a 1-minute candle (OHLC/4), the fallback when we weren't recording at the start.
  candleAvg(asset, minuteStartMs) {
    const k = this.candles[asset] && this.candles[asset].get(minuteStartMs);
    return k ? (k[1] + k[2] + k[3] + k[4]) / 4 : null;
  }
  sigma(asset, minutes = 60) {
    const m = this.candles[asset];
    if (!m || !m.size) return null;
    const keys = [...m.keys()].sort((a, b) => a - b).slice(-(minutes + 1));
    return sigmaFromCloses(keys.map((k) => m.get(k)[4]));
  }

  // ------------------------------------------------------------ loops

  async wsLoop() {
    if (!this.WS) { this.status.coinbase = "REST polling (no websocket)"; return; }
    let backoff = 1000;
    while (!this.signal.aborted) {
      await new Promise((resolve) => {
        let ws;
        try { ws = new this.WS(CB_WS); } catch (e) { this.status.coinbase = "websocket blocked; REST polling"; resolve(); return; }
        this.ws = ws;
        ws.onopen = () => {
          backoff = 1000;
          ws.send(JSON.stringify({ type: "subscribe", product_ids: this.assets.map((a) => ASSETS[a].cb), channels: ["ticker"] }));
        };
        ws.onmessage = (ev) => {
          let d; try { d = JSON.parse(ev.data); } catch { return; }
          if (d.type === "subscriptions") { this.wsOpen = true; this.status.coinbase = "live (websocket)"; return; }
          if (d.type !== "ticker") return;
          const asset = this.assets.find((a) => ASSETS[a].cb === d.product_id);
          const t = Date.parse(d.time);
          if (asset) this.record(asset, Number(d.price), Number.isFinite(t) ? Math.min(t, this.now()) : this.now());
        };
        ws.onerror = () => { this.status.coinbase = "websocket error; REST polling"; };
        ws.onclose = () => { this.wsOpen = false; this.ws = null; resolve(); };
      });
      if (this.signal.aborted) return;
      await sleep(backoff, this.signal).catch(() => {});
      backoff = Math.min(backoff * 2, 30000);
    }
  }

  async getJson(url, limiter) {
    if (limiter) await limiter.wait(this.signal);
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 10000);
    const onAbort = () => ctl.abort();
    this.signal.addEventListener("abort", onAbort, { once: true });
    try {
      const r = await this.fetch(url, { signal: ctl.signal });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return await r.json();
    } finally { clearTimeout(timer); this.signal.removeEventListener("abort", onAbort); }
  }

  // REST ticker (quote midpoint) for any asset the websocket hasn't updated in 4 seconds.
  async restLoop() {
    while (!this.signal.aborted) {
      const stale = this.assets.filter((a) => { const l = this.last(a); return !l || l.ageMs > 4000; });
      for (const a of stale) {
        try {
          // A quiet market may not trade for many seconds; the live bid/ask midpoint still says where it is.
          const d = await this.getJson(`${CB_REST}/products/${ASSETS[a].cb}/ticker`, this.cbLimit);
          const bid = Number(d.bid), ask = Number(d.ask);
          this.record(a, bid > 0 && ask >= bid ? (bid + ask) / 2 : Number(d.price), this.now());
          if (!this.wsOpen) this.status.coinbase = "REST polling";
        } catch (e) { if (this.signal.aborted) return; this.status.coinbase = `error: ${String(e.message || e).slice(0, 60)}`; }
      }
      await sleep(stale.length ? 1500 : 2000, this.signal).catch(() => {});
    }
  }

  async altLoop() {
    const syms = this.assets.map((a) => ASSETS[a].alt);
    while (!this.signal.aborted) {
      try {
        const d = await this.getJson(`${ALT_REST}/api/v3/ticker/price?symbols=${encodeURIComponent(JSON.stringify(syms))}`);
        const now = this.now();
        for (const x of d || []) {
          const a = this.assets.find((k) => ASSETS[k].alt === x.symbol);
          if (a) this.record(a, Number(x.price), now, this.alt);
        }
        this.status["binance.us"] = "ok";
      } catch (e) {
        if (this.signal.aborted) return;
        this.status["binance.us"] = `error: ${String(e.message || e).slice(0, 60)}`;
      }
      await sleep(4000, this.signal).catch(() => {});
    }
  }

  // 1-minute candles: the last ~90 minutes, refreshed each minute (volatility, and start prices we missed).
  async candleLoop() {
    while (!this.signal.aborted) {
      let ok = 0;
      for (const a of this.assets) {
        try {
          const end = new Date(this.now()).toISOString();
          const start = new Date(this.now() - 95 * 60000).toISOString();
          const d = await this.getJson(`${CB_REST}/products/${ASSETS[a].cb}/candles?granularity=60&start=${start}&end=${end}`, this.cbLimit);
          const m = this.candles[a];
          for (const k of d || []) m.set(k[0] * 1000, k.map(Number));
          const cut = this.now() - 6 * 3.6e6;
          for (const t of m.keys()) if (t < cut) m.delete(t);
          ok++;
        } catch (e) { if (this.signal.aborted) return; }
      }
      this.status.candles = ok === this.assets.length ? "ok" : `${ok}/${this.assets.length} assets`;
      await sleep(60000, this.signal).catch(() => {});
    }
  }
}

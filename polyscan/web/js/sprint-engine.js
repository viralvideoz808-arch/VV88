// The engine behind the minute-markets page. Start launches these loops inside the tab:
//   windows   Gamma: every Chainlink-settled crypto "Up or Down" window ending in the next N minutes
//   books     CLOB: both order books of every open window, in one batched request every ~2s
//   strikes   Gamma: Chainlink's published value at each window's start (the previous window's final price)
//   council   the model + all 25 agents on both sides of every open window, the strike ladder, and the trades
//   paper     settle the demo account and score every window: model vs market
// plus the spot feed (Coinbase websocket + Binance.US cross-check + candles). Stop aborts them all.

import { PolyClient, attachBooks, parseMarket, sleep } from "./sources.js";
import { PaperAccount } from "./paper.js";
import { SpotFeed } from "./spot.js";
import {
  ASSETS, STRIKE_SD_BP, SPRINT_AGENTS, classifyWindow, complementCost, decide, defaultSprintRules, ladderPairs,
  modelUp, periodLabel, runSprintCouncil, sideContext,
} from "./sprint.js";

const KEY = { rules: "polyscan.sprint.rules", signals: "polyscan.sprint.signals", calib: "polyscan.sprint.calib", paper: "polyscan.sprint.paper" };
function storageGet(key) { try { return JSON.parse(localStorage.getItem(key) || "null"); } catch { return null; } }
function storageSet(key, v) { try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* ignore */ } }
const iso = (ms) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");
const median = (a) => { if (!a.length) return null; const s = [...a].sort((x, y) => x - y); return s[Math.floor(s.length / 2)]; };
const fmtPrice = (p) => p == null ? "–" : p >= 1000 ? p.toLocaleString("en-US", { maximumFractionDigits: 2 }) : p >= 1 ? p.toFixed(4) : p.toFixed(6);

export class SprintEngine {
  constructor({ now = () => Date.now() } = {}) {
    this.now = now;
    this.rules = { ...defaultSprintRules(), ...(storageGet(KEY.rules) || {}) };
    this.running = false;
    this.windows = new Map();        // market id -> window
    this.finals = new Map();         // boundary ms -> {asset: chainlink value}
    this.finalsTried = new Map();    // boundary ms -> last attempt ms
    this.basis = Object.fromEntries(Object.keys(ASSETS).map((a) => [a, []])); // oracle / coinbase - 1, recent
    this.basisDone = new Set();
    this.analysis = new Map();       // window id -> per-window analysis
    this.trails = new Map();         // window id -> [{t, pUp, mid0}]
    this.signals = (storageGet(KEY.signals) || []).slice(-300);
    this.calib = (storageGet(KEY.calib) || []).slice(-400);
    this.cooldown = new Map();
    this.resolved = new Map();       // market id -> winner side
    this.checkedAt = new Map();
    this.tally = {};
    this.passes = 0; this.lastPassMs = 0;
    this.status = {};
    this.paper = new PaperAccount({
      nowMs: () => this.now(), getMarket: (id) => (this.windows.get(id) || {}).m || null, storageKey: KEY.paper,
      defaults: { autoTrade: true, aggressive: true, sizePct: 30, maxPositions: 4, arbMaxUsd: 6 },
    });
  }

  // ------------------------------------------------------------ lifecycle

  start() {
    if (this.running) return;
    this.running = true;
    this.abort = new AbortController();
    const signal = this.abort.signal;
    this.client = new PolyClient(signal);
    this.paper.client = this.client;
    this.feed = new SpotFeed({ signal, now: this.now }).start();
    this.status = { gamma: "connecting", clob: "connecting" };
    this.startedAt = this.now();
    for (const p of [this.windowsLoop(signal), this.booksLoop(signal), this.strikesLoop(signal), this.councilLoop(signal), this.paperLoop(signal)]) {
      p.catch((e) => { if (!signal.aborted) { console.error("loop crashed", e); this.status.engine = `a loop crashed: ${e.message || e}`; } });
    }
  }

  stop() {
    if (!this.running) return;
    this.running = false;
    this.abort.abort(new DOMException("stopped", "AbortError"));
    for (const k of Object.keys(this.status)) this.status[k] = "stopped";
    if (this.feed) for (const k of Object.keys(this.feed.status)) this.feed.status[k] = "stopped";
    this.save();
  }

  setRules(patch) { Object.assign(this.rules, patch); storageSet(KEY.rules, this.rules); }
  save() { storageSet(KEY.signals, this.signals.slice(-300)); storageSet(KEY.calib, this.calib.slice(-400)); }

  // ------------------------------------------------------------ loops

  async windowsLoop(signal) {
    while (!signal.aborted) {
      const now = this.now();
      const seen = new Set();
      let ok = false;
      for (let page = 0; page < 3; page++) {
        const evs = await this.client.upDownEvents(iso(now), iso(now + this.rules.horizon_min * 60000), { offset: page * 100 });
        if (evs.length) ok = true;
        for (const ev of evs) {
          const mk = (ev.markets || [])[0];
          if (!mk) continue;
          const cls = classifyWindow(ev, mk);
          if (!cls) continue;
          const m = parseMarket(mk, ev);
          const old = this.windows.get(m.id);
          if (old && old.m.books) { m.books = old.m.books; m.booksAt = old.m.booksAt; }
          const md = ev.eventMetadata || {};
          const w = old || { id: m.id };
          Object.assign(w, cls, { m, title: ev.title, url: `https://polymarket.com/event/${ev.slug}` });
          if (md.priceToBeat > 0) w.oraclePtb = Number(md.priceToBeat);
          this.windows.set(m.id, w);
          seen.add(m.id);
        }
        if (evs.length < 100) break;
      }
      // drop windows that ended a while ago (positions are settled through the market id regardless)
      for (const [id, w] of this.windows) if (w.endMs < now - 3 * 60000) { this.windows.delete(id); this.analysis.delete(id); this.trails.delete(id); }
      this.status.gamma = ok ? "ok" : (this.client.errors.gamma || "no windows returned");
      await sleep(ok ? 15000 : 5000, signal);
    }
  }

  async booksLoop(signal) {
    while (!signal.aborted) {
      const now = this.now();
      const live = [...this.windows.values()].filter((w) => w.startMs - 60000 <= now && w.endMs > now - 5000);
      if (live.length) {
        const books = await this.client.booksBatch(live.flatMap((w) => w.m.tokenIds));
        for (const w of live) attachBooks(w.m, books, this.now());
        this.status.clob = this.client.errors.clob || "ok";
      }
      await sleep(2000, signal);
    }
  }

  // Chainlink's value at a boundary = the final price of every window that ended there = the price to beat of every
  // window that started there. Gamma publishes it a few minutes after the boundary.
  async strikesLoop(signal) {
    while (!signal.aborted) {
      const now = this.now();
      const wanted = new Set();
      for (const w of this.windows.values()) if (w.startMs <= now && !(w.oraclePtb > 0) && now - w.startMs < 4 * 3.6e6 + 60000) wanted.add(w.startMs);
      // also recent boundaries, to measure the oracle-vs-Coinbase basis
      const b5 = Math.floor(now / 300000) * 300000;
      for (let k = 1; k <= 3; k++) wanted.add(b5 - k * 300000);
      for (const t of [...wanted].sort((a, b) => b - a).slice(0, 6)) {
        if (this.finals.has(t) && Object.keys(this.finals.get(t)).length >= 6) continue;
        if (now - (this.finalsTried.get(t) || 0) < 30000 || now - t < 60000) continue;
        this.finalsTried.set(t, now);
        const evs = await this.client.upDownEvents(iso(t - 1000), iso(t + 1000), { closed: true });
        const got = this.finals.get(t) || {};
        for (const ev of evs) {
          const mk = (ev.markets || [])[0];
          const cls = mk && classifyWindow(ev, mk);
          const fin = ev.eventMetadata && Number(ev.eventMetadata.finalPrice);
          if (cls && fin > 0 && cls.endMs === t) got[cls.asset] = fin;
        }
        if (Object.keys(got).length) {
          this.finals.set(t, got);
          for (const [a, fin] of Object.entries(got)) {
            const r = this.feed.avg(a, t - 60000, t);
            if (r && r.covered && r.n >= 5 && r.avg > 0 && !this.basisDone.has(`${t}:${a}`)) {
              this.basisDone.add(`${t}:${a}`);
              this.basis[a].push(fin / r.avg - 1);
              if (this.basis[a].length > 12) this.basis[a].shift();
            }
          }
        }
      }
      for (const w of this.windows.values()) if (!(w.oraclePtb > 0)) { const f = this.finals.get(w.startMs); if (f && f[w.asset] > 0) w.oraclePtb = f[w.asset]; }
      await sleep(10000, signal);
    }
  }

  async councilLoop(signal) {
    while (!signal.aborted) {
      const t0 = performance.now?.() ?? Date.now();
      try { this.councilPass(this.now()); } catch (e) { console.error(e); this.status.engine = `council error: ${e.message}`; }
      this.lastPassMs = Math.round((performance.now?.() ?? Date.now()) - t0);
      this.passes++;
      await sleep(1500, signal);
    }
  }

  async paperLoop(signal) {
    while (!signal.aborted) {
      await this.paper.monitor().catch(() => {});
      await this.scoreResolved().catch(() => {});
      await sleep(5000, signal);
    }
  }

  // ------------------------------------------------------------ analysis

  basisFor(asset) { const b = median(this.basis[asset] || []); return b == null ? 0 : b; }

  // The window's price to beat, in Coinbase units, with where it came from and how sure we are of it.
  refFor(w) {
    const oracle = w.oraclePtb > 0 ? w.oraclePtb : null;
    if (w.recorded == null && this.feed && this.now() >= w.startMs + 2000) {
      const rec = this.feed.avg(w.asset, w.startMs - 60000, w.startMs);
      w.recorded = rec && rec.covered && rec.n >= 3 && rec.avg > 0 ? rec.avg : 0;  // 0 = we weren't recording then
    }
    if (w.recorded > 0) {
      return { value: w.recorded, src: "recorded", sdBp: STRIKE_SD_BP.recorded, oracle, display: fmtPrice(oracle || w.recorded * (1 + this.basisFor(w.asset))) };
    }
    if (oracle) return { value: oracle / (1 + this.basisFor(w.asset)), src: "oracle", sdBp: STRIKE_SD_BP.oracle, oracle, display: fmtPrice(oracle) };
    const cav = this.feed ? this.feed.candleAvg(w.asset, w.startMs - 60000) : null;
    if (cav > 0 && this.now() >= w.startMs + 5000) return { value: cav, src: "candle", sdBp: STRIKE_SD_BP.candle, oracle: null, display: fmtPrice(cav * (1 + this.basisFor(w.asset))) };
    return null;
  }

  analyze(w, now) {
    const f = this.feed;
    const started = now >= w.startMs;
    const remSec = (w.endMs - now) / 1000;
    w.ref = started ? this.refFor(w) : null;
    const spot = f.last(w.asset);
    const sigma = f.sigma(w.asset, 60), sigma10 = f.sigma(w.asset, 10);
    let model = null;
    if (started && w.ref && spot && sigma) {
      const tail = remSec < 60 ? f.avg(w.asset, w.endMs - 60000, now) : null;
      model = modelUp({ ref0: w.ref.value, spot: spot.price, sigma, remSec, tailAvg: tail && tail.covered ? tail.avg : null, strikeSdBp: w.ref.sdBp });
    }
    const mv10 = f.moveBp(w.asset, 10000);
    const a = {
      started, remSec, elapsedFrac: Math.max(0, Math.min(1, (now - w.startMs) / (w.endMs - w.startMs))),
      spot, sigma, sigma10, model,
      altMoveBp: started ? f.altMoveBp(w.asset, w.startMs) : null,
      mom30Bp: f.moveBp(w.asset, 30000),
      jumpSigma: mv10 != null && sigma ? Math.abs(mv10) / (sigma * 1e4 * Math.sqrt(10)) : null,
    };
    const trail = this.trails.get(w.id) || [];
    if (model) {
      trail.push({ t: now, pUp: model.pUp, mid0: w.m.mid(0) });
      while (trail.length && now - trail[0].t > 60000) trail.shift();
      this.trails.set(w.id, trail);
    }
    a.trail = trail;
    return a;
  }

  trackSummary() {
    const s = this.calib.filter((r) => r.winner != null).slice(-200);
    if (!s.length) return { n: 0, model: null, market: null };
    const br = (k) => s.reduce((t, r) => t + (r[k] - (r.winner === 0 ? 1 : 0)) ** 2, 0) / s.length;
    return { n: s.length, model: br("pUp"), market: br("mid0") };
  }

  acctView() {
    const snap = this.paper.snapshot();
    const held = new Set(Object.values(this.paper.acct.positions).map((p) => p.marketId));
    return { equity: snap.equity, cash: this.paper.acct.cash, open: Object.keys(this.paper.acct.positions).length, maxPositions: this.paper.settings.maxPositions, heldMarkets: held };
  }

  councilPass(now) {
    if (!this.feed) return;
    const track = this.trackSummary();
    const acct = this.acctView();
    const live = [...this.windows.values()].filter((w) => w.endMs > now - 2000 && w.startMs - 120000 <= now);
    const analyses = new Map();
    for (const w of live) { const a = this.analyze(w, now); a.track = track; analyses.set(w.id, a); }

    // ladder groups: same asset, same end, both open
    const groups = new Map();
    for (const w of live) if (analyses.get(w.id).started && w.ref) {
      const k = `${w.asset}|${w.endMs}`;
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(w);
    }
    this.ladders = [];
    for (const [k, g] of groups) {
      const pairs = g.length > 1 ? ladderPairs(g) : [];
      for (const w of g) { const a = analyses.get(w.id); a.ladderGroup = g; a.ladder = pairs; }
      if (g.length > 1) this.ladders.push({ key: k, group: g, pairs });
    }

    const fresh = [], dirCands = [];
    for (const w of live) {
      const a = analyses.get(w.id);
      const sides = [0, 1].map((side) => {
        const x = sideContext(w, a, side, acct, this.rules, now);
        const res = runSprintCouncil(w, x);
        return { side, x, res };
      });
      // the side the council likes best: no veto first, then edge, then votes
      sides.sort((p, q) => (p.res.vetoedBy.length > 0) - (q.res.vetoedBy.length > 0) || (q.x.edge ?? -9) - (p.x.edge ?? -9) || q.res.buy - p.res.buy);
      a.sides = sides;
      for (const v of sides[0].res.votes) { const t = this.tally[v.agent] || (this.tally[v.agent] = {}); t[v.stance] = (t[v.stance] || 0) + 1; }

      // score the model against the market once per window, at the half-way mark
      if (a.model && a.elapsedFrac >= 0.5 && w.m.mid(0) != null && !this.calib.find((r) => r.id === w.id)) {
        this.calib.push({ id: w.id, asset: w.asset, period: w.periodMin, t: now, endMs: w.endMs, pUp: a.model.pUp, mid0: w.m.mid(0), winner: null });
      }

      if (!a.started) continue;
      // directional trade
      const best = sides[0];
      const kind = decide(best.res, best.x, this.rules, { explore: this.paper.settings.aggressive });
      if (kind && now - (this.cooldown.get(`${w.id}:dir`) || 0) > 10 * 60000) dirCands.push({ w, a, best, kind });
      // complement set inside one window
      const comp = complementCost(w.m);
      if (comp != null && comp < 1 - this.rules.min_arb_edge && this.legsTradeable([{ w, side: 0 }, { w, side: 1 }], now) && this.cooldownOk(`${w.id}:set`, now, 60000)) {
        fresh.push(this.makeArb(`${ASSETS[w.asset].name} ${periodLabel(w.periodMin)} Up+Down set`, [{ w, side: 0 }, { w, side: 1 }], comp, now, "complement"));
      }
    }
    // strike-ladder arbitrage
    for (const L of this.ladders) {
      const p = L.pairs[0];
      if (!p || p.edge < this.rules.min_arb_edge) continue;
      if (!this.legsTradeable(p.legs, now)) continue;
      if (!this.cooldownOk(`ladder:${p.low.id}:${p.high.id}`, now, 60000)) continue;
      fresh.push(this.makeArb(`${ASSETS[p.low.asset].name} strike ladder · ${periodLabel(p.low.periodMin)} Up + ${periodLabel(p.high.periodMin)} Down`, p.legs, p.cost, now, "ladder", p));
    }
    // Directional trades, best edge first. When auto-trading, only as many as the cash and position slots allow, so
    // one pass never fires orders the account can't fill.
    const auto = this.paper.settings.autoTrade;
    let cashLeft = acct.cash * 0.98, slots = acct.maxPositions - acct.open;
    for (const s of fresh) cashLeft -= Math.min(s.stake || 0, cashLeft);
    dirCands.sort((p, q) => (q.best.x.edge - p.best.x.edge) || (q.best.res.buy - p.best.res.buy));
    for (const c of dirCands) {
      const sig = this.makeSignal(c.w, c.a, c.best, c.kind, now, auto ? cashLeft : acct.cash);
      const minCost = (c.w.m.minOrder || 5) * sig.ask;
      if (auto && (slots <= 0 || sig.stake < minCost || sig.stake > cashLeft + 1e-9)) continue;
      this.cooldown.set(`${c.w.id}:dir`, now);
      cashLeft -= sig.stake; slots--;
      fresh.push(sig);
    }
    this.analysis = analyses;
    if (fresh.length) {
      this.signals.push(...fresh);
      if (this.signals.length > 300) this.signals.splice(0, this.signals.length - 300);
      this.save();
      for (const s of fresh) {
        if (!this.paper.settings.autoTrade) continue;
        const w = this.windows.get(s.marketId);
        this.paper.onSignal(s, w && w.m).catch((e) => console.error(e));
      }
    }
  }

  legsTradeable(legs, now) {
    return legs.every(({ w, side }) => {
      const m = w.m, a = m.ask(side), b = m.bid(side);
      return now >= w.startMs && (w.endMs - now) / 1000 >= this.rules.min_sec_left && a != null && m.asks(side).length && !m.crossed(side) &&
        m.asks(side)[0].size >= (m.minOrder || 5) && (b == null || a - b <= this.rules.max_spread * 2);
    });
  }

  cooldownOk(key, now, ms) {
    const t = this.cooldown.get(key);
    if (t && now - t < ms) return false;
    this.cooldown.set(key, now);
    return true;
  }

  makeSignal(w, a, best, kind, now, cash) {
    const { x, res, side } = best;
    const minCost = (w.m.minOrder || 5) * x.ask * 1.03;
    const stake = Math.min(cash, Math.max(x.stake || 0, minCost));
    const buys = res.votes.filter((v) => v.stance === "BUY").sort((p, q) => q.strength - p.strength);
    const dissent = res.votes.filter((v) => v.stance === "AVOID" || (v.stance === "HOLD" && v.strength >= 0.4));
    return {
      id: `${w.id}:${side}:${now}`, created: now, kind, marketId: w.id, asset: w.asset, period: w.periodMin,
      question: w.title, eventTitle: w.title, side, outcome: side === 0 ? "Up" : "Down",
      ask: x.ask, fee_per_share: x.fee, p_model: x.P, edge: x.edge, agree: res.buy, total_agents: SPRINT_AGENTS.length,
      stake, end_ms: w.endMs, start_ms: w.startMs, strike: w.ref ? w.ref.display : null, strike_src: w.ref ? w.ref.src : null,
      spot: a.spot ? a.spot.price : null, z: a.model ? a.model.z : null,
      headline: `BUY ${ASSETS[w.asset].name} ${side === 0 ? "Up" : "Down"} @ ${(x.ask * 100).toFixed(1)}¢ — model ${(x.P * 100).toFixed(1)}%, ${Math.round(a.remSec)}s left`,
      reasons: buys.slice(0, 10).map((v) => ({ agent: v.name, reason: v.reason })),
      cautions: dissent.slice(0, 5).map((v) => ({ agent: v.name, stance: v.stance, reason: v.reason })),
      url: w.url, outcome_result: null,
    };
  }

  makeArb(title, legs, cost, now, sub, pair = null) {
    const first = legs[0].w;
    return {
      id: `arb:${legs.map((l) => `${l.w.id}.${l.side}`).join("+")}:${now}`, created: now, kind: "arbitrage", sub,
      marketId: first.id, asset: first.asset, question: title, eventTitle: title,
      legs: legs.map((l) => ({ marketId: l.w.id, side: l.side, title: l.w.title, outcome: l.side === 0 ? "Up" : "Down", ask: l.w.m.ask(l.side), strike: l.w.ref ? l.w.ref.display : null })),
      arb: { cost, edge: 1 - cost, members: legs.length }, stake: this.paper.settings.arbMaxUsd,
      gap_bp: pair ? pair.gapBp : null, end_ms: first.endMs,
      headline: `${title}: ${(cost * 100).toFixed(1)}¢ for a guaranteed $1+`,
      reasons: sub === "ladder"
        ? [{ agent: "Strike Ladder Agent", reason: `both windows settle on the same Chainlink number at ${new Date(first.endMs).toLocaleTimeString()}; strikes ${legs.map((l) => l.w.ref.display).join(" < ")}` },
          { agent: "Strike Ladder Agent", reason: "Up on the lower strike + Down on the higher strike: at least one always pays $1, both pay if the final lands between" }]
        : [{ agent: "Binary Complement", reason: "Up and Down together always pay exactly $1" }],
      url: first.url, outcome_result: null,
    };
  }

  // ------------------------------------------------------------ scoring

  async scoreResolved() {
    const now = this.now();
    const pendingIds = new Set();
    for (const r of this.calib) if (r.winner == null && r.endMs < now - 30000 && now - r.endMs < 6 * 3.6e6) pendingIds.add(r.id);
    for (const s of this.signals) if (!s.outcome_result && s.end_ms < now - 30000 && now - s.end_ms < 6 * 3.6e6) for (const id of (s.legs ? s.legs.map((l) => l.marketId) : [s.marketId])) pendingIds.add(id);
    let polled = 0;
    for (const id of pendingIds) {
      if (this.resolved.has(id) || polled >= 6) continue;
      if (now - (this.checkedAt.get(id) || 0) < 30000) continue;
      this.checkedAt.set(id, now);
      polled++;
      const m = await this.client.market(id).catch(() => null);
      if (m && m.resolved && m.winner != null) this.resolved.set(id, m.winner);
    }
    let changed = false;
    for (const r of this.calib) if (r.winner == null && this.resolved.has(r.id)) { r.winner = this.resolved.get(r.id); changed = true; }
    for (const s of this.signals) {
      if (s.outcome_result) continue;
      if (s.legs) {
        if (!s.legs.every((l) => this.resolved.has(l.marketId))) continue;
        const payout = s.legs.reduce((t, l) => t + (this.resolved.get(l.marketId) === l.side ? 1 : 0), 0);
        s.outcome_result = { status: payout >= 1 ? "win" : "loss", payout, net_per_set: payout - s.arb.cost };
      } else {
        if (!this.resolved.has(s.marketId)) continue;
        const win = this.resolved.get(s.marketId) === s.side;
        s.outcome_result = { status: win ? "win" : "loss", net_per_share: (win ? 1 : 0) - s.ask - s.fee_per_share, resolved_to: this.resolved.get(s.marketId) === 0 ? "Up" : "Down" };
      }
      changed = true;
    }
    if (changed) this.save();
  }

  // ------------------------------------------------------------ manual demo trades

  async paperBuy(marketId, side) {
    const w = this.windows.get(marketId);
    if (!w) return;
    await this.paper.buy(w.m, side, this.paper.tradeUsd(), null);
  }
  async paperSell(marketId, side) { await this.paper.sellAll(marketId, side, "manual sell"); }

  // ------------------------------------------------------------ dashboard state

  state() {
    const now = this.now();
    const rows = [];
    for (const w of [...this.windows.values()].sort((p, q) => p.endMs - q.endMs || p.asset.localeCompare(q.asset))) {
      if (w.endMs <= now) continue;  // ended windows settle in the background; the table shows what can still trade
      const a = this.analysis.get(w.id) || {};
      const best = a.sides ? a.sides[0] : null;
      rows.push({
        id: w.id, asset: w.asset, name: ASSETS[w.asset].name, period: periodLabel(w.periodMin), title: w.title, url: w.url,
        startMs: w.startMs, endMs: w.endMs, started: now >= w.startMs, remSec: (w.endMs - now) / 1000,
        ref: w.ref ? { display: w.ref.display, src: w.ref.src, sdBp: w.ref.sdBp } : null,
        spot: a.spot ? a.spot.price : null, moveBp: a.model ? a.model.x * 1e4 : null, z: a.model ? a.model.z : null,
        pUp: a.model ? a.model.pUp : null,
        up: { bid: w.m.bid(0), ask: w.m.ask(0) }, down: { bid: w.m.bid(1), ask: w.m.ask(1) }, liq: w.m.liq,
        best: best ? {
          side: best.side, edge: best.x.edge, P: best.x.P, ask: best.x.ask, buy: best.res.buy, avoid: best.res.avoid, hold: best.res.hold,
          vetoedBy: best.res.vetoedBy, votes: best.res.votes,
        } : null,
      });
    }
    const ladders = (this.ladders || []).map((L) => ({
      key: L.key, asset: L.group[0].asset, name: ASSETS[L.group[0].asset].name, endMs: L.group[0].endMs,
      windows: L.group.map((w) => ({ id: w.id, period: periodLabel(w.periodMin), strike: w.ref.display, src: w.ref.src, upAsk: w.m.ask(0), upBid: w.m.bid(0), downAsk: w.m.ask(1) }))
        .sort((p, q) => parseFloat(String(p.strike).replace(/,/g, "")) - parseFloat(String(q.strike).replace(/,/g, ""))),
      best: L.pairs[0] ? { cost: L.pairs[0].cost, edge: L.pairs[0].edge, low: periodLabel(L.pairs[0].low.periodMin), high: periodLabel(L.pairs[0].high.periodMin), gapBp: L.pairs[0].gapBp } : null,
    }));
    const active = [...this.signals].reverse().find((s) => !s.outcome_result && s.end_ms > now && s.kind !== "exploratory") || null;
    const scored = this.signals.filter((s) => s.outcome_result && !s.legs);
    const arbsScored = this.signals.filter((s) => s.outcome_result && s.legs);
    const sources = { ...(this.feed ? this.feed.status : { coinbase: "stopped" }), ...this.status };
    const started = rows.filter((r) => r.started);
    return {
      running: this.running, now, uptime_s: this.startedAt ? Math.round((now - this.startedAt) / 1000) : 0,
      rules: { ...this.rules }, sources,
      stats: {
        windows: rows.length, open: started.length, modeled: started.filter((r) => r.pUp != null).length,
        oracle_strikes: started.filter((r) => r.ref && r.ref.src === "oracle").length,
        ladders: ladders.length, passes: this.passes, pass_ms: this.lastPassMs,
        spot_age: this.feed && this.feed.last("btc") ? this.feed.last("btc").ageMs / 1000 : null,
      },
      rows, ladders, active_signal: active, signals: [...this.signals].reverse().slice(0, 60),
      track: this.trackSummary(),
      record: {
        directional: scored.length, wins: scored.filter((s) => s.outcome_result.status === "win").length,
        avg_net: scored.length ? scored.reduce((t, s) => t + s.outcome_result.net_per_share, 0) / scored.length : null,
        arbs: arbsScored.length, arb_net: arbsScored.reduce((t, s) => t + s.outcome_result.net_per_set, 0),
      },
      agents: SPRINT_AGENTS.map((a) => ({ id: a.id, name: a.name, group: a.group, description: a.description, veto: a.veto, tally: this.tally[a.id] || {} })),
      paper: this.paper.snapshot(),
    };
  }
}

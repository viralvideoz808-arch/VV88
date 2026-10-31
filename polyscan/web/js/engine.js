// The engine behind the Start / Stop button, running inside the page.
//
// Start launches concurrent loops:
//   events    Gamma: pages of active, liquid events + their markets (cheap, no order book)
//   books     CLOB: order books for the top candidates and every complete-negRisk member (for depth and arbitrage)
//   history   CLOB: price history for the strongest candidates
//   council   all 25 agents vote on each candidate market (both outcome sides) and on each risk-free set
//   paper     value the demo account's positions and settle resolved markets
// Stop aborts them all.

import {
  AGENTS, AGENTS_BY_ID, Market, arbForEvent, buildSignal, compareRank, defaultRules, runCouncil, scoreSignal,
} from "./core.js";
import { PolyClient, attachBooks, sleep } from "./sources.js";
import { PaperAccount } from "./paper.js";

const CANDIDATE_CAP = 40;   // markets that get full books + council each pass
const WATCH_CAP = 4000;

function storageGet(key) { try { return JSON.parse(localStorage.getItem(key) || "null"); } catch { return null; } }
function storageSet(key, v) { try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* ignore */ } }

export class Engine {
  constructor() {
    this.rules = { ...defaultRules(), ...(storageGet("polyscan.rules") || {}) };
    this.running = false;
    this.started_at = null;
    this.markets = new Map();   // id -> Market
    this.events = new Map();    // id -> parsed event
    this.marketEvent = new Map(); // marketId -> eventId
    this.signals = (storageGet("polyscan.signals") || []).slice(-300);
    this.status = {};
    this.analyzed = new Set();
    this.events_seen = 0;
    this.passes = 0;
    this.last_pass_ms = 0;
    this.agent_tally = {};
    this.abort = null;
    this.client = null;
    this.paper = new PaperAccount({ nowMs: () => Date.now(), getMarket: (id) => this.markets.get(id) });
  }

  clock() { return Date.now(); }

  // ------------------------------------------------------------ lifecycle

  start() {
    if (this.running) return;
    this.running = true;
    this.abort = new AbortController();
    const signal = this.abort.signal;
    this.markets = new Map();
    this.events = new Map();
    this.marketEvent = new Map();
    this.analyzed = new Set();
    this.events_seen = 0;
    this.passes = 0;
    this.client = new PolyClient(signal);
    this.paper.client = this.client;
    this.status = { gamma: "connecting" };
    const loops = [
      this.eventsLoop(signal), this.booksLoop(signal), this.historyLoop(signal),
      this.councilLoop(signal), this.paperLoop(signal),
    ];
    this.started_at = this.clock();
    for (const p of loops) p.catch((e) => { if (!signal.aborted) { console.error("loop crashed", e); this.status.engine = `a loop crashed: ${e.message || e}`; } });
  }

  stop() {
    if (!this.running) return;
    this.running = false;
    this.abort.abort(new DOMException("stopped", "AbortError"));
    for (const k of Object.keys(this.status)) this.status[k] = "stopped";
    this.saveSignals();
  }

  setRules(patch) {
    Object.assign(this.rules, patch);
    storageSet("polyscan.rules", { min_buy_votes: this.rules.min_buy_votes, min_edge: this.rules.min_edge, min_arb_edge: this.rules.min_arb_edge, max_resolve_hours: this.rules.max_resolve_hours });
  }

  // ------------------------------------------------------------ data loops

  async eventsLoop(signal) {
    while (!signal.aborted) {
      // Events resolving within the fast window, soonest first (a buffer past the window so nothing is missed at the
      // edge as time passes). The map is kept across sweeps so order books survive; stale markets are pruned below.
      const endMaxIso = new Date(this.clock() + (this.rules.max_resolve_hours + 6) * 3.6e6).toISOString();
      const seen = new Set();
      for (let page = 0; page < 8 && !signal.aborted; page++) {
        const evs = await this.client.events(100, page * 100, endMaxIso);
        if (!evs.length) break;
        this.events_seen += this.absorb(evs, seen);
        this.status.gamma = `ok (${this.events.size} events, ${this.markets.size} markets in the fast window)`;
        await sleep(300, signal);
      }
      this.prune(seen);
      await sleep(25000, signal);
    }
  }

  absorb(evs, seen) {
    let n = 0;
    for (const ev of evs) {
      if (!ev.id) continue;
      if (!this.events.has(ev.id)) n++;
      this.events.set(ev.id, ev);
      for (const m of ev.markets) {
        if (!m.id) continue;
        seen && seen.add(m.id);
        const prev = this.markets.get(m.id);
        if (prev) { m.books = prev.books; m.booksAt = prev.booksAt; m.history = prev.history; m.historyAt = prev.historyAt; }
        if (this.markets.size < WATCH_CAP || prev) this.markets.set(m.id, m);
        this.marketEvent.set(m.id, ev.id);
      }
    }
    return n;
  }

  // Drop markets that are clearly out of the fast window or long ended, unless the demo account holds them or an
  // open signal references them. Keeps books for everything still in play.
  prune(seen) {
    const now = this.clock(), maxH = this.rules.max_resolve_hours;
    const keep = new Set(Object.values(this.paper.acct.positions).map((p) => p.marketId));
    for (const s of this.signals) if (!s.outcome_result) keep.add(s.marketId);
    for (const [id, m] of this.markets) {
      if (seen.has(id) || keep.has(id)) continue;
      const h = m.hoursToEnd(now);
      if (h == null || h < -1 || h > maxH + 12) {
        this.markets.delete(id); this.marketEvent.delete(id);
      }
    }
    // forget events with no remaining markets
    for (const [eid, ev] of this.events) if (!ev.markets.some((m) => this.markets.has(m.id))) this.events.delete(eid);
  }

  // The liquid, fast-resolving, two-sided markets worth a full directional council. Only markets that resolve within
  // the fast window, and not near-resolved longshots (mid outside 2c..98c). Sooner resolution ranks higher.
  candidates(now) {
    const out = [];
    const maxH = this.rules.max_resolve_hours;
    for (const m of this.markets.values()) {
      if (m.closed || !m.active || !m.enableOrderBook || !m.acceptingOrders) continue;
      if (m.liq < 2000 && m.vol24 < 2000) continue;
      if (m.yesMid == null || m.yesMid < 0.02 || m.yesMid > 0.98) continue;
      const h = m.hoursToEnd(now);
      if (h == null || h < 0.08 || h > maxH) continue; // fast only; not already ending this minute
      const soonBonus = 2 * (1 - h / maxH); // resolves sooner -> ranks higher
      const score = Math.log10(Math.max(m.vol24, m.liq, 1)) - (m.spread || 0.1) * 5 + soonBonus;
      out.push([score, m]);
    }
    out.sort((a, b) => b[0] - a[0]);
    return out.slice(0, CANDIDATE_CAP).map((x) => x[1]);
  }

  // Complete (mutually-exclusive and exhaustive) negRisk events that resolve within the fast window — the arbitrage
  // universe, soonest first.
  arbEvents(limit = 30) {
    const now = this.clock(), maxH = this.rules.max_resolve_hours;
    return [...this.events.values()].filter((e) => {
      if (!e.complete || e.n < 2 || e.n > 20) return false;
      const hs = e.members.map((m) => m.hoursToEnd(now)).filter((h) => h != null);
      const h = hs.length ? Math.min(...hs) : null;
      return h != null && h > 0 && h <= maxH;
    }).sort((a, b) => b.vol24 - a.vol24).slice(0, limit);
  }

  async booksLoop(signal) {
    while (!signal.aborted) {
      const now = this.clock();
      // directional candidates (both tokens) + every member of the top arbitrage events (outcome-0 token each)
      const toBook = new Map();
      for (const m of this.candidates(now)) toBook.set(m.id, { m, both: true });
      for (const ev of this.arbEvents()) for (const mm of ev.members) if (!toBook.has(mm.id)) toBook.set(mm.id, { m: mm, both: false });
      const entries = [...toBook.values()];
      const tokenIds = [];
      for (const { m, both } of entries) { tokenIds.push(m.tokenIds[0]); if (both) tokenIds.push(m.tokenIds[1]); }
      if (tokenIds.length) {
        const books = await this.client.books(tokenIds.slice(0, 200));
        const t = this.clock();
        for (const { m } of entries) attachBooks(m, books, t);
      }
      const err = this.client.errors.clob;
      this.status.clob = err ? `error: ${err}` : `ok (${entries.length} books/pass)`;
      await sleep(4000, signal);
    }
  }

  async historyLoop(signal) {
    while (!signal.aborted) {
      const cands = this.candidates(this.clock()).slice(0, 12);
      for (const m of cands) {
        if (signal.aborted) break;
        if (m.history && this.clock() - m.historyAt < 120000) continue;
        const h = await this.client.history(m.tokenIds[0]);
        if (h.length) { m.history = h; m.historyAt = this.clock(); }
      }
      await sleep(5000, signal);
    }
  }

  // ------------------------------------------------------------ council

  async councilLoop(signal) {
    while (!signal.aborted) {
      this.councilPass();
      await sleep(3000, signal);
    }
  }

  councilPass() {
    const t0 = performance.now?.() ?? Date.now();
    const now = this.clock();
    const tally = Object.fromEntries(AGENTS.map((a) => [a.id, {}]));
    const addVotes = (vs) => { for (const v of vs) tally[v.agent][v.stance] = (tally[v.agent][v.stance] || 0) + 1; };
    const fresh = [];
    const best = new Map(); // marketId -> best verdict (for the leaderboard)

    // ---- arbitrage: one signal per complete event whose full set fills below $1
    for (const ev of this.arbEvents()) {
      if (!ev.members.every((mm) => mm.books)) continue;
      const arb = arbForEvent(ev);
      ev._arb = arb;
      if (!arb || arb.edge < this.rules.min_arb_edge) continue;
      const m = ev.members[0];
      const ctx = { now, side: 0, event: { augmented: ev.augmented, sumMid: ev.sumMid, n: ev.n, members: ev.members, siblings: ev.members.slice(1), arb }, tradeUsd: this.paper.tradeUsd() };
      const verdict = runCouncil(m, ctx, this.rules);
      addVotes(verdict.votes);
      if (verdict.is_signal && this.arbCooldownOk(ev.id, now)) {
        const sig = buildSignal(m, verdict, this.rules, now, ctx);
        this.signals.push(sig); fresh.push(sig);
        this.paper.onSignal(sig, m).catch((e) => console.error("paper arb", e));
      }
    }

    // ---- directional: each candidate market, each side, on its own merits
    for (const m of this.candidates(now)) {
      if (!m.books) continue;
      const ev = this.events.get(this.marketEvent.get(m.id));
      const eventBase = ev ? { augmented: ev.augmented, sumMid: ev.sumMid, n: ev.n, members: ev.members, siblings: ev.members.filter((x) => x.id !== m.id), arb: null } : null;
      for (const side of [0, 1]) {
        const ctx = { now, side, event: eventBase, tradeUsd: this.paper.tradeUsd() };
        const verdict = runCouncil(m, ctx, this.rules);
        this.analyzed.add(m.id + ":" + side);
        addVotes(verdict.votes);
        const cur = best.get(m.id);
        if (!cur || compareRank(verdict, cur) < 0) { best.set(m.id, verdict); verdict._ctx = ctx; }
        if (verdict.is_signal && this.cooldownOk(m, side, now)) {
          const sig = buildSignal(m, verdict, this.rules, now, ctx);
          this.signals.push(sig); fresh.push(sig);
          this.paper.onSignal(sig, m).catch((e) => console.error("paper trade", e));
        }
      }
      m.verdicts = { best: best.get(m.id) };
    }
    this._lastBest = best;
    this._arbEvents = this.arbEvents();
    this.settleSignals(now);
    this.agent_tally = tally;
    this.passes++;
    this.last_pass_ms = (performance.now?.() ?? Date.now()) - t0;
    if (fresh.length) this.saveSignals();
    return fresh;
  }

  arbCooldownOk(eventId, now) {
    const recent = this.signals.slice(-80).find((s) => s.kind === "arbitrage" && s.arb && s.eventId === eventId);
    return !recent || now - recent.created > 10 * 60 * 1000;
  }

  cooldownOk(m, side, now) {
    const recent = this.signals.slice(-60).find((s) => s.marketId === m.id && s.side === side);
    return !recent || now - recent.created > 20 * 60 * 1000;
  }

  // Score directional signals against the market's resolution once it closes.
  settleSignals(now) {
    let changed = false;
    for (const sig of this.signals) {
      if (sig.outcome_result || sig.kind === "arbitrage") continue;
      const m = this.markets.get(sig.marketId);
      if (m && m.resolved) { sig.outcome_result = scoreSignal(sig, m); changed = true; }
    }
    if (changed) this.saveSignals();
  }

  async paperLoop(signal) {
    while (!signal.aborted) {
      try { await this.paper.monitor(); } catch (e) { console.error("paper monitor", e); }
      await sleep(5000, signal);
    }
  }

  saveSignals() { storageSet("polyscan.signals", this.signals.slice(-300)); }

  // ------------------------------------------------------------ UI state

  trackRecord() {
    const scored = this.signals.filter((s) => s.outcome_result && s.kind !== "arbitrage");
    const wins = scored.filter((s) => s.outcome_result.status === "win").length;
    const nets = scored.map((s) => s.outcome_result.net_per_share);
    const arbs = this.signals.filter((s) => s.kind === "arbitrage").length;
    return {
      signals: this.signals.length, arbs, scored: scored.length, wins,
      win_rate: scored.length ? Math.round(1000 * wins / scored.length) / 10 : null,
      avg_net_per_share: nets.length ? Math.round(nets.reduce((a, b) => a + b, 0) / nets.length * 1000) / 1000 : null,
    };
  }

  marketRow(m, now) {
    const v = m.verdicts && m.verdicts.best;
    const side = v ? v.side : 0;
    return {
      id: m.id, question: m.question, event: m.eventTitle, outcome: m.outcomes[side], image: m.image,
      side, ask: m.ask(side), mid: m.mid(side), spread: m.spreadOf(side),
      vol24: m.vol24, liq: m.liq, hours: m.hoursToEnd(now),
      buy_votes: v ? v.buy_votes : 0, avoid_votes: v ? v.avoid_votes : 0, vetoed: v ? v.vetoed_by.length > 0 : false,
      edge: v ? v.edge : null, arb: v ? v.arb : false, conviction: v ? v.conviction : 0,
      has_book: !!m.books, votes: v ? v.votes : [], veto_reason: v && v.vetoed_by.length ? (v.votes.find((x) => x.agent === v.vetoed_by[0]) || {}).reason : "",
    };
  }

  state() {
    const now = this.clock();
    const rows = this._lastBest ? [...this._lastBest.keys()].map((id) => this.markets.get(id)).filter(Boolean).map((m) => this.marketRow(m, now)) : [];
    rows.sort((a, b) => (b.arb - a.arb) || ((b.edge ?? -9) - (a.edge ?? -9)) || (b.buy_votes - a.buy_votes));
    let active = null;
    for (let i = this.signals.length - 1; i >= 0; i--) {
      const s = this.signals[i];
      if (!s.outcome_result) { active = { ...s }; const m = this.markets.get(s.marketId); if (m) { active.now_ask = m.ask(s.side); active.now_mid = m.mid(s.side); } break; }
    }
    return {
      running: this.running, now,
      uptime_s: this.running && this.started_at ? Math.floor((now - this.started_at) / 1000) : 0,
      stats: {
        events: this.events.size, markets: this.markets.size,
        with_books: [...this.markets.values()].filter((m) => m.books).length,
        analyzed: this.analyzed.size, passes: this.passes, last_pass_ms: Math.round(this.last_pass_ms * 10) / 10,
        complete_sets: this.arbEvents(99).length,
        max_resolve_hours: this.rules.max_resolve_hours,
      },
      sources: { ...this.status },
      rules: { ...this.rules },
      agents: AGENTS.map((a) => ({ id: a.id, name: a.name, group: a.group, description: a.description, veto: a.veto, tally: this.agent_tally[a.id] || {} })),
      candidates: rows.slice(0, 25),
      arb_watch: (this._arbEvents || []).map((ev) => {
        const arb = ev.members.every((mm) => mm.books) ? arbForEvent(ev) : null;
        const sumAsk = ev.members.reduce((s, mm) => s + (mm.ask(0) || 0), 0);
        return { id: ev.id, title: ev.title, outcomes: ev.n, vol24: ev.vol24, sum_ask: sumAsk, cost: arb ? arb.cost : null, edge: arb ? arb.edge : null, priced: !!arb };
      }).filter((x) => x.outcomes >= 2).sort((a, b) => (b.edge ?? -9) - (a.edge ?? -9)).slice(0, 10),
      active_signal: active,
      signals: this.signals.slice(-40).reverse(),
      track_record: this.trackRecord(),
      paper: this.paper.snapshot(),
    };
  }

  // manual demo controls
  paperBuy(marketId, side) { const m = this.markets.get(marketId); if (m) this.paper.buy(m, side, this.paper.tradeUsd()).catch((e) => console.error(e)); }
  paperSell(marketId, side) { this.paper.sellAll(marketId, side, "manual").catch((e) => console.error(e)); }
}

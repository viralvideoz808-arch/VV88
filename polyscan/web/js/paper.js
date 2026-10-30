// The demo account: paper trading on real Polymarket order books.
//
// A position is a number of shares of one outcome token. It is bought by walking the real ask ladder and paying
// Polymarket's taker fee. Unlike a memecoin, a prediction market resolves definitively: at resolution each share of
// the winning outcome pays exactly $1 and each losing share pays $0, with no fee. So a signal's result is a fact,
// not an estimate, which gives a genuinely measured track record. Positions are normally held to resolution; the
// account can also exit early by selling into the bid (paying the taker fee again).
//
// For an arbitrage signal the account buys one share of every outcome in the set, which is guaranteed to return
// exactly $1 per set at resolution for the sub-$1 cost paid.

import { feePerShare, usd, walkAsks, walkBids } from "./core.js";

export const START_USD = 100;
export const DEFAULT_SETTINGS = {
  autoTrade: true,
  sizePct: 20,          // % of free cash per directional trade
  maxPositions: 6,
  arbMaxUsd: 25,        // cap per risk-free set purchase
  holdToResolution: true,
};

function storageGet(key) { try { return JSON.parse(localStorage.getItem(key) || "null"); } catch { return null; } }
function storageSet(key, v) { try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* ignore */ } }

export class PaperAccount {
  constructor({ nowMs = () => Date.now(), getMarket = () => null, client = null, storageKey = "polyscan.paper" } = {}) {
    this.nowMs = nowMs;
    this.getMarket = getMarket;
    this.client = client;
    this.storageKey = storageKey;
    this.pending = new Set();
    this.load();
  }

  load() {
    const s = storageGet(this.storageKey);
    this.settings = { ...DEFAULT_SETTINGS, ...(s && s.settings) };
    this.acct = s && s.acct ? s.acct : {
      createdAt: this.nowMs(), startUsd: START_USD, cash: START_USD,
      positions: {}, trades: [], totals: { fees: 0, realized: 0, invested: 0 },
    };
  }
  save() { storageSet(this.storageKey, { acct: this.acct, settings: this.settings }); }
  setSettings(patch) { Object.assign(this.settings, patch); this.save(); }
  reset() { localStorage.removeItem(this.storageKey); this.load(); this.save(); }

  tradeUsd() { return Math.max(5, this.acct.cash * this.settings.sizePct / 100); }
  freeCash() { return this.acct.cash; }
  log(e) { this.acct.trades.unshift({ t: this.nowMs(), ...e }); if (this.acct.trades.length > 200) this.acct.trades.length = 200; }
  posKey(marketId, side) { return `${marketId}:${side}`; }

  async onSignal(sig, m) {
    if (!this.settings.autoTrade) return;
    if (sig.kind === "arbitrage" && sig.arb) return this.buyArb(sig, m);
    if (Object.keys(this.acct.positions).length >= this.settings.maxPositions) {
      this.log({ side: "BUY", status: "skipped", question: sig.question, outcome: sig.outcome, reason: `already holding ${this.settings.maxPositions} positions` });
      this.save(); return;
    }
    return this.buy(m, sig.side, this.tradeUsd(), sig);
  }

  // Buy one outcome token with up to `budget` USD, walking the real ask ladder.
  async buy(m, side, budget, sig = null) {
    const key = this.posKey(m.id, side);
    if (this.pending.has(key)) return;
    const base = { side: "BUY", marketId: m.id, pside: side, question: m.question, outcome: m.outcomes[side] };
    if (budget < 1 || budget > this.acct.cash + 1e-9) { this.log({ ...base, status: "skipped", reason: "not enough cash" }); this.save(); return; }
    const asks = m.asks(side);
    if (!asks.length) { this.log({ ...base, status: "skipped", reason: "no asks on the book" }); this.save(); return; }
    this.pending.add(key);
    try {
      const fill = walkAsks(asks, budget);
      if (fill.shares < (m.minOrder || 5)) { this.log({ ...base, status: "skipped", reason: `fill ${fill.shares.toFixed(1)} < min order ${m.minOrder}` }); return; }
      const fee = feePerShare(m, fill.avgPrice) * fill.shares;
      const cost = fill.spent + fee;
      if (cost > this.acct.cash + 1e-9) { this.log({ ...base, status: "skipped", reason: "cash moved; fill too large" }); return; }
      this.acct.cash -= cost;
      this.acct.totals.fees += fee; this.acct.totals.invested += fill.spent;
      const p = this.acct.positions[key];
      if (p) { p.shares += fill.shares; p.cost += cost; }
      else this.acct.positions[key] = {
        key, marketId: m.id, side, tokenId: m.tokenIds[side], question: m.question, event: m.eventTitle,
        outcome: m.outcomes[side], shares: fill.shares, cost, avg: cost / fill.shares, opened: this.nowMs(),
        kind: sig ? sig.kind : "manual", arbGroup: null,
      };
      this.log({ ...base, status: "filled", shares: fill.shares, avg_price: fill.avgPrice, fee, cost,
        reason: sig ? (sig.kind === "arbitrage" ? "risk-free set" : `signal ${sig.agree}/25, edge ${(sig.edge * 100).toFixed(1)}¢`) : "manual" });
      return this.acct.positions[key];
    } finally { this.pending.delete(key); this.save(); }
  }

  // Buy one share of every outcome in a risk-free set, sized to the budget cap.
  async buyArb(sig, anyMember) {
    const arb = sig.arb;
    const members = (arb.memberIds || []).map((id) => this.getMarket(id)).filter((m) => m && m.books && m.asks(0).length);
    if (members.length < (arb.members || 2) || members.length < 2) { this.log({ side: "ARB", status: "skipped", question: sig.eventTitle, reason: "set members not fully loaded" }); this.save(); return; }
    const perSet = members.reduce((s, m) => s + (m.ask(0) || 0), 0);
    if (perSet <= 0 || perSet >= 1) { this.log({ side: "ARB", status: "skipped", question: sig.eventTitle, reason: "no live sub-$1 set" }); this.save(); return; }
    const budget = Math.min(this.settings.arbMaxUsd, this.acct.cash);
    const sets = Math.floor(budget / perSet);
    if (sets < 1) { this.log({ side: "ARB", status: "skipped", question: sig.eventTitle, reason: "set too expensive for budget" }); this.save(); return; }
    let totalCost = 0, totalFee = 0, filledAll = true;
    const legs = [];
    for (const m of members) {
      const fill = walkAsks(m.asks(0), sets * (m.ask(0) || 1) * 1.5);
      const shares = Math.min(fill.shares, sets);
      if (shares < sets - 1e-6) { filledAll = false; }
      const px = fill.avgPrice || m.ask(0);
      const fee = feePerShare(m, px) * shares;
      totalCost += shares * px + fee; totalFee += fee;
      legs.push({ m, shares, px, fee });
    }
    if (!filledAll) { this.log({ side: "ARB", status: "skipped", question: sig.eventTitle, reason: "set not fully fillable at these asks" }); this.save(); return; }
    if (totalCost > this.acct.cash + 1e-9) { this.log({ side: "ARB", status: "skipped", question: sig.eventTitle, reason: "not enough cash for the set" }); this.save(); return; }
    this.acct.cash -= totalCost; this.acct.totals.fees += totalFee; this.acct.totals.invested += totalCost - totalFee;
    const group = `arb:${sig.id}`;
    for (const leg of legs) {
      const k = this.posKey(leg.m.id, 0) + ":" + group;
      this.acct.positions[k] = {
        key: k, marketId: leg.m.id, side: 0, tokenId: leg.m.tokenIds[0], question: leg.m.question,
        event: leg.m.eventTitle, outcome: leg.m.outcomes[0], shares: leg.shares, cost: leg.shares * leg.px + leg.fee,
        avg: leg.px, opened: this.nowMs(), kind: "arbitrage", arbGroup: group,
      };
    }
    this.log({ side: "ARB", status: "filled", question: sig.eventTitle, reason: `${sets} set(s) of ${members.length} outcomes for ${usd(totalCost)} → guaranteed ${usd(sets)} at resolution`,
      shares: sets, cost: totalCost, fee: totalFee, guaranteed: sets, edge: sets - totalCost });
    this.save();
  }

  async sellAll(marketId, side, reason) {
    const key = this.posKey(marketId, side);
    const p = this.acct.positions[key];
    if (!p || this.pending.has(key)) return;
    const m = this.getMarket(marketId);
    if (!m || !m.bids(side).length) { this.log({ side: "SELL", marketId, pside: side, status: "skipped", question: p.question, reason: "no bids to sell into" }); this.save(); return; }
    this.pending.add(key);
    try {
      const fill = walkBids(m.bids(side), p.shares);
      const fee = feePerShare(m, fill.avgPrice || 0) * fill.sold;
      const proceeds = fill.proceeds - fee;
      this.acct.cash += proceeds; this.acct.totals.fees += fee;
      const costShare = p.cost * (fill.sold / p.shares);
      this.acct.totals.realized += proceeds - costShare;
      this.log({ side: "SELL", marketId, pside: side, status: "filled", question: p.question, outcome: p.outcome,
        shares: fill.sold, avg_price: fill.avgPrice, proceeds, pnl: proceeds - costShare, reason });
      p.shares -= fill.sold; p.cost -= costShare;
      if (p.shares < 1e-6) delete this.acct.positions[key];
    } finally { this.pending.delete(key); this.save(); }
  }

  // Settle resolved markets and keep a live mark on open positions.
  async monitor() {
    let changed = false;
    for (const p of Object.values(this.acct.positions)) {
      let m = this.getMarket(p.marketId);
      if ((!m || !m.resolved) && this.client && this.nowMs() - (p._checkedAt || 0) > 60000) {
        p._checkedAt = this.nowMs();
        const fresh = await this.client.market(p.marketId).catch(() => null);
        if (fresh) m = fresh;
      }
      if (m && m.resolved && m.winner != null) {
        const payout = m.winner === p.side ? p.shares * 1 : 0;
        this.acct.cash += payout;
        this.acct.totals.realized += payout - p.cost;
        this.log({ side: "SETTLE", marketId: p.marketId, pside: p.side, status: m.winner === p.side ? "win" : "loss",
          question: p.question, outcome: p.outcome, proceeds: payout, pnl: payout - p.cost, reason: `resolved to ${m.outcomes[m.winner]}` });
        delete this.acct.positions[p.key];
        changed = true;
      }
    }
    if (changed) this.save();
  }

  snapshot() {
    const px = this.nowMs();
    let openValue = 0, openCost = 0;
    const positions = Object.values(this.acct.positions).map((p) => {
      const m = this.getMarket(p.marketId);
      const mark = m ? (m.mid(p.side) ?? m.bid(p.side) ?? p.avg) : p.avg;
      const value = p.shares * (mark ?? p.avg);
      openValue += value; openCost += p.cost;
      return {
        key: p.key, marketId: p.marketId, question: p.question, event: p.event, outcome: p.outcome, side: p.side,
        shares: Math.round(p.shares * 100) / 100, avg: p.avg, mark, value, cost: p.cost,
        pnl_pct: p.cost > 0 ? (value / p.cost - 1) * 100 : 0, kind: p.kind,
        resolves_in_h: m ? m.hoursToEnd(px) : null,
      };
    });
    const equity = this.acct.cash + openValue;
    const t = this.acct.totals;
    // settled directional / arb results
    const settles = this.acct.trades.filter((x) => x.side === "SETTLE");
    const wins = settles.filter((x) => x.status === "win").length;
    return {
      funded: true, start_usd: this.acct.startUsd, cash: this.acct.cash, open_value: openValue, open_cost: openCost,
      equity, pnl_usd: equity - this.acct.startUsd, pnl_pct: (equity / this.acct.startUsd - 1) * 100,
      settings: { ...this.settings },
      totals: { fees: t.fees, realized: t.realized, invested: t.invested },
      settled: settles.length, settled_wins: wins,
      positions: positions.sort((a, b) => b.value - a.value),
      trades: this.acct.trades.slice(0, 50),
    };
  }
}

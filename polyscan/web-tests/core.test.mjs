import assert from "node:assert/strict";
import { test } from "node:test";

import {
  AGENTS, AGENTS_BY_ID, AVOID, BUY, Market, arbForEvent, buildSignal, defaultRules, feePerShare,
  runCouncil, scoreSignal, vote, walkAsks, walkBids,
} from "../web/js/core.js";
import { parseBook, parseEvent, parseMarket } from "../web/js/sources.js";
import { NOW, ctx, fx, market, negriskEvent } from "./helpers.mjs";

test("25 agents, with veto agents on the tradeability gate", () => {
  assert.equal(AGENTS.length, 25);
  assert.equal(new Set(AGENTS.map((a) => a.id)).size, 25);
  assert.ok(AGENTS.filter((a) => a.veto).length >= 7);
});

test("fee math matches Polymarket's published schedule", () => {
  const m = new Market({ feesEnabled: true, feeRate: 0.05, feeExp: 1 });
  // $1.25 per 100 shares at 50c => 0.0125/share
  assert.ok(Math.abs(feePerShare(m, 0.5) - 0.0125) < 1e-9);
  assert.ok(Math.abs(feePerShare(m, 0.9) - 0.05 * 0.09) < 1e-9);
  assert.equal(feePerShare(new Market({ feesEnabled: false }), 0.5), 0); // makers / zero-fee markets
});

test("walking the ask and bid ladders", () => {
  const asks = [{ price: 0.60, size: 10 }, { price: 0.61, size: 10 }, { price: 0.62, size: 100 }];
  const f = walkAsks(asks, 20); // 10@.60=6, then 14 left -> 14/.61=22.95 shares
  assert.ok(f.shares > 32 && f.shares < 33);
  assert.ok(Math.abs(f.spent - 20) < 1e-6);
  assert.ok(f.avgPrice > 0.60 && f.avgPrice < 0.62);
  const s = walkBids([{ price: 0.58, size: 20 }, { price: 0.57, size: 100 }], 30);
  assert.ok(Math.abs(s.proceeds - (20 * 0.58 + 10 * 0.57)) < 1e-9);
});

test("parses a real resolved market and reads the winner", () => {
  const m = parseMarket(fx("resolved.json"));
  assert.ok(m.closed && m.resolved);
  assert.equal(m.winner, 0); // outcomePrices ["1","0"]
  assert.equal(m.outcomes[m.winner], JSON.parse(fx("resolved.json").outcomes)[0]);
});

test("parses a real complete negRisk event", () => {
  const ev = parseEvent(fx("event_negrisk.json"));
  assert.ok(ev.negRisk && !ev.augmented && ev.complete);
  assert.ok(ev.members.length >= 3);
  assert.ok(ev.sumMid > 0.9 && ev.sumMid < 1.3, `sum ${ev.sumMid}`);
});

test("parses a real order book, best-first", () => {
  const b = parseBook(fx("book.json"));
  assert.ok(b.asks.length && b.bids.length);
  for (let i = 1; i < b.asks.length; i++) assert.ok(b.asks[i].price >= b.asks[i - 1].price);
  for (let i = 1; i < b.bids.length; i++) assert.ok(b.bids[i].price <= b.bids[i - 1].price);
});

test("dutch-book arbitrage: edge only when the set fills under $1", () => {
  const cheap = negriskEvent([0.3, 0.3, 0.3]); // sum 0.9
  const arb = arbForEvent(cheap);
  assert.ok(arb && arb.edge > 0.09 && arb.edge < 0.11);
  const normal = negriskEvent([0.4, 0.4, 0.4]); // sum 1.2
  assert.ok(arbForEvent(normal).edge < 0);
  // the real fixture has no arb right now (sum ~1.018)
  assert.ok(arbForEvent(parseEvent(fx("event_negrisk.json"))).edge < 0);
  // augmented sets are never treated as full-set arbs
  assert.equal(arbForEvent(negriskEvent([0.2, 0.2, 0.2], { augmented: true })), null);
});

test("fees eat a thin arbitrage", () => {
  const edge = 0.02;
  const asks = [0.49 - edge / 2, 0.49 - edge / 2]; // sum 0.96
  const noFee = arbForEvent(negriskEvent(asks, { feeRate: 0 }));
  const withFee = arbForEvent(negriskEvent(asks, { feeRate: 0.07 }));
  assert.ok(noFee.edge > withFee.edge);
});

test("a genuine risk-free set reaches council consensus", () => {
  const ev = negriskEvent([0.28, 0.3, 0.3]); // sum 0.88, edge ~0.12
  const arb = arbForEvent(ev);
  const m = ev.members[0];
  const c = ctx(0, { event: { ...ev, arb } });
  const v = runCouncil(m, c, defaultRules());
  assert.ok(v.arb, "flagged as arb");
  assert.deepEqual(v.vetoed_by, []);
  assert.ok(v.is_signal, JSON.stringify(v.votes.filter((x) => x.stance !== BUY).map((x) => `${x.agent}:${x.stance}`)));
  const sig = buildSignal(m, v, defaultRules(), NOW, c);
  assert.equal(sig.kind, "arbitrage");
  assert.equal(sig.arb.memberIds.length, 3);
});

test("a closed / illiquid market is vetoed, never signalled", () => {
  const closed = market({ market: { closed: true } });
  assert.equal(vote(AGENTS_BY_ID.tradable, closed, ctx(0)).stance, AVOID);
  const thin = market({ liq: 100, vol24: 100 });
  const v = runCouncil(thin, ctx(0), defaultRules());
  assert.ok(!v.is_signal);
});

test("a wide spread vetoes", () => {
  const wide = market({ yesAsk: 0.65, yesBid: 0.5 });
  const v = vote(AGENTS_BY_ID.spread, wide, ctx(0));
  assert.equal(v.stance, AVOID);
});

test("directional signal needs a positive net edge after fees", () => {
  // ask below a fair estimate pulled up by momentum, zero fees
  const m = market({ yesAsk: 0.5, yesBid: 0.49, chg1h: 0.04, chg24h: 0.06, lastTrade: 0.56, feeRate: 0 });
  const v = runCouncil(m, ctx(0), defaultRules());
  assert.ok(v.edge > 0, `edge ${v.edge}`);
  // a market priced right at fair should not signal
  const flat = market({ yesAsk: 0.6, yesBid: 0.59, chg1h: 0, chg24h: 0, lastTrade: 0.595 });
  assert.ok(runCouncil(flat, ctx(0), defaultRules()).edge < defaultRules().min_edge);
});

test("scoreSignal marks a directional bet against resolution", () => {
  const m = market({ yesAsk: 0.6, feeRate: 0 });
  const sig = buildSignal(m, runCouncil(m, ctx(0), defaultRules()), defaultRules(), NOW, ctx(0));
  m.resolved = true; m.winner = 0;
  const win = scoreSignal(sig, m);
  assert.equal(win.status, "win");
  assert.ok(Math.abs(win.net_per_share - 0.4) < 1e-6); // paid 0.60, got 1.00
  m.winner = 1;
  assert.equal(scoreSignal(sig, m).status, "loss");
});

test("broken inputs never throw a vote", () => {
  const m = market();
  m.books = { 0: { asks: [{ price: NaN, size: NaN }], bids: [] }, 1: { asks: [], bids: [] } };
  for (const a of AGENTS) {
    const v = vote(a, m, ctx(0));
    assert.ok(Number.isFinite(v.strength));
  }
});

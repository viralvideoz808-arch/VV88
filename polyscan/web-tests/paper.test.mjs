import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import { arbForEvent, buildSignal, defaultRules, feePerShare, runCouncil } from "../web/js/core.js";
import { PaperAccount, START_USD } from "../web/js/paper.js";
import { ctx, market, negriskEvent } from "./helpers.mjs";

beforeEach(() => {
  const store = new Map();
  globalThis.localStorage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v)), removeItem: (k) => store.delete(k) };
});

let acctN = 0;
function acct(markets = []) {
  const map = new Map(markets.map((m) => [m.id, m]));
  return new PaperAccount({ nowMs: () => 1_000_000, getMarket: (id) => map.get(id), client: null, storageKey: `polyscan.paper.${acctN++}` });
}

test("funds at $100 and sizes trades from free cash", () => {
  const a = acct();
  assert.equal(a.acct.cash, START_USD);
  assert.equal(a.tradeUsd(), 20); // 20% of $100
});

test("a buy walks the ask ladder and pays the taker fee", async () => {
  const m = market({ yesAsk: 0.5, yesBid: 0.49, feeRate: 0.05 });
  const a = acct([m]);
  const before = a.acct.cash;
  const pos = await a.buy(m, 0, 20);
  assert.ok(pos && pos.shares > 0);
  const expectedFee = feePerShare(m, 0.5) * pos.shares;
  assert.ok(Math.abs((before - a.acct.cash) - pos.cost) < 1e-9);
  assert.ok(Math.abs(a.acct.totals.fees - expectedFee) < 1e-6 && a.acct.totals.fees > 0);
  assert.equal(a.acct.trades[0].status, "filled");
});

test("winning positions settle at $1/share, losing at $0", async () => {
  const m = market({ yesAsk: 0.6, yesBid: 0.59, feeRate: 0 });
  const a = acct([m]);
  const pos = await a.buy(m, 0, 30);
  const shares = pos.shares, cost = pos.cost;
  m.resolved = true; m.winner = 0;
  await a.monitor();
  assert.equal(a.acct.positions[pos.key], undefined);
  assert.ok(Math.abs(a.acct.cash - (START_USD - cost + shares * 1)) < 1e-9); // paid cost, got $1/share
  assert.equal(a.acct.trades[0].status, "win");

  const m2 = market({ id: "M2", yesAsk: 0.6, yesBid: 0.59, feeRate: 0 });
  const b = acct([m2]);
  const p2 = await b.buy(m2, 0, 30);
  m2.resolved = true; m2.winner = 1; // the other side won
  await b.monitor();
  assert.equal(b.acct.trades[0].status, "loss");
  assert.ok(Math.abs(b.acct.cash - (START_USD - p2.cost)) < 1e-9); // lost the stake
});

test("selling into the bid returns proceeds minus fee", async () => {
  const m = market({ yesAsk: 0.6, yesBid: 0.58, feeRate: 0 });
  const a = acct([m]);
  const pos = await a.buy(m, 0, 20);
  await a.sellAll(m.id, 0, "manual");
  assert.equal(a.acct.positions[pos.key], undefined);
  // round trip at a 2c spread loses ~ the spread; cash below start
  assert.ok(a.acct.cash < START_USD && a.acct.cash > START_USD - 2);
  assert.equal(a.acct.trades[0].side, "SELL");
});

test("an arbitrage signal buys the whole set and settles to a guaranteed profit", async () => {
  const ev = negriskEvent([0.28, 0.3, 0.3]); // sum 0.88
  const arb = arbForEvent(ev);
  const c = ctx(0, { event: { ...ev, arb } });
  const sig = buildSignal(ev.members[0], runCouncil(ev.members[0], c, defaultRules()), defaultRules(), 1_000_000, c);
  const a = acct(ev.members);
  await a.onSignal(sig, ev.members[0]);
  const positions = Object.values(a.acct.positions);
  assert.equal(positions.length, 3, "one leg per outcome");
  const spent = START_USD - a.acct.cash;
  const sets = positions[0].shares;
  assert.ok(spent < sets * 1, "paid less than the $1/set payout");
  // exactly one outcome resolves YES
  ev.members.forEach((m, i) => { m.resolved = true; m.winner = i === 0 ? 0 : 1; });
  await a.monitor();
  assert.equal(Object.keys(a.acct.positions).length, 0);
  assert.ok(a.acct.cash > START_USD, `guaranteed profit: ${a.acct.cash} > ${START_USD}`);
});

test("respects the max-position cap", async () => {
  const ms = Array.from({ length: 8 }, (_, i) => market({ id: `K${i}`, yesAsk: 0.5, yesBid: 0.49 }));
  const a = acct(ms);
  a.settings.maxPositions = 2;
  for (const m of ms.slice(0, 2)) await a.buy(m, 0, 10, { kind: "directional", side: 0, agree: 20, edge: 0.03 });
  await a.onSignal({ kind: "directional", side: 0, agree: 20, edge: 0.03, question: "x", outcome: "Yes" }, ms[2]);
  assert.ok(Object.keys(a.acct.positions).length === 2);
  assert.match(a.acct.trades[0].reason, /already holding/);
});

test("snapshot equity is cash plus the mark of open positions, and persists", async () => {
  const m = market({ yesAsk: 0.5, yesBid: 0.49, feeRate: 0 });
  const a = new PaperAccount({ nowMs: () => 1_000_000, getMarket: (id) => (id === m.id ? m : null) });
  await a.buy(m, 0, 20);
  const snap = a.snapshot();
  assert.ok(Math.abs(snap.equity - (snap.cash + snap.open_value)) < 1e-9);
  assert.equal(snap.positions.length, 1);
  // reload keeps the position
  const b = new PaperAccount({ nowMs: () => 1_000_000, getMarket: (id) => (id === m.id ? m : null) });
  assert.equal(Object.keys(b.acct.positions).length, 1);
});

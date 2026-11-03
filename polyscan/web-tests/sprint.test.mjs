import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import { parseMarket } from "../web/js/sources.js";
import { PaperAccount, walkAsksShares } from "../web/js/paper.js";
import { twap } from "../web/js/spot.js";
import {
  Phi, SPRINT_AGENTS, classifyWindow, complementCost, decide, defaultSprintRules, kellyStake, ladderPairs, modelUp,
  runSprintCouncil, sideContext, sigmaFromCloses,
} from "../web/js/sprint.js";
import { NOW, fx, market } from "./helpers.mjs";

beforeEach(() => {
  const store = new Map();
  globalThis.localStorage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v)), removeItem: (k) => store.delete(k) };
});

const SIG = 0.00012 / Math.sqrt(60); // ~1.2bp per minute, per-second log vol

test("normal CDF is accurate", () => {
  assert.ok(Math.abs(Phi(0) - 0.5) < 1e-7);
  assert.ok(Math.abs(Phi(1) - 0.841344746) < 2e-7);
  assert.ok(Math.abs(Phi(-1.96) - 0.024997895) < 2e-7);
});

test("real Gamma events: Chainlink 5m/15m windows are recognised, Binance-candle hourly ones are not", () => {
  const e5 = fx("updown_5m.json"), e15 = fx("updown_15m.json"), eh = fx("updown_hourly.json");
  const c5 = classifyWindow(e5, e5.markets[0]), c15 = classifyWindow(e15, e15.markets[0]);
  assert.deepEqual([c5.asset, c5.periodMin], ["btc", 5]);
  assert.deepEqual([c15.asset, c15.periodMin], ["btc", 15]);
  assert.equal(c5.endMs, c15.endMs, "the two windows end together: one final number settles both");
  assert.equal(classifyWindow(eh, eh.markets[0]), null);
  // the settlement rule on real data: Up iff final >= price to beat
  const md = e5.eventMetadata, m = parseMarket(e5.markets[0], e5);
  assert.equal(m.winner, md.finalPrice >= md.priceToBeat ? 0 : 1);
});

test("model: at the strike it is a coin flip; distance and time move it the right way", () => {
  const base = { ref0: 100, sigma: SIG, strikeSdBp: 0, floorBp: 0 };
  assert.ok(Math.abs(modelUp({ ...base, spot: 100, remSec: 200 }).pUp - 0.5) < 1e-9);
  const up = modelUp({ ...base, spot: 100.02, remSec: 200 }).pUp;
  const upLater = modelUp({ ...base, spot: 100.02, remSec: 70 }).pUp;
  assert.ok(up > 0.5 && upLater > up, "same lead with less time left is more certain");
  assert.ok(modelUp({ ...base, spot: 99.98, remSec: 200 }).pUp < 0.5);
  // r >= 60: variance is sigma^2 (r - 40)
  const r = modelUp({ ...base, spot: 100.01, remSec: 100 });
  assert.ok(Math.abs(r.sd - SIG * Math.sqrt(60)) < 1e-12);
});

test("model: inside the final minute the part of the 60s average already seen counts", () => {
  const base = { ref0: 100, sigma: SIG, strikeSdBp: 0, floorBp: 0, remSec: 10 };
  // spot just dipped below the strike, but the last 50s averaged well above it: still very likely Up
  const p = modelUp({ ...base, spot: 99.995, tailAvg: 100.03 }).pUp;
  assert.ok(p > 0.95, `p=${p}`);
  const noTail = modelUp({ ...base, spot: 99.995 }).pUp;
  assert.ok(noTail < 0.5);
});

test("model: strike uncertainty widens the distribution", () => {
  const a = modelUp({ ref0: 100, spot: 100.01, sigma: SIG, remSec: 100, strikeSdBp: 0, floorBp: 0 });
  const b = modelUp({ ref0: 100, spot: 100.01, sigma: SIG, remSec: 100, strikeSdBp: 4, floorBp: 2 });
  assert.ok(b.sd > a.sd && b.pUp < a.pUp && b.pUp > 0.5);
});

test("volatility from 1-minute closes", () => {
  const closes = Array.from({ length: 61 }, (_, i) => 100 * Math.exp((i % 2 ? 1 : -1) * 0.0005));
  const s = sigmaFromCloses(closes);
  assert.ok(Math.abs(s * Math.sqrt(60) - 0.001) < 1e-9); // alternating ±5bp swings = 10bp per-minute returns
  assert.equal(sigmaFromCloses([100, 101]), null);
});

test("time-weighted average of a tick stream", () => {
  const s = [{ t: 0, p: 10 }, { t: 30, p: 20 }, { t: 45, p: 30 }];
  assert.equal(twap(s, 0, 60).avg, (10 * 30 + 20 * 15 + 30 * 15) / 60);
  assert.equal(twap(s, 40, 60).avg, (20 * 5 + 30 * 15) / 20);
  assert.equal(twap([{ t: 100, p: 1 }], 0, 60, 5).covered, false, "no tick before the window: not covered");
});

// Two windows ending together, with books. Strikes in exchange units.
function win(id, periodMin, strike, upAsk, upBid, { src = "recorded", oracle = null } = {}) { // strikes in exchange units
  const m = market({ id, yesAsk: upAsk, yesBid: upBid, feeRate: 0.07, liq: 5000, outcomes: ["Up", "Down"], market: { minOrder: 5 } });
  return { id, asset: "btc", periodMin, startMs: NOW - periodMin * 60000 + 60000, endMs: NOW + 60000, m, ref: { value: strike, src, sdBp: src === "oracle" ? 2 : 1.5, oracle, display: String(strike) } };
}

test("strike ladder: Up on the low strike + Down on the high strike, flagged when under $1", () => {
  // 15m strike 77274 < 5m strike 77311 (the real pair in the fixtures). Up(low) must be worth >= Up(high).
  // Both strikes are Chainlink's published values (exact), 4.8bp apart.
  const low = win("L", 15, 77274, 0.40, 0.39, { src: "oracle", oracle: 77274.015 }), high = win("H", 5, 77311, 0.52, 0.51, { src: "oracle", oracle: 77311.384 }); // broken: Up(low) < Up(high)
  const pairs = ladderPairs([low, high]);
  assert.equal(pairs.length, 1);
  const p = pairs[0];
  assert.equal(p.low, low); assert.equal(p.high, high);
  const fee = (x) => 0.07 * x * (1 - x);
  assert.ok(Math.abs(p.cost - (0.40 + fee(0.40) + 0.49 + fee(0.49))) < 1e-9);
  assert.ok(p.edge > 0.06);
  // every outcome pays at least $1 per pair: final below both, between, above both
  for (const final of [77200, 77290, 77400]) {
    const payout = (final >= low.ref.value ? 1 : 0) + (final < high.ref.value ? 1 : 0);
    assert.ok(payout >= 1, `final ${final} pays ${payout}`);
  }
});

test("strike ladder: strikes too close to order with estimated prices are skipped; exact oracle strikes are not", () => {
  const a = win("A", 15, 100.000, 0.40, 0.39), b = win("B", 5, 100.002, 0.52, 0.51); // 0.2bp apart, ±1.5bp each
  assert.equal(ladderPairs([a, b]).length, 0);
  const ao = win("A", 15, 100.000, 0.40, 0.39, { src: "oracle", oracle: 100.000 }), bo = win("B", 5, 100.002, 0.52, 0.51, { src: "oracle", oracle: 100.002 });
  assert.equal(ladderPairs([ao, bo])[0].low, ao);
});

test("crossed books (matching paused) are never treated as prices", () => {
  const w = win("X", 5, 100, 0.12, 0.99);
  assert.equal(w.m.crossed(0), true);
  assert.equal(w.m.mid(0), null);
  assert.equal(complementCost(w.m), null);
  assert.equal(ladderPairs([w, win("Y", 15, 101, 0.5, 0.49)]).length, 0);
});

test("half-Kelly stake", () => {
  const r = defaultSprintRules();
  assert.equal(kellyStake(0.5, 0.55, 10, r), 0);
  assert.ok(Math.abs(kellyStake(0.6, 0.5, 10, r) - Math.min(10 * 0.2 * 0.5, 3.5)) < 1e-9);
});

function analysis(over = {}) {
  return {
    started: true, remSec: 150, elapsedFrac: 0.5, spot: { price: 100.05, ageMs: 500, src: "test" }, altMoveBp: 5,
    sigma: SIG, sigma10: SIG, model: modelUp({ ref0: 100, spot: 100.05, sigma: SIG, remSec: 150, strikeSdBp: 1.5 }), mom30Bp: 2, jumpSigma: 0.5,
    trail: [], ladder: [], ladderGroup: null, track: { n: 0 }, ...over,
  };
}
const acctView = { equity: 10, cash: 10, open: 0, maxPositions: 4, heldMarkets: new Set() };

test("the council: 25 agents; a cheap Up with the price well above the strike is a snipe", () => {
  assert.equal(SPRINT_AGENTS.length, 25);
  assert.equal(new Set(SPRINT_AGENTS.map((a) => a.id)).size, 25);
  const w = win("W", 5, 100, 0.70, 0.69);
  const a = analysis();
  assert.ok(a.model.pUp > 0.85);
  const rules = defaultSprintRules();
  const x = sideContext(w, a, 0, acctView, rules, NOW);
  assert.ok(x.edge > rules.min_edge, `edge ${x.edge}`);
  const res = runSprintCouncil(w, x);
  assert.equal(res.votes.length, 25);
  assert.deepEqual(res.vetoedBy, []);
  assert.ok(res.buy >= rules.min_votes, `buy votes ${res.buy}`);
  assert.equal(decide(res, x, rules), "snipe");
  // the other side of the same window is not a trade
  const xd = sideContext(w, a, 1, acctView, rules, NOW);
  assert.equal(decide(runSprintCouncil(w, xd), xd, rules, { explore: true }), null);
});

test("the council: safety vetoes block a trade", () => {
  const rules = defaultSprintRules();
  const w = win("W", 5, 100, 0.70, 0.69);
  const check = (a, acct, agentId) => {
    const x = sideContext(w, a, 0, acct, rules, NOW);
    const res = runSprintCouncil(w, x);
    assert.ok(res.vetoedBy.includes(agentId), `${agentId} should veto: ${JSON.stringify(res.vetoedBy)}`);
    assert.equal(decide(res, x, rules, { explore: true }), null);
  };
  check(analysis({ spot: { price: 100.05, ageMs: 30000, src: "test" } }), acctView, "feed");
  check(analysis({ jumpSigma: null }), acctView, "jump");
  check(analysis({ remSec: 5 }), acctView, "clock");
  check(analysis(), { ...acctView, heldMarkets: new Set(["W"]) }, "exposure");
  check(analysis({ sigma: null, model: null }), acctView, "vol");
});

test("demo account buys a ladder pair in full and each leg settles at $1/$0", async () => {
  const low = win("L", 15, 77274, 0.40, 0.39), high = win("H", 5, 77311, 0.52, 0.51);
  const map = new Map([["L", low.m], ["H", high.m]]);
  const acct = new PaperAccount({ nowMs: () => NOW, getMarket: (id) => map.get(id), storageKey: "sprint.test.legs" });
  await acct.buyLegs({ id: "s1", eventTitle: "ladder", stake: 6, legs: [{ marketId: "L", side: 0 }, { marketId: "H", side: 1 }] });
  const pos = Object.values(acct.acct.positions);
  assert.equal(pos.length, 2);
  const sets = pos[0].shares;
  assert.equal(pos[1].shares, sets);
  assert.ok(sets >= 5);
  const spent = 10 - acct.acct.cash;
  assert.ok(spent < sets, "paid less than the guaranteed payout");
  // real outcome from the fixtures: the final (77273.57) was below both strikes -> 15m Up loses, 5m Down wins
  low.m.resolved = true; low.m.winner = 1; high.m.resolved = true; high.m.winner = 1;
  await acct.monitor();
  assert.equal(Object.keys(acct.acct.positions).length, 0);
  assert.ok(Math.abs(acct.acct.cash - (10 - spent + sets)) < 1e-9);
});

test("ladder buy refuses partial fills and crossed books", async () => {
  const low = win("L", 15, 77274, 0.40, 0.39), high = win("H", 5, 77311, 0.52, 0.51);
  high.m.books[1].asks = [{ price: 0.49, size: 2 }];
  const map = new Map([["L", low.m], ["H", high.m]]);
  const acct = new PaperAccount({ nowMs: () => NOW, getMarket: (id) => map.get(id), storageKey: "sprint.test.partial" });
  await acct.buyLegs({ id: "s2", eventTitle: "ladder", stake: 6, legs: [{ marketId: "L", side: 0 }, { marketId: "H", side: 1 }] });
  assert.equal(Object.keys(acct.acct.positions).length, 0);
  assert.equal(acct.acct.cash, 10);
  const x = win("X", 5, 100, 0.12, 0.99);
  await acct.buy(x.m, 0, 3);
  assert.equal(acct.acct.cash, 10);
  assert.match(acct.acct.trades[0].reason, /crossed/);
});

test("walking asks for an exact share count", () => {
  const f = walkAsksShares([{ price: 0.4, size: 3 }, { price: 0.41, size: 10 }], 5);
  assert.equal(f.shares, 5);
  assert.ok(Math.abs(f.spent - (3 * 0.4 + 2 * 0.41)) < 1e-12);
});

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  AGENTS_BY_ID, AVOID, BUY, HOLD, LAUNCH_PRICE, Token, buildSignal, chainView, defaultRules, makeRugReport,
  pushPoint, roundTripCost, runCouncil, vote,
} from "../web/js/core.js";
import { context } from "./helpers.mjs";

const K = 30e9 * 1.073e15;
const T0 = 1_800_000_000;

// A coin whose curve holds `sols[i]` SOL of real reserves at poll i (2 s apart), launched at T0.
function coin(sols, { startT = T0 + 20, mint = "FLOWpump", symbol = "FLOW" } = {}) {
  const tok = new Token({
    mint, symbol, name: "Flow", created_at: T0, creator: "dev9", pair_address: "CURVE",
    description: "A clean community coin with a real description.", twitter: "https://x.com/f", telegram: "https://t.me/f",
    image: "https://img/f.png", metadata_loaded: true,
  });
  sols.forEach((sol, i) => {
    const vSol = 30e9 + sol * 1e9;
    pushPoint(tok, { t: startT + 2 * i, vSol, vTok: K / vSol, realSol: sol * 1e9, complete: false });
  });
  return tok;
}
const nowOf = (tok) => tok.chain[tok.chain.length - 1].t;
const view = (tok) => chainView(tok, nowOf(tok));
function ctxFor(tok, extra = {}) {
  const ctx = context([tok], { now: nowOf(tok), hot: [] });
  ctx.view = view(tok);
  return Object.assign(ctx, extra);
}

test("chain view: exact price, changes, inflow, launch reference", () => {
  const tok = coin([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]); // +1 SOL every 2 s
  const v = view(tok);
  assert.ok(Math.abs(v.price - (46e9 / 1e9) / ((K / 46e9) / 1e6)) < 1e-15);
  assert.ok(Math.abs(v.inflow(10) - 5) < 1e-9);
  assert.ok(v.change(10) > 0 && v.change(30) > v.change(10));
  // older than the series but younger than the window: change since the exact launch price
  assert.ok(Math.abs(v.change(300) - (v.price / LAUNCH_PRICE - 1)) < 1e-12);
  assert.ok(Math.abs(v.progress - 16 / 85) < 1e-9);
  assert.equal(v.drawdown, 0);
  const f = v.flows(60);
  assert.equal(f.active, f.pairs);
  assert.ok(Math.abs(f.gin - 16) < 1e-9 && f.gout === 0);
});

test("stale series is ignored", () => {
  const tok = coin([1, 2, 3]);
  assert.equal(chainView(tok, nowOf(tok) + 60), null);
});

test("a single 2-second dump of the curve vetoes", () => {
  const tok = coin([...Array.from({ length: 20 }, (_, i) => 5 + i * 0.3), 8]); // 10.7 SOL -> 8 SOL in one poll
  const v = vote(AGENTS_BY_ID.rug_pattern, tok, ctxFor(tok));
  assert.equal(v.stance, AVOID);
  assert.match(v.reason, /one 2-second window/);
});

test("entry timing is a chase filter: waits on vertical candles and deep drawdowns, otherwise no objection", () => {
  // vertical: +4 SOL in the last 10 s on a 6 SOL curve
  const vertical = coin([...Array(30).fill(6), 7, 8, 9, 10, 10.2]);
  const vv = vote(AGENTS_BY_ID.entry, vertical, ctxFor(vertical));
  assert.equal(vv.stance, HOLD);
  assert.match(vv.reason, /vertical/);
  // 45% below the 5-minute high: no base yet
  const deep = coin([...Array.from({ length: 20 }, (_, i) => 4 + i), 20, 15, 12, 10, 9, 9, 9, 9]);
  assert.equal(vote(AGENTS_BY_ID.entry, deep, ctxFor(deep)).stance, HOLD);
  // steady climb: no objection
  const steady = coin(Array.from({ length: 40 }, (_, i) => 3 + i * 0.12));
  const sv = vote(AGENTS_BY_ID.entry, steady, ctxFor(steady));
  assert.equal(sv.stance, BUY, sv.reason);
  assert.match(sv.reason, /no chase/);
});

test("curve stage votes follow the measured odds", async () => {
  const { curveStageVote } = await import("../web/js/core.js");
  assert.equal(curveStageVote(1)[0], AVOID);   // < 2 SOL: worst stage
  assert.equal(curveStageVote(3)[0], HOLD);    // 2-5 SOL
  assert.equal(curveStageVote(7)[0], BUY);     // past the early stage
  assert.equal(curveStageVote(30)[0], BUY);
  assert.equal(curveStageVote(85)[0], HOLD);   // curve full
});

test("cost hurdle: a dust-sized trade is blocked, a normal one is priced", () => {
  const tok = coin(Array.from({ length: 60 }, (_, i) => 5 + Math.sin(i / 3) * 1.5 + i * 0.05));
  const tiny = ctxFor(tok, { trade: { size_sol: 0.005, network_fee_sol: 0.000505 } });
  assert.ok(roundTripCost(tok, tiny).hurdle > 0.2);
  assert.equal(vote(AGENTS_BY_ID.cost, tok, tiny).stance, AVOID);
  const normal = ctxFor(tok, { trade: { size_sol: 0.03, network_fee_sol: 0.000505 } });
  const h = roundTripCost(tok, normal).hurdle;
  assert.ok(h > 0.055 && h < 0.065, `hurdle ${h}`); // 2.5% fees + 3.4% network + ~0.2% impact
  assert.notEqual(vote(AGENTS_BY_ID.cost, tok, normal).stance, AVOID);
});

test("take-profit 1 always clears the round-trip costs twice", () => {
  const tok = coin(Array.from({ length: 60 }, (_, i) => 4 + i * 0.05));
  tok.rug = makeRugReport({ fetched_at: T0, top10_pct: 15, max_holder_pct: 3, creator_pct: 1, creator_token_count: 1, total_holders: 120 });
  const ctx = ctxFor(tok, { trade: { size_sol: 0.02, network_fee_sol: 0.000505 }, sol_usd: 150 });
  const verdict = runCouncil(tok, ctx, defaultRules());
  const sig = buildSignal(tok, verdict, defaultRules(), nowOf(tok), ctx);
  assert.ok(sig.take_profit_1_pct >= 2 * sig.cost_hurdle_pct - 0.1, JSON.stringify(sig));
  assert.ok(sig.entry_mcap_usd > 0);
});

test("a coin can reach consensus from on-chain data alone, before DexScreener lists it", () => {
  // 3 minutes old, steady real buying with a shallow pullback and buyers returning now
  const path = [];
  for (let i = 0; i < 70; i++) path.push(2 + i * 0.18 + (i > 55 ? -(i - 55) * 0.25 : 0) + (i > 64 ? (i - 64) * 0.45 : 0));
  const tok = coin(path, { startT: T0 + 40 });
  tok.rug = makeRugReport({ fetched_at: T0, top10_pct: 14, max_holder_pct: 3, creator_pct: 0.5, creator_token_count: 1, total_holders: 140 });
  tok.holder_history.push([T0 + 60, 60], [nowOf(tok), 140]);
  assert.equal(tok.snapshots.length, 0);
  const ctx = context([tok], { now: nowOf(tok), hot: [] });
  ctx.trade = { size_sol: 0.03, network_fee_sol: 0.000505 };
  const v = runCouncil(tok, ctx, defaultRules());
  const notBuy = v.votes.filter((x) => x.stance !== BUY).map((x) => `${x.agent}:${x.stance}:${x.reason}`);
  assert.deepEqual(v.vetoed_by, [], notBuy.join(" | "));
  assert.ok(v.buy_votes >= 18, notBuy.join(" | "));
});

test("a chain-only signal is scored and voided from the on-chain price", async () => {
  const { scoreOutcome } = await import("../web/js/core.js");
  const tok = coin(Array.from({ length: 40 }, (_, i) => 4 + i * 0.05));
  const ctx = ctxFor(tok, { sol_usd: 150, trade: { size_sol: 0.03, network_fee_sol: 0.000505 } });
  const r = defaultRules();
  const sig = buildSignal(tok, runCouncil(tok, ctx, r), r, nowOf(tok), ctx);
  assert.ok(sig.entry_price_sol > 0 && sig.entry_mcap_usd > 0);
  // the coin then rallies 60% on-chain within the window
  const last = tok.chain.at(-1);
  for (let i = 1; i <= 100; i++) {
    const sol = last.realSol / 1e9 + i * 0.12;
    const vSol = 30e9 + sol * 1e9;
    pushPoint(tok, { t: last.t + 2 * i, vSol, vTok: K / vSol, realSol: sol * 1e9, complete: false });
  }
  const out = scoreOutcome(sig, tok, sig.expires + 1);
  assert.ok(out.peak_pct > 40, JSON.stringify(out));
  assert.equal(out.status, "win");
});

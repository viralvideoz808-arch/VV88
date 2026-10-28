import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { beforeEach, test } from "node:test";

import {
  BASE_FEE_LAMPORTS, CURVE_FEES, PaperAccount, RENT_TOKEN_ACCOUNT, TOKEN_2022_PROGRAM, curveBuy, curveSell, curveSpot,
  decodeCurve, decodePool, fromBase64, poolBuy, poolMcapLamports, poolSell, poolSpot, swapFees, totalFeeUnits,
} from "../web/js/paper.js";

const FX = JSON.parse(readFileSync(new URL("./fixtures/accounts.json", import.meta.url)));
const SOL = 1_000_000_000n;
const fresh = () => ({ vTok: 1_073_000_000_000_000n, vSol: 30_000_000_000n, realTok: 793_100_000_000_000n, realSol: 0n, supply: 1_000_000_000_000_000n, complete: false });

// In-memory localStorage so persistence can be tested under Node.
beforeEach(() => {
  const store = new Map();
  globalThis.localStorage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v)), removeItem: (k) => store.delete(k) };
});

test("decodes a real bonding curve account", () => {
  const c = decodeCurve(fromBase64(FX.bonding_curve.data));
  const e = FX.bonding_curve.expect;
  assert.equal(c.vTok, BigInt(e.virtual_token_reserves));
  assert.equal(c.vSol, BigInt(e.virtual_sol_reserves));
  assert.equal(c.realTok, BigInt(e.real_token_reserves));
  assert.equal(c.realSol, BigInt(e.real_sol_reserves));
  assert.equal(c.supply, BigInt(e.token_total_supply));
  assert.equal(c.complete, false);
  // constant product is preserved from the launch reserves (to rounding)
  const k0 = 30_000_000_000n * 1_073_000_000_000_000n;
  assert.ok(Math.abs(Number(c.vSol * c.vTok - k0) / Number(k0)) < 1e-6);
});

test("decodes a real PumpSwap pool, and effective reserves reproduce the market price", () => {
  const p = decodePool(fromBase64(FX.pumpswap_pool.data));
  const e = FX.pumpswap_pool.expect;
  assert.equal(p.baseMint, e.base_mint);
  assert.equal(p.quoteMint, e.quote_mint);
  assert.equal(p.baseVault, e.pool_base_token_account);
  assert.equal(p.quoteVault, e.pool_quote_token_account);
  assert.equal(p.virtualQuote, BigInt(e.virtual_quote_reserves));
  const pool = { base: BigInt(e.vault_base_amount), quote: BigInt(e.vault_quote_amount) + p.virtualQuote, supply: BigInt(e.mint_supply) };
  const ref = Number(e.dexscreener_price_native);
  assert.ok(Math.abs(poolSpot(pool) / ref - 1) < 0.03, `${poolSpot(pool)} vs ${ref}`);
  // without the virtual quote reserves the price would be ~16x off
  assert.ok(poolSpot({ ...pool, quote: BigInt(e.vault_quote_amount) }) < ref / 10);
});

test("launch price and curve buy math", () => {
  const c = fresh();
  assert.ok(Math.abs(curveSpot(c) - 30 / 1_073_000_000) < 1e-15);
  const q = curveBuy(c, SOL);
  assert.ok(q.total <= SOL && SOL - q.total < 1_000n, `spent ${q.total}`);
  assert.equal(q.protocolFee + q.creatorFee, (q.solCost * 950n + 99_999n) / 100_000n + (q.solCost * 300n + 99_999n) / 100_000n);
  assert.ok(Math.abs(Number(q.protocolFee + q.creatorFee) / Number(q.solCost) - 0.0125) < 1e-6);
  // ~0.9877 SOL buys ~34.2M tokens at launch
  assert.ok(q.tokens > 34_000_000_000_000n && q.tokens < 34_500_000_000_000n, String(q.tokens));
  // the pool never loses value to rounding
  assert.ok(q.after.vSol * q.after.vTok >= c.vSol * c.vTok);
});

test("round trip costs both fees and nothing else", () => {
  const c = fresh();
  const b = curveBuy(c, SOL / 10n);
  const s = curveSell(b.after, b.tokens);
  const lost = 1 - Number(s.net) / Number(b.total);
  assert.ok(lost > 0.0245 && lost < 0.0255, `round trip lost ${(lost * 100).toFixed(3)}%`);
});

test("a buy larger than the curve sells it out", () => {
  const q = curveBuy(fresh(), 200n * SOL);
  assert.equal(q.tokens, 793_100_000_000_000n);
  assert.equal(q.after.realTok, 0n);
  assert.ok(q.total < 200n * SOL);
});

test("PumpSwap fee tiers follow pump.fun's published table", () => {
  const t = (sol) => totalFeeUnits(swapFees(BigInt(sol) * SOL));
  assert.equal(t(100), 1250n);     // 0-420 SOL: 1.25%
  assert.equal(t(420), 1200n);     // 420-1470: 1.20%
  assert.equal(t(5000), 1000n);    // 4420-9820: 1.00%
  assert.equal(t(55000), 525n);    // 54030-58940: 0.525%
  assert.equal(t(200000), 300n);   // 98240+: 0.30%
  assert.equal(swapFees(0n).lp, 20n);
});

test("pool buy/sell math", () => {
  const p = { base: 800_000_000_000_000n, quote: 90n * SOL, supply: 1_000_000_000_000_000n };
  assert.ok(Number(poolMcapLamports(p)) / 1e9 > 112 && Number(poolMcapLamports(p)) / 1e9 < 113);
  const b = poolBuy(p, SOL);
  assert.ok(b.total <= SOL && SOL - b.total < 1_000n);
  assert.ok(b.after.quote * b.after.base >= p.quote * p.base);
  const s = poolSell(b.after, b.tokens);
  const lost = 1 - Number(s.net) / Number(b.total);
  assert.ok(lost > 0.024 && lost < 0.026, `${lost}`);
});

// A chain reader whose state the test controls; each call can return a different state.
function fakeReader(states) {
  let i = 0;
  return { lastError: "", venue: async () => ({ ...states[Math.min(i++, states.length - 1)] }), set(s) { states = [s]; i = 0; } };
}
const curveVenue = (c) => ({ kind: "curve", curve: c, tokenProgram: TOKEN_2022_PROGRAM });
const tok = { mint: "MINTpump", symbol: "TEST", name: "Test", pair_address: "CURVE", migrated: false, snap: { mcap_usd: 5000 } };
const signal = { mint: "MINTpump", symbol: "TEST", id: "MINTpump:1", agree: 21, stop_loss_pct: -20, take_profit_1_pct: 30, take_profit_2_pct: 60 };
const network = (acct) => BASE_FEE_LAMPORTS + BigInt(Math.round(acct.settings.priorityFeeSol * 1e9));

function account(states) {
  const a = new PaperAccount({ clock: () => 1000, solUsd: () => 100, reader: fakeReader(states), sleepFn: async () => {} });
  a.ensureFunded();
  return a;
}

test("a $10 account at $100/SOL holds 0.1 SOL", () => {
  const a = account([curveVenue(fresh())]);
  assert.equal(a.acct.cash, 100_000_000n);
  assert.equal(a.snapshot().equity_usd, 10);
});

test("signal buy charges amount, pump fees, network fee and token-account rent", async () => {
  const a = account([curveVenue(fresh())]);
  const before = a.acct.cash;
  const pos = await a.onSignal(signal, tok);
  assert.ok(pos && pos.tokens > 0n);
  const t = a.acct.trades[0];
  assert.equal(t.status, "filled");
  const spent = before - a.acct.cash;
  assert.equal(spent, pos.cost + network(a) + RENT_TOKEN_ACCOUNT.token2022);
  assert.ok(Math.abs(Number(pos.cost) / Number(a.freeLamports() + spent) - 0.3) < 0.05); // ~30% of free SOL
  assert.ok(t.pump_fees > 0 && t.network_fee > 0 && t.rent > 0);
});

test("a buy that lands after the price ran past slippage fails but still pays network fees", async () => {
  const start = fresh();
  const pumped = curveBuy(start, 20n * SOL).after; // someone bought 20 SOL in front of us
  const a = account([curveVenue(start), curveVenue(pumped)]);
  const before = a.acct.cash;
  const pos = await a.onSignal(signal, tok);
  assert.equal(pos, null);
  assert.equal(a.acct.trades[0].status, "failed");
  assert.match(a.acct.trades[0].reason, /slippage/);
  assert.equal(before - a.acct.cash, network(a));
  assert.deepEqual(Object.keys(a.acct.positions), []);
});

test("exit plan: half at take-profit 1, rest at stop; rent refunded; books balance", async () => {
  const c0 = curveBuy(fresh(), 20n * SOL).after; // enter after 20 SOL of buying, so a crash below entry is possible
  const a = account([curveVenue(c0)]);
  const start = a.acct.cash;
  const pos = await a.onSignal(signal, tok);
  const entryCost = pos.cost;
  // market rallies ~45% (others buy 15 SOL) -> take profit 1 sells half
  const up = curveBuy(curveBuy(c0, entryCost).after, 15n * SOL).after;
  a.reader.set(curveVenue(up));
  await a.monitor(() => tok);
  assert.ok(a.acct.positions.MINTpump.tp1_done);
  const half = a.acct.trades[0];
  assert.equal(half.side, "SELL");
  assert.match(half.reason, /take profit 1/);
  // then it crashes below the entry -> stop loss sells the rest and closes the account
  const down = curveSell(up, 250_000_000_000_000n).after; // holders dump 250M tokens
  assert.ok(curveSpot(down) < pos.entry_spot * 0.8);
  a.reader.set(curveVenue(down));
  await a.monitor(() => tok);
  assert.equal(a.acct.positions.MINTpump, undefined);
  assert.match(a.acct.trades[0].reason, /stop loss/);
  assert.equal(a.acct.trades[0].rent_refund, Number(RENT_TOKEN_ACCOUNT.token2022) / 1e9);
  // every lamport is accounted for
  const t = a.acct.totals;
  const sellNet = a.acct.trades.filter((x) => x.side === "SELL" && x.status === "filled").reduce((s, x) => s + BigInt(Math.round(x.lamports_out * 1e9)), 0n);
  assert.equal(a.acct.cash, start - entryCost + sellNet - t.networkFees - t.rentPaid + t.rentRefunded);
  assert.equal(t.realized, sellNet - entryCost);
});

test("account survives a reload", async () => {
  const a = account([curveVenue(fresh())]);
  await a.onSignal(signal, tok);
  const b = new PaperAccount({ clock: () => 1000, solUsd: () => 100, reader: fakeReader([curveVenue(fresh())]) });
  assert.equal(b.acct.cash, a.acct.cash);
  assert.equal(b.acct.positions.MINTpump.tokens, a.acct.positions.MINTpump.tokens);
  assert.equal(b.acct.trades.length, a.acct.trades.length);
});

test("max positions and dust balances are respected", async () => {
  const a = account([curveVenue(fresh())]);
  a.settings.maxPositions = 1;
  await a.onSignal(signal, tok);
  const second = await a.onSignal({ ...signal, mint: "OTHERpump", id: "x" }, { ...tok, mint: "OTHERpump" });
  assert.equal(second, null);
  assert.match(a.acct.trades[0].reason, /already holding/);
});

test("RPC fails over to the next endpoint and never uses the browser-blocked one", async () => {
  const { ChainReader } = await import("../web/js/paper.js");
  const hits = [];
  const fetchImpl = async (url) => {
    hits.push(new URL(url).host);
    if (url.includes("publicnode")) return new Response("", { status: 429 });
    return Response.json({ jsonrpc: "2.0", id: 1, result: { value: [] } });
  };
  const r = new ChainReader("https://api.mainnet-beta.solana.com", fetchImpl);
  assert.ok(!r.urls.some((u) => u.includes("api.mainnet-beta")));
  await r.accounts(["x"], "base64");
  assert.deepEqual(hits, ["solana-rpc.publicnode.com", "solana-mainnet.gateway.tatum.io"]);
  assert.ok(r.url.includes("tatum"));
  await r.accounts(["x"], "base64");
  assert.equal(hits.at(-1), "solana-mainnet.gateway.tatum.io"); // sticks with the endpoint that works
});

test("a position not yet valued on-chain counts at cost in equity", async () => {
  const a = account([curveVenue(fresh())]);
  const pos = await a.onSignal(signal, tok);
  const snap = a.snapshot();
  const expected = Number(a.acct.cash + pos.cost + pos.rent) / 1e9;
  assert.ok(Math.abs(snap.equity_sol - expected) < 1e-12);
  assert.equal(snap.positions[0].value_sol, null);
});

test("curve poller drops to 10-account batches when the RPC demands it", async () => {
  const { CurveWatch } = await import("../web/js/flow.js");
  const { Token } = await import("../web/js/core.js");
  const curve = FX.bonding_curve;
  const sizes = [];
  const reader = {
    accounts: async (addrs) => {
      sizes.push(addrs.length);
      if (addrs.length > 10) throw new Error("-32602: A maximum of 10 accounts is allowed");
      return addrs.map(() => ({ owner: curve.owner, data: [curve.data, "base64"] }));
    },
  };
  const w = new CurveWatch(reader, { batch: 100 }); // as with a user-supplied RPC
  const toks = Array.from({ length: 25 }, (_, i) => new Token({ mint: `M${i}pump`, pair_address: curve.address }));
  await w.poll(toks, () => 100);
  assert.equal(w.batch, 10);
  assert.equal(toks[0].chain.length, 0); // the rejected first call
  await w.poll(toks, () => 102);
  assert.deepEqual(sizes, [25, 10, 5, 10, 10, 5]); // recovers inside the first poll, then full 10-batches
  assert.equal(toks[24].chain.length, 2); // read in both polls
  assert.equal(toks[24].chain[0].realSol, 12464823524);
});

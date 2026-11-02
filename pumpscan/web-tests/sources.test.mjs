import assert from "node:assert/strict";
import { test } from "node:test";

import { Token } from "../web/js/core.js";
import {
  Http, applyRugReport, parseCreateEvent, parseDexPair, parseIso, parseListing, parseRugReport,
  parseTradeEvent, pickPair,
} from "../web/js/sources.js";

const CREATE = { signature: "sig", mint: "5AyMkYkChfYquBwdiqRXr9hgQvvvsYGNWaPdiyKnpump", traderPublicKey: "3YkM",
  txType: "create", initialBuy: 39820358.98, solAmount: 1.156, bondingCurveKey: "2c5F", marketCapSol: 30.15,
  name: "blephorse", symbol: "ROMAN", uri: "https://ipfs.io/ipfs/x", pool: "pump" };

const PAIR = { chainId: "solana", dexId: "pumpfun", pairAddress: "BH1V",
  baseToken: { address: "2mKPpump", name: "Pingu", symbol: "PINGU" },
  quoteToken: { address: "So111", symbol: "SOL" }, priceNative: "0.0000002986", priceUsd: "0.00003007",
  txns: { m5: { buys: 137, sells: 98 }, h1: { buys: 1423, sells: 1141 } },
  volume: { h1: 112372.22, m5: 12097.3 }, priceChange: { m5: 26.61, h1: 567 },
  marketCap: 30078.05, pairCreatedAt: 1793196541000,
  info: { imageUrl: "https://img", websites: [{ url: "https://w" }], socials: [{ url: "https://x.com/a", type: "twitter" }] } };

test("create event", () => {
  const tok = parseCreateEvent(CREATE, 100);
  assert.equal(tok.symbol, "ROMAN");
  assert.equal(tok.creator, "3YkM");
  assert.equal(tok.dev_buy_sol, 1.156);
  assert.equal(tok.pair_address, "2c5F");
  assert.equal(tok.created_at, 100);
  assert.equal(parseCreateEvent({ ...CREATE, pool: "bonk" }, 1), null);
});

test("trade event", () => {
  const [mint, tr] = parseTradeEvent({ mint: "m", txType: "sell", traderPublicKey: "w", solAmount: 2 }, 5);
  assert.equal(mint, "m");
  assert.equal(tr.is_buy, false);
  assert.equal(tr.sol, 2);
  assert.equal(parseTradeEvent({ txType: "create", mint: "m" }, 1), null);
});

test("dex pair", () => {
  const tok = new Token({ mint: "2mKPpump" });
  const sol = parseDexPair(tok, PAIR, 10);
  assert.equal(tok.snap.buys_m5, 137);
  assert.equal(tok.snap.mcap_usd, 30078.05);
  assert.equal(Math.round(sol), 101);
  assert.equal(tok.symbol, "PINGU");
  assert.equal(tok.twitter, "https://x.com/a");
  assert.equal(tok.website, "https://w");
  assert.equal(tok.created_at, 1793196541);
  assert.ok(!tok.migrated && tok.has_profile);
  parseDexPair(tok, { ...PAIR, dexId: "pumpswap", liquidity: { usd: 30000 } }, 20);
  assert.ok(tok.migrated);
  assert.equal(tok.snap.liquidity_usd, 30000);
});

test("pick pair prefers pump venues", () => {
  assert.equal(pickPair([{ dexId: "raydium", liquidity: { usd: 9e9 } }, { dexId: "pumpswap" }]).dexId, "pumpswap");
  assert.equal(pickPair([]), null);
});

test("rug report excludes the pool and backfills the coin", () => {
  const d = { token: { supply: 1_000_000_000_000_000, decimals: 6 },
    knownAccounts: { POOL: { type: "AMM" }, DEV: { type: "CREATOR" } }, markets: [{ pubkey: "MKT" }],
    topHolders: [{ owner: "POOL", address: "a", pct: 70 }, { owner: "w1", address: "b", pct: 4, insider: true }, { owner: "w2", address: "c", pct: 3 }],
    creatorBalance: 20_000_000_000_000, totalHolders: 120,
    risks: [{ name: "Low liquidity", level: "danger" }, { name: "Few LPs", level: "warn" }],
    creator: "DEV", detectedAt: "2026-10-28T14:09:01.588887933Z",
    tokenMeta: { name: "Pingu", symbol: "PINGU", uri: "https://meta/x" }, fileMeta: { description: "d", image: "https://i" } };
  const tok = new Token({ mint: "m" });
  const r = parseRugReport(d, tok, 1);
  assert.equal(r.top10_pct, 7);
  assert.equal(r.max_holder_pct, 4);
  assert.equal(r.insider_pct, 4);
  assert.equal(r.creator_pct, 2);
  assert.deepEqual(r.danger_risks, ["Low liquidity"]);
  assert.deepEqual(r.warn_risks, ["Few LPs"]);
  assert.equal(applyRugReport(tok, r), true); // metadata JSON should be fetched
  assert.equal(tok.creator, "DEV");
  assert.equal(tok.symbol, "PINGU");
  assert.equal(tok.uri, "https://meta/x");
  assert.equal(tok.created_at, parseIso("2026-10-28T14:09:01.588Z"));
  assert.deepEqual(tok.holder_history.at(-1), [1, 120]);
});

test("listing keeps only pump.fun coins on Solana", () => {
  const out = parseListing([{ chainId: "solana", tokenAddress: "Abcpump", totalAmount: 30 },
    { chainId: "solana", tokenAddress: "NotAPumpCoin" }, { chainId: "bsc", tokenAddress: "0xpump" }]);
  assert.deepEqual(out.map((o) => o.mint), ["Abcpump"]);
  assert.equal(out[0].boosts, 30);
});

test("metadata falls back across IPFS gateways", async (t) => {
  const seen = [];
  t.mock.method(globalThis, "fetch", async (url) => {
    seen.push(url);
    if (url.startsWith("https://pump.mypinata.cloud/")) return new Response("", { status: 429 });
    return Response.json({ description: "hi" });
  });
  const http = new Http(new AbortController().signal);
  const res = await http.metadata("https://ipfs.io/ipfs/bafkCID");
  assert.equal(res.data.description, "hi");
  assert.deepEqual(seen, ["https://pump.mypinata.cloud/ipfs/bafkCID", "https://ipfs.io/ipfs/bafkCID"]);
});

test("a metadata host without CORS is reported as blocked and not retried", async (t) => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => { calls++; throw new TypeError("Failed to fetch"); });
  const http = new Http(new AbortController().signal);
  assert.deepEqual(await http.metadata("https://meta.example/a.json"), { blocked: true });
  assert.deepEqual(await http.metadata("https://meta.example/b.json"), { blocked: true });
  assert.equal(calls, 1);
});

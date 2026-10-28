// The $10 demo account: paper trading on real, live pump.fun market state.
//
// pump.fun has no order book. Every trade fills against an automated market maker: the bonding curve
// (a constant-product curve over virtual reserves) before graduation, and a PumpSwap constant-product
// pool after. So "reading the book" means reading those reserves on-chain and applying the programs'
// own integer math. This module does exactly that:
//
//   1. Quote: read the coin's bonding-curve / pool account from Solana at the moment of the decision
//      and set the slippage limit (min tokens out / min SOL out) the way a real transaction does.
//   2. Land: wait the configured transaction latency, read the chain again, and fill against the state
//      at that moment. If price moved past the slippage limit the transaction fails, and like a real
//      failed Solana transaction it still pays the base fee and the priority fee.
//   3. Charge every real cost: pump.fun's fee schedule (1.25% on the curve, market-cap tiers on
//      PumpSwap), the 5,000-lamport signature fee, your priority fee, and rent for the token account
//      (refunded when the account is closed after a full exit).
//
// Math mirrors pump.fun's published SDK formulas (floor on outputs, ceil on fees). Sources:
// github.com/pump-fun/pump-public-docs (PUMP_PROGRAM_README, PUMP_SWAP_README, FEE_PROGRAM_README,
// VIRTUAL_QUOTE_RESERVES_FEE_ADJUSTMENT) and its published fee tier table.

import { sleep } from "./sources.js";

export const PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";
export const PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA";
export const TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb";
export const WSOL_MINT = "So11111111111111111111111111111111111111112";
// Browser-friendly public RPCs (no key, CORS enabled). Solana's own api.mainnet-beta endpoint answers 403 to any
// request that carries a browser Origin header, so a web page cannot use it.
export const DEFAULT_RPC = "https://solana-rpc.publicnode.com";
export const FALLBACK_RPCS = ["https://solana-rpc.publicnode.com", "https://solana-mainnet.gateway.tatum.io"];
// Free-tier limits measured 2026-10-28: PublicNode allows 10 accounts per getMultipleAccounts call and sustained
// ~4 calls/s; Tatum allows 100 accounts per call but only 5 calls per minute, so it is a fallback only.
const BROWSER_BLOCKED_RPCS = new Set(["https://api.mainnet-beta.solana.com"]);

export const LAMPORTS_PER_SOL = 1_000_000_000n;
const TOKEN_UNITS = 1_000_000n; // pump.fun coins have 6 decimals
export const BASE_FEE_LAMPORTS = 5_000n; // one signature
// Rent-exempt minimum for the token account a first buy creates: (128 + data_len) * 3480 * 2 lamports.
export const RENT_TOKEN_ACCOUNT = { token2022: 2_074_080n /* 170 bytes, ImmutableOwner */, legacy: 2_039_280n /* 165 bytes */ };

// Fees in units of 1/100,000 so the published 0.275% / 0.225% ... tiers are exact.
const FEE_DENOM = 100_000n;
export const CURVE_FEES = { protocol: 950n, creator: 300n, lp: 0n }; // bonding curve: 0.95% + 0.30% = 1.25%
// PumpSwap canonical pools: [market cap threshold in SOL, creator, protocol, lp].
const SWAP_FEE_TIERS = [
  [0, 300, 930, 20], [420, 950, 50, 200], [1470, 900, 50, 200], [2460, 850, 50, 200], [3440, 800, 50, 200],
  [4420, 750, 50, 200], [9820, 700, 50, 200], [14740, 650, 50, 200], [19650, 600, 50, 200], [24560, 550, 50, 200],
  [29470, 500, 50, 200], [34380, 450, 50, 200], [39300, 400, 50, 200], [44210, 350, 50, 200], [49120, 300, 50, 200],
  [54030, 275, 50, 200], [58940, 250, 50, 200], [63860, 225, 50, 200], [68770, 200, 50, 200], [73681, 175, 50, 200],
  [78590, 150, 50, 200], [83500, 125, 50, 200], [88400, 100, 50, 200], [93330, 75, 50, 200], [98240, 50, 50, 200],
].map(([sol, creator, protocol, lp]) => ({ threshold: BigInt(sol) * LAMPORTS_PER_SOL, creator: BigInt(creator), protocol: BigInt(protocol), lp: BigInt(lp) }));

const ceilDiv = (a, b) => (a + b - 1n) / b;
const feeOf = (amount, units) => ceilDiv(amount * units, FEE_DENOM);

// pump-fees-math::calculate_fee_tier: below the first threshold use the first tier, else the highest tier reached.
export function swapFees(mcapLamports) {
  let tier = SWAP_FEE_TIERS[0];
  for (const t of SWAP_FEE_TIERS) if (mcapLamports >= t.threshold) tier = t;
  return { protocol: tier.protocol, creator: tier.creator, lp: tier.lp };
}

export const totalFeeUnits = (f) => f.protocol + f.creator + f.lp;

// ------------------------------------------------------------------ bonding curve math

// Spot price in SOL per whole token.
export const curveSpot = (c) => Number(c.vSol) / 1e9 / (Number(c.vTok) / 1e6);

// Buy with an exact SOL amount (lamportsIn already includes fees), as pump.fun's UI does.
export function curveBuy(c, lamportsIn, fees = CURVE_FEES) {
  if (c.complete || lamportsIn <= 0n) return null;
  const net = lamportsIn * FEE_DENOM / (FEE_DENOM + totalFeeUnits(fees));
  let tokens = net * c.vTok / (c.vSol + net);
  if (tokens > c.realTok) tokens = c.realTok; // the curve sells out and completes
  for (let i = 0; i < 8 && tokens > 0n; i++) {
    const solCost = tokens * c.vSol / (c.vTok - tokens) + 1n; // getBuySolAmountFromTokenAmountQuote
    const protocolFee = feeOf(solCost, fees.protocol), creatorFee = feeOf(solCost, fees.creator);
    const total = solCost + protocolFee + creatorFee;
    if (total <= lamportsIn) {
      return {
        tokens, solCost, protocolFee, creatorFee, lpFee: 0n, total,
        spotBefore: curveSpot(c),
        after: { ...c, vSol: c.vSol + solCost, vTok: c.vTok - tokens, realSol: c.realSol + solCost, realTok: c.realTok - tokens },
      };
    }
    tokens -= tokens / 1_000_000n + 1n; // rounding pushed the cost a few lamports over budget
  }
  return null;
}

// Sell an exact token amount.
export function curveSell(c, tokens, fees = CURVE_FEES) {
  if (c.complete || tokens <= 0n) return null;
  let solOut = tokens * c.vSol / (c.vTok + tokens);
  if (solOut > c.realSol) solOut = c.realSol;
  const protocolFee = feeOf(solOut, fees.protocol), creatorFee = feeOf(solOut, fees.creator);
  const net = solOut - protocolFee - creatorFee;
  return {
    tokens, solOut, protocolFee, creatorFee, lpFee: 0n, net: net > 0n ? net : 0n, spotBefore: curveSpot(c),
    after: { ...c, vSol: c.vSol - solOut, vTok: c.vTok + tokens, realSol: c.realSol - solOut, realTok: c.realTok + tokens },
  };
}

// ------------------------------------------------------------------ PumpSwap pool math

export const poolSpot = (p) => Number(p.quote) / 1e9 / (Number(p.base) / 1e6);
export const poolMcapLamports = (p) => p.quote * p.supply / p.base;

// Buy with an exact SOL amount: pump-swap-sdk buyQuoteInput, then the exact cost of that base amount.
export function poolBuy(p, lamportsIn) {
  if (lamportsIn <= 0n || p.base <= 0n || p.quote <= 0n) return null;
  const fees = swapFees(poolMcapLamports(p));
  const effective = lamportsIn * FEE_DENOM / (FEE_DENOM + totalFeeUnits(fees));
  let tokens = p.base * effective / (p.quote + effective);
  for (let i = 0; i < 8 && tokens > 0n; i++) {
    const quoteIn = ceilDiv(p.quote * tokens, p.base - tokens);
    const lpFee = feeOf(quoteIn, fees.lp), protocolFee = feeOf(quoteIn, fees.protocol), creatorFee = feeOf(quoteIn, fees.creator);
    const total = quoteIn + lpFee + protocolFee + creatorFee;
    if (total <= lamportsIn) {
      return {
        tokens, solCost: quoteIn, protocolFee, creatorFee, lpFee, total, fees, spotBefore: poolSpot(p),
        after: { ...p, base: p.base - tokens, quote: p.quote + quoteIn + lpFee },
      };
    }
    tokens -= tokens / 1_000_000n + 1n;
  }
  return null;
}

export function poolSell(p, tokens) {
  if (tokens <= 0n || p.base <= 0n) return null;
  const fees = swapFees(poolMcapLamports(p));
  const solOut = p.quote * tokens / (p.base + tokens);
  const lpFee = feeOf(solOut, fees.lp), protocolFee = feeOf(solOut, fees.protocol), creatorFee = feeOf(solOut, fees.creator);
  const net = solOut - lpFee - protocolFee - creatorFee;
  return {
    tokens, solOut, protocolFee, creatorFee, lpFee, net: net > 0n ? net : 0n, fees, spotBefore: poolSpot(p),
    after: { ...p, base: p.base + tokens, quote: p.quote - solOut + lpFee },
  };
}

// ------------------------------------------------------------------ account decoding

const B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
export function base58(bytes) {
  let n = 0n;
  for (const b of bytes) n = n * 256n + BigInt(b);
  let s = "";
  while (n > 0n) { s = B58[Number(n % 58n)] + s; n /= 58n; }
  for (const b of bytes) { if (b !== 0) break; s = "1" + s; }
  return s;
}

export function fromBase64(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

// Pump BondingCurve: 8-byte Anchor discriminator, then five u64 and a bool.
export function decodeCurve(bytes) {
  if (bytes.length < 49) throw new Error("bonding curve account too short");
  const v = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  return {
    vTok: v.getBigUint64(8, true), vSol: v.getBigUint64(16, true), realTok: v.getBigUint64(24, true),
    realSol: v.getBigUint64(32, true), supply: v.getBigUint64(40, true), complete: bytes[48] === 1,
  };
}

// PumpSwap Pool: discriminator, pool_bump u8, index u16, six pubkeys, lp_supply u64, coin_creator pubkey,
// is_mayhem_mode bool, is_cashback_coin bool, virtual_quote_reserves i128 (appended; 0 when absent).
export function decodePool(bytes) {
  if (bytes.length < 211) throw new Error("pool account too short");
  const key = (o) => base58(bytes.subarray(o, o + 32));
  const v = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let vqr = 0n;
  if (bytes.length >= 261) vqr = v.getBigUint64(245, true) + (v.getBigInt64(253, true) << 64n);
  return {
    baseMint: key(43), quoteMint: key(75), baseVault: key(139), quoteVault: key(171), virtualQuote: vqr,
  };
}

// ------------------------------------------------------------------ chain reader

export class ChainReader {
  constructor(url = DEFAULT_RPC, fetchImpl = (...a) => fetch(...a)) {
    // The chosen endpoint first, then the public fallbacks; whichever answers becomes the active one.
    this.urls = [...new Set([url, ...FALLBACK_RPCS])].filter((u) => u && !BROWSER_BLOCKED_RPCS.has(u.replace(/\/+$/, "")));
    this.active = 0;
    this.fetch = fetchImpl;
    this.pools = new Map(); // pool address -> decoded static fields
    this.lastError = "";
  }

  get url() { return this.urls[this.active]; }

  async rpc(method, params) {
    let lastErr = null;
    for (let i = 0; i < this.urls.length; i++) {
      const idx = (this.active + i) % this.urls.length;
      try {
        const r = await this.fetch(this.urls[idx], {
          method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status} from ${new URL(this.urls[idx]).host}`);
        const d = await r.json();
        if (d.error) throw new Error(`${d.error.code}: ${d.error.message}`);
        this.active = idx;
        this.lastError = "";
        return d.result;
      } catch (e) {
        lastErr = e;
      }
    }
    throw lastErr;
  }

  // "processed" = the newest state the cluster has seen, like a trading bot reading before it sends.
  async accounts(addresses, encoding) {
    const res = await this.rpc("getMultipleAccounts", [addresses, { encoding, commitment: "processed" }]);
    return res.value;
  }

  // The coin's tradable state right now: {kind: "curve" | "pool" | "migrating" | "unknown", ...}.
  async venue(desc) {
    try {
      if (!desc.migrated && desc.curve) {
        const [acc, mint] = await this.accounts([desc.curve, desc.mint], "base64");
        if (!acc || acc.owner !== PUMP_PROGRAM) return { kind: "unknown", reason: "bonding curve account not found" };
        const curve = decodeCurve(fromBase64(acc.data[0]));
        if (curve.complete) return { kind: "migrating", reason: "curve complete; moving to PumpSwap" };
        return { kind: "curve", curve, tokenProgram: mint ? mint.owner : TOKEN_2022_PROGRAM };
      }
      if (desc.pool) {
        let st = this.pools.get(desc.pool);
        if (!st) {
          const [acc] = await this.accounts([desc.pool], "base64");
          if (!acc || acc.owner !== PUMPSWAP_PROGRAM) return { kind: "unknown", reason: "PumpSwap pool not found" };
          st = decodePool(fromBase64(acc.data[0]));
          if (st.baseMint !== desc.mint || st.quoteMint !== WSOL_MINT) return { kind: "unknown", reason: "pool is not this coin's SOL pool" };
          this.pools.set(desc.pool, st);
        }
        // jsonParsed returns the token accounts parsed and falls back to base64 for the pool itself.
        const [acc, bv, qv, mint] = await this.accounts([desc.pool, st.baseVault, st.quoteVault, desc.mint], "jsonParsed");
        if (!acc || !bv || !qv || !mint) return { kind: "unknown", reason: "pool accounts missing" };
        const live = decodePool(fromBase64(acc.data[0]));
        const base = BigInt(bv.data.parsed.info.tokenAmount.amount);
        const quote = BigInt(qv.data.parsed.info.tokenAmount.amount) + live.virtualQuote; // effective quote reserves
        const supply = BigInt(mint.data.parsed.info.supply);
        return { kind: "pool", pool: { base, quote, supply }, tokenProgram: mint.owner };
      }
      return { kind: "unknown", reason: desc.migrated ? "graduated; waiting for the PumpSwap pool address" : "no bonding curve address yet" };
    } catch (e) {
      this.lastError = String(e.message || e);
      return { kind: "unknown", reason: `RPC error: ${this.lastError}` };
    }
  }
}

export const quoteBuy = (venue, lamports) => venue.kind === "curve" ? curveBuy(venue.curve, lamports) : venue.kind === "pool" ? poolBuy(venue.pool, lamports) : null;
export const quoteSell = (venue, tokens) => venue.kind === "curve" ? curveSell(venue.curve, tokens) : venue.kind === "pool" ? poolSell(venue.pool, tokens) : null;
export const venueSpot = (venue) => venue.kind === "curve" ? curveSpot(venue.curve) : venue.kind === "pool" ? poolSpot(venue.pool) : null;

// ------------------------------------------------------------------ the demo account

export const START_USD = 10;
export const DEFAULT_SETTINGS = {
  autoTrade: true,       // buy every BUY signal automatically
  sizePct: 30,           // % of free SOL per trade
  slippagePct: 15,       // min-out tolerance on every transaction
  priorityFeeSol: 0.0001,// per transaction, paid even when it fails. At $10 size, 0.0005 alone costs 3.4% per round trip
  latencyMs: 1500,       // decision -> transaction lands
  maxPositions: 3,
  maxHoldMin: 30,        // time stop
  exitOnVeto: true,      // sell when a safety agent turns to AVOID
  trailAfterTp1Pct: 25,  // after take-profit 1: sell the rest if price falls this far from its peak...
  breakevenAfterTp1: true, // ...or back to the entry price (the first half already banked the profit)
  closeAccounts: true,   // close the token account on full exit (refunds its rent)
  rpcUrl: DEFAULT_RPC,
};

const toSol = (lamports) => Number(lamports) / 1e9;
const TOKEN_FIELDS = new Set(["tokens", "expected_tokens", "min_tokens", "got_tokens"]);
const big = (x) => BigInt(x);

function storageGet(key) {
  try { return JSON.parse(localStorage.getItem(key) || "null"); } catch { return null; }
}
function storageSet(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value, (k, v) => typeof v === "bigint" ? v.toString() : v)); } catch { /* memory only */ }
}

export class PaperAccount {
  constructor({ clock = () => Date.now() / 1000, solUsd = () => 0, reader = null, storageKey = "pumpscan.paper", sleepFn = sleep } = {}) {
    this.clock = clock;
    this.solUsd = solUsd;
    this.storageKey = storageKey;
    this.sleep = sleepFn;
    this.pending = new Set();
    this.marks = new Map(); // mint -> latest valuation
    this.load();
    this.reader = reader || new ChainReader(this.settings.rpcUrl);
  }

  // ---- persistence

  load() {
    const s = storageGet(this.storageKey);
    this.settings = { ...DEFAULT_SETTINGS, ...(s && s.settings) };
    if (BROWSER_BLOCKED_RPCS.has(String(this.settings.rpcUrl).replace(/\/+$/, ""))) this.settings.rpcUrl = DEFAULT_RPC;
    if (s && s.cash != null) {
      this.acct = {
        ...s,
        cash: big(s.cash), startLamports: big(s.startLamports),
        totals: Object.fromEntries(Object.entries(s.totals).map(([k, v]) => [k, big(v)])),
        positions: Object.fromEntries(Object.entries(s.positions).map(([m, p]) => [m, { ...p, tokens: big(p.tokens), cost: big(p.cost), rent: big(p.rent) }])),
      };
    } else {
      this.acct = null;
    }
  }

  save() { storageSet(this.storageKey, { ...(this.acct || {}), settings: this.settings }); }

  setSettings(patch) {
    Object.assign(this.settings, patch);
    if (patch.rpcUrl) this.reader = new ChainReader(this.settings.rpcUrl);
    this.save();
  }

  // Opens the account with $10 worth of SOL at the live SOL price.
  ensureFunded() {
    if (this.acct) return true;
    const px = this.solUsd();
    if (!(px > 0)) return false;
    const lamports = BigInt(Math.floor(START_USD / px * 1e9));
    this.acct = {
      createdAt: this.clock(), startUsd: START_USD, startSolUsd: px, startLamports: lamports, cash: lamports,
      positions: {}, trades: [],
      totals: { pumpFees: 0n, lpFees: 0n, networkFees: 0n, rentPaid: 0n, rentRefunded: 0n, realized: 0n },
    };
    this.save();
    return true;
  }

  reset() {
    this.acct = null;
    this.marks.clear();
    this.ensureFunded();
    this.save();
  }

  networkFee() { return BASE_FEE_LAMPORTS + BigInt(Math.round(this.settings.priorityFeeSol * 1e9)); }

  rentFor(tokenProgram) { return tokenProgram === TOKEN_2022_PROGRAM ? RENT_TOKEN_ACCOUNT.token2022 : RENT_TOKEN_ACCOUNT.legacy; }

  // SOL that can go into a trade while keeping enough for the fees of the exits.
  freeLamports() {
    const reserve = this.networkFee() * 4n + RENT_TOKEN_ACCOUNT.token2022;
    const free = this.acct.cash - reserve;
    return free > 0n ? free : 0n;
  }

  // Trade log entries are display records: lamport fields become SOL, raw token amounts become whole tokens.
  log(entry) {
    const out = { t: this.clock(), sol_usd: this.solUsd() };
    for (const [k, v] of Object.entries(entry)) {
      out[k] = typeof v === "bigint" ? Number(v) / (TOKEN_FIELDS.has(k) ? 1e6 : 1e9) : v;
    }
    this.acct.trades.unshift(out);
    if (this.acct.trades.length > 300) this.acct.trades.length = 300;
  }

  descriptor(mint, tok) {
    const pos = this.acct && this.acct.positions[mint];
    const d = {
      mint,
      curve: (tok && tok.pair_address) || (pos && pos.curve) || "",
      pool: (tok && tok.pool_address) || (pos && pos.pool) || "",
      migrated: Boolean((tok && tok.migrated) || (pos && pos.migrated)),
    };
    if (pos) Object.assign(pos, { curve: d.curve, pool: d.pool, migrated: d.migrated });
    return d;
  }

  // ---- trading

  async onSignal(sig, tok) {
    if (!this.settings.autoTrade || !this.ensureFunded()) return null;
    if (this.acct.positions[sig.mint]) return null;
    if (Object.keys(this.acct.positions).length >= this.settings.maxPositions) {
      this.log({ side: "BUY", mint: sig.mint, symbol: sig.symbol, status: "skipped", reason: `already holding ${this.settings.maxPositions} positions` });
      this.save();
      return null;
    }
    const size = this.freeLamports() * BigInt(Math.round(this.settings.sizePct * 100)) / 10_000n;
    return this.buy(tok, size, { signal: sig });
  }

  async buy(tok, lamports, { signal = null } = {}) {
    if (!this.ensureFunded()) return null;
    const mint = tok.mint;
    if (this.pending.has(mint)) return null;
    const base = { side: "BUY", mint, symbol: tok.symbol, name: tok.name };
    if (lamports < 100_000n) { // 0.0001 SOL
      this.log({ ...base, status: "skipped", reason: "balance too small for a trade after fees" });
      this.save();
      return null;
    }
    this.pending.add(mint);
    try {
      const t0 = this.clock();
      const desc = this.descriptor(mint, tok);
      const quoted = await this.reader.venue(desc);
      const q = quoteBuy(quoted, lamports);
      if (!q) {
        this.log({ ...base, status: "skipped", reason: `not tradable: ${quoted.reason || "no quote"}`, lamports_in: lamports });
        return null;
      }
      const slipUnits = BigInt(Math.round(this.settings.slippagePct * 100));
      const minTokens = q.tokens * (10_000n - slipUnits) / 10_000n;
      await this.sleep(this.settings.latencyMs);
      const landed = await this.reader.venue(desc);
      const fill = quoteBuy(landed, lamports);
      const network = this.networkFee();
      const timing = { quoted_at: t0, landed_at: this.clock(), venue: landed.kind };
      if (!fill || fill.tokens < minTokens) {
        this.acct.cash -= network;
        this.acct.totals.networkFees += network;
        const reason = !fill ? `not tradable when it landed: ${landed.reason || "no quote"}`
          : `slippage limit hit: price moved ${((Number(q.tokens) / Number(fill.tokens) - 1) * 100).toFixed(1)}% before it landed`;
        this.log({ ...base, ...timing, status: "failed", reason, lamports_in: lamports, network_fee: network,
          expected_tokens: q.tokens, min_tokens: minTokens, got_tokens: fill ? fill.tokens : 0n });
        return null;
      }
      const pos = this.acct.positions[mint];
      const rent = pos ? 0n : this.rentFor(landed.tokenProgram);
      if (this.acct.cash < fill.total + network + rent) {
        this.log({ ...base, ...timing, status: "skipped", reason: "insufficient SOL for amount + fees + rent" });
        return null;
      }
      this.acct.cash -= fill.total + network + rent;
      const pumpFees = fill.protocolFee + fill.creatorFee;
      Object.assign(this.acct.totals, {
        pumpFees: this.acct.totals.pumpFees + pumpFees, lpFees: this.acct.totals.lpFees + fill.lpFee,
        networkFees: this.acct.totals.networkFees + network, rentPaid: this.acct.totals.rentPaid + rent,
      });
      const entrySpot = fill.spotBefore;
      const avg = toSol(fill.total) / (Number(fill.tokens) / 1e6);
      if (pos) {
        pos.tokens += fill.tokens;
        pos.cost += fill.total;
      } else {
        this.acct.positions[mint] = {
          mint, symbol: tok.symbol, name: tok.name, curve: desc.curve, pool: desc.pool, migrated: desc.migrated,
          tokens: fill.tokens, cost: fill.total, rent, opened: this.clock(), entry_spot: entrySpot,
          entry_mcap_usd: tok.snap ? tok.snap.mcap_usd : null,
          plan: signal
            ? { sl: signal.stop_loss_pct, tp1: signal.take_profit_1_pct, tp2: signal.take_profit_2_pct, signal_id: signal.id }
            : { sl: -20, tp1: 30, tp2: 60, signal_id: null },
          tp1_done: false,
        };
      }
      this.log({
        ...base, ...timing, status: "filled", reason: signal ? `signal ${signal.agree}/25` : "manual",
        lamports_in: fill.total, tokens: fill.tokens, quoted_spot: q.spotBefore, spot_at_fill: fill.spotBefore,
        avg_price: avg, price_impact_pct: (avg / fill.spotBefore - 1) * 100,
        slippage_pct: (Number(q.tokens) / Number(fill.tokens) - 1) * 100,
        pump_fees: pumpFees, lp_fee: fill.lpFee, network_fee: network, rent,
      });
      return this.acct.positions[mint];
    } finally {
      this.pending.delete(mint);
      this.save();
    }
  }

  async sell(mint, fraction, reason, tok = null) {
    const pos = this.acct && this.acct.positions[mint];
    if (!pos || this.pending.has(mint)) return null;
    const amount = fraction >= 1 ? pos.tokens : pos.tokens * BigInt(Math.round(fraction * 10_000)) / 10_000n;
    if (amount <= 0n) return null;
    const base = { side: "SELL", mint, symbol: pos.symbol, name: pos.name };
    this.pending.add(mint);
    try {
      const t0 = this.clock();
      const desc = this.descriptor(mint, tok);
      const quoted = await this.reader.venue(desc);
      if (quoted.kind === "migrating") pos.migrated = true;
      const q = quoteSell(quoted, amount);
      if (!q) {
        this.log({ ...base, status: "skipped", reason: `cannot sell yet: ${quoted.reason || "no quote"} (${reason})` });
        return null;
      }
      const slipUnits = BigInt(Math.round(this.settings.slippagePct * 100));
      const minOut = q.net * (10_000n - slipUnits) / 10_000n;
      await this.sleep(this.settings.latencyMs);
      const landed = await this.reader.venue(desc);
      const fill = quoteSell(landed, amount);
      const network = this.networkFee();
      const timing = { quoted_at: t0, landed_at: this.clock(), venue: landed.kind };
      if (!fill || fill.net < minOut) {
        this.acct.cash -= network;
        this.acct.totals.networkFees += network;
        const why = !fill ? `not tradable when it landed: ${landed.reason || "no quote"}`
          : `slippage limit hit: price fell ${((1 - Number(fill.net) / Number(q.net)) * 100).toFixed(1)}% before it landed`;
        this.log({ ...base, ...timing, status: "failed", reason: `${why} (${reason}); will retry`, tokens: amount,
          network_fee: network, expected_out: q.net, min_out: minOut, got_out: fill ? fill.net : 0n });
        return null;
      }
      const costShare = pos.cost * amount / pos.tokens;
      const closing = amount === pos.tokens;
      const refund = closing && this.settings.closeAccounts ? pos.rent : 0n;
      this.acct.cash += fill.net - network + refund;
      const pumpFees = fill.protocolFee + fill.creatorFee;
      Object.assign(this.acct.totals, {
        pumpFees: this.acct.totals.pumpFees + pumpFees, lpFees: this.acct.totals.lpFees + fill.lpFee,
        networkFees: this.acct.totals.networkFees + network, rentRefunded: this.acct.totals.rentRefunded + refund,
        realized: this.acct.totals.realized + fill.net - costShare,
      });
      pos.tokens -= amount;
      pos.cost -= costShare;
      const avg = toSol(fill.net) / (Number(amount) / 1e6);
      this.log({
        ...base, ...timing, status: "filled", reason, tokens: amount, lamports_out: fill.net,
        quoted_spot: q.spotBefore, spot_at_fill: fill.spotBefore, avg_price: avg,
        price_impact_pct: (avg / fill.spotBefore - 1) * 100,
        slippage_pct: (1 - Number(fill.net) / Number(q.net)) * 100,
        pnl_lamports: fill.net - costShare, pump_fees: pumpFees, lp_fee: fill.lpFee, network_fee: network, rent_refund: refund,
      });
      if (closing) {
        delete this.acct.positions[mint];
        this.marks.delete(mint);
      } else if (reason.startsWith("take profit 1")) {
        pos.tp1_done = true;
      }
      return fill;
    } finally {
      this.pending.delete(mint);
      this.save();
    }
  }

  // Value every open position on-chain and run its exit plan. Called every ~2 s while scanning.
  async monitor(getTok) {
    if (!this.acct) return;
    const now = this.clock();
    for (const pos of Object.values(this.acct.positions)) {
      if (this.pending.has(pos.mint)) continue;
      const tok = getTok(pos.mint);
      const venue = await this.reader.venue(this.descriptor(pos.mint, tok));
      if (venue.kind === "migrating") pos.migrated = true;
      const spot = venueSpot(venue);
      const exit = quoteSell(venue, pos.tokens);
      this.marks.set(pos.mint, {
        t: now, venue: venue.kind, reason: venue.reason || "", spot,
        value: exit ? exit.net : null, change_pct: spot ? (spot / pos.entry_spot - 1) * 100 : null,
      });
      if (!spot) continue;
      const chg = (spot / pos.entry_spot - 1) * 100;
      const held = (now - pos.opened) / 60;
      pos.peak_spot = Math.max(pos.peak_spot || pos.entry_spot, spot);
      const fromPeak = (spot / pos.peak_spot - 1) * 100;
      let action = null;
      if (chg <= pos.plan.sl) action = [1, `stop loss (${chg.toFixed(1)}%)`];
      else if (pos.tp1_done && this.settings.breakevenAfterTp1 && chg <= 0) action = [1, `break-even stop after take-profit 1 (${chg.toFixed(1)}%)`];
      else if (pos.tp1_done && fromPeak <= -this.settings.trailAfterTp1Pct) action = [1, `trailing stop: ${fromPeak.toFixed(1)}% from the peak`];
      else if (!pos.tp1_done && chg >= pos.plan.tp1) action = [0.5, `take profit 1 (+${chg.toFixed(1)}%)`];
      else if (chg >= pos.plan.tp2) action = [1, `take profit 2 (+${chg.toFixed(1)}%)`];
      else if (held >= this.settings.maxHoldMin) action = [1, `time limit (${held.toFixed(0)} min)`];
      else if (this.settings.exitOnVeto && tok && tok.vetoed) action = [1, "safety agent veto"];
      if (action) await this.sell(pos.mint, action[0], action[1], tok);
    }
    this.save();
  }

  // ---- UI

  snapshot() {
    const px = this.solUsd();
    if (!this.acct) return { funded: false, settings: { ...this.settings }, sol_usd: px };
    let posValue = 0n, lockedRent = 0n;
    const positions = Object.values(this.acct.positions).map((p) => {
      const m = this.marks.get(p.mint) || {};
      const value = m.value != null ? m.value : null;
      posValue += value != null ? value : p.cost; // not valued on-chain yet (just bought / no venue): count at cost
      lockedRent += p.rent;
      return {
        mint: p.mint, symbol: p.symbol, name: p.name, tokens: Number(p.tokens) / 1e6, cost_sol: toSol(p.cost),
        value_sol: value != null ? toSol(value) : null, pnl_pct: value != null && p.cost > 0n ? (Number(value) / Number(p.cost) - 1) * 100 : null,
        change_pct: m.change_pct ?? null, venue: m.venue || "?", venue_note: m.reason || "", plan: p.plan, tp1_done: p.tp1_done,
        held_min: (this.clock() - p.opened) / 60, pending: this.pending.has(p.mint),
      };
    });
    const equity = this.acct.cash + posValue + (this.settings.closeAccounts ? lockedRent : 0n);
    const t = this.acct.totals;
    return {
      funded: true, sol_usd: px, settings: { ...this.settings },
      cash_sol: toSol(this.acct.cash), positions_sol: toSol(posValue), locked_rent_sol: toSol(lockedRent),
      equity_sol: toSol(equity), equity_usd: px ? toSol(equity) * px : null,
      start_usd: this.acct.startUsd, start_sol: toSol(this.acct.startLamports),
      pnl_usd: px ? toSol(equity) * px - this.acct.startUsd : null,
      pnl_sol_pct: (Number(equity) / Number(this.acct.startLamports) - 1) * 100,
      totals: { pump_fees_sol: toSol(t.pumpFees), lp_fees_sol: toSol(t.lpFees), network_fees_sol: toSol(t.networkFees),
        rent_paid_sol: toSol(t.rentPaid), rent_refunded_sol: toSol(t.rentRefunded), realized_sol: toSol(t.realized) },
      positions,
      trades: this.acct.trades.slice(0, 60),
      rpc_error: this.reader.lastError, rpc_url: this.reader.url,
    };
  }
}

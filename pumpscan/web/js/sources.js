// Live data straight from the browser: PumpPortal (launches, migrations), DexScreener, Rugcheck,
// IPFS metadata. All of them allow cross-origin requests, so no server is needed.
// Parsing is a port of pumpscan/sources.py and kept in plain functions so it can be tested offline.

import { Token, makeRugReport, makeSnapshot } from "./core.js";

export const PUMPPORTAL_WS = "wss://pumpportal.fun/api/data";
const DEX_TOKENS = "https://api.dexscreener.com/tokens/v1/solana/";
const DEX_LISTINGS = [
  "https://api.dexscreener.com/token-profiles/latest/v1",
  "https://api.dexscreener.com/token-boosts/latest/v1",
  "https://api.dexscreener.com/token-boosts/top/v1",
];
const RUGCHECK_REPORT = (mint) => `https://api.rugcheck.xyz/v1/tokens/${mint}/report`;
// Public gateways rate-limit hard (ipfs.io answers 429 under load); pump.fun's own pin gateway goes first.
const IPFS_GATEWAYS = [
  "https://pump.mypinata.cloud/ipfs/",
  "https://ipfs.io/ipfs/",
  "https://gateway.pinata.cloud/ipfs/",
  "https://dweb.link/ipfs/",
];

const num = (x) => Number(x) || 0;
const str = (x, max = 500) => String(x ?? "").slice(0, max);

// ------------------------------------------------------------------ parsing

export const isPumpMint = (mint) => typeof mint === "string" && mint.endsWith("pump");

// A PumpPortal `create` event -> Token. Only pump.fun coins are kept.
export function parseCreateEvent(msg, now) {
  if (msg.txType !== "create" || (msg.pool ?? "pump") !== "pump" || !msg.mint) return null;
  return new Token({
    mint: msg.mint, name: str(msg.name, 64), symbol: str(msg.symbol, 24), created_at: now, first_seen: now,
    creator: msg.traderPublicKey || "", pool: "pump", dev_buy_sol: num(msg.solAmount), dev_tokens: num(msg.initialBuy),
    uri: msg.uri || "", pair_address: msg.bondingCurveKey || "",
  });
}

export function parseTradeEvent(msg, now) {
  if ((msg.txType !== "buy" && msg.txType !== "sell") || !msg.mint) return null;
  return [msg.mint, { t: now, trader: msg.traderPublicKey || "", is_buy: msg.txType === "buy", sol: num(msg.solAmount), mcap_sol: num(msg.marketCapSol) }];
}

export function parseMetadata(tok, meta) {
  tok.description = str(meta.description);
  tok.twitter = tok.twitter || str(meta.twitter);
  tok.telegram = tok.telegram || str(meta.telegram);
  tok.website = tok.website || str(meta.website);
  tok.image = tok.image || str(meta.image);
  tok.metadata_loaded = true;
}

// Prefer the pump.fun curve or PumpSwap pool; otherwise the deepest pool.
export function pickPair(pairs) {
  if (!pairs || !pairs.length) return null;
  for (const dex of ["pumpswap", "pumpfun"]) {
    const p = pairs.find((x) => x.dexId === dex);
    if (p) return p;
  }
  return pairs.reduce((best, p) => ((p.liquidity || {}).usd || 0) > ((best.liquidity || {}).usd || 0) ? p : best);
}

// Apply a DexScreener pair to the token. Returns the implied SOL/USD price, if any.
export function parseDexPair(tok, pair, now) {
  const txns = pair.txns || {}, vol = pair.volume || {}, chg = pair.priceChange || {};
  const m5 = txns.m5 || {}, h1 = txns.h1 || {};
  const price = num(pair.priceUsd);
  const mcap = num(pair.marketCap || pair.fdv);
  if (price <= 0 || mcap <= 0) return null;
  const liq = (pair.liquidity || {}).usd;
  tok.addSnapshot(makeSnapshot({
    t: now, price_usd: price, mcap_usd: mcap,
    buys_m5: num(m5.buys), sells_m5: num(m5.sells), buys_h1: num(h1.buys), sells_h1: num(h1.sells),
    vol_m5: num(vol.m5), vol_h1: num(vol.h1), change_m5: num(chg.m5), change_h1: num(chg.h1),
    liquidity_usd: liq != null ? num(liq) : null,
  }));
  tok.dex_id = pair.dexId || tok.dex_id;
  tok.migrated = tok.migrated || (tok.dex_id !== "pumpfun" && tok.dex_id !== "");
  if (tok.dex_id === "pumpfun" && pair.pairAddress) tok.pair_address = pair.pairAddress; // = bonding curve account
  if (tok.dex_id === "pumpswap" && pair.pairAddress) tok.pool_address = pair.pairAddress; // = PumpSwap pool account
  const base = pair.baseToken || {};
  tok.name = tok.name || str(base.name, 64);
  tok.symbol = tok.symbol || str(base.symbol, 24);
  if (pair.pairCreatedAt) {
    tok.pair_created_at = pair.pairCreatedAt / 1000;
    if (tok.created_at == null && tok.dex_id === "pumpfun") tok.created_at = tok.pair_created_at;
  }
  const info = pair.info;
  if (info && Object.keys(info).length) {
    tok.has_profile = true;
    tok.image = tok.image || str(info.imageUrl);
    for (const soc of info.socials || []) {
      if (soc.type === "twitter") tok.twitter = tok.twitter || str(soc.url);
      else if (soc.type === "telegram") tok.telegram = tok.telegram || str(soc.url);
    }
    for (const site of info.websites || []) tok.website = tok.website || str(site.url);
  }
  tok.boosts = Math.max(tok.boosts, num((pair.boosts || {}).active));
  const native = num(pair.priceNative);
  if ((pair.quoteToken || {}).symbol === "SOL" && native > 0) return price / native;
  return null;
}

export function parseIso(ts) {
  if (!ts) return null;
  const ms = Date.parse(String(ts).replace(/(\.\d{3})\d+/, "$1"));
  return Number.isFinite(ms) ? ms / 1000 : null;
}

export function parseRugReport(d, tok, now) {
  const tokenInfo = d.token || {};
  const supply = num(tokenInfo.supply);
  const known = d.knownAccounts || {};
  const excluded = new Set(Object.entries(known).filter(([, k]) => ["AMM", "LOCKER"].includes((k || {}).type)).map(([a]) => a));
  for (const m of d.markets || []) if (m.pubkey) excluded.add(m.pubkey);
  if (tok.pair_address) excluded.add(tok.pair_address);
  const holders = (d.topHolders || []).filter((h) => !excluded.has(h.owner) && !excluded.has(h.address));
  const pcts = holders.map((h) => num(h.pct));
  const risks = d.risks || [];
  const meta = d.tokenMeta || {}, file = d.fileMeta || {};
  return makeRugReport({
    fetched_at: now,
    score_normalised: d.score_normalised ?? null,
    mint_authority: Boolean(d.mintAuthority || tokenInfo.mintAuthority),
    freeze_authority: Boolean(d.freezeAuthority || tokenInfo.freezeAuthority),
    rugged: Boolean(d.rugged),
    total_holders: d.totalHolders ?? null,
    top10_pct: d.topHolders != null ? pcts.slice(0, 10).reduce((a, b) => a + b, 0) : null,
    max_holder_pct: pcts.length ? Math.max(...pcts) : 0,
    insider_pct: holders.filter((h) => h.insider).reduce((a, h) => a + num(h.pct), 0),
    insiders_detected: num(d.graphInsidersDetected),
    creator_pct: d.creatorBalance != null && supply > 0 ? num(d.creatorBalance) / supply * 100 : null,
    creator_token_count: Array.isArray(d.creatorTokens) ? d.creatorTokens.length : null,
    danger_risks: risks.filter((r) => r.level === "danger").map((r) => str(r.name)),
    warn_risks: risks.filter((r) => r.level === "warn").map((r) => str(r.name)),
    creator: str(d.creator), detected_at: parseIso(d.detectedAt),
    name: str(meta.name, 64), symbol: str(meta.symbol, 24), uri: str(meta.uri),
    description: str(file.description), image: str(file.image),
  });
}

// Store the report and fill facts the scan lacked. True when the metadata JSON should be fetched.
export function applyRugReport(tok, r) {
  tok.rug = r;
  if (r.total_holders != null) tok.addHolders(r.fetched_at, r.total_holders);
  tok.creator = tok.creator || r.creator;
  tok.name = tok.name || r.name;
  tok.symbol = tok.symbol || r.symbol;
  if (tok.created_at == null && r.detected_at) tok.created_at = r.detected_at;
  if (tok.metadata_unreachable && !tok.description) {
    tok.description = r.description;
    tok.image = tok.image || r.image;
  }
  if (tok.metadata_loaded) return false;
  if (r.uri && !tok.uri) { tok.uri = r.uri; return true; }
  parseMetadata(tok, { description: r.description, image: r.image });
  return false;
}

// DexScreener profiles/boosts -> pump.fun mints with their marketing flags.
export function parseListing(items) {
  const out = [];
  for (const it of items || []) {
    const mint = it.tokenAddress || "";
    if (it.chainId !== "solana" || !isPumpMint(mint)) continue;
    out.push({ mint, boosts: num(it.totalAmount || it.amount), cto: Boolean(it.cto), description: str(it.description), links: it.links || [] });
  }
  return out;
}

// ------------------------------------------------------------------ network

// At most `rate` calls per `per` seconds, spread evenly.
export class RateLimiter {
  constructor(rate, per) { this.interval = (per * 1000) / rate; this.next = 0; }
  async wait(signal) {
    const now = performance.now();
    const delay = this.next - now;
    this.next = Math.max(now, this.next) + this.interval;
    if (delay > 0) await sleep(delay, signal);
  }
}

export function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(signal.reason);
    const t = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => { clearTimeout(t); reject(signal.reason); }, { once: true });
  });
}

export class Http {
  constructor(signal) {
    this.signal = signal;
    this.dexLimit = new RateLimiter(250, 60); // DexScreener allows 300/min on these endpoints
    this.rugLimit = new RateLimiter(50, 60);
    this.errors = {};
    this.blockedHosts = new Set(); // metadata hosts without CORS: the browser can never read them
  }

  async getJson(url, source, timeoutMs = 12000) {
    const ctl = new AbortController();
    const onAbort = () => ctl.abort();
    this.signal.addEventListener("abort", onAbort, { once: true });
    const timer = setTimeout(() => ctl.abort(), timeoutMs);
    try {
      const r = await fetch(url, { signal: ctl.signal });
      if (r.status === 429) {
        this.errors[source] = "rate limited (429)";
        await sleep(5000, this.signal);
        return null;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      delete this.errors[source];
      return data;
    } catch (e) {
      if (this.signal.aborted) throw e;
      this.errors[source] = String(e.message || e).slice(0, 120);
      return null;
    } finally {
      clearTimeout(timer);
      this.signal.removeEventListener("abort", onAbort);
    }
  }

  async dexTokens(mints) {
    await this.dexLimit.wait(this.signal);
    const data = await this.getJson(DEX_TOKENS + mints.slice(0, 30).join(","), "dexscreener");
    return Array.isArray(data) ? data : [];
  }

  async dexListings() {
    const out = [];
    for (const url of DEX_LISTINGS) {
      await this.dexLimit.wait(this.signal);
      const data = await this.getJson(url, "dexscreener");
      out.push(...parseListing(Array.isArray(data) ? data : []));
    }
    return out;
  }

  async rugReport(mint) {
    await this.rugLimit.wait(this.signal);
    const data = await this.getJson(RUGCHECK_REPORT(mint), "rugcheck");
    return data && typeof data === "object" && !Array.isArray(data) ? data : null;
  }

  // Fetch the coin's metadata JSON; IPFS URIs are tried across several gateways.
  // Returns {data} on success, or {blocked: true} when the host refuses browser (CORS) access, else null.
  async metadata(uri) {
    if (!/^https?:\/\//.test(uri)) return null;
    const ipfs = uri.includes("/ipfs/");
    const urls = ipfs ? IPFS_GATEWAYS.map((g) => g + uri.split("/ipfs/")[1]) : [uri];
    for (const url of urls) {
      let host;
      try { host = new URL(url).host; } catch { return null; }
      if (this.blockedHosts.has(host)) return { blocked: true };
      const ctl = new AbortController();
      const onAbort = () => ctl.abort();
      this.signal.addEventListener("abort", onAbort, { once: true });
      const timer = setTimeout(() => ctl.abort(), 8000);
      try {
        const r = await fetch(url, { signal: ctl.signal });
        if (r.ok) {
          const d = await r.json();
          if (d && typeof d === "object" && !Array.isArray(d)) return { data: d };
        }
      } catch (e) {
        if (this.signal.aborted) throw e;
        // A non-gateway host that fails instantly (not by our timeout) is refusing cross-origin reads.
        if (!ipfs && !ctl.signal.aborted && e instanceof TypeError) {
          this.blockedHosts.add(host);
          return { blocked: true };
        }
      } finally {
        clearTimeout(timer);
        this.signal.removeEventListener("abort", onAbort);
      }
    }
    return null;
  }
}

// Keep one PumpPortal websocket open (their rules: one connection), reconnecting with backoff.
export function pumpportalStream(onMessage, status, signal) {
  let backoff = 2000, ws = null, timer = null;
  const connect = () => {
    if (signal.aborted) return;
    status.pumpportal = "connecting";
    ws = new WebSocket(PUMPPORTAL_WS);
    ws.onopen = () => {
      status.pumpportal = "connected";
      backoff = 2000;
      ws.send(JSON.stringify({ method: "subscribeNewToken" }));
      ws.send(JSON.stringify({ method: "subscribeMigration" }));
    };
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      onMessage(msg);
    };
    ws.onclose = () => {
      if (signal.aborted) { status.pumpportal = "stopped"; return; }
      status.pumpportal = `reconnecting in ${Math.round(backoff / 1000)}s`;
      timer = setTimeout(connect, backoff);
      backoff = Math.min(60000, backoff * 2);
    };
    ws.onerror = () => { try { ws.close(); } catch { /* already closed */ } };
  };
  signal.addEventListener("abort", () => {
    clearTimeout(timer);
    if (ws) ws.close();
    status.pumpportal = "stopped";
  }, { once: true });
  connect();
}

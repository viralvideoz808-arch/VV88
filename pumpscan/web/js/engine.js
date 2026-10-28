// The engine behind the Start / Stop button, running inside the page (port of pumpscan/engine.py).
//
// Start launches concurrent loops:
//   stream    PumpPortal websocket: every new pump.fun launch and graduation
//   seed      DexScreener profiles/boosts: pump.fun coins already running
//   dex       DexScreener market data for every watched coin, 30 coins per request
//   meta      IPFS metadata (description, socials) for new coins
//   deep      Rugcheck on-chain report for the coins the council likes
//   council   all 25 agents vote on every coin every pass; consensus -> BUY signal
//   prune     drop dead coins so the scan stays fast
// Stop aborts them all.

import {
  AGENTS, AGENTS_BY_ID, AVOID, MarketContext, Token, buildSignal, compareRank,
  curveProgressFromMcapSol, defaultRules, runCouncil, scoreOutcome,
} from "./core.js";
import {
  Http, applyRugReport, parseCreateEvent, parseDexPair, parseMetadata, parseRugReport,
  parseTradeEvent, pickPair, pumpportalStream, sleep,
} from "./sources.js";

const WATCH_CAP = 1500;

function storageGet(key) {
  try { return JSON.parse(localStorage.getItem(key) || "null"); } catch { return null; }
}
function storageSet(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode or quota: history stays in memory */ }
}

export class Engine {
  constructor({ demo = false, demoSpeed = 10, makeSimulator = null } = {}) {
    this.demo = demo;
    this.demoSpeed = demoSpeed;
    this.makeSimulator = makeSimulator;
    this.rules = { ...defaultRules(), ...(storageGet("pumpscan.rules") || {}) };
    this.running = false;
    this.started_at = null;
    this.tokens = new Map();
    this.ctx = new MarketContext();
    this.signals = (storageGet(this.storageKey()) || []).slice(-500);
    this.status = {};
    this.analyzed = new Set();
    this.launches_seen = 0;
    this.passes = 0;
    this.last_pass_ms = 0;
    this.agent_tally = {};
    this.metaQueue = [];
    this.abort = null;
    this.sim = null;
  }

  storageKey() { return this.demo ? "pumpscan.signals.demo" : "pumpscan.signals"; }

  clock() { return this.sim ? this.sim.clock() : Date.now() / 1000; }

  // ------------------------------------------------------------ lifecycle

  start() {
    if (this.running) return;
    this.running = true;
    this.abort = new AbortController();
    const signal = this.abort.signal;
    this.tokens = new Map();
    this.ctx = new MarketContext();
    this.ctx.tokens = this.tokens;
    this.analyzed = new Set();
    this.launches_seen = 0;
    this.passes = 0;
    this.metaQueue = [];
    const loops = [this.councilLoop(signal), this.pruneLoop(signal)];
    if (this.demo) {
      this.sim = this.makeSimulator(this, this.demoSpeed);
      this.status = { simulator: "running (SIMULATED market, not real coins)" };
      loops.push(this.sim.run(signal));
    } else {
      this.sim = null;
      this.http = new Http(signal);
      this.status = { pumpportal: "connecting" };
      pumpportalStream((msg) => this.onStream(msg), this.status, signal);
      loops.push(this.seedLoop(signal), this.dexLoop(signal), this.deepLoop(signal));
      for (let i = 0; i < 6; i++) loops.push(this.metaWorker(signal));
    }
    this.started_at = this.clock();
    for (const p of loops) {
      p.catch((e) => {
        if (signal.aborted) return;
        console.error("pumpscan loop crashed", e);
        this.status.engine = `a loop crashed: ${e.message || e}`;
      });
    }
  }

  stop() {
    if (!this.running) return;
    this.running = false;
    this.abort.abort(new DOMException("stopped", "AbortError"));
    for (const k of Object.keys(this.status)) this.status[k] = "stopped";
    this.updateSignals(this.clock());
    this.saveSignals();
  }

  setRules(patch) {
    Object.assign(this.rules, patch);
    storageSet("pumpscan.rules", { min_buy_votes: this.rules.min_buy_votes, window_minutes: this.rules.window_minutes });
  }

  // ------------------------------------------------------------ token intake

  addToken(tok) {
    const existing = this.tokens.get(tok.mint);
    if (existing) return existing;
    if (this.tokens.size >= WATCH_CAP) this.evict(1);
    this.tokens.set(tok.mint, tok);
    this.ctx.now = this.clock();
    this.ctx.noteLaunch(tok);
    return tok;
  }

  onStream(msg) {
    const now = this.clock();
    if (msg.txType === "create") {
      const tok = parseCreateEvent(msg, now);
      if (tok) {
        this.launches_seen++;
        this.addToken(tok);
        if (tok.uri) this.metaQueue.push(tok.mint);
      }
    } else if (msg.txType === "migrate") {
      if (msg.mint) this.addToken(new Token({ mint: msg.mint, first_seen: now })).migrated = true;
    } else {
      const parsed = parseTradeEvent(msg, now);
      if (parsed && this.tokens.has(parsed[0])) this.tokens.get(parsed[0]).addTrade(parsed[1]);
    }
  }

  // ------------------------------------------------------------ live loops

  async seedLoop(signal) {
    while (!signal.aborted) {
      const now = this.clock();
      for (const item of await this.http.dexListings()) {
        const tok = this.addToken(new Token({ mint: item.mint, first_seen: now }));
        tok.has_profile = true;
        tok.cto = tok.cto || item.cto;
        tok.boosts = Math.max(tok.boosts, item.boosts);
        if (item.description && !tok.metadata_loaded) {
          tok.description = item.description;
          tok.metadata_loaded = true;
        }
        for (const link of item.links) {
          const url = String(link.url || "");
          const kind = String(link.type || link.label || "").toLowerCase();
          if (kind === "twitter") tok.twitter = tok.twitter || url;
          else if (kind === "telegram") tok.telegram = tok.telegram || url;
          else if (kind === "website") tok.website = tok.website || url;
        }
      }
      this.status.seed = `ok (${this.tokens.size} watched)`;
      await sleep(60000, signal);
    }
  }

  async dexLoop(signal) {
    while (!signal.aborted) {
      const t0 = performance.now();
      let now = this.clock();
      const due = (tok) => {
        const hot = (tok.buy_votes >= 14 && !tok.vetoed) || this.isSignalLive(tok.mint) ? 3 : 1;
        return (now - tok.last_dex) * hot;
      };
      const mints = [...this.tokens.values()].filter((t) => now - t.last_dex >= 6)
        .sort((a, b) => due(b) - due(a)).slice(0, 30 * 30).map((t) => t.mint);
      const batches = [];
      for (let i = 0; i < mints.length; i += 30) batches.push(mints.slice(i, i + 30));
      const results = await Promise.all(batches.map((b) => this.http.dexTokens(b)));
      now = this.clock();
      batches.forEach((batch, i) => {
        const byMint = new Map();
        for (const p of results[i]) {
          const m = (p.baseToken || {}).address || "";
          if (!byMint.has(m)) byMint.set(m, []);
          byMint.get(m).push(p);
        }
        for (const mint of batch) {
          const tok = this.tokens.get(mint);
          if (!tok) continue;
          tok.last_dex = now;
          const pair = pickPair(byMint.get(mint));
          if (!pair) { tok.dex_misses++; continue; }
          const sol = parseDexPair(tok, pair, now);
          if (sol && sol > 10 && sol < 10000) this.ctx.sol_usd = sol;
        }
      });
      const err = this.http.errors.dexscreener;
      this.status.dexscreener = err ? `error: ${err}` : `ok (${mints.length} coins/pass)`;
      await sleep(Math.max(1000, 5000 - (performance.now() - t0)), signal);
    }
  }

  async metaWorker(signal) {
    while (!signal.aborted) {
      const mint = this.metaQueue.shift();
      if (!mint) { await sleep(500, signal); continue; }
      const tok = this.tokens.get(mint);
      if (!tok || tok.metadata_loaded) continue;
      const res = await this.http.metadata(tok.uri);
      if (res && res.data) {
        parseMetadata(tok, res.data);
      } else if (res && res.blocked) {
        // the host refuses browser access: agents abstain on socials instead of reading "no socials"
        tok.metadata_unreachable = true;
        tok.metadata_loaded = true;
        if (tok.rug && !tok.description) { tok.description = tok.rug.description; tok.image = tok.image || tok.rug.image; }
      } else if (tok.meta_attempts < 2) {
        // gateways throttle in bursts: retry later instead of judging the coin on missing data
        tok.meta_attempts++;
        sleep(20000 * tok.meta_attempts, signal).then(() => this.metaQueue.push(mint), () => {});
      } else {
        tok.metadata_loaded = true; // unreachable after 3 tries: treat as empty
      }
    }
  }

  // Rugcheck is rate limited, so it goes to the coins closest to a signal first.
  async deepLoop(signal) {
    while (!signal.aborted) {
      let now = this.clock();
      const cands = [...this.tokens.values()].filter((t) => t.snap && !t.vetoed && t.buy_votes >= 11
        && now - t.rug_attempt > 60 && (!t.rug || now - t.rug.fetched_at > 180));
      cands.sort((a, b) => ((!b.rug) - (!a.rug)) || (b.buy_votes - a.buy_votes) || (b.conviction - a.conviction));
      if (!cands.length) { await sleep(1000, signal); continue; }
      const tok = cands[0];
      tok.rug_attempt = now;
      const data = await this.http.rugReport(tok.mint);
      now = this.clock();
      if (data) {
        if (applyRugReport(tok, parseRugReport(data, tok, now))) this.metaQueue.push(tok.mint);
      } else {
        await sleep(2000, signal);
      }
      const err = this.http.errors.rugcheck;
      this.status.rugcheck = err ? `error: ${err}` : "ok";
    }
  }

  // ------------------------------------------------------------ council

  async councilLoop(signal) {
    while (!signal.aborted) {
      this.councilPass();
      await sleep(2000, signal);
    }
  }

  // Every agent votes on every coin with market data. Returns new signals.
  councilPass() {
    const t0 = performance.now();
    const now = this.clock();
    const ctx = this.ctx;
    ctx.now = now;
    ctx.tokens = this.tokens;
    ctx.symbol_index = new Map();
    for (const tok of this.tokens.values()) if (tok.symbol) ctx.indexSymbol(tok);
    ctx.refreshHotWords();
    const tally = Object.fromEntries(AGENTS.map((a) => [a.id, {}]));
    const fresh = [];
    for (const tok of [...this.tokens.values()]) {
      if (!tok.snapshots.length) continue;
      const verdict = runCouncil(tok, ctx, this.rules);
      this.analyzed.add(tok.mint);
      for (const v of verdict.votes) tally[v.agent][v.stance] = (tally[v.agent][v.stance] || 0) + 1;
      if (verdict.is_signal && this.cooldownOk(tok, now)) {
        const sig = buildSignal(tok, verdict, this.rules, now);
        tok.signaled_at = now;
        this.signals.push(sig);
        fresh.push(sig);
      }
    }
    this.updateSignals(now);
    this.agent_tally = tally;
    this.passes++;
    this.last_pass_ms = performance.now() - t0;
    if (fresh.length) this.saveSignals();
    return fresh;
  }

  cooldownOk(tok, now) {
    return tok.signaled_at == null || now - tok.signaled_at >= this.rules.cooldown_minutes * 60;
  }

  isSignalLive(mint) {
    return this.signals.slice(-20).some((s) => s.mint === mint && (s.state || "active") === "active");
  }

  updateSignals(now) {
    let changed = false;
    for (const sig of this.signals) {
      if (sig.outcome) continue;
      const tok = this.tokens.get(sig.mint);
      const state = sig.state || "active";
      if (now >= sig.expires) {
        if (state === "active") sig.state = "expired";
        let out = scoreOutcome(sig, tok, now);
        if (!out && !this.running) out = { status: "no_data" };
        if (out) { sig.outcome = out; changed = true; }
        continue;
      }
      if (!tok || state !== "active") continue;
      const s = tok.snap;
      if (s && s.mcap_usd > sig.max_entry_mcap_usd) {
        sig.state = "void";
        sig.state_reason = "price ran past the max entry; do not chase";
        changed = true;
      } else if (tok.vetoed) {
        const who = tok.last_votes.filter((v) => v.stance === AVOID && AGENTS_BY_ID[v.agent].veto).map((v) => AGENTS_BY_ID[v.agent].name);
        sig.state = "cancelled";
        sig.state_reason = `${who.join(", ")} now votes AVOID`;
        changed = true;
      }
    }
    if (changed) this.saveSignals();
  }

  // ------------------------------------------------------------ housekeeping

  async pruneLoop(signal) {
    while (!signal.aborted) {
      await sleep(20000, signal);
      this.prune();
    }
  }

  prune() {
    const now = this.clock();
    const live = new Set(this.signals.filter((s) => !s.outcome).map((s) => s.mint));
    for (const [m, t] of [...this.tokens]) {
      if (live.has(m)) continue;
      const age = t.ageMin(now), s = t.snap;
      if ((!s && age > 25 && t.dex_misses >= 3)
        || (s && age > 30 && s.buys_m5 + s.sells_m5 < 5)
        || (s && age > 20 && s.mcap_usd < 4500 && !t.migrated)) {
        this.tokens.delete(m);
      }
    }
  }

  evict(n) {
    const live = new Set(this.signals.filter((s) => !s.outcome).map((s) => s.mint));
    const victims = [...this.tokens.values()].filter((t) => !live.has(t.mint)).sort(compareRank).reverse().slice(0, n);
    for (const t of victims) this.tokens.delete(t.mint);
  }

  saveSignals() { storageSet(this.storageKey(), this.signals.slice(-500)); }

  clearHistory() {
    this.signals = [];
    this.saveSignals();
  }

  // ------------------------------------------------------------ UI state

  trackRecord() {
    const scored = this.signals.filter((s) => s.outcome && s.outcome.status !== "no_data");
    const wins = scored.filter((s) => s.outcome.status === "win").length;
    const stopped = scored.filter((s) => s.outcome.status === "stopped").length;
    const avg = (xs) => xs.length ? Math.round(xs.reduce((a, b) => a + b, 0) / xs.length * 10) / 10 : null;
    return {
      signals: this.signals.length, scored: scored.length, wins, stopped,
      win_rate: scored.length ? Math.round(1000 * wins / scored.length) / 10 : null,
      avg_end_pct: avg(scored.map((s) => s.outcome.end_pct)),
      avg_peak_pct: avg(scored.map((s) => s.outcome.peak_pct)),
    };
  }

  coinRow(t, now) {
    const s = t.snap;
    let progress = null;
    if (s && !t.migrated && this.ctx.sol_usd > 0) progress = Math.round(100 * curveProgressFromMcapSol(s.mcap_usd / this.ctx.sol_usd));
    const vetoes = t.last_votes.filter((v) => v.stance === AVOID && AGENTS_BY_ID[v.agent].veto).map((v) => v.reason);
    return {
      mint: t.mint, name: t.name, symbol: t.symbol, image: t.image,
      mcap: s ? s.mcap_usd : null, change_m5: s ? s.change_m5 : null,
      buys_m5: s ? s.buys_m5 : null, sells_m5: s ? s.sells_m5 : null, vol_m5: s ? s.vol_m5 : null,
      age_min: Math.round(t.ageMin(now) * 10) / 10, curve_pct: progress, migrated: t.migrated,
      buy_votes: t.buy_votes, avoid_votes: t.avoid_votes, vetoed: t.vetoed,
      conviction: Math.round(t.conviction * 10) / 10, veto_reason: vetoes[0] || "",
      deep_checked: t.rug != null, votes: t.last_votes,
    };
  }

  state() {
    const now = this.clock();
    const ranked = [...this.tokens.values()].filter((t) => t.last_votes.length).sort(compareRank);
    let active = null;
    for (let i = this.signals.length - 1; i >= 0; i--) {
      const sig = this.signals[i];
      if (["active", "void", "cancelled"].includes(sig.state || "active") && now < sig.expires) {
        active = { ...sig, state: sig.state || "active", remaining_s: Math.max(0, Math.floor(sig.expires - now)) };
        const tok = this.tokens.get(sig.mint);
        if (tok && tok.snap) {
          active.now_mcap_usd = tok.snap.mcap_usd;
          active.since_entry_pct = Math.round((tok.snap.mcap_usd / sig.entry_mcap_usd - 1) * 1000) / 10;
        }
        break;
      }
    }
    const hot = [...this.ctx.hot_words.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8).map(([w]) => w);
    const http = this.http ? this.http.errors : {};
    return {
      running: this.running,
      mode: this.demo ? "demo" : "live",
      now,
      uptime_s: this.running && this.started_at ? Math.floor(now - this.started_at) : 0,
      stats: {
        watched: this.tokens.size,
        with_market_data: [...this.tokens.values()].filter((t) => t.snapshots.length).length,
        deep_checked: [...this.tokens.values()].filter((t) => t.rug).length,
        analyzed_total: this.analyzed.size,
        launches_seen: this.launches_seen,
        passes: this.passes,
        last_pass_ms: Math.round(this.last_pass_ms * 10) / 10,
        sol_usd: Math.round(this.ctx.sol_usd * 100) / 100,
        hot_words: hot,
        metadata_error: http.metadata || "",
      },
      sources: { ...this.status },
      rules: { ...this.rules },
      agents: AGENTS.map((a) => ({ id: a.id, name: a.name, group: a.group, description: a.description, veto: a.veto, tally: this.agent_tally[a.id] || {} })),
      candidates: ranked.slice(0, 25).map((t) => this.coinRow(t, now)),
      active_signal: active,
      signals: this.signals.slice(-40).reverse(),
      track_record: this.trackRecord(),
    };
  }
}

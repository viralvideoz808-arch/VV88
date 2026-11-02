// A simulated pump.fun market for demo mode (port of pumpscan/simulator.py): try the dashboard without
// touching real data. Every coin gets a hidden fate (rug, dud, runner, moon) that shapes its chart,
// order flow and on-chain report. Time runs `speed` times faster than the wall clock.

import { Token, makeRugReport, makeSnapshot } from "./core.js";
import { sleep } from "./sources.js";

const WORDS = ["kimchi", "pepe", "doge", "cat", "frog", "wif", "bonk", "moon", "chad", "goblin", "trump", "elon",
  "ai", "agent", "grok", "pengu", "shiba", "hamster", "monkey", "banana", "sushi", "ramen", "taco",
  "wizard", "ninja", "rocket", "based", "giga", "turbo", "baby", "king", "lord", "space", "pixel"];
const METAS = ["kimchi", "ai", "hamster", "wizard", "pengu"];
const ALNUM = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz123456789";

// Small seedable PRNG so tests can be deterministic.
function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export class Simulator {
  constructor(engine, speed = 10, seed = null) {
    this.engine = engine;
    this.speed = speed;
    this.random = seed == null ? Math.random : mulberry32(seed);
    this.t0Wall = Date.now() / 1000;
    this.t0Sim = this.t0Wall;
    this.hidden = new Map();
    this.meta = this.choice(METAS);
    this.solUsd = 180;
    this.creators = Array.from({ length: 400 }, () => this.chars(44));
  }

  clock() { return this.t0Sim + (Date.now() / 1000 - this.t0Wall) * this.speed; }

  uniform(a, b) { return a + (b - a) * this.random(); }
  randint(a, b) { return Math.floor(this.uniform(a, b + 1)); }
  choice(xs) { return xs[Math.floor(this.random() * xs.length)]; }
  chars(n) { let s = ""; for (let i = 0; i < n; i++) s += ALNUM[Math.floor(this.random() * ALNUM.length)]; return s; }
  gauss(mu, sigma) {
    const u = 1 - this.random(), v = this.random();
    return mu + sigma * Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }
  weighted(items, weights) {
    let r = this.random() * weights.reduce((a, b) => a + b, 0);
    for (let i = 0; i < items.length; i++) { r -= weights[i]; if (r <= 0) return items[i]; }
    return items[items.length - 1];
  }

  async run(signal) {
    this.engine.ctx.sol_usd = this.solUsd;
    for (let i = 0; i < 60; i++) this.launch(this.uniform(2, 50)); // a market already in progress
    while (!signal.aborted) {
      for (let i = this.randint(1, 3); i > 0; i--) this.launch();
      this.tick();
      if (this.random() < 0.01) this.meta = this.choice(METAS);
      await sleep(1000, signal);
    }
  }

  launch(ageMin = 0) {
    const now = this.clock();
    const fate = this.weighted(["rug", "dud", "runner", "moon"], [0.3, 0.45, 0.2, 0.05]);
    const w1 = this.random() < 0.3 ? this.meta : this.choice(WORDS);
    const w2 = this.choice(WORDS);
    const title = (w) => w[0].toUpperCase() + w.slice(1);
    const name = this.random() < 0.5 ? `${title(w1)} ${title(w2)}` : title(w1);
    const symbol = (this.random() < 0.7 ? w1.slice(0, 5) : w1.slice(0, 3) + w2.slice(0, 2)).toUpperCase();
    const good = fate === "runner" || fate === "moon";
    const tok = new Token({
      mint: this.chars(40) + "pump", name, symbol, created_at: now - ageMin * 60, first_seen: now,
      creator: this.choice(this.creators), dev_buy_sol: Math.round(this.uniform(0, 3) * 100) / 100,
      description: good || this.random() < 0.4 ? `The ${w1} coin the internet has been waiting for. Community first, no presale.` : "",
      twitter: good || this.random() < 0.3 ? `https://x.com/${w1}` : "",
      telegram: good && this.random() < 0.7 ? `https://t.me/${w1}` : "",
      website: good && this.random() < 0.5 ? `https://${w1}${w2}.fun` : "",
      metadata_loaded: true,
    });
    if (good && this.random() < 0.3) { tok.boosts = this.randint(1, 5); tok.has_profile = true; }
    const drift = { rug: 0.02, dud: -0.01, runner: 0.05, moon: 0.09 }[fate];
    this.hidden.set(tok.mint, {
      fate, drift, vol: this.uniform(0.04, 0.12),
      mcap: this.uniform(4500, 7000) * Math.exp(drift * ageMin * 0.5),
      peak_at: tok.created_at + this.uniform(8, 45) * 60,
      rug_at: tok.created_at + this.uniform(4, 20) * 60,
    });
    this.engine.addToken(tok);
    this.engine.launches_seen++;
    return tok;
  }

  tick() {
    const now = this.clock();
    const dtMin = this.speed / 60;
    for (const [mint, h] of [...this.hidden]) {
      const tok = this.engine.tokens.get(mint);
      if (!tok) { this.hidden.delete(mint); continue; }
      let drift = now > h.peak_at ? -Math.abs(h.drift) * 0.8 : h.drift;
      if (tok.name.toLowerCase().includes(this.meta)) drift += 0.02;
      h.mcap *= Math.exp(drift * dtMin + this.gauss(0, h.vol) * Math.sqrt(dtMin));
      if (h.fate === "rug" && now > h.rug_at) { h.mcap *= 0.25; h.fate = "rugged"; }
      h.mcap = Math.max(2500, h.mcap);
      const prev = tok.snapshotAgo(300, now);
      const change = prev ? (h.mcap / prev.mcap_usd - 1) * 100 : 0;
      const activity = Math.max(0, change) * 2 + h.mcap / 2000;
      const buys = Math.floor(Math.max(0, this.gauss(activity * (change > 0 ? 1.3 : 0.7), 5)));
      const sells = Math.floor(Math.max(0, this.gauss(activity * (change > 0 ? 0.7 : 1.3), 5)));
      const vol5 = (buys + sells) * this.uniform(40, 220);
      const lastH1 = tok.snap ? tok.snap.vol_h1 : 0;
      tok.addSnapshot(makeSnapshot({
        t: now, price_usd: h.mcap / 1e9, mcap_usd: h.mcap, buys_m5: buys, sells_m5: sells,
        buys_h1: buys * 6, sells_h1: sells * 6, vol_m5: vol5, vol_h1: Math.max(vol5, lastH1 * 0.95 + vol5 * 0.2),
        change_m5: change, change_h1: change * 2,
      }));
      if (!tok.rug && tok.snapshots.length > 3 && this.random() < 0.2) tok.rug = this.report(h, now);
      if (tok.rug && tok.rug.total_holders != null) {
        tok.rug.total_holders += Math.floor(Math.max(0, buys - sells) * this.uniform(0.2, 0.6));
        tok.addHolders(now, tok.rug.total_holders);
      }
    }
  }

  report(h, now) {
    const bad = h.fate === "rug" || h.fate === "rugged";
    const r = () => this.random();
    return makeRugReport({
      fetched_at: now,
      mint_authority: bad && r() < 0.05,
      total_holders: Math.floor(h.mcap / this.uniform(80, 200)),
      top10_pct: bad && r() < 0.6 ? this.uniform(30, 60) : this.uniform(10, 26),
      max_holder_pct: bad && r() < 0.5 ? this.uniform(8, 20) : this.uniform(1.5, 5),
      insider_pct: bad && r() < 0.5 ? this.uniform(5, 20) : 0,
      insiders_detected: bad && r() < 0.5 ? this.randint(3, 9) : 0,
      creator_pct: bad && r() < 0.4 ? this.uniform(6, 15) : this.uniform(0, 4),
      creator_token_count: bad && r() < 0.3 ? this.randint(3, 12) : 1,
      danger_risks: bad && r() < 0.3 ? ["Single holder ownership"] : [],
      warn_risks: r() < 0.3 ? ["Low amount of LP Providers"] : [],
    });
  }
}

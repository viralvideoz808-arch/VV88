// Polls the exact bonding-curve account of watched coins every ~2 s (100 accounts per RPC call) and appends
// each reading to the coin's on-chain series. The series and the view the agents read live in core.js.

import { pushPoint } from "./core.js";
import { decodeCurve, fromBase64, PUMP_PROGRAM } from "./paper.js";

// Polls curve accounts in batches; the engine decides which coins to watch.
export class CurveWatch {
  constructor(reader, { batch = 10 } = {}) {
    this.reader = reader;
    // Free PublicNode: 10 accounts per call. A user-supplied RPC (e.g. Helius) starts at 100 and drops to 10 if refused.
    this.batch = batch;
    this.calls = 0;
    this.errors = 0;
    this.lastError = "";
    this.watched = 0;
    this.limit = null; // adaptive cap after rate limiting
  }

  // How many coins can be polled every 2 s without overrunning a free RPC. PublicNode's free tier sustains ~2
  // calls/s (measured: ~21% of calls refused at 3/s), so the cap shrinks on 429s and regrows on clean cycles.
  get maxCapacity() { return this.batch >= 100 ? 300 : 40; }
  get capacity() { return Math.min(this.maxCapacity, this.limit ?? this.maxCapacity); }

  async poll(tokens, now) {
    tokens = tokens.slice(0, this.capacity);
    this.watched = tokens.length;
    let limited = false;
    for (let i = 0; i < tokens.length; i += this.batch) {
      const batch = tokens.slice(i, i + this.batch);
      let values;
      try {
        values = await this.reader.accounts(batch.map((t) => t.pair_address), "base64");
        this.calls++;
      } catch (e) {
        this.errors++;
        this.lastError = String(e.message || e);
        if (/maximum of 10 accounts/i.test(this.lastError) && this.batch > 10) this.batch = 10;
        if (/429|rate limit|-32005|too many/i.test(this.lastError)) limited = true;
        continue;
      }
      const t = now();
      batch.forEach((tok, k) => {
        const acc = values[k];
        if (!acc || acc.owner !== PUMP_PROGRAM) return;
        let c;
        try { c = decodeCurve(fromBase64(acc.data[0])); } catch { return; }
        pushPoint(tok, { t, vSol: Number(c.vSol), vTok: Number(c.vTok), realSol: Number(c.realSol), complete: c.complete });
        if (c.complete) tok.migrated = true; // the curve sold out: the coin is moving to PumpSwap
      });
    }
    if (limited) this.limit = Math.max(10, Math.floor(this.capacity * 0.75));
    else if (this.limit != null) this.limit = Math.min(this.maxCapacity, this.limit + 1);
  }
}

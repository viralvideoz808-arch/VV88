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
  }

  // How many coins can be polled every 2 s without overrunning a free RPC (~2 calls/s on the free default).
  get capacity() { return this.batch >= 100 ? 300 : 40; }

  async poll(tokens, now) {
    tokens = tokens.slice(0, this.capacity);
    this.watched = tokens.length;
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
  }
}

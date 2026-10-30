import { readFileSync } from "node:fs";
import { Market } from "../web/js/core.js";

export const NOW = 1_800_000_000_000;
export const fx = (name) => JSON.parse(readFileSync(new URL(`./fixtures/${name}`, import.meta.url)));

// A tradeable market with a two-sided book on both outcomes.
export function market(f = {}) {
  const yesAsk = f.yesAsk ?? 0.6, yesBid = f.yesBid ?? 0.58;
  const m = new Market({
    id: f.id || "M1", question: f.question || "Will X happen?", slug: "will-x", eventId: f.eventId || "E1",
    eventTitle: f.eventTitle || "Event", outcomes: f.outcomes || ["Yes", "No"], tokenIds: f.tokenIds || ["t0", "t1"],
    yesMid: (yesAsk + yesBid) / 2, lastTrade: f.lastTrade ?? (yesAsk + yesBid) / 2, spread: yesAsk - yesBid,
    vol24: f.vol24 ?? 50000, liq: f.liq ?? 50000, openInterest: f.openInterest ?? 40000,
    chg1h: f.chg1h ?? 0.01, chg24h: f.chg24h ?? 0.03,
    endMs: f.endMs ?? NOW + 3 * 24 * 3.6e6, createdMs: NOW - 10 * 24 * 3.6e6,
    feeRate: f.feeRate ?? 0, feesEnabled: !!f.feeRate, feeExp: 1,
    negRisk: !!f.negRisk, negRiskAugmented: !!f.augmented,
    resolutionSource: "https://example.com", description: "A clearly described market with a resolution source.",
    ...f.market,
  });
  const ladder = (best, dir, n = 6, size = 2000) => Array.from({ length: n }, (_, i) => ({ price: Math.round((best + dir * 0.01 * i) * 1000) / 1000, size }));
  m.books = {
    0: { asks: ladder(yesAsk, +1), bids: ladder(yesBid, -1) },
    1: { asks: ladder(1 - yesBid, +1), bids: ladder(1 - yesAsk, -1) },
  };
  m.booksAt = NOW;
  if (f.history) m.history = f.history;
  return m;
}

export function ctx(side = 0, over = {}) {
  return { now: NOW, side, event: null, tradeUsd: 20, totalDepthFloorUsd: 20, ...over };
}

// A complete negRisk event with the given YES asks (members get simple books).
export function negriskEvent(asks, { augmented = false, feeRate = 0 } = {}) {
  const members = asks.map((a, i) => market({ id: `N${i}`, eventId: "EV", question: `Outcome ${i}`, yesAsk: a, yesBid: Math.max(0.001, a - 0.01), feeRate, market: { outcomes: [`Out${i}`, "No"] } }));
  const sumMid = members.reduce((s, m) => s + m.mid(0), 0);
  return { id: "EV", title: "Who wins?", complete: !augmented, augmented, members, siblings: members, n: members.length, sumMid };
}

import { MarketContext, Token, makeRugReport, makeSnapshot } from "../web/js/core.js";

export const NOW = 1_800_000_000;

// A coin every agent should like: clean on-chain, rising on real volume, good metadata.
export function runner(mint = "RUNNERmintpump", now = NOW, symbol = "KIMCHI") {
  const tok = new Token({
    mint, name: "Kimchi", symbol, created_at: now - 20 * 60, creator: "dev1",
    description: "Fermented cabbage, fermented gains. Community coin.", twitter: "https://x.com/k",
    telegram: "https://t.me/k", image: "https://img/k.png", metadata_loaded: true, boosts: 2, has_profile: true,
  });
  let mcap = 30000;
  for (let i = 0; i < 8; i++) { // 4 minutes of snapshots, accelerating
    mcap *= 1.02 + i * 0.006;
    tok.addSnapshot(makeSnapshot({
      t: now - (7 - i) * 30, price_usd: mcap / 1e9, mcap_usd: mcap, buys_m5: 90 + i * 10, sells_m5: 40,
      buys_h1: 600, sells_h1: 300, vol_m5: 12000 + i * 1000, vol_h1: 40000, change_m5: 25, change_h1: 80,
    }));
  }
  tok.rug = makeRugReport({ fetched_at: now, total_holders: 300, top10_pct: 18, max_holder_pct: 3, creator_pct: 1.0, creator_token_count: 1 });
  tok.holder_history.push([now - 120, 260], [now, 300]);
  return tok;
}

export function context(tokens, { now = NOW, sol = 150, hot = ["kimchi"] } = {}) {
  const ctx = new MarketContext();
  ctx.now = now;
  ctx.sol_usd = sol;
  for (const t of tokens) { ctx.tokens.set(t.mint, t); ctx.noteLaunch(t); }
  ctx.hot_words = new Map(hot.map((w) => [w, 10]));
  ctx.word_coins = new Map();
  for (const t of tokens) {
    for (const w of t.words()) {
      if (!ctx.hot_words.has(w)) continue;
      const [n, best] = ctx.word_coins.get(w) || [0, 0];
      ctx.word_coins.set(w, [n + 1, Math.max(best, t.snap ? t.snap.mcap_usd : 0)]);
    }
  }
  return ctx;
}

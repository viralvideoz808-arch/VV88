from pumpscan.models import MarketContext, RugReport, Snapshot, Token

NOW = 1_800_000_000.0


def runner(mint="RUNNERmintpump", now=NOW, symbol="KIMCHI") -> Token:
    """A coin every agent should like: clean on-chain, rising on real volume, good metadata."""
    tok = Token(mint=mint, name="Kimchi", symbol=symbol, created_at=now - 20 * 60, creator="dev1",
                description="Fermented cabbage, fermented gains. Community coin.", twitter="https://x.com/k",
                telegram="https://t.me/k", image="https://img/k.png", metadata_loaded=True, boosts=2, has_profile=True)
    mcap = 30000.0
    for i in range(8):  # 4 minutes of snapshots, accelerating
        t = now - (7 - i) * 30
        mcap *= 1.02 + i * 0.006
        tok.add_snapshot(Snapshot(t=t, price_usd=mcap / 1e9, mcap_usd=mcap, buys_m5=90 + i * 10, sells_m5=40,
                                  buys_h1=600, sells_h1=300, vol_m5=12000 + i * 1000, vol_h1=40000,
                                  change_m5=25, change_h1=80))
    tok.rug = RugReport(fetched_at=now, mint_authority=False, freeze_authority=False, total_holders=300,
                        top10_pct=18, max_holder_pct=3, creator_pct=1.0, creator_token_count=1)
    tok.holder_history.extend([(now - 120, 260), (now, 300)])
    return tok


def context(*tokens, now=NOW, sol=150.0, hot=("kimchi",)) -> MarketContext:
    ctx = MarketContext(now=now, sol_usd=sol)
    for t in tokens:
        ctx.tokens[t.mint] = t
        ctx.note_launch(t)
    ctx.now = now
    ctx.hot_words = {w: 10 for w in hot}
    ctx.word_coins = {}
    for t in tokens:
        for w in t.words() & ctx.hot_words.keys():
            n, best = ctx.word_coins.get(w, (0, 0.0))
            ctx.word_coins[w] = (n + 1, max(best, t.snap.mcap_usd if t.snap else 0))
    return ctx

# polyscan — a 25-agent council for Polymarket

Two pages, one static site, everything running inside your browser tab:

- **⚡ Minutes** (`index.html`) — Polymarket's crypto **Up or Down** windows that settle every **5 and 15 minutes**
  (also 4-hour), for Bitcoin, Ethereum, Solana, XRP, Dogecoin, BNB, Hyperliquid and Zcash. The main page.
- **Hours** (`hours.html`) — every market resolving within a day: dutch-book arbitrage and the council's
  directional picks on games and short-dated questions.

Press **START**. Each page has its own **$10 demo account** that trades against the real order books and settles at
the real $1 / $0 outcomes. Keep the tab open while you want it working.

## Minute markets: the strategy

### What actually settles a window

From Polymarket's own data on **2,065 resolved windows**:

- A window ("Bitcoin Up or Down — 9:40–9:45AM ET") resolves **Up** when Chainlink's BTC/USD **60-second TWAP
  stream** reads at least as high at the end as at the start (2,056 of 2,056 checked).
- That boundary value is **one number**. The previous window's final price is the next window's price to beat (2,001
  of 2,037 consecutive pairs identical), and every window that ends at the same moment — 5-minute, 15-minute, 4-hour
  — settles on the same final number (447 of 447 identical).
- Chainlink's value tracks Coinbase's average over the minute before the boundary to within **~1–4 basis points**.
- The *hourly* markets are different: they settle on Binance's 1-hour candle, so polyscan leaves them out.

### Two handles on a window

1. **Fair value from physics.** With the start price, live spot, seconds left and realised volatility, the chance
   that the final 60-second average ends above the start is a normal-distribution calculation:
   `P(Up) = Φ(ln(S/S₀) / σ_eff)`, where `σ_eff² = σ²(t − 40s) + strike² + floor²` while more than a minute is left. Inside
   the last minute, part of the final average is already known and counts as fact. The council buys a side only when
   the book sells it well below that probability, **after Polymarket's crypto taker fee of 7% × p(1−p)** per share.
2. **The strike ladder.** When a 5-minute and a 15-minute window end together, they are bets on the same number at
   different strikes. Up on the lower strike must be worth at least Up on the higher strike. If prices break that
   by more than the fees, **Up(low strike) + Down(high strike) pays at least $1 in every outcome**. It pays $2 if the
   final lands between the strikes. That is arbitrage by arithmetic. Strikes must be ordered beyond doubt: either
   two published Chainlink values, or a gap of at least 3× their combined uncertainty. The same check catches
   Up + Down inside one window costing under $1.

### Where each input comes from

| Input | Source |
|---|---|
| Price to beat | (a) the live Coinbase feed's time-weighted average over the minute before the start, if the page was running then (±1.5bp); (b) Chainlink's published value, once Gamma posts it a few minutes after the boundary (±2bp, adjusted by the measured Chainlink–Coinbase basis); (c) a Coinbase 1-minute candle (±4bp) |
| Spot | Coinbase Exchange websocket (every trade); REST quote midpoint for quiet coins; Binance.US every 4s as an independent cross-check |
| Volatility | 60 minutes of Coinbase 1-minute candles, plus a 10-minute read to catch regime shifts |
| Books | Polymarket CLOB, both tokens of every open window in one batched request every 2s |

### The 25 agents, five desks

| Desk | Agents (✱ = can veto) |
|---|---|
| **Oracle** — what will settle it? | Strike Agent ✱ · Spot Feed Agent ✱ · Cross-Exchange Agent ✱ · Volatility Agent ✱ · Settlement Clock ✱ |
| **Value** — is the price wrong? | Settlement Model · Distance-to-Strike · Edge Agent · Kelly Sizer · Market Consensus |
| **Structure** — does it break arithmetic? | Strike Ladder Agent · Binary Complement · Ladder Bound Agent · Extreme Price Guard · Fee Agent |
| **Execution** — can it be bought? | Spread Sentinel ✱ · Order Book Depth ✱ · Book Imbalance · Quote Lag Agent · Liquidity Floor ✱ |
| **Risk** — should the account take it? | Spot Momentum · Jump Guard ✱ · Exposure Agent ✱ · Bankroll Agent ✱ · Track-Record Agent |

Every vote and its one-line reason is shown for every window. A **crossed book** (bid ≥ ask, which appears as a
window closes and matching pauses) is never treated as a price. The Track-Record Agent scores the model against
the market on the session's resolved windows and votes against trading if the model is losing.

### When it trades

- **Ladder / set arbitrage:** a pair costs under $1 after fees by at least 1¢, every leg is open, uncrossed and has
  5+ shares at the ask. The account buys equal shares of each leg, all or nothing.
- **Fair-value snipe:** no veto, net edge ≥ **5¢/share**, and **17+ of 25** agents vote BUY.
- **Explore** (the "aggressive" switch, on by default): no veto, edge ≥ 4¢, 14+ BUY votes. Experiments: see below.

Stakes are half-Kelly, capped at 35% of equity and raised to Polymarket's 5-share minimum. Up to 4 positions are
held at once, one per window. Trades are taken best edge first, within the cash available.

### What the data says (read this)

Backtest on **1,651 resolved windows (≈14 hours)**, using Polymarket's minute-level price history and Coinbase
1-minute candles. In this test the spot data was up to a minute *older* than the market price it was compared with:

- The model's probabilities were **about as accurate as the market's** (Brier 0.158 vs 0.157). A model + market
  blend **beat the market out of sample** in both halves (0.145 vs 0.148; 0.165 vs 0.167).
- **Directional trades after fees and spread were small and unproven.** At a 4–6¢ edge bar they returned about +9–11%
  per trade across all coins, but most of that came from Zcash's near-empty books. Without Zcash the return was
  +4–5%, and on Bitcoin/Ethereum/Solana/XRP alone +1–3%. None of these is statistically distinguishable from zero.
- **"Fade the leader"** (buy 15–30¢ underdogs) looked great in one half (+20%) and lost in the other (−10%), so
  it was rejected as a regime effect.
- **Ladder breaks** on liquid books appeared about once every 3 hours and always paid at least $1 (one paid $2).
  Most apparent breaks were on books with almost no orders, which the execution desk refuses.

Bots co-located with exchanges update these books in milliseconds, and a browser cannot outrun them. Treat
directional trades as measured experiments. The **Model vs market** scoreboard on the page keeps the live record.

## Hours markets

The original council, for every market resolving within a window you choose (24 hours by default). It looks for
dutch-book arbitrage (a complete outcome set bought for under $1 after fees) and, rarely, a directional edge
above a fair-value estimate. Its agents are tradeability (veto), value, order flow, and structure & context. The
fee is `rate × shares × p(1−p)`, read from each market's schedule.

## Run it

```bash
cd polyscan/web && python3 -m http.server 8090   # then open http://127.0.0.1:8090
```

Tests (Node 20+): `node --test polyscan/web-tests/*.test.mjs` runs 38 cases. They cover the settlement model, the
final-minute averaging, the strike ladder (including the real 5m/15m pair from Polymarket's data), crossed books,
the 25-agent council and its vetoes, ladder fills in the demo account, fees, order-book walking, dutch-book
arbitrage, resolution scoring, and looking up resolved markets (which Gamma hides unless asked).

## Deploy (static, free)

Host `polyscan/web` as a static site (Vercel: Root Directory `polyscan/web`, Framework "Other", no build step).
`web/vercel.json` sets a strict Content-Security-Policy. The page may only connect to Polymarket's Gamma and CLOB
APIs, Coinbase Exchange (REST and websocket) and Binance.US.

## Read this before trading

polyscan is for study and paper trading. Prediction-market prices are efficient, and minute markets are among the
most competitive. Fees and the bid/ask spread are a real cost on every trade. Nothing here is financial or betting
advice. Prediction-market trading is restricted or prohibited in some jurisdictions. Trade only what you can afford
to lose.

Data: Polymarket Gamma (`gamma-api.polymarket.com`) and CLOB (`clob.polymarket.com`), Coinbase Exchange
(`api.exchange.coinbase.com`, `ws-feed.exchange.coinbase.com`), Binance.US (`api.binance.us`). Settlement facts
are from Polymarket's own resolved-market metadata (Chainlink data streams).

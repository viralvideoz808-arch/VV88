# polyscan — a 25-agent council for Polymarket

Open the site, press **START**. 25 analysis agents begin scanning every liquid Polymarket market, pricing each
order book, and looking for two things:

1. **Risk-free arbitrage.** On a *complete* question — one whose outcomes are mutually exclusive and exhaustive, so
   exactly one resolves YES — buying one share of every outcome returns exactly **$1** at resolution. If the whole
   set can be bought for **under $1 after fees**, that is guaranteed profit, no prediction required. The dashboard's
   **Arbitrage Watch** ranks every complete set by how close it is to this, and a signal fires when one crosses
   below $1.
2. **A genuinely mispriced market.** Rare. Prediction-market prices are the crowd's probability and are hard to
   beat. A directional signal fires only when the council reaches consensus **and** the price is below a modest
   fair-value estimate by more than the fees — and it is labelled speculative.

The whole thing runs **inside your browser tab**. Polymarket's Gamma and CLOB APIs both allow direct browser
access, so polyscan is a static site with no server. Keep the tab open while you want it working.

## Why Polymarket, honestly

A Polymarket market is binary at the token level: each outcome token pays exactly **$1** if it wins and **$0** if it
loses, so prices live in [0, 1] and read as probabilities. That makes two things true that memecoins never offered:

- **Signals are measurable.** A market resolves to a definite outcome, so every directional signal is scored as a
  real win or loss against what actually happened — not an estimate.
- **Arbitrage is real.** The $1 payout structure means a sub-$1 complete set is risk-free by arithmetic, not by
  forecasting. That is the one edge polyscan treats as reliable. It is also rare and quickly taken by bots, so most
  of the time the honest output is "no signal — here is how close the nearest set is."

## The 25 agents

**Tradeability (veto group) — can this be traded well at all?**
Market Status Sentinel · Spread Sentinel · Order Book Depth Auditor · Liquidity Floor · Resolution Clock ·
Extreme Price Guard · Stale Quote Detector · Round-Trip Cost Agent

**Value — is there an edge?**
Dutch-Book Arbitrage Agent · Binary Complement Agent · Overround Detector · Favorite-Longshot Bias · Fair-Value Band ·
Expected Value Engine

**Order flow**
Momentum Scout · Price-History Trend · Volume Surge Analyst · Book Imbalance Tracker · Bid Support Agent ·
Open Interest Monitor

**Structure & context**
Resolution Clarity · Event Consistency Check · Time-Decay Value · Probability Band · Category & Fee Fit

Each agent votes BUY / HOLD / AVOID / ABSTAIN with a one-line reason; every vote is shown. An agent abstains when it
lacks data — it never guesses.

### When does a signal fire?

- **Arbitrage:** a complete set's full cost (asks + fees) is below $1 by at least the minimum edge, and no safety
  agent vetoes. It does **not** need a chorus of momentum agents — a risk-free set is risk-free.
- **Directional:** at least the vote threshold (default 18/25) BUY, a positive net edge after fees (default ≥ 2¢
  per share), and no safety veto.

## The money math

- **Fees.** Polymarket's taker fee is `rate × shares × p(1−p)`, read per market (0 for makers and for several
  categories such as geopolitics and many politics markets; 0.04–0.07 elsewhere). This matches the published
  "$1.25 per 100 shares at 50¢" for a 0.05 rate. Makers pay nothing.
- **Fills.** The demo account walks the real ask ladder from the CLOB for slippage, and can exit by walking the bid
  ladder (paying the taker fee again). Arbitrage buys one share of every outcome in the set.
- **Settlement.** Positions are normally held to resolution: each winning share pays **$1**, each losing share $0,
  with no fee. So the demo account's track record is measured against real outcomes.

## The demo account

Starts with **$100** of simulated money and auto-trades the council's signals (toggle it off to trade by hand).
For an arbitrage signal it buys the whole set, sized to a per-set cap. For a directional signal it buys the outcome
token at the ask. It values open positions at the live mid, settles resolved markets at $1/$0, and keeps everything
in your browser's local storage. "Reset to $100" clears it.

## Run it

```bash
cd polyscan/web && python3 -m http.server 8090   # then open http://127.0.0.1:8090
```

Tests (Node 20+): `node --test polyscan/web-tests/*.test.mjs` — 21 cases, including the fee schedule, order-book
walking, dutch-book arbitrage, resolution scoring, the council, and the demo account, several against **real
Polymarket data captured as fixtures**.

## Deploy (static, free)

Host `polyscan/web` as a static site on any static host (Vercel: Root Directory `polyscan/web`, Framework "Other",
no build step). `web/vercel.json` adds a strict Content-Security-Policy that only allows connections to the two
Polymarket API hosts.

## Read this before trading

polyscan is for study and paper trading. Prediction-market prices are efficient; the only edge it treats as reliable
is arbitrage, which is rare, small, and taken quickly by bots with far faster execution than a browser. Directional
signals are the council's opinion, not a forecast that beats the market. Fees and the bid/ask spread are a real cost
on every round trip. Nothing here is financial or betting advice, and prediction-market trading is restricted or
prohibited in some jurisdictions. Trade only what you can afford to lose.

Data: Polymarket Gamma API (`gamma-api.polymarket.com`) and CLOB API (`clob.polymarket.com`), both public.
Fee schedule from Polymarket's own per-market data.

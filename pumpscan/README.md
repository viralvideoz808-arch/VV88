# pumpscan: a 25-agent council for pump.fun coins

Press **START**. 25 analysis agents begin scanning every new pump.fun launch, plus the pump.fun
coins already trending on DexScreener. They re-vote on every coin every 2 seconds.
The dashboard only speaks up when the council reaches consensus:

> **BUY NOW · Kimchi $KIMCHI** within the next 10 minutes · 22/25 agents agree
> Entry $31.2k · don't buy above $35.9k · stop −15% · take profit +22% (sell half) / +45%
> *Why:* volume surging 2.1x · 214 buys vs 88 sells · curve 64% filled · top 10 hold 17% · no insider network · …

Press **STOP** and every loop shuts down.

## Browser version (hosted on Vercel)

`web/` is the same scanner rebuilt to run entirely in the browser. It has the same 25 agents, consensus rules
and dashboard. The page talks to PumpPortal, DexScreener, Rugcheck and IPFS directly, because all of them allow
browser access. So it needs no server and can be hosted as a static site on Vercel's free plan.

- **Open the link, press START.** The scan runs in that tab. Keep the tab open, and on a phone keep the
  screen on (the page asks the phone to keep the screen awake while it runs). Background tabs get throttled
  by the browser.
- **Saved in your browser:** signals, outcomes and your consensus rules are stored in that browser only.
- **Offline test mode:** add `?sim=1` to the address to run against a simulated market (used by the tests).
- **No password needed:** each visitor runs their own scan in their own browser. Nobody else can stop yours
  or see your history.
- **Unreadable metadata:** some launchpads host coin metadata on servers that refuse browser access. For
  those coins the social and metadata agents abstain ("metadata host blocks browser access"). They don't
  guess.

### The $10 demo account (real market, simulated money)

The browser version includes a paper-trading account that starts with **$10 worth of SOL** at the live SOL price.
It trades the council's signals against the **real, live on-chain state** of each coin, so fills match what a real
wallet would get:

- **No order book; the curve is the book.** pump.fun coins trade against an automated market maker: the bonding
  curve before graduation, then a PumpSwap pool. The account reads those reserves directly from Solana. The bonding
  curve account has the virtual and real reserves. The PumpSwap pool uses its vault balances plus
  `virtual_quote_reserves`. It then applies pump.fun's own integer math (floor on outputs, ceil on fees, as in their
  SDK).
- **Quote, then land.** Like a real transaction, it quotes at the moment of the decision and sets a min-out from
  your slippage setting. It waits your latency setting, re-reads the chain, and fills against the state at that
  moment. If the price moved past your slippage, the transaction **fails**. A failed transaction still pays the
  network fee, as on Solana.
- **Every real cost:**
  - pump.fun fees: 1.25% on the bonding curve (0.95% protocol + 0.30% creator). On PumpSwap, the published
    market-cap tiers, from 1.25% down to 0.30%, including the 0.20% LP fee.
  - Solana's 5,000-lamport signature fee, plus your priority fee on every transaction, including failed ones.
  - Rent for the token account a first buy creates: 0.00207408 SOL for Token-2022 coins. It is refunded when the
    account is closed after a full exit.
- **Exits:** follow the signal's plan, checked on-chain every 2 seconds while scanning:
  - half at take-profit 1,
  - the rest at take-profit 2,
  - everything at the stop loss,
  - after the time stop, or
  - when a safety agent vetoes.
  Triggers fire on the live spot price, and the sell then fills with the same quote, latency and slippage rules.
- **Settings** (in the panel): auto-trade on or off, size (% of free SOL), slippage, priority fee, latency, max
  positions, time stop, exit on veto, closing token accounts, and the RPC endpoint.

What it does not model:
- Your own trade's effect on later market data. At $10 the impact is computed in the fill but not fed back.
- Jito/MEV sandwiching.
- Transactions dropped by congestion; the model only fails on slippage.

Fee figures come from pump.fun's public docs: github.com/pump-fun/pump-public-docs and its fee table. If pump.fun
changes its fees, update `CURVE_FEES` / `SWAP_FEE_TIERS` in `web/js/paper.js`.

Solana's own `api.mainnet-beta.solana.com` refuses requests from web pages. The account uses PublicNode's free RPC
and fails over to Tatum's. For heavier use, paste your own RPC URL (for example a free Helius key) in the settings.

Deploy: import the repo in Vercel with **Root Directory** `pumpscan/web` and Framework **Other**. There is no
build step. `web/vercel.json` adds a strict Content-Security-Policy.

Run it locally: `cd pumpscan/web && python3 -m http.server 8077`, then open http://127.0.0.1:8077.
Tests for the browser code: `node --test pumpscan/web-tests/*.test.mjs` (Node 20+).

## Run it

```bash
cd pumpscan
pip install -e .            # Python 3.10+
pumpscan                    # live pump.fun data → opens http://127.0.0.1:8088
pumpscan --demo             # simulated market, to try the dashboard safely
```

Options: `--min-agree 25` (unanimous), `--window 10` (minutes a signal stays valid),
`--host 0.0.0.0` (reach it from your phone on the same Wi-Fi), `--port`, `--no-browser`.
The same settings can be given as `PUMPSCAN_*` environment variables (see `pumpscan/config.py`).
The consensus threshold and window can also be changed from the dashboard while it runs.

## Put it online (open it from anywhere)

The scanner has to keep running between visits, so it needs an always-on server, not a serverless host.
The repo includes a Dockerfile and a Render Blueprint (`render.yaml` at the repo root):

1. Sign in at [render.com](https://render.com) with your GitHub account.
2. Choose **New → Blueprint**, pick this repository and the branch that holds `pumpscan`.
3. When asked, enter a **PUMPSCAN_PASSWORD**. The PumpPortal key is optional; leave it empty.
4. Deploy. You get an `https://pumpscan-….onrender.com` address.
   The browser asks for the password; type anything as the username.

The Blueprint uses Render's paid Starter plan plus a 1 GB disk for the signal history.
The free plan sleeps after 15 idle minutes, which stops the scan.
Hosted, the scan starts by itself at boot. The START/STOP button still works.

On any other server with Docker:

```bash
docker build -t pumpscan pumpscan
docker run -d --restart unless-stopped -p 8088:8088 \
  -e PUMPSCAN_PASSWORD='choose-a-long-one' -v pumpscan-data:/data pumpscan
```

Put HTTPS in front of it (Caddy, nginx, Cloudflare), or the password travels in clear text.
The server refuses to listen on a public address without `PUMPSCAN_PASSWORD`.

## Data sources (all free; no wallet or keys needed)

| source | what it gives the agents |
|---|---|
| PumpPortal websocket | every pump.fun launch the moment it happens: creator, dev buy, metadata URI; graduations |
| DexScreener | price, market cap, 5m/1h buys, sells, volume, change, liquidity, socials, paid boosts. Fetched for up to 900 coins every ~5 s |
| Rugcheck | holders, top-10 concentration, insider networks, dev balance, mint/freeze authority, risk flags |
| IPFS metadata | description, X/Telegram/website links |
| pump.fun API | recently traded coins (used when reachable; it is often behind Cloudflare) |

Optional: set `PUMPSCAN_PUMPPORTAL_API_KEY` to stream every trade of the 40 strongest candidates.
This sharpens the dev-selling, bundle and unique-buyer checks.
PumpPortal bills it to the wallet linked to the key: 0.01 SOL per 10,000 trade messages.

## The 25 agents

**Safety. Each of these 8 can veto a signal on its own:**
Mint Authority Sentinel · Holder Concentration Auditor · Dev Wallet Watcher · Insider & Bundle Detector ·
Serial Launcher Profiler · Rugcheck Risk Scorer · Exit Liquidity Guard · Rug Pattern Detector

**Momentum & order flow:** Momentum Scout · Volume Surge Analyst · Buy Pressure Analyst ·
Transaction Velocity Tracker · Trend Acceleration Engine · Holder Growth Monitor · Whale Flow Tracker

**Market structure:** Bonding Curve Analyst · Market Cap Positioner · Launch Timing Agent ·
Volatility Risk Agent · Entry Timing Agent · Cost Hurdle Agent

**Narrative & social:** Social & Marketing Verifier · Metadata Quality Inspector · Narrative & Meta Detector ·
Copycat Detector

Each agent votes BUY, HOLD, AVOID or ABSTAIN, and gives a one-line reason. Every vote is shown in the dashboard.
An agent abstains when it lacks data; it never guesses.

The browser version differs from the Python server in two places:
- The **Cost Hurdle Agent** replaced the DexScreener Attention Agent in the browser version. Paid DexScreener
  marketing now counts inside the Social & Marketing Verifier.
- The browser version's agents read the **on-chain flow** below. The Python server still uses DexScreener data only.

### On-chain flow (browser version)

Every 2 seconds the page reads the exact bonding-curve account of young coins, strong candidates, live signals and
held positions. From that it knows, exactly:
- the price at each read;
- the net SOL that entered or left the curve;
- the change since the exact launch price (30 SOL / 1.073B tokens), even for a coin seconds old.

Twelve agents use this feed when it is fresh, and fall back to DexScreener otherwise. A coin can therefore be judged
before DexScreener has indexed it.

Free RPC limits (measured): PublicNode allows 10 accounts per call and about 2 calls per second, so the default
watches up to 40 coins. The poller shrinks that number when it is rate limited and grows it back afterwards. Paste
your own RPC URL (for example a free Helius key) in the demo-account settings to watch up to 300 coins.

### How the entry rules were tuned (and what the data really says)

I recorded the exact bonding-curve state of **555 pump.fun coins** every 2 seconds over about 50 minutes, on
2026-10-28. That is about 37,000 readings: new launches plus active curve coins found on DexScreener. I then
simulated $10-account trades, using:
- a $3 position (0.03 SOL), entering about 2 seconds after the decision;
- exact curve math, 1.25% pump.fun fees each way, and network fees;
- the demo exit plan: stop −15%, sell half at +20%, the rest at +40%, a 10-minute limit, break-even after take-profit 1,
  and a trailing stop.

I designed rules on the first half of the data and then checked them on **279 coins collected afterwards** (true
out-of-sample).

| Rule | Trades | Avg per trade, 0.0005 SOL priority | Avg per trade, 0.0001 SOL priority | Win rate |
|---|---|---|---|---|
| Buy every coin at first look | 555 | −7.9% | −5.2% | 10% |
| First-draft sniper rules (pullback/breakout + momentum + inflow), design half | 8 | −23.5% | – | 0% |
| Rules designed on the first half ("5–20 SOL sweet spot"), **design half** | 14 | +0.4% | +3.6% | 57% |
| Same rules on the **fresh coins** | 14 | −13.9% | −11.1% | 29% |
| **Current rules** (past 5 SOL, safe, no chase, not deteriorating), all coins | 67 | −12.8% | −9.8% | 33% |
| Current rules, fresh coins only | 27 | −14.0% | −11.0% | 33% |

Note on the first row: those buys mostly happen at a coin's launch price, and a bonding curve cannot fall below its
launch price. So that baseline loses mostly fees. Any entry later in a coin's life has room to fall.

What held up in both halves of the data, and is encoded in the agents:
- **The very first stage is the worst entry.** Odds of +20% before −15% within 10 minutes: 15% under 2 SOL in the
  curve (n=175), 24% at 2–5 SOL, 31–33% from 5 to 40 SOL, 43% at 40–85 SOL (n=30). The Bonding Curve and Market
  Cap agents AVOID under 2 SOL and HOLD at 2–5 SOL.
- **Chasing is worse than random.** Strong buy pressure (gross buys ≥1.8× sells) averaged −10.9% per trade, and
  +3–40% in the last minute averaged −8.8%. The flow agents now only object to a deteriorating coin; Entry Timing
  refuses vertical candles.
- **Network fees are the cost you control.** 0.0005 SOL priority is 3.4% of a 0.03 SOL round trip. The default is
  now 0.0001 SOL.

What did **not** hold up: the "5–20 SOL sweet spot" looked break-even on the design half and lost on fresh coins.
It was overfitting, and it is gone.

**Bottom line:** in this data, no entry rule tested, including the ones the council uses now, made money after
costs. Break-even needs roughly 60% of trades to hit +20% before −15%. The best stage reached 43%. The agents are
built to avoid the worst entries and to be honest about the rest, not to promise profit. The demo account measures
what actually happens. Treat it as the only evidence, and re-run the research before trusting any change in market
conditions.

### Why the account was losing (and the exit changes)

I traced every trade the current agents would have taken across both recorded periods (118 trades on 1,161 coins),
with the demo exit plan of that time (stop −15%, half at +20%, rest at +40%, 10-minute limit).

- **Losses by source, per trade:** price movement −1.4%, pump.fun fees −2.5%, network fees −0.8%. Total −4.7%.
- **The stop-loss did not protect.** 38% of trades ended at the stop. It was set at −15%, but on average it
  *triggered* at −31% and filled at −33%. pump.fun coins gap down: a holder dumps and the price jumps past the stop
  between two 2-second checks. Those trades averaged −32.7% each, the largest single drain.
- **Time in the trade is the risk.** The shorter the hold, the better, in both periods independently:

| Exit plan | Period 1 (67 trades) | Period 2 (51 trades) |
|---|---|---|
| 10-minute hold, stop −15% (old) | −9.8% | +2.1% |
| 3-minute hold | −7.7% | +3.5% |
| **60-second hold, stop −10% (new default)** | **−3.8%** | **+5.0%** |

  Every hold from 30 to 90 seconds lands around break-even pooled (−1% to +0.2%), so this is a broad effect,
  not one tuned number.
- **Selling on a safety veto slightly hurt in both periods.** By the time the veto fires, the dump has happened.
  It is now off by default.

The demo account now exits within 60 seconds, with a −10% stop and take-profits at +20% (half) and +40%. Saved
settings from earlier versions move to these defaults once; later edits are kept. The dashboard's **"Why am I
winning or losing?"** panel splits your own account's result into:
- price movement;
- pump.fun and LP fees;
- network fees and failed transactions;
- rent;
- the SOL price's own move;
- how positions ended, including how far stop-outs gapped past the planned stop.

Even with these exits the expected result is about break-even before any edge from the agents. pump.fun's 2.5%
round-trip fee is the floor that every trade has to beat.

The research scripts are in `research/`. `collect.py` records curves, `analyze.py` runs the rule search and the
train/test split, and `analyze2.py` produces the robust statistics and the out-of-sample check.

### When does a signal fire?

All three must hold:
- at least **20 of 25** agents vote BUY (adjustable; 25 = unanimous),
- **zero** agents vote AVOID,
- no safety agent objects.

Rugcheck is rate-limited, so the deep on-chain check is spent on the coins closest to consensus.
A coin cannot pass while its safety agents are still waiting for that check.

After it fires, a signal can change state:
- **TOO LATE**: the price ran more than 15% past the entry. Don't chase it.
- **CANCELLED**: a safety agent turned to AVOID.
- When the window closes, the outcome is recorded: take-profit hit, stopped out, or up/down at the close.
  The **track record** panel shows the win rate actually measured.

Signals are saved to `~/.pumpscan/signals.json`.

## Read this before trading

This tool ranks coins with heuristics. It cannot know the future.
No combination of agents can promise that a coin will pump in the next 10 minutes.
Most pump.fun coins go to zero, and on-chain checks miss some rugs (for example a dev selling through fresh wallets).
Run it for a while and judge it by its measured track record, not by how confident a signal sounds.
Only trade money you can afford to lose completely. Keep positions small and honour the stop loss.
Signals are not financial advice.

## Tests

```bash
cd pumpscan && pip install -e '.[dev]' && pytest
```

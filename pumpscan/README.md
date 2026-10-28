# pumpscan: a 25-agent council for pump.fun coins

Press **START**. 25 analysis agents begin scanning every new pump.fun launch, plus the pump.fun
coins already trending on DexScreener. They re-vote on every coin every 2 seconds.
The dashboard only speaks up when the council reaches consensus:

> **BUY NOW · Kimchi $KIMCHI** within the next 10 minutes · 22/25 agents agree
> Entry $31.2k · don't buy above $35.9k · stop −15% · take profit +22% (sell half) / +45%
> *Why:* volume surging 2.1x · 214 buys vs 88 sells · curve 64% filled · top 10 hold 17% · no insider network · …

Press **STOP** and every loop shuts down.

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
Volatility Risk Agent · Entry Timing Agent

**Narrative & social:** Social Presence Verifier · Metadata Quality Inspector · Narrative & Meta Detector ·
Copycat Detector · DexScreener Attention Agent

Each agent votes BUY, HOLD, AVOID or ABSTAIN, and gives a one-line reason. Every vote is shown in the dashboard.
An agent abstains when it lacks data; it never guesses.

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

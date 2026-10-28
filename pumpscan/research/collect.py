"""Record exact bonding-curve state of every new pump.fun launch every ~2 s (research data for entry rules)."""
# Usage: python3 collect.py OUT_DIR SECONDS   (needs: pip install httpx websockets)

import asyncio, base64, json, struct, sys, time
import httpx, websockets

OUT = sys.argv[1]; DURATION = float(sys.argv[2])
RPC = "https://solana-rpc.publicnode.com"  # 10 accounts per call
coins = {}  # mint -> dict(curve, t0, ...)
dead = set()

async def ws_loop():
    while True:
        try:
            async with websockets.connect("wss://pumpportal.fun/api/data", open_timeout=15) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                async for raw in ws:
                    m = json.loads(raw)
                    if m.get("txType") == "create" and m.get("pool", "pump") == "pump" and m.get("bondingCurveKey"):
                        coins[m["mint"]] = {"curve": m["bondingCurveKey"], "t0": time.time(), "ts": time.time(), "symbol": m.get("symbol"),
                                            "name": m.get("name"), "dev_sol": m.get("solAmount"), "creator": m.get("traderPublicKey")}
                        with open(OUT + "/launches.jsonl", "a") as f:
                            f.write(json.dumps({"mint": m["mint"], **coins[m["mint"]]}) + "\n")
        except Exception as e:
            print("ws", e, flush=True); await asyncio.sleep(3)

async def poll_loop(client):
    calls = errs = 0
    while True:
        t = time.time()
        live = [(m, c) for m, c in coins.items() if m not in dead and t - c["ts"] < 3000]
        live.sort(key=lambda mc: -mc[1]["ts"])  # newest first
        live = live[:60]
        for i in range(0, len(live), 10):
            batch = live[i:i + 10]
            try:
                r = await client.post(RPC, json={"jsonrpc": "2.0", "id": 1, "method": "getMultipleAccounts",
                                                  "params": [[c["curve"] for _, c in batch], {"encoding": "base64", "commitment": "processed"}]})
                calls += 1
                d = r.json()
                if "result" not in d:
                    errs += 1; print("rpc error body", r.status_code, r.text[:160], flush=True); await asyncio.sleep(1); continue
                vals = d["result"]["value"]
            except Exception as e:
                errs += 1; print("rpc", type(e).__name__, str(e)[:80], flush=True); await asyncio.sleep(2); continue
            now = time.time(); rows = []
            for (m, c), v in zip(batch, vals):
                if not v: continue
                raw = base64.b64decode(v["data"][0]); vt, vs, rt, rs, ts = struct.unpack_from("<5Q", raw, 8)
                rows.append({"t": round(now, 2), "m": m, "vs": vs, "vt": vt, "rs": rs, "c": raw[48]})
                if raw[48] == 1 or (now - c["ts"] > 240 and rs < 300_000_000):  # graduated, or still < 0.3 SOL after 4 min: stop
                    dead.add(m)
            with open(OUT + "/curves.jsonl", "a") as f:
                f.write("\n".join(json.dumps(x) for x in rows) + "\n")
        if int(t) % 60 < 2:
            print(f"{time.strftime('%H:%M:%S')} coins={len(coins)} live={len(live)} calls={calls} errs={errs}", flush=True)
        await asyncio.sleep(max(0.2, 2 - (time.time() - t)))

async def seed_loop(client):
    """Also follow pump.fun coins already trading on their curve (found via DexScreener listings)."""
    hdr = {"User-Agent": "pumpscan-research/0.1"}
    while True:
        try:
            mints = []
            for u in ("https://api.dexscreener.com/token-profiles/latest/v1", "https://api.dexscreener.com/token-boosts/latest/v1",
                      "https://api.dexscreener.com/token-boosts/top/v1"):
                for it in (await client.get(u, headers=hdr)).json():
                    a = it.get("tokenAddress", "")
                    if it.get("chainId") == "solana" and a.endswith("pump") and a not in coins and a not in mints:
                        mints.append(a)
            for i in range(0, len(mints), 30):
                for p in (await client.get("https://api.dexscreener.com/tokens/v1/solana/" + ",".join(mints[i:i + 30]), headers=hdr)).json():
                    a = p["baseToken"]["address"]
                    if p.get("dexId") == "pumpfun" and a not in coins:
                        coins[a] = {"curve": p["pairAddress"], "t0": p.get("pairCreatedAt", time.time() * 1000) / 1000, "ts": time.time(),
                                    "symbol": p["baseToken"].get("symbol"), "seeded": True}
                        with open(OUT + "/launches.jsonl", "a") as f:
                            f.write(json.dumps({"mint": a, **coins[a]}) + "\n")
        except Exception as e:
            print("seed", type(e).__name__, str(e)[:100], flush=True)
        await asyncio.sleep(60)

async def main():
    async with httpx.AsyncClient(timeout=10) as client:
        await asyncio.wait([asyncio.create_task(ws_loop()), asyncio.create_task(poll_loop(client)), asyncio.create_task(seed_loop(client))], timeout=DURATION)
asyncio.run(main())

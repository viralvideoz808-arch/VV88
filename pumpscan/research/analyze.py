"""Which on-chain entry rules make money after real costs? Fit on early launches, judge on later ones."""
# Usage: python3 analyze.py DATA_DIR   (DATA_DIR holds launches.jsonl and curves.jsonl from collect.py)

import json, math, os, sys, itertools, collections, statistics
QUICK = bool(os.environ.get("QUICK"))

D = sys.argv[1]
SIZE = 0.03            # SOL per trade (~30% of a $10 account)
FEE = 0.0125           # pump.fun bonding-curve fee per side
NET_FEE = 0.000505     # base + 0.0005 priority, per transaction
K = 30e9 * 1.073e15

launch = {}
for line in open(f"{D}/launches.jsonl"):
    x = json.loads(line); launch[x["m" if "m" in x else "mint"]] = x
series = collections.defaultdict(list)
for line in open(f"{D}/curves.jsonl"):
    if not line.strip():
        continue
    x = json.loads(line); series[x["m"]].append(x)

def price(p): return (p["vs"] / 1e9) / (p["vt"] / 1e6)

def buy(p, sol):
    net = sol / (1 + FEE)
    vs, vt = p["vs"] / 1e9, p["vt"] / 1e6
    return net * vt / (vs + net)  # tokens

def sell(p, tokens):
    vs, vt = p["vs"] / 1e9, p["vt"] / 1e6
    return tokens * vs / (vt + tokens) * (1 - FEE)

def features(s, i, t0):
    p = s[i]; t = p["t"]; pr = price(p)
    def ago(sec):
        for j in range(i, -1, -1):
            if t - s[j]["t"] >= sec: return s[j]
        return None
    def chg(sec):
        q = ago(sec)
        if q: return pr / price(q) - 1
        if t - t0 <= sec + 3: return pr / (30 / 1.073e9) - 1
        return None
    def inflow(sec):
        q = ago(sec)
        if q: return (p["rs"] - q["rs"]) / 1e9
        if t - t0 <= sec + 3: return p["rs"] / 1e9
        return None
    gin = gout = 0; act = pairs = 0; max_out_frac = 0
    j = i
    while j > 0 and t - s[j - 1]["t"] <= 60:
        d = (s[j]["rs"] - s[j - 1]["rs"]) / 1e9; pairs += 1
        if d > 0: gin += d; act += 1
        if d < 0:
            gout -= d; act += 1
            if s[j - 1]["rs"] > 0: max_out_frac = max(max_out_frac, -d / (s[j - 1]["rs"] / 1e9))
        j -= 1
    hi = max(price(s[j]) for j in range(i + 1) if t - s[j]["t"] <= 300)
    return {
        "age": t - t0, "sol": p["rs"] / 1e9, "chg10": chg(10), "chg30": chg(30), "chg60": chg(60), "chg300": chg(300),
        "in30": inflow(30), "in60": inflow(60), "gin60": gin, "gout60": gout, "act60": act / pairs if pairs else 0,
        "outfrac60": max_out_frac, "dd": 1 - pr / hi,
    }

def simulate(s, i, tp, sl, hold, trail=None):
    """Enter at the next poll (~2 s latency); exit at TP/SL/time on later polls; return net SOL P&L."""
    if i + 1 >= len(s): return None
    e = s[i + 1]
    tokens = buy(e, SIZE)
    entry_px = price(e)
    peak = entry_px
    for q in s[i + 2:]:
        if q["t"] - e["t"] > hold or q["c"] == 1:
            break
        px = price(q); peak = max(peak, px)
        chg = px / entry_px - 1
        stop = sl
        if trail is not None and peak / entry_px - 1 >= trail[0]:
            stop = max(stop, peak / entry_px * (1 - trail[1]) - 1)
        if chg >= tp or chg <= stop:
            # the sell lands one poll later (latency), at whatever the curve is then
            k = s.index(q)
            exit_p = s[k + 1] if k + 1 < len(s) else q
            return sell(exit_p, tokens) - SIZE - 2 * NET_FEE
    last = q if s[i + 2:] else e
    return sell(last, tokens) - SIZE - 2 * NET_FEE

# Build candidate entry moments: one evaluation per poll, coins ordered by launch time.
coins = sorted([m for m in series if m in launch and len(series[m]) > 10], key=lambda m: launch[m]["t0"])
cut = int(len(coins) * 0.6)
train, test = set(coins[:cut]), set(coins[cut:])
rows = []
for m in coins:
    s = series[m]; t0 = launch[m]["t0"]
    for i in range(len(s)):
        if s[i]["c"] == 1: break
        f = features(s, i, t0)
        if f["age"] < 20 or f["chg30"] is None: continue
        rows.append((m, i, f))
print(f"coins with data: {len(coins)} (train {len(train)}, test {len(test)}); evaluation moments: {len(rows)}")
alive = [m for m in coins if max(p["rs"] for p in series[m]) / 1e9 > 2]
print(f"coins that ever reached 2 SOL in the curve: {len(alive)}; graduated: {sum(any(p['c'] == 1 for p in series[m]) for m in coins)}")

EXITS = [(0.30, -0.15, 600, None), (0.50, -0.20, 600, None), (1.0, -0.25, 600, (0.3, 0.2)), (0.20, -0.10, 300, None)]

def evaluate(rule, coinset, exit_):
    """First trigger per coin -> one trade."""
    pnl = []
    seen = set()
    for m, i, f in rows:
        if m not in coinset or m in seen: continue
        if rule(f):
            seen.add(m)
            r = simulate(series[m], i, *exit_)
            if r is not None: pnl.append(r)
    if not pnl: return None
    return {"n": len(pnl), "ev_sol": statistics.mean(pnl), "ev_pct": statistics.mean(pnl) / SIZE * 100,
            "win": sum(1 for x in pnl if x > 0) / len(pnl), "total": sum(pnl)}

base = lambda f: True
for ex in ([] if QUICK else EXITS):
    print("baseline (buy every coin at first look)", ex, {k: evaluate(base, c, ex) for k, c in (("train", train), ("test", test))})

grid = {
    "min_in60": [0, 0.5, 1, 2, 4],
    "chg30": [(-1, 9), (0.0, 0.25), (0.02, 0.15), (0.05, 0.40), (-0.10, 0.05)],
    "max_dd": [1, 0.35, 0.2, 0.1],
    "max_out": [1, 0.15, 0.08],
    "min_act": [0, 0.3, 0.6],
    "sol": [(0, 999), (1, 30), (3, 60), (8, 80)],
    "age": [(20, 9999), (20, 180), (60, 600), (120, 1500)],
}
results = []
keys = list(grid)
for ex in ([] if QUICK else EXITS[:3]):
    for combo in itertools.product(*grid.values()):
        g = dict(zip(keys, combo))
        def rule(f, g=g):
            return (f["in60"] is not None and f["in60"] >= g["min_in60"] and g["chg30"][0] <= f["chg30"] <= g["chg30"][1]
                    and f["dd"] <= g["max_dd"] and f["outfrac60"] <= g["max_out"] and f["act60"] >= g["min_act"]
                    and g["sol"][0] <= f["sol"] <= g["sol"][1] and g["age"][0] <= f["age"] <= g["age"][1])
        tr = evaluate(rule, train, ex)
        if not tr or tr["n"] < 8: continue
        results.append((tr["ev_pct"], g, ex, tr, rule))
results.sort(key=lambda x: x[0], reverse=True)
if not QUICK: print("\nTop rules on TRAIN, then the same rule on unseen TEST launches:")
for ev, g, ex, tr, rule in results[:12]:
    te = evaluate(rule, test, ex)
    print(f"  train EV {tr['ev_pct']:+.1f}% n={tr['n']} win={tr['win']:.0%} | test {('EV %+.1f%% n=%d win=%.0f%%' % (te['ev_pct'], te['n'], te['win']*100)) if te else 'no trades'} | exit {ex} | {g}")

# Single-feature lift: how does the 10-minute best-case move depend on each feature?
if not QUICK: print("\nFeature buckets (all coins): mean net EV% of a TP30/SL15/10min trade")
for feat, edges in [] if QUICK else [("in60", [-99, 0, 0.5, 1, 2, 4, 99]), ("chg30", [-1, -0.1, 0, 0.05, 0.15, 0.4, 99]),
                    ("dd", [0, 0.05, 0.15, 0.3, 0.5, 1.01]), ("outfrac60", [0, 0.0001, 0.05, 0.15, 1.01]),
                    ("act60", [0, 0.2, 0.4, 0.6, 0.8, 1.01]), ("sol", [0, 1, 3, 8, 20, 50, 90]), ("age", [0, 60, 180, 600, 2000])]:
    out = []
    for lo, hi in zip(edges, edges[1:]):
        vals = []
        seen = set()
        for m, i, f in rows:
            v = f[feat]
            if v is None or not (lo <= v < hi) or (m, lo) in seen: continue
            seen.add((m, lo))  # one sample per coin per bucket
            r = simulate(series[m], i, *EXITS[0])
            if r is not None: vals.append(r / SIZE * 100)
        if vals: out.append(f"[{lo},{hi}) n={len(vals)} ev={statistics.mean(vals):+.1f}%")
    print(f"  {feat}: " + " | ".join(out))

# ---------------------------------------------------------------- the implemented on-chain rules, replicated
def chain_votes(f, s, i, T):
    """Approximation of the browser agents' on-chain BUY conditions (entry, volume, pressure, velocity, momentum, safety)."""
    p = s[i]; t = p["t"]
    def ago(sec):
        for j in range(i, -1, -1):
            if t - s[j]["t"] >= sec: return s[j]
        return None
    q10 = ago(10)
    c10 = f["chg10"]; c30 = f["chg30"]; c60 = f["chg60"]
    in10 = (p["rs"] - q10["rs"]) / 1e9 if q10 else None
    hi_ago = 0
    pr = price(p); best = pr
    for j in range(i, -1, -1):
        if t - s[j]["t"] > 300: break
        if price(s[j]) > best: best = price(s[j]); hi_ago = t - s[j]["t"]
    ok = {}
    vertical = (c10 is not None and c10 >= T["vert10"]) or (c30 is not None and c30 >= T["vert30"])
    pullback = T["pbmin"] <= f["dd"] <= T["pbmax"] and hi_ago >= 20 and (in10 or 0) > 0 and (c10 or 0) > 0
    breakout = f["dd"] < 0.02 and (c30 or 0) >= 0.02 and (f["in30"] or 0) > 0
    ok["entry"] = (not vertical) and (pullback or breakout)
    ok["volume"] = f["in60"] is not None and f["in60"] >= T["in60"]
    ok["pressure"] = f["gin60"] >= 0.5 and f["gin60"] / (f["gout60"] + 0.05) >= 1.8
    ok["velocity"] = f["act60"] >= 0.6
    ok["momentum"] = c60 is not None and 0.03 <= c60 <= 0.40
    ok["safe"] = f["outfrac60"] < 0.15 and f["dd"] < 0.45 and (c30 is None or c30 > -0.25)
    ok["curve"] = f["sol"] >= 0.08 * 85
    return ok

def exit_sim(s, i, sl, tp1, tp2, hold, trail=25, breakeven=True):
    """Demo-account exit plan: half at TP1, then break-even / trailing / TP2 / SL / time, ~2 s latency on each fill."""
    if i + 1 >= len(s): return None
    e = s[i + 1]; tokens = buy(e, SIZE); entry = price(e); peak = entry
    half_done = False; out = 0.0; left = tokens; txs = 1
    k = i + 2
    while k < len(s):
        q = s[k]
        if q["t"] - e["t"] > hold or q["c"] == 1: break
        px = price(q); peak = max(peak, px); chg = px / entry - 1
        fill = s[k + 1] if k + 1 < len(s) else q
        if chg <= sl or (half_done and breakeven and chg <= 0) or (half_done and px / peak - 1 <= -trail / 100) or chg >= tp2:
            out += sell(fill, left); left = 0; txs += 1; break
        if not half_done and chg >= tp1:
            out += sell(fill, left / 2); left /= 2; half_done = True; txs += 1
        k += 1
    if left > 0:
        last = s[min(k, len(s) - 1)]
        out += sell(last, left); txs += 1
    return out - SIZE - txs * NET_FEE

def eval_council(T, coinset, need=6, exit_=(-0.15, 0.20, 0.40, 600)):
    pnl = []; seen = set()
    for m, i, f in rows:
        if m not in coinset or m in seen or f["age"] < T["min_age"]: continue
        ok = chain_votes(f, series[m], i, T)
        if ok["safe"] and ok["entry"] and sum(ok.values()) >= need:
            seen.add(m)
            r = exit_sim(series[m], i, *exit_)
            if r is not None: pnl.append(r)
    if not pnl: return None
    return {"n": len(pnl), "ev_pct": statistics.mean(pnl) / SIZE * 100, "win": sum(x > 0 for x in pnl) / len(pnl),
            "median_pct": statistics.median(pnl) / SIZE * 100}

DEFAULT_T = {"vert10": 0.12, "vert30": 0.30, "pbmin": 0.05, "pbmax": 0.30, "in60": 0.3, "min_age": 120}
if not QUICK: print("\nImplemented on-chain rules (defaults), all 7 conditions, demo exit plan:")
for name, cs in [] if QUICK else (("train", train), ("test", test), ("all", set(coins))):
    print(f"  {name}: {eval_council(DEFAULT_T, cs, need=7)}")
if not QUICK: print("  same, 6 of 7 (safety + entry required):", eval_council(DEFAULT_T, set(coins), need=6))
if not QUICK: print("\nExit plans for the implemented entry (all coins):")
for ex in [] if QUICK else [(-0.15, 0.20, 0.40, 600), (-0.12, 0.15, 0.30, 600), (-0.20, 0.30, 0.60, 600), (-0.10, 0.12, 0.25, 300), (-0.25, 0.40, 1.0, 900)]:
    print(f"  SL {ex[0]:+.0%} TP1 {ex[1]:+.0%} TP2 {ex[2]:+.0%} hold {ex[3]}s ->", eval_council(DEFAULT_T, set(coins), need=7, exit_=ex))
if not QUICK: print("\nThreshold sensitivity (train -> test), demo exit plan:")
cands = []
for vert10, vert30, pbmin, pbmax, in60, min_age in itertools.product([0.08, 0.12, 0.2], [0.2, 0.3, 0.5], [0.03, 0.05, 0.1], [0.2, 0.3, 0.45], [0.1, 0.3, 1.0], [30, 120, 300]):
    if QUICK: break
    T = dict(vert10=vert10, vert30=vert30, pbmin=pbmin, pbmax=pbmax, in60=in60, min_age=min_age)
    tr = eval_council(T, train, need=7)
    if tr and tr["n"] >= 6: cands.append((tr["ev_pct"], T, tr))
cands.sort(key=lambda x: x[0], reverse=True)
for ev, T, tr in cands[:8]:
    te = eval_council(T, test, need=7)
    print(f"  train {tr} | test {te} | {T}")

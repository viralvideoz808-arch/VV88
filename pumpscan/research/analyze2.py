"""Robust questions: graduation odds by stage, hit-before-stop odds, fee drag, bootstrap CIs for the rules."""
# Usage: python3 analyze2.py DATA_DIR   (section 8 also needs ./snap/curves.jsonl next to this script: the design snapshot)
import collections, json, random, statistics, sys
sys.argv = [sys.argv[0], sys.argv[1]]
import os
os.environ["QUICK"] = "1"
exec(open(__file__.replace("analyze2.py", "analyze.py")).read())

D = sys.argv[1]
coins_all = [m for m in series if m in launch and len(series[m]) > 10]

# 1) Odds that a coin keeps climbing, by how much SOL is already in its curve.
print("1) Given a coin's curve holds >= X SOL at some point, how often does it later hold >= 2X (or graduate)?")
for x in [0.5, 1, 2, 4, 8, 16, 32]:
    reached = [m for m in coins_all if max(p["rs"] for p in series[m]) / 1e9 >= x]
    doubled = [m for m in reached if max(p["rs"] for p in series[m]) / 1e9 >= min(2 * x, 85) or any(p["c"] == 1 for p in series[m])]
    print(f"   >= {x:>4} SOL: {len(reached):4d} coins, {len(doubled):3d} went on to >= {min(2*x,85)} SOL ({(len(doubled)/len(reached)*100 if reached else 0):.0f}%)")

# 2) From each coin's first moment at each stage: P(+20% before -15%) within 10 min, from the price one poll later.
def hit_first(s, i, up=0.20, down=-0.15, hold=600):
    if i + 1 >= len(s): return None
    e = price(s[i + 1]); t0 = s[i + 1]["t"]
    for q in s[i + 2:]:
        if q["t"] - t0 > hold: break
        c = price(q) / e - 1
        if c >= up: return 1
        if c <= down: return 0
    return 0
print("\n2) First time a coin's curve reaches each stage: odds of +20% before -15% within 10 min (price only, no costs)")
for lo, hi in [(0.5, 2), (2, 5), (5, 10), (10, 20), (20, 40), (40, 85)]:
    hits = []
    for m in coins_all:
        s = series[m]
        for i, p in enumerate(s):
            if lo <= p["rs"] / 1e9 < hi and p["c"] == 0:
                h = hit_first(s, i)
                if h is not None: hits.append(h)
                break
    if hits: print(f"   {lo:>4}-{hi:<3} SOL: n={len(hits):3d}  P(+20% first) = {statistics.mean(hits)*100:4.0f}%   (break-even needs ~{(0.15+0.06)/(0.20+0.15)*100:.0f}% at 6% costs)")

# 3) Fee drag: the same baseline trades with and without network fees.
def boot_ci(xs, n=2000):
    if len(xs) < 3: return (float("nan"), float("nan"))
    ms = sorted(statistics.mean(random.choices(xs, k=len(xs))) for _ in range(n))
    return ms[int(0.05 * n)], ms[int(0.95 * n)]
random.seed(1)
first = []
for m in coins_all:
    s = series[m]; t0 = launch[m]["t0"]
    for i in range(len(s)):
        f = features(s, i, t0)
        if f["age"] >= 20 and f["chg30"] is not None:
            first.append((m, i, f)); break
pnl = [r for m, i, f in first if (r := simulate(series[m], i, 0.30, -0.15, 600)) is not None]
pct = [x / SIZE * 100 for x in pnl]
pct_ex = [(x + 2 * NET_FEE) / SIZE * 100 for x in pnl]
print(f"\n3) Baseline (every coin at first look), TP30/SL15/10min, n={len(pct)}:")
print(f"   with network fees: mean {statistics.mean(pct):+.1f}%  90% CI {boot_ci(pct)[0]:+.1f}..{boot_ci(pct)[1]:+.1f}")
print(f"   without network fees (pump.fun fees only): mean {statistics.mean(pct_ex):+.1f}%  90% CI {boot_ci(pct_ex)[0]:+.1f}..{boot_ci(pct_ex)[1]:+.1f}")
print(f"   network fees alone cost {2*NET_FEE/SIZE*100:.1f}% of a {SIZE} SOL trade; at priority 0.0001 SOL it would be {2*0.000105/SIZE*100:.1f}%")

# 4) Bootstrap CIs for the implemented rules and a few candidate variants (all coins).
print("\n4) Implemented rules and variants, all coins, demo exit plan (SL-15 / TP1 +20 half / TP2 +40 / 10 min), 90% CI:")
def trades_for(T, need, exit_=(-0.15, 0.20, 0.40, 600)):
    out = []; seen = set()
    for m, i, f in rows:
        if m in seen or f["age"] < T["min_age"]: continue
        ok = chain_votes(f, series[m], i, T)
        if ok["safe"] and ok["entry"] and sum(ok.values()) >= need:
            seen.add(m); r = exit_sim(series[m], i, *exit_)
            if r is not None: out.append(r / SIZE * 100)
    return out
variants = {
    "implemented (7/7)": (DEFAULT_T, 7),
    "implemented (6/7)": (DEFAULT_T, 6),
    "pullback only, deeper (30-50%)": (dict(DEFAULT_T, pbmin=0.30, pbmax=0.50), 5),
    "late stage only (curve >= 40 SOL)": (dict(DEFAULT_T, min_age=30), 5),
}
for name, (T, need) in variants.items():
    xs = trades_for(T, need)
    if name.startswith("late stage"):
        xs = []
        seen = set()
        for m, i, f in rows:
            if m in seen or f["sol"] < 40: continue
            ok = chain_votes(f, series[m], i, T)
            if ok["safe"] and not ((f["chg10"] or 0) >= 0.12):
                seen.add(m); r = exit_sim(series[m], i, -0.15, 0.20, 0.40, 600)
                if r is not None: xs.append(r / SIZE * 100)
    if xs:
        lo, hi = boot_ci(xs)
        print(f"   {name:36s} n={len(xs):3d} mean {statistics.mean(xs):+6.1f}%  win {sum(x>0 for x in xs)/len(xs)*100:3.0f}%  90% CI {lo:+.1f}..{hi:+.1f}")
    else:
        print(f"   {name:36s} no trades")

# 5) Ablation: each on-chain condition on its own (first trigger per coin), plus the stage rule, demo exits.
print("\n5) Each condition alone (first time it is true per coin), demo exit plan, network fee 0.000505 vs 0.000105:")
def run_rule(pred, net_fee):
    global NET_FEE
    old = NET_FEE; NET_FEE = net_fee
    out = []; seen = set()
    for m, i, f in rows:
        if m in seen: continue
        if pred(f, series[m], i):
            seen.add(m); r = exit_sim(series[m], i, -0.15, 0.20, 0.40, 600)
            if r is not None: out.append(r / SIZE * 100)
    NET_FEE = old
    return out
conds = {
    "any moment (baseline)":         lambda f, s, i: True,
    "entry: pullback/breakout":      lambda f, s, i: chain_votes(f, s, i, DEFAULT_T)["entry"],
    "volume: in60 >= 0.3 SOL":       lambda f, s, i: chain_votes(f, s, i, DEFAULT_T)["volume"],
    "pressure: gross buy/sell>=1.8": lambda f, s, i: chain_votes(f, s, i, DEFAULT_T)["pressure"],
    "velocity: active >= 60%":       lambda f, s, i: chain_votes(f, s, i, DEFAULT_T)["velocity"],
    "momentum: +3..40% in 60 s":     lambda f, s, i: chain_votes(f, s, i, DEFAULT_T)["momentum"],
    "not vertical (c10<12%,c30<30%)": lambda f, s, i: (f["chg10"] or 0) < 0.12 and (f["chg30"] or 0) < 0.30,
    "stage 5-10 SOL":                lambda f, s, i: 5 <= f["sol"] < 10,
    "stage 5-10 SOL + not vertical": lambda f, s, i: 5 <= f["sol"] < 10 and (f["chg10"] or 0) < 0.12 and (f["chg30"] or 0) < 0.30,
    "stage 5-10 SOL + safe":         lambda f, s, i: 5 <= f["sol"] < 10 and chain_votes(f, s, i, DEFAULT_T)["safe"],
    "stage 5-20 SOL + safe + not vertical": lambda f, s, i: 5 <= f["sol"] < 20 and chain_votes(f, s, i, DEFAULT_T)["safe"] and (f["chg10"] or 0) < 0.12 and (f["chg30"] or 0) < 0.30,
    "stage >= 2 SOL + safe":         lambda f, s, i: f["sol"] >= 2 and chain_votes(f, s, i, DEFAULT_T)["safe"],
}
for name, pred in conds.items():
    for nf in (0.000505, 0.000105):
        xs = run_rule(pred, nf)
        if xs:
            lo, hi = boot_ci(xs)
            print(f"   {name:38s} fee {nf:.6f}: n={len(xs):3d} mean {statistics.mean(xs):+6.1f}% win {sum(x>0 for x in xs)/len(xs)*100:3.0f}% 90% CI {lo:+.1f}..{hi:+.1f}")

# 6) The revised council (v2) as deployed: no on-chain AVOID, curve stage 5-20 SOL, no chase, >= 4 of 5 flow agents.
def v2_ok(f, s, i):
    p = s[i]; t = p["t"]
    c10, c30, c60 = f["chg10"], f["chg30"], f["chg60"]
    def ago(sec):
        for j in range(i, -1, -1):
            if t - s[j]["t"] >= sec: return s[j]
        return None
    q90 = ago(90)
    c90 = price(p) / price(q90) - 1 if q90 else None
    # on-chain AVOIDs (any one blocks)
    if f["outfrac60"] >= 0.15 or (c30 is not None and c30 <= -0.25) or f["dd"] >= 0.45: return False
    if c60 is not None and c60 < -0.10: return False
    if f["in60"] is not None and f["in60"] <= -0.3: return False
    if f["gout60"] >= 0.3 and f["gin60"] / (f["gout60"] + 0.05) < 0.8: return False
    if f["act60"] < 0.35: return False
    recent = (c30 or 0) / 0.5
    if c90 is not None and recent < -0.05: return False
    if f["sol"] < 2: return False
    # required BUYs
    if not (5 <= f["sol"] <= 20): return False
    if (c10 or 0) >= 0.12 or (c30 or 0) >= 0.30 or f["dd"] > 0.30: return False
    flow = [c60 is not None and c60 >= -0.05, f["in60"] is not None and f["in60"] >= 0,
            f["gin60"] / (f["gout60"] + 0.05) >= 1.0, f["act60"] >= 0.6, recent >= -0.05]
    return sum(flow) >= 4

print("\n6) Revised council (v2), demo exit plan, 90% CI:")
for nf in (0.000505, 0.000105):
    for name, cs in (("train", train), ("test", test), ("all", set(coins))):
        global_rows = rows
        xs = []; seen = set()
        NET_FEE_SAVED = NET_FEE; NET_FEE = nf
        for m, i, f in rows:
            if m not in cs or m in seen or f["age"] < 120: continue
            if v2_ok(f, series[m], i):
                seen.add(m); r = exit_sim(series[m], i, -0.15, 0.20, 0.40, 600)
                if r is not None: xs.append(r / SIZE * 100)
        NET_FEE = NET_FEE_SAVED
        if xs:
            lo, hi = boot_ci(xs)
            print(f"   priority {nf - 0.000005:.4f} {name:5s}: n={len(xs):3d} mean {statistics.mean(xs):+6.1f}% win {sum(x>0 for x in xs)/len(xs)*100:3.0f}% median {statistics.median(xs):+.1f}% 90% CI {lo:+.1f}..{hi:+.1f}")
        else:
            print(f"   priority {nf - 0.000005:.4f} {name:5s}: no trades")

# 7) v3 = deployed final: no on-chain AVOID (without the quiet-coin veto), stage 5-20 SOL, no chase. Compared with
#    the plain structural rule, on train / test / all, both priority fees.
def v3_ok(f, s, i):
    c30, c60 = f["chg30"], f["chg60"]
    if f["outfrac60"] >= 0.15 or (c30 is not None and c30 <= -0.25) or f["dd"] >= 0.45: return False
    if c60 is not None and c60 < -0.10: return False
    if f["in60"] is not None and f["in60"] <= -0.3: return False
    if f["gout60"] >= 0.3 and f["gin60"] / (f["gout60"] + 0.05) < 0.8: return False
    if (c30 or 0) / 0.5 < -0.05: return False
    if not (5 <= f["sol"] <= 20): return False
    if (f["chg10"] or 0) >= 0.12 or (c30 or 0) >= 0.30 or f["dd"] > 0.30: return False
    return True
def structural(f, s, i):
    return 5 <= f["sol"] < 20 and chain_votes(f, s, i, DEFAULT_T)["safe"] and (f["chg10"] or 0) < 0.12 and (f["chg30"] or 0) < 0.30
print("\n7) Final rule (v3) vs the plain structural rule, demo exit plan, 90% CI:")
for label, pred in (("v3 (deployed)", v3_ok), ("structural", structural)):
    for nf in (0.000505, 0.000105):
        for name, cs in (("train", train), ("test", test), ("all", set(coins))):
            xs = []; seen = set(); saved = NET_FEE; NET_FEE = nf
            for m, i, f in rows:
                if m not in cs or m in seen: continue
                if pred(f, series[m], i):
                    seen.add(m); r = exit_sim(series[m], i, -0.15, 0.20, 0.40, 600)
                    if r is not None: xs.append(r / SIZE * 100)
            NET_FEE = saved
            if xs:
                lo, hi = boot_ci(xs)
                print(f"   {label:14s} priority {nf - 0.000005:.4f} {name:5s}: n={len(xs):3d} mean {statistics.mean(xs):+6.1f}% win {sum(x>0 for x in xs)/len(xs)*100:3.0f}% 90% CI {lo:+.1f}..{hi:+.1f}")

# 8) True out-of-sample: coins first seen AFTER the snapshot v3 was designed on.
import os as _os
snap_file = _os.path.join(_os.path.dirname(__file__), "snap", "curves.jsonl")
cutoff = max(json.loads(l)["t"] for l in open(snap_file) if l.strip())
fresh = {m for m in coins if launch[m].get("ts", launch[m]["t0"]) > cutoff}
print(f"\n8) Fresh coins first seen after the design snapshot (cutoff {cutoff:.0f}): {len(fresh)}")
for label, pred in (("baseline", lambda f, s, i: True), ("v3 (deployed)", v3_ok), ("structural", structural)):
    for nf in (0.000505, 0.000105):
        xs = []; seen = set(); saved = NET_FEE; NET_FEE = nf
        for m, i, f in rows:
            if m not in fresh or m in seen: continue
            if pred(f, series[m], i):
                seen.add(m); r = exit_sim(series[m], i, -0.15, 0.20, 0.40, 600)
                if r is not None: xs.append(r / SIZE * 100)
        NET_FEE = saved
        if xs:
            lo, hi = boot_ci(xs)
            print(f"   {label:14s} priority {nf - 0.000005:.4f}: n={len(xs):3d} mean {statistics.mean(xs):+6.1f}% win {sum(x>0 for x in xs)/len(xs)*100:3.0f}% 90% CI {lo:+.1f}..{hi:+.1f}")
        else:
            print(f"   {label:14s} priority {nf - 0.000005:.4f}: no trades")

# 9) Final deployed rule (v4): v3 with the stage requirement widened to >= 5 SOL (only "< 2 SOL is worst" held up).
def v4_ok(f, s, i):
    c30, c60 = f["chg30"], f["chg60"]
    if f["outfrac60"] >= 0.15 or (c30 is not None and c30 <= -0.25) or f["dd"] >= 0.45: return False
    if c60 is not None and c60 < -0.10: return False
    if f["in60"] is not None and f["in60"] <= -0.3: return False
    if f["gout60"] >= 0.3 and f["gin60"] / (f["gout60"] + 0.05) < 0.8: return False
    if (c30 or 0) / 0.5 < -0.05: return False
    if not (5 <= f["sol"] < 85): return False
    if (f["chg10"] or 0) >= 0.12 or (c30 or 0) >= 0.30 or f["dd"] > 0.30: return False
    return True
print("\n9) Final deployed rule (v4), demo exit plan, 90% CI:")
for name, cs in (("design snapshot", set(coins) - fresh), ("fresh (out-of-sample)", fresh), ("all", set(coins))):
    for nf in (0.000505, 0.000105):
        xs = []; seen = set(); saved = NET_FEE; NET_FEE = nf
        for m, i, f in rows:
            if m not in cs or m in seen: continue
            if v4_ok(f, series[m], i):
                seen.add(m); r = exit_sim(series[m], i, -0.15, 0.20, 0.40, 600)
                if r is not None: xs.append(r / SIZE * 100)
        NET_FEE = saved
        lo, hi = boot_ci(xs) if xs else (float("nan"), float("nan"))
        print(f"   {name:22s} priority {nf - 0.000005:.4f}: n={len(xs):3d} mean {statistics.mean(xs) if xs else float('nan'):+6.1f}% win {(sum(x>0 for x in xs)/len(xs)*100 if xs else 0):3.0f}% 90% CI {lo:+.1f}..{hi:+.1f}")
    bx = []; seen = set()
    for m, i, f in rows:
        if m in cs and m not in seen:
            seen.add(m); r = exit_sim(series[m], i, -0.15, 0.20, 0.40, 600)
            if r is not None: bx.append(r / SIZE * 100)
    print(f"   {name:22s} baseline (demo exit, 0.0005): n={len(bx)} mean {statistics.mean(bx):+.1f}%")

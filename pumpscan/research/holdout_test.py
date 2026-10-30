"""Pre-registered holdout test (written before the holdout data was looked at).

Decision rule: adopt "late stage sol>=50, not vertical, safe" as the agents' entry preference only if, on the holdout,
(1) its mean per trade before fees is > 0, (2) after fees it beats the current agents (v4), and (3) it has >= 8 trades.
"""
import os, sys, json, statistics, random
here = os.path.dirname(os.path.abspath(__file__))
HOLD = sys.argv[1]
sys.argv = [sys.argv[0], HOLD]
src = open(os.path.join(here, "analyze3.py")).read().split("results = []")[0]
src = src.replace('cut = max(json.loads(l)["t"] for l in open(os.path.join(here, "snap", "curves.jsonl")) if l.strip())', 'cut = 0')
exec(src)
random.seed(11)
hcoins = set(coins)
print(f"holdout coins: {len(hcoins)}")
picks = ["baseline", "current agents (v4)", "late stage sol>=50, not vertical", "late stage sol>=30, not vertical",
         "late stage sol>=65, not vertical", "dip sol>=10 c30<=-15% then uptick"]
exits = {"demo plan": (-0.15, 0.20, 0.40, 600, 25, True), "SL-20/TP15/TP30/15m": (-0.20, 0.15, 0.30, 900, 25, True)}
res = {}
for ename, ex in exits.items():
    print(f"\nexit {ename}:")
    for name in picks:
        trig = triggers(ENTRIES[name], hcoins, rows, series)
        out = {}
        for label, fee, nf in (("gross", 0.0, 0.0), ("net", 0.0125, 0.000105)):
            globals()["FEE"], globals()["NET_FEE"] = fee, nf
            xs = score(trig, series, ex)
            out[label] = xs
        g, n = out["gross"], out["net"]
        if not g: print(f"  {name:38s} no trades"); continue
        lo, hi = boot_ci(n)
        res[(ename, name)] = (statistics.mean(g), statistics.mean(n), len(n))
        print(f"  {name:38s} n={len(n):3d} gross {statistics.mean(g):+6.1f}% | net {statistics.mean(n):+6.1f}% win {sum(x>0 for x in n)/len(n)*100:3.0f}% CI {lo:+.1f}..{hi:+.1f}")
k = ("demo plan", "late stage sol>=50, not vertical"); v = ("demo plan", "current agents (v4)")
if k in res and v in res:
    g, n, cnt = res[k]
    ok = g > 0 and n > res[v][1] and cnt >= 8
    print(f"\nDECISION (demo exit plan): late stage gross {g:+.1f}%, net {n:+.1f}% (n={cnt}) vs current agents net {res[v][1]:+.1f}% -> {'ADOPT' if ok else 'REJECT'}")
else:
    print("\nDECISION: not enough trades -> REJECT")

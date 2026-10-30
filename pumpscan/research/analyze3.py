"""Wide strategy search: entry families x exit plans. Design on the first half, judge on the fresh half.

Usage: python3 analyze3.py DATA_DIR [HOLDOUT_DIR]
With HOLDOUT_DIR, the top candidates (chosen on design + fresh) are also scored on a third, later dataset.
"""
import itertools, json, os, random, statistics, sys

DATA = sys.argv[1]
HOLDOUT = sys.argv[2] if len(sys.argv) > 2 else None
os.environ["QUICK"] = "1"
here = os.path.dirname(os.path.abspath(__file__))
sys.argv = [sys.argv[0], DATA]
exec(open(os.path.join(here, "analyze.py")).read())
NET_FEE = 0.000105  # current default priority 0.0001 SOL + base fee

def boot_ci(xs, n=2000):
    if len(xs) < 3: return (float("nan"), float("nan"))
    ms = sorted(statistics.mean(random.choices(xs, k=len(xs))) for _ in range(n))
    return ms[int(0.05 * n)], ms[int(0.95 * n)]

# Extra feature: net inflow over 5 minutes, cached per (coin, index).
def in300(s, i):
    t = s[i]["t"]
    for j in range(i, -1, -1):
        if t - s[j]["t"] >= 300: return (s[i]["rs"] - s[j]["rs"]) / 1e9
    return None

def not_vertical(f): return (f["chg10"] or 0) < 0.12 and (f["chg30"] or 0) < 0.30
def safe(f): return f["outfrac60"] < 0.15 and f["dd"] < 0.45

ENTRIES = {"baseline": lambda f, s, i: True}
for a, d in itertools.product([2, 5, 10], [0.15, 0.25, 0.40]):
    ENTRIES[f"dip sol>={a} c30<=-{d:.0%} then uptick"] = (lambda a, d: lambda f, s, i: f["sol"] >= a and f["chg30"] is not None and f["chg30"] <= -d and (f["chg10"] or 0) > 0)(a, d)
for g in [30, 50, 65]:
    ENTRIES[f"late stage sol>={g}, not vertical"] = (lambda g: lambda f, s, i: f["sol"] >= g and not_vertical(f) and safe(f))(g)
ENTRIES["quiet accumulation sol>=5"] = lambda f, s, i: f["sol"] >= 5 and f["act60"] <= 0.3 and (f["in60"] or 0) >= 0 and f["dd"] <= 0.2 and safe(f)
for x in [2, 5, 10]:
    ENTRIES[f"sustained in300>={x} SOL, dd<=15%"] = (lambda x: lambda f, s, i: f["dd"] <= 0.15 and safe(f) and (v := in300(s, i)) is not None and v >= x and not_vertical(f))(x)
ENTRIES["current agents (v4)"] = lambda f, s, i: (5 <= f["sol"] < 85 and safe(f) and not_vertical(f) and f["dd"] <= 0.30
    and (f["chg60"] is None or f["chg60"] >= -0.10) and (f["in60"] is None or f["in60"] > -0.3)
    and not (f["gout60"] >= 0.3 and f["gin60"] / (f["gout60"] + 0.05) < 0.8) and (f["chg30"] or 0) / 0.5 >= -0.05)

EXITS = []
for sl, tp1, mult, hold, be in itertools.product([-0.10, -0.20, -0.35, -0.95], [0.15, 0.30, 0.60], [2, 4], [300, 900], [True, False]):
    EXITS.append((sl, tp1, tp1 * mult, hold, 25, be))

def triggers(pred, coinset, rows_, series_):
    out = []; seen = set()
    for m, i, f in rows_:
        if m in coinset and m not in seen and pred(f, series_[m], i):
            seen.add(m); out.append((m, i))
    return out

def score(trig, series_, ex):
    xs = []
    for m, i in trig:
        r = exit_sim(series_[m], i, *ex)
        if r is not None: xs.append(r / SIZE * 100)
    return xs

cut = max(json.loads(l)["t"] for l in open(os.path.join(here, "snap", "curves.jsonl")) if l.strip())
design = {m for m in coins if launch[m].get("ts", launch[m]["t0"]) <= cut}
fresh = set(coins) - design
print(f"design coins {len(design)}, fresh coins {len(fresh)}; entries {len(ENTRIES)} x exits {len(EXITS)} = {len(ENTRIES) * len(EXITS)} strategies")

results = []
for name, pred in ENTRIES.items():
    td, tf = triggers(pred, design, rows, series), triggers(pred, fresh, rows, series)
    for ex in EXITS:
        xd = score(td, series, ex)
        if len(xd) < 12: continue
        results.append((statistics.mean(xd), name, ex, xd, td, tf))
results.sort(key=lambda r: r[0], reverse=True)

print("\nTop 15 on the DESIGN half, with the same strategy on the FRESH half (the honest number):")
random.seed(7)
shortlist = []
for ev, name, ex, xd, td, tf in results[:15]:
    xf = score(tf, series, ex)
    lo, hi = boot_ci(xf)
    fev = statistics.mean(xf) if xf else float("nan")
    shortlist.append((fev, name, ex, len(xf)))
    print(f"  design {ev:+6.1f}% n={len(xd):3d} | fresh {fev:+6.1f}% n={len(xf):3d} win {(sum(x>0 for x in xf)/len(xf)*100 if xf else 0):3.0f}% CI {lo:+.1f}..{hi:+.1f} | {name} | SL {ex[0]:+.0%} TP1 {ex[1]:+.0%} TP2 {ex[2]:+.0%} hold {ex[3]}s breakeven {ex[5]}")

# Best exit for each entry family on the design half, scored on fresh (less selection pressure than the global top).
print("\nEach entry family with its best design-half exit, scored on fresh:")
best = {}
for ev, name, ex, xd, td, tf in results:
    if name not in best: best[name] = (ev, ex, xd, tf)
for name, (ev, ex, xd, tf) in sorted(best.items(), key=lambda kv: -kv[1][0]):
    xf = score(tf, series, ex)
    lo, hi = boot_ci(xf)
    print(f"  {name:40s} design {ev:+6.1f}% (n={len(xd)}) -> fresh {statistics.mean(xf) if xf else float('nan'):+6.1f}% (n={len(xf)}) CI {lo:+.1f}..{hi:+.1f} | SL {ex[0]:+.0%} TP1 {ex[1]:+.0%} TP2 {ex[2]:+.0%} hold {ex[3]}s be {ex[5]}")

# Exit plans alone: every coin's first look, which exit loses least? (design -> fresh)
print("\nExit plans on every coin (baseline entry), design -> fresh:")
tb_d, tb_f = triggers(ENTRIES["baseline"], design, rows, series), triggers(ENTRIES["baseline"], fresh, rows, series)
ex_rank = sorted(((statistics.mean(score(tb_d, series, ex)), ex) for ex in EXITS), reverse=True)[:5]
for ev, ex in ex_rank:
    xf = score(tb_f, series, ex)
    print(f"  design {ev:+6.1f}% -> fresh {statistics.mean(xf):+6.1f}% | SL {ex[0]:+.0%} TP1 {ex[1]:+.0%} TP2 {ex[2]:+.0%} hold {ex[3]}s be {ex[5]}")

json.dump([[fev, name, list(ex), n] for fev, name, ex, n in shortlist], open(os.path.join(here, "shortlist.json"), "w"))

if HOLDOUT:
    launch.clear(); series.clear()
    for line in open(os.path.join(HOLDOUT, "launches.jsonl")):
        x = json.loads(line); launch[x["mint"]] = x
    for line in open(os.path.join(HOLDOUT, "curves.jsonl")):
        if line.strip():
            x = json.loads(line)
            if x["vt"] > 0 and x["vs"] > 0: series[x["m"]].append(x)
    hcoins = [m for m in series if m in launch and len(series[m]) > 10]
    hrows = []
    for m in hcoins:
        s = series[m]; t0 = launch[m]["t0"]
        for i in range(len(s)):
            if s[i]["c"] == 1: break
            f = features(s, i, t0)
            if f["age"] >= 20 and f["chg30"] is not None: hrows.append((m, i, f))
    print(f"\nHOLDOUT ({len(hcoins)} coins recorded after everything above):")
    for fev, name, ex, n in sorted(shortlist, reverse=True)[:5] + [(0, "baseline", EXITS[0], 0)]:
        th = triggers(ENTRIES[name], set(hcoins), hrows, series)
        xh = score(th, series, tuple(ex))
        lo, hi = boot_ci(xh)
        print(f"  {name:40s} fresh {fev:+6.1f}% -> holdout {statistics.mean(xh) if xh else float('nan'):+6.1f}% n={len(xh)} CI {lo:+.1f}..{hi:+.1f}")

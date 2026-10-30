"""Per-period consistency of exit variants for the current agents' entries."""
import sys, statistics
sys.argv = [sys.argv[0]] + sys.argv[1:]
exec(open(__file__.replace("diagnose2.py", "diagnose.py")).read().split('report("CURRENT')[0])
def per_period(label, **kw):
    out = []
    for name, (series, trig) in periods.items():
        xs = [r["net"] / SIZE * 100 for m, i in trig if (r := trade(series[m], i, **kw))]
        px_ = [r["price"] / SIZE * 100 for m, i in trig if (r := trade(series[m], i, **kw))]
        stops = sum(1 for m, i in trig if (r := trade(series[m], i, **kw)) and r["final_reason"] == "stop loss")
        out.append(f"{name}: net {statistics.mean(xs):+5.1f}% price {statistics.mean(px_):+5.1f}% stops {stops/len(xs)*100:3.0f}% (n={len(xs)})")
    print(f"  {label:44s} | " + " | ".join(out))
print("Exit variants for the current agents' entries, each period separately:")
per_period("current plan (SL-15, TP20/40, 10 min)")
per_period("+ sell on safety veto", veto_exit=True)
for h in (60, 120, 180, 300):
    per_period(f"hold {h} s", hold=h)
for sl in (-0.10, -0.25, -0.99):
    per_period(f"stop {sl:+.0%} (10 min)", sl=sl)
per_period("hold 180 s, no stop", hold=180, sl=-0.99)
per_period("hold 180 s, TP 10/20", hold=180, tp1=0.10, tp2=0.20)
per_period("hold 120 s, no stop", hold=120, sl=-0.99)

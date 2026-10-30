import sys, statistics
exec(open(__file__.replace("diagnose3.py", "diagnose.py")).read().split('report("CURRENT')[0])
def pp(label, **kw):
    cells, pooled = [], []
    for name, (series, trig) in periods.items():
        xs = [r["net"] / SIZE * 100 for m, i in trig if (r := trade(series[m], i, **kw))]
        pooled += xs
        cells.append(f"{name}: {statistics.mean(xs):+5.1f}%")
    print(f"  {label:34s} | " + " | ".join(cells) + f" | pooled {statistics.mean(pooled):+5.1f}% (n={len(pooled)})")
print("Hold x stop, current agents' entries (TP 20/40 unchanged):")
for h in (30, 45, 60, 90):
    for sl in (-0.10, -0.15, -0.25):
        pp(f"hold {h:3d} s, stop {sl:+.0%}", hold=h, sl=sl)

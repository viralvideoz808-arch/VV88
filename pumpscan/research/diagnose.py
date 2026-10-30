"""Where does the money go? Per-trade forensics for the current agents' entries across all recorded periods.

Usage: python3 diagnose.py DIR [DIR ...]   (each DIR has launches.jsonl + curves.jsonl from collect.py)
"""
import collections, json, os, statistics, sys

SIZE, FEE, NET = 0.03, 0.0125, 0.000105
K0 = 30 * 1.073e9

def load(d):
    launch, series = {}, collections.defaultdict(list)
    for l in open(os.path.join(d, "launches.jsonl")):
        x = json.loads(l); launch[x["mint"]] = x
    for l in open(os.path.join(d, "curves.jsonl")):
        if l.strip():
            x = json.loads(l)
            if x["vt"] > 0 and x["vs"] > 0: series[x["m"]].append(x)
    return launch, series

px = lambda p: (p["vs"] / 1e9) / (p["vt"] / 1e6)
def buy(p, sol): net = sol / (1 + FEE); vs, vt = p["vs"] / 1e9, p["vt"] / 1e6; return net * vt / (vs + net)
def sell_gross(p, tok): vs, vt = p["vs"] / 1e9, p["vt"] / 1e6; return tok * vs / (vt + tok)

def feats(s, i):
    p = s[i]; t = p["t"]; pr = px(p)
    def ago(sec):
        for j in range(i, -1, -1):
            if t - s[j]["t"] >= sec: return s[j]
    def chg(sec): q = ago(sec); return pr / px(q) - 1 if q else None
    def inf(sec): q = ago(sec); return (p["rs"] - q["rs"]) / 1e9 if q else None
    gin = gout = 0; mof = 0; j = i
    while j > 0 and t - s[j - 1]["t"] <= 60:
        d = (s[j]["rs"] - s[j - 1]["rs"]) / 1e9
        if d > 0: gin += d
        if d < 0:
            gout -= d
            if s[j - 1]["rs"] > 0: mof = max(mof, -d / (s[j - 1]["rs"] / 1e9))
        j -= 1
    hi = max(px(s[j]) for j in range(i + 1) if t - s[j]["t"] <= 300)
    return dict(sol=p["rs"] / 1e9, c10=chg(10), c30=chg(30), c60=chg(60), in60=inf(60), gin=gin, gout=gout, mof=mof, dd=1 - pr / hi)

def v4(f):
    if f["c30"] is None: return False
    if f["mof"] >= 0.15 or f["c30"] <= -0.25 or f["dd"] >= 0.30: return False
    if f["c60"] is not None and f["c60"] < -0.10: return False
    if f["in60"] is not None and f["in60"] <= -0.3: return False
    if f["gout"] >= 0.3 and f["gin"] / (f["gout"] + 0.05) < 0.8: return False
    if f["c30"] / 0.5 < -0.05: return False
    if (f["c10"] or 0) >= 0.12 or f["c30"] >= 0.30: return False
    return 5 <= f["sol"] < 85

def trade(s, i, sl=-0.15, tp1=0.20, tp2=0.40, hold=600, trail=25, be=True, veto_exit=False, tp1_frac=0.5):
    """Demo exit plan with full bookkeeping. Every fill lands one poll (~2 s) after its trigger."""
    if i + 2 >= len(s): return None
    e = s[i + 1]; tokens = buy(e, SIZE); entry = px(e); peak = entry
    left = tokens; proceeds_gross = 0.0; txs = 1; events = []; half = False
    k = i + 2
    while k < len(s):
        q = s[k]
        if q["t"] - e["t"] > hold or q["c"] == 1: break
        p = px(q); peak = max(peak, p); chg = p / entry - 1
        fill = s[k + 1] if k + 1 < len(s) else q
        reason = None
        if chg <= sl: reason = "stop loss"
        elif half and be and chg <= 0: reason = "break-even after TP1"
        elif half and p / peak - 1 <= -trail / 100: reason = "trailing after TP1"
        elif chg >= tp2: reason = "take profit 2"
        elif veto_exit and k > i + 2:
            f = feats(s, k)
            if f["mof"] >= 0.15 or f["dd"] >= 0.45 or (f["c30"] is not None and f["c30"] <= -0.25): reason = "safety veto"
        if reason:
            g = sell_gross(fill, left); proceeds_gross += g; txs += 1
            events.append((reason, chg, px(fill) / entry - 1)); left = 0; break
        if not half and chg >= tp1:
            amt = left * tp1_frac
            g = sell_gross(fill, amt); proceeds_gross += g; left -= amt; half = True; txs += 1
            events.append(("take profit 1", chg, px(fill) / entry - 1))
            if left <= 0: break
        k += 1
    if left > 0:
        last = s[min(k, len(s) - 1)]
        g = sell_gross(last, left); proceeds_gross += g; txs += 1
        events.append(("time limit", px(last) / entry - 1, px(last) / entry - 1))
    buy_fee = SIZE - SIZE / (1 + FEE)
    sell_fee = proceeds_gross * FEE
    net = proceeds_gross - sell_fee - SIZE - txs * NET
    price_part = proceeds_gross - SIZE / (1 + FEE)  # what price movement alone did to the invested amount
    return {"net": net, "price": price_part, "pump_fees": buy_fee + sell_fee, "network": txs * NET, "events": events,
            "final_reason": events[-1][0]}

periods = {}
for d in sys.argv[1:]:
    launch, series = load(d)
    trig = []
    for m, s in series.items():
        if m not in launch or len(s) < 10: continue
        for i in range(len(s)):
            if s[i]["c"] == 1: break
            f = feats(s, i)
            if v4(f): trig.append((m, i)); break
    periods[os.path.basename(os.path.abspath(d))] = (series, trig)
    print(f"{d}: {len(series)} coins, {len(trig)} current-agent entries")

def report(label, **kw):
    allt = []
    for name, (series, trig) in periods.items():
        for m, i in trig:
            r = trade(series[m], i, **kw)
            if r: allt.append(r)
    n = len(allt)
    tot = lambda key: sum(r[key] for r in allt)
    print(f"\n{label}: n={n}  mean net {tot('net')/n/SIZE*100:+.1f}% per trade")
    print(f"   price movement {tot('price')/n/SIZE*100:+.1f}% | pump.fun fees {-tot('pump_fees')/n/SIZE*100:.1f}% | network fees {-tot('network')/n/SIZE*100:.1f}%")
    by = collections.defaultdict(list)
    for r in allt: by[r["final_reason"]].append(r)
    for reason, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        trig_lv = [e[1] for r in rs for e in r["events"] if e[0] == reason]
        fill_lv = [e[2] for r in rs for e in r["events"] if e[0] == reason]
        print(f"   ends by {reason:22s} {len(rs):3d} trades ({len(rs)/n*100:3.0f}%) avg net {statistics.mean(r['net'] for r in rs)/SIZE*100:+6.1f}% | trigger at {statistics.mean(trig_lv)*100:+6.1f}%, filled at {statistics.mean(fill_lv)*100:+6.1f}%")
    return allt

report("CURRENT demo plan (SL-15, half at +20, rest +40, 10 min, break-even + 25% trail)")
report("same, plus selling on an on-chain safety veto (what exitOnVeto does)", veto_exit=True)
report("no stop loss at all, 10 min", sl=-0.99)
report("full exit at take-profit 1 (+20%)", tp1_frac=1.0)
report("hold 3 minutes only", hold=180)

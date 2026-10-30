"""Memory-layer tokens per request under S2s (one entry per decision), the 10k-token envelope in chars, chain stats (V)."""
import sys, json, statistics as st, collections
sys.path.insert(0, "/Users/atishoo/Project/WorldMock/docs/dev/research/layered-memory-2026-10-29/out3/econ")
from run_sim import *
data = prep("V")
q = lambda v: (round(st.median(v)), round(sorted(v)[int(.9 * (len(v) - 1))]), round(max(v)))
for EL in (125, 145, 165):
    b = make_builder(data, PROP, publish="decision", entry_len=EL, progress="slim")
    mt, mc, n_by_day = [], [], collections.Counter()
    for r in data["reqs"]:
        if len(r["msgs"]) != 2: continue
        m = {l["layer"]: l for l in json.loads(b(r))}["记忆层"]
        s = ser(m); mc.append(len(s)); mt.append(ctok(s))
    e = list(data["entries"].values()); te = st.mean(ctok(x) for x in e)
    rb = ser(recent_block(1791388800, prose_pool(data))); trb = ctok(rb)
    fixed = ctok(ser({"layer": "记忆层", "可查": KECHA, "近三天": []})) + trb
    fit = (10000 - fixed) / te
    print(f"entry {EL} chars = {te:.1f} tok ({te/EL:.3f} tok/char); 记忆层 chars med/p90/max {q(mc)}, tokens {q(mt)}; "
          f"fixed part (可查+近三天+keys) {fixed:.0f} tok; entries fitting in 10k tok: {fit:.0f} "
          f"(= {fit*EL + len(rb) + 120:.0f} chars of 记忆层)")
# decisions per day (all decisions, incl. chain ones) and per-day max
per = collections.Counter(data["date_of"].values()); print("decisions per day", dict(per))
fresh = collections.Counter(day(r["t"]) for r in data["reqs"] if len(r["msgs"]) == 2); print("fresh per day", dict(fresh))
# chain lengths
chains = []; cur = 0
for r in data["reqs"]:
    if len(r["msgs"]) == 2:
        if cur: chains.append(cur)
        cur = 1
    elif r["did"] != getattr(sys.modules[__name__], "_last", None):
        cur += 1
    globals()["_last"] = r["did"]
chains.append(cur)
print("decisions per fresh snapshot (chain length incl. head): max", max(chains), "mean %.2f" % st.mean(chains), collections.Counter(chains).most_common(6))
print("max actual prompt tokens (decision)", max(r["prompt"] for r in data["reqs"]))

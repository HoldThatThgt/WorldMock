"""(a) scenarios with the P2 slim 进度层 and a size readout. Usage: scenarios2.py [G] [T_ST] [ENTRY_LEN]"""
import sys, json, statistics as st
sys.path.insert(0, "/Users/atishoo/Project/WorldMock/docs/dev/research/layered-memory-2026-10-29/out3/econ")
from run_sim import *
G = int(sys.argv[1]) if len(sys.argv) > 1 else 128
T = int(sys.argv[2]) if len(sys.argv) > 2 else 5660
EL = int(sys.argv[3]) if len(sys.argv) > 3 else 145
data = prep("V")
TODO_FIRST = ("人设层", "待办层", "记忆层", "工具层", "环境层", "进度层")
SC = [
    ("S0 actual V", None, None, None, "actual"),
    ("S1s current order + new memory, per-decision, slim progress", CUR, "decision", True, "slim"),
    ("S2s proposal order, per-decision, slim progress", PROP, "decision", True, "slim"),
    ("S3s todo-first, per-decision, slim progress", TODO_FIRST, "decision", True, "slim"),
    ("S4s proposal order, per-second, slim progress", PROP, "second", False, "slim"),
    ("S5 proposal order, per-segment, full-session progress (P1 shape)", PROP, "segment", False, "actual"),
    ("S5s proposal order, per-segment, slim progress (lower bound, facts would be missing)", PROP, "segment", False, "slim"),
    ("S5t proposal order, per-segment, slim progress + current segment's entries in 进度层 tail (equal content)", PROP, "segment", False, "slim+segtail"),
    ("S1 current order + new memory, per-decision, actual progress", CUR, "decision", True, "actual"),
    ("S2 proposal order, per-decision, actual progress", PROP, "decision", True, "actual"),
]
res = {}; base = None
for name, order, pub, chain, prog in SC:
    if order is None:
        b = lambda r: r["msgs"][1]["content"]; cd = None
    else:
        b = make_builder(data, order, publish=pub, entry_len=EL, progress=prog); cd = make_chain_delta(data) if chain else None
    rows = simulate(data, b, G, T, cd)
    c = costs(rows); base = base or c
    fr, hi = c["fresh"], c["history"]
    days = sorted(k for k in c if k[0].isdigit())
    print(f"{name}\n   ${c['all']['cost']/5:.4f}/day (delta {(c['all']['cost']-base['all']['cost'])/5:+.4f}); fresh: prompt {(fr['hit']+fr['miss'])/fr['n']:.0f} "
          f"hit {fr['hit']/fr['n']:.0f} miss {fr['miss']/fr['n']:.0f} hit% {fr['hit']/(fr['hit']+fr['miss']):.3f} "
          f"${fr['cost']/fr['n']:.5f}/turn; history hit% {hi['hit']/(hi['hit']+hi['miss']):.3f} ${hi['cost']/hi['n']:.5f}/turn")
    print("   per day:", " ".join(f"{d} ${c[d]['cost']:.4f}" for d in days))
    res[name] = c
json.dump(res, open(sys.path[0] + f"/scenarios2_G{G}_T{T}_E{EL}.json", "w"), ensure_ascii=False, indent=1)
# size readout for S2s builder
b = make_builder(data, PROP, publish="decision", entry_len=EL, progress="slim")
mem, prog, todo, today_n = [], [], [], []
for r in data["reqs"]:
    if len(r["msgs"]) != 2: continue
    arr = json.loads(b(r)); by = {l["layer"]: l for l in arr}
    mem.append(len(ser(by["记忆层"]))); prog.append(len(ser(by["进度层"]))); todo.append(len(ser(by["待办层"]))); today_n.append(len(by["记忆层"]["今天"]))
q = lambda v: (round(st.median(v)), sorted(v)[int(.9 * (len(v) - 1))], max(v))
print("S2s 记忆层 chars med/p90/max", q(mem), "今天 entries", q(today_n), "进度层(slim)", q(prog), "待办层+orders", q(todo))
e = list(data["entries"].values()); print("entry chars mean %.0f tokens/entry %.1f" % (st.mean(map(len, e)), st.mean(ctok(x) for x in e)))
m = by["记忆层"]; print("近三天 chars", len(ser(m["近三天"])), "tok %.0f" % ctok(ser(m["近三天"])), "可查", len(KECHA))

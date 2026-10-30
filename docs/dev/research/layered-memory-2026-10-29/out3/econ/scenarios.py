"""Scenario table for (a). Usage: scenarios.py [G] [T_ST] [ENTRY_LEN]"""
import sys, json
sys.path.insert(0, "/Users/atishoo/Project/WorldMock/docs/dev/research/layered-memory-2026-10-29/out3/econ")
from run_sim import *
G = int(sys.argv[1]) if len(sys.argv) > 1 else 128
T = int(sys.argv[2]) if len(sys.argv) > 2 else 5660
EL = int(sys.argv[3]) if len(sys.argv) > 3 else 145
data = prep("V")
TODO_FIRST = ("人设层", "待办层", "记忆层", "工具层", "环境层", "进度层")
SC = [
    ("S0 actual V (current order, current content)", None, None, None),
    ("S1 current order + new memory, per-decision", CUR, "decision", True),
    ("S2 proposal order, per-decision (ii)", PROP, "decision", True),
    ("S3 todo before memory, per-decision", TODO_FIRST, "decision", True),
    ("S4 proposal order, per-second publish", PROP, "second", False),
    ("S5 proposal order, per-segment publish", PROP, "segment", False),
    ("S6 todo first, per-segment publish", TODO_FIRST, "segment", False),
]
out = {}
base = None
for name, order, pub, chain in SC:
    if order is None:
        b = lambda r: r["msgs"][1]["content"]; cd = None
    else:
        b = make_builder(data, order, publish=pub, entry_len=EL); cd = make_chain_delta(data) if chain else None
    data["unmapped"] = 0
    rows = simulate(data, b, G, T, cd)
    c = costs(rows)
    if base is None:
        base = c
    days = sorted(k for k in c if k[0].isdigit())
    fr = c["fresh"]; hi = c["history"]
    print(f"{name}: decision-turn cost ${c['all']['cost']:.4f} (/day ${c['all']['cost']/5:.4f}, delta vs S0 "
          f"{(c['all']['cost']-base['all']['cost'])/5:+.4f}/day) fresh hit% {fr['hit']/(fr['hit']+fr['miss']):.3f} "
          f"fresh mean prompt {(fr['hit']+fr['miss'])/fr['n']:.0f} hit {fr['hit']/fr['n']:.0f} | history hit% "
          f"{hi['hit']/(hi['hit']+hi['miss']):.3f} unmapped={data['unmapped']}")
    print("    per day:", " ".join(f"{d} ${c[d]['cost']:.4f}" for d in days))
    out[name] = {k: v for k, v in c.items()}
json.dump(out, open(sys.path[0] + f"/scenarios_G{G}_T{T}_E{EL}.json", "w"), ensure_ascii=False, indent=1)

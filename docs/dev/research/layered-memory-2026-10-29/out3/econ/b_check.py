"""Robustness: same model and scenarios on B (read-only)."""
import sys
sys.path.insert(0, "/Users/atishoo/Project/WorldMock/docs/dev/research/layered-memory-2026-10-29/out3/econ")
from run_sim import *
run = sys.argv[1] if len(sys.argv) > 1 else "B"
data = prep(run)
TF = ("人设层", "待办层", "记忆层", "工具层", "环境层", "进度层")
ident = lambda r: r["msgs"][1]["content"]
rows = simulate(data, ident, 128, 5660)
c = costs(rows); ca = costs(rows, "actual_hit")
print(f"{run} S0 sim ${c['all']['cost']/5:.4f}/day vs actual ${ca['all']['cost']/5:.4f}/day; fresh hit sim/act "
      f"{c['fresh']['hit']:.0f}/{ca['fresh']['hit']:.0f}; MAE {sum(abs(x['hit']-x['actual_hit']) for x in rows)/len(rows):.0f}")
for name, order, pub, chain, prog, todo in [("S1s cur order new mem", CUR, "decision", True, "slim", "orders"),
                                            ("S2s proposal", PROP, "decision", True, "slim", "orders"),
                                            ("S4s per-second", PROP, "second", False, "slim", "orders"),
                                            ("S5 per-segment full progress", PROP, "segment", False, "actual", "orders"),
                                            ("S5t per-segment equal content", PROP, "segment", False, "slim+segtail", "orders"),
                                            ("S3x todo-first, orders in tail", TF, "decision", True, "slim", "orders_tail")]:
    b = make_builder(data, order, publish=pub, progress=prog, todo=todo)
    c2 = costs(simulate(data, b, 128, 5660, make_chain_delta(data) if chain else None))
    fr = c2["fresh"]
    print(f"  {name}: ${c2['all']['cost']/5:.4f}/day (delta {(c2['all']['cost']-c['all']['cost'])/5:+.4f}) fresh hit% {fr['hit']/(fr['hit']+fr['miss']):.3f}")

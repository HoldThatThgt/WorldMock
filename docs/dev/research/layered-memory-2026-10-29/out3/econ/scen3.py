"""(iii) order search under per-decision publication with slim progress (V, read-only)."""
import sys
sys.path.insert(0, "/Users/atishoo/Project/WorldMock/docs/dev/research/layered-memory-2026-10-29/out3/econ")
from run_sim import *
G, T, EL = 128, 5660, int(sys.argv[1]) if len(sys.argv) > 1 else 145
data = prep("V")
TF = ("人设层", "待办层", "记忆层", "工具层", "环境层", "进度层")
for name, order, todo in [("S2s proposal", PROP, "orders"), ("S3s todo-first", TF, "orders"),
                          ("S3x todo-first, orders moved to 进度层 tail", TF, "orders_tail"),
                          ("S2x proposal order, orders moved to 进度层 tail", PROP, "orders_tail"),
                          ("S7 memory before persona", ("记忆层", "人设层", "待办层", "工具层", "环境层", "进度层"), "orders")]:
    b = make_builder(data, order, publish="decision", entry_len=EL, progress="slim", todo=todo)
    c = costs(simulate(data, b, G, T, make_chain_delta(data)))
    fr = c["fresh"]
    print(f"{name}: ${c['all']['cost']/5:.4f}/day fresh hit% {fr['hit']/(fr['hit']+fr['miss']):.3f} hit/turn {fr['hit']/fr['n']:.0f}")

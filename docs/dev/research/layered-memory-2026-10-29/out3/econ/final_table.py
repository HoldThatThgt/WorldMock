"""Per-day decision-turn cost table (V) for the report; markdown rows."""
import sys, json
sys.path.insert(0, "/Users/atishoo/Project/WorldMock/docs/dev/research/layered-memory-2026-10-29/out3/econ")
from run_sim import *
data = prep("V")
TF = ("人设层", "待办层", "记忆层", "工具层", "环境层", "进度层")
SC = [("i  current order, current content (V as run)", None),
      ("i' current order, new memory, per-decision, slim 进度层", (CUR, "decision", True, "slim", "orders")),
      ("ii proposal order, per-decision, V's full 进度层", (PROP, "decision", True, "actual", "orders")),
      ("ii proposal order, per-decision, slim 进度层 (P2)", (PROP, "decision", True, "slim", "orders")),
      ("ii' proposal order, per-second publish, slim", (PROP, "second", False, "slim", "orders")),
      ("seg proposal order, per-segment publish, full 进度层 (P1 shape)", (PROP, "segment", False, "actual", "orders")),
      ("seg' per-segment, slim + segment entries in 进度层 tail (equal content)", (PROP, "segment", False, "slim+segtail", "orders")),
      ("iii 人设,待办(无订单),记忆,工具,环境,进度(+订单), per-decision, slim", (TF, "decision", True, "slim", "orders_tail"))]
base = None
print("| scenario | 10-08 | 10-09 | 10-10 | 10-11 | 10-12 | mean/day | Δ/day vs i | fresh hit% | $/fresh turn |")
print("|---|---|---|---|---|---|---|---|---|---|")
for name, spec in SC:
    if spec is None:
        b, cd = (lambda r: r["msgs"][1]["content"]), None
    else:
        order, pub, chain, prog, todo = spec
        b = make_builder(data, order, publish=pub, progress=prog, todo=todo); cd = make_chain_delta(data) if chain else None
    c = costs(simulate(data, b, 128, 5660, cd)); base = base or c
    fr = c["fresh"]; days = sorted(k for k in c if k[0].isdigit())
    print(f"| {name} | " + " | ".join(f"{c[d]['cost']:.4f}" for d in days) + f" | {c['all']['cost']/5:.4f} | "
          f"{(c['all']['cost']-base['all']['cost'])/5:+.4f} | {fr['hit']/(fr['hit']+fr['miss']):.3f} | {fr['cost']/fr['n']:.5f} |")

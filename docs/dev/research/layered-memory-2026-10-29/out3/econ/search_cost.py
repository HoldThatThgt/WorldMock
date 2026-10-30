"""(c) search_memory result sizes under a new cap, and tokens/cost added per decision for 0-3 searches (V-based)."""
import sys, json
sys.path.insert(0, "/Users/atishoo/Project/WorldMock/docs/dev/research/layered-memory-2026-10-29/out3/econ")
from run_sim import *

data = prep("V")
pool = prose_pool(data)
P = lambda n, o: pool[(o * 131) % (len(pool) - n):(o * 131) % (len(pool) - n) + n]
WEEK = "一二三四五六日"


def result(cat, time, items, unlisted=None):
    return ser({"类别": cat, "时间": time, "关键词": None, "结果": items, "未列出": unlisted or []})


cases = {
    "单类 31 天区间 (31x200字)": result("饮食", "2026-10-01~2026-10-31",
                                   [{"日期": f"10-{d:02d}", "星期": "周" + WEEK[d % 7], "回顾": P(200, d)} for d in range(31, 0, -1)]),
    "全部 一年 (3x2000字)": result("全部", "2026", {c: [{"年份": "2026", "回顾": P(2000, i)}] for i, c in
                                              enumerate(("饮食", "工作", "生活"))}),
    "全部 一月 (3x800字)": result("全部", "2026-10", {c: [{"月份": "2026-10", "回顾": P(800, i)}] for i, c in
                                                enumerate(("饮食", "工作", "生活"))}),
    "全部 一天 (3x200字)": result("全部", "2026-10-12", {c: [{"日期": "10-12", "回顾": P(200, i)}] for i, c in
                                                  enumerate(("饮食", "工作", "生活"))}),
    "单类 一天 (1x200字)": result("饮食", "2026-10-12", [{"日期": "10-12", "回顾": P(200, 3)}]),
}
print("== result sizes (rendered JSON)")
sizes = {}
for k, v in cases.items():
    sizes[k] = (len(v), ctok(v)); print(f"  {k}: {len(v)} chars, {ctok(v):.0f} tok ({ctok(v)/len(v):.3f} tok/char)")
CAP_TEXT = 6400
print(f"== proposed cap: body text (回顾/条目 values) <= {CAP_TEXT}字 per call, newest first, rest listed in 未列出;"
      " item count not capped separately")
worst = result("饮食", "2026-10-01~2026-10-31", [{"日期": f"10-{d:02d}", "星期": "周一", "回顾": P(200, d)} for d in range(32, 0, -1)])
print(f"  worst rendered at cap (32 x 200字 + keys): {len(worst)} chars, {ctok(worst):.0f} tok")
# overflow examples
print("  单类 一年 without year digest (12x800=9600字): returns 8 months (6400字), 未列出 4 months")
print("  全部 31 天 (93x200=18600字): returns 32 cells (~10.7 days x 3), 未列出 the rest -> fix: 指定类别或缩短区间")

# cost per extra search turn, S2s fresh prompt mean and V turn outputs
fresh_prompt = 16769        # S2s mean fresh prompt tokens (scenarios2.py)
fresh_out = 170549 / 305    # V fresh-turn mean completion tokens
hist_out = 40996 / 105      # V history-turn mean completion tokens
call_tok = 60               # assistant search_memory call arguments + wrapper


def decision_cost(k, res_tok):
    """k searches: turn j>1 hits everything before (prompt + earlier outputs, output caching observed),
    misses the new tool result; every turn writes its own output."""
    cost = 0.0; prompt = fresh_prompt; added = 0
    for j in range(k):
        prompt_next = prompt + call_tok + res_tok
        cost += (prompt + call_tok) * P_HIT + res_tok * P_MISS + hist_out * P_OUT
        prompt = prompt_next; added += call_tok + res_tok
    return cost, added


print("== added per decision (on top of the 1-turn decision), S2s baseline")
for label, rt in (("small result (1 day x 3 cats, %d tok)" % sizes["全部 一天 (3x200字)"][1], sizes["全部 一天 (3x200字)"][1]),
                  ("cap-size result (%d tok)" % ctok(worst), ctok(worst))):
    for k in range(0, 4):
        c, a = decision_cost(k, rt)
        print(f"  {label}, {k} searches: +{a:.0f} prompt tok on the final turn, +{k} turns, +${c:.4f}")
limit = 1048576 * 0.85
print(f"== capacity: threshold = 0.85 x 1,048,576 (V frozen model_metadata.context_window) = {limit:,.0f} tok;"
      f" at the ModelConfig floor 262,144 it would be {262144*0.85:,.0f}")
worst_prompt = 37421 + 3 * (call_tok + ctok(worst)) + 16000
print(f"  worst single decision: V max decision prompt 37,421 + 3 cap-size searches + 16,000 reserve = {worst_prompt:,.0f} tok"
      f" ({worst_prompt/limit:.1%} of threshold)")
chain = 37421 + 3 * 3 * (call_tok + ctok(worst)) + 16000
print(f"  worst chain (max chain length 3 in V; replayed earlier decisions keep their search results): {chain:,.0f} tok"
      f" ({chain/limit:.1%}; {chain/(262144*.85):.1%} of the 262k floor)")

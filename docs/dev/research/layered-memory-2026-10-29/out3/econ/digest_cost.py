"""(b) Digest call cost at the new caps (day 200字, month 800字, year 2000字 per category), with retries.

Token counts use the class model fitted on usage.prompt_tokens (tokfit.json); output prose uses the same model
(it reproduced V's non-decision completion tokens within 0.3%, see outratio.py). Day inputs are V's real per-day
text (digest_input.py / query_answers.py logic); month/year inputs are synthetic at the caps using real 自述 prose.
Retry model from V plan_day retries: the correction request re-sends the original messages (cached) plus the
rejected output and an English error (miss), and the model writes a full new output.
"""
import sys, json, re, collections
sys.path.insert(0, "/Users/atishoo/Project/WorldMock/docs/dev/research/layered-memory-2026-10-29/out3/econ")
from run_sim import *

MAX_RETRIES = 3          # reflection_validation_max_retries default, configuration/inputs/models.py:224
ERR_TOK = 200            # English validation error message (V plan_day corrections: +850 tok = output ~500 + ~350)
data = prep("V")
pool = prose_pool(data)
first = layers_of(data["reqs"][0])["人设层"]
card = ser({k: v for k, v in first.items() if k != "preferences"})
# the proposal's day-digest prompt text (docs/dev/PROPOSAL_2026-10-29_LAYERED_MEMORY.md, 压缩 section), read-only
PROMPT = next(l.strip()[2:] for l in open("/Users/atishoo/Project/WorldMock/docs/dev/PROPOSAL_2026-10-29_LAYERED_MEMORY.md")
              if l.strip().startswith("> 你替模拟世界"))
PROMPT_TOK = ctok(PROMPT) + 150   # + the strict submit_memory_digest tool schema


def prose(n, off=0):
    s = pool[off % (len(pool) - n): off % (len(pool) - n) + n]
    return s


def call_cost(inp, out, retries):
    """attempt 1: system prompt block hits, rest misses; each retry: previous prompt hits, rejected output+error miss"""
    sys_hit = (PROMPT_TOK // 128) * 128
    c = (sys_hit * P_HIT + (inp - sys_hit) * P_MISS) + out * P_OUT
    for _ in range(retries):
        c += inp * P_HIT + (out + ERR_TOK) * P_MISS + out * P_OUT
    return c


def digest_output(cell, nrefs):
    o = {}
    for i, k in enumerate(("food", "work", "life")):
        o[k] = {"text": prose(cell, 1000 * i), "refs": [f"n{j}" for j in range(1, nrefs + 1)]}
    return ser(o)


# ---- day digest: real V day text
ev = load(RUNS["V"])
last = {}
for s, e in ev:
    if e["event_type"] == "model_response" and e.get("decision_id"):
        last[e["decision_id"]] = (e["occurred_at"], e["data"]["response"]["choices"][0]["message"])
self_txt = collections.defaultdict(list)


def strings(x):
    if isinstance(x, dict):
        for v in x.values():
            yield from strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from strings(v)
    elif isinstance(x, str) and re.search(r"[一-鿿]", x):
        yield x


n_entries = collections.Counter()
for did, (t, m) in last.items():
    d = day(t); n_entries[d] += 1
    for tc in m.get("tool_calls") or []:
        if tc["function"]["name"] in ("conclude_activity", "report_status", "pause_activity"):
            try:
                self_txt[d] += list(strings(json.loads(tc["function"]["arguments"])))
            except Exception:
                pass
    if m.get("content"):
        self_txt[d].append(m["content"])
settled = collections.defaultdict(list)
for s, e in ev:
    if e["event_type"] == "model_request" and e["data"].get("purpose") == "plan_day" and e["data"]["call_id"].endswith("attempt:1"):
        p = json.loads(e["data"]["request"]["messages"][1]["content"])
        mm = re.match(r"(\d+)月(\d+)日", p.get("回顾时段", ""))
        if mm and p.get("动作结果"):
            settled[f"{int(mm.group(1)):02d}-{int(mm.group(2)):02d}"] = p["动作结果"]
queries = collections.defaultdict(list); seen = {}
for r in data["reqs"]:
    if len(r["msgs"]) != 2:
        continue
    for rec in layers_of(r)["进度层"]["events"]:
        det = ((rec.get("result") or {}).get("details") or {})
        if rec.get("type") == "tool_call" and "query" in det:
            seen[rec["occurred_at"]] = ser(det["query"])
for t, q in seen.items():
    queries["%s-%s" % (t[5:7], t[8:10])].append(q)
shell = ser({"编号": "n12", "时刻": "12:28", "核验": [""], "自述": [""]}) + ","
print("== day digest (V real days; input = card + prompt + all entries with full 自述 + query answers)")
rows = []
for d in sorted(self_txt):
    st_lines = settled.get(d) or settled.get("10-11")   # 10-12 has no next plan_day: reuse a workday's line volume
    parts = {"card": ctok(card), "prompt": PROMPT_TOK, "核验": ctok("".join(st_lines)), "自述": ctok("".join(self_txt[d])),
             "查询答案": ctok("".join(queries[d])), "keys": ctok(shell) * n_entries[d]}
    inp = sum(parts.values())
    out = ctok(digest_output(200, 8))
    rows.append((d, inp, out))
    print(f"  {d}: input {inp:6.0f} tok {{" + ", ".join(f"{k} {v:.0f}" for k, v in parts.items()) + f"}} output {out:.0f} tok;"
          f" cost first try ${call_cost(inp, out, 0):.4f}, with {MAX_RETRIES} retries ${call_cost(inp, out, MAX_RETRIES):.4f}")
avg_in = sum(r[1] for r in rows) / len(rows); out_day = rows[0][2]
print(f"  mean input {avg_in:.0f} tok; per day ${call_cost(avg_in, out_day, 0):.4f} (worst ${call_cost(max(r[1] for r in rows), out_day, MAX_RETRIES):.4f})")

print("== month digest (input 31 days x 3 categories x 200字 + keys + count lines + card + prompt)")
dig = [{"日期": f"10-{i:02d}", "编号": f"d{i}", "回顾": prose(200, 37 * i)} for i in range(1, 32)]
month_in = {"digests": ctok(ser(dig)) * 3, "count_lines": ctok(ser([f"番茄牛肉刀削面 {i % 5 + 1} 次" for i in range(120)])),
            "card": ctok(card), "prompt": PROMPT_TOK}
mi = sum(month_in.values()); mo = ctok(digest_output(800, 31))
print(f"  input {mi:.0f} tok {{" + ", ".join(f"{k} {v:.0f}" for k, v in month_in.items()) + f"}}; output {mo:.0f} tok;"
      f" first try ${call_cost(mi, mo, 0):.4f}; with {MAX_RETRIES} retries ${call_cost(mi, mo, MAX_RETRIES):.4f}")
print("== year digest (input 12 months x 3 categories x 800字 + keys + year count lines + card + prompt)")
md = [{"月份": f"2026-{i:02d}", "编号": f"m{i}", "回顾": prose(800, 91 * i)} for i in range(1, 13)]
year_in = {"digests": ctok(ser(md)) * 3, "count_lines": ctok(ser([f"番茄牛肉刀削面 {i % 30 + 1} 次" for i in range(360)])),
           "card": ctok(card), "prompt": PROMPT_TOK}
yi = sum(year_in.values()); yo = ctok(digest_output(2000, 12))
print(f"  input {yi:.0f} tok {{" + ", ".join(f"{k} {v:.0f}" for k, v in year_in.items()) + f"}}; output {yo:.0f} tok"
      f" ({3 * 2000} 字 of text); first try ${call_cost(yi, yo, 0):.4f}; with {MAX_RETRIES} retries ${call_cost(yi, yo, MAX_RETRIES):.4f}")
print("== amortised per simulated day: day %.4f + month %.5f + year %.6f" % (
    call_cost(avg_in, out_day, 0), call_cost(mi, mo, 0) / 30.4, call_cost(yi, yo, 0) / 365))
print("   output tokens vs reserves: year %.0f tok vs PLAN_DAY_MAX_TOKENS 8000 / ACTION_MAX_TOKENS 16000 / legacy 4096 default" % yo)

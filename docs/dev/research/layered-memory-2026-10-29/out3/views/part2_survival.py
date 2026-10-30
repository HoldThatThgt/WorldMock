"""Part 2b: how much of a day's views survive in a guard-compliant day digest.

The digests are the hand-written illustrations in the proposal's 11:50 example (PROPOSAL lines 499-511),
written by the earlier analysis from V's activities, receipts and that day's views under the day prompt and guards.
They are the only digests that exist; real ones would be model-written at midnight. Read by line number from
the proposal file (read-only)."""
import json, re, collections
from common import *
from model import Run

P = "/Users/atishoo/Project/WorldMock/docs/dev/PROPOSAL_2026-10-29_LAYERED_MEMORY.md"
lines = open(P, encoding="utf-8").read().split("\n")
digests = []
cat = None
for ln in lines[497:512]:
    if '"饮食"' in ln: cat = "饮食"
    if '"工作"' in ln: cat = "工作"
    if '"生活"' in ln: cat = "生活"
    m = re.search(r'\{"日期":"2026-(\d\d-\d\d)".*"回顾":"([^"]+)"', ln)
    if m: digests.append((m.group(1), cat, m.group(2)))
MARK = ("觉得", "让我", "踏实", "合适", "打算", "想", "舒服", "合口味", "顺", "从容", "希望", "省心", "清醒", "过头",
        "安静", "不闷", "惦记", "最", "愿意", "喜欢", "收手")
def clauses(t):
    return [c for c in re.split(r"[，。；！？、]", t) if c]

def lcs(a, b):
    best = 0
    for i in range(len(a)):
        for j in range(i + best + 1, len(a) + 1):
            if a[i:j] in b: best = j - i
            else: break
    return best

R = Run("V")
res = []
tot_obj = tot_hit = 0
for d, c, text in digests:
    vs = [v for v in R.view_rows if v["day"] == d and v["cat"] == c]
    objs = {}
    for v in vs: objs[norm(v["about"])] = v
    hits = []
    for o, v in objs.items():
        k = lcs(o, norm(text))
        if k >= min(3, len(o)):
            hits.append(v["about"])
    att = sum(len(x) for x in clauses(text) if any(m in x for m in MARK))
    vchars = sum(len(f"看法·{v['about']}：{v['text']}") for v in vs)
    res.append({"day": d, "cat": c, "digest_chars": len(text), "attitude_clause_chars": att,
                "views": len(vs), "objects": len(objs), "objects_mentioned": len(hits), "mentioned": hits,
                "view_chars": vchars, "attitude_chars_over_view_chars": round(att / vchars, 2) if vchars else None})
    tot_obj += len(objs); tot_hit += len(hits)
json.dump({"cells": res, "objects": tot_obj, "objects_mentioned": tot_hit}, open("part2_survival.json", "w"),
          ensure_ascii=False, indent=1)
for r in res: print(json.dumps(r, ensure_ascii=False))
print("objects", tot_obj, "mentioned", tot_hit)

"""Read-only: estimate DeepSeek tokens per character for each prompt layer by least squares over fresh decision requests.

prompt_tokens(request) ~ c0 + sum_l r_l * chars_l, using the first-turn request of every fresh decision (2 messages).
Usage: .venv/bin/python tokratio.py <run-dir> [<run-dir> ...]
"""
import json
import sqlite3
import sys

import numpy as np

LAYERS = ["工具层", "人设层", "记忆层", "待办层", "环境层", "进度层"]
X, Y, MEMSPLIT = [], [], []
for run in sys.argv[1:]:
    con = sqlite3.connect(f"file:{run}/run/footprints.sqlite?mode=ro", uri=True)
    usage = {}
    for (body,) in con.execute("select body from events where json_extract(body,'$.event_type')='model_response'"):
        e = json.loads(body)
        u = (e["data"].get("response") or {}).get("usage")
        if u:
            usage[e["data"]["call_id"]] = u["prompt_tokens"]
    for (body,) in con.execute("select body from events where json_extract(body,'$.event_type')='model_request'"):
        e = json.loads(body)
        cid = e["data"]["call_id"]
        msgs = e["data"]["request"]["messages"]
        if (":model:" not in cid or len(msgs) != 2 or cid not in usage
                or not msgs[0]["content"].startswith('{"layer":"系统层"')):
            continue
        layers = {l["layer"]: l for l in json.loads(msgs[1]["content"])}
        # character counts of each layer as serialised in the user message (compact JSON, ensure_ascii False)
        ch = [len(json.dumps(layers[n], ensure_ascii=False, separators=(",", ":"))) for n in LAYERS]
        sys_chars = len(msgs[0]["content"])
        tools = len(json.dumps(e["data"]["request"].get("tools") or [], ensure_ascii=False, separators=(",", ":")))
        X.append([1.0, *ch, sys_chars, tools])
        Y.append(usage[cid])
        mem = layers["记忆层"]
        MEMSPLIT.append(sum(len(s) for en in mem.get("earlier_today", []) for s in en.get("settled", [])))
X, Y = np.array(X), np.array(Y, dtype=float)
names = ["const", *LAYERS, "system", "tools"]
# drop columns with no variance (system and tools may be constant) to keep the system well posed
keep = [0] + [i for i in range(1, X.shape[1]) if X[:, i].std() > 0]
coef, *_ = np.linalg.lstsq(X[:, keep], Y, rcond=None)
pred = X[:, keep] @ coef
res = Y - pred
r2 = 1 - (res ** 2).sum() / ((Y - Y.mean()) ** 2).sum()
print("n", len(Y), "R2", round(float(r2), 4), "resid sd", round(float(res.std()), 1), "max abs resid", round(float(abs(res).max()), 1))
for i, c in zip(keep, coef):
    print(f"  {names[i]:6s} {c:8.4f}" + ("" if i == 0 else f"   (chars mean {X[:, i].mean():8.0f}, sd {X[:, i].std():7.0f})"))
# whole-request ratio for reference
total_chars = X[:, 1:7].sum(1) + X[:, 7] + X[:, 8]
print("tokens/char over whole request: median", round(float(np.median(Y / total_chars)), 3))
# bootstrap the memory-layer coefficient
rng = np.random.default_rng(0)
mi = keep.index(3)
bs = []
for _ in range(500):
    idx = rng.integers(0, len(Y), len(Y))
    c, *_ = np.linalg.lstsq(X[idx][:, keep], Y[idx], rcond=None)
    bs.append(c[mi])
print("记忆层 coef bootstrap 2.5/50/97.5%:", [round(float(x), 4) for x in np.percentile(bs, [2.5, 50, 97.5])])

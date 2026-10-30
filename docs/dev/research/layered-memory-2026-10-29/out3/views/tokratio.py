"""Token/char ratio for prompt text: least squares prompt_tokens ~ a*hanzi + b*other_chars + c over fresh first turns
(3x3 normal equations solved by hand; no numpy)."""
import json
from common import *
from model import Run

def solve3(A, b):
    M = [row[:] + [v] for row, v in zip(A, b)]
    for i in range(3):
        p = max(range(i, 3), key=lambda r: abs(M[r][i])); M[i], M[p] = M[p], M[i]
        for r in range(3):
            if r != i:
                f = M[r][i] / M[i][i]
                M[r] = [x - f * y for x, y in zip(M[r], M[i])]
    return [M[i][3] / M[i][i] for i in range(3)]

for run in ("V", "B"):
    R = Run(run)
    X = []; y = []
    for d in R.decisions:
        if not d["fresh"] or not d["responses"]: continue
        req = d["requests"][0]["data"]["request"]
        txt = "".join(m["content"] for m in req["messages"])
        u = (d["responses"][0]["data"].get("response") or {}).get("usage") or {}
        if "prompt_tokens" not in u: continue
        h = hanzi(txt); X.append([h, len(txt) - h, 1.0]); y.append(u["prompt_tokens"])
    A = [[sum(r[i] * r[j] for r in X) for j in range(3)] for i in range(3)]
    b = [sum(r[i] * t for r, t in zip(X, y)) for i in range(3)]
    a, o, c = solve3(A, b)
    pred = [a * r[0] + o * r[1] + c for r in X]; mean = sum(y) / len(y)
    r2 = 1 - sum((t - p) ** 2 for t, p in zip(y, pred)) / sum((t - mean) ** 2 for t in y)
    print(run, len(y), "tok/hanzi=%.3f tok/other=%.3f const=%.0f R2=%.4f" % (a, o, c, r2))
    # 人设层看法这部分按此比率折算
    from part1_sizes import render, persona_from_request
    base = persona_from_request(next(d for d in R.decisions if d["fresh"]))
    toks = []
    for d in R.decisions:
        pv = d["pv"]; s = compact(pv["preferences"]) + "".join(r["view"] for r in pv["relationships"])
        h = hanzi(s); toks.append(a * h + o * (len(s) - h))
    print("   views tokens per decision", {k: round(v) if v else v for k, v in stats(toks).items()})
    vt = [v["text"] for v in R.view_rows]
    h = sum(hanzi(t) for t in vt); n = sum(len(t) for t in vt)
    print("   view text: hanzi share %.2f, tokens per char %.3f" % (h / n, (a * h + o * (n - h)) / n))

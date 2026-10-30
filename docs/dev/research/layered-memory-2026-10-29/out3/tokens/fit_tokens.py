"""Fit DeepSeek prompt_tokens against character classes of what the provider receives.

Read-only over the V and B runs. Writes only to the out3/tokens folder next to this script.
Run:  python3 fit_tokens.py           (numpy required; uses system python3 or uv's)
"""
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parent
RUNS = {
    "V": "/Users/atishoo/Project/WorldMock-Dataset/execution/20261029T023604Z-white-collar-food-variety",
    "B": "/Users/atishoo/Project/WorldMock-Dataset/execution/20261029T023604Z-white-collar-food-baseline",
}


def is_cjk(o):
    return (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0xF900 <= o <= 0xFAFF
            or 0x20000 <= o <= 0x2FA1F)


FINE = ["cjk", "alpha", "digit", "space", "apunct", "cjkpunct", "otherpunct"]


def classes(text):
    """Return Counter of fine classes; coarse = cjk / ascii(alpha+digit+space+apunct) / other."""
    c = Counter()
    for ch in text:
        o = ord(ch)
        if o < 0x80:
            if ch.isalpha():
                c["alpha"] += 1
            elif ch.isdigit():
                c["digit"] += 1
            elif ch.isspace():
                c["space"] += 1
            else:
                c["apunct"] += 1
        elif is_cjk(o):
            c["cjk"] += 1
        elif 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF:
            c["cjkpunct"] += 1
        else:
            c["otherpunct"] += 1
    return c


def coarse(c):
    return {"cjk": c["cjk"], "ascii": c["alpha"] + c["digit"] + c["space"] + c["apunct"],
            "other": c["cjkpunct"] + c["otherpunct"]}


def request_parts(req):
    """Text the provider receives, split by role/part. tools rendered as compact-free default json, CJK literal."""
    parts = defaultdict(str)
    for m in req["messages"]:
        role = m["role"]
        content = m.get("content")
        if isinstance(content, list):
            content = "".join(x.get("text", "") for x in content if isinstance(x, dict))
        if content:
            parts[f"{role}.content"] += content
        if m.get("reasoning_content"):
            parts["assistant.reasoning"] += m["reasoning_content"]
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function", {})
            parts["assistant.tool_calls"] += (fn.get("name") or "") + (fn.get("arguments") or "")
    if req.get("tools"):
        parts["tools"] = json.dumps(req["tools"], ensure_ascii=False)
    return parts


def family(call_id, req):
    if call_id.startswith("plan-day"):
        return "plan_day"
    if ":reflection:" in call_id:
        return "reflection"
    if not req.get("tools"):
        return "consolidation"
    return "decision_fresh" if len(req["messages"]) == 2 else "decision_chain"


def load(run_key):
    db = Path(RUNS[run_key]) / "run" / "footprints.sqlite"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    reqs, usage = {}, {}
    for (body,) in con.execute(
            "select body from events where json_extract(body,'$.event_type') in ('model_request','model_response')"
            " order by event_seq"):
        b = json.loads(body)
        d = b["data"]
        if b["event_type"] == "model_request" and d.get("request"):
            reqs[d["call_id"]] = (d["request"], b["occurred_at"])
        elif b["event_type"] == "model_response":
            u = d.get("usage") or (d.get("response") or {}).get("usage")
            if u and isinstance(u.get("prompt_tokens"), int):
                usage[d["call_id"]] = u
    con.close()
    rows = []
    for cid, (req, at) in reqs.items():
        if cid not in usage:
            continue
        parts = request_parts(req)
        fine_all = Counter()
        fine_noreason = Counter()
        fine_reason = Counter()
        for k, v in parts.items():
            c = classes(v)
            fine_all += c
            if k == "assistant.reasoning":
                fine_reason += c
            else:
                fine_noreason += c
        rows.append({"run": run_key, "call_id": cid, "family": family(cid, req), "at": at,
                     "n_msgs": len(req["messages"]), "prompt_tokens": usage[cid]["prompt_tokens"],
                     "cache_hit": usage[cid].get("prompt_cache_hit_tokens"),
                     "fine": dict(fine_all), "fine_noreason": dict(fine_noreason), "fine_reason": dict(fine_reason),
                     "chars": sum(fine_all.values()), "req": req})
    return rows


def lstsq(X, y, names):
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    pred = X @ coef
    res = y - pred
    ss_res = float(res @ res)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    rel = res / y
    return {"coef": dict(zip(names, [round(float(x), 4) for x in coef])),
            "r2": round(1 - ss_res / ss_tot, 6), "n": int(len(y)),
            "resid_sd": round(float(res.std(ddof=len(names))), 1),
            "resid_mae": round(float(np.abs(res).mean()), 1),
            "resid_p95_abs": round(float(np.percentile(np.abs(res), 95)), 1),
            "resid_max_abs": round(float(np.abs(res).max()), 1),
            "rel_resid_sd_pct": round(float(rel.std() * 100), 2),
            "rel_resid_p95_abs_pct": round(float(np.percentile(np.abs(rel), 95) * 100), 2)}, coef


def design(rows, cols, key="fine", intercept=True, extra=None):
    X = []
    for r in rows:
        f = r[key]
        line = []
        for col in cols:
            if isinstance(col, tuple):
                line.append(sum(f.get(x, 0) for x in col))
            else:
                line.append(f.get(col, 0))
        if extra:
            line += [fn(r) for fn in extra]
        if intercept:
            line.append(1.0)
        X.append(line)
    return np.array(X, dtype=float)


ASCII = ("alpha", "digit", "space", "apunct")
OTHER = ("cjkpunct", "otherpunct")


def main():
    rows = load("V") + load("B")
    fams = Counter((r["run"], r["family"]) for r in rows)
    y = np.array([r["prompt_tokens"] for r in rows], dtype=float)
    report = {"pairs": len(rows), "families": {f"{a}/{b}": n for (a, b), n in sorted(fams.items())}}

    # 1. basic coarse fit, all requests, reasoning included
    names = ["a_cjk", "b_ascii", "c_other", "d_intercept"]
    report["fit_basic_all"], coef_basic = lstsq(design(rows, ["cjk", ASCII, OTHER]), y, names)
    # per-run & fresh-only stability
    for label, sel in [("fit_basic_V", lambda r: r["run"] == "V"), ("fit_basic_B", lambda r: r["run"] == "B"),
                       ("fit_basic_fresh", lambda r: r["family"] == "decision_fresh"),
                       ("fit_basic_nonfresh", lambda r: r["family"] != "decision_fresh")]:
        sub = [r for r in rows if sel(r)]
        report[label], _ = lstsq(design(sub, ["cjk", ASCII, OTHER]), np.array([r["prompt_tokens"] for r in sub],
                                                                             dtype=float), names)
    # 2. reasoning test: reasoning chars as separate regressors
    Xr = np.hstack([design(rows, ["cjk", ASCII, OTHER], key="fine_noreason", intercept=False),
                    design(rows, ["cjk", ASCII, OTHER], key="fine_reason", intercept=True)])
    report["fit_reasoning_split"], _ = lstsq(Xr, y, ["a_cjk", "b_ascii", "c_other", "r_cjk", "r_ascii", "r_other",
                                                    "d"])
    Xn = design(rows, ["cjk", ASCII, OTHER], key="fine_noreason")
    report["fit_basic_reasoning_excluded"], _ = lstsq(Xn, y, names)
    # 3. fine fit (what the memory-layer estimate uses)
    fine_names = ["cjk", "alpha", "digit", "space", "apunct", ("cjkpunct", "otherpunct")]
    Xf = design(rows, fine_names, extra=[lambda r: r["n_msgs"]])
    report["fit_fine_all"], coef_fine = lstsq(Xf, y, ["cjk", "alpha", "digit", "space", "apunct", "other",
                                                      "per_message", "intercept"])
    # family-wise residuals of basic fit
    pred = design(rows, ["cjk", ASCII, OTHER]) @ coef_basic
    fam_res = defaultdict(list)
    for r, p in zip(rows, pred):
        fam_res[r["family"]].append((r["prompt_tokens"] - p) / r["prompt_tokens"] * 100)
    report["basic_rel_resid_by_family_pct"] = {k: {"n": len(v), "mean": round(float(np.mean(v)), 2),
                                                   "sd": round(float(np.std(v)), 2)} for k, v in fam_res.items()}
    # descriptive: blended tokens/char overall
    tot_chars = sum(r["chars"] for r in rows)
    mix = Counter()
    for r in rows:
        mix.update(r["fine"])
    report["corpus_mix"] = {k: round(mix[k] / tot_chars, 4) for k in FINE}
    report["corpus_tokens_per_char_raw"] = round(float(y.sum() / tot_chars), 4)
    report["prompt_tokens_range"] = [int(y.min()), int(np.median(y)), int(y.max())]
    (OUT / "fit_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    np.save(OUT / "coef_basic.npy", coef_basic)
    np.save(OUT / "coef_fine.npy", coef_fine)
    # keep a light row index for the other scripts
    slim = [{k: r[k] for k in ("run", "call_id", "family", "n_msgs", "prompt_tokens", "cache_hit", "fine", "chars")}
            for r in rows]
    (OUT / "rows.json").write_text(json.dumps(slim, ensure_ascii=False))
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())

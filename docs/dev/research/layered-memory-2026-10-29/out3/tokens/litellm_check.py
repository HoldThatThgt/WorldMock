"""Does the project's estimator (prompt_capacity.prompt_token_count -> litellm token_counter) track DeepSeek?

Run from the WorldMock checkout so the project import resolves:
  cd /Users/atishoo/Project/WorldMock && uv run --no-sync python <this file>
Read-only over V and B. Writes litellm_report.json beside this script.
"""
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
from litellm import token_counter

from worldmock.agents.prompt.prompt_capacity import prompt_token_count

HERE = Path(__file__).resolve().parent
RUNS = {
    "V": "/Users/atishoo/Project/WorldMock-Dataset/execution/20261029T023604Z-white-collar-food-variety",
    "B": "/Users/atishoo/Project/WorldMock-Dataset/execution/20261029T023604Z-white-collar-food-baseline",
}


def family(cid, req):
    if cid.startswith("plan-day"):
        return "plan_day"
    if ":reflection:" in cid:
        return "reflection"
    if not req.get("tools"):
        return "consolidation"
    return "decision_fresh" if len(req["messages"]) == 2 else "decision_chain"


def load(run):
    db = Path(RUNS[run]) / "run" / "footprints.sqlite"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    order, reqs, usage = [], {}, {}
    for (body,) in con.execute(
            "select body from events where json_extract(body,'$.event_type') in ('model_request','model_response')"
            " order by event_seq"):
        b = json.loads(body)
        d = b["data"]
        if b["event_type"] == "model_request" and d.get("request"):
            reqs[d["call_id"]] = d["request"]
            order.append(d["call_id"])
        elif b["event_type"] == "model_response":
            u = d.get("usage") or (d.get("response") or {}).get("usage")
            if u:
                usage[d["call_id"]] = u["prompt_tokens"]
    con.close()
    return [(run, cid, reqs[cid], usage[cid]) for cid in order if cid in usage]


def stratified(rows):
    """15 per run: 9 fresh, 4 chain, 1 consolidation, 1 reflection, evenly spaced through the run."""
    quota = {"decision_fresh": 9, "decision_chain": 4, "consolidation": 1, "reflection": 1}
    pick = []
    for run in ("V", "B"):
        for fam, n in quota.items():
            pool = [r for r in rows if r[0] == run and family(r[1], r[2]) == fam]
            idx = np.linspace(0, len(pool) - 1, n).round().astype(int)
            pick += [pool[i] for i in idx]
    return pick


def stats(pairs):
    est = np.array([p[0] for p in pairs], dtype=float)
    act = np.array([p[1] for p in pairs], dtype=float)
    ratio = est / act
    slope, intercept = np.polyfit(act, est, 1)
    return {"n": len(pairs), "ratio_est_over_actual_mean": round(float(ratio.mean()), 4),
            "ratio_min": round(float(ratio.min()), 4), "ratio_max": round(float(ratio.max()), 4),
            "ratio_sd": round(float(ratio.std()), 4),
            "bias_tokens_mean": round(float((est - act).mean()), 1),
            "bias_tokens_median": round(float(np.median(est - act)), 1),
            "fit_est_vs_actual": {"slope": round(float(slope), 4), "intercept": round(float(intercept), 1)},
            "pearson_r": round(float(np.corrcoef(est, act)[0, 1]), 5),
            "sum_est_over_sum_actual": round(float(est.sum() / act.sum()), 4)}


def main():
    rows = load("V") + load("B")
    sample = stratified(rows)
    detail = []
    for run, cid, req, actual in sample:
        est = prompt_token_count(req)
        detail.append({"run": run, "family": family(cid, req), "call_id": cid[-40:], "litellm": est,
                       "deepseek": actual, "ratio": round(est / actual, 4)})
    report = {"sample30": stats([(d["litellm"], d["deepseek"]) for d in detail]), "sample30_rows": detail}
    by_fam = {}
    for d in detail:
        by_fam.setdefault(d["family"], []).append((d["litellm"], d["deepseek"]))
    report["sample30_by_family"] = {k: stats(v) for k, v in by_fam.items()}
    full = [(prompt_token_count(req), actual) for _, _, req, actual in rows]
    report["all_921"] = stats(full)
    # memory layer text alone, as the capacity-failure log would count it
    mem = []
    for run, cid, req, actual in rows:
        if family(cid, req) != "decision_fresh" or run != "V":
            continue
        for layer in json.loads(req["messages"][1]["content"]):
            if layer.get("layer") == "记忆层":
                text = json.dumps(layer, ensure_ascii=False, separators=(",", ":"))
                mem.append((len(text), token_counter(model=req["model"], text=text)))
    mem = np.array(mem, dtype=float)
    report["memory_layer_V_litellm"] = {"n": int(len(mem)),
                                        "litellm_tokens_per_char": round(float(mem[:, 1].sum() / mem[:, 0].sum()), 4),
                                        "median_litellm_tokens": int(np.median(mem[:, 1]))}
    (HERE / "litellm_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps({k: v for k, v in report.items() if k != "sample30_rows"}, ensure_ascii=False, indent=1))
    for d in detail:
        print(d)


if __name__ == "__main__":
    sys.exit(main())

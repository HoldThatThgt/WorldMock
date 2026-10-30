"""Memory-layer token cost: current 记忆层 in V/B fresh decisions, and the proposed new layer.

1. Per-layer level regression over fresh decision requests: prompt_tokens ~ sum_k k_layer * qwen3_tokens(layer) + c,
   to see whether the 记忆层 tokenizes like the rest (k_memory vs others).
2. Current 记忆层 char mix and its DeepSeek token estimate under each coefficient set.
3. New layer proxy, built from synth/example_fmt.json (V 10-12 data) re-shaped to the ruled design:
   no 长期, 近三天 = per category per day {"日期","星期","回顾"} (回顾 only), 今天 = one entry per decision.
4. Budget arithmetic and per-day decision counts.
Read-only over V/B and the research folder. Writes memlayer_report.json beside this script.
"""
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fit_tokens import RUNS, classes, lstsq  # noqa: E402

SYNTH = Path("/Users/atishoo/Project/WorldMock/docs/dev/research/layered-memory-2026-10-29/synth/example_fmt.json")
TZ = timezone(timedelta(hours=8))
LAYERS = ["工具层", "人设层", "记忆层", "待办层", "环境层", "进度层"]


def render(v):
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def vec(text):
    c = classes(text)
    return np.array([c["cjk"], c["alpha"], c["digit"], c["space"], c["apunct"], c["cjkpunct"] + c["otherpunct"]],
                    dtype=float)


def qwen():
    from tokenizers import Tokenizer
    path = next(Path.home().glob(".cache/huggingface/hub/models--Qwen--Qwen3-Embedding-0.6B/snapshots/*/"
                                 "tokenizer.json"))
    tok = Tokenizer.from_file(str(path))
    return lambda t: len(tok.encode(t, add_special_tokens=False).ids)


def fresh_requests(run):
    db = Path(RUNS[run]) / "run" / "footprints.sqlite"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    reqs, usage = {}, {}
    for (body,) in con.execute(
            "select body from events where json_extract(body,'$.event_type') in ('model_request','model_response')"
            " order by event_seq"):
        b = json.loads(body)
        d = b["data"]
        if b["event_type"] == "model_request":
            r = d["request"]
            if r and len(r["messages"]) == 2 and r.get("tools") and ":model:" in d["call_id"]:
                reqs[d["call_id"]] = (r, b["occurred_at"], b["decision_id"])
        else:
            u = d.get("usage") or (d.get("response") or {}).get("usage")
            if u:
                usage[d["call_id"]] = u["prompt_tokens"]
    decisions = Counter()
    for (body,) in con.execute(
            "select body from events where json_extract(body,'$.event_type')='decision_started' order by event_seq"):
        b = json.loads(body)
        decisions[datetime.fromtimestamp(b["occurred_at"], TZ).date().isoformat()] += 1
    con.close()
    out = []
    for cid, (r, at, did) in reqs.items():
        if cid in usage:
            layers = {L["layer"]: render(L) for L in json.loads(r["messages"][1]["content"])}
            out.append({"run": run, "cid": cid, "y": float(usage[cid]), "layers": layers, "at": at, "did": did})
    return out, decisions


def per_layer_fit(rows, count):
    X = [[count(r["layers"].get(name, "")) for name in LAYERS] + [1.0] for r in rows]
    y = [r["y"] for r in rows]
    res, coef = lstsq(np.array(X, dtype=float), np.array(y), [f"k_{n}" for n in LAYERS] + ["c"])
    return res


def new_layer_proxy():
    """Re-shape the synth example to the ruled design and return its parts."""
    ex = json.loads(SYNTH.read_text())
    near = {}
    for cat, days in ex["近三天"].items():
        near[cat] = [{"日期": d["日期"], "星期": d["星期"], "回顾": d["回顾"]} for d in days]
    layer = {"layer": "记忆层", "可查": ex["可查"], "近三天": near, "今天": ex["今天"]}
    return ex, layer


def entry_shell(cat="饮食"):
    """JSON overhead of one 今天 entry with one 核验 and one 自述 item, content stripped."""
    return render({"时刻": "08:36", "类别": cat, "核验": [""], "自述": [""]}) + ","


def main():
    count_q = qwen()
    rows, per_day = [], {}
    for run in ("V", "B"):
        r, days = fresh_requests(run)
        rows += r
        per_day[run] = dict(sorted(days.items()))
    report = {"fresh_requests": len(rows), "decisions_per_local_day": per_day}
    report["per_layer_qwen_fit"] = per_layer_fit(rows, count_q)
    report["per_layer_qwen_fit_V"] = per_layer_fit([r for r in rows if r["run"] == "V"], count_q)
    report["per_layer_qwen_fit_B"] = per_layer_fit([r for r in rows if r["run"] == "B"], count_q)

    coefs = {"first_diff_fresh": np.load(HERE / "coef_fd.npy"), "level_variant": np.load(HERE / "coef_level.npy")}
    k_q = report["per_layer_qwen_fit"]["coef"]["k_记忆层"]

    def estimate(text):
        v = vec(text)
        out = {"chars": len(text), "mix": dict(zip(["cjk", "alpha", "digit", "space", "apunct", "other"],
                                                    [round(float(x / max(len(text), 1)), 4) for x in v])),
               "qwen3_tokens": count_q(text)}
        for name, c in coefs.items():
            out[f"tok_{name}"] = round(float(v @ c), 1)
        out["tok_qwen_scaled"] = round(out["qwen3_tokens"] * k_q, 1)
        for key in [k for k in out if k.startswith("tok_")]:
            out[key.replace("tok_", "tpc_")] = round(out[key] / max(len(text), 1), 4)
        return out

    # current 记忆层 over V fresh requests
    cur = [r["layers"]["记忆层"] for r in rows if r["run"] == "V" and "记忆层" in r["layers"]]
    agg = "".join(cur)
    report["current_memory_layer_V"] = {"n": len(cur), "median_chars": int(np.median([len(t) for t in cur])),
                                        "max_chars": max(len(t) for t in cur), "aggregate": estimate(agg)}
    biggest = max(cur, key=len)
    report["current_memory_layer_V"]["largest"] = estimate(biggest)
    # pure CJK prose and digits sanity probes (qwen-proxy only meaningful as a ratio check)
    ex, layer = new_layer_proxy()
    today_items = layer["今天"]
    report["new_layer_proxy"] = {
        "whole": estimate(render(layer)),
        "近三天_only": estimate(render(layer["近三天"])),
        "回顾_text_only": estimate("".join(d["回顾"] for days in layer["近三天"].values() for d in days)),
        "今天_only": estimate(render(today_items)),
        "今天_content_only": estimate("".join(x for e in today_items for k in ("核验", "自述") for x in e.get(k, []))),
        "n_today_entries_in_synth": len(today_items),
    }
    shell = entry_shell()
    report["entry_shell"] = {"text": shell, **estimate(shell)}
    report["day_shell_近三天"] = {"text": render({"日期": "2026-10-09", "星期": "星期五", "回顾": ""}) + ",",
                                 **estimate(render({"日期": "2026-10-09", "星期": "星期五", "回顾": ""}) + ",")}
    (HERE / "memlayer_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())

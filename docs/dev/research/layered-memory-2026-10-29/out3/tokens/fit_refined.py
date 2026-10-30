"""Refined identification of DeepSeek tokens/char.

(a) level fit with one dummy per (system layer, tools) variant, so the unknown provider rendering of the
    tools schema and the constant system layer drop out; content features = all non-system message text.
(b) first differences between consecutive fresh decision requests of the same variant (no intercept).
(c) chain deltas: same-second continuation request minus the request whose messages are its exact prefix.
(d) proxy tokenizers: DeepSeek prompt_tokens regressed on cl100k (litellm default) and Qwen3 token counts.
Read-only over V and B. Writes refined_report.json beside this script.
"""
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fit_tokens import RUNS, classes, lstsq  # noqa: E402

import sqlite3  # noqa: E402

OUT = Path(__file__).resolve().parent
FEAT = ["cjk", "alpha", "digit", "space", "apunct", "other"]


def fine6(text):
    c = classes(text)
    return np.array([c["cjk"], c["alpha"], c["digit"], c["space"], c["apunct"], c["cjkpunct"] + c["otherpunct"]],
                    dtype=float)


def msg_text(m):
    out = []
    content = m.get("content")
    if isinstance(content, str):
        out.append(content)
    if m.get("reasoning_content"):
        out.append(m["reasoning_content"])
    for tc in m.get("tool_calls") or []:
        fn = tc.get("function", {})
        out.append((fn.get("name") or "") + (fn.get("arguments") or ""))
    return "".join(out)


def load(run_key):
    db = Path(RUNS[run_key]) / "run" / "footprints.sqlite"
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
            if u and isinstance(u.get("prompt_tokens"), int):
                usage[d["call_id"]] = u
    con.close()
    rows = []
    for cid in order:
        if cid not in usage:
            continue
        req = reqs[cid]
        ms = req["messages"]
        variant = (hashlib.sha1(ms[0]["content"].encode()).hexdigest()[:8],
                   hashlib.sha1(json.dumps(req.get("tools"), ensure_ascii=False).encode()).hexdigest()[:8])
        body_text = "".join(msg_text(m) for m in ms[1:])
        rows.append({"run": run_key, "cid": cid, "req": req, "variant": variant, "n_msgs": len(ms),
                     "x": fine6(body_text), "y": float(usage[cid]["prompt_tokens"]),
                     "hit": usage[cid].get("prompt_cache_hit_tokens"),
                     "fresh": len(ms) == 2 and bool(req.get("tools")) and ":model:" in cid,
                     "body_text": body_text})
    return rows


def level_fit(rows, with_msgs=True):
    variants = sorted({(r["run"], *r["variant"]) for r in rows})
    vidx = {v: i for i, v in enumerate(variants)}
    X, y = [], []
    for r in rows:
        dummies = [0.0] * len(variants)
        dummies[vidx[(r["run"], *r["variant"])]] = 1.0
        X.append([*r["x"], *([r["n_msgs"]] if with_msgs else []), *dummies])
        y.append(r["y"])
    names = FEAT + (["per_message"] if with_msgs else []) + [f"v{i}" for i in range(len(variants))]
    res, coef = lstsq(np.array(X), np.array(y), names)
    res["coef"] = {k: v for k, v in res["coef"].items() if not k.startswith("v")}
    return res, coef[:6]


def first_diff(rows):
    X, y = [], []
    by_run = defaultdict(list)
    for r in rows:
        if r["fresh"]:
            by_run[(r["run"], r["variant"])].append(r)
    for seq in by_run.values():
        for a, b in zip(seq, seq[1:]):
            dx = b["x"] - a["x"]
            if np.abs(dx).sum() == 0:
                continue
            X.append(dx)
            y.append(b["y"] - a["y"])
    return lstsq(np.array(X), np.array(y), FEAT)


def chain_deltas(rows):
    X, y = [], []
    hits = []
    for run in ("V", "B"):
        seq = [r for r in rows if r["run"] == run]
        for i, r in enumerate(seq):
            ms = r["req"]["messages"]
            if len(ms) <= 2:
                continue
            for p in reversed(seq[max(0, i - 3):i]):
                pm = p["req"]["messages"]
                if len(pm) < len(ms) and ms[:len(pm)] == pm and p["variant"] == r["variant"]:
                    added = ms[len(pm):]
                    dx = fine6("".join(msg_text(m) for m in added))
                    X.append([*dx, len(added)])
                    y.append(r["y"] - p["y"])
                    if r["hit"] is not None:
                        hits.append((r["hit"], p["y"]))
                    break
    res, _ = lstsq(np.array(X, dtype=float), np.array(y), FEAT + ["per_added_message"])
    hits = np.array(hits, dtype=float)
    res["cache_hit_vs_prefix_tokens"] = {
        "n": int(len(hits)),
        "median_hit_over_prefix": round(float(np.median(hits[:, 0] / hits[:, 1])), 4),
        "share_hit_within_64_below_prefix": round(float(np.mean((hits[:, 1] - hits[:, 0] >= 0)
                                                               & (hits[:, 1] - hits[:, 0] < 64))), 4)}
    return res


def proxy_fits(rows):
    import tiktoken
    enc = tiktoken.get_encoding("cl100k_base")
    qwen = None
    try:
        from tokenizers import Tokenizer
        snap = next(Path.home().glob(".cache/huggingface/hub/models--Qwen--Qwen3-Embedding-0.6B/snapshots/*/"
                                     "tokenizer.json"))
        qwen = Tokenizer.from_file(str(snap))
    except Exception as error:  # proxy is optional
        print("qwen tokenizer unavailable:", error)
    out = {}
    for name, count in [("cl100k", lambda t: len(enc.encode(t, disallowed_special=()))),
                        ("qwen3", (lambda t: len(qwen.encode(t, add_special_tokens=False).ids)) if qwen else None)]:
        if count is None:
            continue
        variants = sorted({(r["run"], *r["variant"]) for r in rows})
        vidx = {v: i for i, v in enumerate(variants)}
        X, y = [], []
        for r in rows:
            d = [0.0] * len(variants)
            d[vidx[(r["run"], *r["variant"])]] = 1.0
            X.append([count(r["body_text"]), r["n_msgs"], *d])
            y.append(r["y"])
        res, coef = lstsq(np.array(X, dtype=float), np.array(y), ["k_per_proxy_token", "per_message"]
                          + [f"v{i}" for i in range(len(variants))])
        res["coef"] = {k: v for k, v in res["coef"].items() if not k.startswith("v")}
        out[name] = res
    return out, enc, qwen


def main():
    rows = load("V") + load("B")
    report = {}
    report["a_level_variant_dummies"], coef_level = level_fit(rows)
    report["a_level_variant_dummies_V"], _ = level_fit([r for r in rows if r["run"] == "V"])
    report["a_level_variant_dummies_B"], _ = level_fit([r for r in rows if r["run"] == "B"])
    report["a_level_fresh_only"], _ = level_fit([r for r in rows if r["fresh"]], with_msgs=False)
    report["b_first_diff_fresh"], coef_fd = first_diff(rows)
    report["c_chain_deltas"] = chain_deltas(rows)
    proxies, _, _ = proxy_fits(rows)
    report["d_proxy_tokenizers"] = proxies
    (OUT / "refined_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    np.save(OUT / "coef_level.npy", coef_level)
    np.save(OUT / "coef_fd.npy", coef_fd)
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())

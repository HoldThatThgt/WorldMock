"""Uncertainty of the blended tokens/char for the new memory-layer mix.

Bootstrap (1000x) the first-difference fit over consecutive fresh decision pairs and the per-run level fits,
then evaluate tokens/char on the new-layer proxy pieces. Writes bootstrap_report.json beside this script.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fit_refined import load, level_fit  # noqa: E402
from memlayer import new_layer_proxy, render, vec, entry_shell  # noqa: E402


def pieces():
    _, layer = new_layer_proxy()
    today = layer["今天"]
    return {
        "new_layer_whole": render(layer),
        "今天_rendered": render(today),
        "今天_content": "".join(x for e in today for k in ("核验", "自述") for x in e.get(k, [])),
        "回顾_prose": "".join(d["回顾"] for days in layer["近三天"].values() for d in days),
        "entry_shell": entry_shell(),
    }


def diffs(rows):
    by = defaultdict(list)
    for r in rows:
        if r["fresh"]:
            by[(r["run"], r["variant"])].append(r)
    X, y = [], []
    for seq in by.values():
        for a, b in zip(seq, seq[1:]):
            dx = b["x"] - a["x"]
            if np.abs(dx).sum():
                X.append(dx)
                y.append(b["y"] - a["y"])
    return np.array(X), np.array(y)


def main():
    rows = load("V") + load("B")
    X, y = diffs(rows)
    P = {k: (vec(t), len(t)) for k, t in pieces().items()}
    rng = np.random.default_rng(20261029)
    boot = defaultdict(list)
    for _ in range(1000):
        idx = rng.integers(0, len(y), len(y))
        c, *_ = np.linalg.lstsq(X[idx], y[idx], rcond=None)
        for k, (v, n) in P.items():
            boot[k].append(float(v @ c) / n)
    out = {"bootstrap_first_diff": {k: {"median": round(float(np.median(v)), 4),
                                        "p2.5": round(float(np.percentile(v, 2.5)), 4),
                                        "p97.5": round(float(np.percentile(v, 97.5)), 4)} for k, v in boot.items()}}
    for run in ("V", "B"):
        _, c = level_fit([r for r in rows if r["run"] == run])
        out[f"level_{run}"] = {k: round(float(v @ c) / n, 4) for k, (v, n) in P.items()}
    out["chars"] = {k: n for k, (v, n) in P.items()}
    (HERE / "bootstrap_report.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())

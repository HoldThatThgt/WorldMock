"""Budget arithmetic for the ruled memory layer (今天 ≤120字/entry, 日回顾 ≤200字/category, 月 ≤800, 年 ≤2000).

Uses the first-difference char-class coefficients (coef_fd.npy) as central, plus cl100k (what litellm's
token_counter uses for model "deepseek-flash") for the project-estimator view. Writes budget_report.json.
"""
import json
import sys
from pathlib import Path

import numpy as np
import tiktoken

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from memlayer import new_layer_proxy, render, vec, entry_shell  # noqa: E402

C = np.load(HERE / "coef_fd.npy")
ENC = tiktoken.get_encoding("cl100k_base")


def tok(text):
    return float(vec(text) @ C)


def cl(text):
    return len(ENC.encode(text, disallowed_special=()))


def row(text):
    n = len(text)
    return {"chars": n, "deepseek_est": round(tok(text), 1), "deepseek_tpc": round(tok(text) / n, 4),
            "cl100k": cl(text), "cl100k_tpc": round(cl(text) / n, 4)}


def main():
    _, layer = new_layer_proxy()
    today = layer["今天"]
    verified = "".join(x for e in today for x in e.get("核验", []))
    selfrep = "".join(x for e in today for x in e.get("自述", []))
    review = "".join(d["回顾"] for days in layer["近三天"].values() for d in days)
    shell = entry_shell()
    shell_one_list = render({"时刻": "08:50", "类别": "饮食", "核验": [""]}) + ","
    day_shell = render({"日期": "2026-10-09", "星期": "星期五", "回顾": ""}) + ","
    work_day_shell = render({"日期": "2026-10-09", "星期": "星期五", "安排": "工作日", "回顾": ""}) + ","
    wrapper = render({"layer": "记忆层", "可查": {"最早日期": "2026-10-08"},
                      "近三天": {"饮食": [], "工作": [], "生活": []}, "今天": []})
    rep = {"pieces": {"核验_strings": row(verified), "自述_strings": row(selfrep), "回顾_prose": row(review),
                      "entry_shell_2lists": {"text": shell, **row(shell)},
                      "entry_shell_1list": {"text": shell_one_list, **row(shell_one_list)},
                      "day_shell": {"text": day_shell, **row(day_shell)},
                      "work_day_shell": {"text": work_day_shell, **row(work_day_shell)},
                      "wrapper": {"text": wrapper, **row(wrapper)},
                      "new_layer_whole": row(render(layer))}}
    tpc_content = tok(verified + selfrep) / len(verified + selfrep)
    tpc_review = tok(review) / len(review)
    entry_tokens = 120 * tpc_content + tok(shell)
    cell_chars = 200 + len(day_shell)
    near_chars = 6 * cell_chars + 3 * (200 + len(work_day_shell)) + len(wrapper)
    near_tokens = 9 * 200 * tpc_review + 6 * tok(day_shell) + 3 * tok(work_day_shell) + tok(wrapper)
    rep["caps"] = {
        "tpc_today_content": round(tpc_content, 4), "tpc_review": round(tpc_review, 4),
        "today_entry_120": {"content_tokens": round(120 * tpc_content, 1), "shell_tokens": round(tok(shell), 1),
                            "total_tokens": round(entry_tokens, 1), "rendered_chars": 120 + len(shell)},
        "day_review_200_tokens": round(200 * tpc_review, 1),
        "month_review_800_tokens": round(800 * tpc_review, 1),
        "year_review_2000_tokens": round(2000 * tpc_review, 1),
        "near3days_full_caps": {"chars_incl_wrapper": near_chars, "tokens": round(near_tokens, 1)},
    }
    for budget_tokens in (10000,):
        for ratio in (0.638, 0.655, 0.67, 0.70, 0.72):
            rep.setdefault("chars_per_10k_tokens", {})[str(ratio)] = int(budget_tokens / ratio)
    budget_chars = 14000
    rep["budget_14000"] = {
        "tokens_at": {str(r): int(budget_chars * r) for r in (0.638, 0.655, 0.67, 0.70, 0.72)},
        "cl100k_tokens_at_0.91": int(budget_chars * 0.91),
        "capped_entries_that_fit_chars": int((budget_chars - near_chars) / (120 + len(shell))),
        "capped_entries_that_fit_10k_tokens": int((10000 - near_tokens) / entry_tokens),
    }
    for n in (60, 67, 81, 89):
        chars = near_chars + n * (120 + len(shell))
        rep.setdefault("at_caps_by_entries", {})[str(n)] = {"chars": chars,
                                                           "tokens": round(near_tokens + n * entry_tokens)}
    (HERE / "budget_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    print(json.dumps(rep, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())

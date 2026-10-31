"""分层记忆试点指标（tools/analysis/memory_pilot.py）的手算答案测试。

每个用例在 PilotExportBuilder 写出的最小导出上经 load_export 读入，替身认证只记录所收目录（skip_authentication）；
真实认证由 tests/integration/evaluation 的导出测试覆盖。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.evaluation.memory_pilot_fixtures import (
    PilotExportBuilder,
    activity,
    cell,
    compact,
    entry,
    memory_layer,
    search_call,
    skip_authentication,
    tool_call,
)
from tools.analysis.behavior.metrics import digest_identity_residue, digest_repetition
from tools.analysis.errors import EvaluationError
from tools.analysis.memory_pilot import (
    decisions,
    digest_review,
    load_export,
    m1_past_reference,
    m2_dinner_naming,
    m3_past_visibility,
    m4_lunch_repeats,
    m5_searches,
    m6_volume,
    m7_cache,
    m8_cost,
    m9_guardrails,
    m10_digests,
    m11_digest_fidelity,
    m12_endorsed_repurchase,
    main,
    pilot_checks,
    pilot_report,
)
from worldmock.foundation.errors import IntegrityError

REPO_ROOT = Path(__file__).resolve().parents[3]
CHESTNUT = "板栗烧鸡配清炒娃娃菜一人份食材包"
WINGS = "可乐鸡翅配清炒菌菇一人份食材包"
GUARDS = ["response", "truncated", "schema", "text_without_input", "null_with_input", "text_empty", "text_length",
          "refs_empty", "refs_category", "unrecognized"]
CHECK_NAMES = ["cooking_lines_name_the_kit", "yesterday_food_cell_visible", "search_validation_errors",
               "search_non_empty", "search_is_read_only", "memory_layer_budget", "progress_layer_median",
               "progress_layer_p90", "user_message_median", "persona_layer_stable", "fresh_turn_cache_hit",
               "cost_per_day", "decisions_per_day_band", "rejections", "turns_exhausted", "run_failures",
               "capacity_compressions", "no_restarted_activities", "chain_append_only", "chain_without_persona_delta",
               "digest_fallback_share", "one_day_digest_per_day", "retired_calls", "month_digests"]
SECTION_KEYS = {
    "M1": ["by_day", "periods", "decisions"],
    "M2": ["named", "mismatches", "activities"],
    "M3": ["by_day", "periods", "unsorted"],
    "M4": ["workday_lunch_portions", "repeated_portions", "value", "most_frequent", "lunches"],
    "M5": ["calls", "today_calls", "by_day", "succeeded", "non_empty", "validation_errors", "error_codes",
           "limit_refusals", "capped_results", "unanswered", "non_model_events_between"],
    "M6": ["layers", "memory_layer_by_observation", "fallback_rewrites", "persona_layer_identical", "token_ratio"],
    "M7": ["fresh_decision_turn", "by_call_class"],
    "M8": ["days", "per_day_usd", "by_call_class", "complete"],
    "M9": ["decisions_per_day", "decision_band", "days_outside_band", "rejections", "rejection_limit", "turns",
           "run_failures", "capacity_compressions", "chain", "restarted"],
    "M10": ["calls_by_day", "periods", "expected", "retries", "fallback", "guard_discards", "cited",
            "view_coverage", "statistics_copy_violations", "day_digest_problems", "missing_day_digests",
            "duplicate_digests", "retired_calls", "repetition", "identity_residue"],
    "M11": ["coverage", "food_fallback", "coverage_plus_fallback", "cooking_mismatches", "meal_mismatches",
            "foreign_dishes", "days"],
    "M12": ["purchases", "endorsed", "on_card", "not_on_card", "age", "rows", "earlier_view_reliance"],
}
PERSONA = {"layer": "人设层", "身份": "李凡，后端工程师"}


def _ratio(numerator, denominator, value):
    """期望的比例对象。"""
    return {"numerator": numerator, "denominator": denominator, "value": value}


def _load(builder):
    """写出导出并读入，返回（导出，决策）。"""
    export = load_export(builder.build())
    return export, decisions(export)


def _day_payload(food=(), work=(), life=()):
    """日回顾的输入：人物卡片与三类条目行。"""
    return {"人物卡片": "李凡，32 岁。", "饮食": list(food), "工作": list(work), "生活": list(life)}


def _period_payload(rows_key, food=((), ()), work=((), ()), life=((), ())):
    """月、年回顾的输入：每类（回顾行，次数统计行）。"""
    return {name: {rows_key: list(rows), "次数统计": list(lines)}
            for name, (rows, lines) in (("饮食", food), ("工作", work), ("生活", life))}


def _query(activity_id, *targets, app_id="meituan"):
    """查询活动的受理快照。"""
    return activity(activity_id, "query", action="query", app_id=app_id, targets=list(targets))


def _stats(values):
    """手算的两个值的统计量。"""
    low, high = sorted(values)
    return {"n": 2, "median": round((low + high) / 2, 1), "p90": round(low + 0.9 * (high - low), 1),
            "max": float(high)}


def _classes(text):
    """测试自己的六类字符计数：汉字、ASCII 字母、数字、空格、其余可打印 ASCII、其余字符。"""
    han = sum("㐀" <= ch <= "䶿" or "一" <= ch <= "鿿" or "豈" <= ch <= "﫿" for ch in text)
    letters, digits = len(re.findall(r"[A-Za-z]", text)), len(re.findall(r"[0-9]", text))
    spaces = text.count(" ")
    others = len(re.findall(r"[!-~]", text)) - letters - digits
    return [han, letters, digits, spaces, others, len(text) - han - letters - digits - spaces - others]


# ---- 输入与命令行 ----

def test_load_export_authenticates_before_reading_any_file(tmp_path, monkeypatch):
    path = PilotExportBuilder(tmp_path).build()
    # 五样都删掉：认证之前读任何一样都会先报文件不存在。
    for name in ("manifest.json", "events.jsonl", "purchases.jsonl", "quality.json"):
        (path / name).unlink()
    shutil.rmtree(path / "assets")
    seen = []
    refusal = IntegrityError("Export events digest disagrees with its manifest")

    def refuse(directory):
        seen.append(Path(directory))
        raise refusal

    monkeypatch.setattr("tools.analysis.memory_pilot.validate_export", refuse)
    with pytest.raises(IntegrityError) as caught:
        load_export(path)
    assert caught.value is refusal
    assert seen == [path]


def test_pilot_report_refuses_a_multi_person_export(tmp_path, skip_authentication):
    path = PilotExportBuilder(tmp_path, agents=2).build()
    with pytest.raises(EvaluationError, match="^memory_pilot reads single-person exports; the export has 2 agents$") \
            as caught:
        pilot_report(path)
    assert caught.value.code == "memory_pilot_input"
    assert skip_authentication == [path]


def test_pilot_report_refuses_a_frozen_catalog_file_missing_from_the_manifest(tmp_path, skip_authentication):
    for name in ("catalog/merchant-catalog.jsonl", "catalog/content-catalog.jsonl", "catalog/app-catalog.jsonl",
                 "catalog/location.jsonl"):
        builder = PilotExportBuilder(tmp_path / "missing" / name.removeprefix("catalog/"))
        builder.omitted.add(name)
        with pytest.raises(EvaluationError,
                           match=f"^Frozen environment asset {re.escape(name)} is missing from the export$"):
            pilot_report(builder.build())
    full = load_export(PilotExportBuilder(tmp_path / "full").build())
    # laundry 只有不带饮食效果的内容凭它，不是食品状态；pantry 是食品状态但没有重置商品，不凭食材包。
    assert (full.food_states, full.kit_states) == (frozenset({"groceries", "pantry"}), frozenset({"groceries"}))
    assert full.kits == frozenset({"kit-chestnut", "kit-wings"})
    optional = PilotExportBuilder(tmp_path / "optional")
    optional.omitted.add("catalog/callbacks.jsonl")
    without = load_export(optional.build())
    assert (without.food_states, without.kits) == (frozenset(), frozenset())


def _small_builder(root):
    """一个只有一次决策的导出。"""
    builder = PilotExportBuilder(root)
    decision = builder.decision(1, "08:00")
    builder.turn(decision, builder.at(1, "08:00"), layers=[PERSONA, memory_layer()], reasoning="起床")
    return builder


def test_cli_writes_the_report_and_refuses_an_existing_output(tmp_path, skip_authentication, capsys, caplog):
    path = _small_builder(tmp_path).build()
    assert main(["--export", str(path)]) == 0
    printed = capsys.readouterr().out
    assert printed == json.dumps(pilot_report(path), ensure_ascii=False, indent=2) + "\n"
    output = tmp_path / "report.json"
    assert main(["--export", str(path), "--output", str(output)]) == 0
    assert capsys.readouterr().out == ""
    assert output.read_text(encoding="utf-8") == printed
    caplog.set_level(logging.ERROR)
    caplog.clear()
    assert main(["--export", str(path), "--output", str(output)]) == 1
    assert caplog.messages == [f"memory_pilot_input: Output file already exists: {output}"]
    assert output.read_text(encoding="utf-8") == printed


def test_cli_logs_the_input_error_and_returns_one(tmp_path, skip_authentication, caplog, monkeypatch):
    caplog.set_level(logging.ERROR)
    path = PilotExportBuilder(tmp_path / "pair", agents=2).build()
    output = tmp_path / "report.json"
    assert main(["--export", str(path), "--output", str(output)]) == 1
    # 只有异常构造时写的那一条错误日志，命令行入口不另写。
    assert caplog.messages == ["memory_pilot_input: memory_pilot reads single-person exports; the export has 2 agents"]
    assert not output.exists()

    def refuse(directory):
        raise IntegrityError("Export manifest identity is invalid")

    monkeypatch.setattr("tools.analysis.memory_pilot.validate_export", refuse)
    single = _small_builder(tmp_path / "single").build()
    caplog.clear()
    assert main(["--export", str(single)]) == 1
    assert caplog.messages == ["WorldMock failure: Export manifest identity is invalid"]


def test_memory_pilot_imports_no_runtime_code():
    code = ("import sys\nimport tools.analysis.memory_pilot\n"
            "prefixes = ('worldmock.simulation_engine', 'worldmock.agents', 'worldmock.runtime', 'custom')\n"
            "print(sorted(name for name in sys.modules if name.startswith(prefixes)))")
    completed = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True,
                               check=True)
    assert completed.stdout.strip() == "[]"


def test_report_has_every_metric_section_with_its_keys(tmp_path, skip_authentication):
    path = _small_builder(tmp_path).build()
    report = pilot_report(path)
    assert list(report) == ["schema_version", "run_id", "window", "agent_id", "days", "metrics", "checks",
                            "digest_review"]
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    assert (report["schema_version"], report["run_id"], report["agent_id"]) == ("memory-pilot-1", "run-pilot", 1)
    assert report["window"] == manifest["window"]
    assert report["days"] == ["2026-10-08", "2026-10-09", "2026-10-10"]
    assert list(report["metrics"]) == [f"M{number}" for number in range(1, 13)]
    for name, keys in SECTION_KEYS.items():
        assert list(report["metrics"][name]) == keys, name
    metrics = report["metrics"]
    assert list(metrics["M1"]["periods"]) == ["all", "days_1_3", "days_4_plus"]
    assert list(metrics["M6"]["token_ratio"]) == ["pairs", "weights", "memory_layer_tokens_per_char", "reference",
                                                  "reference_range", "within_range"]
    assert list(metrics["M9"]["turns"]) == ["max_react_turns", "p99", "max", "exhausted", "six_or_more"]
    assert list(metrics["M9"]["chain"]) == ["same_second_followups", "continuations", "fresh_followups",
                                            "append_violations", "persona_deltas"]
    assert list(metrics["M10"]["guard_discards"]) == GUARDS
    assert list(metrics["M10"]["fallback"]) == ["numerator", "denominator", "value", "cells"]
    assert list(metrics["M12"]["earlier_view_reliance"]) == ["decisions", "relying", "value", "rows"]
    assert [check["name"] for check in report["checks"]] == CHECK_NAMES
    assert all(list(check) == ["name", "path", "comparison", "threshold", "value", "pass"]
               for check in report["checks"])
    assert report["digest_review"] == []
    # 时区、人物编号与运行日都取导出清单：时区 UTC、人物 7（回顾请求的 call_id 带它）；运行在第三天之后的零点结束，
    # window.end 的本地日期不是运行日。UTC 23:30 是上海次日 07:30。
    other = PilotExportBuilder(tmp_path / "utc", timezone="UTC", agent_id=7)
    decision = other.decision(1, "23:30")
    other.turn(decision, other.at(1, "23:30"), layers=[PERSONA, memory_layer()], reasoning="睡觉")
    other.digest_request("day", other.iso(1), other.at(2, "00:00"),
                         _day_payload(food=[{"编号": "n1", "时刻": "12:00", "核验": ["吃了面"]}]))
    other.digest("day", other.iso(1), other.at(2, "00:00"), {"food": "中午吃了面。"})
    other.window_end = other.at(3, "00:00") + 86_400
    report = pilot_report(other.build())
    assert (report["agent_id"], report["window"]["end"]) == (7, "2026-10-11T00:00:00+00:00")
    assert report["days"] == ["2026-10-08", "2026-10-09", "2026-10-10"]
    assert report["metrics"]["M9"]["decisions_per_day"] == {"2026-10-08": 1, "2026-10-09": 0, "2026-10-10": 0}
    assert report["digest_review"][0] == {"date": "2026-10-08", "category": "饮食", "digest": "中午吃了面。",
                                          "status": "written", "verified": ["吃了面"]}


def test_report_is_byte_identical_on_two_computations(tmp_path, skip_authentication, capsys):
    builder = PilotExportBuilder(tmp_path)
    builder.kit_arrives("kit-chestnut", builder.at(1, "08:00"))
    first = builder.decision(1, "12:00", memory=memory_layer([entry("08:00", "生活", ["起床"])]))
    builder.search(first, builder.at(1, "12:00"), {"category": "全部", "time": "今天", "keyword": None},
                   {"类别": "全部", "时间": "今天", "关键词": None, "结果": [entry("08:00", "生活", ["起床"])],
                    "未列出": []})
    builder.purchase("noodle", builder.at(1, "12:00"))
    # 往日日期取自集合：三类的格日期次序相反，报告仍按升序写出，与散列种子无关。
    recent = {"饮食": [cell("2026-10-07", "中午吃了番茄牛肉刀削面。")], "工作": [cell("2026-10-06", "开会。")],
              "生活": [cell("2026-10-05", "散步。")]}
    cooking = builder.decision(1, "18:00", memory=memory_layer(recent=recent))
    builder.turn(cooking, builder.at(1, "18:00"), layers=[PERSONA, memory_layer()], reasoning="做饭")
    builder.cook(cooking, builder.at(1, "18:00"), "a-cook")
    builder.digest_request("day", "2026-10-08", builder.at(2, "00:00"),
                           _day_payload(food=[{"编号": "n1", "时刻": "12:00", "核验": ["买番茄牛肉刀削面1份"]}]))
    builder.digest("day", "2026-10-08", builder.at(2, "00:00"), {"food": "中午吃了番茄牛肉刀削面。"})
    builder.usage("fresh_decision_turn", hit=10, miss=30, out=5, request_events=2)
    path = builder.build()
    texts = [json.dumps(pilot_report(path), ensure_ascii=False, indent=2) for _ in range(2)]
    assert texts[0] == texts[1]
    assert main(["--export", str(path)]) == 0 and main(["--export", str(path)]) == 0
    printed = capsys.readouterr().out
    assert printed == (texts[0] + "\n") * 2
    # 集合次序随散列种子变：在两个散列种子不同的进程里各算一次，序列化字节相同（最小形状通不过真实认证，同样换掉认证）。
    code = ("import json, sys\nimport tools.analysis.memory_pilot as pilot\n"
            "pilot.validate_export = lambda directory: None\n"
            "sys.stdout.buffer.write((json.dumps(pilot.pilot_report(sys.argv[1]), ensure_ascii=False, indent=2) + "
            "'\\n').encode('utf-8'))")
    outputs = [subprocess.run([sys.executable, "-c", code, str(path)], cwd=REPO_ROOT, capture_output=True, check=True,
                              env={**os.environ, "PYTHONHASHSEED": seed}).stdout for seed in ("1", "2")]
    assert outputs == [(texts[0] + "\n").encode("utf-8")] * 2
    assert json.loads(texts[0])["metrics"]["M1"]["decisions"][0]["past_dates"] == ["2026-10-05", "2026-10-06",
                                                                                 "2026-10-07"]


def test_ratios_split_first_three_days_from_day_four_on(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path, days=5)
    for day in range(1, 5):
        builder.digest("day", builder.iso(day), builder.at(day + 1, "00:00"), {"food": f"第{day}天吃了面。"})
    seen = [(1, None), (2, cell(builder.iso(1), "第1天吃了面。")), (3, None), (4, cell(builder.iso(3), "第3天吃了面。")),
            (5, cell(builder.iso(4), "第4天吃了面。")), (5, None)]
    for day, food in seen:
        recent = {"饮食": [food] if food else [], "工作": [], "生活": []}
        builder.observation(builder.at(day, "09:00"), memory=memory_layer(recent=recent))
    metric = m3_past_visibility(load_export(builder.build()))
    assert metric["periods"] == {"all": _ratio(3, 5, 0.6), "days_1_3": _ratio(1, 2, 0.5),
                                 "days_4_plus": _ratio(2, 3, 0.6667)}
    assert metric["by_day"] == {"2026-10-08": _ratio(0, 0, None), "2026-10-09": _ratio(1, 1, 1.0),
                                "2026-10-10": _ratio(0, 1, 0.0), "2026-10-11": _ratio(1, 1, 1.0),
                                "2026-10-12": _ratio(1, 2, 0.5)}


# ---- M1 ----

def test_m1_counts_lunch_and_dinner_food_decisions_and_any_cooking_start(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path, days=1)
    meal = {"kind": "category", "category": "meal"}

    def started(name, clock, *snapshots):
        decision = builder.decision(1, clock, decision_id=name)
        builder.world(decision, builder.at(1, clock), "start_main_activity", activities=snapshots)

    def order(goods_id, clock):
        return builder.purchase(goods_id, builder.at(1, clock))

    builder.kit_arrives("kit-chestnut", builder.at(1, "07:00"))
    cooking = builder.decision(1, "08:00", decision_id="d-cook-morning")
    builder.cook(cooking, builder.at(1, "08:00"), "a-cook-1")
    started("d-breakfast-query", "09:00", _query("q-9", meal))
    # 时段左闭右开：10:30 与 16:30 落在午饭、晚饭里，14:30 与 22:00 不落在任何一顿里。
    started("d-lunch-opens", "10:30", _query("q-1030", meal))
    started("d-lunch-query", "12:00", _query("q-12", meal))
    started("d-goods-query", "12:05", _query("q-goods", {"kind": "category", "category": "goods"},
                                             {"kind": "goods", "goods_id": "tissue"}))
    started("d-kit-purchase", "12:30", activity("p-kit", "purchase", order_ids=[order("kit-wings", "12:30")]))
    # 午饭时段里只买了日用品不是饮食决策。
    started("d-tissue-purchase", "12:50", activity("p-tissue", "purchase", order_ids=[order("tissue", "12:50")]))
    started("d-drink-query", "12:40", _query("q-drink", {"kind": "category", "category": "drink"}))
    started("d-lunch-closed", "14:30", _query("q-1430", meal))
    started("d-afternoon-purchase", "15:00", activity("p-15", "purchase", order_ids=[order("noodle", "15:00")]))
    started("d-dinner-opens", "16:30", _query("q-1630", meal))
    started("d-dinner-purchase", "18:00", activity("p-18", "purchase", order_ids=[order("noodle", "18:00")]))
    started("d-dinner-pair", "18:30", _query("q-coffee", {"kind": "goods", "goods_id": "coffee"}),
            activity("p-chips", "purchase", order_ids=[order("chips", "18:30")]))
    started("d-kit-query", "18:40", _query("q-kit", {"kind": "goods", "goods_id": "kit-wings"}))
    started("d-office-and-app", "19:00", activity("i-office", "interact", content_id="office-lunch"),
            activity("i-drama", "interact", content_id="cook-1", app_id="bilibili"))
    started("d-second-episode", "21:00", activity("i-cook-2", "interact", content_id="cook-2"))
    # 凭没有重置商品的食品状态的内容、凭非食品状态的内容都不是做饭。
    started("d-pantry-snack", "21:30", activity("i-pantry", "interact", content_id="pantry-snack"))
    started("d-laundry", "21:40", activity("i-laundry", "interact", content_id="laundry"))
    started("d-late-dinner", "21:50", activity("p-2150", "purchase", order_ids=[order("noodle", "21:50")]))
    started("d-night-purchase", "22:00", activity("p-22", "purchase", order_ids=[order("noodle", "22:00")]))
    metric = m1_past_reference(*_load(builder))
    assert [(item["decision_id"], item["kinds"]) for item in metric["decisions"]] == [
        ("d-cook-morning", ["cooking"]), ("d-lunch-opens", ["query"]), ("d-lunch-query", ["query"]),
        ("d-kit-purchase", ["purchase"]), ("d-drink-query", ["query"]), ("d-dinner-opens", ["query"]),
        ("d-dinner-purchase", ["purchase"]), ("d-dinner-pair", ["query", "purchase"]), ("d-kit-query", ["query"]),
        ("d-second-episode", ["cooking"]), ("d-late-dinner", ["purchase"])]
    assert metric["by_day"]["2026-10-08"] == _ratio(0, 11, 0.0)


def test_m1_counts_a_past_dish_or_date_from_recent_days_or_search_results(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path, days=4, start="2026-10-05")
    meal = {"kind": "category", "category": "meal"}
    noodle_cell = {"饮食": [cell("2026-10-07", "中午吃了番茄牛肉刀削面。")], "工作": [], "生活": []}

    def lunch(name, clock, recent=None, reasoning=None, answer=None, summary=None):
        decision = builder.decision(4, clock, memory=memory_layer(recent=recent), decision_id=name)
        if answer is not None:
            builder.search(decision, builder.at(4, clock), {"category": "饮食", "time": "2026-10-06"}, answer,
                           call_id=f"call-{name}")
        builder.turn(decision, builder.at(4, clock), reasoning=reasoning)
        builder.world(decision, builder.at(4, clock), "start_main_activity", activities=[_query(f"q-{name}", meal)])
        if summary is not None:
            builder.world(decision, builder.at(4, clock), "report_status", {"items": [{"summary": summary}]})

    # 思考里的 U+2028 原样写进 events.jsonl；读入按字节切行（与认证相同），它不切开这一行。
    lunch("d-name", "12:00", noodle_cell, reasoning="还想吃\u2028番茄牛肉刀削面")
    lunch("d-date", "12:05", noodle_cell, reasoning="10月7日那家不错")
    lunch("d-search", "12:10", answer={"结果": [{"日期": "2026-10-06", "类别": "饮食", "回顾": "晚上做了可乐鸡翅。"}]},
          summary="晚饭再做可乐鸡翅")
    lunch("d-unsorted", "12:15", reasoning="那天是10-05吧", answer={"结果": {"饮食": [
        {"日期": "2026-10-05", "类别": "饮食", "未整理": True,
         "条目": [{"时刻": "12:00", "类别": "饮食", "核验": ["买薯片1份，8元"]}]}]}})
    lunch("d-silent", "12:20", {"饮食": [cell("2026-10-07", "喝了美式咖啡。")], "工作": [],
                                "生活": [cell("2026-10-06", unsorted=True)]}, reasoning="随便吃点")
    # 往日文字取「近三天」各类的格，生活格里的菜名同样算。
    lunch("d-life", "12:22", {"饮食": [], "工作": [], "生活": [cell("2026-10-07", "晚上喝了美式咖啡。")]},
          reasoning="昨晚的美式咖啡不错")
    lunch("d-month", "12:25", reasoning="土豆牛肉饭也行",
          answer={"结果": [{"月份": "2026-09", "类别": "饮食", "回顾": "九月常吃土豆牛肉饭。"}]})
    lunch("d-year", "12:30", reasoning="还是喝美式咖啡吧",
          answer={"结果": [{"年份": "2025", "类别": "饮食", "回顾": "去年常喝美式咖啡。"}]})
    # 三类的格按类别次序给出、日期次序相反，往日日期仍升序列出。
    lunch("d-three-days", "12:35", {"饮食": [cell("2026-10-07", "中午吃了番茄牛肉刀削面。")],
                                    "工作": [cell("2026-10-06", "开了一天会。")], "生活": [cell("2026-10-05", "晚上散步。")]},
          reasoning="10月5日散步以后吃的什么")
    # 月份不补零：九月的日期写作「9月30日」。
    lunch("d-september", "12:40", reasoning="9月30日那家还行",
          answer={"结果": [{"日期": "2026-09-30", "类别": "饮食", "回顾": "晚上吃了薯片。"}]})
    metric = m1_past_reference(*_load(builder))
    assert [(item["decision_id"], item["past_names"], item["past_dates"], item["mentioned"])
            for item in metric["decisions"]] == [
        ("d-name", ["番茄牛肉刀削面"], ["2026-10-07"], True),
        ("d-date", ["番茄牛肉刀削面"], ["2026-10-07"], True),
        ("d-search", ["可乐鸡翅"], ["2026-10-06"], True),
        ("d-unsorted", ["薯片"], ["2026-10-05"], True),
        ("d-silent", ["美式咖啡"], ["2026-10-07"], False),
        ("d-life", ["美式咖啡"], ["2026-10-07"], True),
        ("d-month", ["土豆牛肉饭"], [], True),
        ("d-year", ["美式咖啡"], [], True),
        ("d-three-days", ["番茄牛肉刀削面"], ["2026-10-05", "2026-10-06", "2026-10-07"], True),
        ("d-september", ["薯片"], ["2026-09-30"], True)]
    assert metric["decisions"][0]["time"] == "2026-10-08 12:00"
    assert metric["by_day"]["2026-10-08"] == _ratio(9, 10, 0.9)
    assert metric["periods"]["days_4_plus"] == _ratio(9, 10, 0.9)


def test_m1_ignores_names_only_in_today_or_in_the_request_target(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path, days=1)
    meal = {"kind": "goods", "goods_id": "noodle"}
    # 往日名称只写在正文与请求参数里、不在思考里时不算引用。
    noodle = {"饮食": [cell("2026-10-07", "中午吃了番茄牛肉刀削面。")], "工作": [], "生活": []}
    today = memory_layer([entry("08:00", "饮食", ["买番茄牛肉刀削面1份"])], recent=noodle)
    first = builder.decision(1, "12:00", memory=today)
    builder.turn(first, builder.at(1, "12:00"), reasoning="吃什么好呢", content="我要番茄牛肉刀削面",
                 tool_calls=[tool_call("c1", "start_main_activity", {"targets": ["番茄牛肉刀削面"]})])
    builder.world(first, builder.at(1, "12:00"), "start_main_activity", activities=[_query("q-1", meal)])
    recent = {"饮食": [cell("2026-10-07", "喝了美式咖啡。")], "工作": [], "生活": []}
    second = builder.decision(1, "12:30", memory=memory_layer([entry("12:00", "饮食", ["查番茄牛肉刀削面"])],
                                                             recent=recent))
    builder.turn(second, builder.at(1, "12:30"), reasoning="早上的番茄牛肉刀削面不错")
    builder.world(second, builder.at(1, "12:30"), "start_main_activity", activities=[_query("q-2", meal)])
    metric = m1_past_reference(*_load(builder))
    assert [(item["past_names"], item["mentioned"]) for item in metric["decisions"]] == [
        (["番茄牛肉刀削面"], False), (["美式咖啡"], False)]


# ---- M2 ----

def _cooking_day(builder, day, activity_id, line):
    """一天的做饭：18:00 开始，时钟推进到 19:00 自然结束，19:00 与 19:30 各一次决策，后者的「今天」带结束核验。"""
    start = builder.decision(day, "18:00")
    snapshot = builder.cook(start, builder.at(day, "18:00"), activity_id)
    builder.progress(snapshot, builder.at(day, "19:00"))
    later = builder.decision(day, "19:00")
    builder.world(later, builder.at(day, "19:00"), "report_status", {"items": []})
    builder.decision(day, "19:30", memory=memory_layer([entry("18:00", "饮食", [line])]))


def test_m2_names_the_kit_and_its_received_date_in_the_cooking_line(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path)
    builder.kit_arrives("kit-chestnut", builder.at(1, "20:00"))
    _cooking_day(builder, 2, "a-chestnut", f"备菜做饭 18:00–19:00，用10月8日收到的{CHESTNUT}")
    builder.kit_arrives("kit-wings", builder.at(2, "20:00"))
    _cooking_day(builder, 3, "a-wings", "备菜做饭 18:00–19:00，用家里原有的食材")
    metric = m2_dinner_naming(*_load(builder))
    assert metric["named"] == _ratio(1, 2, 0.5)
    assert metric["activities"] == [
        {"activity_id": "a-chestnut", "name": "a-chestnut做饭", "ended_at": "2026-10-09 19:00", "kit": CHESTNUT,
         "received_on": "2026-10-08", "line_named": True, "mismatched_dishes": []},
        {"activity_id": "a-wings", "name": "a-wings做饭", "ended_at": "2026-10-10 19:00", "kit": WINGS,
         "received_on": "2026-10-09", "line_named": False, "mismatched_dishes": []}]
    # 月日都不补零：九月收到的食材包写作「9月29日」。
    september = PilotExportBuilder(tmp_path / "september", start="2026-09-29")
    september.kit_arrives("kit-chestnut", september.at(1, "20:00"))
    _cooking_day(september, 2, "a-september", f"备菜做饭 18:00–19:00，用9月29日收到的{CHESTNUT}")
    metric = m2_dinner_naming(*_load(september))
    assert [(item["received_on"], item["line_named"]) for item in metric["activities"]] == [("2026-09-29", True)]


def test_m2_counts_cooking_whose_line_is_visible_to_a_later_decision(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path, days=6)
    line = memory_layer([entry("17:00", "饮食", [f"备菜做饭 17:00–18:00，用10月8日收到的{CHESTNUT}"])])
    home = builder.decision(1, "06:00")
    builder.progress(builder.cook(home, builder.at(1, "06:00"), "a-home"), builder.at(1, "06:30"))
    for clock in ("06:40", "06:50"):
        builder.world(builder.decision(1, clock), builder.at(1, clock), "report_status", {"items": []})
    builder.kit_arrives("kit-chestnut", builder.at(1, "08:00"))
    # 进入 cancelled 与进入 completed 一样是结束；每次结束同一提交里另有给开始请求的终态结果，它带开始那次决策的身份，
    # 不改变那次决策的 C_j（C_j 只从它最早的结果算起）。
    for day, activity_id, later, status in ((2, "a-one-after", 1, "completed"), (3, "a-two-after", 2, "cancelled")):
        start = builder.decision(day, "17:00")
        builder.progress(builder.cook(start, builder.at(day, "17:00"), activity_id), builder.at(day, "18:00"), status)
        for index in range(later):
            clock = f"18:{index + 1}0"
            builder.world(builder.decision(day, clock, memory=line), builder.at(day, clock), "report_status",
                          {"items": []})
    # 收尾在决策自己的提交里取消做饭：结束事件是带取消快照的 activity_concluded 环境事件，没有 activity_progress。
    for day, activity_id, after in ((4, "a-concluded", True), (5, "a-last", False)):
        builder.cook(builder.decision(day, "17:00"), builder.at(day, "17:00"), activity_id)
        builder.conclude(builder.decision(day, "18:00"), builder.at(day, "18:00"), [{"summary": "做完了"}],
                         [activity_id])
        if after:
            builder.decision(day, "18:10", memory=line)
    # 时钟推进里结束、待收尾的做饭到当天最后一次决策才收尾：那次收尾另写的 activity_concluded 带同一终态快照，结束事件
    # 仍是更早的 activity_progress。同时结束的另一项活动先在 18:10 收尾，这次决策是 k，k 之后还有 18:20 的收尾，进分母。
    start = builder.decision(6, "17:00")
    cooking = builder.cook(start, builder.at(6, "17:00"), "a-wrapped-last")
    drama = activity("a-drama", "interact", content_id="drama-1", app_id="bilibili")
    builder.world(start, builder.at(6, "17:00"), "start_main_activity", activities=[drama])
    builder.progress(cooking, builder.at(6, "18:00"))
    builder.progress(drama, builder.at(6, "18:00"))
    for clock, activity_id in (("18:10", "a-drama"), ("18:20", "a-wrapped-last")):
        builder.conclude(builder.decision(6, clock, memory=line), builder.at(6, clock), [{"summary": "做完了"}],
                         [activity_id])
    metric = m2_dinner_naming(*_load(builder))
    assert [(item["activity_id"], item["kit"], item["line_named"]) for item in metric["activities"]] == [
        ("a-home", "家里原有的食材", None), ("a-one-after", CHESTNUT, None), ("a-two-after", CHESTNUT, True),
        ("a-concluded", CHESTNUT, True), ("a-last", CHESTNUT, None), ("a-wrapped-last", CHESTNUT, True)]
    assert metric["activities"][-1]["ended_at"] == "2026-10-13 18:00"
    assert metric["named"] == _ratio(3, 3, 1.0)


def test_m2_counts_dishes_in_cooking_self_reports_outside_the_used_kit(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path, days=1)
    # 菜名词表的另外几种来源：不带饮食效果、凭食品状态的内容也是食品内容，摘要只取第一个「比如／例如」之后、第一个
    # 「，」「。」「；」「…」之前，按「、」「和」「或」切开（「蛋炒饭」在第二个「比如」之后，不进词表）；名字只以「食材包」
    # 结尾的食材包去掉「食材包」再按「配」切开。
    builder.catalog["catalog/content-catalog.jsonl"].extend([
        {"consumes_state": "groceries", "content_id": "cook-3", "location_kinds": ["home"],
         "summary": "例如鱼香肉丝和宫保鸡丁或麻婆豆腐；比如蛋炒饭…", "title": "炒菜"},
        {"completion_effects": {"food": "snack"}, "content_id": "nibble", "location_kinds": ["home"],
         "summary": "比如牛肉干…或瓜子。", "title": "吃点零嘴"}])
    builder.catalog["catalog/merchant-catalog.jsonl"].append(
        {"category": "goods", "goods_id": "kit-pork", "name": "红烧肉配米饭食材包", "price_fen": 3200})
    builder.catalog["catalog/callbacks.jsonl"][0]["reset_on_goods"].append("kit-pork")
    home = builder.decision(1, "07:00")
    builder.cook(home, builder.at(1, "07:00"), "a-home")
    builder.conclude(home, builder.at(1, "07:30"), [{"summary": "做了番茄炒蛋"}], ["a-home"])
    builder.kit_arrives("kit-chestnut", builder.at(1, "08:00"))
    lunch = builder.decision(1, "12:00")
    builder.cook(lunch, builder.at(1, "12:00"), "a-lunch")
    # 「盒饭」只出现在非食品内容的摘要里，不进菜名词表；「土豆牛肉饭」只写在记忆里，同样是自述。第 i 项只归结果里的
    # 第 i 项活动：第二项写的番茄牛肉刀削面是 a-side 的自述，不是 a-lunch 的。收尾结束了进行中的 a-lunch，同一提交里
    # 另有开始请求的终态结果，它也链到这次收尾但不带 activity_ids：活动归属只看收尾自己的（序号最小的）结果。
    builder.conclude(builder.decision(1, "13:00"), builder.at(1, "13:00"), [
        {"summary": "做了番茄炒蛋，还有可乐鸡翅。又炒了鱼香肉丝、宫保鸡丁和麻婆豆腐，配了红烧肉和米饭，还吃了蛋炒饭和牛肉干。",
         "views": [{"about": "咖啡", "type": "preference", "text": "美式咖啡配饭不错"}],
         "memories": [{"type": "routine", "text": "中午还热了公司的盒饭和土豆牛肉饭", "importance": 0.3,
                       "keywords": []}]},
        {"summary": "外卖的番茄牛肉刀削面也吃完了"}], ["a-lunch", "a-side"])
    # 凭没有重置商品的食品状态、凭非食品状态的 consume_on_start 都不是做饭活动。
    snack = builder.decision(1, "15:00")
    builder.cook(snack, builder.at(1, "15:00"), "a-snack", content_id="pantry-snack", state="pantry")
    builder.cook(snack, builder.at(1, "15:00"), "a-laundry", content_id="laundry", state="laundry")
    dinner = builder.decision(1, "18:00")
    builder.cook(dinner, builder.at(1, "18:00"), "a-dinner")
    builder.conclude(builder.decision(1, "19:00"), builder.at(1, "19:00"),
                     [{"summary": "那份板栗烧鸡配清炒娃娃菜食材包留着做晚饭"}], ["a-dinner"])
    builder.conclude(builder.decision(1, "19:10"), builder.at(1, "19:10"), [{"summary": "还吃了薯片"}],
                     ["a-dinner"], status="failed")
    # 暂停与播报同样是自述的来源。
    builder.cook(builder.decision(1, "20:00"), builder.at(1, "20:00"), "a-supper")
    builder.world(builder.decision(1, "20:30"), builder.at(1, "20:30"), "pause_activity",
                  {"items": [{"summary": "先停一下，热了一份土豆牛肉饭"}]}, activity_ids=["a-supper"])
    builder.world(builder.decision(1, "20:40"), builder.at(1, "20:40"), "report_status",
                  {"items": [{"summary": "边做边喝了美式咖啡"}]}, activity_ids=["a-supper"])
    metric = m2_dinner_naming(*_load(builder))
    assert [(item["activity_id"], item["mismatched_dishes"]) for item in metric["activities"]] == [
        ("a-home", []), ("a-lunch", ["番茄炒蛋", "可乐鸡翅", "鱼香肉丝", "宫保鸡丁", "麻婆豆腐", "红烧肉", "米饭", "牛肉干",
                                     "美式咖啡", "土豆牛肉饭"]), ("a-dinner", []),
        ("a-supper", ["土豆牛肉饭", "美式咖啡"])]
    assert metric["mismatches"] == 2


# ---- M3、M4 ----

def test_m3_requires_yesterdays_food_cell_written_or_unsorted(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path)
    builder.digest("day", builder.iso(1), builder.at(2, "00:00"), {"food": "早饭吃了面。", "life": "散步。"})
    builder.digest("day", builder.iso(2), builder.at(3, "00:00"), {"life": "散步。"}, fallback=["food"])
    # 第 2 天第三条观察只有生活列表里有 D−1 那一格，饮食列表为空，不算看得到昨天的饮食；第 3 天最后一条观察的饮食
    # 只有 D−2 那一格（写着回顾），D−1 那一格不在，同样不算。
    for day, food, life in ((2, cell(builder.iso(1), "早饭吃了面。"), None), (2, None, None),
                            (2, None, cell(builder.iso(1), "散步。")), (3, cell(builder.iso(2), unsorted=True), None),
                            (3, cell(builder.iso(2), ""), None), (3, cell(builder.iso(1), "早饭吃了面。"), None)):
        recent = {"饮食": [food] if food else [], "工作": [], "生活": [life] if life else []}
        builder.observation(builder.at(day, "09:00"), memory=memory_layer(recent=recent))
    metric = m3_past_visibility(load_export(builder.build()))
    assert metric["periods"]["all"] == _ratio(2, 6, 0.3333)
    assert metric["unsorted"] == 1
    assert metric["by_day"]["2026-10-09"] == _ratio(1, 3, 0.3333)
    assert metric["by_day"]["2026-10-10"] == _ratio(1, 3, 0.3333)


def test_m3_excludes_days_after_a_day_without_food_records(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path)
    builder.digest("day", builder.iso(1), builder.at(2, "00:00"), {"life": "散步。"})
    for day in (1, 2, 3):
        recent = {"饮食": [cell(builder.iso(max(day - 1, 1)), "吃了面。")], "工作": [], "生活": []}
        builder.observation(builder.at(day, "09:00"), memory=memory_layer(recent=recent))
    metric = m3_past_visibility(load_export(builder.build()))
    assert metric["periods"]["all"] == _ratio(0, 0, None)
    assert metric["unsorted"] == 0


def test_m4_counts_workday_lunch_portions_repeated_within_three_days(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path, days=6, start="2026-10-05")
    builder.catalog["catalog/merchant-catalog.jsonl"].extend([
        {"category": "meal", "goods_id": "wonton", "name": "鲜肉馄饨", "price_fen": 1800},
        {"category": "meal", "goods_id": "dumpling", "name": "猪肉水饺", "price_fen": 2000}])
    for day in range(1, 7):
        duty = "休息日" if day == 4 else "工作日"
        builder.observation(builder.at(day, "07:00"), environment={"layer": "环境层", "duty": {"day": duty}})
    builder.observation(builder.at(4, "09:00"), environment={"layer": "环境层", "duty": {"day": "工作日"}})
    # 馄饨的上一次购买恰在 4 天前（不算重复），水饺的上一次购买恰在 3 天前（算重复）；午饭时段的薯片与咖啡不是 meal。
    for day, clock, goods_id, quantity in ((1, "12:00", "noodle", 1), (1, "18:00", "wonton", 1),
                                           (2, "12:00", "noodle", 2), (2, "12:10", "chips", 1),
                                           (3, "11:00", "beef-rice", 1),
                                           (3, "18:00", "noodle", 1), (3, "18:30", "dumpling", 1),
                                           (4, "12:00", "beef-rice", 1), (5, "12:30", "wonton", 1),
                                           (5, "13:00", "coffee", 1), (5, "14:00", "noodle", 1),
                                           (6, "10:00", "noodle", 1),
                                           (6, "12:00", "beef-rice", 1), (6, "12:30", "dumpling", 1)):
        builder.purchase(goods_id, builder.at(day, clock), quantity, received_at=builder.at(day, clock) + 1800)
    # 收货行与下单尝试不是购买，不计份数也不算前三天买过。
    builder.order_attempt(builder.at(5, "12:10"))
    assert m4_lunch_repeats(load_export(builder.build())) == {
        "workday_lunch_portions": 8, "repeated_portions": 5, "value": 0.625,
        "most_frequent": {"name": "番茄牛肉刀削面", "portions": 4},
        "lunches": [{"time": "2026-10-05 12:00", "name": "番茄牛肉刀削面", "portions": 1, "repeat": False},
                    {"time": "2026-10-06 12:00", "name": "番茄牛肉刀削面", "portions": 2, "repeat": True},
                    {"time": "2026-10-07 11:00", "name": "土豆牛肉饭", "portions": 1, "repeat": False},
                    {"time": "2026-10-09 12:30", "name": "鲜肉馄饨", "portions": 1, "repeat": False},
                    {"time": "2026-10-09 14:00", "name": "番茄牛肉刀削面", "portions": 1, "repeat": True},
                    {"time": "2026-10-10 12:00", "name": "土豆牛肉饭", "portions": 1, "repeat": True},
                    {"time": "2026-10-10 12:30", "name": "猪肉水饺", "portions": 1, "repeat": True}]}

    def lunches(root, orders):
        """一个工作日的若干次下单（时刻，商品）。"""
        single = PilotExportBuilder(root, days=1)
        single.catalog["catalog/merchant-catalog.jsonl"].append(
            {"category": "meal", "goods_id": "wonton", "name": "鲜肉馄饨", "price_fen": 1800})
        single.observation(single.at(1, "07:00"), environment={"layer": "环境层", "duty": {"day": "工作日"}})
        for clock, goods_id in orders:
            single.purchase(goods_id, single.at(1, clock))
        return m4_lunch_repeats(load_export(single.build()))

    # 同份数取最先出现的商品（先出现的馄饨按字典序排在土豆牛肉饭之后）；没有午饭时 most_frequent 与 value 为 null。
    tied = lunches(tmp_path / "tied", (("12:00", "wonton"), ("12:30", "beef-rice")))
    assert (tied["workday_lunch_portions"], tied["value"], tied["most_frequent"]) == (
        2, 0.0, {"name": "鲜肉馄饨", "portions": 1})
    assert lunches(tmp_path / "none", (("18:00", "noodle"),)) == {
        "workday_lunch_portions": 0, "repeated_portions": 0, "value": None, "most_frequent": None, "lunches": []}


# ---- M5 ----

def test_m5_counts_calls_today_queries_non_empty_errors_limits_and_caps(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path)
    item = {"日期": "2026-10-07", "类别": "饮食", "回顾": "吃了面。"}
    unsorted = {"日期": "2026-10-07", "类别": "饮食", "未整理": True, "条目": [entry("12:00", "饮食", ["买面"])]}
    first = builder.decision(1, "12:00")
    builder.search(first, builder.at(1, "12:00"), {"category": "全部", "time": "今天"},
                   {"结果": [entry("08:00", "生活", ["起床"])], "未列出": []}, call_id="s1")
    builder.search(first, builder.at(1, "12:00"), {"category": "全部", "time": "2026-10-07"},
                   {"结果": {"饮食": [item]}, "未列出": []}, call_id="s2")
    builder.search(first, builder.at(1, "12:00"), {"category": "饮食", "time": "今天 08:00~09:00"},
                   {"结果": [], "未列出": []}, call_id="s3")
    second = builder.decision(2, "12:00")
    calls = [("s4", {"结果": {"今天": [entry("08:00", "生活", ["起床"])], "饮食": []}, "未列出": []}),
             ("s5", {"结果": {}, "未列出": []}),
             ("s6", {"结果": [item], "未列出": ["2026-10-01 饮食"], "提示": "范围太大，换更短的时间、指定类别或加关键词"}),
             ("s7", {"结果": [unsorted], "未列出": ["2026-10-07 12:00 饮食"], "提示": "加关键词缩小范围"}),
             ("s8", {"error_code": "MEMORY_TIME_INVALID", "reason": "x", "fix": "y", "items": [], "unmet": []}),
             ("s9", {"error_code": "MEMORY_SEARCH_LIMIT", "reason": "x", "fix": "y", "items": [], "unmet": []})]
    for call_id, answer in calls:
        builder.search(second, builder.at(2, "12:00"), {"category": "全部", "time": "全部", "keyword": "面"},
                       answer, call_id=call_id)
    builder.search(second, builder.at(2, "12:00"), {"time": "2026-10-07"}, call_id="s10",
                   error="arguments: Invalid JSON", answered=False)
    builder.search(second, builder.at(2, "12:00"), {"time": "2026-10-07"}, call_id="s11", answered=False)
    builder.turn(second, builder.at(2, "12:00"), reasoning="不查了")
    assert m5_searches(*_load(builder)) == {
        "calls": 11, "today_calls": 2,
        "by_day": {"2026-10-08": {"calls": 3, "today_calls": 2}, "2026-10-09": {"calls": 8, "today_calls": 0},
                   "2026-10-10": {"calls": 0, "today_calls": 0}},
        "succeeded": 7, "non_empty": _ratio(5, 7, 0.7143), "validation_errors": _ratio(2, 11, 0.1818),
        "error_codes": {"MEMORY_SEARCH_LIMIT": 1, "MEMORY_TIME_INVALID": 1}, "limit_refusals": 1,
        "capped_results": 2, "unanswered": 1, "non_model_events_between": 0}


def test_m5_counts_events_between_a_search_and_the_next_request(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path)
    first = builder.decision(1, "12:00")
    call = search_call("s1", {"category": "全部", "time": "今天"})
    _, response = builder.turn(first, builder.at(1, "12:00"), tool_calls=[call])
    builder.environment("time_trigger", builder.at(1, "12:00"), key="hourly", scheduled_id="h1")
    builder.add("model_response", builder.at(1, "12:00"), {"phase": "response", "call_id": "other"})
    builder.request(first, builder.at(1, "12:00"), history=[
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {"role": "tool", "tool_call_id": "s1", "content": compact({"结果": [], "未列出": []})}])
    second = builder.decision(1, "13:00")
    builder.turn(second, builder.at(1, "13:00"), tool_calls=[search_call("s2", {"time": "今天"})])
    builder.environment("time_trigger", builder.at(1, "13:00"), key="hourly", scheduled_id="h2")
    builder.action(second, builder.at(1, "13:00"), "report_status")
    assert m5_searches(*_load(builder))["non_model_events_between"] == 1


# ---- M6 ----

def test_m6_layer_statistics_by_layer_name_and_memory_parts(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path)
    first_memory = memory_layer([entry("08:00", "生活", ["起床"])])
    second_memory = memory_layer([entry("08:00", "生活", ["起床"]), entry("08:30", "饮食", ["买美式咖啡1份"])])
    progress = [{"layer": "进度层", "events": []}, {"layer": "进度层", "events": [{"type": "tool_call"}]}]
    requests = []
    for clock, layers, memory in (("08:10", [PERSONA, first_memory, progress[0]], first_memory),
                                  ("08:40", [progress[1], PERSONA, second_memory], second_memory)):
        decision = builder.decision(1, clock, memory=memory)
        requests.append(builder.turn(decision, builder.at(1, clock), layers=layers)[0])
    chained = builder.decision(1, "08:40", memory=second_memory)
    builder.request(chained, builder.at(1, "08:40"), [{"layer": "人设层", "x": "很长" * 50}],
                    history=[{"role": "tool", "tool_call_id": "c", "content": "{}"}])
    metric = m6_volume(*_load(builder))
    layers = metric["layers"]
    assert list(layers) == ["人设层", "记忆层", "记忆层·可查", "记忆层·近三天", "记忆层·今天", "进度层", "system",
                            "user", "tools"]
    assert layers["人设层"] == {"n": 2, "median": float(len(compact(PERSONA))), "p90": float(len(compact(PERSONA))),
                              "max": float(len(compact(PERSONA)))}
    assert layers["记忆层"] == _stats([len(compact(first_memory)), len(compact(second_memory))])
    assert layers["记忆层·今天"] == _stats([len(compact(first_memory["今天"])), len(compact(second_memory["今天"]))])
    assert layers["记忆层·可查"]["max"] == float(len(compact({"最早日期": "2026-10-08"})))
    assert layers["进度层"] == _stats([len(compact(item)) for item in progress])
    assert layers["system"]["max"] == float(len("生活系统层"))
    assert layers["user"] == _stats([len(request["data"]["request"]["messages"][1]["content"]) for request in requests])
    assert layers["tools"]["max"] == float(len(compact(requests[0]["data"]["request"]["tools"])))
    sizes = sorted([len(compact(first_memory)), len(compact(second_memory)), len(compact(second_memory))])
    assert metric["memory_layer_by_observation"] == {"n": 3, "median": float(sizes[1]),
                                                     "p90": round(sizes[1] + 0.8 * (sizes[2] - sizes[1]), 1),
                                                     "max": float(sizes[2])}


def test_m6_detects_a_fallback_rewrite_as_a_non_prefix_today(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path)
    first, second, third = entry("08:00", "生活", ["起床"]), entry("09:00", "工作", ["开会"]), entry("10:00", "生活", ["散步"])
    rewritten = entry("08:00", "生活", ["…"])
    for day, today in ((1, [first]), (1, [first, second]), (1, [rewritten, third]), (1, [rewritten, third, second]),
                       (2, [first]), (2, [first, third])):
        builder.observation(builder.at(day, "11:00"), memory=memory_layer(today))
    assert m6_volume(*_load(builder))["fallback_rewrites"] == 1


def test_m6_persona_layer_identical_share(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path)
    changed = {"layer": "人设层", "身份": "李凡，后端工程师", "看法": ["咖啡不错"]}
    for clock, persona in (("08:00", PERSONA), ("09:00", PERSONA), ("10:00", changed)):
        decision = builder.decision(1, clock)
        builder.turn(decision, builder.at(1, clock), layers=[persona, memory_layer()])
    chained = builder.decision(1, "10:00")
    builder.request(chained, builder.at(1, "10:00"), [changed], history=[{"role": "tool", "content": "{}"}])
    assert m6_volume(*_load(builder))["persona_layer_identical"] == _ratio(2, 3, 0.6667)


#: 拟合用的已知权重：汉字、字母、数字、空格、其余 ASCII、其余字符。
WEIGHTS = (2, 1, 3, 1, 2, 4)


def _fresh_snapshots(builder, rows, failed=(), weights=WEIGHTS):
    """每行一次新快照：记忆层「今天」一条核验由六类字符各若干个拼成，另有个数随行变化的 U+3400「㐀」与 U+F900「豈」
    （汉字），system 末尾另有个数随行变化的换行（其余字符，不是空格）；prompt_tokens 按已知权重 weights 从整份请求文字
    算出；下标在 failed 里的那一行只有传输失败证据，没有 usage。"""
    memories = []
    for index, counts in enumerate(rows):
        text = "".join(char * count for char, count in zip(("汉", "a", "1", " ", "!", "é"), counts))
        text += "㐀" * (index * 2 % 5) + "豈" * (index % 3)
        memory = memory_layer([entry("08:00", "生活", [text])])
        decision = builder.decision(1, f"{8 + index // 60:02d}:{index % 60:02d}", memory=memory)
        request = builder.request(decision, builder.at(1, f"{8 + index // 60:02d}:{index % 60:02d}"),
                                  [PERSONA, memory], system="生活系统层" + "\n" * (index * 3 % 4))
        body = request["data"]["request"]
        whole = body["messages"][0]["content"] + body["messages"][1]["content"] + compact(body["tools"])
        if index in failed:
            builder.response(request, error="ConnectionError()", phase="failure")
        else:
            builder.response(request, prompt_tokens=sum(w * c for w, c in zip(weights, _classes(whole))))
        memories.append(memory)
    return memories


def test_m6_fits_memory_layer_tokens_per_character_by_first_differences(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path)
    rows = [((3 * i) % 7 + i, (5 * i) % 11, (i * i) % 13, (7 * i) % 5, (11 * i) % 9, (2 * i) % 3 + i % 4)
            for i in range(13)]
    memories = _fresh_snapshots(builder, rows)
    totals = [sum(column) for column in zip(*(_classes(compact(memory)) for memory in memories))]
    expected = round(sum(w * n for w, n in zip(WEIGHTS, totals)) / sum(totals), 3)
    ratio = m6_volume(*_load(builder))["token_ratio"]
    assert ratio == {"pairs": 12, "weights": {"han": 2.0, "ascii_letter": 1.0, "ascii_digit": 3.0, "space": 1.0,
                                              "ascii_other": 2.0, "other": 4.0},
                     "memory_layer_tokens_per_char": expected, "reference": 0.638, "reference_range": [0.63, 0.645],
                     "within_range": False}
    # 同一组请求换两组权重：比值 0.639 落在区间里，0.623 低于下端。
    for weights, within in (((2, 1, 0, 1, 0, 1), True), ((2, 1, 0, 1, 0, 0), False)):
        other = PilotExportBuilder(tmp_path / f"weights-{within}")
        _fresh_snapshots(other, rows, weights=weights)
        ratio = m6_volume(*_load(other))["token_ratio"]
        share = round(sum(w * n for w, n in zip(weights, totals)) / sum(totals), 3)
        assert (ratio["memory_layer_tokens_per_char"], ratio["within_range"]) == (share, within)
        assert share == (0.639 if within else 0.623)


def test_m6_token_fit_is_null_when_singular_or_too_few_pairs(tmp_path, skip_authentication):
    short = PilotExportBuilder(tmp_path / "short")
    _fresh_snapshots(short, [((3 * i) % 7 + i, (5 * i) % 11, (i * i) % 13, (7 * i) % 5, (11 * i) % 9, i % 4)
                             for i in range(12)])
    singular = PilotExportBuilder(tmp_path / "singular")
    _fresh_snapshots(singular, [(i + 1, 2, 3, 1, 1, 1) for i in range(13)])
    # 14 条新快照有 13 对相邻；第 7 条没有 usage，去掉它两侧的两对，不把第 6 条与第 8 条接成一对，只剩 11 对。
    gap = PilotExportBuilder(tmp_path / "gap")
    _fresh_snapshots(gap, [((3 * i) % 7 + i, (5 * i) % 11, (i * i) % 13, (7 * i) % 5, (11 * i) % 9, (2 * i) % 3 + i % 4)
                           for i in range(14)], failed={6})
    for builder, pairs in ((short, 11), (singular, 12), (gap, 11)):
        ratio = m6_volume(*_load(builder))["token_ratio"]
        assert (ratio["pairs"], ratio["weights"], ratio["memory_layer_tokens_per_char"], ratio["within_range"]) == (
            pairs, None, None, None)


# ---- M7、M8 ----

def test_m7_reads_the_fresh_decision_turn_cache_hit_rate(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path)
    builder.usage("fresh_decision_turn", hit=300, miss=700)
    builder.usage("turn_with_history", hit=5, miss=0)
    builder.usage("plan_day", hit=0, miss=0)
    assert m7_cache(load_export(builder.build())) == {
        "fresh_decision_turn": {"hit_tokens": 300, "miss_tokens": 700, "value": 0.3},
        "by_call_class": {"fresh_decision_turn": 0.3, "turn_with_history": 1.0, "truncation_retry": None,
                          "consolidation": None, "reflection": None, "plan_day": None, "memory_digest": None}}


def test_m8_prices_every_call_class_at_the_frozen_peak_price_per_day(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path, days=2)
    builder.usage("fresh_decision_turn", hit=1_000_000, miss=1_000_000, out=100_000)
    # plan_day 每日 0.0245184 美元、合计每日 0.2381184 美元，保留六位小数。
    builder.usage("plan_day", hit=0, miss=123_456, out=10_000)
    builder.usage("memory_digest", hit=200_000, miss=0, out=0)
    builder.complete = False
    assert m8_cost(load_export(builder.build())) == {
        "days": 2, "per_day_usd": 0.238118,
        "by_call_class": {"fresh_decision_turn": 0.213, "turn_with_history": 0.0, "truncation_retry": 0.0,
                          "consolidation": 0.0, "reflection": 0.0, "plan_day": 0.024518, "memory_digest": 0.0006},
        "complete": False}


# ---- M9 ----

def test_m9_band_rejections_turns_failures_and_compressions(tmp_path, skip_authentication):
    # 上限取冻结清单的 max_react_turns（这里是 6，不是默认的 16）：恰为 6 回合的决策同样算达到上限。第 3 个运行日没有
    # 决策，按 0 次列出，也在带宽外。
    builder = PilotExportBuilder(tmp_path, days=3, max_react_turns=6)
    for index in range(69):
        builder.decision(1, f"{6 + index // 6:02d}:{index % 6 * 10:02d}")
    six = builder.decision(1, "18:00", decision_id="d-six")
    for _ in range(6):
        builder.turn(six, builder.at(1, "18:00"))
    sixteen = builder.decision(1, "19:00", decision_id="d-sixteen")
    for _ in range(16):
        builder.turn(sixteen, builder.at(1, "19:00"))
    builder.response(builder.request(sixteen, builder.at(1, "19:00"), turn=16, attempt=2))
    rejecting = builder.decision(2, "10:00")
    for _ in range(3):
        builder.reject(rejecting, builder.at(2, "10:00"))
    condensed = builder.decision(2, "12:00")
    builder.turn(condensed, builder.at(2, "12:00"), layers=[memory_layer(), {"layer": "进度层", "events": [
        {"occurred_at": "2026-10-09T11:00:00+08:00", "type": "condensed"}]}])
    following = builder.decision(2, "12:00")
    builder.turn(following, builder.at(2, "12:00"), layers=[memory_layer(), {"layer": "进度层", "events": [
        {"type": "tool_call"}]}])
    # 每条回合请求都数，不只数首个请求：这一决策第二回合的进度层带折叠记录。
    builder.turn(following, builder.at(2, "12:00"), layers=[memory_layer(), {"layer": "进度层", "events": [
        {"occurred_at": "2026-10-09T11:30:00+08:00", "type": "condensed"}]}])
    builder.add("run_failed", builder.at(2, "13:00"), {"reason": "stopped"})
    metric = m9_guardrails(*_load(builder))
    assert metric["decisions_per_day"] == {"2026-10-08": 71, "2026-10-09": 3, "2026-10-10": 0}
    assert (metric["decision_band"], metric["days_outside_band"]) == ([68.85, 93.15], ["2026-10-09", "2026-10-10"])
    assert (metric["rejections"], metric["rejection_limit"]) == (3, 7.2)
    assert metric["turns"] == {"max_react_turns": 6, "p99": 8.7, "max": 16, "exhausted": 2, "six_or_more": [
        {"decision_id": "d-six", "time": "2026-10-08 18:00", "turns": 6},
        {"decision_id": "d-sixteen", "time": "2026-10-08 19:00", "turns": 16}]}
    assert (metric["run_failures"], metric["capacity_compressions"]) == (1, 2)
    assert metric["chain"] == {"same_second_followups": 1, "continuations": 0, "fresh_followups": 1,
                               "append_violations": 0, "persona_deltas": 0}
    assert metric["restarted"] == []


def _continuation(builder, clock, today, changes):
    """同一秒接着的决策：首个请求是续接，最后一条 tool 消息带 changes。"""
    decision = builder.decision(1, clock, memory=memory_layer(today))
    history = [{"role": "assistant", "content": None, "tool_calls": [tool_call("c", "report_status", {})]},
               {"role": "tool", "tool_call_id": "a", "content": compact({"status": "completed"})},
               {"role": "tool", "tool_call_id": "c", "content": compact({"status": "completed", "changes": changes})}]
    builder.request(decision, builder.at(1, clock), history=history)


def test_m9_chain_must_append_the_previous_entry_and_carry_no_persona_delta(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path, days=1)
    e1, e2, e3, e4, e5 = (entry(f"08:0{index}", "生活", [f"第{index}件事"]) for index in range(1, 6))
    head = builder.decision(1, "08:00", memory=memory_layer([e1]))
    builder.turn(head, builder.at(1, "08:00"))
    # tool 消息里的条目保持渲染键序，导出的观察是键已排序的规范 JSON：只差键序不算违例。
    _continuation(builder, "08:00", [e1, e2], {"targets": [], "memory": {"added": [dict(reversed(e2.items()))]}})
    _continuation(builder, "08:00", [e1, e2, e4], {"memory": {"added": [e3]}, "relationships": {"小王": "同事"}})
    later = builder.decision(1, "08:05", memory=memory_layer([e1, e2, e4, e5]))
    builder.turn(later, builder.at(1, "08:05"))
    _continuation(builder, "08:05", [e1, e2, e4, e5], {"targets": [], "preferences": ["咖啡"]})
    assert m9_guardrails(*_load(builder))["chain"] == {
        "same_second_followups": 3, "continuations": 3, "fresh_followups": 0, "append_violations": 1,
        "persona_deltas": 2}


def test_m9_lists_a_restart_of_a_just_ended_activity(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path, days=1)
    orders = [builder.purchase(goods_id, builder.at(1, "08:00"))
              for goods_id in ("noodle", "noodle", "noodle", "coffee", "coffee", "noodle")]
    meal, snack = {"kind": "category", "category": "meal"}, {"kind": "category", "category": "snack"}
    pairs = [
        (activity("app-1", "interact", content_id="drama-1", app_id="bilibili"),
         activity("app-2", "interact", content_id="drama-1", app_id="bilibili")),
        (activity("cook-a", "interact", content_id="cook-1"), activity("cook-b", "interact", content_id="cook-2")),
        (activity("buy-1", "purchase", order_ids=[orders[0]]), activity("buy-2", "purchase", order_ids=[orders[1]])),
        (_query("ask-1", meal), _query("ask-2", meal)),
        (activity("go-1", "travel", destination_id="office"), activity("go-2", "travel", destination_id="office")),
        (activity("nap-1", "sleep"), activity("nap-2", "sleep")),
        (activity("walk-1", "interact", content_id="walk"), activity("drama-2", "interact", content_id="drama-1",
                                                                     app_id="bilibili")),
        (activity("walk-x", "interact", content_id="walk"), activity("walk-y", "interact", content_id="walk")),
        # 查询的类别与购买的商品按升序比较：次序相反的同一组目标算同一目标。
        (_query("ask-3", snack, meal), _query("ask-4", meal, snack)),
        (activity("buy-3", "purchase", order_ids=orders[2:4]), activity("buy-4", "purchase", order_ids=orders[4:])),
    ]
    builder.decision(1, "08:00")
    # 刚进入 failed 或 cancelled 的活动与刚 completed 的一样算刚了结。
    statuses = {2: "failed", 4: "cancelled"}
    for index, (ended, started) in enumerate(pairs):
        builder.progress(ended, builder.at(1, f"09:{index * 2:02d}"), statuses.get(index, "completed"))
        decision = builder.decision(1, f"09:{index * 2 + 1:02d}", decision_id=f"d-{index}")
        builder.world(decision, builder.at(1, f"09:{index * 2 + 1:02d}"), "start_main_activity", activities=[started])
    # 在本决策的观察之后才结束的活动不在这一次的窗口里，即使本决策又受理了同一目标。
    late = builder.decision(1, "09:20", decision_id="d-nap-late")
    builder.progress(activity("nap-3", "sleep"), builder.at(1, "09:20"))
    builder.world(late, builder.at(1, "09:20"), "start_main_activity", activities=[activity("nap-4", "sleep")])
    builder.progress(activity("walk-2", "interact", content_id="walk"), builder.at(1, "10:00"))
    builder.decision(1, "10:01")
    walk = builder.decision(1, "10:02", decision_id="d-walk-later")
    builder.world(walk, builder.at(1, "10:02"), "start_main_activity",
                  activities=[activity("walk-3", "interact", content_id="walk")])
    # 收尾在本决策的提交里取消进行中的散步：结束事件是 activity_concluded 环境事件（没有 activity_progress），下一次决策
    # 又开始散步。
    stop = builder.decision(1, "10:10", decision_id="d-stop-walk")
    builder.conclude(stop, builder.at(1, "10:10"), [{"summary": "不走了"}], ["walk-3"])
    again = builder.decision(1, "10:20", decision_id="d-walk-again")
    builder.world(again, builder.at(1, "10:20"), "start_main_activity",
                  activities=[activity("walk-4", "interact", content_id="walk")])
    # 时钟推进里结束、待收尾的做饭在后来的收尾提交里另有带同一终态快照的 activity_concluded，结束事件仍是更早的
    # activity_progress：收尾的那次决策里接着开始同一系列的列出；收尾之后的下一次决策才开始的不列出。
    cook = builder.decision(1, "11:00", decision_id="d-cook-start")
    first = activity("cook-c", "interact", content_id="cook-1")
    builder.world(cook, builder.at(1, "11:00"), "start_main_activity", activities=[first])
    builder.progress(first, builder.at(1, "11:30"))
    wrap = builder.decision(1, "11:31", decision_id="d-wrap-and-cook")
    builder.conclude(wrap, builder.at(1, "11:31"), [{"summary": "做完了"}], ["cook-c"])
    second = activity("cook-d", "interact", content_id="cook-2")
    builder.world(wrap, builder.at(1, "11:31"), "start_main_activity", activities=[second])
    builder.progress(second, builder.at(1, "12:00"))
    builder.conclude(builder.decision(1, "12:01"), builder.at(1, "12:01"), [{"summary": "吃完了"}], ["cook-d"])
    after = builder.decision(1, "12:02", decision_id="d-cook-after-wrap")
    builder.world(after, builder.at(1, "12:02"), "start_main_activity",
                  activities=[activity("cook-e", "interact", content_id="cook-1")])
    restarted = m9_guardrails(*_load(builder))["restarted"]
    assert [(item["decision_id"], item["target"]) for item in restarted] == [
        ("d-0", ["app", "bilibili"]), ("d-1", ["content", "做晚饭"]), ("d-2", ["purchase", ["noodle"]]),
        ("d-3", ["query", "meituan", ["meal"]]), ("d-4", ["travel", "office"]), ("d-5", ["sleep"]),
        ("d-7", ["content", "walk"]), ("d-8", ["query", "meituan", ["meal", "snack"]]),
        ("d-9", ["purchase", ["coffee", "noodle"]]), ("d-walk-again", ["content", "walk"]),
        ("d-wrap-and-cook", ["content", "做晚饭"])]
    assert restarted[0]["time"] == "2026-10-08 09:01"


# ---- M10 ----

def test_m10_counts_calls_retries_and_day_fallback_share(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path, start="2026-09-30")
    row = [{"编号": "n1", "时刻": "08:00", "核验": ["起床"]}]
    for tier, period, day, attempts in (("day", "2026-09-30", 2, 2), ("month", "2026-09", 2, 3),
                                        ("day", "2026-10-01", 3, 1)):
        payload = _day_payload(row, [], row) if tier == "day" else _period_payload("日回顾")
        for attempt in range(1, attempts + 1):
            builder.digest_response(builder.digest_request(tier, period, builder.at(day, "00:00"), payload, attempt))
    builder.digest("day", "2026-09-30", builder.at(2, "00:00"), {"food": "早上吃了面。"}, fallback=["life"])
    builder.digest("month", "2026-09", builder.at(2, "00:00"), {"life": "九月常散步。"}, fallback=["food"])
    # 同一期的类别按 food、work、life 排，与 fallback 列表的次序无关。
    builder.digest("day", "2026-10-01", builder.at(3, "00:00"), {"food": "吃了面。"}, fallback=["life", "work"])
    metric = m10_digests(*_load(builder))
    assert metric["calls_by_day"] == {"2026-10-01": {"day": 2, "month": 3, "year": 0},
                                      "2026-10-02": {"day": 1, "month": 0, "year": 0}}
    assert metric["periods"] == {"day": 2, "month": 1, "year": 0}
    assert metric["expected"] == {"month": 1, "year": 0}
    assert metric["retries"] == 3
    assert metric["fallback"] == {**_ratio(3, 5, 0.6), "cells": [
        {"tier": "day", "period": "2026-09-30", "category": "life"},
        {"tier": "day", "period": "2026-10-01", "category": "work"},
        {"tier": "day", "period": "2026-10-01", "category": "life"},
        {"tier": "month", "period": "2026-09", "category": "food"}]}
    quiet = m10_digests(*_load(PilotExportBuilder(tmp_path / "quiet")))
    assert (quiet["calls_by_day"], quiet["periods"], quiet["expected"], quiet["retries"]) == (
        {}, {"day": 0, "month": 0, "year": 0}, {"month": 0, "year": 0}, 0)
    assert quiet["fallback"] == {**_ratio(0, 0, None), "cells": []}


def test_m10_guard_discards_follow_the_digest_problem_texts(tmp_path, skip_authentication, caplog):
    builder = PilotExportBuilder(tmp_path)
    errors = [
        # 「; the day digest limit」「; shorten it」后面不接 digests.，不被切开；同一响应同一类的两条依据归属问题计一次。
        ("day", "2026-10-08", "; ".join([
            "digests.food.text has 251 characters counting punctuation; the day digest limit is 250; "
            "shorten it by at least 1",
            "digests.food.refs: n9 is not a food record number",
            "digests.food.refs: n7 is not a food record number",
            "digests.work must be null: the input has no work records",
            "digests.life.refs must cite at least one record number"])),
        # 第五批裁定之前记下的正文长度写法（此前两次试点的导出里就是它）照旧计入 text_length，不算 unrecognized。
        ("day", "2026-10-08", "digests.food.text has 230 characters; the day digest limit is 200"),
        ("day", "2026-10-08", "response must contain exactly one choice"),
        ("day", "2026-10-08", "response must contain exactly one submit_memory_digest tool call"),
        ("day", "2026-10-08", "the only tool call must be submit_memory_digest"),
        ("day", "2026-10-08", "submit_memory_digest arguments must be a JSON string"),
        ("day", "2026-10-08", "memory digest output was truncated at max_tokens"),
        ("day", "2026-10-08", "food.text: Input should be a valid string"),
        # 第四批裁定撤销的九道守卫没有模板：它们的文字按问题条数计入 unrecognized。
        ("day", "2026-10-09", "; ".join([
            "digests.food: the name 拿铁咖啡 does not appear in the food input",
            "digests.food: 8:30 does not appear in the food input",
            "digests.work.text must contain Chinese",
            "digests.life.text contains a hexadecimal identifier",
            "digests.food: the goods name 薯片 does not appear in the food verified lines",
            "digests.food must name every food received and every meal kit cooked that day; missing: 美式咖啡、薯片",
            f"digests.food: the sentence about 做饭 names {WINGS}, but the meal kit cooked that day was none",
            "digests.work: 3 份 does not appear in the same input record as a name in its sentence",
            "digests.life: the count 2 次 must be copied with its whole count line"])),
        # 不认识的问题与认得的问题在同一响应里：前者计入 unrecognized，后者照常计入它的守卫。
        ("day", "2026-10-09", "digests.life: the weather was odd; digests.work.refs: n2 is not a work record number"),
        ("day", "2026-10-09", None),
        # 同一响应里不同类别的依据归属问题各计一次。
        ("month", "2026-10", "; ".join([
            "digests.food must not be null: the input has food records",
            "digests.work.text must not be empty",
            "digests.life.text has 801 characters counting punctuation; the month digest limit is 800; "
            "shorten it by at least 1",
            "digests.food.refs: d3 is not a food record number",
            "digests.life.refs: d4 is not a life record number"])),
    ]
    attempts: dict[str, int] = {}
    for tier, period, error in errors:
        attempts[period] = attempts.get(period, 0) + 1
        payload = _day_payload() if tier == "day" else _period_payload("日回顾")
        request = builder.digest_request(tier, period, builder.at(3, "00:00"), payload, attempts[period])
        builder.digest_response(request, error)
    builder.digest_response(builder.digest_request("day", "2026-10-10", builder.at(3, "00:00"), _day_payload()),
                            "ConnectionError()", phase="failure")
    # 决策回合响应里的错误不是回顾的丢弃，即使文字与回顾的参数结构错误或响应级错误相同。
    decision = builder.decision(3, "08:00")
    builder.turn(decision, builder.at(3, "08:00"), error="arguments: Invalid JSON")
    builder.turn(decision, builder.at(3, "08:00"), error="response must contain exactly one choice")
    caplog.set_level(logging.ERROR)
    discards = m10_digests(*_load(builder))["guard_discards"]
    assert discards == {"response": 4, "truncated": 1, "schema": 1, "text_without_input": 1, "null_with_input": 1,
                        "text_empty": 1, "text_length": 3, "refs_empty": 1, "refs_category": 4, "unrecognized": 10}
    assert list(discards) == GUARDS
    assert [message for message in caplog.messages if message.endswith("matches no guard")] == [
        "Memory digest error memory-digest:1:day:2026-10-09:attempt:1 matches no guard"] * 9 + [
        "Memory digest error memory-digest:1:day:2026-10-09:attempt:2 matches no guard"]


def test_m10_digest_is_cited_by_an_eight_character_echo_while_visible(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path, days=3)
    texts = {"food": "中午吃了番茄牛肉刀削面很满足。", "work": "上午处理消息与收尾。", "life": "晚上在公园散步看了晚霞。"}
    builder.digest("day", builder.iso(1), builder.at(2, "00:00"), texts)
    # 月回顾的段落不是日回顾格，被回声了也不进分母。
    builder.digest_request("month", "2026-10", builder.at(2, "00:00"), _period_payload("日回顾"))
    builder.digest("month", "2026-10", builder.at(2, "00:00"), {"life": "这个月常在公园散步看了晚霞。"})
    recent = {"饮食": [cell(builder.iso(1), texts["food"])], "工作": [cell(builder.iso(1), texts["work"])], "生活": []}
    first = builder.decision(2, "08:00", memory=memory_layer(recent=recent))
    builder.turn(first, builder.at(2, "08:00"), reasoning="昨天吃了番茄牛肉刀削面")
    second = builder.decision(2, "09:00")
    builder.search(second, builder.at(2, "09:00"), {"category": "生活", "time": builder.iso(1)},
                   {"结果": [{"日期": builder.iso(1), "类别": "生活", "回顾": texts["life"]}], "未列出": []},
                   reasoning="想起在公园散步看了晚霞")
    # 第三次决策只看得到同一天的饮食格，回声的是工作格：类别不同，不算引用。
    food_only = {"饮食": [cell(builder.iso(1), texts["food"])], "工作": [], "生活": []}
    third = builder.decision(2, "10:00", memory=memory_layer(recent=food_only))
    builder.turn(third, builder.at(2, "10:00"), reasoning="上午处理消息与收尾很顺利")
    # 看得到工作格、但只有连续 7 字「上午处理消息与」相同，不够八字回声，不算引用。
    seven = {"饮食": [], "工作": [cell(builder.iso(1), texts["work"])], "生活": []}
    builder.turn(builder.decision(2, "11:00", memory=memory_layer(recent=seven)), builder.at(2, "11:00"),
                 reasoning="上午处理消息与，接着做")
    # 回声只写在响应正文 content 里同样算引用。
    report = "下午写周报和整理文档。"
    builder.digest("day", builder.iso(2), builder.at(3, "00:00"), {"work": report})
    work_only = {"饮食": [], "工作": [cell(builder.iso(2), report)], "生活": []}
    fourth = builder.decision(3, "08:00", memory=memory_layer(recent=work_only))
    builder.turn(fourth, builder.at(3, "08:00"), reasoning="今天接着做", content="昨天下午写周报和整理文档，今天收尾")
    assert m10_digests(*_load(builder))["cited"] == _ratio(3, 4, 0.75)


def test_m10_view_object_coverage_reads_the_digest_input(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path, days=2)
    # 看法正文里也可能有「：」，对象只到第一个「：」为止：「看法·早餐店：便宜：好吃」的对象是「早餐店」。
    food = [{"编号": "n1", "时刻": "08:36", "核验": ["买美式咖啡1份"],
             "自述": ["购买美式咖啡：提神", "看法·冰美式咖啡：很提神", "看法·楼下咖啡店：排队太久"]},
            {"编号": "n2", "时刻": "12:00", "自述": ["看法·冰美式咖啡：再来一杯", "看法·早餐店：便宜：好吃"]}]
    life = [{"编号": "n3", "时刻": "20:00", "自述": ["看法·公园：人少", "看法·无冒号"]}]
    builder.digest_request("day", builder.iso(1), builder.at(2, "00:00"), _day_payload(food, [], life))
    builder.digest_request("day", builder.iso(1), builder.at(2, "00:00"),
                           _day_payload([{"编号": "n1", "时刻": "08:00", "自述": ["看法·薯片：好吃"]}], [], life), 2)
    # 对象只在自己那一格的正文里找：写在工作格里的「楼下咖啡店」不算饮食这一格写到了。
    builder.digest("day", builder.iso(1), builder.at(2, "00:00"),
                   {"food": "早上的冰美式咖啡很提神，早餐店便宜。", "work": "在楼下咖啡店开会。"}, fallback=["life"])
    assert m10_digests(*_load(builder))["view_coverage"] == _ratio(2, 4, 0.5)


def test_m10_counts_number_mentions_not_copied_from_statistics(tmp_path, skip_authentication):
    # 统计行照抄只报告（第四批裁定之后不是检查项），口径照旧。
    builder = PilotExportBuilder(tmp_path, days=2, start="2026-10-31")
    month = _period_payload("日回顾", food=([{"编号": "d1", "日期": "2026-10-30", "回顾": "吃了面。"}],
                                          ["番茄牛肉刀削面 3 次", "美式咖啡 2 次"]),
                            life=([{"编号": "d2", "日期": "2026-10-30", "回顾": "散步。"}], []))
    builder.digest_request("month", "2026-10", builder.at(2, "00:00"), month)
    # 生活段落里的「美式咖啡 2 次」只是饮食的统计行，不是生活这一类的，照样记一次；数字与「次」之间没有空格的
    # 「薯片4次」同样是次数写法。
    builder.digest("month", "2026-10", builder.at(2, "00:00"),
                   {"food": "这个月番茄牛肉刀削面 3 次，美式咖啡 2 次，另外薯片 4 次。还吃了 3 次面，买了薯片4次。",
                    "life": "散步 5 次。美式咖啡 2 次。"})
    builder.digest_request("year", "2026", builder.at(2, "00:00"),
                           _period_payload("月回顾", life=([{"编号": "m1", "月份": "2026-10", "回顾": "散步。"}],
                                                          ["公园散步 5 次"])))
    builder.digest("year", "2026", builder.at(2, "00:00"), {"life": "今年公园散步 5 次。"})
    builder.digest("day", "2026-10-31", builder.at(2, "00:00"), {"food": "吃了 2 次面。"})
    assert m10_digests(*_load(builder))["statistics_copy_violations"] == 5


def test_m10_lists_missing_and_duplicate_day_digests_and_expected_months(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path, days=5, start="2026-09-29")
    for day in (1, 2, 3, 5):
        builder.decision(day, "09:00")
    builder.digest("day", "2026-09-29", builder.at(2, "00:00"), {"life": "散步。"})
    builder.digest_request("month", "2026-09", builder.at(3, "00:00"), _period_payload("日回顾"))
    builder.digest("month", "2026-09", builder.at(3, "00:00"), {"life": "九月散步。"})
    builder.digest("day", "2026-10-01", builder.at(4, "00:00"), {"life": "散步。"})
    builder.digest("day", "2026-10-01", builder.at(4, "00:00"), {"life": "又散步。"})
    metric = m10_digests(*_load(builder))
    assert (metric["day_digest_problems"], metric["missing_day_digests"], metric["duplicate_digests"]) == (
        2, ["2026-09-30"], [{"tier": "day", "period": "2026-10-01", "events": 2}])
    assert (metric["expected"], metric["periods"]["month"]) == ({"month": 1, "year": 0}, 1)
    # 第一个运行日即使是 1 日也不算；跨年的零点同时应有一条月回顾与一条年回顾。
    for start, expected in (("2026-01-01", {"month": 0, "year": 0}), ("2026-12-31", {"month": 1, "year": 1})):
        span = PilotExportBuilder(tmp_path / start, days=2, start=start)
        assert m10_digests(*_load(span))["expected"] == expected, start


def test_m10_repetition_and_residue_come_from_the_behavior_tool(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path)
    same = "今天中午在公司楼下吃了番茄牛肉刀削面"
    builder.digest("day", builder.iso(1), builder.at(2, "00:00"),
                   {"food": same, "life": "Marketing analyst at AOI 500038964 的样子"})
    builder.digest("day", builder.iso(2), builder.at(3, "00:00"), {"food": same})
    builder.usage("consolidation", hit=0, miss=0, request_events=2)
    builder.usage("reflection", hit=0, miss=0, request_events=1)
    export, rows = _load(builder)
    metric = m10_digests(export, rows)
    assert metric["repetition"] == digest_repetition(list(export.events)) == {"pairs": 1, "similar_pairs": 1,
                                                                              "ratio": 1.0}
    assert metric["identity_residue"] == digest_identity_residue(list(export.events))
    assert metric["identity_residue"]["hits"] == 1
    assert metric["retired_calls"] == 3


# ---- M11 ----

def test_m11_coverage_and_food_fallback_partition_the_food_days(tmp_path, skip_authentication):
    # M11 ① 只报告（第四批裁定之后不是检查项），口径照旧。
    builder = PilotExportBuilder(tmp_path, days=6)
    builder.received("noodle", builder.at(1, "12:00"))
    builder.digest("day", builder.iso(1), builder.at(2, "00:00"), {"food": "中午吃了番茄牛肉刀削面。"})
    # 第 2 天用家里原有的食材做了饭（之前没有重置）：「家里原有的食材」不进 F(D)。
    home = builder.cook(builder.decision(2, "18:00"), builder.at(2, "18:00"), "a-home")
    builder.progress(home, builder.at(2, "19:00"))
    builder.digest("day", builder.iso(2), builder.at(3, "00:00"), {"food": "只喝了水。"})
    builder.digest("day", builder.iso(3), builder.at(4, "00:00"), {"life": "散步。"}, fallback=["food"])
    builder.received("coffee", builder.at(4, "09:00"))
    builder.digest("day", builder.iso(4), builder.at(5, "00:00"), {"life": "散步。"})
    builder.digest("day", builder.iso(5), builder.at(6, "00:00"), {"life": "散步。"})
    # 最后一个运行日没有日回顾，不是回顾日：当天的收货不进分母。
    builder.received("coffee", builder.at(6, "09:00"))
    metric = m11_digest_fidelity(load_export(builder.build()))
    assert (metric["coverage"], metric["food_fallback"], metric["coverage_plus_fallback"]) == (
        _ratio(2, 4, 0.5), _ratio(1, 4, 0.25), _ratio(3, 4, 0.75))
    assert [(day["date"], day["expected"], day["missing"], day["fallback"]) for day in metric["days"]] == [
        ("2026-10-08", ["番茄牛肉刀削面"], [], False), ("2026-10-09", [], [], False), ("2026-10-10", [], [], True),
        ("2026-10-11", ["美式咖啡"], ["美式咖啡"], False)]
    long = PilotExportBuilder(tmp_path / "long", days=161, start="2026-01-01")
    long.received("noodle", long.at(1, "12:00"))
    long.digest("day", long.iso(1), long.at(2, "00:00"), {"food": "番茄牛肉刀削面"})
    for day in range(2, 161):
        long.digest("day", long.iso(day), long.at(day + 1, "00:00"), {"life": "散步。"}, fallback=["food"])
    metric = m11_digest_fidelity(load_export(long.build()))
    assert (metric["coverage"]["value"], metric["food_fallback"]["value"]) == (0.0063, 0.9938)
    assert metric["coverage_plus_fallback"] == _ratio(160, 160, 1.0)


def test_m11_cooking_meal_and_foreign_dish_mismatches(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path, days=4)
    # 「虾配粥」按「配」切出的「虾」「粥」不足两字，不进菜名词表。
    builder.catalog["catalog/merchant-catalog.jsonl"].append(
        {"category": "goods", "goods_id": "kit-shrimp", "name": "虾配粥一人份食材包", "price_fen": 3000})
    builder.catalog["catalog/callbacks.jsonl"][0]["reset_on_goods"].append("kit-shrimp")
    builder.kit_arrives("kit-chestnut", builder.at(1, "20:00"))
    builder.kit_arrives("detergent", builder.at(1, "20:30"), state="laundry")
    # purchases.jsonl 的收货行与下单尝试不是购买行。04:00 收到的咖啡正落在早饭时段的起点上（左闭）。
    builder.purchase("coffee", builder.at(2, "03:40"), received_at=builder.at(2, "04:00"))
    builder.received("coffee", builder.at(2, "04:00"))
    builder.purchase("noodle", builder.at(2, "11:40"), received_at=builder.at(2, "12:00"))
    builder.received("noodle", builder.at(2, "12:00"))
    builder.order_attempt(builder.at(2, "11:30"))
    # 薯片当天只买了没收到，土豆牛肉饭当天只收到没买：两者都在 N(D) 里。
    builder.purchase("chips", builder.at(2, "15:00"))
    builder.received("beef-rice", builder.at(2, "15:30"))
    snapshot = builder.cook(builder.decision(2, "18:00"), builder.at(2, "18:00"), "a-cook")
    builder.progress(snapshot, builder.at(2, "19:00"))
    # 凭非食品状态 laundry 的洗衣服用了洗衣液，不是做饭，洗衣液不进 F(D)。
    laundry = builder.cook(builder.decision(2, "20:00"), builder.at(2, "20:00"), "a-laundry", content_id="laundry",
                           state="laundry")
    builder.progress(laundry, builder.at(2, "20:40"))
    # 句在「。」「！」「？」处切开：「做饭」与「早饭」各在以「！」结尾的一句里，食材包名与土豆牛肉饭在后面以「？」结尾的
    # 一句里，都不同句；最后不带标点的「晚饭吃了薯片」也是一句。
    text = (f"早饭喝了美式咖啡。午饭吃了番茄牛肉刀削面。晚饭做饭用了{WINGS}，还做了番茄炒蛋。早饭还吃了薯片。"
            f"晚饭喝了美式咖啡。晚上没再做饭！冰箱里还有一份{WINGS}？早饭很简单！下午收到土豆牛肉饭？"
            "公司的盒饭没吃，喝了白粥，吃了虾仁。晚饭吃了薯片")
    builder.digest("day", builder.iso(2), builder.at(3, "00:00"), {"food": text})
    # 第 3 天用第 2 天晚上收到的虾配粥食材包做饭，经收尾取消结束（结束事件是 activity_concluded，没有 activity_progress），
    # 这份食材包同样是当天做饭用的。N(D) 与餐次只看 D 当天：前一天买、收到的咖啡与刀削面不算第 3 天的。
    builder.kit_arrives("kit-shrimp", builder.at(2, "21:00"))
    builder.cook(builder.decision(3, "18:00"), builder.at(3, "18:00"), "a-shrimp")
    builder.conclude(builder.decision(3, "18:30"), builder.at(3, "18:30"), [{"summary": "不做了"}], ["a-shrimp"])
    shrimp = "虾配粥一人份食材包"
    builder.digest("day", builder.iso(3), builder.at(4, "00:00"),
                   {"food": f"早饭喝了美式咖啡。夜里又吃了番茄牛肉刀削面。晚饭做饭用了{shrimp}。"})
    metric = m11_digest_fidelity(load_export(builder.build()))
    assert (metric["cooking_mismatches"], metric["meal_mismatches"], metric["foreign_dishes"]) == (1, 4, 5)
    assert metric["days"] == [
        {"date": "2026-10-09", "expected": ["美式咖啡", "番茄牛肉刀削面", "土豆牛肉饭", CHESTNUT], "missing": [CHESTNUT],
         "fallback": False, "cooking_mismatch": [WINGS], "meal_mismatch": ["薯片", "美式咖啡", "薯片"],
         "foreign": [WINGS, "番茄炒蛋", WINGS]},
        {"date": "2026-10-10", "expected": [shrimp], "missing": [], "fallback": False, "cooking_mismatch": [],
         "meal_mismatch": ["美式咖啡"], "foreign": ["美式咖啡", "番茄牛肉刀削面"]}]
    assert metric["coverage"] == _ratio(1, 2, 0.5)


# ---- M12 ----

def _views(builder, day, clock, *views, status="completed"):
    """一次收尾写下若干看法（about, type, text）。"""
    decision = builder.decision(day, clock)
    items = [{"summary": "收尾", "views": [{"about": about, "type": kind, "text": text} for about, kind, text in views]}]
    builder.conclude(decision, builder.at(day, clock), items, ["x"], status=status)
    return decision


def test_m12_counts_food_purchases_named_by_an_earlier_preference(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path, days=4)
    # 点名土豆牛肉饭的是 relationship 看法，不是 preference，不计入分子。
    _views(builder, 1, "09:00", ("番茄牛肉刀削面", "preference", "很好吃"), ("同事小王", "relationship", "人不错"),
           ("土豆牛肉饭", "relationship", "老板人好"))
    _views(builder, 1, "10:00", ("薯片零食", "preference", "好吃"), status="cancelled")
    _views(builder, 2, "09:00", ("美式咖啡", "preference", "提神"))
    _views(builder, 3, "09:00", ("薯片零食", "preference", "好吃"))
    # 每笔购买另有一条收货行，再加一条下单尝试：两者都不是购买，不计次数。
    for day, clock, goods_id in ((1, "12:00", "noodle"), (2, "12:00", "noodle"), (2, "13:00", "beef-rice"),
                                 (3, "12:00", "chips"), (3, "18:00", "coffee"), (3, "19:00", "tissue"),
                                 (4, "12:00", "chips")):
        builder.purchase(goods_id, builder.at(day, clock), received_at=builder.at(day, clock) + 1800)
    builder.order_attempt(builder.at(4, "11:00"))
    metric = m12_endorsed_repurchase(*_load(builder))
    assert (metric["purchases"], metric["endorsed"]) == (5, _ratio(3, 5, 0.6))
    assert [(row["time"], row["goods"], row["endorsed_by"]) for row in metric["rows"]] == [
        ("2026-10-09 12:00", "番茄牛肉刀削面", [{"about": "番茄牛肉刀削面", "date": "2026-10-08"}]),
        ("2026-10-09 13:00", "土豆牛肉饭", []),
        ("2026-10-10 12:00", "薯片", []),
        ("2026-10-10 18:00", "美式咖啡", [{"about": "美式咖啡", "date": "2026-10-09"}]),
        ("2026-10-11 12:00", "薯片", [{"about": "薯片零食", "date": "2026-10-10"}])]


def test_m12_short_or_ambiguous_subjects_never_name_goods(tmp_path, skip_authentication):
    def endorsed(root, extra_kit):
        builder = PilotExportBuilder(root, days=2)
        if extra_kit:
            builder.catalog["catalog/merchant-catalog.jsonl"].append(
                {"category": "goods", "goods_id": "kit-beef", "name": "土豆烧牛肉一人份食材包", "price_fen": 3800})
            builder.catalog["catalog/callbacks.jsonl"][0]["reset_on_goods"].append("kit-beef")
        # 恰好 3 字的对象与商品名相等时点名。
        builder.catalog["catalog/merchant-catalog.jsonl"].append(
            {"category": "drink", "goods_id": "iced-latte", "name": "冰拿铁", "price_fen": 1600})
        _views(builder, 1, "09:00", ("一人份食材包", "preference", "方便"), ("薯片", "preference", "好吃"),
               ("刀削面", "preference", "好吃"), ("冰拿铁", "preference", "好喝"))
        for goods_id in ("kit-chestnut", "chips", "noodle", "iced-latte"):
            builder.purchase(goods_id, builder.at(2, "12:00"))
        return [bool(row["endorsed_by"]) for row in m12_endorsed_repurchase(*_load(builder))["rows"]]

    assert endorsed(tmp_path / "three-kits", True) == [False, False, False, True]
    assert endorsed(tmp_path / "two-kits", False) == [True, False, False, True]


def test_m12_splits_by_card_and_view_age(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path, days=6)
    # 卡片与番茄牛肉刀削面最长的公共段是「牛肉刀削」4 字（不到全名 7 字，够 min(4, 7)），与美式咖啡是「美式咖」3 字
    # （不够 min(4, 4)）；两字的薯片只要两字都在卡片上就算在卡片上。
    card = {"layer": "人设层", "口味": "爱喝美式咖，常吃牛肉刀削，下午来点薯片"}
    first = _views(builder, 1, "09:00", ("番茄牛肉刀削面", "preference", "很好吃"), ("美式咖啡", "preference", "提神"))
    builder.turn(first, builder.at(1, "09:00"), layers=[card, memory_layer()])
    _views(builder, 4, "09:00", ("美式咖啡", "preference", "还是提神"))
    # 卡片只取第一条新快照：后来的新快照换了人设层，咖啡照样不在卡片上。
    later = builder.decision(5, "08:00")
    builder.turn(later, builder.at(5, "08:00"), layers=[{"layer": "人设层", "口味": "最爱美式咖啡"}, memory_layer()])
    # 刀削面在看法之后 2、3、4 天各买一次，3 天归「1-3」，4 天归「4+」；第 6 天的咖啡有 5 天前和 2 天前两条看法，
    # 年龄按最晚那条算。
    for day, goods_id in ((3, "noodle"), (4, "chips"), (4, "noodle"), (5, "noodle"), (6, "coffee")):
        builder.purchase(goods_id, builder.at(day, "12:00"))
    metric = m12_endorsed_repurchase(*_load(builder))
    assert (metric["purchases"], metric["endorsed"], metric["on_card"], metric["not_on_card"]) == (
        5, _ratio(4, 5, 0.8), _ratio(3, 5, 0.6), _ratio(1, 5, 0.2))
    assert metric["age"] == {"1-3": 3, "4+": 1}
    assert [row["on_card"] for row in metric["rows"]] == [True, True, True, True, False]


def test_m12_earlier_view_reliance_by_mention_or_echo_absent_from_the_request(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path, days=2)
    writer = _views(builder, 1, "09:00", ("番茄牛肉刀削面", "preference", "汤头浓郁面条筋道很好吃"),
                    ("同事小王", "relationship", "小王总是帮我带咖啡很贴心"), ("猫", "preference", "楼下那只猫很亲人每天来蹭腿"),
                    ("楼下的咖啡店", "preference", "排队的人太多"), ("Ｃ位咖啡", "preference", "名字好记"))
    builder.turn(writer, builder.at(1, "09:00"), reasoning="番茄牛肉刀削面真好吃")
    named = memory_layer([entry("08:00", "饮食", ["查番茄牛肉刀削面", "汤头浓郁面条筋道"])])
    # 规范化后不足 2 字的对象「猫」写在思考里也不算点名；规范化删去「的」并做 NFKC，所以「楼下的咖啡店」被「楼下咖啡店」
    # 点名，全角的「Ｃ位咖啡」被半角的「C位咖啡」点名；与看法正文只有连续 7 字「汤头浓郁面条筋」相同不够八字回声。
    for clock, reasoning, layers in (("08:10", "中午还吃番茄牛肉刀削面", None), ("08:20", "汤头浓郁面条筋道，今天也想吃", None),
                                     ("08:30", "番茄牛肉刀削面", [named]), ("08:40", "小王总是帮我带咖啡", None),
                                     ("08:50", "汤头浓郁面条筋道", [named]), ("09:00", "楼下有只猫", None),
                                     ("09:01", "楼下咖啡店还行", None), ("09:02", "C位咖啡不错", None),
                                     ("09:03", "汤头浓郁面条筋，再说", None)):
        decision = builder.decision(2, clock, decision_id=f"d-{clock}")
        builder.turn(decision, builder.at(2, clock), layers=layers, reasoning=reasoning)
    # 模型文字含工具参数：items 每项去掉 views 后各自紧凑序列化，所以收尾总结里的回声算、看法正文里的不算；其余键组成的
    # 对象（没有 items 时是整个参数）紧凑序列化，键名把两段值隔开，拼不出看法对象。
    # 参数解析不了时用原文。请求文字含历史里 assistant 工具调用的参数：对象在那里出现过就不算点名。
    truncated = {"id": "c-f", "type": "function",
                 "function": {"name": "conclude_activity", "arguments": '{"items":[{"summary":"汤头浓郁面条筋道'}}
    conclude = {"items": [{"summary": "汤头浓郁面条筋道，吃完了"}]}
    with_views = {"items": [{"summary": "吃完了", "views": [
        {"about": "番茄牛肉刀削面", "type": "preference", "text": "汤头浓郁面条筋道很好吃"}]}]}
    history = [{"role": "assistant", "content": None,
                "tool_calls": [tool_call("c-h", "start_side_activity", {"targets": ["番茄牛肉刀削面"]})]},
               {"role": "tool", "tool_call_id": "c-h", "content": compact({"status": "completed"})}]
    for clock, reply, previous in (
            ("09:10", {"tool_calls": [tool_call("c-a", "conclude_activity", conclude)]}, ()),
            ("09:20", {"tool_calls": [tool_call("c-b", "conclude_activity", with_views)]}, ()),
            ("09:30", {"reasoning": "番茄牛肉刀削面还不错"}, history),
            ("09:40", {"tool_calls": [tool_call("c-d", "start_side_activity", {"name": "番茄牛肉", "note": "刀削面"})]},
             ()),
            ("09:50", {"tool_calls": [tool_call("c-e", "report_status", {"items": [{"summary": "在吃"}],
                                                                          "name": "番茄牛肉", "note": "刀削面"})]}, ()),
            ("10:00", {"tool_calls": [truncated]}, ())):
        decision = builder.decision(2, clock, decision_id=f"d-{clock}")
        builder.turn(decision, builder.at(2, clock), history=previous, **reply)
    reliance = m12_endorsed_repurchase(*_load(builder))["earlier_view_reliance"]
    assert reliance == {"decisions": 16, "relying": 7, "value": 0.4375, "rows": [
        {"decision_id": "d-08:10", "time": "2026-10-09 08:10",
         "views": [{"about": "番茄牛肉刀削面", "date": "2026-10-08", "by": "mention"}]},
        {"decision_id": "d-08:20", "time": "2026-10-09 08:20",
         "views": [{"about": "番茄牛肉刀削面", "date": "2026-10-08", "by": "echo"}]},
        {"decision_id": "d-08:40", "time": "2026-10-09 08:40",
         "views": [{"about": "同事小王", "date": "2026-10-08", "by": "echo"}]},
        {"decision_id": "d-09:01", "time": "2026-10-09 09:01",
         "views": [{"about": "楼下的咖啡店", "date": "2026-10-08", "by": "mention"}]},
        {"decision_id": "d-09:02", "time": "2026-10-09 09:02",
         "views": [{"about": "Ｃ位咖啡", "date": "2026-10-08", "by": "mention"}]},
        {"decision_id": "d-09:10", "time": "2026-10-09 09:10",
         "views": [{"about": "番茄牛肉刀削面", "date": "2026-10-08", "by": "echo"}]},
        {"decision_id": "d-10:00", "time": "2026-10-09 10:00",
         "views": [{"about": "番茄牛肉刀削面", "date": "2026-10-08", "by": "echo"}]}]}


# ---- 检查项与对照表 ----

#: 每条检查的通过值与失败值（按 CHECK_NAMES 的次序）。
CHECK_VALUES = [
    ("M2.named.value", 1.0, 0.9), ("M3.periods.all.value", 1.0, 0.99), ("M5.validation_errors.value", 0.05, 0.06),
    ("M5.non_empty.value", 0.7, 0.69), ("M5.non_model_events_between", 0, 1),
    ("M6.memory_layer_by_observation.max", 14000.0, 14000.1), ("M6.layers.进度层.median", 3000.0, 3000.5),
    ("M6.layers.进度层.p90", 7000.0, 7000.5), ("M6.layers.user.median", 22000.0, 22000.5),
    ("M6.persona_layer_identical.value", 1.0, 0.95), ("M7.fresh_decision_turn.value", 0.55, 0.5499),
    ("M8.per_day_usd", 0.22, 0.220001), ("M9.days_outside_band", [], ["2026-10-09"]), ("M9.rejections", 4, 5),
    ("M9.turns.exhausted", 0, 1), ("M9.run_failures", 0, 1), ("M9.capacity_compressions", 0, 2),
    ("M9.restarted", [], [{"decision_id": "d"}]), ("M9.chain.append_violations", 0, 1),
    ("M9.chain.persona_deltas", 0, 1), ("M10.fallback.value", 0.1, 0.1001), ("M10.day_digest_problems", 0, 1),
    ("M10.retired_calls", 0, 2), ("M10.periods.month", 1, 0),
]


def _metrics(values):
    """按点分路径造报告的 metrics。"""
    metrics: dict = {}
    for path, value in values.items():
        node = metrics
        *parents, last = path.split(".")
        for key in parents:
            node = node.setdefault(key, {})
        node[last] = value
    return metrics


def test_checks_apply_the_final_state_thresholds():
    passing = {path: good for path, good, _ in CHECK_VALUES}
    passing.update({"M9.rejection_limit": 4.8, "M10.expected.month": 1})
    checks = pilot_checks(_metrics(passing))
    assert [check["name"] for check in checks] == CHECK_NAMES
    names = {check["name"] for check in checks}
    assert not {"statistics_copied", "food_coverage_partition", "cooking_mismatch"} & names
    assert all(check["pass"] is True for check in checks)
    # 统计行照抄、M11 ① 与 ② 只报告：取什么值都不改变任何一项的结果。
    for report_only in ({"M10.statistics_copy_violations": 7, "M11.coverage.value": 0.1,
                         "M11.food_fallback.value": 0.2, "M11.coverage_plus_fallback.value": 0.3,
                         "M11.cooking_mismatches": 3},
                        {"M10.statistics_copy_violations": None, "M11.coverage_plus_fallback.value": None,
                         "M11.cooking_mismatches": None}):
        assert pilot_checks(_metrics({**passing, **report_only})) == checks
    assert checks[13] == {"name": "rejections", "path": "M9.rejections", "comparison": "<=", "threshold": 4.8,
                          "value": 4, "pass": True}
    assert checks[23] == {"name": "month_digests", "path": "M10.periods.month", "comparison": "==", "threshold": 1,
                          "value": 1, "pass": True}
    assert (checks[12]["value"], checks[17]["value"]) == (0, 0)
    for index, (path, _, bad) in enumerate(CHECK_VALUES):
        failing = pilot_checks(_metrics({**passing, path: bad}))[index]
        missing = pilot_checks(_metrics({**passing, path: None}))[index]
        assert (failing["pass"], missing["value"], missing["pass"]) == (False, None, None), path


def test_digest_review_lists_each_category_with_its_verified_lines(tmp_path, skip_authentication):
    builder = PilotExportBuilder(tmp_path)
    food = [{"编号": "n1", "时刻": "08:36", "核验": ["买美式咖啡1份", "送达美式咖啡"], "自述": ["看法·咖啡：提神"]},
            {"编号": "n2", "时刻": "12:00", "核验": ["吃了面"]}]
    work = [{"编号": "n1", "时刻": "09:00", "核验": ["处理消息 09:00–10:00"]}]
    builder.digest_request("day", builder.iso(2), builder.at(3, "00:00"), _day_payload([], work, []))
    builder.digest("day", builder.iso(2), builder.at(3, "00:00"), {"work": "上午处理消息。"})
    builder.digest_request("day", builder.iso(1), builder.at(2, "00:00"),
                           _day_payload(food, [], [{"编号": "n3", "时刻": "20:00", "核验": ["散步 20:00–20:30"],
                                                    "自述": ["散步"]}]))
    builder.digest("day", builder.iso(1), builder.at(2, "00:00"), {"food": "喝了美式咖啡。"}, fallback=["life"])
    # 对照表只列日回顾，月回顾不进表。
    builder.digest_request("month", "2026-10", builder.at(3, "00:00"), _period_payload("日回顾"))
    builder.digest("month", "2026-10", builder.at(3, "00:00"), {"life": "这个月常散步。"})
    assert digest_review(load_export(builder.build())) == [
        {"date": "2026-10-08", "category": "饮食", "digest": "喝了美式咖啡。", "status": "written",
         "verified": ["买美式咖啡1份", "送达美式咖啡", "吃了面"]},
        {"date": "2026-10-08", "category": "工作", "digest": None, "status": "no_records", "verified": []},
        {"date": "2026-10-08", "category": "生活", "digest": None, "status": "unsorted",
         "verified": ["散步 20:00–20:30"]},
        {"date": "2026-10-09", "category": "饮食", "digest": None, "status": "no_records", "verified": []},
        {"date": "2026-10-09", "category": "工作", "digest": "上午处理消息。", "status": "written",
         "verified": ["处理消息 09:00–10:00"]},
        {"date": "2026-10-09", "category": "生活", "digest": None, "status": "no_records", "verified": []}]
    orphan = PilotExportBuilder(tmp_path / "orphan")
    orphan.digest("day", orphan.iso(1), orphan.at(2, "00:00"), {"food": "喝了美式咖啡。"})
    with pytest.raises(EvaluationError, match="^Memory digest request memory-digest:1:day:2026-10-08:attempt:1 "
                                              "does not hold the category entries$"):
        digest_review(load_export(orphan.build()))
    # 请求在、形状不对时同样拒绝：日回顾的输入缺「工作」条目，月回顾的类别缺「次数统计」。
    partial = PilotExportBuilder(tmp_path / "partial")
    payload = _day_payload()
    del payload["工作"]
    partial.digest_request("day", partial.iso(1), partial.at(2, "00:00"), payload)
    partial.digest("day", partial.iso(1), partial.at(2, "00:00"), {"food": "喝了美式咖啡。"})
    with pytest.raises(EvaluationError, match="^Memory digest request memory-digest:1:day:2026-10-08:attempt:1 "
                                              "does not hold the category entries$"):
        digest_review(load_export(partial.build()))
    month = PilotExportBuilder(tmp_path / "month")
    month.digest_request("month", "2026-10", month.at(3, "00:00"),
                         {name: {"日回顾": []} for name in ("饮食", "工作", "生活")})
    month.digest("month", "2026-10", month.at(3, "00:00"), {"life": "这个月常散步。"})
    with pytest.raises(EvaluationError, match="^Memory digest request memory-digest:1:month:2026-10:attempt:1 "
                                              "does not hold the category entries$"):
        m10_digests(*_load(month))

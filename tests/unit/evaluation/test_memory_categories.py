"""记忆类别表（tools/analysis/memory_categories.py）：逐行照抄 row_categories，只加名称、目录字段与理由。

样例环境的副本在 tests/fixtures/dataset-environment 上加行或改行，按生产入口刷新清单；类别规则本身由
tests/unit/memory/test_categories.py 测。样例人物工作日在公司（地点类型 office）上班。
"""

from __future__ import annotations

import json
import logging

from tests.frozen_inputs import fixture_metadata
from tests.life.environment_inputs import catalog_rows, copy_environment, set_content, write_catalog_rows
from tests.unit.memory.test_categories import OFFICE_DOC, add_rows, set_app, without_work_schedule
from tools.analysis.memory_categories import ROW_REASONS, SCHEMA_VERSION, category_table, main
from worldmock.configuration.inputs.environment import load_environment
from worldmock.simulation_engine.observation.memory.categories import category_book, row_categories

#: 规则 0 的理由与表里其余七行理由（{workplace_kind} 已换成样例的 office）。
DECLARED = "目录行写了 memory_category"
REASONS = {
    ("content", 3, "饮食"): "属于食品单元：同一单元有一集写了 completion_effects.food，或凭的是食品状态",
    ("content", 5, "工作"): "在班次地点类型 office：工作日在班次地点开始时归工作，休息日归生活",
    ("content", 7, "生活"): "其余归生活；工作日在岗时段内（不含休息时段）在家以外的地点开始时归工作（规则 5）",
    ("content", 6, "工作"): "前提要求班次地点类型 office",
    ("content", 6, "生活"): "app 内容不要求班次地点，归生活",
    ("app", 6, "工作"): "有一条内容的前提要求班次地点类型 office",
    ("app", 6, "生活"): "没有内容要求班次地点，归生活",
}


def nine_kinds(tmp_path):
    """样例副本，九种行各至少一条：c-chat 写「饮食」覆盖、c-rest 做完供一餐（食品单元）、topic-sales 在 office、
    parcel-service 在驿站、c-office-doc 是前提要求 office 的哔哩哔哩内容、c-article 是无前提的 app 内容；
    app 行里微信写「工作」覆盖、哔哩哔哩含 office 前提的内容、抖音不含。"""
    root = add_rows(copy_environment(tmp_path / "input"), "content-catalog.jsonl", "content_id", OFFICE_DOC)
    set_content(root, "c-chat", memory_category="饮食")
    set_content(root, "c-rest", completion_effects={"food": "meal"})
    set_app(root, "wechat", memory_category="工作")
    return root


def by_id(table):
    """类别表的行按 (行种类, 身份) 取。"""
    return {(row["row"], row["id"]): row for row in table["rows"]}


def test_category_table_copies_row_categories_with_names_and_fields(tmp_path):
    root = nine_kinds(tmp_path)
    table = category_table(root)
    assert list(table) == ["schema_version", "environment_id", "environment_version", "workplace_kind", "rows"]
    assert (table["schema_version"], table["environment_id"], table["environment_version"]) == (
        SCHEMA_VERSION, "artificial-test", "4")
    assert SCHEMA_VERSION == "memory-categories-1"
    assert table["workplace_kind"] == "office"
    derived = row_categories(category_book(load_environment(root, metadata_loader=fixture_metadata)))
    assert [(row["row"], row["id"], row["category"], row["rule"]) for row in table["rows"]] == [
        (row.kind, row.row_id, row.category, row.rule) for row in derived]
    contents = {row["content_id"]: row for row in catalog_rows(root, "content-catalog.jsonl")}
    apps = {row["app_id"]: row for row in catalog_rows(root, "app-catalog.jsonl")}
    assert len(table["rows"]) == len(contents) + len(apps)
    for row in table["rows"]:
        assert list(row) == ["row", "id", "name", "location_kinds", "app_ids", "category", "rule", "reason"]
        if row["row"] == "content":
            source = contents[row["id"]]
            assert (row["name"], row["location_kinds"], row["app_ids"]) == (
                source["title"], source.get("location_kinds", []), source.get("app_ids", []))
        else:
            assert (row["name"], row["location_kinds"], row["app_ids"]) == (apps[row["id"]]["name"], [], [])
    rows = by_id(table)
    assert rows[("content", "c-long")]["app_ids"] == ["bilibili", "douyin"]
    assert rows[("content", "topic-sales")]["location_kinds"] == ["office"]


def test_category_table_gives_each_row_kind_rule_and_category_its_reason(tmp_path):
    rows = by_id(category_table(nine_kinds(tmp_path)))
    expected = {
        ("content", "c-chat"): ("饮食", 0, DECLARED),
        ("content", "c-rest"): ("饮食", 3, REASONS[("content", 3, "饮食")]),
        ("content", "topic-sales"): ("工作", 5, REASONS[("content", 5, "工作")]),
        ("content", "parcel-service"): ("生活", 7, REASONS[("content", 7, "生活")]),
        ("content", "c-office-doc"): ("工作", 6, REASONS[("content", 6, "工作")]),
        ("content", "c-article"): ("生活", 6, REASONS[("content", 6, "生活")]),
        ("app", "wechat"): ("工作", 0, DECLARED),
        ("app", "bilibili"): ("工作", 6, REASONS[("app", 6, "工作")]),
        ("app", "douyin"): ("生活", 6, REASONS[("app", 6, "生活")]),
    }
    assert {key: (rows[key]["category"], rows[key]["rule"], rows[key]["reason"]) for key in expected} == expected
    # 理由表的八行都出现，每一行的理由都是它 (行种类, 规则, 类别) 那一行的文字，班次地点类型已替换。
    assert {row["reason"] for row in rows.values()} == {DECLARED, *REASONS.values()}
    for row in rows.values():
        wanted = DECLARED if row["rule"] == 0 else REASONS[(row["row"], row["rule"], row["category"])]
        assert row["reason"] == wanted
    zero = {(kind, 0, category) for kind in ("content", "app") for category in ("饮食", "工作", "生活")}
    assert set(ROW_REASONS) == zero | set(REASONS)
    assert len(ROW_REASONS) == 13
    assert {ROW_REASONS[key] for key in zero} == {DECLARED}
    assert {key: ROW_REASONS[key].replace("{workplace_kind}", "office") for key in REASONS} == REASONS


def test_a_persona_without_work_schedule_has_no_workplace_kind(tmp_path):
    root = without_work_schedule(add_rows(copy_environment(tmp_path / "input"), "content-catalog.jsonl",
                                          "content_id", OFFICE_DOC))
    table = category_table(root)
    assert table["workplace_kind"] is None
    assert not [row for row in table["rows"] if row["rule"] == 5 or (row["rule"], row["category"]) == (6, "工作")]
    rows = by_id(table)
    assert (rows[("content", "topic-sales")]["category"], rows[("content", "topic-sales")]["rule"]) == ("生活", 7)
    assert [(rows[key]["category"], rows[key]["rule"], rows[key]["reason"])
            for key in (("content", "c-office-doc"), ("app", "bilibili"))] == [
        ("生活", 6, REASONS[("content", 6, "生活")]), ("生活", 6, REASONS[("app", 6, "生活")])]
    assert not any("{workplace_kind}" in row["reason"] or "None" in row["reason"] for row in table["rows"])


def test_cli_prints_or_writes_the_table(tmp_path, capsys, caplog):
    root = nine_kinds(tmp_path)
    assert main(["--package", str(root)]) == 0
    printed = capsys.readouterr().out
    assert printed == json.dumps(category_table(root), ensure_ascii=False, indent=2) + "\n"
    output = tmp_path / "table.json"
    assert main(["--package", str(root), "--output", str(output)]) == 0
    assert capsys.readouterr().out == ""
    assert output.read_bytes() == printed.encode("utf-8")
    caplog.set_level(logging.ERROR)
    caplog.clear()
    assert main(["--package", str(root), "--output", str(output)]) == 1
    assert caplog.messages == [f"memory_categories_input: Output file already exists: {output}"]
    assert output.read_bytes() == printed.encode("utf-8")
    # 包加载失败：目录行写成 null 的覆盖被准入拒绝，错误由加载器写日志，命令行返回 1，不写输出。
    broken = copy_environment(tmp_path / "broken")
    rows = catalog_rows(broken, "app-catalog.jsonl")
    next(row for row in rows if row["app_id"] == "wechat")["memory_category"] = None
    write_catalog_rows(broken, "app-catalog.jsonl", rows)
    caplog.clear()
    missing = tmp_path / "missing.json"
    assert main(["--package", str(broken), "--output", str(missing)]) == 1
    assert any("ENVIRONMENT_KIND_INVALID" in message for message in caplog.messages)
    assert not any(message.startswith("memory_categories_input") for message in caplog.messages)
    assert not missing.exists()
    assert capsys.readouterr().out == ""

"""冻结环境跨完整自然日推进，所有实际决策与计划正常结算。"""
from datetime import timedelta
import pytest
from scripts import render_day
from tests.life.conftest import MAIN_TOOL, SIDE_TOOL, do_at, purchase, stage_day_plan, travel, use_app
from tests.life.environment_runtime import act, act_group, events, finish_activity, run_to_end
from worldmock.footprints.export import export_run
from worldmock.footprints.timeline_labels import label_findings
from worldmock.footprints.validation import validate_export
from tests.integration.runtime_fixtures import write_agent_workspace
from worldmock.footprints.checkpoint import snapshot_directory

@pytest.mark.parametrize("days", [1, 2])
def test_complete_days_reach_declared_stop_without_failure(engine_factory, tmp_path, days):
    def extend(persona, config):
        config["stop_time"] = config["start_time"] + timedelta(days=days)
    engine = engine_factory(mutate=extend)
    stage_day_plan(engine)
    recorded = run_to_end(engine)
    assert engine.context.state.current_time == engine.context.state.end_time
    assert any(row.event_type == "run_ended" for row in recorded)
    assert not any(row.event_type == "run_failed" for row in recorded)
    workspace = write_agent_workspace(tmp_path / "agent")
    engine.checkpoint({"agent:1": snapshot_directory(workspace, engine.context.store.assets)})
    export_run(engine.context.store, engine.context.store.last_event_seq, tmp_path / "export")
    manifest, exported_events, activities = validate_export(tmp_path / "export")
    assert manifest["experiment_completed"] is True and exported_events and activities


def test_day_timeline_of_a_real_day_names_what_each_start_tool_started(engine_factory, tmp_path):
    """完整自然日的真实运行只用两个开始工具开始活动：在家休息并同时刷哔哩哔哩（一个开始组）、买两份柠檬茶、驾车去公司，
    之后步行回家睡到截止。日时间线经 scripts/render_day.py 的命令行入口从运行的 SQLite 投影：每项活动一行统一写法，
    同组开始的两项活动各占一行、同一时刻起；在地点做的事写「在<地点>：<单元名>」，用 app 写 app 名与看过的各条内容，
    购买写商品与订单记录上的下单 app，出行写实际方式与目的地，睡眠写「睡觉」。"""
    def one_day(persona, config):
        config["stop_time"] = config["start_time"] + timedelta(days=1)

    engine = engine_factory(mutate=one_day)
    stage_day_plan(engine)
    _, (rest, watch) = act_group(engine, "home", do_at("家", "测试休息"), use_app("哔哩哔哩"))
    assert (rest.status, watch.status) == ("in_progress", "in_progress"), (rest.error_code, watch.error_code)
    finish_activity(engine, rest, "rest")
    finish_activity(engine, watch, "watch")
    _, bought = act(engine, "buy", purchase("柠檬茶", "柠檬茶"))
    assert bought.status == "in_progress", bought.error_code
    finish_activity(engine, bought, "buy")
    _, drive = act(engine, "drive", travel("公司", "驾车"))
    assert drive.status == "in_progress", drive.error_code
    finish_activity(engine, drive, "drive")
    recorded = run_to_end(engine)
    assert any(row.event_type == "run_ended" for row in recorded)
    starts = [(row.data["tool_name"], row.data["request"]["action"]) for row in events(engine, "action_requested")
              if row.data["tool_name"] in {MAIN_TOOL, SIDE_TOOL}]
    assert starts[:5] == [(MAIN_TOOL, "interact"), (SIDE_TOOL, "interact"), (SIDE_TOOL, "purchase"),
                          (MAIN_TOOL, "travel"), (MAIN_TOOL, "travel")]
    assert starts[5:] and set(starts[5:]) == {(MAIN_TOOL, "sleep")}
    store = engine.context.store
    before = store.last_event_seq
    render_day.main([str(store.run_dir / "footprints.sqlite"), "--agent", "1", "--date", "2026-01-05",
                     "--out", str(tmp_path / "out")])
    assert store.last_event_seq == before
    lines = (tmp_path / "out/timeline/1-2026-01-05.md").read_text(encoding="utf-8").splitlines()
    assert lines[:4] == ["# 人物 1 · 2026-01-05 周一", "", "| 起止 | 活动 | 总时长 | 段数 |", "| --- | --- | --- | --- |"]
    rows = [line.strip("| ").split(" | ") for line in lines[4:]]
    labels = [row[1] for row in rows]
    catalog = engine.context.catalog
    played = engine.context.state.activities[watch.activity_id].data["played"]
    titles = "".join(f"《{catalog.contents[row['content_id']].title}》" for row in played)
    assert titles and all("bilibili" in catalog.contents[row["content_id"]].app_ids for row in played)
    [ordering] = {catalog.apps[order.app_id].name for order in engine.context.state.deliveries.values()}
    assert labels[:5] == ["在家：测试休息", f"使用哔哩哔哩{titles}", f"购买柠檬茶×2（经{ordering}下单）", "驾车去公司",
                          "步行去家"]
    assert labels[5:] and set(labels[5:]) == {"睡觉"}
    # 同组开始的两项活动同一时刻起；在地点做的事照所点名单元的登记时长（900 秒）结束。
    assert rows[0][0] == "08:00–08:15" and rows[0][2] == "15 分钟"
    assert rows[1][0].startswith("08:00–")
    # 一条时间线首尾相接到当日零点：每行的起点不早于上一行的起点，最后一行止于 00:00。
    assert [row[0][:5] for row in rows] == sorted(row[0][:5] for row in rows)
    assert rows[-1][0].endswith("–00:00")
    assert not [(label, found) for label in labels for found in label_findings(label)]
    assert not any(token in "\n".join(lines) for token in ("activity-action", "c-rest", "bilibili", "office", "home"))

"""@brief render_day 投影与 inspect_run._brief 工具名修正的单元测试。"""
import argparse
import importlib.util
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from worldmock.footprints.models import EventDraft
from worldmock.footprints.store import FootprintStore
from worldmock.footprints.timeline_labels import label_findings
from worldmock.life.models import ActivityKind
from worldmock.life.readable import ACTIVITY_KIND_LABELS

REPO_ROOT = Path(__file__).resolve().parents[3]
TZ = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 1, 5)
DAY0 = int(datetime(2026, 1, 5, 0, 0, tzinfo=TZ).timestamp())
#: 当前人工环境两段真实通话与一段工作主题的原始进度、观察和时间线。


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _at(hour, minute, day_offset=0):
    return int(datetime(2026, 1, 5 + day_offset, hour, minute, tzinfo=TZ).timestamp())


def _snapshot(activity_id, kind, **fields):
    payload = {"activity_id": activity_id, "kind": kind, "status": "active"}
    payload.update(fields)
    return payload


def _progress(activity_id, snapshot, start, end, agent=1):
    return EventDraft(event_type="activity_progress", occurred_at=end, agent_id=agent,
                      activity_id=activity_id,
                      data={"interval_start": start, "interval_end": end, "activity": snapshot})


def _observation(data, occurred_at=DAY0, agent=1):
    return EventDraft(event_type="observation", occurred_at=occurred_at, agent_id=agent, data=data)


def _build_store(tmp_path, events, agent_ids=(1, 2)):
    store = FootprintStore.create(tmp_path / "run", {
        "run_id": "run", "agent_ids": list(agent_ids),
        "window": {"start": DAY0, "end": DAY0 + 86400, "timezone": "Asia/Shanghai"},
    }, {"version": 0, "current_time": DAY0})
    store.commit({"version": 1,
                  "current_time": max((item.occurred_at for item in events), default=DAY0)}, events)
    store.close()
    return tmp_path / "run" / "footprints.sqlite"


def _render(sqlite_path, agent, day=DAY):
    module = _load_script("render_day")
    conn = module._connect(sqlite_path)
    try:
        return module.render_day_markdown(
            conn, agent, day, json.loads((sqlite_path.parent / "manifest.json").read_bytes())["window"]["timezone"])
    finally:
        conn.close()


def test_merges_consecutive_progress_of_same_activity(tmp_path):
    """同一活动的连续 progress（含静默边界续行）合并为一行：起止、总时长、段数。"""
    sleep = _snapshot("act-sleep", "sleep")
    wait = _snapshot("act-wait", "sleep")
    events = [
        _progress("act-sleep", sleep, _at(0, 0), _at(2, 0)),
        _progress("act-sleep", sleep, _at(2, 0), _at(4, 0)),
        _progress("act-sleep", sleep, _at(4, 0), _at(7, 0)),
        _progress("act-wait", wait, _at(7, 5), _at(7, 20)),
        _progress("act-wait", wait, _at(7, 20), _at(7, 35)),
        _progress("act-wait", wait, _at(7, 35), _at(7, 50)),
        _progress("act-wait", wait, _at(7, 50), _at(8, 5)),
    ]
    markdown = _render(_build_store(tmp_path, events), 1)
    assert "| 00:00–07:00 | 睡觉 | 7 小时 | 3 |" in markdown
    assert "| 07:05–08:05 | 睡觉 | 1 小时 | 4 |" in markdown
    assert markdown.splitlines()[0] == "# 人物 1 · 2026-01-05 周一"


def test_interleaved_activities_keep_separate_rows(tmp_path):
    """跨活动穿插时同一活动的非连续段不跨断点合并。"""
    wait = _snapshot("act-wait", "sleep")
    browse = _snapshot("act-app", "interact", content_title="推荐文章",
                       data={"action": "interact", "app_id": None, "content_id": "topic"})
    events = [
        _progress("act-wait", wait, _at(7, 5), _at(7, 20)),
        _progress("act-app", browse, _at(7, 20), _at(7, 35)),
        _progress("act-wait", wait, _at(7, 35), _at(8, 0)),
    ]
    markdown = _render(_build_store(tmp_path, events), 1)
    assert "| 07:05–07:20 | 睡觉 | 15 分钟 | 1 |" in markdown
    assert "| 07:20–07:35 | 互动《推荐文章》 | 15 分钟 | 1 |" in markdown
    assert "| 07:35–08:00 | 睡觉 | 25 分钟 | 1 |" in markdown
    assert markdown.index("07:05–07:20") < markdown.index("07:20–07:35") < markdown.index("07:35–08:00")


def test_parallel_interleaved_progress_merges_each_activity_independently(tmp_path):
    """并行结算按同秒 A/B/A/B 交错时，时间线各显示一条完整持续活动。"""
    travel = _snapshot("walk", "travel", travel_mode="walking", destination_id="office")
    watch = _snapshot("watch", "interact", content_title="播客节目",
                      data={"action": "interact", "app_id": None, "content_id": "podcast-topic"})
    events = [
        _progress("walk", travel, _at(7, 0), _at(7, 15)),
        _progress("watch", watch, _at(7, 0), _at(7, 15)),
        _progress("walk", travel, _at(7, 15), _at(7, 30)),
        _progress("watch", watch, _at(7, 15), _at(7, 30)),
    ]
    markdown = _render(_build_store(tmp_path, events), 1)
    assert markdown.count("07:00–07:30") == 2
    assert "| 07:00–07:30 | 步行 | 30 分钟 | 2 |" in markdown
    assert "| 07:00–07:30 | 互动《播客节目》 | 30 分钟 | 2 |" in markdown


def test_resolves_names_instead_of_identifiers(tmp_path):
    """地名、动作、app和内容均由当前观察索引解析，不渲染退役进食或读邮件活动。"""
    observation = _observation({
        "locations": [{"location_id": "office", "name": "公司"}],
        "apps": [{"app_id": "bili", "name": "哔哩哔哩"}],
        "contents": [{"content_id": "drama", "title": "测试剧集"}]})
    travel = _snapshot("go", "travel", destination_id="office", travel_mode="driving")
    watch = _snapshot("watch", "interact", data={"action": "interact", "app_id": "bili", "content_id": "drama"})
    events = [observation, _progress("go", travel, _at(7, 0), _at(7, 5)),
              _progress("watch", watch, _at(19, 13), _at(19, 53))]
    markdown = _render(_build_store(tmp_path, events), 1)
    assert "驾车去公司" in markdown
    assert "使用哔哩哔哩《测试剧集》" in markdown
    assert "browse" not in markdown and "drama" not in markdown


def test_resolves_names_from_seven_layer_observation_evidence_keys(tmp_path):
    """七层观察保留的证据索引读取当前动作，不依赖旧场景投影。"""
    observation = _observation({
        "tool_layer": {"layer": "工具层", "targets": []},
        "todo_layer": {"layer": "待办层", "paused_activities": [], "errands": [], "day_plan": None},
        "locations": [{"location_id": "office", "name": "公司"}],
        "apps": [{"app_id": "bili", "name": "哔哩哔哩"}],
        "contents": [{"content_id": "course", "title": "测试课程"}]})
    events = [observation,
        _progress("go", _snapshot("go", "travel", destination_id="office", travel_mode="walking"),
                  _at(7, 0), _at(7, 5)),
        _progress("course", _snapshot("course", "interact", data={"action": "interact", "app_id": "bili",
            "content_id": "course"}), _at(19, 13), _at(19, 53)),
        _progress("yoga", _snapshot("yoga", "interact", content_title="瑜伽", data={"action": "interact",
            "app_id": None, "content_id": "routine"}), _at(20, 0), _at(20, 30))]
    markdown = _render(_build_store(tmp_path, events), 1)
    assert "步行去公司" in markdown
    assert "使用哔哩哔哩《测试课程》" in markdown
    assert "用手机" not in markdown
    assert "瑜伽" in markdown and "office" not in markdown and "routine" not in markdown


def test_unresolvable_targets_show_only_the_chinese_kind_name(tmp_path):
    """无法解析的身份仅显示中文活动种类，内部身份不能泄露到时间线。"""
    kinds = [kind.value for kind in ActivityKind]
    events = [_observation({})]
    for index, kind in enumerate(kinds):
        snapshot = _snapshot(f"act-{kind}", kind, travel_mode="teleport", destination_id="loc-x",
                             data={"action": "interact", "app_id": None, "content_id": "unresolved-content"})
        events.append(_progress(f"act-{kind}", snapshot, _at(8, index * 2), _at(8, index * 2 + 1)))
    events.append(_progress("odd", _snapshot("odd", "levitate"), _at(9, 0), _at(9, 5)))
    markdown = _render(_build_store(tmp_path, events), 1)
    labels = [line.split(" | ")[1] for line in markdown.splitlines()[4:]]
    assert labels == [ACTIVITY_KIND_LABELS[kind] for kind in kinds] + ["活动"]
    assert all(value not in markdown for value in ("a1b2c3d4", "unresolved-content", "loc-x", "teleport"))
    assert not [(label, found) for label in labels for found in label_findings(label)]


def test_work_and_chat_rows_use_actual_content_titles_from_snapshot_or_catalog(tmp_path):
    """工作与社交使用同一内容合同，真实标题来自快照或内容目录。"""
    observation = _observation({
        "contents": [{"content_id": "work-day", "title": "项目会议纪要"},
                     {"content_id": "chat-topic", "title": "周末计划"}]})
    titled = _snapshot("titled", "interact", content_title="项目会议纪要",
                       data={"action": "interact", "app_id": None, "content_id": "unindexed-topic"})
    indexed = _snapshot("indexed", "interact",
                        data={"action": "interact", "app_id": None, "content_id": "chat-topic"})
    events = [observation, _progress("titled", titled, _at(9, 0), _at(9, 5)),
              _progress("indexed", indexed, _at(9, 10), _at(9, 15))]
    labels = [line.split(" | ")[1] for line in _render(_build_store(tmp_path, events), 1).splitlines()[4:]]
    assert labels == ["互动《项目会议纪要》", "互动《周末计划》"]




def test_output_is_byte_identical_for_same_inputs(tmp_path):
    """确定性：同库同参数两次渲染逐字节相同。"""
    sleep = _snapshot("act-sleep", "sleep")
    events = [_progress("act-sleep", sleep, _at(0, 0), _at(7, 0))]
    sqlite_path = _build_store(tmp_path, events)
    assert _render(sqlite_path, 1) == _render(sqlite_path, 1)


def test_date_filter_and_midnight_clip(tmp_path):
    """按 Asia/Shanghai 日期过滤：跨零点裁剪，别的日期与别的人物不出现。"""
    sleep = _snapshot("act-sleep", "sleep")
    wait = _snapshot("act-other", "sleep")
    events = [
        _progress("act-sleep", sleep, _at(23, 0, day_offset=-1), _at(0, 0)),
        _progress("act-other", wait, _at(9, 0), _at(10, 0), agent=2),
        _progress("act-sleep", sleep, _at(19, 53), _at(7, 0, day_offset=1)),
    ]
    sqlite_path = _build_store(tmp_path, events)
    day_markdown = _render(sqlite_path, 1)
    assert "| 19:53–00:00 | 睡觉 | 4 小时 7 分钟 | 1 |" in day_markdown
    assert "23:00–00:00" not in day_markdown
    assert "| 09:00–10:00" not in day_markdown
    assert _render(sqlite_path, 1, date(2026, 1, 4)).count("|") >= 8
    assert "| 23:00–00:00 | 睡觉 | 1 小时 | 1 |" in _render(sqlite_path, 1, date(2026, 1, 4))
    assert "| 00:00–07:00 | 睡觉 | 7 小时 | 1 |" in _render(sqlite_path, 1, date(2026, 1, 6))
    assert "| 09:00–10:00 | 睡觉 | 1 小时 | 1 |" in _render(sqlite_path, 2)


def test_empty_day_renders_marker(tmp_path):
    """当日无活动时仍产出确定性的空标记文件。"""
    wait = _snapshot("act-other", "sleep")
    events = [_progress("act-other", wait, _at(9, 0), _at(10, 0), agent=2)]
    markdown = _render(_build_store(tmp_path, events), 1)
    assert markdown.splitlines()[0] == "# 人物 1 · 2026-01-05 周一"
    assert "当日无活动记录。" in markdown


def test_cli_writes_timeline_file(tmp_path, capsys):
    """CLI 入参（sqlite、agent、date、输出目录）落盘 timeline/<agent>-<date>.md。"""
    sleep = _snapshot("act-sleep", "sleep")
    events = [_progress("act-sleep", sleep, _at(0, 0), _at(7, 0))]
    sqlite_path = _build_store(tmp_path, events)
    module = _load_script("render_day")
    module.main([str(sqlite_path), "--agent", "1", "--date", "2026-01-05",
                 "--out", str(tmp_path / "out")])
    out_path = tmp_path / "out" / "timeline" / "1-2026-01-05.md"
    assert out_path.is_file()
    content = out_path.read_text(encoding="utf-8")
    assert content.startswith("# 人物 1 · 2026-01-05 周一")
    assert "| 00:00–07:00 | 睡觉 | 7 小时 | 1 |" in content
    assert capsys.readouterr().out.startswith("wrote ")


def test_inspect_run_brief_reads_start_requests_and_result():
    """两个开始工具的请求各显示工具名与完整参数；已结算结果用状态和业务反馈表达。"""
    module = _load_script("inspect_run")
    main = module._brief({"event_type": "action_requested",
                          "data": {"tool_name": "start_main_activity",
                                   "request": {"action": "sleep", "target": "450", "detail": None}}})
    assert main == 'start_main_activity {"action": "sleep", "target": "450", "detail": null}'
    side = module._brief({"event_type": "action_requested",
                          "data": {"tool_name": "start_side_activity",
                                   "request": {"action": "purchase", "target": ["牛肉面", "薯片"]}}})
    assert side == 'start_side_activity {"action": "purchase", "target": ["牛肉面", "薯片"]}'
    accepted = module._brief({"event_type": "action_result",
                              "data": {"status": "in_progress", "error_code": None, "message": "Activities started",
                                       "data": {"activities": [{"kind": "sleep"}], "orders": []}}})
    assert '"status": "in_progress"' in accepted and '"message": "Activities started"' in accepted
    rejected = module._brief({"event_type": "action_result",
                              "data": {"status": "rejected", "error_code": "MERCHANT_CLOSED",
                                       "message": "Merchant closed", "data": {}}})
    assert '"error_code": "MERCHANT_CLOSED"' in rejected


def test_inspect_run_body_reads_values_and_levels(capsys):
    """身体轨迹读观察事件的 body.values 与 body.levels（不再读已删除的告警字段），同值相邻行只打印一次。"""
    module = _load_script("inspect_run")
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE events (event_seq INTEGER PRIMARY KEY, body TEXT)")

    def observation(seq, energy, appetite, agent=1):
        body = {"values": {"energy_kcal": energy, "sleep_deficit_hours": 1.5},
                "levels": {"appetite": appetite, "sleepiness": "困"}}
        return seq, json.dumps({"event_type": "observation", "agent_id": agent, "occurred_at": DAY0 + seq,
                                "data": {"body": body}}, ensure_ascii=False)

    rows = [observation(1, 350.0, "饿得难受"), observation(2, 350.0, "饿得难受"),
            observation(3, 80.0, "饿得发慌"), observation(4, 900.0, "不饿", agent=2)]
    conn.executemany("INSERT INTO events VALUES (?, ?)", rows)
    module.cmd_body(conn, argparse.Namespace(agent=1))
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert "能量=  350.0" in lines[0] and "饥饿=饿得难受" in lines[0] and "困意=困" in lines[0]
    assert "睡眠不足= 1.50h" in lines[0]
    assert "饥饿=饿得发慌" in lines[1]

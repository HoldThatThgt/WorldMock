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

REPO_ROOT = Path(__file__).resolve().parents[2]
TZ = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 1, 5)
DAY0 = int(datetime(2026, 1, 5, 0, 0, tzinfo=TZ).timestamp())
#: BIZ 商务一日运行（4a5b0e6）两段通话与一段工作的原始进度记录、给它们命名的观察行，以及修复前导出的时间线行。
BIZ_TIMELINE = json.loads((REPO_ROOT / "tests" / "fixtures" / "dayrun-biz-timeline.json").read_text(encoding="utf-8"))


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
        return module.render_day_markdown(conn, agent, day)
    finally:
        conn.close()


def test_merges_consecutive_progress_of_same_activity(tmp_path):
    """同一活动的连续 progress（含静默边界续行）合并为一行：起止、总时长、段数。"""
    sleep = _snapshot("act-sleep", "sleep")
    wait = _snapshot("act-wait", "wait")
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
    assert "| 07:05–08:05 | 空档 | 1 小时 | 4 |" in markdown
    assert markdown.splitlines()[0] == "# 人物 1 · 2026-01-05 周一"


def test_interleaved_activities_keep_separate_rows(tmp_path):
    """跨活动穿插时同一活动的非连续段不跨断点合并。"""
    wait = _snapshot("act-wait", "wait")
    browse = _snapshot("act-browse", "browse", target_id="product_catalog")
    events = [
        _progress("act-wait", wait, _at(7, 5), _at(7, 20)),
        _progress("act-browse", browse, _at(7, 20), _at(7, 35)),
        _progress("act-wait", wait, _at(7, 35), _at(8, 0)),
    ]
    markdown = _render(_build_store(tmp_path, events), 1)
    assert "| 07:05–07:20 | 空档 | 15 分钟 | 1 |" in markdown
    assert "| 07:20–07:35 | 浏览商品目录 | 15 分钟 | 1 |" in markdown
    assert "| 07:35–08:00 | 空档 | 25 分钟 | 1 |" in markdown
    assert markdown.index("07:05–07:20") < markdown.index("07:20–07:35") < markdown.index("07:35–08:00")


def test_parallel_interleaved_progress_merges_each_activity_independently(tmp_path):
    """并行结算按同秒 A/B/A/B 交错时，时间线各显示一条完整持续活动。"""
    travel = _snapshot("walk", "travel", travel_mode="walking", destination_id="office")
    watch = _snapshot("watch", "watch", target_id="episode", title="播客节目")
    events = [
        _progress("walk", travel, _at(7, 0), _at(7, 15)),
        _progress("watch", watch, _at(7, 0), _at(7, 15)),
        _progress("walk", travel, _at(7, 15), _at(7, 30)),
        _progress("watch", watch, _at(7, 15), _at(7, 30)),
    ]
    markdown = _render(_build_store(tmp_path, events), 1)
    assert markdown.count("07:00–07:30") == 2
    assert "| 07:00–07:30 | 步行 | 30 分钟 | 2 |" in markdown
    assert "| 07:00–07:30 | 看《播客节目》 | 30 分钟 | 2 |" in markdown


def test_resolves_names_instead_of_identifiers(tmp_path):
    """目标解析为标题/品名/地名/邮件主题：观察事件建名称索引。"""
    observation = _observation({
        "products": [{"product_id": "noodle-01", "name": "红烧牛肉面", "use_modes": ["eat"]}],
        "locations": [{"location_id": "office", "name": "公司"}, {"location_id": "home", "name": "家"}],
        "messages": [{"message_id": "mail-1", "subject": "Work planning"}],
    })
    travel = _snapshot("act-go", "travel", destination_id="office", travel_mode="driving")
    consume = _snapshot("act-eat", "consume", target_id="noodle-01")
    read = _snapshot("act-mail", "read_mail", target_id="mail-1")
    watch = _snapshot("act-watch", "watch", target_id="drama-0002", title="测试剧集")
    events = [
        observation,
        _progress("act-go", travel, _at(7, 0), _at(7, 5)),
        _progress("act-mail", read, _at(8, 0), _at(8, 5)),
        _progress("act-eat", consume, _at(12, 13), _at(12, 28)),
        _progress("act-watch", watch, _at(19, 13), _at(19, 53)),
    ]
    markdown = _render(_build_store(tmp_path, events), 1)
    assert "驾车去公司" in markdown
    assert "吃 红烧牛肉面" in markdown
    assert "读邮件「Work planning」" in markdown
    assert "看《测试剧集》" in markdown


def test_resolves_names_from_seven_layer_observation_evidence_keys(tmp_path):
    """第八版观察（无 scene/facts/recent_action_results，带七层原文与私有绑定）仍由保留的证据键建名称索引。"""
    observation = _observation({
        "tool_layer": {"layer": "工具层", "targets": [{"name": "地点：公司", "kind": "location", "phase": None,
                                                    "state": None, "actions": [], "tools": []}]},
        "todo_layer": {"layer": "待办层", "paused_activities": [], "errands": [], "day_plan": None},
        "environment_layer": {"layer": "环境层", "observed_at": "2026-01-05T07:00:00+08:00",
                              "body": {"appetite": "不饿", "sleepiness": "清醒", "entertainment": "平常",
                                       "social": "平常", "mood": "平静"},
                              "location": {"session_kind": "stay", "name": "家", "place_type": "住宅",
                                           "fixed_rules": []}},
        "memory_layer": {"layer": "记忆层", "earlier_days": [], "earlier_today": []},
        "persona_views": {"relationships": [], "preferences": []},
        "progress_event_ids": [], "presented_message_ids": [],
        "interaction_bindings": [{"object": "地点：公司", "kind": "location", "object_id": "office", "actions": []}],
        "products": [{"product_id": "noodle-01", "name": "红烧牛肉面", "use_modes": ["eat"]}],
        "locations": [{"location_id": "office", "name": "公司"}],
        "messages": [{"message_id": "mail-1", "subject": "Work planning"}],
        "content_progress": {"drama-0002": {"title": "测试剧集"}},
    })
    browsed = EventDraft(event_type="action_result", occurred_at=_at(6, 0), agent_id=1,
                         data={"status": "completed", "items": [{"activity_id": "yoga", "name": "瑜伽"}]})
    events = [
        observation, browsed,
        _progress("act-go", _snapshot("act-go", "travel", destination_id="office", travel_mode="walking"),
                  _at(7, 0), _at(7, 5)),
        _progress("act-mail", _snapshot("act-mail", "read_mail", target_id="mail-1"), _at(8, 0), _at(8, 5)),
        _progress("act-eat", _snapshot("act-eat", "consume", target_id="noodle-01"), _at(12, 13), _at(12, 28)),
        _progress("act-watch", _snapshot("act-watch", "watch", target_id="drama-0002"), _at(19, 13), _at(19, 53)),
        _progress("act-yoga", _snapshot("act-yoga", "lifestyle", target_id="yoga"), _at(20, 0), _at(20, 30)),
    ]
    markdown = _render(_build_store(tmp_path, events), 1)
    assert "去公司" in markdown
    assert "读邮件「Work planning」" in markdown
    assert "吃 红烧牛肉面" in markdown
    assert "看《测试剧集》" in markdown
    assert "瑜伽" in markdown
    assert "office" not in markdown and "noodle-01" not in markdown


def test_unresolvable_targets_show_only_the_chinese_kind_name(tmp_path):
    """名称解析不到时只写中文种类名：每个活动种类都不输出英文种类名、内部标识或其片段（docs/dev/SPEC-dayrun-fidelity.md
    7.4）。"""
    hex_id = "a1b2c3d4" * 4
    targets = {"watch": hex_id, "play_game": "game-0001", "read_mail": "mail-9", "consume": "noodle-01",
               "browse": "catalog-x", "lifestyle": "lifestyle:yoga", "work": "task-zhaoming-client-a"}
    data = {"call": {"contact_id": "contact-zhaoming-colleague"}, "chat": {"partner_id": "agent:2"}}
    kinds = [kind.value for kind in ActivityKind]
    events = [_observation({})]
    for index, kind in enumerate(kinds):
        snapshot = _snapshot(f"act-{kind}", kind, target_id=targets.get(kind), data=data.get(kind, {}),
                             travel_mode="teleport" if kind == "travel" else None, destination_id="loc-x")
        events.append(_progress(f"act-{kind}", snapshot, _at(8, index * 2), _at(8, index * 2 + 1)))
    events.append(_progress("act-odd", _snapshot("act-odd", "levitate"), _at(9, 0), _at(9, 5)))
    markdown = _render(_build_store(tmp_path, events), 1)
    labels = [line.split(" | ")[1] for line in markdown.splitlines()[4:]]
    assert labels == [ACTIVITY_KIND_LABELS[kind] for kind in kinds] + ["活动"]
    for text in (*targets.values(), *data["call"].values(), *data["chat"].values(), "#b2c3d4", "loc-x", "teleport"):
        assert text not in markdown
    assert not [(label, found) for label in labels for found in label_findings(label)]


def test_work_call_and_chat_rows_name_the_task_contact_and_person(tmp_path):
    """工作、通话、交谈写任务标题、联系人称谓与人物姓名：先取引擎写在快照上的标题；旧运行的快照没有标题时，由名称索引
    （观察的 tasks、联系人与人物的私有绑定）解析。"""
    observation = _observation({
        "tasks": [{"task_id": "task-2026-01-05", "title": "1月5日项目会议纪要"}],
        "interaction_bindings": [{"object": "联系人：王磊", "kind": "contact", "object_id": "contact-wanglei",
                                  "actions": []}],
        "person_bindings": [{"name": "人物：李凡", "person_id": "agent:2"}],
    })
    titled = [_snapshot("act-work", "work", target_id="task-2026-01-05", title="1月5日项目会议纪要"),
              _snapshot("act-call", "call", data={"contact_id": "contact-mother"}, title="陈母"),
              _snapshot("act-chat", "chat", data={"partner_id": "agent:2"}, title="李凡")]
    indexed = [_snapshot("old-work", "work", target_id="task-2026-01-05"),
               _snapshot("old-call", "call", data={"contact_id": "contact-wanglei"}),
               _snapshot("old-chat", "chat", data={"partner_id": "agent:2"})]
    events = [observation] + [_progress(item["activity_id"], item, _at(9, index * 10), _at(9, index * 10 + 5))
                              for index, item in enumerate(titled + indexed)]
    markdown = _render(_build_store(tmp_path, events), 1)
    labels = [line.split(" | ")[1] for line in markdown.splitlines()[4:]]
    assert labels == ["工作「1月5日项目会议纪要」", "和陈母通话", "和李凡交谈",
                      "工作「1月5日项目会议纪要」", "和王磊通话", "和李凡交谈"]


def test_real_biz_run_rows_name_the_calls_and_the_work_task(tmp_path):
    """BIZ 一日运行的原始记录（快照不带标题）重新投影：修复前导出的「call」「工作（task-zhaoming-client-a）」写成联系人
    称谓与任务标题；标签检查对修复前的行报英文种类名与内部标识，对重新投影的行为零（docs/dev/SPEC-dayrun-fidelity.md 7.4）。"""
    observation = BIZ_TIMELINE["observation"]
    drafts = [_observation(observation["data"], occurred_at=observation["occurred_at"], agent=5)]
    drafts += [EventDraft(event_type="activity_progress", occurred_at=item["occurred_at"], agent_id=5,
                          activity_id=item["data"]["activity"]["activity_id"], data=item["data"])
               for item in BIZ_TIMELINE["progress"]]
    sqlite_path = _build_store(tmp_path, sorted(drafts, key=lambda item: item.occurred_at), agent_ids=(5,))
    rows = [line.split(" | ")[:2] for line in _render(sqlite_path, 5, date(2026, 9, 28)).splitlines()[4:]]
    assert [(interval.removeprefix("| "), label) for interval, label in rows] == [
        ("09:15–09:30", "和刘洋通话"), ("09:48–10:03", "和张总通话"), ("10:40–11:15", "工作「整理客户A拜访纪要」")]
    old = [line.split(" | ")[1] for line in BIZ_TIMELINE["exported_rows"]]
    assert old == ["call", "call", "工作（task-zhaoming-client-a）"]
    assert [label_findings(label) for label in old] == [
        [("english_kind_name", "call")], [("english_kind_name", "call")], [("internal_id", "task-zhaoming-client-a")]]
    assert not [found for _, label in rows for found in label_findings(label)]


def test_output_is_byte_identical_for_same_inputs(tmp_path):
    """确定性：同库同参数两次渲染逐字节相同。"""
    sleep = _snapshot("act-sleep", "sleep")
    events = [_progress("act-sleep", sleep, _at(0, 0), _at(7, 0))]
    sqlite_path = _build_store(tmp_path, events)
    assert _render(sqlite_path, 1) == _render(sqlite_path, 1)


def test_date_filter_and_midnight_clip(tmp_path):
    """按 Asia/Shanghai 日期过滤：跨零点裁剪，别的日期与别的人物不出现。"""
    sleep = _snapshot("act-sleep", "sleep")
    wait = _snapshot("act-other", "wait")
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
    assert "| 09:00–10:00 | 空档 | 1 小时 | 1 |" in _render(sqlite_path, 2)


def test_empty_day_renders_marker(tmp_path):
    """当日无活动时仍产出确定性的空标记文件。"""
    wait = _snapshot("act-other", "wait")
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


def test_inspect_run_brief_reads_start_activity_request_and_result():
    """请求显示可读对象、动词与方式；已结算结果用状态和业务反馈表达。"""
    module = _load_script("inspect_run")
    requested = module._brief({"event_type": "action_requested",
                               "data": {"tool_name": "start_activity",
                                        "request": {"targets": ["自己"], "actions": ["interact"],
                                                    "ways": ["等待"]}}})
    assert requested.startswith('start_activity {"targets": ["自己"], "actions": ["interact"], "ways": ["等待"]}')
    accepted = module._brief({"event_type": "action_result",
                              "data": {"status": "in_progress", "error_code": None,
                                       "message": "Activity started", "data": {"activity": {"kind": "wait"}}}})
    assert '"status": "in_progress"' in accepted
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

"""@brief 一天时间线的统一写法（经真实请求、引擎与 SQLite）、render_day 投影的合并与过滤，以及 inspect_run 的两种读法。"""
import argparse
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from scripts import inspect_run, render_day
from tests.footprints.display_fixtures import freeze_environment, sample_display_names
from tests.life.conftest import (
    advance_next, conclude_request, do_at, pause_request, purchase, query, report_request, travel, use_app,
)
from tests.life.environment_inputs import (
    catalog_rows, copy_environment, replace_travel_methods, travel_method_row, write_catalog_rows,
)
from tests.life.environment_runtime import act, conclude_app_use, events, finish_activity, settle_decision
from tests.life.prerequisite_inputs import add_rows
from worldmock.footprints.models import EventDraft
from worldmock.footprints.store import FootprintStore
from worldmock.footprints.timeline_labels import label_findings

TZ = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 1, 5)
DAY0 = int(datetime(2026, 1, 5, 0, 0, tzinfo=TZ).timestamp())
#: 夹具副本给住处加的两集系列：系列名与两集的标题。
DINNER = ("做晚饭", "备菜做饭", "吃晚饭")


def _at(hour, minute, day_offset=0):
    return int(datetime(2026, 1, 5 + day_offset, hour, minute, tzinfo=TZ).timestamp())


def _snapshot(activity_id, kind, location_id="home", **fields):
    """当前形状的活动快照：带地点与活动数据，写法只读其中的种类、地点和数据。"""
    payload = {"activity_id": activity_id, "kind": kind, "status": "active", "location_id": location_id,
               "data": fields.pop("data", {})}
    payload.update(fields)
    return payload


def _watching(activity_id, content_id):
    """用哔哩哔哩看一条内容的活动快照。"""
    return _snapshot(activity_id, "interact",
                     data={"action": "interact", "app_id": "bilibili", "content_id": content_id})


def _progress(activity_id, snapshot, start, end, agent=1):
    return EventDraft(event_type="activity_progress", occurred_at=end, agent_id=agent,
                      activity_id=activity_id,
                      data={"interval_start": start, "interval_end": end, "activity": snapshot})


def _build_store(tmp_path, events, agent_ids=(1, 2), environment=None):
    """建一个运行目录：冻结样例环境（或给定的环境副本）的素材，提交给定的事件。"""
    assets = freeze_environment(tmp_path / "run", **({} if environment is None else {"root": environment}))
    store = FootprintStore.create(tmp_path / "run", {
        "run_id": "run", "agent_ids": list(agent_ids), "environment_assets": assets,
        "window": {"start": DAY0, "end": DAY0 + 86400, "timezone": "Asia/Shanghai"},
    }, {"version": 0, "current_time": DAY0})
    store.commit(store.state, {"version": 1,
                               "current_time": max((item.occurred_at for item in events), default=DAY0)}, events)
    store.close()
    return tmp_path / "run" / "footprints.sqlite"


def _render(sqlite_path, agent, day=DAY):
    conn = render_day._connect(sqlite_path)
    try:
        return render_day.render_day_markdown(
            conn, agent, day, json.loads((sqlite_path.parent / "manifest.json").read_bytes())["window"]["timezone"],
            sample_display_names())
    finally:
        conn.close()


def _timeline(engine, out):
    """经命令行入口从真实运行的 SQLite 写出 2026-01-05 的时间线，返回各行（起止、活动、总时长、段数）。"""
    render_day.main([str(engine.context.store.run_dir / "footprints.sqlite"), "--agent", "1", "--date", "2026-01-05",
                     "--out", str(out)])
    lines = (out / "timeline" / "1-2026-01-05.md").read_text(encoding="utf-8").splitlines()
    return [line.strip("| ").split(" | ") for line in lines[4:]]


def _labels(engine, out, prefix):
    """时间线里以给定文字开头的各行的活动一栏，按时间先后。"""
    return [row[1] for row in _timeline(engine, out) if row[1].startswith(prefix)]


def _drama_only(tmp_path):
    """夹具副本：哔哩哔哩只留「测试剧集」三集（每集 30 分钟），别的内容解除与它的绑定。"""
    root = copy_environment(tmp_path / "drama-environment")
    rows = []
    for row in catalog_rows(root, "content-catalog.jsonl"):
        if "bilibili" in row.get("app_ids", []) and not row["content_id"].startswith("c-drama-"):
            row = {**row, "app_ids": [app for app in row["app_ids"] if app != "bilibili"]}
            if not row["app_ids"]:
                continue
        rows.append(row)
    write_catalog_rows(root, "content-catalog.jsonl", rows)
    return root


def _report_every_quarter_hour(persona, config):
    """配置副本：每 15 分钟一次播报机会，用 app 看的时候每集的一半处都有一次决策。"""
    config["progress_report_interval_seconds"] = 900


def _ready_after(engine, moment):
    """逐边界推进到 moment 之后第一次有决策机会的那一秒；途中的决策机会只播报进行中的活动。"""
    while True:
        if engine.scheduling_state().ready_agent_ids:
            if engine.context.state.current_time > moment:
                return
            settle_decision(engine, f"report-{engine.context.state.current_time}")
        else:
            advance_next(engine)


def _played(engine, activity_id):
    """这项活动各条进度事件结算的内容身份，按事件先后。"""
    return [row.data["content"]["content_id"] for row in events(engine, "activity_progress")
            if row.activity_id == activity_id and "content" in row.data]


def test_app_use_row_names_the_app_and_every_item_in_order(engine_factory, tmp_path):
    """用哔哩哔哩放完至少一条后收尾：一行「使用哔哩哔哩」接这项活动各条进度事件结算的内容标题（相邻重复只写一次），
    多于三条时只写前三条再写「等 K 条」，标签检查零发现。哔哩哔哩只留三集的副本里，一次使用放完第一集、
    第二集放到一半时结束：一行列出两集，没有系列名、没有「未完」，跨两个结算段的第一集只写一次；暂停再恢复得到两行，各列各的。"""
    engine = engine_factory()
    _, watched = act(engine, "watch", use_app("哔哩哔哩"))
    assert watched.status == "in_progress", watched.error_code
    conclude_app_use(engine, watched.activity_id, "watch")
    played = _played(engine, watched.activity_id)
    titles = [engine.context.catalog.contents[key].title
              for index, key in enumerate(played) if index == 0 or key != played[index - 1]]
    listed = "".join(f"《{title}》" for title in titles[:3])
    [label] = _labels(engine, tmp_path / "plain", "使用")
    assert label == f"使用哔哩哔哩{listed if len(titles) <= 3 else f'{listed}等 {len(titles)} 条'}"
    assert titles and label_findings(label) == []

    drama = _drama_only(tmp_path)
    engine = engine_factory(environment_root=drama, mutate=_report_every_quarter_hour)
    started_at = engine.context.state.current_time
    _, series = act(engine, "series", use_app("哔哩哔哩"))
    _ready_after(engine, started_at + 1800)
    assert engine.context.state.agents[1].content_progress["c-drama-2"].position_seconds > 0
    name = engine.context.state.activities[series.activity_id].name
    _, ended = act(engine, "end-series", conclude_request(name), "conclude_activity")
    assert ended.status == "completed", ended.error_code
    played = _played(engine, series.activity_id)
    assert played.count("c-drama-1") >= 2 and played[-1] == "c-drama-2"
    assert _labels(engine, tmp_path / "series", "使用") == ["使用哔哩哔哩《测试剧集第一集》《测试剧集第二集》"]

    engine = engine_factory(environment_root=drama, mutate=_report_every_quarter_hour)
    _, resumed = act(engine, "media", use_app("哔哩哔哩"))
    while not engine.scheduling_state().ready_agent_ids:
        advance_next(engine)
    name = engine.context.state.activities[resumed.activity_id].name
    act(engine, "pause", pause_request(name), "pause_activity")
    for label, place in (("away", "公司"), ("back", "家")):
        _, trip = act(engine, label, travel(place))
        finish_activity(engine, trip, label)
    back_at = engine.context.state.current_time
    act(engine, "resume", report_request(name), "report_status")
    _ready_after(engine, back_at + 900)
    act(engine, "end-media", conclude_request(name), "conclude_activity")
    assert _labels(engine, tmp_path / "paused", "使用") == [
        "使用哔哩哔哩《测试剧集第一集》", "使用哔哩哔哩《测试剧集第一集》《测试剧集第二集》"]


def test_rows_list_what_was_played_not_the_next_item_in_the_snapshot(engine_factory, tmp_path, capsys):
    """推进到第一集放完的那一秒，就在这一秒结束：最后一条进度事件的快照已是第二集、data.content 是第一集；时间线这一行
    只有第一集，inspect_run 对这条进度事件写的也是第一集。"""
    engine = engine_factory(environment_root=_drama_only(tmp_path))
    started_at = engine.context.state.current_time
    _, series = act(engine, "series", use_app("哔哩哔哩"))
    while not engine.scheduling_state().ready_agent_ids:
        advance_next(engine)
    assert engine.context.state.current_time == started_at + 1800
    last = [row for row in events(engine, "activity_progress") if row.activity_id == series.activity_id][-1]
    assert last.data["activity"]["data"]["content_id"] == "c-drama-2"
    assert last.data["content"]["content_id"] == "c-drama-1" and last.data["content"]["completed"] is True
    name = engine.context.state.activities[series.activity_id].name
    _, ended = act(engine, "end", conclude_request(name), "conclude_activity")
    assert ended.status == "completed", ended.error_code
    assert _labels(engine, tmp_path / "out", "使用") == ["使用哔哩哔哩《测试剧集第一集》"]
    with inspect_run._connect(engine.context.store.run_dir) as conn:
        inspect_run.cmd_events(conn, argparse.Namespace(run_dir=engine.context.store.run_dir,
                                                        type="activity_progress", agent=1, tail=0, full=False))
    [line] = [line for line in capsys.readouterr().out.splitlines() if line.startswith(f"[{last.event_seq:>5}]")]
    assert line.endswith(" 使用哔哩哔哩《测试剧集第一集》 → active")


def test_location_rows_name_the_place_the_unit_and_the_series_episodes(engine_factory, tmp_path):
    """在家做「测试休息」是「在家：测试休息」；住处加分店名后是「在家（老宅）：测试休息」；住处加两集的系列，做完是
    「在家：做晚饭《备菜做饭》《吃晚饭》」。"""
    engine = engine_factory()
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    finish_activity(engine, rest, "rest")
    assert _labels(engine, tmp_path / "plain", "在") == ["在家：测试休息"]

    branch = copy_environment(tmp_path / "branch-environment")
    places = catalog_rows(branch, "location.jsonl")
    next(row for row in places if row["location_id"] == "home")["branch_label"] = "老宅"
    write_catalog_rows(branch, "location.jsonl", places)

    def at_branch(persona, config):
        persona["home_location"] = persona["initial_location"] = "家（老宅）"

    engine = engine_factory(environment_root=branch, mutate=at_branch)
    _, rest = act(engine, "rest", do_at("家（老宅）", "测试休息"))
    finish_activity(engine, rest, "rest")
    assert _labels(engine, tmp_path / "branch", "在") == ["在家（老宅）：测试休息"]

    series = copy_environment(tmp_path / "series-environment")
    title, *episodes = DINNER
    source = {"kind": "synthetic", "notice": "测试用合成摘要", "url": None}
    add_rows(series, "content-catalog.jsonl", "content_id", *(
        {"content_id": f"c-dinner-{number}", "duration_seconds": 600, "exertion": "light",
         "location_kinds": ["home"], "pushable": False, "relaxation_relief": "none",
         "series": {"episode": number, "title": title}, "social_relief": "none", "summary": f"{episode}。",
         "summary_source": source, "title": episode} for number, episode in enumerate(episodes, start=1)))
    engine = engine_factory(environment_root=series)
    _, dinner = act(engine, "dinner", do_at("家", title))
    assert dinner.status == "in_progress", dinner.error_code
    finish_activity(engine, dinner, "dinner")
    assert _labels(engine, tmp_path / "series", "在") == ["在家：做晚饭《备菜做饭》《吃晚饭》"]


def test_purchase_row_is_split_by_order_with_its_channel(engine_factory, tmp_path):
    """商品与渠道取订单记录：在家买牛肉面写订单记录上的下单 app；同名两份写「×2」；只在 app 里卖的平台笔记本写经哔哩哔哩
    下单；在四季便利买纸巾和饭团是一张到店订单；在便利店一次买纸巾和牛肉面分两段；提前结束的购买照写同一句。"""
    def report_early(persona, config):
        config["progress_report_interval_seconds"] = 840

    engine = engine_factory(mutate=report_early)
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    while not engine.scheduling_state().ready_agent_ids:
        advance_next(engine)
    _, early = act(engine, "early", purchase("牛肉面"))
    while not engine.scheduling_state().ready_agent_ids:
        advance_next(engine)
    assert engine.context.state.activities[early.activity_id].status == "active"
    names = [engine.context.state.activities[key].name for key in (rest.activity_id, early.activity_id)]
    act(engine, "end", conclude_request(*names), "conclude_activity")
    assert engine.context.state.activities[early.activity_id].status == "cancelled"
    for label, goods in (("noodles", ("牛肉面",)), ("teas", ("柠檬茶", "柠檬茶")), ("notebook", ("平台笔记本",))):
        _, bought = act(engine, label, purchase(*goods))
        assert bought.status == "in_progress", bought.error_code
        finish_activity(engine, bought, label)
    apps = {order.goods_id: engine.context.catalog.apps[order.app_id].name
            for order in engine.context.state.deliveries.values()}
    assert apps["online-notebook"] == "哔哩哔哩"
    assert _labels(engine, tmp_path / "home", "购买") == [
        f"购买牛肉面（经{apps['beef-noodles']}下单）", f"购买牛肉面（经{apps['beef-noodles']}下单）",
        f"购买柠檬茶×2（经{apps['lemon-tea']}下单）", "购买平台笔记本（经哔哩哔哩下单）"]

    def at_store(persona, config):
        persona["initial_location"] = "四季便利"

    engine = engine_factory(mutate=at_store)
    for label, goods in (("pickup", ("纸巾", "饭团")), ("split", ("纸巾", "牛肉面"))):
        _, bought = act(engine, label, purchase(*goods))
        assert bought.status == "in_progress", bought.error_code
        finish_activity(engine, bought, label)
    assert {(order.goods_id, order.app_id) for order in engine.context.state.deliveries.values()} == {
        ("tissue", None), ("rice-ball", None), ("beef-noodles", "meituan")}
    # 到店订单也经付款 app 付款：渠道只看订单记录的下单 app，付款 app 不进渠道。
    assert all(order.payment_app_id is not None for order in engine.context.state.deliveries.values())
    assert _labels(engine, tmp_path / "store", "购买") == ["购买饭团、纸巾（到店）", "购买牛肉面（经美团下单）；纸巾（到店）"]


def test_purchase_row_reports_the_actual_channel_when_prefer_cannot_be_used(engine_factory, tmp_path):
    """渠道不取 prefer：牛肉面只有一心面馆能送、那家店没上饿了么，写「饿了么」时订单落在美团；写一个不存在的「美团外卖」时
    环境另选（柠檬茶有两个下单 app 在卖，选到哪个都可以）。两行都写订单记录上的下单 app，不出现各自请求里所写的名称。"""
    engine = engine_factory()
    for label, goods, prefer in (("noodles", "牛肉面", "饿了么"), ("tea", "柠檬茶", "美团外卖")):
        _, bought = act(engine, label, purchase(goods, prefer=prefer))
        assert bought.status == "in_progress", bought.error_code
        finish_activity(engine, bought, label)
    apps = {order.goods_id: order.app_id for order in engine.context.state.deliveries.values()}
    assert apps["beef-noodles"] == "meituan" and apps["lemon-tea"] is not None
    labels = _labels(engine, tmp_path / "out", "购买")
    assert labels == ["购买牛肉面（经美团下单）",
                      f"购买柠檬茶（经{engine.context.catalog.apps[apps['lemon-tea']].name}下单）"]
    noodles, tea = labels
    assert "饿了么" not in noodles and "美团外卖" not in tea


def test_query_row_names_what_was_looked_up_and_the_app(engine_factory, tmp_path, capsys):
    """真实引擎做完一次带 app 的查询和一次没有 app 的查询：时间线各一行，名称按所写次序（类别写中文类别名），
    带 app 的加「（<app 名>）」，没有 app 的不加括号，标签检查零发现；inspect_run 的活动进度行写同一句。"""
    engine = engine_factory()
    looked = []
    for label, request in (("meals", query("食品", "柠檬茶", prefer="美团")), ("tissue", query("纸巾"))):
        _, started = act(engine, label, request)
        assert started.status == "in_progress", started.error_code
        looked.append(finish_activity(engine, started, label))
    assert [(item.kind, item.status, item.data["app_id"]) for item in looked] == [
        ("query", "completed", "meituan"), ("query", "completed", None)]
    labels = _labels(engine, tmp_path / "out", "查询")
    assert labels == ["查询食品、柠檬茶（美团）", "查询纸巾"]
    assert not [(label, found) for label in labels for found in label_findings(label)]
    with inspect_run._connect(engine.context.store.run_dir) as conn:
        inspect_run.cmd_events(conn, argparse.Namespace(run_dir=engine.context.store.run_dir,
                                                        type="activity_progress", agent=1, tail=0, full=False))
    lines = capsys.readouterr().out.splitlines()
    for item, phrase in zip(looked, labels):
        *_, last = [row.event_seq for row in events(engine, "activity_progress") if row.activity_id == item.activity_id]
        [line] = [line for line in lines if line.startswith(f"[{last:>5}]")]
        assert line.endswith(f" {phrase} → completed")


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
    browse = _watching("act-app", "c-article")
    events = [
        _progress("act-wait", wait, _at(7, 5), _at(7, 20)),
        _progress("act-app", browse, _at(7, 20), _at(7, 35)),
        _progress("act-wait", wait, _at(7, 35), _at(8, 0)),
    ]
    markdown = _render(_build_store(tmp_path, events), 1)
    assert "| 07:05–07:20 | 睡觉 | 15 分钟 | 1 |" in markdown
    assert "| 07:20–07:35 | 使用哔哩哔哩《测试文章》 | 15 分钟 | 1 |" in markdown
    assert "| 07:35–08:00 | 睡觉 | 25 分钟 | 1 |" in markdown
    assert markdown.index("07:05–07:20") < markdown.index("07:20–07:35") < markdown.index("07:35–08:00")


def test_parallel_interleaved_progress_merges_each_activity_independently(tmp_path):
    """并行结算按同秒 A/B/A/B 交错时，时间线各显示一条完整持续活动。"""
    walk = _snapshot("walk", "travel", travel_method_id="walking", destination_id="office")
    watch = _watching("watch", "c-course")
    events = [
        _progress("walk", walk, _at(7, 0), _at(7, 15)),
        _progress("watch", watch, _at(7, 0), _at(7, 15)),
        _progress("walk", walk, _at(7, 15), _at(7, 30)),
        _progress("watch", watch, _at(7, 15), _at(7, 30)),
    ]
    markdown = _render(_build_store(tmp_path, events), 1)
    assert markdown.count("07:00–07:30") == 2
    assert "| 07:00–07:30 | 步行去公司 | 30 分钟 | 2 |" in markdown
    assert "| 07:00–07:30 | 使用哔哩哔哩《测试课程》 | 30 分钟 | 2 |" in markdown


def test_travel_rows_show_the_dataset_name_of_the_travel_method(tmp_path):
    """出行行写冻结的 travel-method.jsonl 里出行方式的名称和目的地的显示名；自带的步行不在文件里，写「步行」。"""
    environment = replace_travel_methods(copy_environment(tmp_path / "environment"), [
        travel_method_row("bus", "公共交通", 5), travel_method_row("ferry-line-2", "二号线轮渡", 4)],
        ["bus", "ferry-line-2", "walking"])
    trips = [("walking", "office"), ("bus", "home"), ("ferry-line-2", "park")]
    events = [
        _progress(f"trip-{index}", _snapshot(f"trip-{index}", "travel", destination_id=destination,
                                             travel_method_id=method, data={"action": "travel"}),
                  _at(7 + index, 0), _at(7 + index, 10)) for index, (method, destination) in enumerate(trips)]
    sqlite_path = _build_store(tmp_path, events, environment=environment)
    render_day.main([str(sqlite_path), "--agent", "1", "--date", "2026-01-05", "--out", str(tmp_path / "out")])
    lines = (tmp_path / "out" / "timeline" / "1-2026-01-05.md").read_text(encoding="utf-8").splitlines()
    labels = [line.split(" | ")[1] for line in lines[4:]]
    assert labels == ["步行去公司", "公共交通去家", "二号线轮渡去五里公园"]
    assert not [(label, found) for label in labels for found in label_findings(label)]


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
    """命令行入参（sqlite、agent、date、输出目录）落盘 timeline/<agent>-<date>.md：名称取运行目录里冻结的环境目录，
    写出的字节等于 render_day_markdown 的 UTF-8 字节，任何平台上都没有 \\r。"""
    sleep = _snapshot("act-sleep", "sleep")
    events = [_progress("act-sleep", sleep, _at(0, 0), _at(7, 0)),
              _progress("act-app", _watching("act-app", "c-image"), _at(7, 0), _at(7, 5))]
    sqlite_path = _build_store(tmp_path, events)
    render_day.main([str(sqlite_path), "--agent", "1", "--date", "2026-01-05", "--out", str(tmp_path / "out")])
    out_path = tmp_path / "out" / "timeline" / "1-2026-01-05.md"
    content = out_path.read_bytes()
    assert content == _render(sqlite_path, 1).encode("utf-8") and b"\r" not in content
    text = content.decode("utf-8")
    assert text.startswith("# 人物 1 · 2026-01-05 周一")
    assert "| 00:00–07:00 | 睡觉 | 7 小时 | 1 |" in text and "| 07:00–07:05 | 使用哔哩哔哩《测试图文》 | 5 分钟 | 1 |" in text
    assert capsys.readouterr().out.startswith("wrote ")


def test_inspect_run_brief_reads_start_requests_and_result():
    """两个开始工具的请求各显示工具名与完整参数；已结算结果用状态和业务反馈表达。"""
    display = inspect_run.Display(sample_display_names(), {}, TZ)
    main = inspect_run._brief({"event_type": "action_requested", "resource_changes": {},
                               "data": {"tool_name": "start_main_activity",
                                        "request": {"action": "sleep", "target": "450", "detail": None}}}, display)
    assert main == 'start_main_activity {"action": "sleep", "target": "450", "detail": null}'
    side = inspect_run._brief({"event_type": "action_requested", "resource_changes": {},
                               "data": {"tool_name": "start_side_activity",
                                        "request": {"action": "purchase", "target": ["牛肉面", "薯片"],
                                                    "prefer": None}}}, display)
    assert side == 'start_side_activity {"action": "purchase", "target": ["牛肉面", "薯片"], "prefer": null}'
    accepted = inspect_run._brief({"event_type": "action_result", "resource_changes": {},
                                   "data": {"status": "in_progress", "error_code": None,
                                            "message": "Activities started",
                                            "data": {"activities": [{"kind": "sleep"}], "orders": []}}}, display)
    assert '"status": "in_progress"' in accepted and '"message": "Activities started"' in accepted
    rejected = inspect_run._brief({"event_type": "action_result", "resource_changes": {},
                                   "data": {"status": "rejected", "error_code": "MERCHANT_CLOSED",
                                            "message": "Merchant closed", "data": {}}}, display)
    assert '"error_code": "MERCHANT_CLOSED"' in rejected


def test_inspect_run_body_reads_values_and_levels(tmp_path, capsys):
    """身体轨迹读观察事件的 body.values 与 body.levels（不再读已删除的告警字段），同值相邻行只打印一次；时刻按运行时区。"""
    def observation(offset, energy, appetite, agent=1):
        body = {"values": {"energy_kcal": energy, "sleep_deficit_hours": 1.5},
                "levels": {"appetite": appetite, "sleepiness": "困"}}
        return EventDraft(event_type="observation", occurred_at=DAY0 + offset, agent_id=agent, data={"body": body})

    sqlite_path = _build_store(tmp_path, [observation(1, 350.0, "饿得难受"), observation(2, 350.0, "饿得难受"),
                                          observation(3, 80.0, "饿得发慌"), observation(4, 900.0, "不饿", agent=2)])
    with inspect_run._connect(sqlite_path.parent) as conn:
        inspect_run.cmd_body(conn, argparse.Namespace(run_dir=sqlite_path.parent, agent=1))
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert "01-05 00:00:01 a1" in lines[0]
    assert "能量=  350.0" in lines[0] and "饥饿=饿得难受" in lines[0] and "困意=困" in lines[0]
    assert "睡眠不足= 1.50h" in lines[0]
    assert "饥饿=饿得发慌" in lines[1]

"""全单元代表 target_episode 挂起解锁、next_episode 与暂停一致、全耗尽无未读内容与一次性地点锁定。"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tests.life.conftest import (
    action_rows,
    advance_next,
    conclude_request,
    context,
    do_at,
    pause_request,
    target_actions,
    target_tools,
    tool_targets,
    travel,
    use_app,
)
from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from tests.life.environment_runtime import act, finish_activity, reach, settle_decision
from worldmock.foundation.randomness import random_stream
from worldmock.simulation_engine.activities.content.content_select import (
    ContentRequest,
    autoplay_content,
    eligible_contents,
    next_episode,
)
from worldmock.simulation_engine.activities.content.location_activities import unit_locked
from worldmock.simulation_engine.observation.wording.content_prerequisite import (
    app_prerequisite_refusal,
    app_time_state,
    prerequisite_unmet,
)
from worldmock.simulation_engine.scheduling.callbacks.app_push import _push_candidates, issue_app_push
from worldmock.state.planning.events import ScheduledEvent


def _synth_content(content_id: str, title: str, duration: int, **extra) -> dict:
    """@brief 构造带合成摘要的内容行。"""
    return {
        "content_id": content_id, "duration_seconds": duration, "exertion": "light", "pushable": False,
        "relaxation_relief": "none", "social_relief": "none", "summary": f"{title}的测试摘要。",
        "summary_source": {"kind": "synthetic", "notice": "测试用合成摘要", "url": None}, "title": title, **extra,
    }


def _series_and_repeatable_env(root, *, include_b_office: bool = True, repeatable_app: bool = False):
    """@brief 构造跨城分集系列、异地城市办公楼、本地多城办公楼及一次性地点单元的隔离环境。"""
    locs = catalog_rows(root, "location.jsonl")
    if include_b_office:
        locs.append({"app_ids": [], "city_id": "city-b", "kind": "office",
                     "location_id": "b-office", "name": "乙城分部"})
        write_catalog_rows(root, "location.jsonl", sorted(locs, key=lambda r: r["location_id"]))
        write_catalog_rows(root, "map.jsonl", [
            {"a": "b-office", "b": "home", "length_m": 10000, "travel_method_ids": ["driving"]},
        ])

    # Keep 仅留单次笔记，哔哩哔哩仅留跨城专题课，抖音仅留受地点类型限制的本地专题课，互不干扰。
    base_rows = [row for row in catalog_rows(root, "content-catalog.jsonl")
                 if row.get("app_ids") not in (["keep"], ["bilibili"], ["douyin"]) and row["content_id"] != "c-long"]
    keep_rows = [
        _synth_content("k-once-a", "单次笔记", 120, app_ids=["keep"], pushable=True, repeatable=repeatable_app),
    ]
    bili_rows = [
        _synth_content("s-cross-1", "跨城课第一集", 240, app_ids=["bilibili"], pushable=True,
                       repeatable=False, series={"episode": 1, "title": "跨城专题课"}),
        _synth_content("s-cross-2", "跨城课第二集", 240, app_ids=["bilibili"], pushable=True,
                       repeatable=False, series={"episode": 2, "title": "跨城专题课"},
                       prerequisite={"city_ids": ["city-b"], "location_kinds": ["office"],
                                     "opens": {"weekly": {"mon": [["10:00", "12:00"]]}}}),
        _synth_content("s-cross-3", "跨城课第三集", 240, app_ids=["bilibili"], pushable=True,
                       repeatable=False, series={"episode": 3, "title": "跨城专题课"}),
    ]
    douyin_rows = [
        _synth_content("s-other-1", "本地课第一集", 240, app_ids=["douyin"], pushable=True,
                       repeatable=True, series={"episode": 1, "title": "本地专题课"},
                       prerequisite={"location_kinds": ["office"]}),
    ]
    loc_rows = [
        _synth_content("loc-once", "一次性整理", 120, location_kinds=["home"], repeatable=False),
        _synth_content("loc-step-1", "第一步", 300, location_kinds=["home"],
                       repeatable=False, series={"episode": 1, "title": "实验步骤"}),
        _synth_content("loc-step-2", "第二步", 300, location_kinds=["home"],
                       repeatable=False, series={"episode": 2, "title": "实验步骤"}),
    ]
    all_rows = sorted([*base_rows, *keep_rows, *bili_rows, *douyin_rows, *loc_rows], key=lambda r: r["content_id"])
    write_catalog_rows(root, "content-catalog.jsonl", all_rows)
    return root


def _start_at(place_name: str, *, hour: int = 9, minute: int = 0, feedback: int = 60):
    """@brief 返回给定起点、启动时刻与反馈步长的配置函数。"""
    def mutate(persona, config):
        persona["initial_location"] = place_name
        config["activity_feedback_seconds"] = feedback
        config["reconsideration_interval_seconds"] = feedback
        config["start_time"] = datetime(2026, 1, 5, hour, minute, tzinfo=ZoneInfo("Asia/Shanghai"))
        config["stop_time"] = config["start_time"] + timedelta(hours=6)
    return mutate


def test_full_unit_target_episode_suspends_on_locked_episode_and_unlocks(engine_factory, tmp_path):
    """@brief 系列按完整单元定代表集：第2集未解锁时挂起整部系列（既不跳播第3集也不重播第1集），到目标城时段后解锁。"""
    root = _series_and_repeatable_env(copy_environment(tmp_path / "env"))
    engine = engine_factory(environment_root=root, mutate=_start_at("家", hour=9, minute=0))
    # 09:00 在家打开 哔哩哔哩：唯一代表集是第 1 集（前提为空），完整播放完。
    _, res1 = act(engine, "play-ep1", use_app("哔哩哔哩"))
    assert res1.status == "in_progress"
    act1 = engine.context.state.activities[res1.activity_id]
    assert act1.data["content_id"] == "s-cross-1"
    # 到达第 1 集结束时第 2 集受 city-b/office/10:00 阻断：next_episode 返回 None，自动停止，绝不跳播第 3 集。
    while engine.context.state.activities[res1.activity_id].status == "active":
        if engine.scheduling_state().ready_agent_ids:
            settle_decision(engine, f"ep1-step-{engine.context.state.current_time}")
        else:
            advance_next(engine)
    played_ep1 = [row["content_id"] for row in engine.context.state.activities[res1.activity_id].data["played"]]
    assert played_ep1 == ["s-cross-1"]
    _, end1 = act(engine, "conclude-ep1", conclude_request(act1.name), "conclude_activity")
    assert end1.status == "completed"

    # 现在第 1 集已完成 1 轮，第 2 集代表集挂起：eligible_contents 必须为空，工具层与拒载只列出第 2 集的「乙城分部」及 10:00–12:00。
    state = engine.context.state
    agent = state.agents[1]
    assert eligible_contents(engine.context.catalog, state, ContentRequest(1, "bilibili", "home")) == ()
    local_0904 = datetime.fromtimestamp(state.current_time, ZoneInfo(engine.context.config.timezone))
    assert prerequisite_unmet(
        engine.context.catalog.contents["s-cross-2"], engine.context.catalog.locations["home"], local_0904,
    ) == ("content_location", "content_time")
    assert prerequisite_unmet(
        engine.context.catalog.contents["s-cross-2"], engine.context.catalog.locations["office"], local_0904,
    ) == ("content_location", "content_time")
    assert app_time_state(engine.context.catalog, "bilibili",
                          engine.context.catalog.locations["home"], local_0904, agent) == "available 10:00–12:00 today"

    _, blocked_home = act(engine, "open-while-suspended", use_app("哔哩哔哩"))
    assert blocked_home.status == "rejected" and blocked_home.error_code == "CONTENT_PREREQUISITE_UNMET"
    assert blocked_home.data["reason"] == (
        "Nothing in 哔哩哔哩 can start here now: it requires presence at 乙城分部 during 10:00–12:00 today."
    )
    assert blocked_home.data["next_step"] == "Go to 乙城分部."
    assert blocked_home.data["unmet"] == ["content_location", "content_time"]

    # 驾车去乙城分部（约 09:27 到达 < 10:00）：地点已匹配，仅余时段未到（无 here，unmet 仅含 content_time）。
    _, drive = act(engine, "to-b-office", travel("乙城分部", "驾车"))
    finish_activity(engine, drive, "arrived-b-office")
    _, blocked_early = act(engine, "open-before-10", use_app("哔哩哔哩"))
    assert blocked_early.status == "rejected" and blocked_early.error_code == "CONTENT_PREREQUISITE_UNMET"
    assert blocked_early.data["reason"] == "Nothing in 哔哩哔哩 can start now: it is available 10:00–12:00 today."
    assert blocked_early.data["unmet"] == ["content_time"]

    # 在乙城分部开始原地工作直到 10:00:00 后再打开 哔哩哔哩：第 2 集解锁并立即自动接续第 3 集直至第 3 集放完。
    t1000 = int(datetime(2026, 1, 5, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
    _, work_wait = act(engine, "wait-at-b-office", do_at("乙城分部", "季度销售数据整理"))
    assert work_wait.status == "in_progress"
    reach(engine, t1000)
    work_name = engine.context.state.activities[work_wait.activity_id].name
    _, stopped = act(engine, "stop-work", conclude_request(work_name), "conclude_activity")
    assert stopped.status == "completed"

    _, unlock = act(engine, "unlock-bili", use_app("哔哩哔哩"))
    assert unlock.status == "in_progress"
    assert engine.context.state.activities[unlock.activity_id].data["content_id"] == "s-cross-2"
    while engine.context.state.activities[unlock.activity_id].status == "active":
        if engine.scheduling_state().ready_agent_ids:
            settle_decision(engine, f"ep23-step-{engine.context.state.current_time}")
        else:
            advance_next(engine)
    played_ids = [item["content_id"] for item in engine.context.state.activities[unlock.activity_id].data["played"]]
    assert played_ids == ["s-cross-2", "s-cross-3"]


def test_autoplay_and_push_candidates_respect_unit_target_episode_and_pushable(engine_factory, tmp_path):
    """@brief app_push 与 autoplay_content 均读取完整单元的 target_episode 及可推送资格，不推不播被锁后序集。"""
    root = _series_and_repeatable_env(copy_environment(tmp_path / "env"))
    rows = catalog_rows(root, "content-catalog.jsonl")
    # 给 哔哩哔哩 加一部已开始系列（「无推系列」，第 1 集 pushable=False 尚未读，第 2 集 pushable=True）检验不可推送代表集。
    rows.append(_synth_content("s-nopush-1", "无推第一集", 120, app_ids=["bilibili"],
                               pushable=False, repeatable=True, series={"episode": 1, "title": "无推系列"}))
    rows.append(_synth_content("s-nopush-2", "无推第二集", 120, app_ids=["bilibili"],
                               pushable=True, repeatable=True, series={"episode": 2, "title": "无推系列"}))
    write_catalog_rows(root, "content-catalog.jsonl", sorted(rows, key=lambda r: r["content_id"]))

    engine = engine_factory(environment_root=root, mutate=_start_at("家", hour=9, minute=0))
    catalog = engine.context.catalog
    # 把 s-cross-1 标记为已读 1 轮（代表集变成需乙城与10点的 s-cross-2）；无推系列代表集是 pushable=False 的 s-nopush-1。
    state = engine.context.state.model_copy(deep=True)
    from worldmock.state.world.agent import ContentProgress

    state.agents[1].content_progress["s-cross-1"] = ContentProgress(
        content_id="s-cross-1", completed_rounds=1, position_seconds=0.0, last_consumed_at=state.current_time - 60,
    )
    candidates = _push_candidates(state, catalog, 1)
    assert "bilibili" not in candidates
    assert "keep" in candidates

    # 把 s-cross-1 当作在刚放完时的串联场景：即使 s-nopush-2 与 s-cross-3 可推，代表集分别是不可推/未解锁，autoplay 为 None。
    auto = autoplay_content(
        catalog, state, ContentRequest(1, "bilibili", "home", previous_content_id="s-cross-1"),
        random_stream(state.random_state, "auto:1"),
    )
    assert auto is None
    # 发出定时推送同样跳过 哔哩哔哩 而投递给 Keep。
    ev = ScheduledEvent(scheduled_id="push-check", kind="app_push", due_at=state.current_time,
                        sequence=1, agent_id=1, data={})
    _, notice = issue_app_push(state, ev, catalog)
    assert notice["app_id"] == "keep" and notice["content_id"] == "k-once-a"


def test_exhausted_repeatable_app_displays_no_unread_and_refuses_while_local_city_clause_leads(
    engine_factory, tmp_path,
):
    """@brief repeatable:false 全耗尽 app 状态显示无未读内容并拒载；未耗尽差地点时优先只列当前城市匹配地点。"""
    root = _series_and_repeatable_env(copy_environment(tmp_path / "env"), repeatable_app=False)
    engine = engine_factory(environment_root=root, mutate=_start_at("家", hour=9, minute=0))

    # 1. 本地多城均存在 office（公司 与 乙城分部）：在 家 检验 抖音（s-other-1 仅要求 office），仅列本城「公司」，在路上才列全城。
    state0 = engine.context.state
    local_0900 = datetime.fromtimestamp(state0.current_time, ZoneInfo(engine.context.config.timezone))
    refusal_home = app_prerequisite_refusal(
        engine.context.catalog, "douyin", engine.context.catalog.locations["home"], local_0900, state0.agents[1],
    )
    assert refusal_home is not None and "公司" in refusal_home.reason and "乙城分部" not in refusal_home.reason
    refusal_road = app_prerequisite_refusal(engine.context.catalog, "douyin", None, local_0900, state0.agents[1])
    assert refusal_road is not None and "公司" in refusal_road.reason and "乙城分部" in refusal_road.reason

    # 2. Keep 只剩 1 条 repeatable=False 的单次笔记：看完一次并收尾。
    _, play_k = act(engine, "use-keep-once", use_app("Keep"))
    assert play_k.status == "in_progress"
    finish_activity(engine, play_k, "done-keep-once")

    dec = context(engine, "obs-exhausted")
    engine.begin_decision_round([dec])
    obs = engine.observe(dec)
    keep_target = tool_targets(obs)["Keep"]
    assert keep_target["state"] == "无未读内容"
    interact_act = target_actions(obs, "Keep")["interact"]
    assert interact_act["available"] is False
    assert interact_act["reason"] == "All content in Keep has already been finished."
    engine.abort_decision_round("obs-exhausted", "inspection")

    _, refused = act(engine, "reopen-exhausted-keep", use_app("Keep"))
    assert refused.status == "rejected" and refused.error_code == "CONTENT_PREREQUISITE_UNMET"
    assert refused.data["reason"] == "All content in Keep has already been finished."
    assert refused.data["next_step"] == "Choose another action." and refused.data.get("fix") is None


def test_location_repeatable_unit_locked_and_pause_under_three_minutes_matches_next_episode(engine_factory, tmp_path):
    """@brief 地点单元暂停提示与 next_episode 严格一致，repeatable:false 做完一轮后工具层隐藏且受理报已读完（无消耗状态）。"""
    root = _series_and_repeatable_env(copy_environment(tmp_path / "env"))
    # feedback=180s 使 300s 单元在 +180s 停表（余 120s < 180s），精确检验 3 分钟内暂停提示。
    engine = engine_factory(environment_root=root, mutate=_start_at("家", hour=9, minute=0, feedback=180))

    # 系列「实验步骤」第 1 集做到余 120 秒：next_episode 返回第 2 集，工具层与暂停拒绝均说之后继续下一集。
    _, st1 = act(engine, "start-step1", do_at("家", "实验步骤"))
    act_s1 = engine.context.state.activities[st1.activity_id]
    while not engine.scheduling_state().ready_agent_ids:
        advance_next(engine)
    dec1 = context(engine, "obs-pause-s1")
    engine.begin_decision_round([dec1])
    obs1 = engine.observe(dec1)
    pause_row1 = target_tools(obs1, act_s1.name)["pause_activity"]
    assert pause_row1["available"] is False and "continues with the next episode" in pause_row1["reason"]
    engine.abort_decision_round("obs-pause-s1", "inspection")

    _, pause1 = act(engine, "try-pause-s1", pause_request(act_s1.name), "pause_activity")
    assert pause1.status == "rejected" and pause1.error_code == "ACTIVITY_PAUSE_UNAVAILABLE"
    assert pause1.data["reason"] == pause_row1["reason"]
    assert next_episode(
        engine.context.catalog, engine.context.state.agents[1],
        engine.context.catalog.contents["loc-step-1"], 1,
        (engine.context.catalog.locations["home"],
         datetime.fromtimestamp(engine.context.state.current_time, ZoneInfo(engine.context.config.timezone))),
    ).content_id == "loc-step-2"
    while engine.context.state.activities[st1.activity_id].data["content_id"] == "loc-step-1":
        if engine.scheduling_state().ready_agent_ids:
            settle_decision(engine, f"step1-wait-{engine.context.state.current_time}")
        else:
            advance_next(engine)
    act_s2 = engine.context.state.activities[st1.activity_id]
    assert act_s2.data["content_id"] == "loc-step-2"
    while (not engine.scheduling_state().ready_agent_ids
           or act_s2.planned_end_at - engine.context.state.current_time >= 180):
        if engine.scheduling_state().ready_agent_ids:
            settle_decision(engine, f"step2-wait-{engine.context.state.current_time}")
        else:
            advance_next(engine)
    _, pause2 = act(engine, "try-pause-s2", pause_request(act_s2.name), "pause_activity")
    assert pause2.status == "rejected" and pause2.error_code == "ACTIVITY_PAUSE_UNAVAILABLE"
    assert pause2.data["reason"] == f"The activity {act_s2.name} ends in under three minutes and cannot be paused."
    finish_activity(engine, st1, "finish-step2")

    # 再将无消耗状态的独立一次性地点单元「一次性整理」做完 1 次；完成后整套「实验步骤」与「一次性整理」由 locked_refusal 安全拒载而非查空键。
    try:
        _, once = act(engine, "start-loc-once", do_at("家", "一次性整理"))
        finish_activity(engine, once, "finish-loc-once")

        assert unit_locked(
            [engine.context.catalog.contents["loc-step-1"], engine.context.catalog.contents["loc-step-2"]],
            engine.context.state.agents[1],
        ) is True
        assert unit_locked([engine.context.catalog.contents["loc-once"]], engine.context.state.agents[1]) is True

        dec_end = context(engine, "obs-loc-locked")
        engine.begin_decision_round([dec_end])
        obs_end = engine.observe(dec_end)
        details_at_home = {row["detail"] for row in action_rows(obs_end, "家", "interact")}
        assert "实验步骤" not in details_at_home and "一次性整理" not in details_at_home
        engine.abort_decision_round("obs-loc-locked", "inspection")

        for detail in ("实验步骤", "一次性整理"):
            _, ref = act(engine, f"retry-{detail}", do_at("家", detail))
            assert ref.status == "rejected" and ref.error_code == "ACTIVITY_PRECONDITION_UNMET"
            assert ref.data["reason"] == f"{detail} has already been finished."
            assert ref.data["unmet"] == ["content_repeatable"]
    except KeyError as error:
        raise AssertionError(f"locked_refusal dereferenced None callback_state: {error}") from error

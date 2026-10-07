"""@brief 当前动作契约的真实引擎决策、推进、检查点与重开闭环。"""

from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from tests.life.conftest import advance_next, context, submit_round
from worldmock.life.models import StartActivityRequest


def request(target="刷推荐", app="抖音", way="进行", to=None, payment=None):
    """@brief 完整请求不包含任何旧来源、偏好或内容参数。"""
    return StartActivityRequest.model_validate({"items": [{"target": target, "way": way, "app": app,
        "to": to, "pay_app": payment}], "answering": None})


def test_real_action_decision_progress_checkpoint_and_reopen(engine_factory):
    """@brief 真实受理只选一次，正秒结算持久化，再开保持选择与跨应用进度。"""
    from worldmock.life.engine import LifeEngine

    engine = engine_factory()
    _, results = submit_round(engine, {1: ("start_activity", request())}, "start")
    result = results[0]
    assert result.error_code is None
    assert result.data["content"]["action"] == "刷推荐"
    assert result.data["content"]["app"] == "抖音"
    activity = engine.context.state.activities[result.activity_id]
    assert activity.name.startswith("刷推荐（抖音）（")
    assert len(activity.data) == 11
    advance_next(engine)
    activity = engine.context.state.activities[result.activity_id]
    assert activity.elapsed_seconds > 0
    assert engine.context.state.agents[1].content_progress
    snapshots = {f"agent:{identity}": engine.context.store.assets.put_json(agent.model_dump(mode="json"),
                 {"kind": "test_agent_snapshot"}) for identity, agent in engine.context.state.agents.items()}
    checkpoint = engine.checkpoint(snapshots)
    expected = engine.context.state.model_dump(mode="json")
    from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT
    scenario = ARTIFICIAL_ENVIRONMENT_ROOT / "scenarios" / "single-person.json"
    run_dir = engine.context.store.run_dir
    reopened = LifeEngine.open(scenario, run_dir)
    try:
        assert reopened.context.state.model_dump(mode="json") == expected
        assert reopened.context.state.model_dump(mode="json") == checkpoint.state
        assert "inventory" not in expected["agents"]["1"]
        assert "contact_holdings" not in expected
    finally:
        reopened.close()


def test_tool_observation_lists_actions_and_app_choices_without_consuming(engine_factory):
    """@brief 工具行的时长与应用只读展示，观察不写内容进度或随机状态。"""
    engine = engine_factory()
    before = deepcopy(engine.context.state.model_dump(mode="json"))
    observation = engine.observe(context(engine, "read"))
    targets = observation["tool_layer"]["targets"]
    actions = [row for row in targets if row["kind"] == "action"]
    assert {row["name"] for row in actions} >= {"刷推荐", "安静休息", "办公"}
    feed = next(row for row in actions if row["name"] == "刷推荐")
    assert {row["app"] for row in feed["actions"]} == {"抖音", "哔哩哔哩"}
    assert all(row["duration"] for row in feed["actions"])
    assert all(row["kind"] not in {"app", "task", "lifestyle", "message", "holding"} for row in targets)
    assert set(observation["tool_layer"]["callable_index"]) == {
        "goods", "navigation_apps", "payment_apps", "query_categories"}
    after = engine.context.state.model_dump(mode="json")
    assert after["random_state"] == before["random_state"]
    assert after["agents"]["1"]["content_progress"] == before["agents"]["1"]["content_progress"]


def _registered_scenario(tmp_path, custom=None):
    """@brief 在完整人工环境副本中发布明确动作、应用、内容，再交正式加载入口。"""
    from tests.life.scenario_fixtures import read_scenario, write_scenario
    from worldmock.catalog.identity import refresh_manifest_identity

    document = read_scenario("single-person.json")
    document.update(start_at="2026-01-05T08:58:00+08:00", end_at="2026-01-05T12:00:00+08:00",
                    progress_report_interval_seconds=60, messages=[], bills=[], payrolls=[])
    path = write_scenario(document, tmp_path / "environment")
    if custom is not None:
        action_name, app_name, manner = custom
        catalog = path.parent.parent / "catalog"
        declarations = {
            "apps": [{"app_id": "custom-app", "name": app_name, "purposes": [], "payment_app_ids": []}],
            "actions": [{"action_id": "custom-action", "name": action_name, "description": "环境明确登记的娱乐动作。",
                         "category": "entertainment", "app_ids": ["custom-app"], "manner": manner,
                         "communication_mode": None}],
            "contents": [{"content_id": f"custom-content-{index}", "action_id": "custom-action",
                          "app_ids": ["custom-app"], "title": f"{action_name}测试内容{index}",
                          "summary": "环境维护的测试合成内容。", "duration_seconds": 3600, "series": None,
                          "summary_source": {"kind": "synthetic", "url": None, "notice": "测试合成素材"}}
                         for index in range(2)],
        }
        for kind, rows in declarations.items():
            filename = catalog / f"{kind}.json"
            payload = json.loads(filename.read_bytes())
            payload[kind].extend(rows)
            identity = {"apps": "app_id", "actions": "action_id", "contents": "content_id"}[kind]
            payload[kind].sort(key=lambda row: row[identity])
            filename.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        refresh_manifest_identity(catalog)
    return path, document


def _assert_current_content_evidence(engine, activity_id, result):
    """@brief 状态、结果、实际消费与推送只保留动作绑定，不带内容分类。"""
    activity = engine.context.state.activities[activity_id]
    catalog = engine.context.semantics.catalog
    content = catalog.contents[activity.data["content_id"]]
    assert "kind" not in content.model_dump(mode="json")
    assert not {"content_kind", "content_type", "kind"}.intersection(activity.data)
    assert set(result.data["content"]) == {"action", "app", "title", "summary", "duration", "pushed"}
    progress = [event.data["content"] for event in engine.context.store.read_agent_events("activity_progress", 1)
                if event.activity_id == activity_id]
    assert progress and all(row["position_after_seconds"] > row["position_before_seconds"] for row in progress)
    assert sum(row["position_after_seconds"] - row["position_before_seconds"] for row in progress) == activity.elapsed_seconds
    for row in progress:
        assert not {"kind", "content_kind", "content_type"}.intersection(row)
    pushes = [event.data for event in engine.context.store.read_events(engine.context.store.last_event_seq)
              if event.data.get("kind") == "app_push" and event.data.get("app_id") == activity.data["app_id"]]
    if activity.data["manner"] == "phone_continuous":
        assert pushes and all(row["action_id"] == activity.data["action_id"] for row in pushes)
    else:
        assert not pushes
    assert all(not {"content_kind", "content_type"}.intersection(row) for row in pushes)
    for session in engine.context.state.sessions.values():
        if activity.data["app_id"] in session.app_pushes:
            assert "kind" not in session.app_pushes[activity.data["app_id"]].model_dump(mode="json")


@pytest.mark.parametrize("custom", [
    ("玩游戏", "测试游戏应用", "other_device"),
    ("听音乐", "测试音乐应用", "phone_continuous"),
    ("轻松播客", "测试播客应用", "phone_continuous"),
], ids=["game", "music", "podcast"])
def test_custom_game_music_and_podcast_execute_real_content_and_continue_after_reopen(tmp_path, custom):
    """@brief 自定义娱乐名称经真实发布、受理、正秒结算与恢复，不依赖系统内容分类。"""
    from tests.life.test_acs_amended_runtime import checkpoint, perform, pump
    from tests.life.scenario_fixtures import run_identity
    from worldmock.life.engine import LifeEngine

    path, document = _registered_scenario(tmp_path, custom)
    engine = LifeEngine.create(path, tmp_path / "run", run_identity(document, "custom-content"))
    try:
        started = perform(engine, "custom-start", request(custom[0], custom[1]))
        assert started.error_code is None, started.data
        assert started.data["content"]["action"] == custom[0] and started.data["content"]["app"] == custom[1]
        push_at = engine.context.state.current_time + 120
        pump(engine, lambda: engine.context.state.current_time >= push_at)
        activity = engine.context.state.activities[started.activity_id]
        assert 0 < activity.media_position_seconds < 3600 and activity.elapsed_seconds > 0
        _assert_current_content_evidence(engine, started.activity_id, started)
        point = checkpoint(engine)
        expected = engine.context.state.model_dump(mode="json")
        environment = engine.context.store.manifest["environment_identity"]
        position, content_id, elapsed = activity.media_position_seconds, activity.data["content_id"], activity.elapsed_seconds
        run_dir = engine.context.store.run_dir
        engine.close()
        engine = LifeEngine.open(path, run_dir)
        assert engine.context.state.model_dump(mode="json") == expected == point.state
        assert engine.context.store.manifest["environment_identity"] == environment
        pump(engine, lambda: engine.context.state.activities[started.activity_id].elapsed_seconds > elapsed)
        resumed = engine.context.state.activities[started.activity_id]
        assert resumed.data["content_id"] == content_id and resumed.media_position_seconds > position
        _assert_current_content_evidence(engine, started.activity_id, started)
        assert not engine.context.store.read_agent_events("run_failed", 1)
    finally:
        engine.close()


def test_first_registered_environment_loop_rest_feed_reopen_and_delivery(tmp_path):
    """@brief 首个闭环从无应用动作到娱乐接续中途恢复，再按实际送达时刻收货。"""
    from tests.life.conftest import conclude_request
    from tests.life.test_acs_amended_runtime import checkpoint, perform, pump
    from tests.life.scenario_fixtures import run_identity
    from worldmock.life.engine import LifeEngine

    path, document = _registered_scenario(tmp_path)
    engine = LifeEngine.create(path, tmp_path / "run", run_identity(document, "first-action-loop"))
    try:
        rested = perform(engine, "rest-start", request("安静休息", None))
        assert rested.error_code is None, rested.data
        pump(engine, lambda: engine.context.state.activities[rested.activity_id].status != "active")
        assert engine.context.state.activities[rested.activity_id].elapsed_seconds > 0
        ended = perform(engine, "rest-end", conclude_request(engine.context.state.activities[rested.activity_id].name),
                        "conclude_activity")
        assert ended.error_code is None, ended.data
        feed = perform(engine, "feed-start", request())
        assert feed.error_code is None, feed.data
        pump(engine, lambda: engine.context.state.activities[feed.activity_id].data["draw_index"] >= 1
             and engine.context.state.activities[feed.activity_id].media_position_seconds > 0)
        activity = engine.context.state.activities[feed.activity_id]
        assert activity.media_position_seconds < engine.context.semantics.catalog.contents[activity.data["content_id"]].duration_seconds
        point = checkpoint(engine)
        expected = engine.context.state.model_dump(mode="json")
        environment = engine.context.store.manifest["environment_identity"]
        run_dir = engine.context.store.run_dir
        engine.close()
        engine = LifeEngine.open(path, run_dir)
        assert engine.context.state.model_dump(mode="json") == expected == point.state
        assert engine.context.store.manifest["environment_identity"] == environment
        stopped = perform(engine, "feed-end", conclude_request(engine.context.state.activities[feed.activity_id].name),
                          "conclude_activity")
        assert stopped.error_code is None, stopped.data
        purchased = perform(engine, "online-order", request("薯片", "美团", "购买", payment="支付宝"))
        assert purchased.error_code is None, purchased.data
        [order] = purchased.data["orders"]
        pump(engine, lambda: engine.context.state.deliveries[order["order_id"]].status == "delivered")
        receipts = [event for event in engine.context.store.read_agent_events("environment_event", 1)
                    if event.data.get("kind") == "goods_received" and event.data["order_id"] == order["order_id"]]
        assert len(receipts) == 1 and receipts[0].occurred_at == order["expected_at"]
        assert not engine.context.store.read_agent_events("run_failed", 1)
    finally:
        engine.close()


@pytest.mark.parametrize("field, value", [("from", None), ("duration_seconds", 60), ("content", "test")])
def test_retired_request_fields_are_rejected(field, value):
    """@brief 旧请求键不能被当前模型接受或静默丢弃。"""
    document = request().model_dump(mode="json")
    document["items"][0][field] = value
    with pytest.raises(ValidationError):
        StartActivityRequest.model_validate(document)

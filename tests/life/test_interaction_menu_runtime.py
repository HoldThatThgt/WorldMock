"""交互菜单在真实场景加载、观察、共同结算与 SQLite 上的行为验收。"""

from __future__ import annotations

import copy
import json

import pytest
from agentsociety2.society import DirectToolCall

from tests.life.conftest import advance_next, advance_until_ready, context, stage_day_plan, travel_quote, wake_by_mail
from tests.life.test_commerce import _browse, _delivery_request, _observed_action, _stage_open
from tests.life.test_scheduling import _custom_engine
from worldmock.errors import IntegrityError
from worldmock.life.engine import LifeEngine
from worldmock.life.models import (
    BrowseActivitiesRequest,
    ConsumeItem,
    ConsumeRequest,
    ContinueActivityRequest,
    MeetingMinutes,
    SendMessageRequest,
    SetActivityStateRequest,
    StartBasicActivityRequest,
    StartTargetedActivityRequest,
    StartTravelRequest,
    SubmitWorkRequest,
)


def _restrict(document, *, tools=(), kinds=(), location="home", agent=None):
    """在真实场景输入上显式缩窄一个地点或人物的菜单。"""
    menu = copy.deepcopy(document["interaction_menus_by_location_kind"][location])
    menu["allowed_tools"] = [name for name in menu["allowed_tools"] if name not in tools]
    menu["allowed_activity_kinds"] = [kind for kind in menu["allowed_activity_kinds"] if kind not in kinds]
    if agent is None:
        document["interaction_menus_by_location_kind"][location] = menu
    else:
        document["agents"][agent]["interaction_menu"] = menu


def _engine(tmp_path, mutate, base="white-collar-12-weeks.json"):
    """使用完整场景解析与目录装配创建被测运行。"""
    return _custom_engine(base, mutate, tmp_path, run_id="interaction-menu")


def _domain_state(engine):
    """提取拒绝前后不得被菜单改变的领域状态。"""
    state = engine.state.model_dump(mode="json")
    return {"accounts": state["accounts"], "activities": state["activities"],
            "supply_stock": state["supply_stock"],
            "agents": {key: {name: value[name] for name in (
                "location_id", "inventory", "game_sessions", "content_progress", "current_activity_id")}
                       for key, value in state["agents"].items()}}


@pytest.mark.parametrize("tool,values,blocked_tools,blocked_kinds", [
    ("start_targeted_activity", {"activity_type": "play_game", "target_id": "game",
     "planned_duration_seconds": 60, "playback_speed": 1}, (), ("play_game",)),
    ("start_basic_activity", {"activity_type": "lifestyle:walk", "planned_duration_seconds": 60},
     (), ("lifestyle",)),
    ("start_travel", {"destination_id": "office", "travel_mode": "walking", "planned_duration_seconds": 60},
     (), ("travel",)),
    ("browse_activities", {"category": "game_session", "cursor": None}, (), ("play_game",)),
    ("browse_activities", {"category": "work_contract", "cursor": None}, (), ("work",)),
    ("browse_activities", {"category": "subtitle_experience", "cursor": None}, (), ("watch",)),
    ("send_message", {"recipient_id": "contact-chenxi-oldfriend", "text": "你好"}, ("send_message",), ()),
    ("consume", {"items": [{"product_id": "food", "quantity": 1}], "use_mode": "eat",
     "duration_seconds": 60}, (), ("consume",)),
])
def test_direct_calls_are_rejected_without_domain_effects(
        tmp_path, caplog, tool, values, blocked_tools, blocked_kinds):
    """直接调用跳过人物工具面仍必须留下菜单拒绝且没有领域副作用。"""
    from worldmock.life.models import REQUEST_MODELS

    engine = _engine(tmp_path, lambda document: _restrict(
        document, tools=blocked_tools, kinds=blocked_kinds,
        location="office" if values.get("category") == "work_contract" else "home"))
    try:
        if values.get("category") == "work_contract":
            from tests.life.test_work_review import _at_task_start

            stage_day_plan(engine)
            _at_task_start(engine)
            assert engine.observe(context(engine, "work-available"))["activity_summary"]["work_contract"] > 0
        before = _domain_state(engine)
        _, result = _observed_action(engine, "menu-denied", tool, REQUEST_MODELS[tool].model_validate(values))
        assert result.error_code == "INTERACTION_MENU_DENIED"
        assert _domain_state(engine) == before
        assert "INTERACTION_MENU_DENIED" in caplog.text
        rejected = engine.store.read_agent_events("action_rejected", 1)
        assert len(rejected) == 1
        assert rejected[0].data["error_code"] == "INTERACTION_MENU_DENIED"
        assert advance_until_ready(engine)
        after = engine.observe(context(engine, "after-denial"))
        assert after["facts"]["recent_action_results"][-1]["error_code"] == "INTERACTION_MENU_DENIED"
    finally:
        engine.close()


def test_denied_purchase_does_not_pay_or_change_stock(tmp_path, caplog):
    """真实商品已发现且商家营业时，菜单仍能在扣款和库存变化前拒绝。"""
    engine = _engine(tmp_path, lambda document: _restrict(document, tools=("purchase",)))
    try:
        _stage_open(engine)
        _browse(engine)
        request = _delivery_request(engine, engine.scenario.products[0].product_id)
        before = _domain_state(engine)
        _, result = _observed_action(engine, "denied-purchase", "purchase", request)
        assert result.error_code == "INTERACTION_MENU_DENIED"
        assert _domain_state(engine) == before
        assert "INTERACTION_MENU_DENIED" in caplog.text
    finally:
        engine.close()


def test_unknown_browse_category_keeps_domain_error_when_menu_disallows_browsing(tmp_path, caplog):
    """菜单禁止下钻也不能把不存在的品类改写成菜单拒绝。"""
    engine = _engine(tmp_path, lambda document: _restrict(document, tools=("browse_activities",)))
    try:
        before = _domain_state(engine)
        _, result = _observed_action(engine, "unknown-category", "browse_activities",
                                     BrowseActivitiesRequest(category="nonexistent_category", cursor=None))
        assert result.error_code == "BROWSE_CATEGORY_NOT_FOUND"
        assert _domain_state(engine) == before
        assert "BROWSE_CATEGORY_NOT_FOUND" in caplog.text
    finally:
        engine.close()


def test_replayed_menu_rejection_preserves_counter_and_marks_reused_evidence(tmp_path):
    """菜单拒绝的幂等重放只增加已标明的审计证据，不重复累计拒绝或执行领域行为。"""
    engine = _engine(tmp_path, lambda document: _restrict(document, tools=("send_message",)))
    try:
        request = SendMessageRequest(recipient_id="contact-chenxi-oldfriend", text="你好")
        before = _domain_state(engine)
        _, original = _observed_action(engine, "denied-original", "send_message", request, action_id="denied")
        assert original.error_code == "INTERACTION_MENU_DENIED"
        count = engine.state.agents[1].consecutive_rejections
        assert count == 1
        assert advance_until_ready(engine)
        _, replay = _observed_action(engine, "denied-replay", "send_message", request, action_id="denied")
        assert replay.error_code == "INTERACTION_MENU_DENIED"
        assert engine.state.agents[1].consecutive_rejections == count
        assert _domain_state(engine) == before
        events = engine.store.read_agent_events("action_rejected", 1)
        assert len(events) == 2
        assert not events[0].data.get("reused", False)
        assert events[1].data["reused"] is True
    finally:
        engine.close()


def test_menu_observation_keeps_facts_candidates_and_other_people_private(tmp_path):
    """菜单只追加自己的交集，观察事实、候选计数和候选排除统计不被删除。"""
    def restrict(document):
        _restrict(document, tools=("send_message",), agent=0)
        _restrict(document, tools=("purchase",), agent=1)

    engine = _engine(tmp_path, restrict, "multi-person.json")
    try:
        observed = [engine.observe(context(engine, f"person-{person}", person)) for person in (1, 2)]
        assert "send_message" not in observed[0]["interaction_menu"]["allowed_tools"]
        assert "purchase" in observed[0]["interaction_menu"]["allowed_tools"]
        assert "send_message" in observed[1]["interaction_menu"]["allowed_tools"]
        assert "purchase" not in observed[1]["interaction_menu"]["allowed_tools"]
        assert all("interaction_menu" not in item["facts"] for item in observed)
        assert all("interaction_menu" not in item["scene"] for item in observed)
        assert all("interaction_menus_by_location_kind" not in item for item in observed)
        again = engine.observe(context(engine, "same-state", 1))
        assert again["interaction_menu"] == observed[0]["interaction_menu"]
        events = engine.store.read_agent_events("observation", 1)
        assert events[-1].data["interaction_menu"] == observed[0]["interaction_menu"]
    finally:
        engine.close()


def test_game_browse_uses_the_game_pool_not_the_content_pool(tmp_path):
    """真实游戏下钻返回已登记游戏，不被同名内容品类误分流到空池。"""
    engine = _engine(tmp_path, lambda document: None)
    try:
        before = engine.observe(context(engine, "before-game-browse"))
        assert before["activity_summary"]["game_session"] > 0
        _, result = _observed_action(
            engine, "game-browse", "browse_activities",
            BrowseActivitiesRequest(category="game_session", cursor=None))
        assert result.error_code is None
        assert result.data["items"]
        assert result.data["total"] == before["activity_summary"]["game_session"]
    finally:
        engine.close()


def _travel_to_office(engine):
    """按真实出行和反馈边界到达办公室，不直接改人物位置。"""
    route = next(item for item in engine.scenario.routes
                 if item.origin_id == "home" and item.destination_id == "office" and item.mode == "driving")
    _, started = _observed_action(engine, "to-office", "start_travel", StartTravelRequest(
        destination_id="office", travel_mode=route.mode,
        planned_duration_seconds=travel_quote(engine, route.mode, route.duration_seconds)))
    assert started.error_code is None
    for turn in range(64):
        if engine.state.agents[1].location_id == "office":
            return
        if engine.scheduling_state().ready_agent_ids:
            activity_id = engine.state.agents[1].current_activity_id
            _, continued = _observed_action(engine, f"continue-travel-{turn}", "continue_activity",
                                             ContinueActivityRequest(activity_id=activity_id))
            assert continued.error_code is None
        else:
            advance_next(engine)
    pytest.fail("Travel did not reach the office")


def test_paused_activity_is_not_resumed_after_arrival_but_can_be_cancelled(tmp_path):
    """家中暂停的观看活动到办公室后不能恢复，取消仍成功且进度不丢。"""
    engine = _engine(tmp_path, lambda document: _restrict(document, kinds=("watch",), location="office"))
    try:
        stage_day_plan(engine)
        _, browsed = _observed_action(engine, "watch-catalog", "browse_activities",
                                     BrowseActivitiesRequest(category="subtitle_experience", cursor=None))
        assert browsed.error_code is None
        assert advance_until_ready(engine)
        _, started = _observed_action(engine, "watch-start", "start_targeted_activity",
                                      StartTargetedActivityRequest(activity_type="watch",
                                      target_id="sintel-en-subtitles", planned_duration_seconds=1260,
                                      playback_speed=0.5))
        assert started.error_code is None
        wake_by_mail(engine)
        assert advance_until_ready(engine)
        _, paused = _observed_action(engine, "watch-pause", "set_activity_state",
                                     SetActivityStateRequest(activity_id=started.activity_id, operation="pause"))
        assert paused.error_code is None
        assert advance_until_ready(engine)
        _travel_to_office(engine)
        assert advance_until_ready(engine)
        observation = engine.observe(context(engine, "office-pause-observation"))
        assert "watch" not in observation["interaction_menu"]["allowed_activity_kinds"]
        assert any(item["activity_id"] == started.activity_id for item in observation["paused_activities"])
        previous = engine.state.activities[started.activity_id].model_dump(mode="json")
        _, denied = _observed_action(engine, "watch-resume", "set_activity_state",
                                     SetActivityStateRequest(activity_id=started.activity_id, operation="resume"))
        assert denied.error_code == "INTERACTION_MENU_DENIED"
        assert engine.state.activities[started.activity_id].model_dump(mode="json") == previous
        assert advance_until_ready(engine)
        _, cancelled = _observed_action(engine, "watch-cancel", "set_activity_state",
                                         SetActivityStateRequest(activity_id=started.activity_id, operation="cancel"))
        assert cancelled.error_code is None
        assert engine.state.activities[started.activity_id].elapsed_seconds == previous["elapsed_seconds"]
    finally:
        engine.close()


def test_menu_does_not_remove_candidates_or_change_observation_facts(tmp_path):
    """相同世界只修改菜单，候选计数、公开事实和注册表合同逐项一致。"""
    unrestricted = _engine(tmp_path / "unrestricted", lambda document: None)
    restricted = _engine(tmp_path / "restricted", lambda document: _restrict(
        document, tools=("purchase", "send_message"), kinds=("watch", "play_game", "lifestyle")))
    try:
        full = unrestricted.observe(context(unrestricted, "full-menu"))
        limited = restricted.observe(context(restricted, "limited-menu"))
        assert full["interaction_menu"] != limited["interaction_menu"]
        for field in ("scene", "facts", "activity_summary", "products", "affordance_contract"):
            assert full[field] == limited[field], field
    finally:
        unrestricted.close()
        restricted.close()


@pytest.mark.parametrize("field", ["interaction_menu", "interaction_menus_by_location_kind"])
def test_menu_support_cannot_be_copied_into_prompt_facts(field, caplog):
    """菜单支持数据意外混入公开事实时，实际提示渲染校验失败而非再次下发。"""
    from worldmock.life.narration import ForbiddenObservationFieldError, enforce_field_blacklist

    with pytest.raises(ForbiddenObservationFieldError, match=field):
        enforce_field_blacklist({"facts": {"nested": [{field: {"allowed_tools": ["purchase"]}}]}})
    assert "Forbidden" in caplog.text or "forbidden" in caplog.text


@pytest.mark.asyncio
async def test_denied_work_submission_does_not_invoke_review(tmp_path, monkeypatch):
    """工作菜单关闭时，即使材料校验可通过也不能启动独立模型评审。"""
    import worldmock.life.engine as engine_module

    engine = _engine(tmp_path, lambda document: _restrict(document, tools=("submit_work",)))
    try:
        def unexpected(*args, **kwargs):
            pytest.fail("Menu-denied submission reached work preparation")

        monkeypatch.setattr(engine_module, "prepare_work_submission", unexpected)
        request = SubmitWorkRequest(task_id=engine.scenario.tasks[0].task_id,
                                    deliverable=MeetingMinutes(summary="会议纪要", decisions=(), action_items=()))
        ctx = context(engine, "denied-review")
        engine.begin_decision_round([ctx])
        engine.observe(ctx)
        engine.submit_action(DirectToolCall(ctx, "denied-review", "submit_work", request))
        await engine.prepare_pending_work_review("denied-review")
        result = engine.commit_decision_round(ctx.round_id)[0]
        assert result.error_code == "INTERACTION_MENU_DENIED"
        assert not engine.store.read_agent_events("model_request", 1)
    finally:
        engine.close()


@pytest.mark.parametrize("change_person", [False, True])
def test_changed_location_or_person_menu_cannot_open_existing_run(tmp_path, change_person):
    """同一运行不能用不同地点声明或不同人物声明打开，源码模板相同也不能绕过。"""
    engine = _engine(tmp_path, lambda document: None)
    path = tmp_path / "scenarios" / "custom.json"
    run_dir = engine.store.run_dir
    original = engine.scenario_sha256
    engine.close()
    document = json.loads(path.read_text())
    _restrict(document, tools=("purchase",), agent=0 if change_person else None)
    path.write_text(json.dumps(document, ensure_ascii=False))
    from worldmock.life.scenario import load_scenario

    assert load_scenario(path)[1] != original
    with pytest.raises(IntegrityError, match="scenario identity"):
        LifeEngine.open(path, run_dir)

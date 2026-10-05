"""第五章「并行准入」的验收：组合表逐行、途中的地点与菜单、整组拒绝不变量、公开原因与下一步、制作与使用、
超长派生时长（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章；裁决 #19、#20、#23）。

全部经真实引擎：观察冻结的私有绑定按「动词＋对象＋方式」解析，整组先按名额、目标在场与实际地点、出行方式、占用、
物品状态的顺序判定再派发；拒绝写 data.reason 与 data.next_step，并在下一次观察的进度层该调用的结果中呈现；工具层每个
动作的 available 与 reason 与这套判定同源（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「工具层」）。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError
from tests.life.conftest import (advance_next, context, last_result, stage_day_plan, submit_round, target_actions,
                                 tool_targets, report_request)
from worldmock.errors import BusinessRuleError
from worldmock.footprints.models import EventDraft
from worldmock.life import order_routing
from worldmock.life.engine import LifeEngine
from worldmock.life.vocabulary import WAYS
from worldmock.life.interaction_menu import menu_allows_action, resolve_interaction_menu
from worldmock.life.models import (ActivityKind, ActivityStatus, MeetingMinutes, SendMessageActionRequest,
                                   StartActivityRequest, SubmitWorkActionRequest, active_activities)
from worldmock.life.recipients import agent_identity

_OPEN = int(datetime.fromisoformat("2026-01-05T10:00:00+08:00").timestamp())
_UNMET, _CONFLICT = "ACTIVITY_PRECONDITION_UNMET", "RESOURCE_CONFLICT"
_RECIPE_INPUT = "ingredient-vegetable-kit"
_RECIPE_OUTPUT = "meal-0030"


def _stage(engine: LifeEngine, agent_id: int = 1, *, location: str | None = None, task: bool = False,
           holding: str | None = None, market: bool = False) -> None:
    """@brief 把隔离运行推进到 1 月 5 日 10:00（任务开放、门店营业），按需把人物放到某地、公开当日任务、手边放一份
    食品并呈现全部门店供给（经存储落盘，保持版本连续）。"""
    staged = engine.context.state.model_copy(deep=True)
    now = max(staged.current_time, _OPEN)
    staged.current_time = now
    staged.pending_events = [event for event in staged.pending_events if event.due_at > now]
    agent = staged.agents[agent_id]
    place = location or agent.location_id
    update = {"location_id": place, "next_decision_at": now, "body_settled_at": now}
    if task:
        update["visible_task_ids"] = {*agent.visible_task_ids, engine.context.scenario.tasks[0].task_id}
    if holding is not None:
        update["inventory"] = {place: {holding: 1.0}}
    if market:
        visible = {item.supply_id for item in engine.context.scenario.supplies}
        update.update(visible_supply_ids=visible, supply_browsed_at={key: now for key in visible},
                      supply_browsed_location={key: place for key in visible})
    staged.agents[agent_id] = agent.model_copy(update=update, deep=True)
    for other in staged.agents.values():
        other.body_settled_at = now
        other.next_decision_at = now
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=now, visibility="system", source_kind="scenario",
        data={"kind": "test_parallel_admission_staged"})])
    engine.context.state = staged


def _engine(engine_factory, scenario: str = "white-collar-12-weeks.json", **stage) -> LifeEngine:
    """@brief 登记空计划的运行，并按需预置地点、任务、持有物与门店供给。"""
    engine = engine_factory(scenario)
    for agent_id in engine.context.state.agents:
        stage_day_plan(engine, agent_id)
    if stage:
        _stage(engine, **stage)
    return engine


def _observe(engine: LifeEngine, name: str, agent_id: int = 1) -> dict:
    """@brief 冻结一次本人观察（不登记动作），用于读取交互对象层。"""
    return engine.observe(context(engine, name, agent_id))


def _choice(observed: dict, way: str, *, kind: str | None = None, object_id: str | None = None,
            prefix: str = "") -> tuple[str, str, str]:
    """@brief 从本轮私有绑定取一项（对象名，动词，方式）：按方式、对象种类、对象身份或名称前缀筛选。"""
    return next((row["object"], action["verb"], action["label"]) for row in observed["interaction_bindings"]
                if (kind is None or row["kind"] == kind) and (object_id is None or row["object_id"] == object_id)
                and row["object"].startswith(prefix) for action in row["actions"] if action["label"] == way)


def _request(*choices: tuple[str, str, str]) -> StartActivityRequest:
    """@brief 由（对象，动词，方式）逐项构造 start_activity 请求。"""
    purchase = any(item[2] in {"到店", "远程下单", "购买"} for item in choices)
    return StartActivityRequest(items=[{"target": item[0], "way": item[2], "from": None, "to": None}
                                       for item in choices], answering=None, prefer="最快拿到" if purchase else None)


def _act(engine: LifeEngine, name: str, tool: str, request, agent_id: int = 1):
    """@brief 按真实顺序观察、登记并结算一轮，返回该人物的结果；同一秒就绪的其他人物（多人场景）开始等待。"""
    ready = engine.scheduling_state().ready_agent_ids or (agent_id,)
    others = {other: ("start_activity", _request(("自己", "interact", "等待")))
              for other in ready if other != agent_id}
    _, results = submit_round(engine, {agent_id: (tool, request), **others}, name)
    return next(item for item in results if item.agent_id == agent_id)


def _world(engine: LifeEngine, agent_id: int = 1) -> tuple:
    """@brief 整组拒绝要保持不变的世界事实：账户余额、库存、位置、活动与订单。"""
    agent = engine.context.state.agents[agent_id]
    activities = {key: (item.status, item.elapsed_seconds) for key, item in engine.context.state.activities.items()
                  if item.agent_id == agent_id}
    return (dict(engine.context.state.accounts), agent.inventory, agent.location_id, activities, dict(engine.context.state.deliveries),
            dict(engine.context.state.supply_stock))


def _rejected(engine: LifeEngine, name: str, request, code: str, tool: str = "start_activity"):
    """@brief 断言整组拒绝：错误码、世界不变，原因与下一步随下一次观察进入进度层该调用结果的 details。"""
    before = _world(engine)
    result = _act(engine, name, tool, request)
    assert result.status == "rejected" and result.error_code == code, (result.error_code, result.message)
    assert _world(engine) == before
    assert result.data["reason"] and result.data["next_step"]
    shown = last_result(_observe(engine, f"{name}-shown"))
    assert shown["error_code"] == code
    assert shown["details"]["reason"] == result.data["reason"]
    assert shown["details"]["next_step"] == result.data["next_step"]
    for text in (result.data["reason"], result.data["next_step"]):
        assert "agent:" not in text and "office" not in text and "supply-" not in text
    return result


def _food(engine: LifeEngine) -> str:
    """@brief 一件可食用的商品身份。"""
    return next(item.product_id for item in engine.context.scenario.products if "eat" in item.use_modes)


@pytest.mark.parametrize("companion", ["podcast", "call"])
def test_driving_with_podcast_or_hands_free_call_is_accepted_and_ends_at_arrival(engine_factory, companion) -> None:
    """驾车与播客、免提通话同一请求开始即受理，归入路上会话；到达的同一秒一并结束并成为待收尾（组合表第一行）。"""
    engine = _engine(engine_factory)
    observed = _observe(engine, "road-preview")
    drive = _choice(observed, "驾车", object_id="office")
    other = _choice(observed, "接着听") if companion == "podcast" else _choice(observed, "通话", kind="contact")
    result = _act(engine, "road-start", "start_activity", _request(drive, other))
    assert result.status == "in_progress", (result.error_code, result.message)
    travel, partner = (engine.context.state.activities[row["activity_id"]] for row in result.data["activities"])
    assert travel.kind == ActivityKind.TRAVEL and partner.session_ids == travel.session_ids
    staged = engine.context.state.model_copy(deep=True)
    longer = travel.planned_end_at - partner.started_at + 600
    staged.activities[partner.activity_id] = partner.model_copy(
        update={"planned_end_at": partner.started_at + longer, "planned_duration_seconds": longer}, deep=True)
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        data={"kind": "test_companion_outlasts_trip"})])
    engine.context.state = staged
    while engine.context.state.activities[travel.activity_id].status == ActivityStatus.ACTIVE:
        assert not engine.scheduling_state().ready_agent_ids or engine.context.state.current_time < travel.planned_end_at
        advance_next(engine)
    arrived = engine.context.state.activities[travel.activity_id]
    ended = engine.context.state.activities[partner.activity_id]
    assert arrived.status == ActivityStatus.COMPLETED and engine.context.state.agents[1].location_id == "office"
    assert ended.ended_at == arrived.ended_at and ended.boundary_type == "route_ended"
    assert ended.concluded_at is None and ended.status != ActivityStatus.ACTIVE


@pytest.mark.parametrize("way,kind", [("接着看", "content"), ("游玩", "game"), ("浏览", "catalog"),
                                      ("食用", "holding"), ("睡觉", "self")])
@pytest.mark.parametrize("drive_first", [True, False])
def test_driving_with_eyes_or_hands_activities_is_a_resource_conflict_in_either_order(
        engine_factory, way, kind, drive_first) -> None:
    """驾车占 eyes、hands：与视频、游戏、浏览、食用、睡眠同一请求时以 RESOURCE_CONFLICT 整组拒绝，与组内先后无关；
    余额、库存、位置与活动不变，原因与下一步进入下一次观察（组合表第二行）。"""
    engine = _engine(engine_factory)
    if kind == "holding":
        _stage(engine, holding=_food(engine))
    observed = _observe(engine, "conflict-preview")
    drive = _choice(observed, "驾车", object_id="office")
    other = _choice(observed, way, kind=kind)
    pair = (drive, other) if drive_first else (other, drive)
    result = _rejected(engine, "conflict", _request(*pair), _CONFLICT)
    assert result.data["next_step"] == "Choose only one of the conflicting activities."


def _presence_case(engine_factory, case: str):
    """@brief 目标在场三例的起点与两项选择：办公室工作、到店取得（门店）与当面交谈（多人场景）。"""
    if case == "work":
        engine = _engine(engine_factory, location="office", task=True)
        observed = _observe(engine, "presence-preview")
        return engine, _choice(observed, "驾车", object_id="home"), _choice(observed, "工作", kind="work_task")
    if case == "pickup":
        engine = _engine(engine_factory, location="market", market=True)
        observed = _observe(engine, "presence-preview")
        return engine, _choice(observed, "驾车", object_id="home"), _choice(observed, "到店", kind="supply")
    engine = _engine(engine_factory, "multi-person.json")
    observed = _observe(engine, "presence-preview")
    return engine, _choice(observed, "驾车", kind="location"), _choice(observed, "交谈", kind="person")


@pytest.mark.parametrize("case", ["work", "pickup", "chat"])
@pytest.mark.parametrize("drive_first", [True, False])
def test_driving_with_office_work_pickup_or_face_chat_fails_target_presence(engine_factory, case, drive_first) -> None:
    """办公室工作、到店取得与当面交谈都要求人在目标地点：与出行同一请求时出发即不在场，以 ACTIVITY_PRECONDITION_UNMET
    整组拒绝，与组内先后无关，且先于占用判定（组合表第三行；「驾车途中开始办公室工作报 ACTIVITY_PRECONDITION_UNMET」）。"""
    engine, drive, other = _presence_case(engine_factory, case)
    pair = (drive, other) if drive_first else (other, drive)
    result = _rejected(engine, "presence", _request(*pair), _UNMET)
    assert result.data["unmet"] in (["required_location_id"], ["partner_present"])


@pytest.mark.parametrize("companion", [("食用", "holding"), ("接着看", "content"), ("接着听", "content")])
def test_work_with_eating_watching_or_podcast_is_accepted_and_settles_one_shared_body(engine_factory, companion) -> None:
    """工作占 work、idle，与食用、观看、播客都不相交：同一请求受理；此后每个边界身体只结算一次，两项活动各自累计进度
    并各自留证（组合表第四行、「共同身体只结算一次、各活动分别留证」）。"""
    engine = _engine(engine_factory, location="office", task=True)
    _stage(engine, holding=_food(engine))
    observed = _observe(engine, "work-preview")
    work = _choice(observed, "工作", kind="work_task")
    other = _choice(observed, companion[0], kind=companion[1])
    result = _act(engine, "work-pair", "start_activity", _request(work, other))
    assert result.status == "in_progress", (result.error_code, result.message)
    started = [row["activity_id"] for row in result.data["activities"]]
    advance_next(engine)
    boundary = engine.context.state.current_time
    events = engine.context.store.read_agent_events("environment_event", 1)
    body = [event for event in events if event.data.get("kind") == "body_settlement"
            and event.data["interval_end"] == boundary]
    assert len(body) == 1 and set(started) <= set(body[0].data["activity_ids"])
    progress = [event for event in engine.context.store.read_agent_events("activity_progress", 1)
                if event.occurred_at == boundary]
    assert sorted(event.activity_id for event in progress) == sorted(started)
    assert all(event.data["body_settlement_event_id"] == body[0].event_id for event in progress)
    assert all(engine.context.state.activities[key].elapsed_seconds > 0 for key in started)


def _conflict_case(engine_factory, case: str):
    """@brief 占用冲突四例：两段观看、观看与游戏、两条出行，交谈与通话（多人场景）。"""
    if case == "chat_call":
        engine = _engine(engine_factory, "multi-person.json")
        observed = _observe(engine, "pair-preview")
        return engine, _choice(observed, "交谈", kind="person"), _choice(observed, "通话", kind="contact")
    engine = _engine(engine_factory)
    observed = _observe(engine, "pair-preview")
    videos = [(row["object"], "interact", "接着看") for row in observed["interaction_bindings"]
              if row["kind"] == "content" and row["actions"][0]["label"] == "接着看"]
    pairs = {"two_watches": (videos[0], videos[1]),
             "watch_game": (videos[0], _choice(observed, "游玩", kind="game")),
             "two_trips": (_choice(observed, "步行", object_id="office"), _choice(observed, "驾车", object_id="market"))}
    return (engine, *pairs[case])


@pytest.mark.parametrize("case", ["two_watches", "watch_game", "two_trips", "chat_call"])
def test_overlapping_pairs_are_resource_conflicts(engine_factory, case) -> None:
    """两段观看、观看与游戏、交谈与通话、两条出行的占用相交，以 RESOURCE_CONFLICT 整组拒绝（组合表第五行）。"""
    engine, first, second = _conflict_case(engine_factory, case)
    _rejected(engine, "pair", _request(first, second), _CONFLICT)


def _driving(engine: LifeEngine, name: str, way: str = "驾车", destination: str | None = None) -> None:
    """@brief 独自出发去另一个可达地点，并在途中的一分钟后获得一次决策机会（邮件叫醒；多人时同伴仍在原地）。"""
    from tests.life.conftest import advance_until_ready, wake_by_mail

    observed = _observe(engine, f"{name}-preview")
    trip = _act(engine, f"{name}-go", "start_activity",
                _request(_choice(observed, way, kind="location", object_id=destination)))
    assert trip.status == "in_progress", (trip.error_code, trip.message)
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine) and active_activities(engine.context.state, 1)[0].kind == ActivityKind.TRAVEL


def test_driving_rejects_messages_remote_orders_and_submissions(engine_factory) -> None:
    """驾车途中需要手动操作的瞬时动作（发消息、远程下单、提交成果）以 ACTIVITY_PRECONDITION_UNMET 拒绝，原因与下一步
    写明到达后再做；远程下单照常列出并带同一说明（组合表第六行）。"""
    engine = _engine(engine_factory, location="office", task=True, market=True)
    _driving(engine, "hands")
    observed = _observe(engine, "hands-preview")
    order = next((row["object"], action) for row in observed["interaction_bindings"] if row["kind"] == "supply"
                 for action in row["actions"] if action["label"] == "远程下单")
    assert order[1]["note"] == "Cannot place an order while driving."
    contact = next(row["object"] for row in observed["interaction_bindings"] if row["kind"] == "contact")
    message = _rejected(engine, "hands-message", SendMessageActionRequest(recipient_name=contact, text="在路上"),
                        _UNMET, tool="send_message")
    assert message.data["reason"] == "Cannot send a message while driving."
    assert message.data["next_step"].startswith("Wait until arrival at ")
    _rejected(engine, "hands-order", _request((order[0], "obtain", "远程下单")), _UNMET)
    minutes = MeetingMinutes(summary="会议纪要", decisions=("按计划推进",), action_items=("整理材料",))
    submission = SubmitWorkActionRequest(task_name=f"工作：{engine.context.scenario.tasks[0].title}", deliverable=minutes)
    submitted = _rejected(engine, "hands-submit", submission, _UNMET, tool="submit_work")
    assert submitted.data["reason"] == "Cannot submit work while driving."


def test_walking_accepts_messages_and_remote_orders_delivered_to_the_origin(engine_factory) -> None:
    """步行途中发消息与远程下单照常受理；远程下单的送达地取出发地（agent.location_id），订单之后才能取得：
    订单未送达时出发地与目的地都没有这件商品，到了预计送达时刻才出现在出发地（「订单与取得的先后」）。"""
    from tests.life.conftest import advance_until_ready, wake_by_mail

    engine = _engine(engine_factory, location="office", market=True)
    _driving(engine, "walk", way="步行", destination="home")
    observed = _observe(engine, "walk-preview")
    contact = next(row["object"] for row in observed["interaction_bindings"] if row["kind"] == "contact")
    sent = _act(engine, "walk-message", "send_message", SendMessageActionRequest(recipient_name=contact, text="路上"))
    assert sent.status == "completed"
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine) and active_activities(engine.context.state, 1)[0].kind == ActivityKind.TRAVEL
    order = _choice(_observe(engine, "walk-order-preview"), "远程下单", kind="supply")
    bought = _act(engine, "walk-order", "start_activity", _request(order))
    assert bought.status == "completed", (bought.error_code, bought.message)
    [delivery] = engine.context.state.deliveries.values()
    assert delivery.destination_id == "office" and delivery.status == "pending"
    assert engine.context.state.agents[1].location_id == "office" and not engine.context.state.agents[1].inventory.get("home")
    assert not engine.context.state.agents[1].inventory.get("office", {}).get(delivery.product_id)
    _live_until(engine, delivery.expected_at)
    inventory = engine.context.state.agents[1].inventory
    assert engine.context.state.deliveries[delivery.order_id].status == "delivered"
    assert inventory["office"][delivery.product_id] == 1 and not inventory.get("home", {}).get(delivery.product_id)
    assert engine.context.state.agents[1].location_id == "home"


def _live_until(engine: LifeEngine, moment: int) -> None:
    """@brief 真实推进到 moment：到达或活动结束后当秒收尾，有进行中活动时照常播报，空闲时等待。"""
    from tests.life.conftest import activity_name, conclude_pending, start_activity
    from worldmock.life import activity_control

    for step in range(64):
        if engine.context.state.current_time >= moment:
            return
        if not engine.scheduling_state().ready_agent_ids:
            advance_next(engine)
        elif activity_control.pending_conclusions(engine.context.state, 1):
            conclude_pending(engine, f"live-conclude-{step}")
        elif active_activities(engine.context.state, 1):
            name = activity_name(engine, active_activities(engine.context.state, 1)[0].activity_id)
            _act(engine, f"live-report-{step}", "report_status", report_request(name, summary="继续。"))
        else:
            _act(engine, f"live-wait-{step}", "start_activity", start_activity("等待", "自己"))
    raise AssertionError("the run did not reach the moment")


def test_en_route_person_is_nowhere_with_nothing_at_hand(engine_factory) -> None:
    """途中不在任何地点：身边没有人、手边没有持有物，出发地的持有物以「持有物：<商品>（<地点>）」列出且食用不可用，
    生活活动标不可用「路上做不了」；菜单仍按出发地解析，路上的持有物食用在占用判定之后才报物品状态
    （驾车报 RESOURCE_CONFLICT，步行报 PRODUCT_NOT_HELD）。"""
    from worldmock.life import admission

    engine = _engine(engine_factory, "multi-person.json")
    _stage(engine, holding=_food(engine))
    before = _observe(engine, "nowhere-before")
    assert any(row["kind"] == "person" for row in before["interaction_bindings"])
    assert any(row["kind"] == "holding" for row in before["interaction_bindings"])
    held = [row["object"] for row in before["interaction_bindings"] if row["kind"] == "holding"]
    assert held and all("（" not in name for name in held)
    _driving(engine, "nowhere")
    during = _observe(engine, "nowhere-during")
    assert not [row for row in during["tool_layer"]["targets"] if row["kind"] == "person"
                and row["name"].startswith("人物：")]
    away = [row for row in during["interaction_bindings"] if row["kind"] == "holding"]
    origin_name = next(item.name for item in engine.context.scenario.locations
                       if item.location_id == engine.context.state.agents[1].location_id)
    assert [row["object"] for row in away] == [f"{name}（{origin_name}）" for name in held]
    assert all(row["location_id"] == engine.context.state.agents[1].location_id for row in away)
    lifestyle = [row for row in during["interaction_bindings"] if row["kind"] == "lifestyle"]
    assert lifestyle and all(row["actions"][0]["note"].endswith(
        " requires a stationary location and cannot run during travel.") for row in lifestyle)
    assert all(target_actions(during, row["object"])["进行"]["available"] is False for row in lifestyle)
    origin = engine.context.state.agents[1].location_id
    assert during["interaction_menu"] == resolve_interaction_menu(engine.context.scenario, 1, origin).model_dump(mode="json")
    context_ = admission.Context(state=engine.context.state, agent_id=1, place_names={})
    eat = admission.Item(canonical="consume", way="食用", label="食用牛奶", claims=frozenset({"hands"}), holding=True)
    assert admission.group_refusal(context_, [eat]).code == _CONFLICT
    walking = [item for item in engine.context.state.activities.values() if item.kind == ActivityKind.TRAVEL][0]
    staged = engine.context.state.model_copy(deep=True)
    staged.activities[walking.activity_id] = walking.model_copy(update={"resource_claims": {"travel", "idle"}})
    staged.activities[walking.activity_id].travel_mode = None
    refusal = admission.group_refusal(admission.Context(state=staged, agent_id=1, place_names={}), [eat])
    assert refusal.code == "PRODUCT_NOT_HELD" and refusal.reason == "Held items cannot be used during travel."


def test_departure_with_a_running_activity_names_it_in_reason_and_next_step(engine_factory) -> None:
    """出发前须先结束或暂停进行中的活动：出行照常列出并带说明，选了以 ACTIVITY_PRECONDITION_UNMET 返回原因与下一步，
    都点名要先处理的活动（第五章「动作照常列出，做错了再说明」、第七章）。"""
    from tests.life.conftest import start_activity, wake_by_mail, advance_until_ready

    engine = _engine(engine_factory)
    waited = _act(engine, "busy-wait", "start_activity", start_activity("等待", "自己"))
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    name = engine.context.state.activities[waited.activity_id].name
    observed = _observe(engine, "busy-preview")
    trip = next(action for row in observed["interaction_bindings"] if row["object_id"] == "office"
                for action in row["actions"] if action["label"] == "步行")
    assert trip["note"] == f"Conclude or pause the current activities {name} before departure."
    office = next(row["object"] for row in observed["interaction_bindings"] if row["object_id"] == "office")
    assert target_actions(observed, office)["步行"]["reason"] == trip["note"]
    result = _rejected(engine, "busy-go", _request(_choice(observed, "步行", object_id="office")), _UNMET)
    assert result.data["next_step"] == f"Conclude or pause {name} first."


def test_zero_stock_supply_is_listed_with_its_ways_marked_sold_out(engine_factory) -> None:
    """呈现的供给库存为 0 时对象照常列出、取得方式照常列出并标不可用「卖完了」（运行内可能满足的条件照常列出并标原因）；
    提交以 OUT_OF_STOCK 业务拒绝、原因一字不差，决策照常继续，没有 run_failed（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「工具层」、
    第九章「仍须修复」第 1 条）。"""
    from tests.life.conftest import start_activity

    from worldmock.life.models import SameMomentPresentation

    engine = _engine(engine_factory, location="market", market=True)
    supply = next(item.supply_id for item in engine.context.scenario.supplies if item.location_id == "market")
    staged = engine.context.state.model_copy(deep=True)
    staged.supply_stock[supply] = 0
    staged.same_moment_presentation[1] = SameMomentPresentation(stamps={}, pinned_supply_ids=(supply,))
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        data={"kind": "test_sold_out"})])
    engine.context.state = staged
    observed = _observe(engine, "sold-out")
    name = next(row["object"] for row in observed["interaction_bindings"] if row["object_id"] == supply)
    ways = target_actions(observed, name)
    reason = "This supply has no remaining stock for the requested quantity."
    assert ways and all((item["available"], item["reason"]) == (False, reason) for item in ways.values())
    assert "自己" in tool_targets(observed)
    way = next(iter(ways))
    refused = _act(engine, "sold-out-buy", "start_activity", _request((name, "obtain", way)))
    assert (refused.status, refused.error_code, refused.data["reason"]) == ("rejected", "OUT_OF_STOCK", reason)
    assert _act(engine, "sold-out-wait", "start_activity", start_activity("等待", "自己")).status == "in_progress"
    assert not engine.context.store.read_agent_events("run_failed", 1)


def test_listed_actions_are_menu_allowed_and_self_is_always_offered(engine_factory) -> None:
    """交互对象层恒含「自己」（等待、睡觉）；列出的每个动作的内部动作都在有效菜单内，所以不会以 INTERACTION_MENU_DENIED
    拒绝；说明为空的动作与准入判定一致（第五章「动作照常列出，做错了再说明」）。"""
    engine = _engine(engine_factory, location="office", task=True, market=True)
    observed = _observe(engine, "listed")
    menu = resolve_interaction_menu(engine.context.scenario, 1, "office")
    assert [(item["action"], item["way"]) for item in tool_targets(observed)["自己"]["actions"]] == [
        ("interact", "等待"), ("sleep", "睡觉")]
    for row in observed["interaction_bindings"]:
        for action in row["actions"]:
            assert WAYS[action["label"]] == (action["verb"], action["canonical_action"])
            assert menu_allows_action(menu, "start_activity", [action["canonical_action"]])
    work = _choice(observed, "工作", kind="work_task")
    assert _act(engine, "listed-work", "start_activity", _request(work)).status == "in_progress"


def test_holding_lists_obtain_make_for_inventory_recipes_and_consume_use_otherwise(engine_factory, monkeypatch) -> None:
    """持有物的 consume:use 规则是库存转换配方时列 obtain「制作」，否则列 consume「使用」；两者都映射回内部动作
    consume（use_mode 为 use），与 prepare_inventory_recipe 同一判据（第五章「请求形状与菜单」）；冻结目录的配对本身
    失败时按完整性错误处理。"""
    from worldmock.categories import CategoryError
    from worldmock.errors import IntegrityError
    from worldmock.life.inventory_recipe import is_inventory_recipe

    engine = _engine(engine_factory, holding="item-umbrella")
    plain = next(row for row in _observe(engine, "use-plain")["interaction_bindings"]
                 if row["object_id"] == "item-umbrella")
    assert [(item["verb"], item["label"], item["canonical_action"], item.get("use_mode"))
            for item in plain["actions"]] == [("consume", "使用", "consume", "use"), ("consume", "给予", "consume", None)]
    assert not is_inventory_recipe(engine.context.semantics.catalog, engine.context.rules.items["item-umbrella"])
    _hold(engine, (_RECIPE_INPUT,))
    recipe = next(row for row in _observe(engine, "use-recipe")["interaction_bindings"]
                  if row["object_id"] == _RECIPE_INPUT)
    assert [(item["verb"], item["label"], item["canonical_action"], item.get("use_mode"))
            for item in recipe["actions"]] == [("obtain", "制作", "consume", "use"), ("consume", "给予", "consume", None)]
    made = _act(engine, "use-make", "start_activity", _request((recipe["object"], "obtain", "制作")))
    assert made.status == "in_progress" and engine.context.state.activities[made.activity_id].kind == ActivityKind.CONSUME
    plan = engine.context.state.activities[made.activity_id].data["inventory_recipe"]
    assert (plan["input_object_id"], plan["output_object_id"]) == (_RECIPE_INPUT, _RECIPE_OUTPUT)
    assert Decimal(plan["input_quantity"]) == Decimal(plan["output_quantity"]) == 1
    product = engine.context.rules.items[_RECIPE_INPUT]
    assert is_inventory_recipe(engine.context.semantics.catalog, product)
    assert not is_inventory_recipe(engine.context.semantics.catalog, engine.context.rules.items["ingredient-broccoli"])
    assert not is_inventory_recipe(engine.context.semantics.catalog, engine.context.rules.items[_food(engine)])

    def broken(*args):
        """@brief 冻结目录的品类配对本身失败（目录数据不完整）。"""
        raise CategoryError("CATEGORY_NOT_FOUND", "sealed")

    monkeypatch.setattr(type(engine.context.semantics.catalog), "pairing", broken)
    with pytest.raises(IntegrityError, match="CATEGORY_NOT_FOUND"):
        is_inventory_recipe(engine.context.semantics.catalog, product)


def test_content_beyond_one_day_is_accepted_with_the_86400_second_cap(engine_factory, monkeypatch) -> None:
    """〔#19〕white-collar-12-weeks 的段子流（138264 秒）从头开始按 86400 秒受理，受理结果的 details.planned 写明计划时长；
    出行超限仍以 ROUTE_DURATION_INVALID 拒绝、不取上限，世界不变（第五章「派生时长上限」）。"""
    from worldmock.life.travel import route_for

    engine = _engine(engine_factory)
    monkeypatch.setattr("worldmock.life.travel.route_for",
                        lambda *args: route_for(*args).model_copy(update={"duration_seconds": 200_000}))
    trip = _choice(_observe(engine, "long-trip-preview"), "步行", object_id="office")
    before = _world(engine)
    too_far = _act(engine, "long-trip", "start_activity", _request(trip))
    assert (too_far.status, too_far.error_code) == ("rejected", "ROUTE_DURATION_INVALID")
    assert too_far.data["reason"] == "The route duration exceeds the maximum travel duration."
    assert _world(engine) == before
    monkeypatch.undo()
    item = next(content for content in engine.context.scenario.contents if content.item_id == "jokes-feed")
    assert item.duration_seconds == 138264
    feed = _choice(_observe(engine, "long-preview"), "接着看", object_id="jokes-feed")
    result = _act(engine, "long-watch", "start_activity", _request(feed))
    assert result.status == "in_progress", (result.error_code, result.message)
    assert engine.context.state.activities[result.activity_id].planned_duration_seconds == 86_400
    shown = last_result(_observe(engine, "long-shown"))
    assert shown["details"]["planned"] == [{"name": engine.context.state.activities[result.activity_id].name,
                                             "duration": "约 24 小时"}]


def _hold(engine: LifeEngine, products: tuple[str, ...], agent_id: int = 1) -> None:
    """@brief 在人物所在地点各放一件所列持有物（经存储落盘，保持版本连续）。"""
    staged = engine.context.state.model_copy(deep=True)
    agent = staged.agents[agent_id]
    staged.agents[agent_id] = agent.model_copy(
        update={"inventory": {agent.location_id: {product: 1.0 for product in products}}}, deep=True)
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        data={"kind": "test_parallel_admission_holdings"})])
    engine.context.state = staged


def _supply_choice(engine: LifeEngine, observed: dict, way: str, place: str) -> tuple[str, str, str]:
    """@brief 在某地出售、列出了该取得方式的一项供给（对象名，动词，方式）。"""
    return next((row["object"], action["verb"], action["label"]) for row in observed["interaction_bindings"]
                if row["kind"] == "supply" and engine.context.rules.supplies[row["object_id"]].location_id == place
                for action in row["actions"] if action["label"] == way)


def _action_evidence(engine: LifeEngine, action_id: str) -> set[tuple]:
    """@brief 一个动作的评估公开证据（类别、交互、状态、对象是否有身份），取自存储中该动作的全部事件。"""
    from worldmock.evaluation.public_evidence import worldmock_public_evidence

    rows = [event for event in engine.context.store.read_events(engine.context.store.last_event_seq)
            if event.action_id == action_id]
    return {(item.category, item.interaction, item.state, item.object_key is not None)
            for item in worldmock_public_evidence(rows, [], {})}


def test_two_foods_are_one_meal_and_only_same_way_obtains_are_one_order(engine_factory) -> None:
    """同为食用的两件持有物合成一餐、同为远程下单的两项合成一单（第五章「请求形状与菜单」）：合成的两项占用相同，
    只与已有活动比较，组内不互相冲突；到店与远程下单各自成单，不合成一单，也不以 FULFILLMENT_NOT_SUPPORTED 拒绝。"""
    engine = _engine(engine_factory, location="market", market=True)
    foods = tuple(item.product_id for item in engine.context.scenario.products if "eat" in item.use_modes)[:2]
    _hold(engine, foods)
    observed = _observe(engine, "order-preview")
    listed = [(row["object"], row["object_id"]) for row in observed["interaction_bindings"] if row["kind"] == "supply"
              and engine.context.rules.supplies[row["object_id"]].location_id == "market"]
    sellers = [engine.context.rules.supplies[supply].seller_account_id for _, supply in listed]
    seller = max(sorted(set(sellers)), key=sellers.count)  # 一单只能买同一个卖家的东西：取窗口里同一卖家的两项
    rows = [pair for pair, owner in zip(listed, sellers) if owner == seller][:2]
    assert len(rows) == 2
    one_order = _act(engine, "one-order", "start_activity",
                     _request(*((name, "obtain", "远程下单") for name, _ in rows)))
    assert one_order.status == "completed", (one_order.error_code, one_order.message)
    assert [(item["supply_id"], item["fulfillment"]) for item in one_order.data["orders"]] == [
        (supply, "delivery") for _, supply in rows]
    assert "activity_results" not in one_order.data
    mixed = _act(engine, "two-orders", "start_activity",
                 _request((rows[0][0], "obtain", "到店"), (rows[1][0], "obtain", "远程下单")))
    assert mixed.status == "completed", (mixed.error_code, mixed.message)
    assert [item["fulfillment"] for item in mixed.data["orders"]] == ["pickup", "delivery"]
    assert [len(item["orders"]) for item in mixed.data["activity_results"]] == [1, 1]
    meal = _observe(engine, "meal-preview")
    eaten = _act(engine, "meal", "start_activity",
                 _request(*(_choice(meal, "食用", kind="holding", object_id=food) for food in foods)))
    assert eaten.status == "in_progress", (eaten.error_code, eaten.message)
    activity = engine.context.state.activities[eaten.activity_id]
    assert activity.kind == ActivityKind.CONSUME
    assert [item["product_id"] for item in activity.data["request"]["items"]] == list(foods)


def test_order_routing_accepts_only_items_that_share_one_way(engine_factory) -> None:
    """拆单只接受方式相同的各项（start_activity 只把方式相同的两项取得合成一次派发）：同为远程下单的两项按收货地成一单；
    一项到店、一项远程下单交给拆单时以 FULFILLMENT_NOT_SUPPORTED 拒绝，不形成任何整单。"""
    engine = _engine(engine_factory, location="market", market=True)
    observed = _observe(engine, "routing-preview")
    rows = [row for row in observed["interaction_bindings"] if row["kind"] == "supply"
            and engine.context.rules.supplies[row["object_id"]].location_id == "market"][:2]

    def obtained(row: dict, fulfillment: str) -> dict:
        """一项取得的路由行：本轮绑定、所选方式与收货人（自己）。"""
        action = next(item for item in row["actions"] if item.get("fulfillment") == fulfillment)
        return {**row, "selected_action": action, "recipient_id": agent_identity(1)}

    same = order_routing.order_requests(engine.context.state, engine.context.semantics, engine.context.rules.supplies, 1,
                                        [obtained(row, "delivery") for row in rows])
    assert [(order.fulfillment, [item.supply_id for item in order.items]) for order in same] == [
        ("delivery", [row["object_id"] for row in rows])]
    with pytest.raises(BusinessRuleError, match="FULFILLMENT_NOT_SUPPORTED"):
        order_routing.order_requests(engine.context.state, engine.context.semantics, engine.context.rules.supplies, 1,
                                     [obtained(rows[0], "pickup"), obtained(rows[1], "delivery")])


def test_two_makes_are_not_merged_and_two_uses_are_refused_with_a_reason(engine_factory) -> None:
    """@brief 两次选择同一真实配方不会合并，严格请求直接拒绝重复；两件普通物品使用按单件使用合同整组拒绝。"""
    engine = _engine(engine_factory, location="home")
    items = ("item-umbrella", "item-notebook")
    _hold(engine, items)
    observed = _observe(engine, "use-preview")
    used = _rejected(engine, "use-two", _request(*(_choice(observed, "使用", kind="holding", object_id=item)
                                                   for item in items)), "ACTION_REQUEST_INVALID")
    assert (used.data["reason"], used.data["next_step"]) == (
        "Only one non-food item can be used at a time.", "Select one item.")
    _hold(engine, (_RECIPE_INPUT,))
    observed = _observe(engine, "make-preview")
    recipe = _choice(observed, "制作", kind="holding", object_id=_RECIPE_INPUT)
    before = _world(engine)
    with pytest.raises(ValidationError, match="a target may repeat only with a different recipient"):
        _request(recipe, recipe)
    assert _world(engine) == before


@pytest.mark.parametrize("case", ["eat", "lifestyle"])
@pytest.mark.parametrize("walk_first", [True, False])
def test_holding_or_lifestyle_with_a_walk_is_refused_in_either_order(engine_factory, case, walk_first) -> None:
    """要停在一个地点做的事按整组开始之后人在哪里判定：食用持有物与步行同一请求以 PRODUCT_NOT_HELD（出发后手边没有
    可用的持有物，先用完再出发）拒绝，生活活动与步行同一请求以 ACTIVITY_PRECONDITION_UNMET（先做完再出发）拒绝，
    结果与组内先后无关，世界不变（第五章「并行准入」）。"""
    engine = _engine(engine_factory, location="home")
    if case == "eat":
        _stage(engine, holding=_food(engine))
    observed = _observe(engine, "stay-preview")
    walk = _choice(observed, "步行", object_id="office")
    other = _choice(observed, "食用", kind="holding") if case == "eat" else next(
        (row["object"], "interact", "进行") for row in observed["interaction_bindings"]
        if row["kind"] == "lifestyle" and row["actions"][0]["note"] is None)
    pair = (walk, other) if walk_first else (other, walk)
    code, reason, step = (("PRODUCT_NOT_HELD", "Held items cannot be used after departure.",
                          "Use the item before departure.") if case == "eat"
                         else (_UNMET, f"进行{other[0].split('：', 1)[1]} requires a stationary location "
                               "and cannot start with travel.", "Finish the activity before departure."))
    result = _rejected(engine, "stay", _request(*pair), code)
    assert (result.data["reason"], result.data["next_step"]) == (reason, step)


def test_pickup_and_remote_order_stay_listed_away_from_the_store(engine_factory) -> None:
    """到店与远程下单不按人物此刻在哪里剪裁（第五章「动作照常列出，做错了再说明」）：人不在店里时供给照样列出「到店」，说明需要在
    那家店，选了以 ACTIVITY_PRECONDITION_UNMET 返回原因与下一步「去<店>」；支持配送的照样列出「远程下单」；只能到店的
    供给同样列出「到店」。"""
    engine = _engine(engine_factory, location="home", market=True)
    observed = _observe(engine, "away-preview")
    market = next(item.name for item in engine.context.scenario.locations if item.location_id == "market")
    row = next(row for row in observed["interaction_bindings"] if row["kind"] == "supply"
               and engine.context.rules.supplies[row["object_id"]].location_id == "market")
    notes = {action["label"]: action["note"] for action in row["actions"]}
    assert notes == {"到店": f"到店{row['object']} requires presence at {market}.", "远程下单": None}
    pickup_only = [row for row in observed["interaction_bindings"] if row["kind"] == "supply" and row["actions"]
                   and engine.context.rules.supplies[row["object_id"]].fulfillment_modes == ("pickup",)]
    assert pickup_only and all([action["label"] for action in item["actions"]] == ["到店"] for item in pickup_only)
    result = _rejected(engine, "away-pickup", _request((row["object"], "obtain", "到店")), _UNMET)
    assert (result.data["reason"], result.data["next_step"]) == (
        f"到店{row['object']} requires presence at {market}.", f"Go to {market}.")


def test_remote_order_where_it_cannot_be_delivered_is_noted_and_refused_naming_where_to_go(engine_factory) -> None:
    """远程下单的送达地取 agent.location_id：这里不在该供给的可配送目的地时，动作照常列出并带「这里不在这家店的配送范围内」，
    选了以 ACTIVITY_PRECONDITION_UNMET（unmet=delivery_destination）返回同一原因与能送达、从这里可达的地点；送达地在范围内
    而门店到这里的配送路线缺失时换成 delivery_route 与另一句原因；说明与拒绝出自同一判定（第八章「呈现」，
    docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-浏览结果与提示看可达性 ）。"""
    from worldmock.life import admission
    from worldmock.life.guidance import DELIVERY_DESTINATION_REASON, DELIVERY_ROUTE_REASON

    engine = _engine(engine_factory, location="home", market=True)
    observed = _observe(engine, "reach-preview")
    row = next(row for row in observed["interaction_bindings"] if row["kind"] == "supply"
               and any(action["label"] == "远程下单" for action in row["actions"]))
    action = next(action for action in row["actions"] if action["label"] == "远程下单")
    supply = engine.context.rules.supplies[row["object_id"]]
    places = {item.location_id: item.name for item in engine.context.scenario.locations}
    context_ = admission.Context(state=engine.context.state, agent_id=1, place_names=places, routes=engine.context.scenario.routes)

    def item_for(destinations: tuple[str, ...]) -> admission.Item:
        """@brief 可配送目的地换成 destinations 的同一项远程下单。"""
        changed = supply.model_copy(update={"delivery_destination_ids": destinations})
        facts = admission.WorldFacts(tasks={}, supplies={row["object_id"]: changed}, items={}, contents={})
        return admission.selection_item(row, action, facts)

    refusal = admission.group_refusal(context_, [item_for(("office", "market"))])
    assert (refusal.code, refusal.reason, refusal.unmet) == (_UNMET, DELIVERY_DESTINATION_REASON,
                                                             ("delivery_destination",))
    assert refusal.next_step == f"Go to {places['office']} or {places['market']}, then place the order."
    assert admission.group_refusal(context_, [item_for(("office",))]).reason == DELIVERY_DESTINATION_REASON
    assert admission.group_refusal(context_, [item_for(("home",))]) is None
    blocked = tuple(route.model_copy(update={"status": "unknown", "distance_meters": None, "duration_seconds": None,
                                             "fare_fen": None, "reason": "no_route_record"})
                    if (route.origin_id, route.destination_id, route.mode.value) == (supply.location_id, "home",
                                                                                     "driving") else route
                    for route in engine.context.scenario.routes)
    routed = admission.Context(state=engine.context.state, agent_id=1, place_names=places, routes=blocked)
    refusal = admission.group_refusal(routed, [item_for(("home", "office"))])
    assert (refusal.code, refusal.reason, refusal.unmet) == (_UNMET, DELIVERY_ROUTE_REASON, ("delivery_route",))
    assert refusal.next_step == f"Go to {places['office']}, then place the order."


@pytest.mark.parametrize("companion", ["步行", "等待"])
def test_an_instant_obtain_joins_a_timed_activity_in_one_request(engine_factory, companion) -> None:
    """瞬时的取得不占名额，可与持续活动同一请求（第五章「并行准入」）：远程下单当场成单并付款，步行或等待照常开始、
    按自己的方式命名并归入会话；评估证据逐项对应，订单与付款归订单，活动的终态不再算作这项订单的结果。"""
    engine = _engine(engine_factory, location="home", market=True)
    observed = _observe(engine, "mixed-preview")
    other = _choice(observed, "步行", object_id="office") if companion == "步行" else _choice(observed, "等待")
    result = _act(engine, "mixed", "start_activity", _request(_supply_choice(engine, observed, "远程下单", "market"),
                                                               other))
    assert result.status == "in_progress", (result.error_code, result.message)
    [started] = result.data["activities"]
    activity = engine.context.state.activities[started["activity_id"]]
    assert activity.name.startswith("步行前往" if companion == "步行" else "等待") and activity.session_ids
    [delivery] = engine.context.state.deliveries.values()
    assert delivery.destination_id == "home" and [item["order_id"] for item in result.data["orders"]] == [
        delivery.order_id]
    agent_account = f"account:{engine.context.state.agents[1].balance_account_id}"
    assert result.data["resource_changes"][agent_account] < 0
    orders = {("purchase", "order", "requested", True), ("purchase", "order", "pending", True),
              ("purchase", "pay", "paid", True)}
    assert {row for row in _action_evidence(engine, "action-mixed-1") if row[0] == "purchase"} == orders
    if companion == "步行":
        _live_until(engine, activity.planned_end_at)
        assert engine.context.state.activities[activity.activity_id].status == ActivityStatus.COMPLETED
        evidence = _action_evidence(engine, "action-mixed-1")
        assert {row for row in evidence if row[0] == "purchase"} == orders
        assert {row[1:] for row in evidence if row[0] != "purchase"} >= {
            ("travel", "requested", True), ("travel", "started", True), ("travel", "completed", True)}


def test_making_with_a_pickup_at_the_store_places_the_order_and_starts_the_making(engine_factory) -> None:
    """在店里「制作」与「到店」同一请求（第五章组合表：到店取得要求人在店里）：到店当场成单、商品进入店里的持有物，
    制作照常开始，只有制作占名额。"""
    engine = _engine(engine_factory, location="market", market=True)
    _hold(engine, (_RECIPE_INPUT,))
    observed = _observe(engine, "make-pickup-preview")
    make = _choice(observed, "制作", kind="holding", object_id=_RECIPE_INPUT)
    result = _act(engine, "make-pickup", "start_activity",
                  _request(make, _supply_choice(engine, observed, "到店", "market")))
    assert result.status == "in_progress", (result.error_code, result.message)
    [started] = result.data["activities"]
    activity = engine.context.state.activities[started["activity_id"]]
    assert activity.kind == ActivityKind.CONSUME
    recipe = activity.data["inventory_recipe"]
    assert (recipe["input_object_id"], recipe["output_object_id"]) == (_RECIPE_INPUT, _RECIPE_OUTPUT)
    [order] = result.data["orders"]
    assert order["fulfillment"] == "pickup" and engine.context.state.agents[1].inventory["market"][order["product_id"]] == 1
    assert [item.kind for item in active_activities(engine.context.state, 1)] == [ActivityKind.CONSUME]
    assert engine.context.state.agents[1].inventory["market"][_RECIPE_INPUT] == 1
    assert engine.context.state.agents[1].inventory["market"].get(_RECIPE_OUTPUT, 0) == 0
    _live_until(engine, activity.planned_end_at)
    inventory = engine.context.state.agents[1].inventory["market"]
    assert inventory.get(_RECIPE_INPUT, 0) == 0 and inventory[_RECIPE_OUTPUT] == 1
    assert engine.context.state.activities[activity.activity_id].data["recipe_applied"] is True


@pytest.mark.parametrize("drive_first", [True, False])
def test_driving_with_a_remote_order_in_one_request_says_order_first(engine_factory, drive_first) -> None:
    """驾车与远程下单同一请求：下单要手动操作，以 ACTIVITY_PRECONDITION_UNMET 整组拒绝，原因「开车时不能下单」、
    下一步「先下单，再出发」，与组内先后无关，世界不变（第五章组合表「驾车时发消息、远程下单、提交成果」）。"""
    engine = _engine(engine_factory, location="home", market=True)
    observed = _observe(engine, "drive-order-preview")
    drive = _choice(observed, "驾车", object_id="office")
    order = _supply_choice(engine, observed, "远程下单", "market")
    result = _rejected(engine, "drive-order", _request(*((drive, order) if drive_first else (order, drive))), _UNMET)
    assert (result.data["reason"], result.data["next_step"], result.data["unmet"]) == (
        "Orders cannot be placed while driving.", "Place the order before departure.", ["hands_free"])


def test_game_sessions_to_watch_are_listed_to_resume_or_restart_with_their_length(engine_factory) -> None:
    """只能观看的游戏会话与内容对象同一呈现（第八章「呈现」）：方式为「接着看」或「从头再看」，工具层动作的 duration 写
    全长或剩余时长；剩余不足 60 秒的给全长并从头派生。"""
    from worldmock.life.models import ContentProgress
    from worldmock.life.readable import duration_phrase

    engine = _engine(engine_factory, location="home")
    preview = _observe(engine, "game-preview")
    row = next(row for row in preview["interaction_bindings"]
               if row["kind"] == "game" and row["actions"][0]["canonical_action"] == "watch")
    item = engine.context.rules.content(row["object_id"])

    def listed(position: int) -> list[tuple]:
        """@brief 观看进度为 position 秒时这一游戏会话列出的动作。"""
        staged = engine.context.state.model_copy(deep=True)
        staged.agents[1].content_progress[item.item_id] = ContentProgress(
            item_id=item.item_id, position_seconds=position, exposure_count=1)
        staged.version += 1
        engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
            event_type="environment_event", occurred_at=staged.current_time, visibility="system",
            data={"kind": "test_game_session_progress"})])
        engine.context.state = staged
        seen = _observe(engine, f"game-{position}")
        return [(action["action"], action["way"], action["duration"])
                for action in tool_targets(seen)[row["object"]]["actions"]]

    assert [(action["action"], action["way"], action["duration"])
            for action in tool_targets(preview)[row["object"]]["actions"]] == [
        ("interact", "接着看", duration_phrase(item.duration_seconds))]
    half = item.duration_seconds // 2
    assert listed(half) == [("interact", "接着看", duration_phrase(item.duration_seconds - half))]
    assert listed(item.duration_seconds - 30) == [("interact", "从头再看", duration_phrase(item.duration_seconds))]
    again = _act(engine, "game-restart", "start_activity", _request((row["object"], "interact", "从头再看")))
    assert again.status == "in_progress", (again.error_code, again.message)
    assert engine.context.state.activities[again.activity_id].planned_duration_seconds == item.duration_seconds


@pytest.mark.parametrize("fields,path,written,reason", [
    ({"items": [], "answering": None, "prefer": None}, ["items"], [],
     "items requires one or two activity objects; acceptance is atomic"),
    ({"items": [{"target": 7, "way": "等待", "from": None, "to": None}], "answering": None, "prefer": None},
     ["items", 0, "target"], 7, "A JSON string is required."),
    ({"items": [{"target": "自己", "way": "等待", "from": None}], "answering": None, "prefer": None},
     ["items", 0, "to"], None, "This required field was omitted."),
], ids=["empty-items", "wrong-target-type", "missing-null-field"])
def test_malformed_request_gets_the_default_reason_without_the_validation_text(
        engine_factory, fields, path, written, reason) -> None:
    """@brief 绕过构造期校验仍由真实引擎拒绝，逐项公开完整位置、原始值和英文格式条件，不泄露库转储；世界不变。"""
    engine = _engine(engine_factory)
    malformed = StartActivityRequest.model_construct(**fields)
    before = _world(engine)
    result = _act(engine, "malformed", "start_activity", malformed)
    assert (result.status, result.error_code) == ("rejected", "ACTION_REQUEST_INVALID")
    assert _world(engine) == before
    assert result.message == result.data["reason"]
    assert not any(text in result.message for text in ("validation", "Action rejected", "ACTION_REQUEST_INVALID"))
    assert result.data["reason"] == reason
    assert result.data["fix"] == "Correct every reported parameter."
    [item] = result.data["items"]
    assert item["path"] == ["tool_calls", 0, "function", "arguments", *path] and item["written"] == written
    assert item["error_code"] == "ACTION_REQUEST_INVALID" and item["why"] == reason
    assert item["expected"] and item["allowed_values"] == [] and item["candidates"] == []
    shown = last_result(_observe(engine, "malformed-shown"))["details"]
    assert (shown["reason"], shown["next_step"]) == (result.data["reason"], result.data["next_step"])
    assert shown["items"] == result.data["items"]


def test_a_repeated_observation_of_one_decision_is_ambiguous_on_every_read(engine_factory) -> None:
    """名称与报价只从本决策唯一的已提交观察解析，每次都重读存储：同一决策再提交一次观察后，此前读到过的观察也不再
    唯一，以 OBSERVATION_NOT_FOUND 拒绝，不沿用任何缓存。"""
    from types import SimpleNamespace

    from worldmock.errors import BusinessRuleError

    engine = _engine(engine_factory)
    decision = context(engine, "twice")
    engine.observe(decision)
    pending = SimpleNamespace(agent_id=1, decision_id=decision.decision_id, observed_at=engine.context.state.current_time)
    assert engine.actions._frozen_observation(pending)["interaction_bindings"]
    engine.observe(decision)
    with pytest.raises(BusinessRuleError, match="OBSERVATION_NOT_FOUND"):
        engine.actions._frozen_observation(pending)

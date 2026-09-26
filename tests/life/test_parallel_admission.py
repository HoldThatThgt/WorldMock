"""第五章「并行准入」的验收：组合表逐行、途中的地点与菜单、整组拒绝不变量、公开原因与下一步、制作与使用、
超长派生时长（docs/dev/SPEC-time-and-energy.md 第五章；裁决 #19、#20、#23）。

全部经真实引擎：观察冻结的私有绑定按「动词＋对象＋方式」解析，整组先按名额、目标在场与实际地点、出行方式、占用、
物品状态的顺序判定再派发；拒绝写 data.reason 与 data.next_step，并在下一次观察的 facts.progress.results 中呈现。
"""

from __future__ import annotations

from datetime import datetime

import pytest
from agentsociety2.society import DirectToolCall

from tests.life.conftest import advance_next, context, stage_day_plan, submit_round
from worldmock.footprints.models import EventDraft
from worldmock.life.engine import LifeEngine
from worldmock.life.interaction_menu import WAYS, menu_allows_action, resolve_interaction_menu
from worldmock.life.models import (ActivityKind, ActivityStatus, MeetingMinutes, SendMessageActionRequest,
                                   StartActivityRequest, SubmitWorkActionRequest, active_activities)

_OPEN = int(datetime.fromisoformat("2026-01-05T10:00:00+08:00").timestamp())
_UNMET, _CONFLICT = "ACTIVITY_PRECONDITION_UNMET", "RESOURCE_CONFLICT"


def _stage(engine: LifeEngine, agent_id: int = 1, *, location: str | None = None, task: bool = False,
           holding: str | None = None, market: bool = False) -> None:
    """@brief 把隔离运行推进到 1 月 5 日 10:00（任务开放、门店营业），按需把人物放到某地、公开当日任务、手边放一份
    食品并呈现全部门店供给（经存储落盘，保持版本连续）。"""
    staged = engine.state.model_copy(deep=True)
    now = max(staged.current_time, _OPEN)
    staged.current_time = now
    staged.pending_events = [event for event in staged.pending_events if event.due_at > now]
    agent = staged.agents[agent_id]
    place = location or agent.location_id
    update = {"location_id": place, "next_decision_at": now, "body_settled_at": now}
    if task:
        update["visible_task_ids"] = {*agent.visible_task_ids, engine.scenario.tasks[0].task_id}
    if holding is not None:
        update["inventory"] = {place: {holding: 1.0}}
    if market:
        visible = {item.supply_id for item in engine.scenario.supplies}
        update.update(visible_supply_ids=visible, supply_browsed_at={key: now for key in visible},
                      supply_browsed_location={key: place for key in visible})
    staged.agents[agent_id] = agent.model_copy(update=update, deep=True)
    for other in staged.agents.values():
        other.body_settled_at = now
        other.next_decision_at = now
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=now, visibility="system", source_kind="scenario",
        data={"kind": "test_parallel_admission_staged"})])
    engine.state = staged


def _engine(engine_factory, scenario: str = "white-collar-12-weeks.json", **stage) -> LifeEngine:
    """@brief 登记空计划的运行，并按需预置地点、任务、持有物与门店供给。"""
    engine = engine_factory(scenario)
    for agent_id in engine.state.agents:
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
    return StartActivityRequest(targets=[item[0] for item in choices], actions=[item[1] for item in choices],
                                ways=[item[2] for item in choices])


def _act(engine: LifeEngine, name: str, tool: str, request, agent_id: int = 1):
    """@brief 按真实顺序观察、登记并结算一轮，返回该人物的结果；同一秒就绪的其他人物（多人场景）开始等待。"""
    ready = engine.scheduling_state().ready_agent_ids or (agent_id,)
    others = {other: ("start_activity", StartActivityRequest(targets=["自己"], actions=["interact"], ways=["等待"]))
              for other in ready if other != agent_id}
    _, results = submit_round(engine, {agent_id: (tool, request), **others}, name)
    return next(item for item in results if item.agent_id == agent_id)


def _world(engine: LifeEngine, agent_id: int = 1) -> tuple:
    """@brief 整组拒绝要保持不变的世界事实：账户余额、库存、位置、活动与订单。"""
    agent = engine.state.agents[agent_id]
    activities = {key: (item.status, item.elapsed_seconds) for key, item in engine.state.activities.items()
                  if item.agent_id == agent_id}
    return (dict(engine.state.accounts), agent.inventory, agent.location_id, activities, dict(engine.state.deliveries),
            dict(engine.state.supply_stock))


def _rejected(engine: LifeEngine, name: str, request, code: str, tool: str = "start_activity"):
    """@brief 断言整组拒绝：错误码、世界不变，原因与下一步随下一次观察进入 facts.progress.results[].details。"""
    before = _world(engine)
    result = _act(engine, name, tool, request)
    assert result.status == "rejected" and result.error_code == code, (result.error_code, result.message)
    assert _world(engine) == before
    assert result.data["reason"] and result.data["next_step"]
    shown = _observe(engine, f"{name}-shown")["facts"]["progress"]["results"][-1]
    assert shown["error_code"] == code
    assert shown["details"]["reason"] == result.data["reason"]
    assert shown["details"]["next_step"] == result.data["next_step"]
    for text in (result.data["reason"], result.data["next_step"]):
        assert "agent:" not in text and "office" not in text and "supply-" not in text
    return result


def _food(engine: LifeEngine) -> str:
    """@brief 一件可食用的商品身份。"""
    return next(item.product_id for item in engine.scenario.products if "eat" in item.use_modes)


@pytest.mark.parametrize("companion", ["podcast", "call"])
def test_driving_with_podcast_or_hands_free_call_is_accepted_and_ends_at_arrival(engine_factory, companion) -> None:
    """驾车与播客、免提通话同一请求开始即受理，归入路上会话；到达的同一秒一并结束并成为待收尾（组合表第一行）。"""
    engine = _engine(engine_factory)
    observed = _observe(engine, "road-preview")
    drive = _choice(observed, "驾车", object_id="office")
    other = _choice(observed, "接着听") if companion == "podcast" else _choice(observed, "通话", kind="contact")
    result = _act(engine, "road-start", "start_activity", _request(drive, other))
    assert result.status == "in_progress", (result.error_code, result.message)
    travel, partner = (engine.state.activities[row["activity_id"]] for row in result.data["activities"])
    assert travel.kind == ActivityKind.TRAVEL and partner.session_ids == travel.session_ids
    staged = engine.state.model_copy(deep=True)
    longer = travel.planned_end_at - partner.started_at + 600
    staged.activities[partner.activity_id] = partner.model_copy(
        update={"planned_end_at": partner.started_at + longer, "planned_duration_seconds": longer}, deep=True)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        data={"kind": "test_companion_outlasts_trip"})])
    engine.state = staged
    while engine.state.activities[travel.activity_id].status == ActivityStatus.ACTIVE:
        assert not engine.scheduling_state().ready_agent_ids or engine.state.current_time < travel.planned_end_at
        advance_next(engine)
    arrived = engine.state.activities[travel.activity_id]
    ended = engine.state.activities[partner.activity_id]
    assert arrived.status == ActivityStatus.COMPLETED and engine.state.agents[1].location_id == "office"
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
    assert result.data["next_step"] == "一次只选其中一件"


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
    boundary = engine.state.current_time
    events = engine.store.read_agent_events("environment_event", 1)
    body = [event for event in events if event.data.get("kind") == "body_settlement"
            and event.data["interval_end"] == boundary]
    assert len(body) == 1 and set(started) <= set(body[0].data["activity_ids"])
    progress = [event for event in engine.store.read_agent_events("activity_progress", 1)
                if event.occurred_at == boundary]
    assert sorted(event.activity_id for event in progress) == sorted(started)
    assert all(event.data["body_settlement_event_id"] == body[0].event_id for event in progress)
    assert all(engine.state.activities[key].elapsed_seconds > 0 for key in started)


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
    assert advance_until_ready(engine) and active_activities(engine.state, 1)[0].kind == ActivityKind.TRAVEL


def test_driving_rejects_messages_remote_orders_and_submissions(engine_factory) -> None:
    """驾车途中需要手动操作的瞬时动作（发消息、远程下单、提交成果）以 ACTIVITY_PRECONDITION_UNMET 拒绝，原因与下一步
    写明到达后再做；远程下单照常列出并带同一说明（组合表第六行）。"""
    engine = _engine(engine_factory, location="office", task=True, market=True)
    _driving(engine, "hands")
    observed = _observe(engine, "hands-preview")
    order = next((row["object"], action) for row in observed["interaction_bindings"] if row["kind"] == "supply"
                 for action in row["actions"] if action["label"] == "远程下单")
    assert order[1]["note"] == "开车时不能下单"
    contact = next(row["object"] for row in observed["interaction_bindings"] if row["kind"] == "contact")
    message = _rejected(engine, "hands-message", SendMessageActionRequest(recipient_name=contact, text="在路上"),
                        _UNMET, tool="send_message")
    assert message.data["reason"] == "开车时不能发消息" and message.data["next_step"].startswith("到达")
    _rejected(engine, "hands-order", _request((order[0], "obtain", "远程下单")), _UNMET)
    minutes = MeetingMinutes(summary="会议纪要", decisions=("按计划推进",), action_items=("整理材料",))
    submission = SubmitWorkActionRequest(task_name=engine.scenario.tasks[0].title, deliverable=minutes)
    submitted = _rejected(engine, "hands-submit", submission, _UNMET, tool="submit_work")
    assert submitted.data["reason"] == "开车时不能提交成果"


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
    assert advance_until_ready(engine) and active_activities(engine.state, 1)[0].kind == ActivityKind.TRAVEL
    order = _choice(_observe(engine, "walk-order-preview"), "远程下单", kind="supply")
    bought = _act(engine, "walk-order", "start_activity", _request(order))
    assert bought.status == "completed", (bought.error_code, bought.message)
    [delivery] = engine.state.deliveries.values()
    assert delivery.destination_id == "office" and delivery.status == "pending"
    assert engine.state.agents[1].location_id == "office" and not engine.state.agents[1].inventory.get("home")
    assert not engine.state.agents[1].inventory.get("office", {}).get(delivery.product_id)
    _live_until(engine, delivery.expected_at)
    inventory = engine.state.agents[1].inventory
    assert engine.state.deliveries[delivery.order_id].status == "delivered"
    assert inventory["office"][delivery.product_id] == 1 and not inventory.get("home", {}).get(delivery.product_id)
    assert engine.state.agents[1].location_id == "home"


def _live_until(engine: LifeEngine, moment: int) -> None:
    """@brief 真实推进到 moment：到达或活动结束后当秒收尾，有进行中活动时照常播报，空闲时等待。"""
    from tests.life.conftest import activity_name, conclude_pending, start_activity
    from worldmock.life import activity_control
    from worldmock.life.models import ReportStatusRequest

    for step in range(64):
        if engine.state.current_time >= moment:
            return
        if not engine.scheduling_state().ready_agent_ids:
            advance_next(engine)
        elif activity_control.pending_conclusions(engine.state, 1):
            conclude_pending(engine, f"live-conclude-{step}")
        elif active_activities(engine.state, 1):
            name = activity_name(engine, active_activities(engine.state, 1)[0].activity_id)
            _act(engine, f"live-report-{step}", "report_status", ReportStatusRequest(task=[name], summary=["继续。"]))
        else:
            _act(engine, f"live-wait-{step}", "start_activity", start_activity("等待", "自己"))
    raise AssertionError("the run did not reach the moment")


def test_en_route_person_is_nowhere_with_nothing_at_hand(engine_factory) -> None:
    """途中不在任何地点：身边没有人、手边没有持有物，出发地的持有物在 facts.inventory 中注明不在手边，生活活动带
    「路上做不了」说明，办公室工作带「需要在…」说明；菜单仍按出发地解析，路上的持有物食用在占用判定之后才报物品状态
    （驾车报 RESOURCE_CONFLICT，步行报 PRODUCT_NOT_HELD）。"""
    from worldmock.life import admission

    engine = _engine(engine_factory, "multi-person.json")
    _stage(engine, holding=_food(engine))
    before = _observe(engine, "nowhere-before")
    assert any(row["kind"] == "person" for row in before["interaction_bindings"])
    assert any(row["kind"] == "holding" for row in before["interaction_bindings"])
    assert before["facts"]["inventory"] and not any("note" in row for row in before["facts"]["inventory"])
    _driving(engine, "nowhere")
    during = _observe(engine, "nowhere-during")
    assert during["facts"]["people_here"] == [] and during["scene"]["inventory"] == []
    assert not [row for row in during["interaction_bindings"] if row["kind"] in {"person", "holding"}]
    assert [row["note"] for row in during["facts"]["inventory"]] == [
        f"不在手边，只能在{row['place']}使用" for row in before["facts"]["inventory"]]
    lifestyle = [row for row in during["interaction_bindings"] if row["kind"] == "lifestyle"]
    assert lifestyle and all(row["actions"][0]["note"].endswith("要停在一个地方做，路上做不了") for row in lifestyle)
    origin = engine.state.agents[1].location_id
    assert during["interaction_menu"] == resolve_interaction_menu(engine.scenario, 1, origin).model_dump(mode="json")
    context_ = admission.Context(state=engine.state, agent_id=1, place_names={})
    eat = admission.Item(canonical="consume", way="食用", label="食用牛奶", claims=frozenset({"hands"}), holding=True)
    assert admission.group_refusal(context_, [eat]).code == _CONFLICT
    walking = [item for item in engine.state.activities.values() if item.kind == ActivityKind.TRAVEL][0]
    staged = engine.state.model_copy(deep=True)
    staged.activities[walking.activity_id] = walking.model_copy(update={"resource_claims": {"travel", "idle"}})
    staged.activities[walking.activity_id].travel_mode = None
    refusal = admission.group_refusal(admission.Context(state=staged, agent_id=1, place_names={}), [eat])
    assert refusal.code == "PRODUCT_NOT_HELD" and refusal.reason == "路上没有可用的持有物"


def test_departure_with_a_running_activity_names_it_in_reason_and_next_step(engine_factory) -> None:
    """出发前须先结束或暂停进行中的活动：出行照常列出并带说明，选了以 ACTIVITY_PRECONDITION_UNMET 返回原因与下一步，
    都点名要先处理的活动（第五章「动作照常列出，做错了再说明」、第七章）。"""
    from tests.life.conftest import start_activity, wake_by_mail, advance_until_ready

    engine = _engine(engine_factory)
    waited = _act(engine, "busy-wait", "start_activity", start_activity("等待", "自己"))
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    name = engine.state.activities[waited.activity_id].name
    observed = _observe(engine, "busy-preview")
    trip = next(action for row in observed["interaction_bindings"] if row["object_id"] == "office"
                for action in row["actions"] if action["label"] == "步行")
    assert trip["note"] == f"需要先结束或暂停手上的事：{name}"
    result = _rejected(engine, "busy-go", _request(_choice(observed, "步行", object_id="office")), _UNMET)
    assert result.data["next_step"] == f"先结束或暂停{name}"


def test_zero_stock_supply_is_listed_without_actions_and_left_out_of_the_targets_enum(engine_factory) -> None:
    """呈现的供给库存为 0（按世界事实不能买）时对象照常列出、动作为空列表，宿主不把它放进 targets 枚举；
    决策照常继续，没有 run_failed（第五章「按世界事实做的过滤保留」、第九章「仍须修复」第 1 条）。"""
    from custom.agents.life_agent import _bind_start_activity_schema, _visible_interactive_objects
    from tests.life.conftest import start_activity

    from worldmock.life.models import SameMomentPresentation

    engine = _engine(engine_factory, location="market", market=True)
    supply = next(item.supply_id for item in engine.scenario.supplies if item.location_id == "market")
    staged = engine.state.model_copy(deep=True)
    staged.supply_stock[supply] = 0
    staged.same_moment_presentation[1] = SameMomentPresentation(stamps={}, pinned_supply_ids=(supply,))
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        data={"kind": "test_sold_out"})])
    engine.state = staged
    observed = _observe(engine, "sold-out")
    name = next(row["object"] for row in observed["interaction_bindings"] if row["object_id"] == supply)
    assert {"object": name, "actions": []} in observed["facts"]["interactive_objects"]
    objects = _visible_interactive_objects(observed)
    assert objects[name] == () and "自己" in objects
    schema = [{"function": {"name": "start_activity", "parameters": {"properties": {
        key: {"items": {}} for key in ("targets", "actions", "ways")}}}}]
    _bind_start_activity_schema(schema, objects, frozenset(observed["interaction_menu"]["allowed_actions"]))
    enums = schema[0]["function"]["parameters"]["properties"]
    assert name not in enums["targets"]["items"]["enum"] and "自己" in enums["targets"]["items"]["enum"]
    assert _act(engine, "sold-out-wait", "start_activity", start_activity("等待", "自己")).status == "in_progress"
    assert not engine.store.read_agent_events("run_failed", 1)


def test_listed_actions_are_menu_allowed_and_self_is_always_offered(engine_factory) -> None:
    """交互对象层恒含「自己」（等待、睡觉）；列出的每个动作的内部动作都在有效菜单内，所以不会以 INTERACTION_MENU_DENIED
    拒绝；说明为空的动作与准入判定一致（第五章「动作照常列出，做错了再说明」）。"""
    engine = _engine(engine_factory, location="office", task=True, market=True)
    observed = _observe(engine, "listed")
    menu = resolve_interaction_menu(engine.scenario, 1, "office")
    self_row = next(row for row in observed["facts"]["interactive_objects"] if row["object"] == "自己")
    assert [item["action"] for item in self_row["actions"]] == ["interact：等待", "sleep：睡觉"]
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
    from types import SimpleNamespace

    from worldmock.categories import CategoryError, InventoryTransformRule, PairingState
    from worldmock.errors import IntegrityError
    from worldmock.life.inventory_recipe import is_inventory_recipe

    engine = _engine(engine_factory, holding="item-umbrella")
    plain = next(row for row in _observe(engine, "use-plain")["interaction_bindings"]
                 if row["object_id"] == "item-umbrella")
    assert [(item["verb"], item["label"], item["canonical_action"], item["use_mode"]) for item in plain["actions"]] \
        == [("consume", "使用", "consume", "use")]
    assert not is_inventory_recipe(engine.semantics.catalog, engine.rules.items["item-umbrella"])
    monkeypatch.setattr("worldmock.life.observe.is_inventory_recipe",
                        lambda catalog, product: product.product_id == "item-umbrella")
    recipe = next(row for row in _observe(engine, "use-recipe")["interaction_bindings"]
                  if row["object_id"] == "item-umbrella")
    assert [(item["verb"], item["label"], item["canonical_action"], item["use_mode"]) for item in recipe["actions"]] \
        == [("obtain", "制作", "consume", "use")]
    made = _act(engine, "use-make", "start_activity", _request((recipe["object"], "obtain", "制作")))
    assert made.status == "in_progress" and engine.state.activities[made.activity_id].kind == ActivityKind.CONSUME
    rule = InventoryTransformRule.model_validate({
        "rule_id": "open-item", "category_id": "sealed", "action": "consume:use", "operator": "inventory_transform",
        "resource_id": "inventory_closed", "unit": "item", "conservation": "closed", "input_category_id": "sealed",
        "output_category_id": "opened", "input_quantity": 1, "output_quantity": 1, "inventory_unit": "item"})
    catalog = SimpleNamespace(
        pairing=lambda *args: SimpleNamespace(state=PairingState.DEFINED, rule_id="open-item", coefficient=1),
        exchange_rules=SimpleNamespace(require=lambda rule_id: rule))
    no_effect = SimpleNamespace(pairing=lambda *args: SimpleNamespace(state=PairingState.NO_EFFECT))
    sealed = SimpleNamespace(category_id="sealed", use_modes=("use",))
    assert is_inventory_recipe(catalog, sealed) and not is_inventory_recipe(no_effect, sealed)
    assert not is_inventory_recipe(catalog, SimpleNamespace(category_id="sealed", use_modes=("eat",)))

    def broken(*args):
        """@brief 冻结目录的品类配对本身失败（目录数据不完整）。"""
        raise CategoryError("CATEGORY_NOT_FOUND", "sealed")

    with pytest.raises(IntegrityError, match="CATEGORY_NOT_FOUND"):
        is_inventory_recipe(SimpleNamespace(pairing=broken), sealed)


def test_content_beyond_one_day_is_accepted_with_the_86400_second_cap(engine_factory, monkeypatch) -> None:
    """〔#19〕white-collar-12-weeks 的段子流（138264 秒）从头开始按 86400 秒受理，受理结果的 details.planned 写明计划时长；
    出行超限仍以 ROUTE_DURATION_INVALID 拒绝、不取上限，世界不变（第五章「派生时长上限」）。"""
    from types import SimpleNamespace

    engine = _engine(engine_factory)
    monkeypatch.setattr("worldmock.life.session_actions.route_for",
                        lambda *args: SimpleNamespace(duration_seconds=200_000))
    trip = _choice(_observe(engine, "long-trip-preview"), "步行", object_id="office")
    before = _world(engine)
    too_far = _act(engine, "long-trip", "start_activity", _request(trip))
    assert (too_far.status, too_far.error_code) == ("rejected", "ROUTE_DURATION_INVALID")
    assert too_far.data["reason"] == "这段路太长，一次走不完" and _world(engine) == before
    monkeypatch.undo()
    item = next(content for content in engine.scenario.contents if content.item_id == "jokes-feed")
    assert item.duration_seconds == 138264
    feed = _choice(_observe(engine, "long-preview"), "接着看", object_id="jokes-feed")
    result = _act(engine, "long-watch", "start_activity", _request(feed))
    assert result.status == "in_progress", (result.error_code, result.message)
    assert engine.state.activities[result.activity_id].planned_duration_seconds == 86_400
    shown = _observe(engine, "long-shown")["facts"]["progress"]["results"][-1]
    assert shown["details"]["planned"] == [{"name": engine.state.activities[result.activity_id].name,
                                             "duration": "约24小时"}]


def _hold(engine: LifeEngine, products: tuple[str, ...], agent_id: int = 1) -> None:
    """@brief 在人物所在地点各放一件所列持有物（经存储落盘，保持版本连续）。"""
    staged = engine.state.model_copy(deep=True)
    agent = staged.agents[agent_id]
    staged.agents[agent_id] = agent.model_copy(
        update={"inventory": {agent.location_id: {product: 1.0 for product in products}}}, deep=True)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        data={"kind": "test_parallel_admission_holdings"})])
    engine.state = staged


def _supply_choice(engine: LifeEngine, observed: dict, way: str, place: str) -> tuple[str, str, str]:
    """@brief 在某地出售、列出了该取得方式的一项供给（对象名，动词，方式）。"""
    return next((row["object"], action["verb"], action["label"]) for row in observed["interaction_bindings"]
                if row["kind"] == "supply" and engine.rules.supplies[row["object_id"]].location_id == place
                for action in row["actions"] if action["label"] == way)


def _action_evidence(engine: LifeEngine, action_id: str) -> set[tuple]:
    """@brief 一个动作的评估公开证据（类别、交互、状态、对象是否有身份），取自存储中该动作的全部事件。"""
    from worldmock.evaluation.public_evidence import worldmock_public_evidence

    rows = [event.model_dump(mode="json") for event in engine.store.read_events(engine.store.last_event_seq)
            if event.action_id == action_id]
    return {(item.category, item.interaction, item.state, item.object_key is not None)
            for item in worldmock_public_evidence(rows, [], {})}


def test_two_foods_are_one_meal_and_only_same_way_obtains_are_one_order(engine_factory) -> None:
    """同为食用的两件持有物合成一餐、同为远程下单的两项合成一单（第五章「请求形状与菜单」）：合成的两项占用相同，
    只与已有活动比较，组内不互相冲突；到店与远程下单各自成单，不合成一单，也不以 FULFILLMENT_NOT_SUPPORTED 拒绝。"""
    engine = _engine(engine_factory, location="market", market=True)
    foods = tuple(item.product_id for item in engine.scenario.products if "eat" in item.use_modes)[:2]
    _hold(engine, foods)
    observed = _observe(engine, "order-preview")
    rows = [(row["object"], row["object_id"]) for row in observed["interaction_bindings"] if row["kind"] == "supply"
            and engine.rules.supplies[row["object_id"]].location_id == "market"][:2]
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
    activity = engine.state.activities[eaten.activity_id]
    assert activity.kind == ActivityKind.CONSUME
    assert [item["product_id"] for item in activity.data["request"]["items"]] == list(foods)


def test_two_makes_are_not_merged_and_two_uses_are_refused_with_a_reason(engine_factory, monkeypatch) -> None:
    """「制作」按一份库存转换配方执行，两项不合成一次：各自占 hands，以 RESOURCE_CONFLICT 整组拒绝；两件「使用」合成
    一次使用但一次只能用一件，以带原因与下一步的 ACTION_REQUEST_INVALID 拒绝，世界不变（第五章「请求形状与菜单」）。"""
    engine = _engine(engine_factory, location="home")
    items = ("item-umbrella", "item-notebook")
    _hold(engine, items)
    observed = _observe(engine, "use-preview")
    used = _rejected(engine, "use-two", _request(*(_choice(observed, "使用", kind="holding", object_id=item)
                                                   for item in items)), "ACTION_REQUEST_INVALID")
    assert (used.data["reason"], used.data["next_step"]) == ("一次只能使用一件东西", "一次只选其中一件")
    monkeypatch.setattr("worldmock.life.observe.is_inventory_recipe",
                        lambda catalog, product: product.product_id in items)
    observed = _observe(engine, "make-preview")
    made = _rejected(engine, "make-two", _request(*(_choice(observed, "制作", kind="holding", object_id=item)
                                                    for item in items)), _CONFLICT)
    assert made.data["next_step"] == "一次只选其中一件"


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
    code, reason, step = (("PRODUCT_NOT_HELD", "出发后手边没有可用的持有物", "先用完再出发") if case == "eat"
                          else (_UNMET, f"进行{other[0].split('：', 1)[1]}要停在一个地方做，不能和出行一起开始",
                                "先做完再出发"))
    result = _rejected(engine, "stay", _request(*pair), code)
    assert (result.data["reason"], result.data["next_step"]) == (reason, step)


def test_pickup_and_remote_order_stay_listed_away_from_the_store(engine_factory) -> None:
    """到店与远程下单不按人物此刻在哪里剪裁（第五章「动作照常列出，做错了再说明」）：人不在店里时供给照样列出「到店」，说明需要在
    那家店，选了以 ACTIVITY_PRECONDITION_UNMET 返回原因与下一步「去<店>」；支持配送的照样列出「远程下单」；只能到店的
    供给同样列出「到店」。"""
    engine = _engine(engine_factory, location="home", market=True)
    observed = _observe(engine, "away-preview")
    market = next(item.name for item in engine.scenario.locations if item.location_id == "market")
    row = next(row for row in observed["interaction_bindings"] if row["kind"] == "supply"
               and engine.rules.supplies[row["object_id"]].location_id == "market")
    notes = {action["label"]: action["note"] for action in row["actions"]}
    assert notes == {"到店": f"到店{row['object']}需要在{market}", "远程下单": None}
    pickup_only = [row for row in observed["interaction_bindings"] if row["kind"] == "supply" and row["actions"]
                   and engine.rules.supplies[row["object_id"]].fulfillment_modes == ("pickup",)]
    assert pickup_only and all([action["label"] for action in item["actions"]] == ["到店"] for item in pickup_only)
    result = _rejected(engine, "away-pickup", _request((row["object"], "obtain", "到店")), _UNMET)
    assert (result.data["reason"], result.data["next_step"]) == (f"到店{row['object']}需要在{market}", f"去{market}")


def test_remote_order_where_it_cannot_be_delivered_is_noted_and_refused_naming_where_to_go(engine_factory) -> None:
    """远程下单的送达地取 agent.location_id：这里不在该供给的可配送目的地时，动作照常列出并带「暂时送不到这里」，
    选了以 ACTIVITY_PRECONDITION_UNMET 返回同一原因与能送达的地点；说明与拒绝出自同一判定（第八章「呈现」）。"""
    from worldmock.life import admission

    engine = _engine(engine_factory, location="home", market=True)
    observed = _observe(engine, "reach-preview")
    row = next(row for row in observed["interaction_bindings"] if row["kind"] == "supply"
               and any(action["label"] == "远程下单" for action in row["actions"]))
    action = next(action for action in row["actions"] if action["label"] == "远程下单")
    supply = engine.rules.supplies[row["object_id"]]
    places = {item.location_id: item.name for item in engine.scenario.locations}
    context_ = admission.Context(state=engine.state, agent_id=1, place_names=places)

    def item_for(destinations: tuple[str, ...]) -> admission.Item:
        """@brief 可配送目的地换成 destinations 的同一项远程下单。"""
        changed = supply.model_copy(update={"delivery_destination_ids": destinations})
        facts = admission.WorldFacts(tasks={}, supplies={row["object_id"]: changed}, items={}, contents={})
        return admission.selection_item(row, action, facts)

    refusal = admission.group_refusal(context_, [item_for(("office", "market"))])
    assert (refusal.code, refusal.reason, refusal.unmet) == (_UNMET, "暂时送不到这里", ("delivery_destination",))
    assert refusal.next_step == f"去{places['office']}或{places['market']}再下单"
    assert admission.action_note(context_, item_for(("office",)), {}) == "暂时送不到这里"
    assert admission.group_refusal(context_, [item_for(("home",))]) is None


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
    activity = engine.state.activities[started["activity_id"]]
    assert activity.name.startswith("步行前往" if companion == "步行" else "等待") and activity.session_ids
    [delivery] = engine.state.deliveries.values()
    assert delivery.destination_id == "home" and [item["order_id"] for item in result.data["orders"]] == [
        delivery.order_id]
    agent_account = f"account:{engine.state.agents[1].balance_account_id}"
    assert result.data["resource_changes"][agent_account] < 0
    orders = {("purchase", "order", "requested", True), ("purchase", "order", "pending", True),
              ("purchase", "pay", "paid", True)}
    assert {row for row in _action_evidence(engine, "action-mixed-1") if row[0] == "purchase"} == orders
    if companion == "步行":
        _live_until(engine, activity.planned_end_at)
        assert engine.state.activities[activity.activity_id].status == ActivityStatus.COMPLETED
        evidence = _action_evidence(engine, "action-mixed-1")
        assert {row for row in evidence if row[0] == "purchase"} == orders
        assert {row[1:] for row in evidence if row[0] != "purchase"} >= {
            ("travel", "requested", True), ("travel", "started", True), ("travel", "completed", True)}


def test_making_with_a_pickup_at_the_store_places_the_order_and_starts_the_making(engine_factory, monkeypatch) -> None:
    """在店里「制作」与「到店」同一请求（第五章组合表：到店取得要求人在店里）：到店当场成单、商品进入店里的持有物，
    制作照常开始，只有制作占名额。"""
    monkeypatch.setattr("worldmock.life.observe.is_inventory_recipe",
                        lambda catalog, product: product.product_id == "item-umbrella")
    engine = _engine(engine_factory, location="market", market=True)
    _hold(engine, ("item-umbrella",))
    observed = _observe(engine, "make-pickup-preview")
    make = _choice(observed, "制作", kind="holding", object_id="item-umbrella")
    result = _act(engine, "make-pickup", "start_activity",
                  _request(make, _supply_choice(engine, observed, "到店", "market")))
    assert result.status == "in_progress", (result.error_code, result.message)
    [started] = result.data["activities"]
    assert engine.state.activities[started["activity_id"]].kind == ActivityKind.CONSUME
    [order] = result.data["orders"]
    assert order["fulfillment"] == "pickup" and engine.state.agents[1].inventory["market"][order["product_id"]] == 1
    assert [item.kind for item in active_activities(engine.state, 1)] == [ActivityKind.CONSUME]


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
        "开车时不能下单", "先下单，再出发", ["hands_free"])


def test_game_sessions_to_watch_are_listed_to_resume_or_restart_with_their_length(engine_factory) -> None:
    """只能观看的游戏会话与内容对象同一呈现（第八章「呈现」）：方式为「接着看」或「从头再看」，说明写全长或剩余时长；
    剩余不足 60 秒的给全长并从头派生。"""
    from worldmock.life.activity_control import readable_duration
    from worldmock.life.models import ContentProgress

    engine = _engine(engine_factory, location="home")
    row = next(row for row in _observe(engine, "game-preview")["interaction_bindings"]
               if row["kind"] == "game" and row["actions"][0]["canonical_action"] == "watch")
    item = engine.rules.content(row["object_id"])

    def listed(position: int) -> list[tuple]:
        """@brief 观看进度为 position 秒时这一游戏会话列出的动作。"""
        staged = engine.state.model_copy(deep=True)
        staged.agents[1].content_progress[item.item_id] = ContentProgress(
            item_id=item.item_id, position_seconds=position, exposure_count=1)
        staged.version += 1
        engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
            event_type="environment_event", occurred_at=staged.current_time, visibility="system",
            data={"kind": "test_game_session_progress"})])
        engine.state = staged
        found = next(found for found in _observe(engine, f"game-{position}")["interaction_bindings"]
                     if found["object_id"] == item.item_id)
        return [(action["verb"], action["label"], action["note"]) for action in found["actions"]]

    assert [(action["verb"], action["label"], action["note"]) for action in row["actions"]] == [
        ("interact", "接着看", f"全长{readable_duration(item.duration_seconds)}")]
    half = item.duration_seconds // 2
    assert listed(half) == [("interact", "接着看", f"还剩{readable_duration(item.duration_seconds - half)}")]
    assert listed(item.duration_seconds - 30) == [
        ("interact", "从头再看", f"全长{readable_duration(item.duration_seconds)}")]
    again = _act(engine, "game-restart", "start_activity", _request((row["object"], "interact", "从头再看")))
    assert again.status == "in_progress", (again.error_code, again.message)
    assert engine.state.activities[again.activity_id].planned_duration_seconds == item.duration_seconds


def test_malformed_request_gets_the_default_reason_without_the_validation_text(engine_factory) -> None:
    """绕过构造期校验的请求以 ACTION_REQUEST_INVALID 拒绝，公开原因取该码的默认说法，不把请求模型的校验转储（其中含
    所填的中文）当作原因；原因只取引导性拒绝写明的那一句（第五章「动作照常列出，做错了再说明」）。"""
    engine = _engine(engine_factory)
    malformed = StartActivityRequest.model_construct(targets="自己", actions=["interact"], ways=["等待"])
    result = _act(engine, "malformed", "start_activity", malformed)
    assert (result.status, result.error_code) == ("rejected", "ACTION_REQUEST_INVALID")
    assert "validation" in result.message and "自己" in result.message
    assert result.data["reason"] == "所选的对象或方式不在这一轮列出的范围内"
    assert result.data["next_step"] == "从本轮列出的对象和方式里选"
    shown = _observe(engine, "malformed-shown")["facts"]["progress"]["results"][-1]["details"]
    assert (shown["reason"], shown["next_step"]) == (result.data["reason"], result.data["next_step"])


def test_a_repeated_observation_of_one_decision_is_ambiguous_on_every_read(engine_factory) -> None:
    """名称与报价只从本决策唯一的已提交观察解析，每次都重读存储：同一决策再提交一次观察后，此前读到过的观察也不再
    唯一，以 OBSERVATION_NOT_FOUND 拒绝，不沿用任何缓存。"""
    from types import SimpleNamespace

    from worldmock.errors import BusinessRuleError

    engine = _engine(engine_factory)
    decision = context(engine, "twice")
    engine.observe(decision)
    pending = SimpleNamespace(agent_id=1, decision_id=decision.decision_id, observed_at=engine.state.current_time)
    assert engine._frozen_observation(pending)["interaction_bindings"]
    engine.observe(decision)
    with pytest.raises(BusinessRuleError, match="OBSERVATION_NOT_FOUND"):
        engine._frozen_observation(pending)

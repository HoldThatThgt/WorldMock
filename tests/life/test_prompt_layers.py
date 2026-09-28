"""环境构建的提示层：Schema 校验、枚举闭集与唯一出现原则（docs/dev/SPEC-persona-prompt.md 第一章）。

工具层、待办层、环境层、进度层与记忆层在真实引擎的各种状态下逐一通过 prompt_schemas 校验；Schema 中的
体感分级、方式、动词、动作状态、意图与目标类别是源头闭集的字面副本，由本文件锁定一致。唯一出现性质（「唯一出现原则」）：
直接消息全文只在进度层；暂停活动的进度只在待办层，它不是工具层目标；工作合同的截止与剩余只在任务目标的 contract，
工作活动的状态不写「还差」，暂停的工作在待办层也不重复合同；浏览得到的供给只在工具层，浏览结果只列名称。系统层、人设层、user 消息与原生 tools 由宿主侧
tests/integration/test_seven_layer_request.py 覆盖。
"""

from __future__ import annotations

import copy
import json

import pytest

from tests.life.conftest import (activity_name, advance_next, conclude_request, context, progress_calls,
                                 stage_day_plan, start_activity, submit_round, tool_targets, wake_by_mail)
from tests.life.test_commerce import _choice, _stage_open
from tests.life.test_dayrun_restaurants import _stage
from tests.life.test_same_moment_decisions import _act, _finish, _observed_act
from tests.life.test_work_review import _at_task_start
from worldmock.life import activity_control, felt, schedule
from worldmock.life.interaction_menu import LIFE_VERBS, WAYS
from worldmock.life.models import (ActionStatus, PauseActivityRequest, PlanGoalInput, ReportStatusRequest,
                                   ReviseDayPlanRequest, SendMessageActionRequest)
from worldmock.life.prompt_schemas import (ACTION_STATUSES, BODY_LEVELS, INTENT_KINDS, LAYER_SCHEMAS, TARGET_KINDS,
                                           USER_LAYER_NAMES, VERBS, WAY_LABELS, validate_layer)
from worldmock.life.tool_layer import BINDING_KINDS

#: 环境构建的五层（人设层由宿主构建）。
ENV_LAYERS = {"tool_layer": "工具层", "todo_layer": "待办层", "environment_layer": "环境层",
              "memory_layer": "记忆层", "progress_layer": "进度层"}


def _checked(engine, round_id: str, agent_id: int = 1) -> dict:
    """读取一次观察并逐层校验环境构建的五层（层名与 Schema）。"""
    observed = engine.observe(context(engine, round_id, agent_id))
    for key, name in ENV_LAYERS.items():
        assert observed[key]["layer"] == name
        validate_layer(observed[key])
    return observed


def _rendered(observed: dict) -> str:
    """环境构建的五层按请求中的写法串起来（ensure_ascii=False），用于计数信息出现的次数。"""
    return json.dumps([observed[key] for key in ENV_LAYERS], ensure_ascii=False)


def test_schema_enums_are_literal_copies_of_the_source_closed_sets() -> None:
    """体感分级、方式、动词、动作状态、意图与目标类别的 Schema 枚举与源头闭集一致；七个层名各有一份 Schema。"""
    def levels(base: str, bands: tuple, *extra: str) -> tuple[str, ...]:
        """基档、各档文本与表外的档。"""
        return (base, *(text for _, text in bands), *extra)

    assert BODY_LEVELS == {
        "appetite": levels(felt.APETITE_BASE_LEVEL, felt.APPETITE_BANDS, felt.APPETITE_STARVING_LEVEL),
        "sleepiness": levels(felt.SLEEPINESS_BASE_LEVEL, felt.SLEEPINESS_BANDS),
        "entertainment": levels(felt.ENTERTAINMENT_BASE_LEVEL, felt.ENTERTAINMENT_BANDS),
        "social": levels(felt.SOCIAL_BASE_LEVEL, felt.SOCIAL_BANDS),
        "mood": levels(felt.MOOD_BASE_LEVEL, felt.MOOD_BANDS),
    }
    assert WAY_LABELS == tuple(WAYS) and VERBS == LIFE_VERBS
    assert ACTION_STATUSES == tuple(item.value for item in ActionStatus)
    assert INTENT_KINDS == schedule.INTENT_KINDS
    assert set(TARGET_KINDS) == set(BINDING_KINDS.values()) | {"activity"}
    assert set(LAYER_SCHEMAS) == {"系统层", *USER_LAYER_NAMES} and len(USER_LAYER_NAMES) == 6


def test_memory_layer_and_persona_views_are_empty_at_run_start(engine_factory) -> None:
    """运行开始时还没有结束的 session 与写下的看法：记忆层两个列表都为空并通过 Schema，看法与偏好都为空；运行开始的五层
    都通过 Schema（派生内容见 test_memory_layer.py 与 test_persona_layer.py）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    observed = _checked(engine, "memory")
    assert observed["memory_layer"] == {"layer": "记忆层", "earlier_days": [], "earlier_today": []}
    assert observed["persona_views"] == {"relationships": [], "preferences": []}


def test_env_layers_validate_through_purchase_rejection_pause_travel_and_arrival(engine_factory) -> None:
    """一段连续的真实状态中每次观察的五层都通过 Schema：在店里（商品目标与送达说明）、远程下单后（购买结果）、业务拒绝后
    （原因与下一步）、暂停等待后（待办层）、邮件到达、路上（路程与进行中的出行）、到达待收尾（刚结束的活动与到达结果）与收尾后。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _stage(engine, "market", 700)
    submit_round(engine, {1: ("revise_day_plan", ReviseDayPlanRequest(goals=(
        PlanGoalInput(intent_kind="用餐", goal="中午好好吃一顿", remind_time="12:00"),)))}, "layers-plan")
    shop = _checked(engine, "layers-shop")
    target, way = next((row["object"], action["label"]) for row in shop["interaction_bindings"]
                       if row["kind"] == "supply" for action in row["actions"]
                       if action["label"] == "远程下单" and action["note"] is None)
    assert _act(engine, "layers-buy", "start_activity", start_activity(way, target)).status == "completed"
    assert "purchases" in progress_calls(_checked(engine, "layers-bought"))[-1]["result"]["details"]
    assert _act(engine, "layers-reject", "start_activity", start_activity("观看", "不存在的对象")).status == "rejected"
    assert "next_step" in progress_calls(_checked(engine, "layers-rejected"))[-1]["result"]["details"]
    waited = _act(engine, "layers-wait", "start_activity", start_activity("等待", "自己"))
    wake_by_mail(engine, offset=60)
    advance_next(engine)
    _act(engine, "layers-pause", "pause_activity", PauseActivityRequest(
        task=[activity_name(engine, waited.activity_id)], summary=["先出门。"]))
    paused = _checked(engine, "layers-paused")
    assert paused["todo_layer"]["paused_activities"] and paused["todo_layer"]["day_plan"]["goals"]
    home = next(row["object"] for row in paused["interaction_bindings"] if row["object_id"] == "home")
    walking = _act(engine, "layers-walk", "start_activity", start_activity("步行", home))
    wake_by_mail(engine, offset=60)
    advance_next(engine)
    road = _checked(engine, "layers-road")
    assert road["environment_layer"]["location"]["session_kind"] == "transit"
    _finish(engine, walking.activity_id, "layers-walk")
    arrived = _checked(engine, "layers-arrived")
    assert tool_targets(arrived)[activity_name(engine, walking.activity_id)]["phase"] == "刚结束"
    _act(engine, "layers-conclude", "conclude_activity", conclude_request(activity_name(engine, walking.activity_id)))
    _checked(engine, "layers-concluded")


def test_schema_validation_rejects_unknown_layers_extra_keys_and_unexplained_refusals(engine_factory) -> None:
    """逐层校验不是空转：未知层名、真实观察的一层多出一个键、不可用的动作没有写原因时，都以 ValueError 指明层名与位置。"""
    engine = engine_factory()
    stage_day_plan(engine)
    observed = _checked(engine, "schema-negative")
    with pytest.raises(ValueError, match="Unknown prompt layer: '场景层'"):
        validate_layer({**observed["environment_layer"], "layer": "场景层"})
    with pytest.raises(ValueError, match=r"Prompt layer 待办层 violates its schema at \[\]: .*'scene'"):
        validate_layer({**observed["todo_layer"], "scene": "你在家里。"})
    tools = copy.deepcopy(observed["tool_layer"])
    index = next(position for position, target in enumerate(tools["targets"]) if target["actions"])
    tools["targets"][index]["actions"][0].update(available=False, reason=None)
    with pytest.raises(ValueError, match=rf"Prompt layer 工具层 violates its schema at "
                                         rf"\['targets', {index}, 'actions', 0, 'reason'\]"):
        validate_layer(tools)


def test_direct_message_and_invite_text_appear_only_in_the_progress_layer(engine_factory) -> None:
    """同伴发来的直接消息与交谈邀请全文在五层中各恰好出现一次，就在进度层的 message_arrival；工具层没有这两种消息的
    目标（只有邮件是阅读对象）；各层通过 Schema。"""
    engine = engine_factory("multi-person.json")
    for agent_id in engine.state.agents:
        stage_day_plan(engine, agent_id)
    me, friend = engine.state.agents[1].name, engine.state.agents[2].name
    text = "今天下午三点在楼下的咖啡馆见，带上上周那份报价单，顺便把你改过的两页也打印出来。"
    message = SendMessageActionRequest(recipient_name=f"人物：{me}", text=text)
    _, results = submit_round(engine, {1: ("start_activity", start_activity("等待", "自己")),
                                       2: ("send_message", message)}, "unique-message")
    waiting = next(item.activity_id for item in results if item.agent_id == 1)
    report = ReportStatusRequest(task=[activity_name(engine, waiting)], summary=["看到消息了。"])
    submit_round(engine, {1: ("report_status", report), 2: ("start_activity", start_activity("交谈", f"人物：{me}"))},
                 "unique-invite")
    observed = _checked(engine, "unique-seen")
    arrivals = [row for row in observed["progress_layer"]["events"] if row["type"] == "message_arrival"]
    assert [(row["sender"], row["kind"]) for row in arrivals] == [(friend, "direct"), (friend, "chat_invite")]
    assert arrivals[0]["text"] == text
    others = json.dumps([observed[key] for key in ENV_LAYERS if key != "progress_layer"], ensure_ascii=False)
    for row in arrivals:
        assert _rendered(observed).count(row["text"]) == 1 and row["text"] not in others
    assert all(row["kind"] != "message" or row["name"].startswith("邮件：")
               for row in observed["tool_layer"]["targets"])


def test_paused_activity_progress_appears_only_in_the_todo_layer(engine_factory) -> None:
    """暂停活动的客观进度在五层中只出现在待办层；它不是工具层目标，进度层只有暂停调用的参数（按名称引用）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    waited = _act(engine, "paused-wait", "start_activity", start_activity("等待", "自己"))
    wake_by_mail(engine, offset=120)
    advance_next(engine)
    name = activity_name(engine, waited.activity_id)
    _act(engine, "paused-pause", "pause_activity", PauseActivityRequest(task=[name], summary=["先停一下。"]))
    observed = _checked(engine, "paused-seen")
    (item,) = observed["todo_layer"]["paused_activities"]
    assert item["name"] == name and name not in tool_targets(observed)
    assert _rendered(observed).count(item["progress"]) == 1
    assert item["progress"] in json.dumps(observed["todo_layer"], ensure_ascii=False)
    (pause,) = progress_calls(observed, "pause_activity")
    assert pause["arguments"]["task"] == [name] and item["progress"] not in json.dumps(pause, ensure_ascii=False)


def test_work_contract_appears_only_in_the_task_target(engine_factory) -> None:
    """正在工作时：截止时刻与有效工时的剩余要求只在任务目标的 contract 出现一次；工作活动目标的状态不写「还差」，
    待办层与进度层不重复合同。"""
    engine = engine_factory()
    _at_task_start(engine)
    stage_day_plan(engine)
    task = f"工作：{engine.scenario.tasks[0].title}"
    submit_round(engine, {1: ("start_activity", start_activity("工作", task))}, "contract-work")
    wake_by_mail(engine, offset=600)
    advance_next(engine)
    observed = _checked(engine, "contract-seen")
    contract = tool_targets(observed)[task]["contract"]
    rendered = _rendered(observed)
    assert rendered.count(contract["deadline"]) == 1 and rendered.count(contract["remaining"]) == 1
    (working,) = [row for row in observed["tool_layer"]["targets"] if row["kind"] == "activity"]
    assert "还差" not in working["state"] and "有效" in working["state"]
    for key in ("todo_layer", "progress_layer", "environment_layer"):
        text = json.dumps(observed[key], ensure_ascii=False)
        assert contract["deadline"] not in text and contract["remaining"] not in text


def test_paused_work_does_not_repeat_the_contract_in_the_todo_layer(engine_factory) -> None:
    """工作暂停后：暂停的工作在待办层只有名称、暂停时刻、地点、客观进度与两个工具（通过 Schema），不写截止与工时要求；
    截止时刻与有效工时的剩余要求仍只在任务目标的 contract 出现一次。"""
    engine = engine_factory()
    _at_task_start(engine)
    stage_day_plan(engine)
    task = f"工作：{engine.scenario.tasks[0].title}"
    _, results = submit_round(engine, {1: ("start_activity", start_activity("工作", task))}, "paused-contract-work")
    wake_by_mail(engine, offset=600)
    advance_next(engine)
    name = activity_name(engine, results[0].activity_id)
    _act(engine, "paused-contract-pause", "pause_activity", PauseActivityRequest(task=[name], summary=["先停一下。"]))
    observed = _checked(engine, "paused-contract-seen")
    (item,) = observed["todo_layer"]["paused_activities"]
    assert item["name"] == name and name not in tool_targets(observed)
    contract = tool_targets(observed)[task]["contract"]
    rendered = _rendered(observed)
    assert rendered.count(contract["deadline"]) == 1 and rendered.count(contract["remaining"]) == 1
    todo = json.dumps(observed["todo_layer"], ensure_ascii=False)
    assert not any(text in todo for text in (contract["deadline"], contract["remaining"], "截止", "工时"))


def test_browse_supplies_live_in_the_tool_layer_and_the_result_lists_names_only(engine_factory) -> None:
    """浏览完成后：结果的 browse_items 只是商品名称（没有价格、库存或供给身份）；浏览得到的供给作为工具层商品目标列出，
    价格与方式只在那里。"""
    engine = engine_factory()
    _stage_open(engine)
    stage_day_plan(engine)
    _, browse = _observed_act(engine, "browse-start", "start_activity",
                              _choice(engine.observe(context(engine, "browse-preview")), "browse"))
    assert browse.status == "in_progress"
    _finish(engine, browse.activity_id, "browse")
    revealed = [row["supply_id"] for row in engine.state.activities[browse.activity_id].data["revealed"]["items"]]
    observed = _checked(engine, "browse-seen")
    (record,) = [row for row in progress_calls(observed, "start_activity")
                 if "browse_items" in row["result"]["details"]]
    items = record["result"]["details"]["browse_items"]
    assert items and all(isinstance(name, str) for name in items)
    assert not {"price", "supply_id", "stock", "price_fen"} & set(json.dumps(record, ensure_ascii=False).split('"'))
    assert not any(identity in _rendered(observed) for identity in revealed)
    shown = [row["object"] for row in observed["interaction_bindings"] if row["object_id"] in revealed]
    targets = tool_targets(observed)
    assert shown and all(targets[name]["kind"] == "product" for name in shown)
    assert all(action["price"] is not None for name in shown for action in targets[name]["actions"]
               if action["action"] == "obtain")


def test_just_ended_browse_target_states_the_end_reason_without_the_browse_result(engine_factory) -> None:
    """浏览结束后：工具层刚结束的浏览活动目标只写终局说法与客观进度，不复述进度层该调用结果里的商品名称（结果取该请求的
    最新结果，浏览得到的名称只在那里，docs/dev/SPEC-persona-prompt.md「唯一出现原则」）。"""
    engine = engine_factory()
    _stage_open(engine)
    stage_day_plan(engine)
    _, browse = _observed_act(engine, "ended-browse-start", "start_activity",
                              _choice(engine.observe(context(engine, "ended-browse-preview")), "browse"))
    _finish(engine, browse.activity_id, "ended-browse")
    observed = _checked(engine, "ended-browse-seen")
    (record,) = [row for row in progress_calls(observed, "start_activity")
                 if "browse_items" in row["result"]["details"]]
    activity = engine.state.activities[browse.activity_id]
    target = tool_targets(observed)[activity_name(engine, browse.activity_id)]
    assert target["phase"] == "刚结束"
    assert target["state"] == (f"{activity_control.ending_phrase(activity)}，"
                               f"{activity_control.objective_progress(activity)}")
    assert record["result"]["details"]["browse_items"]
    assert not any(name in target["state"] for name in record["result"]["details"]["browse_items"])

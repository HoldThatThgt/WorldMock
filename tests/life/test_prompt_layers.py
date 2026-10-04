"""环境构建的提示层：Schema 校验、枚举闭集与唯一出现原则（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第一章）。

工具层、待办层、环境层、进度层与记忆层在真实引擎的各种状态下逐一通过 prompt_schemas 校验；Schema 中的
体感分级、方式、动词、动作状态、意图与目标类别是源头闭集的字面副本，由本文件锁定一致。唯一出现性质（「唯一出现原则」）：
直接消息全文只在进度层；暂停活动的进度只在待办层，它不是工具层目标；工作合同的截止与剩余只在任务目标的 contract，
工作活动的状态不写「还差」，暂停的工作在待办层也不重复合同；浏览得到的供给只在工具层，浏览结果只列名称。工具层的可调用索引
callable_index 只有名称、品类计数与至多 3 个示例名（docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-只列名称的可调用索引 ）。系统层、人设层、user 消息与原生 tools 由宿主侧
tests/integration/test_seven_layer_request.py 覆盖。
"""

from __future__ import annotations

import copy
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tests.life.conftest import (activity_name, advance_next, conclude_request, context, progress_calls, stage_day_plan,
                                 start_activity, start_request, submit_round, tool_targets, wake_by_mail, pause_request,
                                 report_request)
from tests.life.test_commerce import _choice, _stage_open
from tests.life.test_dayrun_restaurants import _stage
from tests.life.test_presentation_fidelity import _stage as _stage_moment
from tests.life.test_same_moment_decisions import _act, _finish, _observed_act
from tests.life.test_work_review import _at_task_start
from worldmock.footprints.models import EventDraft
from worldmock.life import activity_control, felt, schedule
from worldmock.life.callable_index import INDEX_KEYS, MAX_EXAMPLES
from worldmock.life.vocabulary import LIFE_VERBS, SYSTEM_PURCHASE_WAY, WAYS
from worldmock.life.models import ActionStatus, PlanGoalInput, ReviseDayPlanRequest, SendMessageActionRequest
from worldmock.life.prompt_schemas import (ACTION_STATUSES, BODY_LEVELS, INDEX_EXAMPLES_MAX, INTENT_KINDS,
                                           LAYER_SCHEMAS, TARGET_KINDS, TOOL_LAYER_SCHEMA, USER_LAYER_NAMES, VERBS,
                                           WAY_LABELS, validate_layer)
from worldmock.life.readable import count_phrase
from worldmock.life.tool_layer import BINDING_KINDS
from worldmock.life import work_schedule
from worldmock.life.prompt_schemas import DUTY_DAYS, DUTY_STATUSES, ENVIRONMENT_LAYER_SCHEMA

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
    """体感分级、方式、动词、动作状态、意图与目标类别的 Schema 枚举与源头闭集一致（工具层的方式不含只写在请求里、由系统
    定方式的「购买」，工具层从不列出它，docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-更简单的购买请求裁决-s10 「收敛」第 2 条）；七个层名各有一份 Schema。"""
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
    assert WAY_LABELS == tuple(way for way in WAYS if way != SYSTEM_PURCHASE_WAY) and VERBS == LIFE_VERBS
    assert ACTION_STATUSES == tuple(item.value for item in ActionStatus)
    assert INTENT_KINDS == schedule.INTENT_KINDS
    assert set(TARGET_KINDS) == set(BINDING_KINDS.values()) | {"activity"}
    assert set(LAYER_SCHEMAS) == {"系统层", *USER_LAYER_NAMES} and len(USER_LAYER_NAMES) == 6
    index = TOOL_LAYER_SCHEMA["properties"]["callable_index"]
    assert INDEX_EXAMPLES_MAX == MAX_EXAMPLES
    assert tuple(index["properties"]) == INDEX_KEYS == tuple(index["required"])


def test_duty_enumerations_are_the_work_schedule_constants() -> None:
    """环境层 duty 的两组枚举是 work_schedule 闭集的字面副本（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-环境层-duty ）：日子为工作日、
    休息日，状态为「未到上班时间」「当班中」「休息时段」「已下班」，Schema 里的枚举与之相同。duty 必填可空；休息日的
    hours、place、status 只能为 null、breaks 为空，工作日的 hours、place 是文本、status 是四种之一，不接受其他键。"""
    assert DUTY_DAYS == work_schedule.DUTY_DAYS and DUTY_STATUSES == work_schedule.DUTY_STATUSES
    schema = ENVIRONMENT_LAYER_SCHEMA["properties"]["duty"]["oneOf"][1]
    assert schema["properties"]["day"]["enum"] == list(work_schedule.DUTY_DAYS)
    assert schema["properties"]["status"]["enum"] == [*work_schedule.DUTY_STATUSES, None]
    assert schema["else"]["properties"]["status"]["enum"] == list(work_schedule.DUTY_STATUSES)
    layer = {"layer": "环境层", "observed_at": "2026-01-05T10:00:00+08:00",
             "body": {key: levels[0] for key, levels in BODY_LEVELS.items()},
             "location": {"session_kind": "stay", "name": "办公室", "place_type": "办公室", "fixed_rules": []}}
    rest = {"day": "休息日", "hours": None, "place": None, "breaks": [], "status": None}
    work = {"day": "工作日", "hours": "09:00–18:00", "place": "办公室", "breaks": ["12:00–13:30"], "status": "当班中"}
    for duty in (None, rest, *({**work, "status": status} for status in work_schedule.DUTY_STATUSES)):
        validate_layer({**layer, "duty": duty})
    for duty in ({**rest, "hours": "09:00–18:00"}, {**rest, "breaks": ["12:00–13:30"]}, {**rest, "status": "当班中"},
                 {**work, "status": None}, {**work, "place": None}, {**work, "status": "加班中"},
                 {**work, "location_id": "office"}):
        with pytest.raises(ValueError, match="环境层"):
            validate_layer({**layer, "duty": duty})
    with pytest.raises(ValueError, match="duty"):
        validate_layer(layer)


#: 环境层 duty 的两个合法取值：休息日，与当班中的工作日。
_REST_DUTY = {"day": "休息日", "hours": None, "place": None, "breaks": [], "status": None}
_WORK_DUTY = {"day": "工作日", "hours": "09:00–18:00", "place": "办公室", "breaks": ["12:00–13:30"], "status": "当班中"}


@pytest.mark.parametrize(("duty", "change"), [
    (_WORK_DUTY, {"hours": None}),
    (_REST_DUTY, {"place": "办公室"}),
    (_WORK_DUTY, {"breaks": [[720, 810]]}),
    (_WORK_DUTY, {"breaks": [""]}),
], ids=["working-day-hours-null", "rest-day-place-text", "breaks-as-minute-pairs", "break-empty-text"])
def test_duty_schema_pins_working_day_hours_rest_day_place_and_text_breaks(duty: dict, change: dict) -> None:
    """环境层 duty 的取值表（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-环境层-duty ；接口合同「七层字段」）逐键钉在 Schema 上：工作日的
    hours 是文本、不能为 null；休息日的 place 只能为 null；breaks 的每一项是非空文本，不接受原始的分钟对或空串。两个
    合法取值各自通过，只改这一个键就在 duty 处不合 Schema。"""
    layer = {"layer": "环境层", "observed_at": "2026-01-05T10:00:00+08:00",
             "body": {key: levels[0] for key, levels in BODY_LEVELS.items()},
             "location": {"session_kind": "stay", "name": "办公室", "place_type": "办公室", "fixed_rules": []}}
    validate_layer({**layer, "duty": duty})
    with pytest.raises(ValueError, match=r"Prompt layer 环境层 violates its schema at \['duty'"):
        validate_layer({**layer, "duty": {**duty, **change}})


def test_progress_schema_registers_the_eight_record_types_with_both_errand_records() -> None:
    """进度层 Schema 的 events[].type 恰为接口合同「七层 Schema」登记的八种：tool_call、message_arrival、delivery、
    task_deadline、plan_reminder、errand_reminder、errand_completed、condensed（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-待办层与进度层呈现 、6.4）。
    只写事务名的 errand_reminder 与 errand_completed 记录各自通过校验；六种事务事件里其余四种的名字不是记录类型，写成
    记录即不合 Schema。"""
    records = LAYER_SCHEMAS["进度层"]["properties"]["events"]["items"]["oneOf"]
    assert sorted(item["properties"]["type"]["const"] for item in records) == sorted((
        "tool_call", "message_arrival", "delivery", "task_deadline", "plan_reminder", "errand_reminder",
        "errand_completed", "condensed"))
    layer = {"layer": "进度层", "session_started_at": "2026-01-05T10:00:00+08:00", "rejected_today": []}
    record = {"occurred_at": "2026-01-05T12:40:00+08:00", "errand": "买牛奶"}
    for kind in ("errand_reminder", "errand_completed"):
        validate_layer({**layer, "events": [{**record, "type": kind}]})
    for kind in ("errand_registered", "errand_updated", "errand_cancelled", "errand_progressed"):
        with pytest.raises(ValueError, match=r"Prompt layer 进度层 violates its schema at \['events', 0\]"):
            validate_layer({**layer, "events": [{**record, "type": kind}]})


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
    submit_round(engine, {1: ("revise_day_plan", ReviseDayPlanRequest(goals=[
        PlanGoalInput(intent_kind="用餐", goal="中午好好吃一顿", remind_time="12:00",
                      around_time="12:30")]))}, "layers-plan")
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
    _act(engine, "layers-pause", "pause_activity", pause_request(
        activity_name(engine, waited.activity_id), summary="先出门。"))
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


#: 可调用索引的反例：（改法，Schema 报出的位置）。
_BROKEN_INDEXES = (
    (lambda index: index.clear(), r"\['callable_index'\]: 'lifestyle' is a required property"),
    (lambda index: index["lifestyle"].append({"name": "洗碗", "actions": []}), r"\['callable_index', 'lifestyle', \d+\]"),
    (lambda index: index["lifestyle"].append(index["lifestyle"][0]), r"\['callable_index', 'lifestyle'\]"),
    (lambda index: index["goods"][0]["examples"].extend(["甲", "乙", "丙"]), r"\['callable_index', 'goods', 0, 'examples'\]"),
    (lambda index: index["contents"][0].update(available=True), r"\['callable_index', 'contents', 0\]"),
    (lambda index: index["goods"][0].update(count=22), r"\['callable_index', 'goods', 0, 'count'\]"),
    (lambda index: index["goods"][0].update(count="0 个"), r"\['callable_index', 'goods', 0, 'count'\]"),
    (lambda index: index.update(targets=[]), r"\['callable_index'\]"),
)


def test_callable_index_schema_takes_names_counts_and_up_to_three_examples(engine_factory) -> None:
    """工具层的可调用索引（docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-只列名称的可调用索引 ）按工具层 Schema 校验：12:30 在家的真实索引通过，lifestyle 是不重复的
    名称，contents 与 goods 每项只有 category、人读计数 count（「N 个」）与至多 3 个不重复的示例名；工具层缺少索引、名称带上
    动作、名称重复、多给示例、品类行多出可用性、计数写成裸整数或零个、索引多出键都以 ValueError 指明位置。"""
    engine = engine_factory()
    stage_day_plan(engine)
    moment = datetime(2026, 1, 5, 12, 30, tzinfo=ZoneInfo(engine.context.scenario.timezone))
    _stage_moment(engine, int(moment.timestamp()), "home")
    observed = _checked(engine, "index-schema")
    index = observed["tool_layer"]["callable_index"]
    assert list(index) == list(INDEX_KEYS) and all(index[key] for key in INDEX_KEYS)
    assert index["query_categories"] and all(isinstance(row["count"], int) for row in index["query_categories"])
    assert all(set(row) == {"category", "count", "examples"} for key in ("contents", "goods") for row in index[key])
    assert all(row["count"] == count_phrase(int(row["count"].split()[0])) for row in index["goods"])
    missing = copy.deepcopy(observed["tool_layer"])
    del missing["callable_index"]
    with pytest.raises(ValueError, match=r"violates its schema at \[\]: 'callable_index' is a required property"):
        validate_layer(missing)
    for breaking, where in _BROKEN_INDEXES:
        layer = copy.deepcopy(observed["tool_layer"])
        breaking(layer["callable_index"])
        with pytest.raises(ValueError, match=rf"Prompt layer 工具层 violates its schema at {where}"):
            validate_layer(layer)


def test_direct_message_and_invite_text_appear_only_in_the_progress_layer(engine_factory) -> None:
    """同伴发来的直接消息与交谈邀请全文在五层中各恰好出现一次，就在进度层的 message_arrival；工具层没有这两种消息的
    目标（只有邮件是阅读对象）；各层通过 Schema。"""
    engine = engine_factory("multi-person.json")
    for agent_id in engine.context.state.agents:
        stage_day_plan(engine, agent_id)
    me, friend = engine.context.state.agents[1].name, engine.context.state.agents[2].name
    text = "今天下午三点在楼下的咖啡馆见，带上上周那份报价单，顺便把你改过的两页也打印出来。"
    message = SendMessageActionRequest(recipient_name=f"人物：{me}", text=text)
    _, results = submit_round(engine, {1: ("start_activity", start_activity("等待", "自己")),
                                       2: ("send_message", message)}, "unique-message")
    waiting = next(item.activity_id for item in results if item.agent_id == 1)
    report = report_request(activity_name(engine, waiting), summary="看到消息了。")
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
    _act(engine, "paused-pause", "pause_activity", pause_request(name, summary="先停一下。"))
    observed = _checked(engine, "paused-seen")
    (item,) = observed["todo_layer"]["paused_activities"]
    assert item["name"] == name and name not in tool_targets(observed)
    assert _rendered(observed).count(item["progress"]) == 1
    assert item["progress"] in json.dumps(observed["todo_layer"], ensure_ascii=False)
    (pause,) = progress_calls(observed, "pause_activity")
    assert pause["arguments"] == {"items": [{"activity_name": name, "summary": "先停一下。"}]}
    assert item["progress"] not in json.dumps(pause, ensure_ascii=False)


def test_work_contract_appears_only_in_the_task_target(engine_factory) -> None:
    """正在工作时：截止时刻与有效工时的剩余要求只在任务目标的 contract 出现一次；工作活动目标的状态不写「还差」，
    待办层与进度层不重复合同。"""
    engine = engine_factory()
    _at_task_start(engine)
    stage_day_plan(engine)
    task = f"工作：{engine.context.scenario.tasks[0].title}"
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
    task = f"工作：{engine.context.scenario.tasks[0].title}"
    _, results = submit_round(engine, {1: ("start_activity", start_activity("工作", task))}, "paused-contract-work")
    wake_by_mail(engine, offset=600)
    advance_next(engine)
    name = activity_name(engine, results[0].activity_id)
    _act(engine, "paused-contract-pause", "pause_activity", pause_request(name, summary="先停一下。"))
    observed = _checked(engine, "paused-contract-seen")
    (item,) = observed["todo_layer"]["paused_activities"]
    assert item["name"] == name and name not in tool_targets(observed)
    contract = tool_targets(observed)[task]["contract"]
    rendered = _rendered(observed)
    assert rendered.count(contract["deadline"]) == 1 and rendered.count(contract["remaining"]) == 1
    todo = json.dumps(observed["todo_layer"], ensure_ascii=False)
    assert not any(text in todo for text in (contract["deadline"], contract["remaining"], "截止", "工时"))


def test_browse_supplies_live_in_the_tool_layer_and_the_result_lists_names_only(engine_factory) -> None:
    """浏览完成后：结果的 browse_items 只是名称，写完整标签「<门店>出售的<商品>」（没有价格、库存、可得性或供给身份，
    docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-浏览结果与提示看可达性 ）；浏览得到的供给作为工具层商品目标列出，名称与标签相同，价格与方式只在那里。"""
    engine = engine_factory()
    _stage_open(engine)
    stage_day_plan(engine)
    _, browse = _observed_act(engine, "browse-start", "start_activity",
                              _choice(engine.observe(context(engine, "browse-preview")), "browse"))
    assert browse.status == "in_progress"
    _finish(engine, browse.activity_id, "browse")
    rows = engine.context.state.activities[browse.activity_id].data["revealed"]["items"]
    revealed = [row["supply_id"] for row in rows]
    observed = _checked(engine, "browse-seen")
    (record,) = [row for row in progress_calls(observed, "start_activity")
                 if "browse_items" in row["result"]["details"]]
    items = record["result"]["details"]["browse_items"]
    places = {item.location_id: item.name for item in engine.context.scenario.locations}
    names = {item.product_id: item.name for item in engine.context.scenario.products}
    assert items and items == [f"{places[row['location_id']]}出售的{names[row['product_id']]}" for row in rows]
    listed = {row["object_id"]: row["object"] for row in observed["interaction_bindings"] if row["kind"] == "supply"}
    assert [listed[row["supply_id"]] for row in rows if row["supply_id"] in listed] == [
        label for label, row in zip(items, rows) if row["supply_id"] in listed]
    assert not {"price", "supply_id", "stock", "price_fen"} & set(json.dumps(record, ensure_ascii=False).split('"'))
    assert not any(identity in _rendered(observed) for identity in revealed)
    shown = [row["object"] for row in observed["interaction_bindings"] if row["object_id"] in revealed]
    targets = tool_targets(observed)
    assert shown and all(targets[name]["kind"] == "product" for name in shown)
    assert all(action["price"] is not None for name in shown for action in targets[name]["actions"]
               if action["action"] == "obtain")


def test_a_browse_ending_with_the_other_activity_of_its_request_is_listed_and_pinned_once(engine_factory) -> None:
    """同一请求开始浏览与播客，两项在同一边界结束：原动作只写一条聚合终态（各活动快照，没有顶层浏览结果）；进度层该调用
    的结果仍写 browse_items，恰好一次，浏览得到的供给置顶进工具层商品目标（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「唯一出现原则」）。"""
    engine = engine_factory()
    _stage_open(engine)
    stage_day_plan(engine)
    preview = engine.observe(context(engine, "pair-browse-preview"))
    picks = [next((row["object"], action["verb"], action["label"]) for row in preview["interaction_bindings"]
                  for action in row["actions"] if wanted(action)) for wanted in (
        lambda action: action["canonical_action"] == "browse", lambda action: action["label"] == "接着听")]
    started = _act(engine, "pair-browse-start", "start_activity", start_request(
        targets=[pick[0] for pick in picks], actions=[pick[1] for pick in picks], ways=[pick[2] for pick in picks]))
    assert started.status == "in_progress", (started.error_code, started.message)
    browse_id, podcast_id = (row["activity_id"] for row in started.data["activities"])
    staged = engine.context.state.model_copy(deep=True)
    end = staged.activities[browse_id].planned_end_at
    podcast = staged.activities[podcast_id]
    staged.activities[podcast_id] = podcast.model_copy(
        update={"planned_end_at": end, "planned_duration_seconds": end - podcast.started_at}, deep=True)
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        data={"kind": "test_browse_and_podcast_end_together"})])
    engine.context.state = staged
    _finish(engine, browse_id, "pair-browse")
    assert engine.context.state.activities[podcast_id].ended_at == engine.context.state.activities[browse_id].ended_at
    revealed = engine.context.state.activities[browse_id].data["revealed"]["items"]
    observed = _checked(engine, "pair-browse-seen")
    (record,) = progress_calls(observed, "start_activity")
    assert record["result"]["message"] == "Activities finished"
    names = {item.product_id: item.name for item in engine.context.scenario.products}
    places = {item.location_id: item.name for item in engine.context.scenario.locations}
    assert record["result"]["details"]["browse_items"] == [
        f"{places[item['location_id']]}出售的{names[item['product_id']]}" for item in revealed]
    assert _rendered(observed).count('"browse_items"') == 1
    pinned = tuple(dict.fromkeys(item["supply_id"] for item in revealed))
    assert pinned and engine.observations.pending_presentation[1].pinned_supply_ids == pinned
    shown = {row["object_id"]: row["object"] for row in observed["interaction_bindings"] if row["kind"] == "supply"}
    assert all(tool_targets(observed)[shown[supply]]["kind"] == "product" for supply in pinned)


def test_a_trip_ending_with_its_companion_leaves_the_arrival_to_the_memory_layer(engine_factory) -> None:
    """步行与播客同一请求，两项在到达时一起结束：原动作的聚合终态落在到达那次提交，归路程 session，出发请求整体归原停留
    session，所以到达之后的进度层没有这条调用，任何进度层都不出现 details.arrival（单项出行同样如此）；到达只以记忆层
    路程条目的 settled 句出现一次（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第三章「session 与记忆条目」）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    preview = engine.observe(context(engine, "pair-trip-preview"))
    picks = [next((row["object"], action["verb"], action["label"]) for row in preview["interaction_bindings"]
                  for action in row["actions"] if wanted(row, action)) for wanted in (
        lambda row, action: row["object_id"] == "office" and action["label"] == "步行",
        lambda row, action: action["label"] == "接着听")]
    started = _act(engine, "pair-trip-start", "start_activity", start_request(
        targets=[pick[0] for pick in picks], actions=[pick[1] for pick in picks], ways=[pick[2] for pick in picks]))
    assert started.status == "in_progress", (started.error_code, started.message)
    travel_id, podcast_id = (row["activity_id"] for row in started.data["activities"])
    staged = engine.context.state.model_copy(deep=True)
    podcast, longer = staged.activities[podcast_id], staged.activities[travel_id].planned_end_at + 600
    staged.activities[podcast_id] = podcast.model_copy(
        update={"planned_end_at": longer, "planned_duration_seconds": longer - podcast.started_at}, deep=True)
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        data={"kind": "test_podcast_outlasts_the_trip"})])
    engine.context.state = staged
    _finish(engine, travel_id, "pair-trip")
    assert engine.context.state.activities[podcast_id].ended_at == engine.context.state.activities[travel_id].ended_at
    assert [event.data["message"] for event in engine.context.store.read_agent_events("action_result", 1)
            if event.action_id == started.action_id][-1] == "Activities finished"
    observed = _checked(engine, "pair-trip-seen")
    assert progress_calls(observed, "start_activity") == [] and '"arrival"' not in _rendered(observed)
    trip = activity_name(engine, travel_id)
    lines = [line for entry in observed["memory_layer"]["earlier_today"] for line in entry["settled"]]
    (arrived,) = [line for line in lines if line.startswith(f"{trip}：")]
    assert arrived.endswith("，到达")


def test_just_ended_browse_target_states_the_end_reason_without_the_browse_result(engine_factory) -> None:
    """浏览结束后：工具层刚结束的浏览活动目标只写终局说法与客观进度，不复述进度层该调用结果里的商品名称（结果取该请求的
    最新结果，浏览得到的名称只在那里，docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「唯一出现原则」）。"""
    engine = engine_factory()
    _stage_open(engine)
    stage_day_plan(engine)
    _, browse = _observed_act(engine, "ended-browse-start", "start_activity",
                              _choice(engine.observe(context(engine, "ended-browse-preview")), "browse"))
    _finish(engine, browse.activity_id, "ended-browse")
    observed = _checked(engine, "ended-browse-seen")
    (record,) = [row for row in progress_calls(observed, "start_activity")
                 if "browse_items" in row["result"]["details"]]
    activity = engine.context.state.activities[browse.activity_id]
    target = tool_targets(observed)[activity_name(engine, browse.activity_id)]
    assert target["phase"] == "刚结束"
    assert target["state"] == (f"{activity_control.ending_phrase(activity)}，"
                               f"{activity_control.objective_progress(activity)}")
    assert record["result"]["details"]["browse_items"]
    assert not any(name in target["state"] for name in record["result"]["details"]["browse_items"])

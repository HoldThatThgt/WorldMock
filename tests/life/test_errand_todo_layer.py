"""待办层的个人事务与观察的私有事务绑定（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-校验与错误 、2.6）。

经真实引擎：正式场景与目录建立的 LifeEngine 上预置未完成事务（stage_errands 同一提交落库），再经 engine.observe 取得观察，
并与 SQLite 里存下的观察事件逐值比对。待办层键序为 layer、paused_activities、errands、day_plan；事务行按记下的时刻与事务
身份排序，六键 name、done_when、progress、memo、remind_time、noted_on，不含内部身份、状态枚举与可用性。观察另带两类私有
绑定 errand_bindings 与 errand_item_bindings，它们与 errand_id 都是提示禁键；事务名不是回看名称。
"""

from __future__ import annotations

import pytest

from tests.life.conftest import context, stage_day_plan, stage_errands
from worldmock.errors import BusinessRuleError
from worldmock.life.errand_state import MAX_OPEN_ERRANDS, ErrandState
from worldmock.life.models import RecallTaskSummaryRequest
from worldmock.life.narration import ForbiddenObservationFieldError, render_layer_json
from worldmock.life.prompt_schemas import TODO_LAYER_SCHEMA, validate_layer
from worldmock.life.readable import local_date
from worldmock.life.schedule import PlanGoal, epoch_clock

#: 正式目录里的真实商品：食材包（取得）、素炒什锦（吃掉）与一种饮料。
_KIT, _MEAL, _TEA = "ingredient-vegetable-kit", "meal-0030", "food-6922507005019"
_ROW_KEYS = ["name", "done_when", "progress", "memo", "remind_time", "noted_on"]
#: 五层环境构建的提示层（人设层由宿主构建，不含事务）。
_LAYER_KEYS = ("tool_layer", "todo_layer", "environment_layer", "memory_layer", "progress_layer")


def _errand(errand_id: str, name: str, product_id: str, registered_at: int, **fields) -> ErrandState:
    """一条未完成事务：缺省取得一份、尚无进展、没有备注与提醒。"""
    values = {"outcome": "obtain", "quantity": 1, "progress": 0.0, "memo": None, "remind_at": None,
              "reminded_at": None, "origin_action_id": errand_id.split(":")[0], "origin_event_id": f"event-{errand_id}"}
    return ErrandState(errand_id=errand_id, name=name, product_id=product_id, registered_at=registered_at,
                       **{**values, **fields})


def _three_errands(now: int) -> list[ErrandState]:
    """三条事务，按插入顺序故意打乱：最早记下的在中间，两条同刻记下的按事务身份排序（action-a:errand:0 在前）。"""
    return [
        _errand("action-a:errand:1", "囤三瓶冰红茶", _TEA, now - 3600, quantity=3, progress=1.0),
        _errand("action-b:errand:0", "补一份素炒什锦食材包", _KIT, now - 7200, memo="下班前下单送到家",
                remind_at=now + 17 * 3600),
        _errand("action-a:errand:0", "晚饭吃掉素炒什锦", _MEAL, now - 3600, outcome="eat", quantity=2, progress=0.5),
    ]


def _stored_observation(engine, observed: dict) -> dict:
    """SQLite 里与这次观察同一事件身份的观察事件数据。"""
    (event,) = [item for item in engine.context.store.read_agent_events("observation", 1)
                if item.event_id == observed["observation_event_id"]]
    return event.data


def test_todo_layer_lists_open_errands_between_paused_activities_and_day_plan(engine_factory) -> None:
    """待办层键序为 layer、paused_activities、errands、day_plan；事务行按记下的时刻、再按事务身份排序，六键依次为名称、
    「取得<商品名> N 份」或「吃掉<商品名> N 份」、「已取得 N 份」或「已吃掉 N 份」（尚无进展写 0 份，小数照份量短语）、
    备注（可为 null）、尚未提醒的钟点与记下的本地日期；整层通过待办层 Schema，与存进 SQLite 的观察事件逐值相同。"""
    engine = engine_factory()
    now, zone = engine.context.state.current_time, engine.context.scenario.timezone
    stage_day_plan(engine, goals=(PlanGoal(intent_kind="用餐", goal="好好吃晚饭", registered_at=now),))
    stage_errands(engine, 1, _three_errands(now))
    observed = engine.observe(context(engine, "errand-todo"))
    todo = observed["todo_layer"]
    assert list(todo) == ["layer", "paused_activities", "errands", "day_plan"]
    assert todo["day_plan"]["goals"][0]["goal"] == "好好吃晚饭"
    assert [list(row) for row in todo["errands"]] == [_ROW_KEYS] * 3
    tea = engine.context.rules.items[_TEA].name
    assert todo["errands"] == [
        {"name": "补一份素炒什锦食材包", "done_when": "取得素炒什锦食材包 1 份", "progress": "已取得 0 份",
         "memo": "下班前下单送到家", "remind_time": epoch_clock(now + 17 * 3600, zone),
         "noted_on": local_date(now - 7200, zone)},
        {"name": "晚饭吃掉素炒什锦", "done_when": "吃掉素炒什锦 2 份", "progress": "已吃掉 0.5 份", "memo": None,
         "remind_time": None, "noted_on": local_date(now - 3600, zone)},
        {"name": "囤三瓶冰红茶", "done_when": f"取得{tea} 3 份", "progress": "已取得 1 份", "memo": None,
         "remind_time": None, "noted_on": local_date(now - 3600, zone)},
    ]
    assert todo["errands"][0]["remind_time"] == "17:00" and todo["errands"][0]["noted_on"] == "2026-01-04"
    validate_layer(todo)
    assert TODO_LAYER_SCHEMA["properties"]["errands"]["maxItems"] == MAX_OPEN_ERRANDS
    assert TODO_LAYER_SCHEMA["required"] == ["layer", "paused_activities", "errands", "day_plan"]
    assert _stored_observation(engine, observed)["todo_layer"] == todo


def test_todo_layer_errands_is_an_empty_list_without_errands(engine_factory) -> None:
    """没有未完成事务时 errands 是必填的空列表（不是缺键、不是 null），事务绑定也为空；缺 errands 键的待办层不合 Schema。"""
    engine = engine_factory()
    observed = engine.observe(context(engine, "errand-none"))
    todo = observed["todo_layer"]
    assert todo["errands"] == [] and list(todo) == ["layer", "paused_activities", "errands", "day_plan"]
    validate_layer(todo)
    assert observed["errand_bindings"] == []
    missing = {key: value for key, value in todo.items() if key != "errands"}
    with pytest.raises(ValueError, match="errands"):
        validate_layer(missing)


def test_reminded_or_unset_reminder_shows_null_remind_time(engine_factory) -> None:
    """remind_time 只写尚未交付的提醒钟点：已交付的提醒与没有提醒时刻的事务都写 null（提醒时刻照存在状态里）。"""
    engine = engine_factory()
    now, zone = engine.context.state.current_time, engine.context.scenario.timezone
    stage_errands(engine, 1, [
        _errand("action-r:errand:0", "等提醒的事务", _KIT, now - 60, remind_at=now + 600),
        _errand("action-r:errand:1", "已提醒的事务", _MEAL, now - 60, outcome="eat", remind_at=now - 30,
                reminded_at=now - 30),
        _errand("action-r:errand:2", "不提醒的事务", _TEA, now - 60),
    ])
    todo = engine.observe(context(engine, "errand-remind"))["todo_layer"]
    assert [row["remind_time"] for row in todo["errands"]] == [epoch_clock(now + 600, zone), None, None]
    assert engine.context.state.agents[1].errands["action-r:errand:1"].remind_at == now - 30
    validate_layer(todo)


def test_observation_carries_private_errand_bindings_outside_every_layer(engine_factory) -> None:
    """观察带两类私有事务绑定：errand_bindings（未完成事务的名称、事务身份、结果种类、商品身份，待办层顺序）与
    errand_item_bindings（全部商品名到商品身份，按商品身份排序），都存进 SQLite 的观察事件；它们与 errand_id 不进任何
    一层（五层照常渲染，事务身份与事务的商品身份不出现在提示文字里；任何一层带上这三个键即渲染失败）；事务名不是回看
    名称，按事务名回看任务以 TASK_NOT_FOUND 拒绝。"""
    engine = engine_factory()
    now = engine.context.state.current_time
    stage_errands(engine, 1, _three_errands(now))
    observed = engine.observe(context(engine, "errand-bindings"))
    assert observed["errand_bindings"] == [
        {"name": "补一份素炒什锦食材包", "errand_id": "action-b:errand:0", "outcome": "obtain", "product_id": _KIT},
        {"name": "晚饭吃掉素炒什锦", "errand_id": "action-a:errand:0", "outcome": "eat", "product_id": _MEAL},
        {"name": "囤三瓶冰红茶", "errand_id": "action-a:errand:1", "outcome": "obtain", "product_id": _TEA},
    ]
    items = engine.context.rules.items
    assert observed["errand_item_bindings"] == [{"name": items[product_id].name, "product_id": product_id}
                                                for product_id in sorted(items)]
    assert {_KIT, _MEAL, _TEA} <= {row["product_id"] for row in observed["errand_item_bindings"]}
    stored = _stored_observation(engine, observed)
    assert (stored["errand_bindings"], stored["errand_item_bindings"]) == (
        observed["errand_bindings"], observed["errand_item_bindings"])
    rendered = "".join(render_layer_json(observed[key]) for key in _LAYER_KEYS)
    for row in observed["errand_bindings"]:
        assert row["errand_id"] not in rendered and row["product_id"] not in rendered
    todo = observed["todo_layer"]
    leaks = [{**todo, "errand_bindings": observed["errand_bindings"]},
             {**todo, "errand_item_bindings": observed["errand_item_bindings"]},
             {**todo, "errands": [{**todo["errands"][0], "errand_id": "action-b:errand:0"}]}]
    for leak in leaks:
        with pytest.raises(ForbiddenObservationFieldError):
            render_layer_json(leak)
    with pytest.raises(BusinessRuleError, match="TASK_NOT_FOUND"):
        engine.read_task_summary(1, RecallTaskSummaryRequest(task_name="补一份素炒什锦食材包"), observed)


def test_the_todo_layer_schema_closes_an_errand_row_to_exactly_its_six_keys(engine_factory) -> None:
    """待办层 Schema 把事务行限定为恰好六键（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-待办层与进度层呈现 、6.4；接口合同「七层 Schema」）：真实观察的
    待办层（与存进 SQLite 的观察事件逐值相同）通过校验；在它的第一条事务行上多写内部身份、状态或可用性中的任何一个键，
    或少写六键中的任何一个，校验都以 ValueError 指明待办层与该行的位置，其余行原样不动。"""
    engine = engine_factory()
    stage_errands(engine, 1, _three_errands(engine.context.state.current_time))
    observed = engine.observe(context(engine, "errand-closed"))
    todo = observed["todo_layer"]
    assert _stored_observation(engine, observed)["todo_layer"] == todo
    validate_layer(todo)
    first, rest = todo["errands"][0], todo["errands"][1:]
    assert list(first) == _ROW_KEYS and len(rest) == 2
    broken = [{**first, **extra} for extra in ({"errand_id": "action-b:errand:0"}, {"status": "未完成"},
                                                {"available": True})]
    broken += [{key: value for key, value in first.items() if key != missing} for missing in _ROW_KEYS]
    for row in broken:
        with pytest.raises(ValueError, match=r"Prompt layer 待办层 violates its schema at \['errands', 0\]"):
            validate_layer({**todo, "errands": [row, *rest]})


def test_each_persons_todo_layer_and_bindings_list_only_their_own_errands(engine_factory) -> None:
    """事务由记下它的人物持有（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-范围与状态 、2.6）。两人场景经真实请求、引擎与 SQLite：陈曦在收尾
    请求里记下「买牛奶」之后，李凡的观察里待办层 errands 与私有 errand_bindings 都还是空的；李凡随后记下自己的「囤冰红茶」，
    两人的待办层与私有绑定各只列本人那一条，存进 SQLite 的本人观察事件与之逐值相同。"""
    from tests.life.conftest import errand_add
    from tests.life.routing_fixtures import observe, open_world
    from tests.life.test_errand_completion import MILK, note

    engine = open_world(engine_factory)
    note(engine, "mine", [errand_add("买牛奶", MILK)])
    alone = observe(engine, "alone", 2)
    assert alone["todo_layer"]["errands"] == [] and alone["errand_bindings"] == []
    note(engine, "theirs", [errand_add("囤冰红茶", engine.context.rules.items[_TEA].name)], agent_id=2)
    for agent_id, name in ((1, "买牛奶"), (2, "囤冰红茶")):
        observed = observe(engine, f"both-{agent_id}", agent_id)
        assert [row["name"] for row in observed["todo_layer"]["errands"]] == [name]
        assert [row["name"] for row in observed["errand_bindings"]] == [name]
        [stored] = [event for event in engine.context.store.read_agent_events("observation", agent_id)
                    if event.event_id == observed["observation_event_id"]]
        assert stored.data["todo_layer"]["errands"] == observed["todo_layer"]["errands"]
        assert stored.data["errand_bindings"] == observed["errand_bindings"]


def test_the_host_corrects_a_recall_by_the_name_of_an_open_errand(engine_factory) -> None:
    """事务名不进回看名称，宿主与环境对同一冻结观察判定一致（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-校验与错误  首句与末条）：真实引擎上
    预置一条当日目标与三条未完成事务，冻结观察（存进 SQLite）并按它准备直接导入的 LifeAgent。按任何一条事务名回看任务，
    宿主都以 NAME_NOT_FOUND 纠正（不把这次回看放给环境），环境对同一份观察也以 TASK_NOT_FOUND 拒绝；同一份观察里
    待办层写出的当日目标名照常可以回看，宿主与环境都受理。"""
    from tests.life.test_name_resolution import _check, _open

    engine = engine_factory()
    now = engine.context.state.current_time
    stage_day_plan(engine, goals=(PlanGoal(intent_kind="用餐", goal="好好吃晚饭", registered_at=now),))
    stage_errands(engine, 1, _three_errands(now))
    _, observed, host = _open(engine, "errand-recall")
    assert type(host).__module__ == "custom.agents.life_agent"
    assert _stored_observation(engine, observed)["errand_bindings"] == observed["errand_bindings"]
    names = [row["name"] for row in observed["todo_layer"]["errands"]]
    assert names == [row["name"] for row in observed["errand_bindings"]]
    assert names == ["补一份素炒什锦食材包", "晚饭吃掉素炒什锦", "囤三瓶冰红茶"]
    for name in names:
        assert _check(host, "recall_task_summary", {"task_name": name}) == (
            f"NAME_NOT_FOUND: The name {name} is not known.")
        with pytest.raises(BusinessRuleError, match="TASK_NOT_FOUND"):
            engine.read_task_summary(1, RecallTaskSummaryRequest(task_name=name), observed)
    goal = observed["todo_layer"]["day_plan"]["goals"][0]["goal"]
    assert goal == "好好吃晚饭" and _check(host, "recall_task_summary", {"task_name": goal}) == ""
    recalled = engine.read_task_summary(1, RecallTaskSummaryRequest(task_name=goal), observed)
    assert recalled["recall"] == {"kind": "goal", "goal": goal, "intent_kind": "用餐", "progressed": False}

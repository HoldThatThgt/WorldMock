"""零点计划、上班安排与明天（docs/design/06-agent.md「零点计划」与「环境层」，docs/design/04-simulation-engine.md
「唤醒、日计划与个人事务」）：都经真实引擎与 SQLite。

零点计划经生产入口 runtime.settle_pending_day_plans（确定性客户端桩记下那次请求）；环境层读 observe_only 的观察与
落库的那次观察；个人事务经 act 提交真实的 conclude_activity。人设的例外、去掉的班次、去掉的工作安排与挪到零点的
运行开始都由 tests/life/work_arrangement_inputs.py 的变更函数写在样例环境的副本上。
"""

from __future__ import annotations

import json

import pytest

from tests.life.conftest import StubPlanDayClient, conclude_request, do_at, errand_add, settle_day_plans
from tests.life.environment_runtime import act, observe_only, until_ended
from tests.life.work_arrangement_inputs import (
    holiday_monday,
    makeup_monday,
    no_work_schedule,
    rest_tuesday,
    rest_tuesday_from_midnight,
    shorter_monday,
)
from worldmock.simulation_engine.scheduling.plans.day_planning import PLAN_DAY_PROMPT
from worldmock.agents.prompt.prompt_schemas import validate_layer

#: 环境层的键次序：明天是最后一个键。
_ENVIRONMENT_KEYS = ["layer", "observed_at", "body", "location", "weather", "duty", "tomorrow"]
#: 运行开始那一刻（2026-01-05 星期一 08:00）观察到的明天：样例环境星期二照常上班。
_WORKING_TUESDAY = {"date": "2026-01-06", "weekday": "星期二", "day": "工作日"}


def _planned_and_observed(engine):
    """运行开始那一刻：零点计划经生产入口执行一次（桩记下请求），再观察一次。返回（零点计划请求，用户消息的输入，
    观察，落库的这次观察的环境层）。"""
    client = StubPlanDayClient({"requested_model": engine.context.config.model.requested_model})
    settle_day_plans(engine, client)
    [request] = client.calls
    observed = observe_only(engine, "arrangement")
    stored = engine.context.store.read_agent_events("observation", 1)[-1].data["environment_layer"]
    return request, json.loads(request["messages"][1]["content"]), observed, stored


def test_a_holiday_exception_note_reaches_the_plan_and_the_environment_layer(engine_factory):
    """星期一按日期例外休息：零点计划请求的系统消息就是模板，输入「上班安排」带说明、「生成时刻」照写星期一；
    环境层七个键依次出现，duty 是带说明的六键休息日，tomorrow 是星期二上班；整层合 Schema，落库的观察与返回的相同。"""
    request, payload, observed, stored = _planned_and_observed(engine_factory(mutate=holiday_monday))
    assert request["messages"][0]["content"] == PLAN_DAY_PROMPT
    assert payload["上班安排"] == {"breaks": [], "day": "休息日", "hours": None,
                               "note": "平常星期一上班，今天按工作安排休息", "place": None}
    assert payload["生成时刻"] == "1月5日 08:00（星期一）"
    layer = observed["environment_layer"]
    assert list(layer) == _ENVIRONMENT_KEYS
    assert list(layer["duty"].items()) == [("day", "休息日"), ("hours", None), ("place", None), ("breaks", []),
                                           ("note", "平常星期一上班，今天按工作安排休息"), ("status", None)]
    assert layer["tomorrow"] == _WORKING_TUESDAY
    validate_layer(layer)
    assert (stored["duty"], stored["tomorrow"]) == (layer["duty"], layer["tomorrow"])


def test_a_make_up_workday_note_reaches_the_plan_and_the_environment_layer(engine_factory):
    """每周声明不含星期一、日期例外让这天上班：计划输入与 duty 的说明都是「平常星期一休息，今天按工作安排上班」，
    duty 的状态是「未到上班时间」。"""
    _, payload, observed, _ = _planned_and_observed(engine_factory(mutate=makeup_monday))
    duty = observed["environment_layer"]["duty"]
    assert payload["上班安排"]["note"] == duty["note"] == "平常星期一休息，今天按工作安排上班"
    assert (payload["上班安排"]["day"], duty["day"], duty["status"]) == ("工作日", "工作日", "未到上班时间")


@pytest.mark.parametrize("mutate", [None, shorter_monday], ids=["sample", "shorter-monday"])
def test_no_note_without_a_change_of_work_or_rest(engine_factory, mutate):
    """没有改上不上班的日子没有说明：样例环境原样，和同是上班只差时段的日期例外，计划输入与 duty 的 note 都是 null；
    后者的时段与休息照例外写。"""
    _, payload, observed, _ = _planned_and_observed(engine_factory(mutate=mutate))
    duty = observed["environment_layer"]["duty"]
    assert payload["上班安排"]["note"] is None and duty["note"] is None
    assert duty["day"] == payload["上班安排"]["day"] == "工作日"
    if mutate is shorter_monday:
        assert (duty["hours"], duty["breaks"]) == ("10:00–17:00", [])
        assert (payload["上班安排"]["hours"], payload["上班安排"]["breaks"]) == ("10:00–17:00", [])


def test_tomorrow_reads_the_arrangement_of_the_next_local_date(engine_factory):
    """明天按下一个本地日的安排写：星期二按日期例外休息时，今天照常上班、没有说明，tomorrow 写星期二休息日；
    落库的这次观察与返回的相同。"""
    _, _, observed, stored = _planned_and_observed(engine_factory(mutate=rest_tuesday))
    layer = observed["environment_layer"]
    assert (layer["duty"]["day"], layer["duty"]["note"]) == ("工作日", None)
    assert layer["tomorrow"] == {"date": "2026-01-06", "weekday": "星期二", "day": "休息日"}
    assert stored["tomorrow"] == layer["tomorrow"]


def test_tomorrow_counts_the_local_date_in_the_run_timezone(engine_factory):
    """本地日按运行配置的时区数，与 UTC 的日期无关：运行从星期一 00:00（上海）开始，那一刻 UTC 还是 1 月 4 日星期日；
    星期二按日期例外休息时，计划的生成时刻写星期一，tomorrow 仍写星期二休息日；落库的这次观察与返回的相同。"""
    _, payload, observed, stored = _planned_and_observed(engine_factory(mutate=rest_tuesday_from_midnight))
    assert payload["生成时刻"] == "1月5日 00:00（星期一）"
    layer = observed["environment_layer"]
    assert (layer["duty"]["day"], layer["duty"]["note"]) == ("工作日", None)
    assert layer["tomorrow"] == {"date": "2026-01-06", "weekday": "星期二", "day": "休息日"}
    assert stored["tomorrow"] == layer["tomorrow"]


def test_tomorrow_has_no_day_without_a_work_schedule(engine_factory):
    """没有工作安排：duty 与计划输入「上班安排」都是 null，明天照样写日期与星期，day 为 null；环境层合 Schema。"""
    _, payload, observed, _ = _planned_and_observed(engine_factory(mutate=no_work_schedule))
    layer = observed["environment_layer"]
    assert layer["duty"] is None and payload["上班安排"] is None
    assert layer["tomorrow"] == {"date": "2026-01-06", "weekday": "星期二", "day": None}
    validate_layer(layer)


def test_an_errand_without_goods_is_refused_and_a_product_errand_is_kept(engine_factory):
    """个人事务只记商品：收尾时新增 item 为「给母亲打个电话」的事务，整条请求以 ACTION_REQUEST_INVALID 拒绝、明细指到
    那一项 item，活动仍待收尾、事务仍为空；再收尾、新增「纸巾」，受理并登记一条纸巾一份的事务。"""
    engine = engine_factory()
    _, started = act(engine, "rest", do_at("家", "测试休息"))
    assert started.status == "in_progress", started.error_code
    until_ended(engine, started.activity_id)
    name = engine.context.state.activities[started.activity_id].name
    assert name == "进行测试休息（1月5日 08:00 起）"
    _, refused = act(engine, "call", conclude_request(name, errands=[errand_add("给母亲打个电话", "给母亲打个电话")]))
    reason = "No product is named 「给母亲打个电话」."
    assert (refused.status, refused.error_code) == ("rejected", "ACTION_REQUEST_INVALID")
    details = refused.data
    assert (details["reason"], details["next_step"], details["fix"], details["unmet"]) == (
        reason, "Choose a known object and an offered action.", "Use an exact name.", [])
    [item] = details["items"]
    assert item["path"] == ["tool_calls", 0, "function", "arguments", "errands", 0, "item"]
    assert (item["written"], item["error_code"], item["expected"], item["why"]) == (
        "给母亲打个电话", "NAME_NOT_FOUND", "An exact known product name is required.", reason)
    assert (item["allowed_values"], item["candidates"]) == ([], [])
    agent = engine.context.state.agents[1]
    assert engine.context.state.activities[started.activity_id].concluded_at is None and not agent.errands
    _, kept = act(engine, "tissue", conclude_request(name, errands=[errand_add("纸巾", "纸巾")]))
    assert kept.status == "completed", kept.error_code
    [errand] = engine.context.state.agents[1].errands.values()
    assert (errand.product_id, errand.quantity) == ("tissue", 1)

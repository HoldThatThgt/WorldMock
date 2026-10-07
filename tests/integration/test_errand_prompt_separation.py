"""个人事务与记忆、进度参数的分工经真实请求、引擎与 SQLite（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-记下更新取消 、2.6、3.4 第 2 项）。

真实 LifeEngine、SQLite、LifeEnv 与从工作区装配、启用记忆的 LifeAgent（经 LifeEnv 观察、登记与写记忆证据），只替换远程
模型传输；不调用人物模型的过渡步骤（开始等待、出门、到达）照真实顺序直接登记给引擎。
- 事务变更不是记忆写入点：收尾时一并记下的事务不进 AS2 记忆库，memory_updated 只为收尾记忆写一次；进度层工具调用的
  参数略去 errands，回执 details.errands 是事务变更在进度层的唯一痕迹（只有名称）；离开这段停留之后，记忆层 self_notes
  里有那次收尾与暂停写下的自述与记忆，却没有事务的名称、备注与取消原因。
- 跨夜：23:51 播报时记下的事务，过了零点后下一次实际行动请求的待办层原样列着它，这次决策只有一个模型请求，没有任何
  记忆查询。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from custom.envs.life_env import LifeEnv  # noqa: E402
from tests.integration.test_conclude_memories import _person, _ScriptedModel  # noqa: E402
from tests.integration.test_life_agent_protocol import _memory, _ModelResponse  # noqa: E402
from tests.integration.test_unique_presence_run import _holders  # noqa: E402
from tests.engine_fixture import (engine_factory)  # noqa: F401
from tests.life.conftest import (advance_next, context, errand_add, errand_cancel, errand_update, start_arguments, wake_by_mail)  # noqa: F401
from tests.life.routing_fixtures import (  # noqa: E402
    MEAL_NAME,
    SNACK_NAME,
    WAIT_ITEM,
    act,
    observe,
    routed,
    run_until,
    single_person_home,
)
from tests.life.test_errand_reminder import evening, moment  # noqa: E402
from tests.life.test_memory_layer import _arrive, _walk  # noqa: E402
from worldmock.life import prompt_capacity  # noqa: E402
from worldmock.life.tool_groups import expand_tool_layer  # noqa: E402

#: 收尾时记下的两条事务与其后的更新、取消（名称、备注与原因都不在别处出现）。
_KIT_ERRAND, _MILK_ERRAND = "周六做饭的那份食材包", "给自己囤一盒奶"
_KIT_MEMO, _KIT_MEMO_LATER, _MILK_MEMO = "顺路去小区门口那家看看", "改成下单送到楼下", "冰箱最下层留个位置"
_MILK_REASON = "妈妈说周末会带过来"
_ERRAND_TEXTS = (_KIT_ERRAND, _MILK_ERRAND, _KIT_MEMO, _KIT_MEMO_LATER, _MILK_MEMO, _MILK_REASON)
#: 同一次收尾写下的自述与一条收尾记忆，以及其后暂停的自述。
_NOTED = "等了一会儿，想起冰箱空了。"
_FEELING = _memory("冰箱空着的时候心里总不踏实。", importance=0.3)
_PAUSED = "先停一下，去公司一趟。"


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    """脚本模型使用固定供应商窗口，不请求外部模型元数据。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1_048_576)


def _environment(engine) -> LifeEnv:
    """把真实引擎接到 LifeEnv（人物经它观察、登记、留证与写记忆证据）。"""
    environment = LifeEnv()
    environment.engine = engine
    return environment


async def _decide(environment: LifeEnv, person, round_id: str) -> tuple:
    """真实人物步骤：开轮、观察、经人物模型登记唯一世界请求（登记时写记忆），然后结算；返回（决策身份，登记回执，结果）。"""
    decision = context(environment.engine, round_id)
    await environment.begin_decision_round((decision,))
    person.bind_decision_context(decision)
    registered = await person.step(0, decision.observed_at)
    [result] = await environment.commit_decision_round(decision.round_id)
    return decision, registered, result


def _activity(request: dict, phase: str) -> str | None:
    """一次模型请求的工具层里处于给定阶段的活动目标名（宿主实际列给模型的写法）；没有时为 None。"""
    tools = expand_tool_layer(json.loads(request["messages"][1]["content"])[0])
    names = [target["name"] for target in tools["targets"] if target["kind"] == "activity" and target["phase"] == phase]
    assert len(names) <= 1, names
    return names[0] if names else None


def _call(call_id: str, tool: str, arguments: Callable[[dict], dict]) -> Callable[[dict], _ModelResponse]:
    """脚本项：按本次实际请求构造唯一一个工具调用（带思考，回复正文为空）。"""
    def respond(request: dict) -> _ModelResponse:
        """@brief 由本次请求写出参数。"""
        return _ModelResponse(call_id, tool, json.dumps(arguments(request), ensure_ascii=False),
                              reasoning_content="按眼前的情况决定。")
    return respond


def _receipt(changes: list[dict]) -> list[dict]:
    """一次受理的事务变更回执：按请求顺序的（名称，做什么）。"""
    return [{"errand": item["name"], "change": item["change"]} for item in changes]


def _memory_updates(engine) -> list[tuple]:
    """人物 1 每一步的记忆证据（memory_updated）：（决策身份，写入点，新建条目，合并条目，本步条目原文）。"""
    return [(event.decision_id, event.data["trigger"], event.data["created_episode_ids"],
             event.data["merged_episode_ids"], [item["text"] for item in event.data["episodes"]])
            for event in engine.context.store.read_agent_events("memory_updated", 1)]


def _control_records(progress: dict) -> list[dict]:
    """进度层里结束与暂停活动的工具调用记录（按记录顺序）。"""
    return [row for row in progress["events"]
            if row["type"] == "tool_call" and row["tool"] in {"conclude_activity", "pause_activity"}]


def _assert_receipts_only(progress: dict) -> None:
    """进度层里事务只留回执：调用参数没有 errands，备注与取消原因不出现，名称只作为回执的 errand 键（或进度记录的 errand
    键）出现。"""
    assert all("errands" not in row["arguments"] for row in _control_records(progress))
    text = json.dumps(progress, ensure_ascii=False)
    assert [item for item in (_KIT_MEMO, _KIT_MEMO_LATER, _MILK_MEMO, _MILK_REASON) if item in text] == []
    for name in (_KIT_ERRAND, _MILK_ERRAND):
        assert _holders(progress, name) and set(_holders(progress, name)) == {"errand"}, name


@pytest.mark.asyncio
async def test_errand_changes_write_no_memory_and_stay_out_of_self_notes_and_progress_arguments(
        engine_factory, tmp_path) -> None:  # noqa: F811
    """12:31 真实 LifeAgent 收尾进行中的等待，写下自述、一条收尾记忆，并在请求级 errands 里新增两条事务（各带备注）：
    登记时宿主只把收尾记忆写进 AS2 记忆库（这一步的 memory_updated 触发点只有 activity_conclusion、新建条目只有这一条），
    事务的名称与备注不进记忆库。12:32 同一个人物暂停另一段等待，更新一条事务的备注、取消另一条并写原因：这一步的记忆
    证据没有写入点，也没有新建或合并任何条目（每一步都留一条记忆证据，只有写入点才带条目）。这段停留里
    人物实际看到的进度层与之后的观察中，两次调用的参数都略去 errands（结束活动只剩活动名、自述与收尾记忆），回执是事务
    在进度层的唯一痕迹，名称只作为回执的 errand 键，备注与原因都不出现。随后出门去公司、到达后收尾：到达后那次实际
    请求的记忆层里，在家这段停留的条目 self_notes 写着收尾与暂停的自述和收尾记忆，却没有事务的名称、备注与原因；新
    session 的进度层只有到达时收尾的调用（回执是空列表），也没有事务的文字；事务只在待办层（更新后的那条）。整个过程只有
    那次收尾是记忆写入点，AS2 记忆库里只有那条收尾记忆。"""
    engine = single_person_home(engine_factory, {})
    environment = _environment(engine)
    assert act(engine, "wait", routed(WAIT_ITEM)).status == "in_progress"
    wake_by_mail(engine, offset=60)
    advance_next(engine)
    noted = [errand_add(_KIT_ERRAND, MEAL_NAME, memo=_KIT_MEMO), errand_add(_MILK_ERRAND, SNACK_NAME, memo=_MILK_MEMO)]
    changed = [errand_update(_KIT_ERRAND, memo=_KIT_MEMO_LATER), errand_cancel(_MILK_ERRAND, _MILK_REASON)]
    model = _ScriptedModel([
        _call("call-note", "conclude_activity", lambda request: {"items": [{
            "activity_name": _activity(request, "进行中"), "summary": _NOTED, "memories": [_FEELING], "views": []}],
            "errands": noted}),
        _call("call-pause", "pause_activity", lambda request: {"items": [{
            "activity_name": _activity(request, "进行中"), "summary": _PAUSED}], "errands": changed}),
        _call("call-office", "start_activity", lambda request: start_arguments(["自己"], ["interact"], ["等待"]))])
    person = await _person(environment, tmp_path / "agent_0001", model)

    decision, registered, result = await _decide(environment, person, "note")
    assert (registered, result.status.value, result.data["errands"]) == (
        "conclude_activity registered", "completed", _receipt(noted))
    stored = person._memory_runtime.store.iter_episodes()
    assert [item["text"] for item in stored] == [_FEELING["text"]]
    assert _memory_updates(engine) == [(decision.decision_id, ["activity_conclusion"], [stored[0]["id"]], [],
                                        [_FEELING["text"]])]
    assert act(engine, "rest", routed(WAIT_ITEM)).status == "in_progress"
    wake_by_mail(engine, offset=60)
    advance_next(engine)
    paused, registered, result = await _decide(environment, person, "pause")
    assert (registered, result.status.value, result.data["errands"]) == (
        "pause_activity registered", "completed", _receipt(changed))
    assert _memory_updates(engine)[1:] == [(paused.decision_id, [], [], [], [])]

    seen = json.loads(model.requests[1]["messages"][1]["content"])[5]
    [concluded] = _control_records(seen)
    assert concluded["arguments"] == {"items": [{"activity_name": concluded["arguments"]["items"][0]["activity_name"],
                                                 "summary": _NOTED, "memories": [_FEELING]}]}
    assert concluded["result"]["details"]["errands"] == _receipt(noted)
    _assert_receipts_only(seen)
    progress = observe(engine, "home")["progress_layer"]
    assert [row["result"]["details"]["errands"] for row in _control_records(progress)] == [
        _receipt(noted), _receipt(changed)]
    _assert_receipts_only(progress)

    _arrive(engine, "arrive", _walk(engine, "walk").activity_id)
    _, registered, _ = await _decide(environment, person, "office")
    assert registered == "start_activity registered"
    layers = json.loads(model.requests[2]["messages"][1]["content"])
    memory, todo, office = layers[2], layers[3], layers[5]
    [stay] = [entry for entry in memory["earlier_today"] if any(note["text"] == _NOTED for note in entry["self_notes"])]
    assert {_NOTED, _FEELING["text"], _PAUSED} <= {note["text"] for note in stay["self_notes"]}
    assert [row["result"]["details"]["errands"] for row in _control_records(office)] == [[]]
    text = json.dumps([memory, office], ensure_ascii=False)
    assert [item for item in _ERRAND_TEXTS if item in text] == []
    assert [(row["name"], row["memo"]) for row in todo["errands"]] == [(_KIT_ERRAND, _KIT_MEMO_LATER)]
    assert [row for row in _memory_updates(engine) if row[1] or row[2] or row[3]] == _memory_updates(engine)[:1]
    episodes = json.dumps(person._memory_runtime.store.iter_episodes(), ensure_ascii=False)
    assert _FEELING["text"] in episodes and [item for item in _ERRAND_TEXTS if item in episodes] == []


def _next_step(request: dict) -> dict:
    """过了零点之后那次决策的唯一世界请求：有刚结束的活动就收尾，有进行中的就播报，否则开始等待；都不带事务变更。"""
    ended, running = _activity(request, "刚结束"), _activity(request, "进行中")
    if ended is not None:
        return {"tool": "conclude_activity", "arguments": {"items": [
            {"activity_name": ended, "summary": "等完了。", "memories": [], "views": []}], "errands": []}}
    if running is not None:
        return {"tool": "report_status", "arguments": {"items": [
            {"activity_name": running, "summary": "还在等。"}], "errands": []}}
    return {"tool": "start_activity", "arguments": start_arguments(["自己"], ["interact"], ["等待"])}


def _after_midnight(request: dict) -> _ModelResponse:
    """脚本项：按 _next_step 选出的唯一世界请求作答，不做任何记忆查询。"""
    step = _next_step(request)
    return _ModelResponse("call-after-midnight", step["tool"], json.dumps(step["arguments"], ensure_ascii=False),
                          reasoning_content="过了零点，接着过。")


@pytest.mark.asyncio
async def test_the_next_action_request_shows_the_errand_in_the_todo_layer_without_any_memory_query(
        engine_factory, tmp_path) -> None:  # noqa: F811
    """1 月 5 日 23:50 人物开始等待，23:51 真实 LifeAgent 播报这段等待时记下「吃掉薯片 1 份」（备注、次日 07:30 提醒）；
    播报之后时间推进，零点边界照常（零点计划由确定性桩登记）。过了零点后人物的下一次决策：库里记下的这一次实际行动请求
    （model_request）只有 system 与六层 user 两条消息（不是续接），环境层是 1 月 6 日，待办层原样列着这条事务（记下日期
    仍是 1 月 5 日、提醒钟点 07:30、已吃掉 0 份）；本决策只有这一个模型请求，响应只调用了世界请求，没有任何记忆查询
    （记忆工具照常开放）。"""
    engine = evening(engine_factory, "23:50")
    environment = _environment(engine)
    assert act(engine, "wait", routed(WAIT_ITEM)).status == "in_progress"
    wake_by_mail(engine, offset=60)
    advance_next(engine)
    snack = errand_add("明早先吃的那份薯片", SNACK_NAME, outcome="吃掉", memo="起床后记得吃", remind_time="07:30")
    model = _ScriptedModel([
        _call("call-note", "report_status", lambda request: {"items": [{
            "activity_name": _activity(request, "进行中"), "summary": "再等一会儿就去睡。"}], "errands": [snack]}),
        _after_midnight])
    person = await _person(environment, tmp_path / "agent_0001", model)
    _, registered, result = await _decide(environment, person, "note")
    assert (registered, result.status.value, result.data["errands"]) == (
        "report_status registered", "completed", _receipt([snack]))
    assert 1 not in engine.scheduling_state().ready_agent_ids
    midnight = moment(engine, "2026-01-06 00:00")
    run_until(engine, lambda current: current.context.state.current_time >= midnight
              and bool(current.scheduling_state().ready_agent_ids))

    decision, registered, _ = await _decide(environment, person, "after-midnight")

    [requested] = [event for event in engine.context.store.read_agent_events("model_request", 1)
                   if event.decision_id == decision.decision_id]
    responses = [event for event in engine.context.store.read_agent_events("model_response", 1)
                 if event.decision_id == decision.decision_id]
    messages = requested.data["request"]["messages"]
    assert [message["role"] for message in messages] == ["system", "user"]
    layers = json.loads(messages[1]["content"])
    assert layers[4]["observed_at"].startswith("2026-01-06")
    assert layers[3]["errands"] == [{"name": "明早先吃的那份薯片", "done_when": f"吃掉{SNACK_NAME} 1 份",
                                     "progress": "已吃掉 0 份", "memo": "起床后记得吃", "remind_time": "07:30",
                                     "noted_on": "2026-01-05"}]
    offered = [tool["function"]["name"] for tool in requested.data["request"]["tools"]]
    assert {"memory_recent", "memory_search", "memory_time_range"} <= set(offered)
    called = [call["function"]["name"] for event in responses
              for call in event.data["response"]["choices"][0]["message"]["tool_calls"]]
    assert called == [registered.removesuffix(" registered")] and len(responses) == 1
    assert len(model.requests) == 2

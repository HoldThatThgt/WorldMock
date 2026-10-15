"""Life 领域测试共享的真实运行工厂。"""

from __future__ import annotations

import json
import os
import shutil
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")


if TYPE_CHECKING:
    from agentsociety2.society import DecisionContext
    from worldmock.life.engine import LifeEngine







def context(engine: LifeEngine, round_id: str, agent_id: int = 1) -> DecisionContext:
    """形成指向当前已提交世界的宿主上下文。"""
    from agentsociety2.society import DecisionContext

    observed = datetime.fromtimestamp(engine.context.state.current_time, ZoneInfo(engine.context.config.timezone))
    return DecisionContext(engine.context.state.run_id, round_id, f"decision-{round_id}-{agent_id}",
                           agent_id, observed, engine.context.state.version)


def stage_day_plan(engine: LifeEngine, agent_id: int = 1, goals: tuple = (), submitted: bool = False) -> None:
    """预置当日计划（只置 day_plan）并保持存储版本连续；submitted 时同时写入当日日期（零点计划的幂等键）。

    用于经 LifeEnv 驱动的测试之前时传 submitted=True，零点计划后台任务就不再为该人物生成计划。
    """
    from worldmock.footprints.models import EventDraft
    from worldmock.life.day_planning import plan_date
    from worldmock.life.schedule import DayPlan, day_end_seconds

    now = engine.context.state.current_time
    day_end = day_end_seconds(now, engine.context.config.timezone)
    staged = engine.context.state.model_copy(deep=True)
    agent = staged.agents[agent_id]
    dates = set(agent.plan_submitted_dates) | ({plan_date(now, engine.context.config.timezone)} if submitted else set())
    staged.agents[agent_id] = agent.model_copy(
        update={"day_plan": DayPlan(day_end=day_end, goals=tuple(goals)), "plan_submitted_dates": dates}, deep=True)
    staged.version += 1
    engine.context.store.commit(engine.context.state.model_dump(mode="json"), staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time,
        visibility="system", data={"kind": "test_day_plan_staged"})])
    engine.context.state = staged


def stage_errands(engine: LifeEngine, agent_id: int, errands=()) -> None:
    """预置人物的未完成个人事务（只置 AgentState.errands，键为事务身份，替换原有的事务）并保持存储版本连续：用于需要
    指定记下时刻、累计量与提醒状态的用例。状态副本与一条系统可见的测试事件同一提交落库。"""
    from worldmock.footprints.models import EventDraft

    staged = engine.context.state.model_copy(deep=True)
    staged.agents[agent_id] = staged.agents[agent_id].model_copy(
        update={"errands": {item.errand_id: item for item in errands}}, deep=True)
    staged.version += 1
    engine.context.store.commit(engine.context.state.model_dump(mode="json"), staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time,
        visibility="system", data={"kind": "test_errands_staged"})])
    engine.context.state = staged


def stage_legacy_control_request(engine: LifeEngine, tool: str, agent_id: int = 1) -> str:
    """经 FootprintStore 补登一条旧形状的控制请求及其结果（世界结构 8 的写法：带 items 而缺请求级 errands）：照该人物最近
    一次 tool 请求的逐项内容，同一决策、动作身份另起，请求摘要按同一算法对旧形状重算，结果照原结果；世界状态不变、版本
    加一。引擎自己写不出这种记录，只用于核对各读取方对旧记录的拒绝（docs/design/04-simulation-engine.md 、2.9）。
    返回补登的动作身份。"""
    import hashlib

    from worldmock.footprints.models import EventDraft

    requested = [event for event in engine.context.store.read_agent_events("action_requested", agent_id)
                 if event.data["tool_name"] == tool][-1]
    result = next(event for event in engine.context.store.read_agent_events("action_result", agent_id)
                  if event.action_id == requested.action_id)
    request = {"items": requested.data["request"]["items"]}
    content = json.dumps({"tool_name": tool, "request": request}, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode()
    action_id = f"{requested.action_id}-legacy"
    data = {**requested.data, "request": request, "request_sha256": hashlib.sha256(content).hexdigest()}
    staged = engine.context.state.model_copy(deep=True)
    staged.version += 1
    shared = {"occurred_at": staged.current_time, "agent_id": agent_id, "decision_id": requested.decision_id,
              "action_id": action_id}
    engine.context.store.commit(engine.context.state.model_dump(mode="json"), staged.model_dump(mode="json"), [
        EventDraft(event_type="action_requested", source_kind="model", data=data, **shared),
        EventDraft(event_type="action_result", data={**result.data, "action_id": action_id}, **shared)])
    engine.context.state = staged
    return action_id


def errand_add(name: str, item: str, quantity: int = 1, memo=None, remind_time=None) -> dict:
    """一条七键「新增」事务变更：按真实收货商品与份数完成，原因为 null。"""
    return {"change": "新增", "name": name, "item": item, "quantity": quantity, "memo": memo,
            "remind_time": remind_time, "reason": None}


def errand_update(name: str, memo=None, remind_time=None) -> dict:
    """一条「更新」事务变更：按待办层名称点名，只替换备注与提醒钟点（null 即清空），完成依据与原因为 null。"""
    return {"change": "更新", "name": name, "item": None, "quantity": None, "memo": memo,
            "remind_time": remind_time, "reason": None}


def errand_cancel(name: str, reason: str) -> dict:
    """一条「取消」事务变更：按待办层名称点名并写原因，其余键为 null。"""
    return {"change": "取消", "name": name, "item": None, "quantity": None, "memo": None,
            "remind_time": None, "reason": reason}


def bring_to_window(engine: LifeEngine, object_ids, agent_id: int = 1) -> None:
    """让给定商品进入下一次观察的呈现窗口：其余门店商品都记为此刻刚呈现过，
    给定对象从未呈现——轮转按每个对象的上次呈现时刻把换新名额留给该分区最久未呈现的对象
    （docs/design/04-simulation-engine.md ），每个分区至多三个。经存储落盘并保持版本连续。"""
    from worldmock.footprints.models import EventDraft

    wanted = set(object_ids)
    catalog = engine.context.catalog
    everything = set(catalog.goods)
    staged = engine.context.state.model_copy(deep=True)
    staged.presentation_state = {**staged.presentation_state,
                                 agent_id: {item: staged.current_time for item in sorted(everything - wanted)}}
    staged.version += 1
    engine.context.store.commit(engine.context.state.model_dump(mode="json"), staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        data={"kind": "test_brought_to_window"})])
    engine.context.state = staged


def plan_day_response(goals: list[dict] | None = None, arguments: str | None = None, model="test-model") -> dict:
    """零点计划模型的确定性原始响应：唯一 submit_day_plan 调用（arguments 覆盖时原样作为参数文本）。"""
    import json

    text = arguments if arguments is not None else json.dumps({"goals": goals or []}, ensure_ascii=False)
    return {"model": model,
            "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call-plan-day", "type": "function",
                 "function": {"name": "submit_day_plan", "arguments": text}}]}}],
            "usage": {"prompt_tokens": 30, "completion_tokens": 9, "total_tokens": 39}}


class StubPlanDayClient:
    """确定性零点计划客户端桩（经后台任务的 client 参数注入，同联系人回复）：按脚本依次返回响应并记录请求。

    脚本用尽后按 goals（缺省为空清单）返回合法计划；raises 非空时下一次调用抛出该异常（模拟传输失败）。
    """

    model_name = "test-model"

    def __init__(self, model_identity: dict | None = None, goals: list[dict] | None = None,
                 responses: list[dict] | None = None, raises: BaseException | None = None):
        self.model_name = (model_identity or {}).get("requested_model", "test-model")
        self.calls: list[dict] = []
        self.goals = goals or []
        self.responses = list(responses or [])
        self.raises = raises

    async def call(self, request: dict) -> dict:
        self.calls.append(request)
        if self.raises is not None:
            raise self.raises
        return self.responses.pop(0) if self.responses else plan_day_response(self.goals, model=self.model_name)


def drive(coroutine):
    """不经事件循环驱动只含立即完成的 await 的协程（确定性客户端桩），同步与异步用例都能调用。"""
    try:
        coroutine.send(None)
    except StopIteration as stop:
        return stop.value
    coroutine.close()
    raise AssertionError("coroutine suspended on a real await")


def settle_day_plans(engine: LifeEngine, client=None) -> list[dict]:
    """执行零点计划后台任务（确定性桩），零点边界随后执行零点节律入口；返回登记摘要。"""
    from worldmock.runtime import settle_pending_day_plans

    registered = drive(settle_pending_day_plans(engine, client=client or StubPlanDayClient({"requested_model": engine.context.config.model.requested_model})))
    if engine.midnight_rhythm_pending:
        engine.decide_midnight_rhythm()
    return registered


def travel_quote(engine: LifeEngine, mode: str, base_seconds: int) -> int:
    """当前出发时刻的出行受理口径计划时长（travel-time 分时系数折算）。"""
    return engine.context.semantics.travel_planned_seconds(
        mode, base_seconds, engine.context.state.current_time)


def submit_round(engine: LifeEngine, requests: dict[int, tuple[str, object]], round_id: str):
    """按全部就绪人物创建并提交一个真实决策轮。"""
    from agentsociety2.society import DirectToolCall

    ready = engine.scheduling_state().ready_agent_ids
    contexts = [context(engine, round_id, agent_id) for agent_id in ready]
    engine.begin_decision_round(contexts)
    for item in contexts:
        engine.observe(item)
    receipts = []
    for item in reversed(contexts):
        tool_name, request = requests[item.agent_id]
        action_id = f"action-{round_id}-{item.agent_id}"
        receipts.append(engine.submit_action(DirectToolCall(item, action_id, tool_name, request)))
    return receipts, engine.commit_decision_round(round_id)


#: 两个开始工具：start_main_activity 开始占槽位 1 的活动，start_side_activity 开始占槽位 2 的活动。
MAIN_TOOL, SIDE_TOOL = "start_main_activity", "start_side_activity"
#: 夹具人设每日睡眠需要 7.5 小时折成的分钟数。
NIGHT_SLEEP_MINUTES = 450


def advance_next(engine: LifeEngine) -> None:
    """推进到环境报告的下一个严格未来边界；零点边界照 LifeEnv.step 先生成当日计划（确定性空清单桩）再判定节律。"""
    state = engine.scheduling_state()
    assert state.next_event_at is not None
    engine.advance(int(state.next_event_at.timestamp()) - engine.context.state.current_time, state.next_event_at)
    if engine.midnight_rhythm_pending:
        settle_day_plans(engine)


def advance_until_ready(engine: LifeEngine, limit: int = 64) -> bool:
    """推进穿过静默结算边界，直到某个人物获得决策机会（修订决策节律）。

    静默边界不调用模型、不产生决策事件；唤醒条件（计划结束、体感分级
    变化、重估间隔等）命中的边界才使人物就绪。返回是否在限次内就绪。
    """
    for _ in range(limit):
        state = engine.scheduling_state()
        if state.ready_agent_ids:
            return True
        if state.next_event_at is None:
            return False  # 运行已到终点，不会再有决策机会
        advance_next(engine)
    return False


def main_arguments(action: str, target: str, detail: str | None = None) -> dict:
    """@brief start_main_activity 的完整参数：三个键都出现，睡眠的 detail 为 null。"""
    return {"action": action, "target": target, "detail": detail}


def side_arguments(action: str, *targets: str, prefer: str | None = None) -> dict:
    """@brief start_side_activity 的完整参数：三个键都出现，target 始终是名称数组，prefer 缺省为 null。"""
    return {"action": action, "target": list(targets), "prefer": prefer}


def travel(target: str, method: str = "步行"):
    """@brief 出行请求：目的地地点名与出行方式名称（步行、驾车、打车、公共交通）。"""
    from worldmock.life.models import StartMainActivityRequest

    return StartMainActivityRequest.model_validate(main_arguments("travel", target, method))


def do_at(location: str, unit: str):
    """@brief 在当前地点按名称做一件事：当前地点名与内容单元名。"""
    from worldmock.life.models import StartMainActivityRequest

    return StartMainActivityRequest.model_validate(main_arguments("interact", location, unit))


def sleep(minutes: int | str = NIGHT_SLEEP_MINUTES):
    """@brief 睡眠请求：分钟数写成数字文字，detail 为 null。"""
    from worldmock.life.models import StartMainActivityRequest

    return StartMainActivityRequest.model_validate(main_arguments("sleep", str(minutes)))


def purchase(*goods: str, prefer: str | None = None):
    """@brief 购买请求：一份一项，同一个名称写两次就是两份；prefer 是想用来下单的 app 名称原文，缺省为 null。"""
    from worldmock.life.models import StartSideActivityRequest

    return StartSideActivityRequest.model_validate(side_arguments("purchase", *goods, prefer=prefer))


def use_app(app: str):
    """@brief 使用一个内容 app 的请求：prefer 为 null。"""
    from worldmock.life.models import StartSideActivityRequest

    return StartSideActivityRequest.model_validate(side_arguments("interact", app))


def query(*targets: str, prefer: str | None = None):
    """@brief 查询请求：商品的完整名称或商品类别名，可以混写；prefer 是想在其中查询的 app 名称原文，缺省为 null。"""
    from worldmock.life.models import StartSideActivityRequest

    return StartSideActivityRequest.model_validate(side_arguments("query", *targets, prefer=prefer))


def tool_of(request) -> str:
    """@brief 请求模型对应的世界请求工具名。"""
    from worldmock.life.models import REQUEST_MODELS

    return next(name for name, model in REQUEST_MODELS.items() if type(request) is model)


def activity_name(engine: LifeEngine, activity_id: str) -> str:
    """活动的可读名称「做什么＋对象（M月D日 HH:MM 起）」；控制工具每项的 activity_name 按它点名活动。

    同一轮可见的名称重复时观察另加「（第二次）」这类序号，这类用例改从观察的 activity_bindings 取名。
    """
    return engine.context.state.activities[activity_id].name


def _summaries(names: tuple[str, ...], summary) -> list[str]:
    """逐项自述：一句话用于全部点名活动，列表则逐项对应。"""
    return list(summary) if isinstance(summary, (list, tuple)) else [summary for _ in names]


def report_request(*names: str, summary="继续。", errands=()):
    """播报所点名活动的请求（点名暂停的活动即恢复它）：一项一个对象 {activity_name, summary}；请求级 errands 是个人事务
    变更（errand_add、errand_update、errand_cancel 构造的八键字典），缺省为空列表。"""
    from worldmock.life.models import ReportStatusRequest

    return ReportStatusRequest(items=[{"activity_name": name, "summary": text}
                                      for name, text in zip(names, _summaries(names, summary), strict=True)],
                               errands=list(errands))


def pause_request(*names: str, summary="先停一下。", errands=()):
    """暂停所点名活动的请求：一项一个对象 {activity_name, summary}；请求级 errands 缺省为空列表。"""
    from worldmock.life.models import PauseActivityRequest

    return PauseActivityRequest(items=[{"activity_name": name, "summary": text}
                                       for name, text in zip(names, _summaries(names, summary), strict=True)],
                                errands=list(errands))


def conclude_request(*names: str, summary="收尾。", memories=None, views=None, errands=()):
    """结束所点名活动的请求：一项一个对象 {activity_name, summary, memories, views}；memories 与 views 逐项给出
    （与 names 等长的列表的列表），默认都不写；请求级 errands 缺省为空列表。"""
    from worldmock.life.models import ConcludeActivityRequest

    memories = memories if memories is not None else [[] for _ in names]
    views = views if views is not None else [[] for _ in names]
    return ConcludeActivityRequest(items=[
        {"activity_name": name, "summary": text, "memories": list(group), "views": list(seen)}
        for name, text, group, seen in zip(names, _summaries(names, summary), memories, views, strict=True)],
        errands=list(errands))


def pending_names(observed: dict) -> list[str]:
    """本轮观察中刚结束、待收尾的活动名（私有绑定的阶段为「刚结束」）。"""
    from worldmock.life.activity_control import PHASE_PENDING

    return [row["name"] for row in observed["activity_bindings"] if row["phase"] == PHASE_PENDING]


def conclude_pending(engine: LifeEngine, round_id: str, agent_id: int = 1):
    """单个就绪人物当秒给全部待收尾活动收尾（第七章「活动一结束就要结束它」），返回本轮动作结果。"""
    from agentsociety2.society import DirectToolCall

    decision = context(engine, round_id, agent_id)
    engine.begin_decision_round([decision])
    names = pending_names(engine.observe(decision))
    assert names, "no activity is awaiting conclusion"
    engine.submit_action(DirectToolCall(decision, f"action-{round_id}", "conclude_activity",
                                        conclude_request(*names)))
    return engine.commit_decision_round(round_id)[0]


def tool_targets(observed: dict) -> dict[str, dict]:
    """观察工具层的目标，按名称索引（名称全局唯一）。"""
    return {row["name"]: row for row in observed["tool_layer"]["targets"]}


def target_actions(observed: dict, name: str) -> dict[str, dict]:
    """工具层某个目标的动作，按动作索引；同一动作有多行（出行各方式、地点各单元）时取第一行。"""
    rows: dict[str, dict] = {}
    for row in tool_targets(observed)[name]["actions"]:
        rows.setdefault(row["action"], row)
    return rows


def travel_rows(observed: dict, name: str) -> dict[str, dict]:
    """工具层某个目的地下的出行动作行，按出行方式名称索引。"""
    return {row["detail"]: row for row in action_rows(observed, name, "travel")}


def target_tools(observed: dict, name: str) -> dict[str, dict]:
    """工具层某个目标（或待办层某项暂停活动）以其名称为参数的工具，按工具名索引。"""
    targets = tool_targets(observed)
    rows = (targets[name]["tools"] if name in targets
            else next(item["tools"] for item in observed["todo_layer"]["paused_activities"] if item["name"] == name))
    return {row["tool"]: row for row in rows}


def action_rows(observed: dict, name: str, action: str) -> list[dict]:
    """@brief 工具层某个目标下某个动作的全部动作行（出行按方式、在地点做事按单元各一行）。"""
    return [row for row in tool_targets(observed)[name]["actions"] if row["action"] == action]


def layer_start(observed: dict, name: str, action: str, detail: str | None = None):
    """@brief 从观察中的实际动作行构造开始请求：出行与在地点做事按 detail 取行，缺省取该动作的第一行。"""
    rows = action_rows(observed, name, action)
    assert rows, (name, action)
    row = next(item for item in rows if detail is None or item["detail"] == detail)
    kind = tool_targets(observed)[name]["kind"]
    if kind == "location":
        return sleep() if action == "sleep" else (travel if action == "travel" else do_at)(name, row["detail"])
    return purchase(name) if action == "purchase" else use_app(name)


def progress_calls(observed: dict, tool: str | None = None) -> list[dict]:
    """观察进度层的 tool_call 记录（可按工具名筛选）。"""
    return [row for row in observed["progress_layer"]["events"]
            if row["type"] == "tool_call" and (tool is None or row["tool"] == tool)]


def last_result(observed: dict) -> dict:
    """观察进度层最后一次世界请求（tool_call）的公开结果。"""
    return progress_calls(observed)[-1]["result"]

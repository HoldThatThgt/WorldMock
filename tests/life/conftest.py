"""Life 领域测试共享的真实运行工厂。"""

from __future__ import annotations

import asyncio
import json
import shutil
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest


if TYPE_CHECKING:
    from worldmock.simulation_engine.engine import LifeEngine
    from worldmock.state.decisions.barrier import AgentWakeupInput


def wakeup_for(engine: LifeEngine, agent_id: int = 1) -> AgentWakeupInput:
    """从引擎当前就绪状态准备单个人物唤醒输入（不写事件）。"""
    [wakeup] = engine.prepare_barrier((agent_id,))
    return wakeup


def action_identity(label: str, number: int = 1, agent_id: int = 1) -> str:
    """@brief 测试唤醒 wakeup-<标签>-<人物> 的第 number 条动作身份，形式与 LifeAgent 相同。"""
    return f"wakeup-{label}-{agent_id}:action:{number:04d}"


def oversleep_identity(engine: LifeEngine, label: str, seconds: int | None = None) -> str:
    """@brief 标签为 label 的决策里找一条会抽中多睡的动作身份（给了秒数时要求恰好多睡这么久）：此前睡得不足时，用它开始的
    睡眠一定睡过头。"""
    from worldmock.simulation_engine.body.sleep_plan import oversleep_seconds

    state = engine.context.state.random_state
    return next(action_identity(label, number) for number in range(1, 10000)
                if (extra := oversleep_seconds(0, state, action_identity(label, number))) and seconds in (None, extra))


def stage_day_plan(engine: LifeEngine, agent_id: int = 1, goals: tuple = (), submitted: bool = False) -> None:
    """预置当日计划（只置 day_plan）并保持存储版本连续；submitted 时同时写入当日日期（零点计划的幂等键）。

    用于经运行宿主驱动的测试之前时传 submitted=True，零点计划任务就不再为该人物生成计划。
    """
    from worldmock.recording_engine.events.models import EventDraft
    from worldmock.simulation_engine.scheduling.plans.day_planning import plan_date
    from worldmock.state.planning.schedule import DayPlan, day_end_seconds

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
    from worldmock.recording_engine.events.models import EventDraft

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

    from worldmock.recording_engine.events.models import EventDraft

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
    shared = {"occurred_at": staged.current_time, "agent_id": agent_id, "wakeup_id": requested.wakeup_id,
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


def stub_day_session(wakeup: AgentWakeupInput):
    """@brief 为直接调用沙盒 finish_wakeup 的测试构造最小合法 DaySessionState。"""
    from worldmock.agents.life_agent import life_tool_schema_sha256
    from worldmock.agents.prompt.life_prompt import system_message
    from worldmock.state.decisions.day_session import DaySessionState

    if wakeup.day_session is not None:
        return wakeup.day_session
    sys_msg = system_message()
    return DaySessionState(
        local_date=wakeup.local_date,
        system_prompt=sys_msg["content"],
        tools_digest=life_tool_schema_sha256(),
        messages=(sys_msg, {"role": "user", "content": "[]"}),
    )


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


def memory_digest_response(arguments: dict | str, finish_reason: str = "tool_calls", model: str = "test-model") -> dict:
    """回顾模型的确定性原始响应：唯一 submit_memory_digest 调用（arguments 为字符串时原样作为参数文本）。"""
    text = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
    return {"model": model,
            "choices": [{"finish_reason": finish_reason, "message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call-memory-digest", "type": "function",
                 "function": {"name": "submit_memory_digest", "arguments": text}}]}}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 12, "total_tokens": 52}}


#: 回顾用户消息的中文类别键与工具参数的英文键。
_DIGEST_KEYS = (("饮食", "food"), ("工作", "work"), ("生活", "life"))


def fixture_goods() -> tuple[str, ...]:
    """测试环境 tests/fixtures/dataset-environment 目录里全部商品的名称（不按长度过滤），按目录行序。"""
    from tests.data_paths import CATALOG_ROOT

    rows = (json.loads(line) for line in (CATALOG_ROOT / "merchant-catalog.jsonl").read_text(encoding="utf-8").splitlines())
    return tuple(row["name"] for row in rows if "goods_id" in row)


class StubMemoryDigestClient:
    """确定性回顾客户端桩（经后台任务的 client 参数注入）：按脚本依次返回响应并记录请求。

    脚本用尽后按请求的用户消息写一份能通过全部守卫的回答：有「人物卡片」键是日回顾，否则是月、年回顾；每个有输入的类写一段，
    refs 是这一类全部编号，无输入的类为 null。日回顾的饮食类列出夹具目录商品名称中作子串出现在饮食核验里的（按首次出现的
    位置、去重），没有时写「这一天吃喝照常。」，工作类、生活类写「这一天这一类的事照常。」；月、年回顾写「这段时间的回顾已经
    整理。」。这样写出的段落非空、refs 都是这一类的编号，在夹具的条目量下不超过字数上限。raises 非空时下一次调用抛出该异常
    （模拟传输失败）。
    """

    model_name = "test-model"

    def __init__(self, model_identity: dict | None = None, responses: list[dict] | None = None,
                 raises: BaseException | None = None):
        self.model_name = (model_identity or {}).get("requested_model", "test-model")
        self.calls: list[dict] = []
        self.responses = list(responses or [])
        self.raises = raises
        self.goods = fixture_goods()

    async def call(self, request: dict) -> dict:
        self.calls.append(request)
        if self.raises is not None:
            raise self.raises
        if self.responses:
            return self.responses.pop(0)
        return memory_digest_response(self.answer(json.loads(request["messages"][1]["content"])),
                                      model=self.model_name)

    def answer(self, payload: dict) -> dict:
        """按一份用户消息写出的合格参数。"""
        day = "人物卡片" in payload
        arguments = {}
        for name, key in _DIGEST_KEYS:
            rows = payload[name] if day else next(value for label, value in payload[name].items() if label != "次数统计")
            if not rows:
                arguments[key] = None
                continue
            text = self._food_text(rows) if day and key == "food" else (
                "这一天这一类的事照常。" if day else "这段时间的回顾已经整理。")
            arguments[key] = {"text": text, "refs": [row["编号"] for row in rows]}
        return arguments

    def _food_text(self, rows: list[dict]) -> str:
        """日回顾饮食类的一段：饮食核验里出现的商品名称按首次出现的位置排列。"""
        verified = "\n".join(line for row in rows for line in row.get("核验", ()))
        named = sorted((name for name in dict.fromkeys(self.goods) if name in verified), key=verified.find)
        return "收到和用到了" + "、".join(named) + "。" if named else "这一天吃喝照常。"


def drive(coroutine):
    """用独立事件循环同步驱动确定性协程（仅用于同步测试用例）。"""
    return asyncio.run(coroutine)


def _midnight_registry(engine: LifeEngine, plan_client=None, digest_client=None):
    """构建零点阶段复用注册表桩与计划/回顾客户端。"""
    from worldmock.configuration.model.model_registry import ResolvedModelBinding

    cfg = engine.context.config
    routing = cfg.model_routing

    def _binding(stage: str, client):
        pid = getattr(routing, stage)
        prof = cfg.models[pid]
        meta = engine.context.inputs.model_metadata[pid]
        eff = min(prof.max_output_tokens, meta.max_output_tokens)
        return ResolvedModelBinding(pid, prof, meta, client, eff)

    model = {"requested_model": cfg.models[routing.plan_day].requested_model}
    p_cli = plan_client or StubPlanDayClient(model)
    d_cli = digest_client or StubMemoryDigestClient({"requested_model": cfg.models[routing.digest].requested_model})
    return SimpleNamespace(resolve=lambda stage, aid: _binding(stage, p_cli if stage == "plan_day" else d_cli))


async def settle_midnight_tasks_async(engine: LifeEngine, plan_client=None, digest_client=None) -> list[dict]:
    """在异步用例中依次执行零点后台任务（先回顾，再零点计划，再零点节律）。"""
    from worldmock.runtime.lifecycle.midnight import settle_pending_day_plans, settle_pending_memory_digests

    registry = _midnight_registry(engine, plan_client, digest_client)
    await settle_pending_memory_digests(engine, registry)
    registered = await settle_pending_day_plans(engine, registry)
    if engine.midnight_rhythm_pending:
        engine.decide_midnight_rhythm()
    return registered


def settle_midnight_tasks(engine: LifeEngine, plan_client=None, digest_client=None) -> list[dict]:
    """依次执行零点的后台任务（缺省都用确定性桩）：先回顾，再零点计划，零点边界随后执行零点节律入口；返回零点计划的
    登记摘要。"""
    return drive(settle_midnight_tasks_async(engine, plan_client, digest_client))


def travel_quote(engine: LifeEngine, mode: str, base_seconds: int) -> int:
    """当前出发时刻的出行受理口径计划时长（travel-time 分时系数折算）。"""
    return engine.context.semantics.travel_planned_seconds(
        mode, base_seconds, engine.context.state.current_time)


def submit_barrier(engine: LifeEngine, requests: dict[int, tuple[str, object]], label: str = "barrier"):
    """按全部就绪人物创建并提交一个真实全局屏障唤醒。"""
    from worldmock.state.decisions.suspended import SuspendedLoopState

    ready = engine.scheduling_state().ready_agent_ids
    wakeups = engine.prepare_barrier(ready)
    outcomes = []
    action_ids = []
    for wakeup in wakeups:
        tool_name, request = requests[wakeup.agent_id]
        sandbox = engine.open_sandbox(wakeup)
        action_id = f"{wakeup.wakeup_id}:action:0001"
        action_ids.append(action_id)
        res = sandbox.dispatch_world_tool(tool_name, request, action_id)
        suspended = None
        if res.suspended:
            suspended = SuspendedLoopState(
                wakeup_id=wakeup.wakeup_id, action_id=action_id,
                activity_id=res.activity_id, pending_tool_call_id="call-1",
                turn_used=1, memory_search_count=0, accepted_action_ids=(action_id,),
                pending_memories=(), verified_experiences=(), local_rejection_counts={},
                observation_event_id=wakeup.observation["observation_event_id"],
                observation_parts=dict(wakeup.observation_parts), suspended_at=wakeup.observed_at,
            )
        outcomes.append(sandbox.finish_wakeup(suspended, stub_day_session(wakeup)))
    receipt = engine.commit_barrier(outcomes)
    return receipt, action_ids


#: 两个开始工具：start_main_activity 开始占槽位 1 的活动，start_side_activity 开始占槽位 2 的活动。
MAIN_TOOL, SIDE_TOOL = "start_main_activity", "start_side_activity"
#: 夹具人设每日睡眠需要 7.5 小时折成的分钟数。
NIGHT_SLEEP_MINUTES = 450


def advance_next(engine: LifeEngine) -> None:
    """推进到环境报告的下一个严格未来边界；零点边界照运行宿主的零点推进先生成回顾（确定性桩），再生成当日计划（确定性空清单桩），
    最后判定节律。"""
    state = engine.scheduling_state()
    assert state.next_event_at is not None
    engine.advance(int(state.next_event_at.timestamp()) - engine.context.state.current_time, state.next_event_at)
    if engine.midnight_rhythm_pending:
        settle_midnight_tasks(engine)


async def advance_next_async(engine: LifeEngine) -> None:
    """@brief 在异步宿主协程内推进到下一未来边界并在零点边界直接 await 确定性零点任务。"""
    state = engine.scheduling_state()
    assert state.next_event_at is not None
    engine.advance(int(state.next_event_at.timestamp()) - engine.context.state.current_time, state.next_event_at)
    if engine.midnight_rhythm_pending:
        await settle_midnight_tasks_async(engine)


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
    from worldmock.state.interaction.requests import StartMainActivityRequest

    return StartMainActivityRequest.model_validate(main_arguments("travel", target, method))


def do_at(location: str, unit: str):
    """@brief 在当前地点按名称做一件事：当前地点名与内容单元名。"""
    from worldmock.state.interaction.requests import StartMainActivityRequest

    return StartMainActivityRequest.model_validate(main_arguments("interact", location, unit))


def sleep(minutes: int | str = NIGHT_SLEEP_MINUTES):
    """@brief 睡眠请求：分钟数写成数字文字，detail 为 null。"""
    from worldmock.state.interaction.requests import StartMainActivityRequest

    return StartMainActivityRequest.model_validate(main_arguments("sleep", str(minutes)))


def purchase(*goods: str, prefer: str | None = None):
    """@brief 购买请求：一份一项，同一个名称写两次就是两份；prefer 是想用来下单的 app 名称原文，缺省为 null。"""
    from worldmock.state.interaction.requests import StartSideActivityRequest

    return StartSideActivityRequest.model_validate(side_arguments("purchase", *goods, prefer=prefer))


def use_app(app: str):
    """@brief 使用一个内容 app 的请求：prefer 为 null。"""
    from worldmock.state.interaction.requests import StartSideActivityRequest

    return StartSideActivityRequest.model_validate(side_arguments("interact", app))


SEARCH_TOOL = "search_world"


def search_world(query_text: str, prefer: str | None = None):
    """@brief 原生搜索工具请求：想找的商品、预算或地点说明；prefer 是意向 app 完整名称，缺省为 null。"""
    from worldmock.state.interaction.requests import SearchWorldRequest

    return SearchWorldRequest(query=query_text, prefer=prefer)


def continue_request(reason: str | None = None):
    """@brief 维持当前进行中活动的边界退出请求；reason 可为 null 或 1..200 字符的说明。"""
    from worldmock.state.interaction.requests import ContinueActivityRequest

    return ContinueActivityRequest(reason=reason)


def tool_of(request) -> str:
    """@brief 请求模型对应的世界请求工具名。"""
    from worldmock.state.interaction.requests import REQUEST_MODELS

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
    from worldmock.state.interaction.requests import ReportStatusRequest

    return ReportStatusRequest(items=[{"activity_name": name, "summary": text}
                                      for name, text in zip(names, _summaries(names, summary), strict=True)],
                               errands=list(errands))


def pause_request(*names: str, summary="先停一下。", errands=()):
    """暂停所点名活动的请求：一项一个对象 {activity_name, summary}；请求级 errands 缺省为空列表。"""
    from worldmock.state.interaction.requests import PauseActivityRequest

    return PauseActivityRequest(items=[{"activity_name": name, "summary": text}
                                       for name, text in zip(names, _summaries(names, summary), strict=True)],
                                errands=list(errands))


def conclude_request(*names: str, summary="收尾。", memories=None, views=None, errands=()):
    """结束所点名活动的请求：一项一个对象 {activity_name, summary, memories, views}；memories 与 views 逐项给出
    （与 names 等长的列表的列表），默认都不写；请求级 errands 缺省为空列表。"""
    from worldmock.state.interaction.requests import ConcludeActivityRequest

    memories = memories if memories is not None else [[] for _ in names]
    views = views if views is not None else [[] for _ in names]
    return ConcludeActivityRequest(items=[
        {"activity_name": name, "summary": text, "memories": list(group), "views": list(seen)}
        for name, text, group, seen in zip(names, _summaries(names, summary), memories, views, strict=True)],
        errands=list(errands))


def pending_names(observed: dict) -> list[str]:
    """本轮观察中刚结束、待收尾的活动名（私有绑定的阶段为「刚结束」）。"""
    from worldmock.simulation_engine.activities.lifecycle.activity_control import PHASE_PENDING

    return [row["name"] for row in observed["activity_bindings"] if row["phase"] == PHASE_PENDING]


def conclude_pending(engine: LifeEngine, label: str = "conclude", agent_id: int = 1):
    """单个就绪人物当秒给全部待收尾活动收尾，返回本动作提交结果。"""
    [wakeup] = engine.prepare_barrier((agent_id,))
    sandbox = engine.open_sandbox(wakeup)
    names = pending_names(wakeup.observation)
    assert names, "no activity is awaiting conclusion"
    action_id = f"{wakeup.wakeup_id}:action:0001"
    sandbox.dispatch_world_tool("conclude_activity", conclude_request(*names), action_id)
    outcome = sandbox.finish_wakeup(None, stub_day_session(wakeup))
    engine.commit_barrier([outcome])
    row = next(event for event in engine.context.store.iter_events(engine.context.store.last_event_seq)
               if event.event_type == "action_result" and event.action_id == action_id)
    from worldmock.state.decisions.results import ActionResult

    return ActionResult.model_validate({key: row.data[key] for key in ActionResult.model_fields if key in row.data})


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

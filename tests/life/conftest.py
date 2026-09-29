"""Life 领域测试共享的真实运行工厂。"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")

from agentsociety2.society import DecisionContext, DirectToolCall  # noqa: E402

from worldmock.life.engine import LifeEngine  # noqa: E402
from worldmock.life.models import ModelIdentity, RunIdentity  # noqa: E402
from worldmock.life.scenario import load_scenario  # noqa: E402


@pytest.fixture
def engine_factory(tmp_path: Path):
    """建立绑定正式内容但隔离存储的 LifeEngine；给出 mutate(document) 时先改写场景文档的副本。"""
    created = []

    def create(scenario_name: str = "white-collar-12-weeks.json", mutate=None) -> LifeEngine:
        scenario_path = Path("scenarios") / scenario_name
        if mutate is not None:
            scenario_path = _mutated_scenario(scenario_path, mutate, tmp_path / f"scenario-{len(created)}")
        scenario, _ = load_scenario(scenario_path)
        run_id = f"test-{len(created)}-{scenario.scenario_id}"
        identity = RunIdentity(
            run_id=run_id,
            source_group_id=f"group-{run_id}",
            agent_ids=tuple(item.agent_id for item in scenario.agents),
            start_at=scenario.start_at.isoformat(),
            end_at=scenario.end_at.isoformat(),
            timezone=scenario.timezone,
            code_sha256="2" * 64,
            model_identity=ModelIdentity(
                provider="openai_compatible",
                base_url="https://api.deepseek.com/beta",
                requested_model="deepseek-flash",
                thinking="enabled",
                tool_choice="auto",
                max_retries=0,
                prompt_sha256="0" * 64,
                tool_schema_sha256="1" * 64,
            ),
        )
        engine = LifeEngine.create(scenario_path, tmp_path / f"run-{len(created)}", identity)
        created.append(engine)
        return engine

    yield create
    for engine in created:
        try:
            engine.close()
        except Exception:
            pass


def _mutated_scenario(source: Path, mutate, root: Path) -> Path:
    """把正式场景文档的副本交给 mutate(document) 改写，连同冻结目录写到 root 下（语料根改为正式语料的绝对路径）。"""
    document = json.loads(source.read_text(encoding="utf-8"))
    mutate(document)
    document["corpus_root"] = str((source.parent / document["corpus_root"]).resolve())
    (root / "scenarios").mkdir(parents=True)
    shutil.copytree(Path("catalog"), root / "catalog")
    path = root / "scenarios" / source.name
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def context(engine: LifeEngine, round_id: str, agent_id: int = 1) -> DecisionContext:
    """形成指向当前已提交世界的宿主上下文。"""
    observed = datetime.fromtimestamp(engine.state.current_time, ZoneInfo(engine.scenario.timezone))
    return DecisionContext(engine.state.run_id, round_id, f"decision-{round_id}-{agent_id}",
                           agent_id, observed, engine.state.version)


def stage_day_plan(engine: LifeEngine, agent_id: int = 1, goals: tuple = (), submitted: bool = False) -> None:
    """预置当日计划（只置 day_plan）并保持存储版本连续；submitted 时同时写入当日日期（零点计划的幂等键）。

    用于经 LifeEnv 驱动的测试之前时传 submitted=True，零点计划后台任务就不再为该人物生成计划。
    """
    from worldmock.footprints.models import EventDraft
    from worldmock.life.day_planning import plan_date
    from worldmock.life.schedule import DayPlan, day_end_seconds

    now = engine.state.current_time
    day_end = day_end_seconds(now, engine.scenario.timezone)
    staged = engine.state.model_copy(deep=True)
    agent = staged.agents[agent_id]
    dates = set(agent.plan_submitted_dates) | ({plan_date(now, engine.scenario.timezone)} if submitted else set())
    staged.agents[agent_id] = agent.model_copy(
        update={"day_plan": DayPlan(day_end=day_end, goals=tuple(goals)), "plan_submitted_dates": dates}, deep=True)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time,
        visibility="system", data={"kind": "test_day_plan_staged"})])
    engine.state = staged


def plan_day_response(goals: list[dict] | None = None, arguments: str | None = None) -> dict:
    """零点计划模型的确定性原始响应：唯一 submit_day_plan 调用（arguments 覆盖时原样作为参数文本）。"""
    import json

    text = arguments if arguments is not None else json.dumps({"goals": goals or []}, ensure_ascii=False)
    return {"model": "deepseek-flash",
            "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call-plan-day", "type": "function",
                 "function": {"name": "submit_day_plan", "arguments": text}}]}}],
            "usage": {"prompt_tokens": 30, "completion_tokens": 9, "total_tokens": 39}}


class StubPlanDayClient:
    """确定性零点计划客户端桩（经后台任务的 client 参数注入，同联系人回复）：按脚本依次返回响应并记录请求。

    脚本用尽后按 goals（缺省为空清单）返回合法计划；raises 非空时下一次调用抛出该异常（模拟传输失败）。
    """

    model_name = "deepseek-flash"

    def __init__(self, model_identity: dict | None = None, goals: list[dict] | None = None,
                 responses: list[dict] | None = None, raises: BaseException | None = None):
        self.calls: list[dict] = []
        self.goals = goals or []
        self.responses = list(responses or [])
        self.raises = raises

    async def call(self, request: dict) -> dict:
        self.calls.append(request)
        if self.raises is not None:
            raise self.raises
        return self.responses.pop(0) if self.responses else plan_day_response(self.goals)


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

    registered = drive(settle_pending_day_plans(engine, client=client or StubPlanDayClient()))
    if engine.midnight_rhythm_pending:
        engine.decide_midnight_rhythm()
    return registered


def travel_quote(engine: LifeEngine, mode: str, base_seconds: int) -> int:
    """当前出发时刻的出行受理口径计划时长（travel-time 分时系数折算）。"""
    return engine.semantics.travel_planned_seconds(
        mode, base_seconds, engine.state.current_time)


def submit_round(engine: LifeEngine, requests: dict[int, tuple[str, object]], round_id: str):
    """按全部就绪人物创建并提交一个真实决策轮。"""
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


def advance_next(engine: LifeEngine) -> None:
    """推进到环境报告的下一个严格未来边界；零点边界照 LifeEnv.step 先生成当日计划（确定性空清单桩）再判定节律。"""
    state = engine.scheduling_state()
    assert state.next_event_at is not None
    engine.advance(int(state.next_event_at.timestamp()) - engine.state.current_time, state.next_event_at)
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


def wake_by_mail(engine: LifeEngine, agent_id: int = 1, offset: int = 900) -> int:
    """在 current+offset 注入一封到达邮件，使该边界成为 message_arrival 唤醒。

    修订决策节律下静默边界不提供决策；用受控的邮件到达把决策机会钉在
    指定边界，供需要在中途边界控制活动（暂停/继续/切换）的用例使用。
    """
    from worldmock.scheduling.events import ScheduledEvent, ScheduledKind

    staged = engine.state.model_copy(deep=True)
    at = staged.current_time + offset
    message_id = engine.scenario.messages[0].message_id
    sequence = max((item.sequence for item in staged.pending_events), default=0) + 1
    staged.pending_events.append(ScheduledEvent(
        scheduled_id=f"test-wake-mail-{sequence}", kind=ScheduledKind.MAIL_ARRIVAL,
        due_at=at, sequence=sequence, agent_id=agent_id, data={"message_id": message_id}))
    staged.pending_events.sort(key=lambda item: (item.due_at, item.sequence, item.scheduled_id))
    staged.version += 1
    from worldmock.footprints.models import EventDraft
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time,
        visibility="system", data={"kind": "test_wake_mail_scheduled"})])
    engine.state = staged
    return at


def routing_defaults(actions: list[str]) -> dict:
    """@brief 缺省的来源、去向与 answering（docs/dev/SPEC-persona-prompt.md 第五章「来源与去向」）：obtain 送给自己，
    consume 从自己手边给自己用（给予要显式写人物），其余动词都为 null，不回应消息。"""
    return {"from_": ["自己" if verb == "consume" else None for verb in actions],
            "to": ["自己" if verb in {"obtain", "consume"} else None for verb in actions], "answering": None}


def start_arguments(targets, actions, ways, **fields) -> dict:
    """@brief start_activity 的模型参数（JSON 形状，来源的字段名是 from），未写的来源、去向与 answering 取缺省。"""
    defaults = routing_defaults(list(actions))
    return {"targets": list(targets), "actions": list(actions), "ways": list(ways), "from": defaults["from_"],
            "to": defaults["to"], "answering": None, **fields}


def start_request(**fields):
    """@brief 按对象、动词与方式三个数组构造启动请求，未写的来源、去向与 answering 取 routing_defaults（来源可按字段名
    from_ 或 JSON 名 from 给出）。"""
    from worldmock.life.models import StartActivityRequest

    defaults = routing_defaults(list(fields.get("actions", [])))
    if "from" in fields:
        del defaults["from_"]
    return StartActivityRequest(**{**defaults, **fields})


def start_activity(way: str, *targets: str, **fields):
    """@brief 按「动词＋对象＋方式」构造启动请求：每个对象取同一方式短语，动词由方式唯一确定（第五章）。"""
    from worldmock.life.interaction_menu import WAYS

    payload = {"targets": list(targets), "actions": [WAYS[way][0] for _ in targets], "ways": [way for _ in targets]}
    payload.update(fields)
    return start_request(**payload)


def activity_name(engine: LifeEngine, activity_id: str) -> str:
    """活动的可读名称「做什么＋对象（M月D日 HH:MM 起）」；控制工具的 task[] 按它点名活动。

    同一轮可见的名称重复时观察另加「（第二次）」这类序号，这类用例改从观察的 activity_bindings 取名。
    """
    return engine.state.activities[activity_id].name


def conclude_request(*names: str, summary: str = "收尾。", memories=None):
    """结束所点名活动的请求：task、summary 与 memories 三数组等长，默认不写记忆。"""
    from worldmock.life.models import ConcludeActivityRequest

    return ConcludeActivityRequest(task=list(names), summary=[summary for _ in names],
                                   memories=memories if memories is not None else [[] for _ in names])


def pending_names(observed: dict) -> list[str]:
    """本轮观察中刚结束、待收尾的活动名（私有绑定的阶段为「刚结束」）。"""
    from worldmock.life.activity_control import PHASE_PENDING

    return [row["name"] for row in observed["activity_bindings"] if row["phase"] == PHASE_PENDING]


def conclude_pending(engine: LifeEngine, round_id: str, agent_id: int = 1):
    """单个就绪人物当秒给全部待收尾活动收尾（第七章「活动一结束就要结束它」），返回本轮动作结果。"""
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
    """工具层某个目标的动作，按方式索引。"""
    return {row["way"]: row for row in tool_targets(observed)[name]["actions"]}


def target_tools(observed: dict, name: str) -> dict[str, dict]:
    """工具层某个目标（或待办层某项暂停活动）以其名称为参数的工具，按工具名索引。"""
    targets = tool_targets(observed)
    rows = (targets[name]["tools"] if name in targets
            else next(item["tools"] for item in observed["todo_layer"]["paused_activities"] if item["name"] == name))
    return {row["tool"]: row for row in rows}


def layer_start(observed: dict, name: str, way: str):
    """按工具层目标名与方式构造 start_activity 请求（动词取该动作的 action）。"""
    action = target_actions(observed, name)[way]
    return start_request(targets=[name], actions=[action["action"]], ways=[way])


def progress_calls(observed: dict, tool: str | None = None) -> list[dict]:
    """观察进度层的 tool_call 记录（可按工具名筛选）。"""
    return [row for row in observed["progress_layer"]["events"]
            if row["type"] == "tool_call" and (tool is None or row["tool"] == tool)]


def last_result(observed: dict) -> dict:
    """观察进度层最后一次世界请求（tool_call）的公开结果。"""
    return progress_calls(observed)[-1]["result"]

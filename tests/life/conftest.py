"""Life 领域测试共享的真实运行工厂。"""

from __future__ import annotations

import os
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
    """建立绑定正式内容但隔离存储的 LifeEngine。"""
    created = []

    def create(scenario_name: str = "white-collar-12-weeks.json") -> LifeEngine:
        scenario_path = Path("scenarios") / scenario_name
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
                base_url="https://api.deepseek.com",
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


def context(engine: LifeEngine, round_id: str, agent_id: int = 1) -> DecisionContext:
    """形成指向当前已提交世界的宿主上下文。"""
    observed = datetime.fromtimestamp(engine.state.current_time, ZoneInfo(engine.scenario.timezone))
    return DecisionContext(engine.state.run_id, round_id, f"decision-{round_id}-{agent_id}",
                           agent_id, observed, engine.state.version)


def stage_day_plan(engine: LifeEngine, agent_id: int = 1) -> None:
    """给非目标测试登记空清单，解除日首提交要求并保持存储版本连续。"""
    from worldmock.footprints.models import EventDraft
    from worldmock.life.schedule import DayPlan, day_end_seconds

    now = engine.state.current_time
    day_end = day_end_seconds(now, engine.scenario.timezone)
    staged = engine.state.model_copy(deep=True)
    staged.agents[agent_id] = staged.agents[agent_id].model_copy(
        update={"day_plan": DayPlan(day_end=day_end, goals=())}, deep=True)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time,
        visibility="system", data={"kind": "test_day_plan_staged"})])
    engine.state = staged


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
    """推进到环境报告的下一个严格未来边界。"""
    state = engine.scheduling_state()
    assert state.next_event_at is not None
    engine.advance(int(state.next_event_at.timestamp()) - engine.state.current_time, state.next_event_at)


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


def start_activity(action: str, *targets: str, **fields):
    """@brief 使用人物可读对象与动作短语构造唯一合法的启动请求。"""
    from worldmock.life.models import StartActivityRequest

    payload = {"targets": list(targets), "actions": [action for _ in targets]}
    payload.update(fields)
    return StartActivityRequest(**payload)


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


def presented_rows(engine: LifeEngine, round_id: str, kind: str, agent_id: int = 1,
                   rounds: int = 8) -> list[dict]:
    """累计连续几轮呈现窗口里出现过的某类交互对象行（object_id 去重，保持首见序）。

    目录下钻工具已随稳定工具面收敛删除，呈现窗口是内容/游戏/生活活动的唯一可见通道
    （docs/dev/SPEC-life-runtime.md「曝光轮转」）。轮转游标只在决策轮提交或世界推进时
    落盘，因此每观察一轮都要真正走完一轮，才能看到下一批身份。
    """
    rows: dict[str, dict] = {}
    for step in range(rounds):
        ready = list(engine.scheduling_state().ready_agent_ids)
        if agent_id not in ready:
            if not advance_until_ready(engine):
                break
            ready = list(engine.scheduling_state().ready_agent_ids)
            if agent_id not in ready:
                break
        data = engine.observe(context(engine, f"{round_id}-{step}", agent_id))
        for row in data["facts"]["interactive_objects"]:
            if row["kind"] == kind:
                rows.setdefault(row["object_id"], row)
        requests = {item: ("start_activity", start_activity("wait", planned_duration_seconds=60))
                    for item in ready}
        submit_round(engine, requests, f"{round_id}-{step}")
    return list(rows.values())

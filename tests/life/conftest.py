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
                base_url="https://openrouter.ai/api/v1",
                requested_model="deepseek-v4.1-flash",
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
    """给人物预置当日计划：解除日首 plan_day 工具面强制（非计划语义测试用）。

    D6 缺口 6 后，日首唤醒（run_start/wake_from_sleep）且当日无计划时动作面
    只留 plan_day；考察其他工具面的测试先预置一份当日计划（单段覆盖到
    当日结束，无未来段起点——不引入额外唤醒）。经存储落盘保持版本连续。
    """
    from worldmock.footprints.models import EventDraft
    from worldmock.life.schedule import DayPlan, PlanSegment, day_end_seconds

    now = engine.state.current_time
    day_end = day_end_seconds(now, engine.scenario.timezone)
    staged = engine.state.model_copy(deep=True)
    staged.agents[agent_id] = staged.agents[agent_id].model_copy(
        update={"day_plan": DayPlan(bedtime=day_end, day_end=day_end, segments=(
            PlanSegment(start=now, end=day_end, intent_kind="休息"),))}, deep=True)
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


def browse_activities(engine: LifeEngine, round_id: str, category: str,
                      agent_id: int = 1) -> list[dict]:
    """按真实轮次下钻一个活动品类并返回该品类的全量条目（browse 是纯查询动作）。

    下推按游标逐页进行，因此返回的是全量有序候选而不是首屏；round_id 只用于首屏。
    """
    from worldmock.life.models import BrowseActivitiesRequest

    ready = list(engine.scheduling_state().ready_agent_ids)
    requests = {item: ("browse_activities",
                       BrowseActivitiesRequest(category=category, cursor=None))
                for item in ready}
    _, results = submit_round(engine, requests, round_id)
    result = next(item for item in results if item.agent_id == agent_id)
    assert result.status.value == "completed", result.error_code
    items = list(result.data["items"])
    cursor = result.data["next_cursor"]
    step = 0
    while cursor is not None:
        if not advance_until_ready(engine):
            break
        step += 1
        ready = list(engine.scheduling_state().ready_agent_ids)
        requests = {item: ("browse_activities",
                           BrowseActivitiesRequest(category=category, cursor=cursor))
                    for item in ready}
        _, results = submit_round(engine, requests, f"{round_id}-page-{step}")
        page = next(item for item in results if item.agent_id == agent_id)
        assert page.status.value == "completed", page.error_code
        items.extend(page.data["items"])
        cursor = page.data["next_cursor"]
    return items


def browse_all(engine: LifeEngine, category: str, agent_id: int = 1,
               limit: int = 32) -> list[dict]:
    """以空游标起反复带上一页游标下推，拼出一个品类的全量条目（无重、无漏）。

    游标下推要求每个下一屏落在新的决策位上，因此每次下推前先推进到人物就绪的边界。
    """
    from worldmock.life.models import BrowseActivitiesRequest

    items: list[dict] = []
    cursor = None
    for step in range(limit):
        ready = list(engine.scheduling_state().ready_agent_ids)
        if agent_id not in ready and step:
            if not advance_until_ready(engine):
                break
            ready = list(engine.scheduling_state().ready_agent_ids)
        requests = {item: ("browse_activities",
                           BrowseActivitiesRequest(category=category, cursor=cursor))
                    for item in ready}
        _, results = submit_round(engine, requests, f"menu-all-{category}-{step}")
        payload = next(item for item in results if item.agent_id == agent_id).data
        items.extend(payload["items"])
        cursor = payload["next_cursor"]
        if cursor is None:
            break
    return items

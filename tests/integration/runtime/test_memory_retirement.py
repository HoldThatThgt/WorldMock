"""人物一侧的记忆不再调用模型（docs/design/06-agent.md「宿主模型协议」、docs/design/05-recording-engine.md「事件与来源」）：
当天第一次睡醒那一步与其他步骤相同，只发行动请求；memory_updated 只有步尾登记，环境拒绝来源观察为空或 consolidated 为真的
证据；Life 扩展通道只剩 read_query；巩固、睡醒反思与 due_reflection 的代码都已删除。

真实宿主运行（tests/integration/host_run_fixtures.py）：真实引擎、SQLite、LifeEnv 与直接导入的 LifeAgent，脚本模型只答
行动请求，收到别的请求当场失败。
"""

from __future__ import annotations

import logging
import os
from dataclasses import replace

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.person import PersonAgent  # noqa: E402
from custom.agents.life_agent import LifeAgent  # noqa: E402
from custom.envs.life_env import LifeEnv  # noqa: E402
from tests.engine_fixture import engine_factory  # noqa: E402,F401
from tests.integration.host_run_fixtures import _evening, host_run, memory, tool_names  # noqa: E402
from tests.life.conftest import MAIN_TOOL, stage_day_plan  # noqa: E402
from worldmock.agents.memory import life_memory  # noqa: E402
from worldmock.agents.memory.life_memory import WorldMockMemoryRuntime  # noqa: E402
from worldmock.agents.memory.life_memory_store import WorldMockMemoryStore  # noqa: E402
from worldmock.agents.prompt import prompt_capacity  # noqa: E402
from worldmock.foundation.errors import ProtocolError  # noqa: E402
from worldmock.runtime.framework.read_query import LifeEnvActor, LifeEnvProxy  # noqa: E402
from worldmock.simulation_engine.engine import LifeEngine  # noqa: E402
from worldmock.simulation_engine.evidence.model import ModelEvidenceRecorder  # noqa: E402
from worldmock.simulation_engine.observation.layers import memory_layer  # noqa: E402
from worldmock.state.model.exchange import is_action_request  # noqa: E402

#: 两次收尾各写的六条互不相似的记忆。
_NOTES = (("早上跑了五公里", "给妈妈回了电话", "把书架整理好了", "午饭吃了一碗面", "下午读完一章小说", "晚上去超市买菜"),
          ("周末想去爬山", "答应同事周五交初稿", "楼下新开了花店", "学会了做蛋饼", "阳台的花开了", "地铁上听了播客"))


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    """脚本模型使用固定供应商窗口，不请求外部模型元数据。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: metadata.context_window)


async def _woken(engine_factory, tmp_path, recorder=None):
    """周一 20:00 开始：做两次测试休息，每次收尾写六条重要度 0.7 的记忆（合计 12 条、重要度 8.4，超过已退役的两道阈值），
    再睡 480 分钟到当天第一次睡醒，并完成那一步的决策（给这段睡眠收尾）。recorder 给出时包住 LifeEnv 的记忆登记。"""
    engine = engine_factory(mutate=_evening)
    stage_day_plan(engine, submitted=True)
    run = await host_run(engine, tmp_path / "people" / "agent_0001")
    if recorder is not None:
        original = run.environment.record_memory_update

        async def record(evidence):
            """先交给替身检查，再照常登记。"""
            recorder(engine, evidence)
            await original(evidence)

        run.environment.record_memory_update = record
    for notes in _NOTES:
        await run.start("interact", "家", "测试休息")
        await run.conclude(*(memory(text, importance=0.7) for text in notes))
    slept = await run.sleep(480)
    await run.conclude()
    assert engine.context.state.activities[slept].concluded_at is not None
    return engine, run


async def test_first_wake_up_of_the_day_sends_only_action_requests(engine_factory, tmp_path):
    """当天第一次睡醒那一步与其他步骤相同：脚本模型收到的每个请求的工具都含 start_main_activity；库里不带 purpose 的
    model_request 都是行动请求，其余只有零点的 plan_day 与 memory_digest；主观摘要为空，MEMORY.md 写「暂无人物自述」。"""
    engine, run = await _woken(engine_factory, tmp_path)
    assert run.model.requests and all(MAIN_TOOL in tool_names(request) for _, request in run.model.requests)
    requests = [event.data for event in run.events("model_request")]
    assert all(is_action_request(data["request"]) for data in requests if data.get("purpose") is None)
    assert {data["purpose"] for data in requests if data.get("purpose") is not None} == {"plan_day", "memory_digest"}
    store = run.agent._memory_runtime.store
    assert store.read_subjective_summary() == ""
    assert "> 暂无人物自述。" in store.memory_md_path.read_text(encoding="utf-8")


async def test_memory_updates_are_step_tail_registrations_only(engine_factory, tmp_path):
    """每条 memory_updated 都是步尾登记：来源观察是本决策的观察，reflection 为 null，consolidated 为假，写入点是 [] 或
    ["activity_conclusion"]；同一决策至多一条；快照恰为四个基本键。"""
    engine, run = await _woken(engine_factory, tmp_path)
    updates = run.events("memory_updated")
    assert len(updates) >= 2
    observations = {event.decision_id: event.event_id for event in run.events("observation")}
    for event in updates:
        data = event.data
        assert data["observation_event_id"] == observations[event.decision_id]
        assert data["reflection"] is None and data["consolidated"] is False
        assert data["trigger"] in ([], ["activity_conclusion"])
        assert all(set(item) == {"id", "type", "text", "epistemic_status"} for item in data["episodes"])
    assert len({event.decision_id for event in updates}) == len(updates)


def _refused(field: str, value):
    """替身检查：把本次的真实步尾证据改写一个键交给引擎，必须被拒且库里不多一条事件。"""
    def check(engine, evidence) -> None:
        before = engine.context.store.last_event_seq
        with pytest.raises(ProtocolError, match="^Memory update evidence is invalid$"):
            engine.record_memory_update(replace(evidence, **{field: value}))
        assert engine.context.store.last_event_seq == before

    return check


async def test_memory_update_evidence_without_an_observation_is_rejected(engine_factory, tmp_path, caplog):
    """把一条合法的步尾证据改成 observation_event_id 为空交给 LifeEngine.record_memory_update：协议错误，先写拒绝日志，
    事件序号不变。"""
    with caplog.at_level(logging.ERROR):
        await _woken(engine_factory, tmp_path, _refused("observation_event_id", None))
    assert "Memory update evidence rejected" in caplog.text


async def test_consolidated_memory_update_evidence_is_rejected(engine_factory, tmp_path, caplog):
    """同上，改成 consolidated 为真。"""
    with caplog.at_level(logging.ERROR):
        await _woken(engine_factory, tmp_path, _refused("consolidated", True))
    assert "Memory update evidence rejected" in caplog.text


def test_life_channel_offers_only_read_query():
    """Life 扩展通道的 actor、代理、LifeEnv 与生活引擎都没有 due_reflection；前三者有 read_query。"""
    for owner in (LifeEnvActor, LifeEnvProxy, LifeEnv, LifeEngine):
        assert not hasattr(owner, "due_reflection")
    for owner in (LifeEnvActor, LifeEnvProxy, LifeEnv):
        assert callable(getattr(owner, "read_query", None))


def test_retired_memory_paths_are_gone():
    """巩固、睡醒反思与 8000 字窗口的私有方法与常量都已删除；LifeAgent 不覆写 AS2 的记忆模型入口。"""
    for name in ("wake_reflection", "_run_reflection", "_validated_reflection", "_consolidation_due",
                 "_consolidate_subjective", "_reflection_episodes", "_reflection_messages"):
        assert not hasattr(WorldMockMemoryRuntime, name)
    for name in ("_PENDING_THRESHOLD", "_IMPORTANCE_THRESHOLD", "_REFLECTION_SOURCE", "_SUBJECTIVE_PROMPT"):
        assert not hasattr(life_memory, name)
    assert not hasattr(WorldMockMemoryStore, "unconsolidated_view")
    assert not hasattr(LifeAgent, "_wake_reflection") and not hasattr(LifeAgent, "_memory_call_id")
    assert LifeAgent._dispatch_memory_llm is PersonAgent._dispatch_memory_llm
    assert not hasattr(ModelEvidenceRecorder, "_reflection_registration")
    for name in ("MEMORY_WINDOW_CHARS", "_reflections", "_fit"):
        assert not hasattr(memory_layer, name)

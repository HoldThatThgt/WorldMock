"""WorldMock 人物记忆的已核验事实和主观自述隔离。

主观条目只经 conclude_activity 逐项的记忆与看法写入，宿主按项展平并由系统固定来源（docs/design/04-simulation-engine.md 第七章）。
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")

from agentsociety2.agent.memory_runtime import MemoryRuntimeConfig
from agentsociety2.agent.base.react import MemoryUpdateEvidence
from tests.life.conftest import (
    advance_until_ready, conclude_request, stage_day_plan, start_activity, submit_round, wake_by_mail,
)
from custom.agents.life_agent import _conclusion_memories
from worldmock.agents.life_memory import WorldMockMemoryRuntime, render_memory_view
from worldmock.agents.life_reflection import REFLECTION_PROMPT, reflection_tool
from worldmock.footprints.experiences import project_verified_experiences
from worldmock.footprints.models import EventDraft
from worldmock.footprints.store import FootprintStore
from worldmock.life.memory_evidence import _memory_evidence_data


_TIME = datetime(2026, 9, 21, 8, tzinfo=timezone.utc)


def _response(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _runtime(tmp_path, dispatch=None, config=None):
    async def no_call(**_):
        pytest.fail("subjective consolidation was not due")

    owner = SimpleNamespace(
        id=1, name="Person", _model_name="fixture", _dispatch_memory_llm=dispatch or no_call,
        get_profile=lambda: {"name": "Person"}, _logger=SimpleNamespace(warning=lambda *a: None),
        trace_span=None, _persona_card_text=lambda: "人物画像里的身份",
        _life_verified_experiences=[], _life_observation_event_id="observation-1",
    )
    runtime = WorldMockMemoryRuntime(owner, config or MemoryRuntimeConfig())
    runtime.ensure_store(tmp_path)
    return runtime, owner


def _concluded(text: str, kind: str = "goal", importance: float = 0.5,
               keywords: tuple[str, ...] = (), about: str | None = None) -> list[dict]:
    """结束一项活动时写下的一条记忆（about 非空时是写在 views 的看法），经宿主展平（来源 activity_conclusion，认知状态
    subjective，看法的对象去掉）。"""
    entry = {"type": kind, "text": text, "importance": importance, "keywords": list(keywords)}
    memories, views = ([entry], []) if about is None else ([], [{"type": kind, "about": about, **entry}])
    return _conclusion_memories("conclude_activity", {"items": [
        {"activity_name": "等待（9月21日 16:00 起）", "summary": "收尾。", "memories": memories, "views": views}],
        "errands": []})


def _wait_fact(event_id="progress-1", seq=1):
    return {"kind": "waited", "agent_id": 1, "activity_id": "waiting", "source_event_id": event_id,
            "event_seq": seq, "occurred_at": 100, "interval_start": 90, "interval_end": 100,
            "duration_seconds": 10, "reason": None}


@pytest.mark.asyncio
async def test_memory_runtime_without_a_workspace_does_not_claim_a_persisted_update(tmp_path):
    runtime, owner = _runtime(tmp_path)
    cold = WorldMockMemoryRuntime(owner, MemoryRuntimeConfig())
    assert cold.build_context() == {}
    outcome = await cold.after_step(tick=60, t=_TIME, step_count=1, finish_memories=[])
    assert outcome.created_episodes == () and outcome.merged_episodes == ()
    assert cold.store is None


def test_oversized_fact_view_fails_instead_of_truncating_verified_evidence():
    with pytest.raises(ValueError, match="budget"):
        render_memory_view("a" * 4000, [_wait_fact()], "我想休息")


@pytest.mark.asyncio
async def test_fact_view_only_uses_current_observation_and_does_not_reset_subjective_counter(tmp_path):
    runtime, owner = _runtime(tmp_path)
    owner._life_verified_experiences = [_wait_fact()]
    subjective = _concluded("我已经吃完三顿饭", importance=0.7, keywords=("饭",))
    first = await runtime.after_step(tick=60, t=_TIME, step_count=1, finish_memories=subjective)
    assert len(first.created_episodes) == 1 and first.merged_episodes == ()
    assert first.created_episodes[0]["source"] == "activity_conclusion"
    assert first.verified_event_ids == ("progress-1",)
    assert runtime.store.read_state()["pending_episode_count"] == 1
    view = runtime.store.memory_md_path.read_text(encoding='utf-8')
    assert view.count("## 已核验经历") == 1
    identity = view.split("## 身份\n", 1)[1].split("## 已核验经历", 1)[0].strip()
    assert identity == "> 人物画像里的身份"
    facts = view.split("## 已核验经历\n", 1)[1].split("## 人物自述", 1)[0]
    assert "progress-1" in facts and "吃完三顿饭" not in facts
    context = runtime.build_context()
    assert set(context) == {"recent_episodes", "files"}
    assert context["recent_episodes"][0]["epistemic_status"] == "subjective"
    owner._life_verified_experiences = [_wait_fact("progress-2", 2)]
    owner._life_observation_event_id = "observation-2"
    second = await runtime.after_step(tick=60, t=_TIME, step_count=2, finish_memories=[])
    assert second.created_episodes == () and second.merged_episodes == ()
    assert second.verified_event_ids == ("progress-2",)
    assert runtime.store.read_state()["pending_episode_count"] == 1
    assert "progress-2" in runtime.store.memory_md_path.read_text(encoding='utf-8')
    assert "progress-1" not in json.dumps(runtime.build_context(), ensure_ascii=False)
    third = await runtime.after_step(tick=60, t=_TIME, step_count=3, finish_memories=[])
    assert third.created_episodes == () and third.merged_episodes == ()
    assert third.memory_before_sha256 is not None


@pytest.mark.asyncio
async def test_multiple_receipts_keep_goods_and_their_source_references(tmp_path):
    footprint = FootprintStore.create(
        tmp_path / "run", {"run_id": "run", "agent_ids": [1]},
        {"version": 0, "current_time": 100},
    )
    try:
        receipts = footprint.commit(footprint.state, [EventDraft(
            event_type="environment_event", occurred_at=100, agent_id=1,
            data={"kind": "goods_received", "order_id": f"order-{goods_id}",
                  "goods_id": goods_id, "category": "meal", "quantity": 1},
        ) for goods_id in ("rice", "soup")])
        facts = project_verified_experiences(receipts, agent_id=1, cutoff_seq=receipts[-1].event_seq)
        runtime, owner = _runtime(tmp_path / "memory")
        owner._life_verified_experiences = facts
        outcome = await runtime.after_step(tick=60, t=_TIME, step_count=1,
                                           finish_memories=_concluded("想再来一碗汤"))
        assert [item["goods_id"] for item in facts] == ["rice", "soup"]
        assert outcome.verified_event_ids == tuple(receipt.event_id for receipt in receipts)
        view = runtime.store.memory_md_path.read_text(encoding='utf-8')
        assert all(view.count(receipt.event_id) == 1 for receipt in receipts)
        episode = outcome.created_episodes[0]
        evidence = MemoryUpdateEvidence(
            context=None, operation="append_episodes", created_episode_ids=(episode["id"],),
            merged_episode_ids=(), episodes=({key: episode[key] for key in
                                              ("id", "type", "text", "epistemic_status")},),
            memory_before_sha256=outcome.memory_before_sha256,
            memory_sha256=_sha256(runtime.store.memory_md_path),
            episodes_sha256=_sha256(runtime.store.episodes_path),
            state_sha256=_sha256(runtime.store.state_path), consolidated=False,
            observation_event_id=outcome.observation_event_id,
            verified_event_ids=outcome.verified_event_ids,
        )
        assert _memory_evidence_data(evidence)["verified_event_ids"] == [receipt.event_id for receipt in receipts]
    finally:
        footprint.close()


@pytest.mark.asyncio
async def test_actual_app_progress_is_written_to_verified_memory_with_source_and_cursor(engine_factory, tmp_path):
    """真实 app 足迹进入启用的 AS2 记忆存储；应用、内容与实际区间位置不靠人物自述补齐。"""
    engine = engine_factory()
    stage_day_plan(engine)
    wake_by_mail(engine, offset=120)
    _, results = submit_round(engine, {1: ("start_activity", start_activity("进行", "刷推荐", app=["抖音"]))},
                              "memory-app")
    started = results[0]
    assert started.status == "in_progress"
    assert advance_until_ready(engine)
    activity = engine.context.state.activities[started.activity_id]
    _, results = submit_round(engine, {1: ("conclude_activity", conclude_request(activity.name))},
                              "memory-app-end")
    assert results[0].status == "completed"
    events = engine.context.store.read_events(engine.context.store.last_event_seq)
    facts = project_verified_experiences(events, agent_id=1, cutoff_seq=engine.context.store.last_event_seq)[-5:]
    assert facts and all(fact["kind"] == "played" for fact in facts)
    runtime, owner = _runtime(tmp_path / "app-memory")
    owner._life_verified_experiences = facts
    result = await runtime.after_step(tick=60, t=_TIME, step_count=1, finish_memories=[])
    view = runtime.store.memory_md_path.read_text(encoding="utf-8")
    assert result.verified_event_ids == tuple(fact["source_event_id"] for fact in facts)
    for fact in facts:
        assert f"使用应用 {fact['app_id']}，内容 {fact['content_id']}" in view
        assert f"{fact['position_before_seconds']:g} 至 {fact['position_after_seconds']:g} 秒" in view
        assert fact["source_event_id"] in view
    assert runtime.store.read_state()["pending_episode_count"] == 0 and result.created_episodes == ()


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.asyncio
async def test_sleep_reflection_is_subjective_and_its_id_is_reported(tmp_path):
    """就寝边界先做主观归纳、再经唯一严格工具 submit_reflection 提交反思（docs/design/10-runtime.md ）：请求只带
    这一个工具并强制调用，洞见固定为主观的 reflection 条目（重要度 0.8、无关键词、来源 reflection），其身份随本步结果报告。"""
    holder = {}

    async def dispatch(**request):
        if len(request["messages"]) != 2:
            pytest.fail("unexpected memory request")
        holder["calls"] = holder.get("calls", 0) + 1
        if holder["calls"] == 1:
            return _response("我想和朋友保持联系。")
        identity = holder["runtime"].store.iter_episodes()[0]["id"]
        assert request["tool_choice"] == "required" and "purpose" not in request
        assert request["tools"] == [reflection_tool()]
        assert request["tools"][0]["function"]["strict"] is True
        assert request["messages"][0] == {"role": "system", "content": REFLECTION_PROMPT}
        function = SimpleNamespace(name="submit_reflection", arguments=json.dumps(
            {"insights": [{"insight": "我更珍惜这段关系", "refs": [identity]}]}))
        message = SimpleNamespace(content=None, tool_calls=[SimpleNamespace(function=function)])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    runtime, owner = _runtime(tmp_path, dispatch)
    holder["runtime"] = runtime
    outcome = await runtime.after_step(
        tick=60, t=_TIME, step_count=1, finish_memories=_concluded("想多联系朋友", "relationship", 0.6, about="朋友"),
        sleep_boundary=True)
    assert holder["calls"] == 2
    assert len(outcome.created_episodes) == 2
    reflection = next(item for item in outcome.created_episodes if item["type"] == "reflection")
    assert reflection["epistemic_status"] == "subjective"
    assert reflection["source"] == "reflection"
    assert reflection["text"] == "我更珍惜这段关系"
    assert reflection["importance"] == 0.8 and reflection["keywords"] == []
    conclusion = next(item for item in outcome.created_episodes if item["type"] == "relationship")
    assert conclusion["source"] == "activity_conclusion"
    assert outcome.consolidated is True
    assert runtime.store.read_subjective_summary() == "我想和朋友保持联系。"
    assert "subject" not in conclusion and "about" not in conclusion
    assert "我想和朋友保持联系" not in json.dumps(runtime.build_context(), ensure_ascii=False)


@pytest.mark.asyncio
async def test_zero_recent_memory_limit_returns_no_recent_entries(tmp_path):
    """recent_memory_limit 为 0 时最近条目为空列表（docs/design/06-agent.md 第六章「零条最近记忆失效」回归：
    AS2 存储会把 0 改回 8 条，去掉守卫即失败）；大于 0 时照常返回。"""
    runtime, _ = _runtime(tmp_path, config=MemoryRuntimeConfig(recent_limit=0))
    await runtime.after_step(tick=60, t=_TIME, step_count=1, finish_memories=_concluded("想休息"))
    assert runtime.store.iter_episodes() and runtime.build_context()["recent_episodes"] == []
    capped, _ = _runtime(tmp_path / "capped", config=MemoryRuntimeConfig(recent_limit=1))
    await capped.after_step(tick=60, t=_TIME, step_count=1, finish_memories=_concluded("想休息"))
    assert [item["text"] for item in capped.build_context()["recent_episodes"]] == ["想休息"]


def test_memory_tool_returns_explicit_subjective_label_even_when_empty(tmp_path):
    runtime, _ = _runtime(tmp_path)
    ok, _, data = runtime.dispatch_tool("memory_recent", {"limit": 3})
    assert ok and data == {"epistemic_status": "subjective", "episodes": []}


def test_memory_store_results_cannot_mutate_persisted_nested_episode_data(tmp_path):
    runtime, _ = _runtime(tmp_path)
    store = runtime.store
    store.append_episodes(_concluded("愿意去散步", keywords=("散步",)), tick=60, t=_TIME, step_count=1)
    retrieved = store.iter_episodes()
    retrieved[0]["keywords"].append("fabricated")
    assert store.iter_episodes()[0]["keywords"] == ["散步"]


@pytest.mark.asyncio
async def test_restore_preserves_subjective_identity_and_rejects_status_tampering(tmp_path):
    runtime, owner = _runtime(tmp_path)
    owner._life_verified_experiences = [_wait_fact()]
    outcome = await runtime.after_step(tick=60, t=_TIME, step_count=1, finish_memories=_concluded("想休息"))
    identity = outcome.created_episodes[0]["id"]
    persisted_view = runtime.store.memory_md_path.read_bytes()
    resumed = WorldMockMemoryRuntime(owner, MemoryRuntimeConfig())
    resumed.restore_store(tmp_path)
    assert resumed.store.iter_episodes()[0]["id"] == identity
    assert resumed.store.memory_md_path.read_bytes() == persisted_view
    episode = resumed.store.iter_episodes()[0]
    episode["epistemic_status"] = "objective"
    resumed.store.episodes_path.write_text(json.dumps(episode, ensure_ascii=False) + "\n", encoding='utf-8')
    with pytest.raises(ValueError, match="epistemic status"):
        WorldMockMemoryRuntime(owner, MemoryRuntimeConfig()).restore_store(tmp_path)


@pytest.mark.asyncio
async def test_repeated_subjective_memory_reports_merge_identity_without_duplicate(tmp_path):
    """相似记忆合并沿用旧身份，并在本次更新结果明确报告合并。"""
    runtime, _ = _runtime(tmp_path)
    memory = _concluded("想去附近散步")
    first = await runtime.after_step(tick=60, t=_TIME, step_count=1, finish_memories=memory)
    identity = first.created_episodes[0]["id"]
    second = await runtime.after_step(tick=120, t=_TIME, step_count=2, finish_memories=memory)
    assert second.created_episodes == ()
    assert [row["id"] for row in second.merged_episodes] == [identity]
    assert second.merged_episodes[0]["epistemic_status"] == "subjective"
    stored = runtime.store.iter_episodes()
    assert len(stored) == 1 and stored[0]["id"] == identity
    assert stored[0]["merge_count"] == 2

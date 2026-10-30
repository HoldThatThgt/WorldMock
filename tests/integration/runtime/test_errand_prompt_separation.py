"""个人事务仅出现在待办与回执，收尾记忆保持主观内容。"""
import json
import pytest
from tests.life.conftest import stage_day_plan, do_at, errand_add, context, advance_until_ready
from tests.life.environment_runtime import act
from tests.integration.runtime.current_contract_fixtures import environment
from tests.integration.runtime.test_same_second_continuation import _workspace_person, _step, _reply

@pytest.mark.asyncio
async def test_errand_arguments_never_become_memory_or_progress_parameters(engine_factory, tmp_path):
    engine = engine_factory()
    stage_day_plan(engine)
    _, started = act(engine, "chat", do_at("家", "测试闲聊"))
    assert advance_until_ready(engine)
    name = engine.context.state.activities[started.activity_id].name
    env = environment(engine)
    memory = {"type": "reflection", "text": "聊天让我心情轻松。", "importance": 0.3,
              "keywords": ["聊天"]}
    errands = [errand_add("独特买面事务", "牛肉面", memo="独特事务备注")]
    arguments = {"items": [{"activity_name": name, "summary": "聊完了。", "memories": [memory],
                           "views": []}], "errands": errands}
    agent, _ = await _workspace_person(env, tmp_path / "agent", [
        _reply("conclude", "conclude_activity", arguments, "把事情记在待办里。")])
    await _step(env, agent, "concluded")
    notes = engine.context.store.read_agent_events("memory_updated", 1)
    assert len(notes) == 1
    assert memory["text"] in json.dumps(notes[0].data, ensure_ascii=False)
    assert all(text not in json.dumps(notes[0].data, ensure_ascii=False)
               for text in ["独特买面事务", "独特事务备注"])
    observed = engine.observe(context(engine, "next"))
    assert "独特买面事务" in json.dumps(observed["todo_layer"], ensure_ascii=False)
    assert not [row for row in observed["progress_layer"]["events"]
                if row["type"] == "tool_call" and row["tool"] == "conclude_activity"]
    assert "独特事务备注" not in json.dumps(observed["progress_layer"], ensure_ascii=False)

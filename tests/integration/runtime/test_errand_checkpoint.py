"""共同检查点保存真实个人事务，分支观察不重复登记事务。"""
from tests.life.conftest import stage_day_plan, start_activity, conclude_request, errand_add, context, advance_until_ready
from tests.life.environment_runtime import act
from worldmock.footprints.checkpoint import restore_branch
from worldmock.life.engine import LifeEngine
from tests.integration.runtime.current_contract_fixtures import profile
from tests.integration.runtime_fixtures import write_agent_workspace
from worldmock.footprints.checkpoint import snapshot_directory

def test_checkpoint_restores_identical_errands_and_todo_without_renoting(engine_factory, tmp_path):
    engine = engine_factory()
    stage_day_plan(engine)
    _, started = act(engine, "chat", start_activity("interact", "家"))
    assert advance_until_ready(engine)
    name = engine.context.state.activities[started.activity_id].name
    _, result = act(engine, "note", conclude_request(name, errands=[
        errand_add("明天吃碗面", "牛肉面", quantity=2, memo="午饭带回来", remind_time="12:30")]), "conclude_activity")
    assert result.status == "completed"
    expected = engine.context.state.agents[1].errands
    workspace = tmp_path / "agent"
    write_agent_workspace(workspace)
    checkpoint = engine.checkpoint({"agent:1": snapshot_directory(workspace, engine.context.store.assets)})
    child_store = restore_branch(engine.context.store, checkpoint.checkpoint_id, tmp_path / "branch")
    child_store.close()
    child = LifeEngine.open(engine.context.inputs, tmp_path / "branch")
    try:
        assert child.context.state.agents[1].errands == expected
        parent_view = engine.observe(context(engine, "parent-view"))
        child_view = child.observe(context(child, "child-view"))
        assert child_view["todo_layer"] == parent_view["todo_layer"]
        noted = [row for row in child.context.store.read_events(child.context.store.last_event_seq)
                 if row.event_type == "environment_event" and row.data.get("kind") == "errand_registered"]
        assert len(noted) == 1
    finally:
        child.close()

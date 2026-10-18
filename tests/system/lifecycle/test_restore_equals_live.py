"""恢复等于不中断：脚本化活动完整日的每一步之后都写共同检查点并开出恢复分支，分支里的世界与此刻的不中断运行逐字节
相同（运行身份与版本除外）、活动先后相同，人物就绪时同一时刻的观察也逐字节相同（docs/design/09-io-formats.md
「世界、人物与活动」）。"""

import shutil

from tests.integration.runtime_fixtures import branch_of
from tests.life.day_baseline_runtime import create_complete_day_engine, play_complete_day
from tests.life.environment_runtime import check_every_step
from worldmock.footprints.models import canonical_json


def _world(engine) -> bytes:
    """引擎世界的规范 JSON，去掉运行身份与版本。"""
    payload = engine.context.state.model_dump(mode="json")
    return canonical_json({key: value for key, value in payload.items() if key not in {"run_id", "version"}})


def test_a_branch_at_every_step_matches_the_live_world_and_observation(tmp_path, monkeypatch):
    """每次决策轮提交、每次推进与每次零点判定之后：分支的世界与父运行相同、版本多一，活动先后相同；人物就绪时分支同样
    就绪，两边同一时刻的观察（用私有构建器取，不写观察事件，父运行的本秒呈现暂存原样放回）逐字节相同。"""
    parent = create_complete_day_engine(tmp_path / "case", "activity")
    steps = []

    def probe(parent, origins) -> None:
        """在此刻开出恢复分支并与父运行逐项比较，比较完关闭并删掉分支。"""
        step_dir = tmp_path / f"step-{len(steps)}"
        pending = dict(parent.observations.pending_presentation)
        child = branch_of(parent, step_dir, "resume", "-")
        try:
            assert child.context.state.version == parent.context.state.version + 1
            assert _world(child) == _world(parent)
            assert list(child.context.state.activities) == list(parent.context.state.activities)
            ready = parent.scheduling_state().ready_agent_ids
            if ready:
                assert child.scheduling_state().ready_agent_ids == ready
                observed = [canonical_json(engine.observations._observation_data(engine.context.state.agents[1]))
                            for engine in (parent, child)]
                assert observed[0] == observed[1]
            steps.append(bool(ready))
        finally:
            child.close()
            parent.observations.pending_presentation = pending
            shutil.rmtree(step_dir)

    check_every_step(monkeypatch, parent, probe)
    try:
        play_complete_day(parent, "activity")
    finally:
        parent.close()
    assert len(steps) > 100 and any(steps)

"""生活状态规范序列化的跨进程回归。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from worldmock.footprints.models import canonical_json
from worldmock.footprints.store import FootprintStore
from worldmock.life.errand_state import ErrandState
from worldmock.life.models import WORLD_SCHEMA_VERSION, ActivityState, AgentState, BodyState, WorldState


def _world_state() -> WorldState:
    """构造覆盖全部持久字符串集合的最小世界。"""
    agent = AgentState(
        agent_id=1,
        name="Alice",
        location_id="home",
        balance_account_id="agent:1",
        body=BodyState(energy_kcal=500, sleep_pressure=0.0, sleep_deficit_hours=1, sleep_need_hours=7.5,
                       chronotype_midsleep=4.0, relaxation_need=0.5, social_need=0.5,
                       social_need_growth_per_hour=0.06),
        callback_once_keys={"fuel:2026-01-06", "fuel:2026-01-05"},
        body_settled_at=1,
        next_decision_at=1,
    )
    activity = ActivityState(
        activity_id="activity-1",
        agent_id=1,
        location_id="home",
        kind="interact",
        status="active",
        started_at=1,
        planned_end_at=10,
        last_settled_at=1,
        elapsed_seconds=0,
        planned_duration_seconds=9,
        resource_claims={"first"},
        origin_action_id="action-1",
        origin_decision_id="decision-1",
        origin_request_event_id="request-1",
        data={"action": "interact"},
    )
    return WorldState(
        schema_version=WORLD_SCHEMA_VERSION,
        version=1,
        current_time=1,
        end_time=10,
        run_id="run-1",
        timezone="Asia/Shanghai",
        persona_sha256="a" * 64,
        config_sha256="b" * 64,
        agents={1: agent},
        accounts={"agent:1": 0, "external:scenario": 0},
        activities={activity.activity_id: activity},
        presentation_state={1: {"product-z": 1_700_000_000, "product-a": 1_700_000_600}},
        random_seed=1,
        random_state="random-state",
    )


def _set_arrays(payload: dict) -> list[list[str]]:
    """按模型声明顺序取得全部持久集合的 JSON 数组。"""
    agent = payload["agents"]["1"]
    activity = payload["activities"]["activity-1"]
    return [
        agent["callback_once_keys"],
        activity["resource_claims"],
    ]


def _subprocess_env(root: Path, seed: int) -> dict[str, str]:
    """绑定隔离 checkout 和明确 Python 哈希种子。"""
    environment = dict(os.environ)
    vendor = root / "vendor" / "agentsociety2" / "packages" / "agentsociety2"
    environment.update(
        PYTHONHASHSEED=str(seed),
        PYTHONPATH=os.pathsep.join((str(root / "src"), str(vendor), str(root))),
        WORKSPACE_PATH=str(root),
        LITELLM_LOCAL_MODEL_COST_MAP="True",
    )
    return environment


def _commit_readonly_in_subprocess(root: Path, run_dir: Path, seed: int) -> str:
    """在真实独立解释器中打开状态并执行同版本事件提交。"""
    script = """
import json
import sys
from worldmock.footprints.models import EventDraft, canonical_json
from worldmock.footprints.store import FootprintStore
from worldmock.life.models import WorldState

store = FootprintStore(sys.argv[1])
state = WorldState.model_validate(store.state)
payload = state.model_dump(mode="json")
store.require_committed(payload)
store.append_evidence([EventDraft(event_type="environment_event", occurred_at=1,
                                  visibility="system", data={"kind": "readonly_probe"})])
sys.stdout.buffer.write(canonical_json(payload))
store.close()
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(run_dir)],
        cwd=root,
        env=_subprocess_env(root, seed),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def test_json_mode_sorts_every_persisted_string_set() -> None:
    """全部当前持久集合保留数组形状并按元素排序。"""
    state = _world_state()
    payload = state.model_dump(mode="json")

    for values in _set_arrays(payload):
        assert isinstance(values, list)
        assert values == sorted(values)
    assert isinstance(state.model_dump()["agents"][1]["callback_once_keys"], set)
    assert isinstance(state.model_dump()["activities"]["activity-1"]["resource_claims"], set)
    # 轮转派生量按（人物, 对象标识）逐个记录时刻，JSON 模式取值确定
    assert payload["presentation_state"] == {
        "1": {"product-a": 1_700_000_600, "product-z": 1_700_000_000}}


def _errand(errand_id: str, registered_at: int, progress: float) -> ErrandState:
    """一条未完成事务（取得两份商品，带部分进展与已交付的提醒）。"""
    return ErrandState(errand_id=errand_id, name=f"事务 {errand_id}", product_id="product-a",
                       quantity=2, progress=progress, memo="下班顺路买", registered_at=registered_at, remind_at=5,
                       reminded_at=6, origin_action_id=errand_id.split(":")[0], origin_event_id="request-1")


def test_open_errands_round_trip_through_json_with_a_byte_stable_encoding(tmp_path: Path) -> None:
    """人物的未完成事务随世界状态往返（docs/design/04-simulation-engine.md 「存放」、2.7）：JSON 模式按事务身份为键写出
    完整快照，读回逐值相等；规范编码不随字典插入顺序变化；经 FootprintStore 落盘再打开后状态字节相同。"""
    first, second = _errand("action-2:errand:0", 3, 0.1 + 0.2), _errand("action-1:errand:1", 2, 1.0)
    forward, backward = _world_state(), _world_state()
    forward.agents[1] = forward.agents[1].model_copy(update={"errands": {item.errand_id: item for item in (first, second)}})
    backward.agents[1] = backward.agents[1].model_copy(update={"errands": {item.errand_id: item for item in (second, first)}})
    payload = forward.model_dump(mode="json")
    assert payload["agents"]["1"]["errands"]["action-2:errand:0"] == {
        "errand_id": "action-2:errand:0", "name": "事务 action-2:errand:0", "product_id": "product-a",
        "quantity": 2, "progress": 0.3, "memo": "下班顺路买", "registered_at": 3, "remind_at": 5, "reminded_at": 6,
        "origin_action_id": "action-2", "origin_event_id": "request-1"}
    assert WorldState.model_validate(payload) == forward
    assert WorldState.model_validate_json(json.dumps(payload)).agents[1].errands == {
        "action-2:errand:0": first, "action-1:errand:1": second}
    assert canonical_json(payload) == canonical_json(backward.model_dump(mode="json"))
    store = FootprintStore.create(tmp_path / "run", {"run_id": "run-1", "agent_ids": [1]}, payload)
    store.close()
    reopened = FootprintStore(tmp_path / "run")
    assert canonical_json(WorldState.model_validate(reopened.state).model_dump(mode="json")) == canonical_json(payload)
    reopened.close()


def test_readonly_commit_is_identical_across_python_hash_seeds(tmp_path: Path) -> None:
    """不同哈希种子的独立进程可对同一状态提交只读事件。"""
    root = Path(__file__).resolve().parents[3]
    base = tmp_path / "base"
    state = _world_state().model_dump(mode="json")
    store = FootprintStore.create(base, {"run_id": "run-1", "agent_ids": [1]}, state)
    store.close()
    first = tmp_path / "seed-1"
    second = tmp_path / "seed-2"
    shutil.copytree(base, first)
    shutil.copytree(base, second)

    first_payload = _commit_readonly_in_subprocess(root, first, 1)
    second_payload = _commit_readonly_in_subprocess(root, second, 2)

    assert first_payload == second_payload
    assert _set_arrays(json.loads(first_payload)) == _set_arrays(state)


def test_world_state_schema_is_fourteen_and_the_previous_version_is_refused() -> None:
    """世界状态结构版本是 14；结构号 13 的世界状态由严格字面量拒绝，不迁移、不读取。"""
    import pytest
    from pydantic import ValidationError

    assert WORLD_SCHEMA_VERSION == 14
    payload = _world_state().model_dump(mode="json")
    assert WorldState.model_validate(payload).schema_version == 14
    for retired in (13, "14", None):
        with pytest.raises(ValidationError, match="schema_version"):
            WorldState.model_validate({**payload, "schema_version": retired})
    missing = {key: value for key, value in payload.items() if key != "schema_version"}
    with pytest.raises(ValidationError, match="schema_version"):
        WorldState.model_validate(missing)


def test_world_state_with_a_running_query_round_trips(engine_factory, tmp_path: Path) -> None:
    """真实引擎开始一次查询后写共同检查点：恢复出的世界里查询活动逐值相等（种类、槽位、data.app_id 都在），世界状态能
    写入能读回；重开的引擎把它做完时时长与 app 不重抽，答案与不中断的父运行逐值相同（docs/design/09-io-formats.md）。"""
    from tests.integration.runtime_fixtures import branch_of
    from tests.life.conftest import advance_next, query
    from tests.life.environment_runtime import act

    parent = engine_factory()
    observed, result = act(parent, "query", query("零食", "柠檬茶"))
    assert result.status == "in_progress", result.error_code
    running = parent.context.state.activities[result.activity_id]
    assert (running.kind, running.resource_claims) == ("query", {"second"})
    assert running.data["app_id"] in {"eleme", "meituan"}
    payload = parent.context.state.model_dump(mode="json")
    assert WorldState.model_validate(payload).model_dump(mode="json") == payload
    child = branch_of(parent, tmp_path, "resume", observed["observation_event_id"])
    try:
        restored = child.context.state.activities[result.activity_id]
        assert restored == running and "app_id" in restored.data
        for engine in (parent, child):
            while engine.context.state.activities[result.activity_id].status == "active":
                advance_next(engine)
        done, again = (engine.context.state.activities[result.activity_id] for engine in (parent, child))
        assert (again.planned_duration_seconds, again.data["app_id"]) == (
            running.planned_duration_seconds, running.data["app_id"])
        assert again.data["answer"] == done.data["answer"] and done.data["answer"]["rows"]
    finally:
        child.close()

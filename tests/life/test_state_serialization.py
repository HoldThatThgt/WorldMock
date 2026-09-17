"""生活状态规范序列化的跨进程回归。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from worldmock.footprints.store import FootprintStore
from worldmock.life.models import ActivityState, AgentState, BodyState, WorldState


def _world_state() -> WorldState:
    """构造覆盖全部持久字符串集合的最小世界。"""
    agent = AgentState(
        agent_id=1,
        name="Alice",
        location_id="home",
        balance_account_id="agent:1",
        body=BodyState(energy_kcal=500, sleep_pressure=0.0, sleep_deficit_hours=1, entertainment_need=0.5),
        active_effects={"work_notes", "coffee"},
        next_decision_at=1,
        read_message_ids={"mail-z", "mail-a"},
        visible_message_ids={"visible-z", "visible-a"},
        visible_task_ids={"task-z", "task-a"},
        visible_product_ids={"product-z", "product-a"},
    )
    activity = ActivityState(
        activity_id="activity-1",
        agent_id=1,
        kind="wait",
        status="active",
        started_at=1,
        planned_end_at=10,
        last_settled_at=1,
        elapsed_seconds=0,
        planned_duration_seconds=9,
        resource_claims={"hands", "device"},
        origin_action_id="action-1",
        origin_decision_id="decision-1",
        origin_request_event_id="request-1",
    )
    return WorldState(
        schema_version=2,
        version=1,
        current_time=1,
        end_time=10,
        run_id="run-1",
        timezone="Asia/Shanghai",
        scenario_sha256="a" * 64,
        content_manifest_sha256="b" * 64,
        agents={1: agent},
        accounts={"agent:1": 0},
        merchant_stock={},
        activities={activity.activity_id: activity},
        paid_task_ids={"paid-z", "paid-a"},
        random_seed=1,
        random_state="random-state",
    )


def _set_arrays(payload: dict) -> list[list[str]]:
    """按模型声明顺序取得全部持久集合的 JSON 数组。"""
    agent = payload["agents"]["1"]
    activity = payload["activities"]["activity-1"]
    return [
        agent["active_effects"],
        agent["read_message_ids"],
        agent["visible_message_ids"],
        agent["visible_task_ids"],
        agent["visible_product_ids"],
        activity["resource_claims"],
        payload["paid_task_ids"],
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
store.commit(payload, [EventDraft(event_type="environment_event", occurred_at=1,
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
    """全部七个持久集合保留数组形状并按元素排序。"""
    state = _world_state()
    payload = state.model_dump(mode="json")

    for values in _set_arrays(payload):
        assert isinstance(values, list)
        assert values == sorted(values)
    assert isinstance(state.model_dump()["agents"][1]["active_effects"], set)
    assert isinstance(state.model_dump()["activities"]["activity-1"]["resource_claims"], set)
    assert isinstance(state.model_dump()["paid_task_ids"], set)


def test_readonly_commit_is_identical_across_python_hash_seeds(tmp_path: Path) -> None:
    """不同哈希种子的独立进程可对同一状态提交只读事件。"""
    root = Path(__file__).resolve().parents[2]
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

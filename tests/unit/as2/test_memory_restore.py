"""共同检查点恢复所需的人物记忆严格校验测试。"""

import json
from datetime import datetime, timezone

import pytest

from agentsociety2.agent.memory import AgentMemoryStore


def _episode(memory_type: str = "observation") -> dict:
    """构造一条合法的正式记忆输入。"""
    return {
        "type": memory_type,
        "importance": 0.5,
        "keywords": ["wait"],
        "text": "I waited for one minute.",
        "source": "step_result",
        "refs": [],
    }


def test_existing_memory_validates_without_changing_files(tmp_path) -> None:
    """合法记忆能够重开，严格校验不修改任何持久内容。"""
    store = AgentMemoryStore(tmp_path, agent_id=1)
    store.append_episodes(
        [_episode(), _episode("commitment")],
        tick=60,
        t=datetime(2026, 1, 5, tzinfo=timezone.utc),
        step_count=1,
    )
    before = {path: path.read_bytes() for path in (
        store.memory_md_path, store.episodes_path, store.state_path,
    )}

    restored = AgentMemoryStore(tmp_path, agent_id=1)
    restored.validate_existing()

    recent = restored.recent(limit=2)
    assert [item["id"] for item in recent] == [
        "m_00000060_000001",
        "m_00000060_000002",
    ]
    assert recent[1]["type"] == "commitment"
    assert before == {path: path.read_bytes() for path in before}


def test_corrupt_memory_state_is_not_replaced_with_defaults(tmp_path) -> None:
    """损坏 state.json 直接失败且原始字节保持不变。"""
    store = AgentMemoryStore(tmp_path, agent_id=1)
    store.ensure()
    damaged = b'{"schema_version":1}'
    store.state_path.write_bytes(damaged)

    with pytest.raises(ValueError, match="schema"):
        store.validate_existing()

    assert store.state_path.read_bytes() == damaged


def test_malformed_episode_is_not_silently_skipped(tmp_path) -> None:
    """episodes.jsonl 任一坏行使恢复失败而非丢失经历。"""
    store = AgentMemoryStore(tmp_path, agent_id=1)
    store.ensure()
    state = store.default_state()
    state["episode_counter"] = 1
    store.write_state(state)
    store.episodes_path.write_text(json.dumps({"id": "incomplete"}) + "\n")

    with pytest.raises(ValueError, match="invalid schema"):
        store.validate_existing()

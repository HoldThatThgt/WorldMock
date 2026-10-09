from tests.integration.runtime.current_contract_fixtures import profile as current_profile
"""公开接口行为补齐：人物按 ISO 时间闭区间读取记忆（memory_time_range）的请求校验。

`MemoryTimeRangeRequest.validate_interval` 此前只被反向区间与缺时区两种拒绝路径执行过；这里补上
受理路径：区间两端按带时区的绝对时刻比较（不是按字符串），端点相同的闭区间有效，受理后原样转交
记忆运行时的区间查询；不可能的日期与反向区间在转交之前拒绝。
"""


import copy
import os

import pytest
from pydantic import ValidationError

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactLoopRequest
from custom.agents.life_agent import LifeAgent, MemoryTimeRangeRequest
from tests.integration.runtime.test_life_agent_protocol import _context, _observations


#: 记忆运行时里带内部身份与来源的一条已存条目。
_STORED = {"id": "m_00000060_000001", "agent_id": 7, "type": "goal", "text": "想早点睡",
           "time": "2026-01-04T23:30:00+08:00", "importance": 0.4, "keywords": ["睡觉"],
           "source": "activity_conclusion", "refs": [], "epistemic_status": "subjective"}


class _RecordingMemory:
    """记录宿主转交给记忆运行时的查询，并返回一条带内部身份的已存条目。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def dispatch_tool(self, action: str, args: dict) -> tuple[bool, str, dict]:
        """按记忆运行时的工具协议应答：成功标记、观察文本与结构化结果。"""
        self.calls.append((action, dict(args)))
        return True, "{}", {"episodes": [dict(_STORED)]}


def _person(memory: _RecordingMemory) -> LifeAgent:
    """通过正式入口冻结一次决策的当前观察，并接上记录用的记忆运行时。"""
    person = LifeAgent()
    person._profile = current_profile()
    person.prepare_react_loop(ReactLoopRequest(0, _context().observed_at, copy.deepcopy(_observations()), None, False))
    person._memory_runtime = memory
    return person


def test_time_range_compares_absolute_instants_not_strings() -> None:
    """闭区间按带时区的绝对时刻比较：跨时区书写、字面上「倒序」的有序区间受理，端点为同一时刻的闭区间有效；
    字面上「正序」但实际反向的区间与不存在的日期拒绝。"""
    ordered = MemoryTimeRangeRequest(start_time="2026-01-04T23:00:00+08:00", end_time="2026-01-04T16:00:00Z",
                                     limit=5)
    assert (ordered.start_time, ordered.end_time, ordered.limit) == (
        "2026-01-04T23:00:00+08:00", "2026-01-04T16:00:00Z", 5)
    instant = MemoryTimeRangeRequest(start_time="2026-01-04T22:00:00+08:00", end_time="2026-01-04T14:00:00Z",
                                     limit=1)
    assert instant.limit == 1
    with pytest.raises(ValidationError, match="memory time range is reversed"):
        MemoryTimeRangeRequest(start_time="2026-01-04T16:30:00Z", end_time="2026-01-04T23:59:00+08:00", limit=5)
    with pytest.raises(ValidationError, match="requires ISO datetimes"):
        MemoryTimeRangeRequest(start_time="2026-02-30T08:00:00+08:00", end_time="2026-03-01T08:00:00+08:00",
                               limit=5)


@pytest.mark.asyncio
async def test_memory_time_range_tool_forwards_a_valid_interval_and_hides_identities() -> None:
    """人物调用 memory_time_range：有效区间原样转交记忆运行时的区间查询，结果只投影 {type, text, time, epistemic_status}；
    反向区间在转交之前拒绝，记忆运行时不被调用。"""
    memory = _RecordingMemory()
    person = _person(memory)
    window = {"start_time": "2026-01-04T20:00:00+08:00", "end_time": "2026-01-04T16:00:00Z", "limit": 5}
    result = await person.dispatch_react_tool("memory_time_range", window)
    assert memory.calls == [("memory_range", window)]
    assert result.ok is True
    assert result.data == {"epistemic_status": "subjective", "episodes": [
        {"type": "goal", "text": "想早点睡", "time": "2026-01-04T23:30:00+08:00", "epistemic_status": "subjective"}]}
    assert _STORED["id"] not in result.observation
    with pytest.raises(ValidationError, match="memory time range is reversed"):
        await person.dispatch_react_tool("memory_time_range", {**window, "end_time": "2026-01-04T11:59:59Z"})
    assert len(memory.calls) == 1

"""@brief 用真实宿主流程录制查看器请求，验证当前工具与同秒续接。"""

from pathlib import Path
import json

import pytest

from tests.engine_fixture import engine_factory as _engine_factory  # noqa: F401
from tests.integration.runtime.current_contract_fixtures import ToolResponse, decide
from tests.life.conftest import SIDE_TOOL, side_arguments, stage_day_plan


async def _recorded_viewer_requests(factory):
    """@brief 真实人物先修订计划，再通过同秒续接打开哔哩哔哩（prefer 为 null）。"""
    engine = factory()
    stage_day_plan(engine, submitted=True)
    first = await decide(engine, "viewer-plan", [ToolResponse("revise_day_plan", {
        "goals": [{"intent_kind": "休息", "goal": "看一会儿哔哩哔哩", "remind_time": None,
                   "around_time": None}]}, call_id="viewer-plan")])
    second = await decide(engine, "viewer-browse", [ToolResponse(SIDE_TOOL,
        side_arguments("interact", "哔哩哔哩"), call_id="viewer-browse")])
    return {"action": first[1][0], "continuation": second[1][0]}, engine


def _layer_names(request: dict) -> list[str]:
    """@brief 请求首个 user 消息的用户层层名，按层序。"""
    user = next(message for message in request["messages"] if message["role"] == "user")
    return [layer["layer"] for layer in json.loads(user["content"])]


def _last_changes(request: dict) -> dict:
    """@brief 续接请求最后一条 tool 消息的 changes。"""
    tool = [message for message in request["messages"] if message["role"] == "tool"][-1]
    return json.loads(tool["content"])["changes"]


@pytest.mark.asyncio
async def test_viewer_fixture_is_recorded_from_the_current_host(_engine_factory):
    """@brief 冻结请求逐字保留当前真实宿主的系统层、工具、用户层的层序及续接消息与 changes。"""
    requests, engine = await _recorded_viewer_requests(_engine_factory)
    fixture = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "viewer-context.json"
    recorded = json.loads(fixture.read_text(encoding="utf-8"))
    assert recorded["action"]["messages"][0] == requests["action"]["messages"][0]
    assert recorded["action"]["tools"] == requests["action"]["tools"]
    assert recorded["continuation"]["messages"][:2] == recorded["action"]["messages"]
    assert requests["continuation"]["messages"][:2] == requests["action"]["messages"]
    # 用户层的层序与续接 changes 的键和 memory.added（上一决策的今天条目）都与宿主此刻的请求相同。
    assert _layer_names(recorded["action"]) == _layer_names(requests["action"])
    recorded_changes, host_changes = _last_changes(recorded["continuation"]), _last_changes(requests["continuation"])
    assert list(recorded_changes) == list(host_changes)
    assert recorded_changes["memory"]["added"] == host_changes["memory"]["added"]
    # 录制样例里的工具清单：八个工具各出现一次，含两个开始工具与唯一的记忆检索工具 search_memory，不含已删除的
    # query_world、start_activity 与三个 memory_* 工具。
    listed = [tool["function"]["name"] for tool in recorded["action"]["tools"]]
    names = set(listed)
    assert len(listed) == len(names) == 8
    assert {"start_main_activity", "start_side_activity", "report_status", "pause_activity", "conclude_activity",
            "revise_day_plan", "recall_task_summary", "search_memory"} <= names
    assert names.isdisjoint({"query_world", "start_activity", "set_alarm", "send_message", "submit_work",
                             "memory_recent", "memory_search", "memory_time_range"})
    events = list(engine.context.store.iter_events(engine.context.store.last_event_seq))
    assert not any(event.event_type == "action_rejected" for event in events)
    activity = next(iter(engine.context.state.activities.values()))
    assert activity.kind.value == "interact" and activity.data["app_id"] == "bilibili"
    assert activity.data["content_id"]

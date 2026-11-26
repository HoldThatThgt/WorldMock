"""@brief 用真实宿主流程录制查看器请求，验证当前 11 个工具与循环内多回合沙盒消息。"""

from pathlib import Path
import json

import pytest

from tests.engine_fixture import engine_factory as _engine_factory  # noqa: F401
from tests.integration.runtime.current_contract_fixtures import ToolResponse, decide
from tests.life.conftest import SIDE_TOOL, side_arguments, stage_day_plan
from worldmock.configuration.model.model_client import REQUEST_KEYS


async def _recorded_viewer_requests(factory):
    """@brief 真实人物在一次唤醒循环内先修订当日计划，再收到沙盒差量并打开哔哩哔哩（prefer 为 null）。"""
    engine = factory()
    stage_day_plan(engine, submitted=True)
    _, model_requests, _, _ = await decide(engine, "viewer-loop", [
        ToolResponse("revise_day_plan", {
            "goals": [{"intent_kind": "休息", "goal": "看一会儿哔哩哔哩", "remind_time": None,
                       "around_time": None}]}, call_id="viewer-plan"),
        ToolResponse(SIDE_TOOL, side_arguments("interact", "哔哩哔哩"), call_id="viewer-browse"),
    ])
    assert len(model_requests) == 2
    return {"action": model_requests[0], "in_loop": model_requests[1]}, engine


def _layer_names(request: dict) -> list[str]:
    """@brief 请求首个 user 消息的用户层层名，按层序。"""
    user = next(message for message in request["messages"] if message["role"] == "user")
    return [layer["layer"] for layer in json.loads(user["content"])]


def _last_changes(request: dict) -> dict:
    """@brief 循环内后续请求最后一条 tool 消息的 changes。"""
    tool = [message for message in request["messages"] if message["role"] == "tool"][-1]
    return json.loads(tool["content"])["changes"]


@pytest.mark.asyncio
async def test_viewer_fixture_is_recorded_from_the_current_host(_engine_factory):
    """@brief 冻结请求逐字保留当前真实宿主的系统层、11 个工具、用户层层序与循环内消息及 changes。"""
    requests, engine = await _recorded_viewer_requests(_engine_factory)
    try:
        fixture = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "viewer-context.json"
        recorded = json.loads(fixture.read_text(encoding="utf-8"))
        # 两个请求字典恰为模型客户端收的六个键，次序与宿主此刻构造的相同；没有从未发出的参数。
        assert list(recorded["action"]) == list(recorded["in_loop"]) == list(REQUEST_KEYS)
        assert list(requests["action"]) == list(requests["in_loop"]) == list(REQUEST_KEYS)
        assert recorded["action"]["messages"][0] == requests["action"]["messages"][0]
        assert recorded["action"]["tools"] == requests["action"]["tools"]
        assert recorded["in_loop"]["messages"][:2] == recorded["action"]["messages"]
        assert requests["in_loop"]["messages"][:2] == requests["action"]["messages"]
        assert [message["role"] for message in requests["in_loop"]["messages"]] == [
            "system", "user", "assistant", "tool"]
        # 用户层的层序与循环内 changes（待办层变化）都与宿主此刻的请求相同。
        assert _layer_names(recorded["action"]) == _layer_names(requests["action"])
        recorded_changes, host_changes = _last_changes(recorded["in_loop"]), _last_changes(requests["in_loop"])
        assert list(recorded_changes) == list(host_changes) == ["待办层"]
        assert recorded_changes["待办层"] == host_changes["待办层"]
        # 录制样例里的工具清单：11 个工具各出现一次，含 continue_activity、lookup_targets、search_world 与 search_memory。
        listed = [tool["function"]["name"] for tool in recorded["action"]["tools"]]
        names = set(listed)
        assert len(listed) == len(names) == 11
        assert {"start_main_activity", "start_side_activity", "continue_activity", "search_world", "report_status",
                "pause_activity", "conclude_activity", "revise_day_plan", "recall_task_summary", "search_memory",
                "lookup_targets"} == names
        assert names.isdisjoint({"query_world", "start_activity", "set_alarm", "send_message", "submit_work",
                                 "memory_recent", "memory_search", "memory_time_range"})
        events = list(engine.context.store.iter_events(engine.context.store.last_event_seq))
        assert not any(event.event_type == "action_rejected" for event in events)
        activity = next(iter(engine.context.state.activities.values()))
        assert activity.kind.value == "interact" and activity.data["app_id"] == "bilibili"
        assert activity.data["content_id"]
    finally:
        engine.close()

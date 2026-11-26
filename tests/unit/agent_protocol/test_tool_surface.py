"""@brief 工具面一致性：人物侧开放的世界请求工具与只读查询工具，和引擎侧的请求模型与只读查询模型是同一份；运行时不再核对，由本用例守住（docs/design/06-agent.md「提示与工具」）。"""

from worldmock.agents.life_agent import life_action_tool_names, life_query_tool_names
from worldmock.state.interaction.requests import QUERY_MODELS, REQUEST_MODELS


def test_agent_and_engine_share_one_tool_surface():
    """请求模型恰为九个工具名，查询模型为两个工具名；人物侧动作工具集与查询工具集与引擎一致。"""
    assert frozenset(REQUEST_MODELS) == frozenset({
        "start_main_activity",
        "start_side_activity",
        "search_world",
        "continue_activity",
        "recall_task_summary",
        "report_status",
        "pause_activity",
        "conclude_activity",
        "revise_day_plan",
    })
    assert life_action_tool_names() == frozenset(REQUEST_MODELS)
    assert life_query_tool_names() == frozenset({"recall_task_summary", "search_memory", "lookup_targets"})
    assert frozenset(QUERY_MODELS) == {"search_memory", "lookup_targets"}
    assert "recall_task_summary" in REQUEST_MODELS

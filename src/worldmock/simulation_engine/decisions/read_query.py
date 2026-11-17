"""@brief 引擎的只读查询：在本次决策的冻结观察上回答回看任务与检索记忆，不写事件、不改世界（docs/design/06-agent.md「提示与工具」）。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from worldmock.foundation.errors import BusinessRuleError, ProtocolError
from worldmock.simulation_engine.actions.dispatcher import ActionHandlers
from worldmock.simulation_engine.decisions.decision_coordinator import DecisionCoordinator
from worldmock.simulation_engine.observation.memory import search
from worldmock.simulation_engine.observation.wording.guidance import query_rejection
from worldmock.simulation_engine.world.context import EngineContext
from worldmock.state.interaction.requests import RecallTaskSummaryRequest, SearchMemoryRequest

if TYPE_CHECKING:
    from worldmock.foundation.models import FrozenModel
    from worldmock.state.decisions.round import DecisionContext

#: 只读查询的白名单：工具名 → 请求类型（docs/design/06-agent.md「提示与工具」）；
#: 由引擎一侧显式写出，与人物侧只读查询工具集的一致由单元测试核对。
_READ_QUERY_MODELS = {"recall_task_summary": RecallTaskSummaryRequest, "search_memory": SearchMemoryRequest}


class ReadQueryResponder:
    """@brief 只读查询的核对与回答；只读本次决策的冻结观察、它之前的事件与冻结输入。"""

    def __init__(self, context: EngineContext, decisions: DecisionCoordinator, actions: ActionHandlers):
        """@brief 绑定引擎上下文、决策协调与动作处理。"""
        self.context, self.decisions, self.actions = context, decisions, actions

    def answer(self, context: DecisionContext, tool: str, request: FrozenModel) -> dict:
        """@brief 依次核对决策上下文、开放轮、白名单与唯一冻结观察，再回答回看任务或检索记忆；不写事件、不改世界，
        业务拒绝以五字段形状返回，其余异常原样传播。"""
        self.decisions.validate_context(context)
        self.decisions.require_round(context.round_id)
        if type(request) is not _READ_QUERY_MODELS.get(tool):
            logging.error("Read query tool and request type do not match: %s", tool)
            raise ProtocolError("Read query tool and request type do not match")
        rows = self.context.store.read_decision_observations(
            context.agent_id, context.decision_id, int(context.observed_at.timestamp()))
        if len(rows) != 1:
            logging.error("Read query requires one frozen decision observation, found %d", len(rows))
            raise ProtocolError("Read query requires one frozen decision observation")
        try:
            if tool == "recall_task_summary":
                return self.actions.read_task_summary(context.agent_id, request, rows[0].data)
            return search.search_memory(self.context, context.agent_id, request, rows[0])
        except BusinessRuleError as error:
            return query_rejection(error)

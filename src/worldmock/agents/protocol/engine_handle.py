"""@brief 人物决策与搜索子会话调用引擎能力的抽象协议边界。"""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from pydantic import BaseModel

from worldmock.state.decisions.barrier import SandboxOutcome, ToolDispatchResult
from worldmock.state.decisions.day_session import DaySessionState
from worldmock.state.decisions.suspended import SuspendedLoopState
from worldmock.state.model.exchange import ModelCallEvidence


class SandboxHandle(Protocol):
    """@brief SimulationEngine 为单次人物唤醒提供的沙盒状态与工具调度句柄。"""

    def search_memory(self, request: BaseModel) -> dict[str, Any]:
        """@brief 执行记忆检索只读查询。"""

    def recall_task_summary(self, request: BaseModel) -> dict[str, Any]:
        """@brief 读取当前同一活动最近一次阶段摘要。"""

    def lookup_targets(self, request: BaseModel | Mapping[str, Any]) -> dict[str, Any]:
        """@brief 零耗时只读检索工具层未常驻展开的地点、内容应用与商品条目。"""

    def dispatch_world_tool(
        self,
        tool_name: str,
        arguments: BaseModel | Mapping[str, Any],
        call_id: str,
        note: str | None = None,
    ) -> ToolDispatchResult:
        """@brief 在沙盒内调度单次世界控制或行动工具，返回即时结算结果。"""

    def record_model_exchange(self, exchange: ModelCallEvidence) -> None:
        """@brief 在当前沙盒证据缓冲中登记一次模型请求、响应或失败。"""

    def compact_snapshot(self) -> dict[str, Any]:
        """@brief 清空沙盒已载入目标键集并返回最新五层与进度元数据用于日内 Compaction。"""

    def finish_wakeup(
        self,
        suspended: SuspendedLoopState | None,
        day_session: DaySessionState,
        compaction_records: tuple[dict[str, Any], ...] = (),
    ) -> SandboxOutcome:
        """@brief 封口本次唤醒沙盒、补全日会话状态并返回不可变的唤醒产出对象。"""


class SearchCatalogHandle(Protocol):
    """@brief SimulationEngine 为异步搜索子会话绑定的只读目录检索与 Fact Guard 句柄。"""

    def search_goods(self, request: BaseModel) -> dict[str, Any]:
        """@brief 在绑定地点与时间视角下按关键词与过滤条件检索候选商品。"""

    def inspect_goods_offers(self, request: BaseModel) -> dict[str, Any]:
        """@brief 批量查询指定商品编号的实时权威报价与门店营业状态。"""

    def search_places(self, request: BaseModel) -> dict[str, Any]:
        """@brief 在当前域与同城可到达地点目录中检索候选地点与出行选项。"""

    def validate_submission(self, request: BaseModel) -> dict[str, Any]:
        """@brief 核验子会话提交的商品与地点编号全部来自本轮真实检索结果且未越界。"""

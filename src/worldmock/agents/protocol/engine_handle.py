"""@brief 人物接触引擎的唯一句柄：五个同步方法，名字与参数名同 LifeEngine 的同名方法。

运行宿主把 LifeEngine 本身交给 LifeAgent；人物模块不导入引擎，只按这个协议调用，调用时不 await。
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel

from worldmock.recording_engine.events.memory_evidence import MemoryUpdateEvidence
from worldmock.state.decisions.results import ActionReceipt
from worldmock.state.decisions.round import DecisionContext, DirectToolCall
from worldmock.state.model.exchange import ModelCallEvidence


class EngineHandle(Protocol):
    """@brief 人物一侧声明的引擎句柄：观察、登记请求、只读查询与两类证据写入。"""

    def observe(self, context: DecisionContext) -> dict[str, Any]:
        """@brief 取本次决策唯一的一份冻结观察（深拷贝）。"""

    def submit_action(self, call: DirectToolCall) -> ActionReceipt:
        """@brief 登记一条宿主补齐身份的世界请求，返回受理回执。"""

    def read_query(self, context: DecisionContext, tool: str, request: BaseModel) -> dict[str, Any]:
        """@brief 按本次决策的冻结观察当场回答一个只读查询（深拷贝）。"""

    def record_model_exchange(self, exchange: ModelCallEvidence) -> None:
        """@brief 写入一份人物模型调用证据。"""

    def record_memory_update(self, evidence: MemoryUpdateEvidence) -> None:
        """@brief 登记步尾已落盘记忆的内容身份。"""

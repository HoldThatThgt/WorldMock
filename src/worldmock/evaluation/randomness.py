"""@brief 评估只从认证 WorldMock 检查点取得随机根。"""

from typing import Any, Mapping

from worldmock.evaluation.errors import EvaluationError


def checkpoint_random_state(state: Mapping[str, Any]) -> str:
    """@brief 拒绝没有真实随机根的检查点，不从配置补造种子。"""
    value = state.get("random_state")
    if not isinstance(value, str) or not value:
        raise EvaluationError("invalid_input", "WorldMock checkpoint random_state is missing")
    return value

"""@brief 将领域拒绝定位回原模型请求，保留真实公开条件与完整错误合同。"""

from __future__ import annotations

from typing import Any

from worldmock.life.error_contract import ErrorItem, ErrorReport


def business_feedback(request: dict, code: str, guidance: dict, unmet: list[str], observation: dict) -> dict:
    """@brief 已有精确错误保持原样；领域条件拒绝补真实参数位置、原值和明确条件。"""
    items = guidance.get("items")
    if items:
        return {"error_code": code, "reason": guidance["reason"], "fix": guidance.get("fix"),
                "items": items, "unmet": unmet}
    fields = _affected_fields(request, code)
    details = tuple(_domain_item(request, code, guidance["reason"], observation, path) for path in fields)
    return ErrorReport(code, guidance["reason"], guidance.get("fix"), details, tuple(unmet)).payload()


def _affected_fields(request: dict, code: str) -> list[tuple[str | int, ...]]:
    """@brief 依据工具实际结构和业务条件定位，不暴露内部价格、身份或时长。"""
    if isinstance(request.get("items"), list):
        paths = []
        for index, item in enumerate(request["items"]):
            field = "target" if "target" in item else "activity_name"
            paths.append(("items", index, field))
        return paths
    return [("task_name" if "task_name" in request else "goals",)]


def _domain_item(request: dict, code: str, reason: str, observation: dict,
                 path: tuple[str | int, ...]) -> ErrorItem:
    """@brief 每项绑定原 JSON 值，补公开任务合同条件；对象名称不冒充静态枚举。"""
    written: Any = request
    for segment in path:
        written = written[segment]
    expected = reason
    allowed = ()
    return ErrorItem(path=path, written=written, error_code=code, allowed_values=allowed,
                     expected=expected, why=reason)

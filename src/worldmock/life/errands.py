"""@brief 个人事务的全部逻辑（docs/dev/SPEC-errands-workday.md 第二章）：私有绑定与待办层行。

全部是纯函数：不读写存储、不访问引擎。状态模型与常量在 errand_state.py（状态与逻辑分两个模块）；本模块可依赖
models、名称解析与错误合同，models.py 从不导入本模块。

- 私有绑定（2.3）：`errand_bindings` 列本人未完成事务的名称、事务身份、结果种类与商品身份（待办层顺序），
  `errand_item_bindings` 列全部商品名到商品身份（按商品身份排序）；两者与 `errand_id` 都是提示禁键，事务名不进回看名称。
- 待办层行（2.6）：按记下的时刻与事务身份排序，每行六键 name、done_when、progress、memo、remind_time、noted_on；
  不含内部身份、状态枚举与可用性。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from worldmock.life.errand_state import OUTCOME_WORDS, ErrandState
from worldmock.life.models import AgentState
from worldmock.life.readable import local_date, portion_phrase
from worldmock.life.schedule import epoch_clock


def bindings(agent: AgentState, items: Mapping[str, Any]) -> dict:
    """@brief 本次观察的两类事务私有绑定：本人未完成事务（待办层顺序）与全部商品名（按商品身份排序）。

    items 是冻结目录的可持有对象（商品身份 → 带 name 的对象，LifeRuleEngine.items）；商品名本就可按名调用，列全量不扩大
    权限。宿主与环境对同一份冻结观察里的这两张表做同一校验。
    """
    return {
        "errand_bindings": [{"name": errand.name, "errand_id": errand.errand_id, "outcome": errand.outcome,
                             "product_id": errand.product_id} for errand in _open_errands(agent)],
        "errand_item_bindings": [{"name": items[product_id].name, "product_id": product_id}
                                 for product_id in sorted(items)],
    }


def todo_rows(agent: AgentState, product_names: Mapping[str, str], timezone_name: str) -> list[dict]:
    """@brief 待办层 errands：本人未完成事务按记下的时刻与事务身份排序，每条一行（product_names：商品身份 → 商品名）。"""
    return [_row(errand, product_names, timezone_name) for errand in _open_errands(agent)]


def _open_errands(agent: AgentState) -> list[ErrandState]:
    """@brief 本人的未完成事务，按记下的时刻、再按事务身份排序（待办层与私有绑定的同一顺序）。"""
    return sorted(agent.errands.values(), key=lambda errand: (errand.registered_at, errand.errand_id))


def _row(errand: ErrandState, product_names: Mapping[str, str], timezone_name: str) -> dict:
    """@brief 一条事务的待办层行：名称、要办成的事（「取得<商品名> N 份」）、已办到哪（「已取得 N 份」，尚无进展写 0 份）、
    备注、尚未提醒的提醒钟点（没有或已提醒为 null）与记下的本地日期。"""
    word = OUTCOME_WORDS[errand.outcome]
    pending = errand.remind_at is not None and errand.reminded_at is None
    return {"name": errand.name,
            "done_when": f"{word}{product_names[errand.product_id]} {portion_phrase(errand.quantity)}",
            "progress": f"已{word} {portion_phrase(errand.progress)}",
            "memo": errand.memo,
            "remind_time": epoch_clock(errand.remind_at, timezone_name) if pending else None,
            "noted_on": local_date(errand.registered_at, timezone_name)}

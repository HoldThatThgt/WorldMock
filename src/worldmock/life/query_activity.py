"""@brief 查询活动的受理与做完：时长、这几分钟在前台的 app、活动数据与活动名，以及做完那一秒写下答案并自己结束。

规则见 docs/design/04-simulation-engine.md「出行、内容与查询」；答案怎样算见 life/query_answer.py。开始组与时钟推进只调用
这里的 start_query、complete_query，不在自己的分支里写查询的规则。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from worldmock.exchange.types import GOODS_CATEGORY_LABELS
from worldmock.life import activity_control
from worldmock.life.action_contract import ACTION_LABELS
from worldmock.life.activities import ActivityStartParams, schedule_feedback, start_activity
from worldmock.life.error_contract import ErrorItem
from worldmock.life.guidance import Refusal
from worldmock.life.models import ActivityState, PendingAction, WorldState
from worldmock.life.order_apps import selling_app_ids
from worldmock.life.query_answer import queried_goods, query_answer
from worldmock.life.randomness import random_stream
from worldmock.life.world_ops import _activity_origin, _put_activity

#: 查询时长的下限（整数秒，取得到）。
QUERY_SECONDS_MIN = 300
#: 查询时长的上限（整数秒，取得到）。
QUERY_SECONDS_MAX = 600
#: 查询写错名称时允许的类别名，按 GOODS_CATEGORY_LABELS 的声明次序。
_CATEGORY_NAMES = tuple(GOODS_CATEGORY_LABELS.values())


def query_seconds(random_state: str, action_id: str) -> int:
    """@brief 受理时抽一次查询时长：按 query-duration:{action_id} 的随机流在 300 到 600 秒之间取整数，两端都能取到。

    :param random_state: 世界的随机状态。
    :param action_id: 这条查询请求的动作身份。
    """
    return random_stream(random_state, f"query-duration:{action_id}").randint(QUERY_SECONDS_MIN, QUERY_SECONDS_MAX)


def query_app(catalog, random_state: str, action_id: str, goods_ids: Sequence[str],
              prefer_app_id: str | None) -> tuple[str | None, bool]:
    """@brief 这几分钟在前台的 app 与答案是否只列经它能买的。

    意向 app 卖所查商品里的至少一件就用它，答案只列经它能买的；否则在卖其中至少一件的下单 app 里（按 app_id 升序）
    按 query-app:{action_id} 等概率抽一个，答案列出全部途径；没有任何 app 卖所查商品时手机上没有 app。

    :param goods_ids: 所查商品。
    :param prefer_app_id: 从人物所写 prefer 认出的意向 app；没有意向时为 None。
    :return: （app 身份或 None，答案是否只列经它能买的）。
    """
    selling = selling_app_ids(catalog, goods_ids)
    if prefer_app_id in selling:
        return prefer_app_id, True
    if not selling:
        return None, False
    return random_stream(random_state, f"query-app:{action_id}").choice(selling), False


def query_targets(rows: Sequence[Mapping[str, Any]]) -> list[dict]:
    """@brief 名称已解析的各行写成活动数据里的 targets：按所写次序，同一件商品、同一个类别只留第一次出现的那一项。

    :param rows: 这条查询调用解析出的各行：商品行带 object_id，类别行带 category。
    """
    targets: list[dict] = []
    for row in rows:
        item = ({"kind": "category", "category": row["category"]} if row["kind"] == "category"
                else {"kind": "goods", "goods_id": row["object_id"]})
        if item not in targets:
            targets.append(item)
    return targets


def goods_not_found(call_index: int, position: int, written: str) -> Refusal:
    """@brief 查询数组里一项既不是类别、也找不到唯一商品：SUPPLY_NOT_FOUND，原因列出四个类别名，不给候选。

    :param call_index: 这条查询调用在本决策已登记动作中的位置。
    :param position: 这一项在人物所写数组里的下标（去重之前）。
    :param written: 所写的那个名称。
    """
    reason = f"No goods or goods category is named {written}; the goods categories are {', '.join(_CATEGORY_NAMES)}."
    item = ErrorItem(("tool_calls", call_index, "function", "arguments", "target", position), written,
                     "SUPPLY_NOT_FOUND", _CATEGORY_NAMES,
                     "An exact known goods name or one of the goods categories is required.", reason)
    return Refusal("SUPPLY_NOT_FOUND", reason, "Choose a known product or one of the goods categories.",
                   fix="Use an exact known goods name or one of the goods categories.", items=(item,))


def _target_name(catalog, item: Mapping[str, str]) -> str:
    """@brief 一项的规范名称：商品的完整名称，或类别的中文名。"""
    if item["kind"] == "category":
        return GOODS_CATEGORY_LABELS[item["category"]]
    return catalog.goods[item["goods_id"]].name


def start_query(context, state: WorldState, pending: PendingAction, call) -> WorldState:
    """@brief 受理一条查询：定下时长、这几分钟的 app 与答案范围，经 start_activity 建活动并登记到世界副本。

    活动名是「查询」加各项的规范名称（以「、」相连，不截断），再加开始时刻。

    :param context: 引擎上下文（冻结目录、身体规则与运行配置）。
    :param call: 名称已解析、带着意向 app（prefer）的这条查询调用。
    """
    catalog = context.catalog
    targets = query_targets(call.rows)
    app_id, scoped = query_app(catalog, state.random_state, pending.action_id, queried_goods(catalog, targets),
                               call.prefer)
    params = ActivityStartParams("query", query_seconds(state.random_state, pending.action_id),
                                 data={"targets": targets, "app_id": app_id, "scoped": scoped})
    agent, activity = start_activity(state.agents[pending.agent_id], params, state.current_time,
                                     _activity_origin(state, pending), context.body)
    names = "、".join(_target_name(catalog, item) for item in targets)
    name = activity_control.activity_name(ACTION_LABELS["query"], names, state.current_time, state.timezone)
    activity = schedule_feedback(activity.model_copy(update={"name": name}), state.current_time,
                                 context.config.activity_feedback_seconds)
    return _put_activity(state, agent, activity)


def complete_query(inputs, state: WorldState, activity: ActivityState) -> ActivityState:
    """@brief 查询走满时长的那一秒：按这一秒的世界算出答案写进活动数据，并把 concluded_at 写成结束时刻，自己结束、让出槽位 2。

    :param inputs: 完整环境输入。
    :param state: 时钟已到这一秒的世界副本。
    :param activity: 刚走满时长、状态为已完成的查询活动。
    :return: 带答案、已结束的查询活动。
    """
    answer = query_answer(inputs, state, activity)
    return activity.model_copy(update={"data": {**activity.data, "answer": answer},
                                       "concluded_at": activity.ended_at}, deep=True)

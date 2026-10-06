"""@brief 按名调用的私有生活活动目录绑定与本人住处身份。

曝光轮转只决定本轮展示对象，启用的生活活动仍可直接写名称。每项记录名称、类别、身份、方式、受理拒绝原因与时长；
受理仍使用工具层同一准入判定。应用已始终列为目标，不增加目录绑定。
"""

from __future__ import annotations

from typing import Any

from worldmock.life.durations import capped_duration_seconds
from worldmock.life.vocabulary import WAYS
from worldmock.life.interaction_menu import binding_action
from worldmock.life.object_labels import object_label
from worldmock.life.readable import duration_phrase
from worldmock.life.tool_layer import Candidate, LayerContext, refusal_for

#: 生活活动的方式。
_DO_WAY = "进行"


def name_bindings(context: LayerContext, lifestyle: tuple, home_location_id: str) -> dict[str, Any]:
    """@brief 观察的按名调用私有绑定：目录绑定（本轮没有列出的本人启用的生活活动）与住处的地点身份。

    :param context: 本次观察的层上下文（bindings 为本轮列出的交互对象，名称已在其中的不再重复）。
    :param lifestyle: 本人启用集内的生活活动目录条目。
    :param home_location_id: 人物的住处（「家」「住处」按它解析）。
    """
    listed = {row["object"] for row in context.bindings}
    rows = [*_lifestyle_rows(context, lifestyle), *_work_topic_rows(context)]
    return {"catalog_bindings": [row for row in rows if row["object"] not in listed],
            "home_location_id": home_location_id}


def _row(context: LayerContext, entry: tuple[str, str, Any], way: str, note: str | None) -> dict[str, Any]:
    """@brief 目录绑定的一项：名称、类别、身份、方式、单独提交时的第一条拒绝原因（可以做时为 null）与时长说明。

    :param entry: （名称，私有绑定类别，身份）。
    """
    label, kind, identity = entry
    action = binding_action(way, WAYS[way][1])
    binding = {"object": label, "kind": kind, "object_id": identity, "actions": [action]}
    refusal = refusal_for(context, Candidate("start_activity", binding, action))
    return {"object": label, "kind": kind, "object_id": identity, "way": way,
            "reason": refusal.reason if refusal is not None else None, "note": note}


def _lifestyle_rows(context: LayerContext, items: tuple) -> list[dict[str, Any]]:
    """@brief 本人启用的生活活动：方式「进行」，说明为目录典型时长按活动上限封顶的受理秒数。"""
    return [_row(context, (object_label("lifestyle", item.name), "lifestyle", item.activity_id), _DO_WAY,
                 duration_phrase(capped_duration_seconds(item.typical_duration_minutes * 60)))
            for item in sorted(items, key=lambda value: value.activity_id)]


def _work_topic_rows(context: LayerContext) -> list[dict[str, Any]]:
    """@brief 已分配主题的私有名称绑定保留完成后的真实拒绝，不把完成主题列为工具目标。"""
    config = next(item for item in context.scenario.agents if item.agent_id == context.agent_id)
    return [_row(context, (object_label("work_topic", context.rules.work_topics[topic_id].topic),
                           "work_topic", topic_id), "工作", None)
            for topic_id in sorted(config.work_topic_ids)]

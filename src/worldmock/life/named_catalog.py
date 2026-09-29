"""@brief 按名调用的私有绑定（docs/dev/SPEC-dayrun-fidelity.md 3.1 第 4 条）：本轮没有列出、但人物可以直接写名称调用的
生活活动、内容与游戏（catalog_bindings），以及本人住处的地点身份（home_location_id）。

曝光轮转与呈现上限只决定工具层列出哪些对象，不是资格上限：存在但没有列出的生活活动（本人启用集内）、内容与游戏照样可以
写名称，由宿主与环境在同一冻结观察上按 name_resolution 解析；受理时照常做资格与准入判定（地点类不符的生活活动以
ELIGIBILITY_LOCATION_KIND_MISMATCH 拒绝）。每项 {object, kind, object_id, way, reason, note}：名称取 object_labels 的唯一
写法，way 是它此刻会列出的方式（内容按看过的进度取「接着看」「从头再看」等），reason 是单独提交这一项时环境受理的第一条
拒绝原因（与工具层可用性同一判定 tool_layer.refusal_for，可以做时为 null），note 是候选说明的时长（如「约 20 分钟」）。
这些键只在观察私有证据中，不进提示；名称不存在的由宿主判定并给候选（重试不花钱）。
"""

from __future__ import annotations

from typing import Any

from worldmock.life.content import restarts_from_beginning
from worldmock.life.interaction_menu import CONTENT_WAYS, WAYS, binding_action
from worldmock.life.object_labels import object_label
from worldmock.life.presentation import GAME_CATEGORY
from worldmock.life.readable import duration_phrase
from worldmock.life.tool_layer import Candidate, LayerContext, refusal_for

#: 生活活动与游戏的方式（内容按进度另取续看方式）。
_DO_WAY, _PLAY_WAY = "进行", "游玩"


def name_bindings(context: LayerContext, lifestyle: tuple, home_location_id: str) -> dict[str, Any]:
    """@brief 观察的按名调用私有绑定：目录绑定（本轮没有列出的本人启用的生活活动、内容与游戏）与住处的地点身份。

    :param context: 本次观察的层上下文（bindings 为本轮列出的交互对象，名称已在其中的不再重复）。
    :param lifestyle: 本人启用集内的生活活动目录条目。
    :param home_location_id: 人物的住处（「家」「住处」按它解析）。
    """
    listed = {row["object"] for row in context.bindings}
    rows = [*_lifestyle_rows(context, lifestyle), *_content_rows(context), *_game_rows(context)]
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
    """@brief 本人启用的生活活动：方式「进行」，说明为目录的典型时长。"""
    return [_row(context, (object_label("lifestyle", item.name), "lifestyle", item.activity_id), _DO_WAY,
                 duration_phrase(item.typical_duration_minutes * 60))
            for item in sorted(items, key=lambda value: value.activity_id)]


def _content_rows(context: LayerContext) -> list[dict[str, Any]]:
    """@brief 有标题的内容：内容侧的游戏会话按游戏写名称；方式与说明按看过的进度取（看完或只剩不足一分钟时从头，写全长，
    否则接着，写剩余时长；播客写「听」），与观察投影同一口径。"""
    rows = []
    agent = context.state.agents[context.agent_id]
    for item in sorted(context.scenario.contents, key=lambda value: value.item_id):
        title = context.rules.content_titles.get(item.item_id)
        if title is None:
            continue
        progress = agent.content_progress.get(item.item_id)
        position = progress.position_seconds if progress else 0
        restart = restarts_from_beginning(position, item.duration_seconds)
        way = CONTENT_WAYS[(item.kind == "podcast_episode", restart)]
        kind = "game" if item.kind == GAME_CATEGORY else "content"
        seconds = item.duration_seconds if restart else item.duration_seconds - position
        rows.append(_row(context, (object_label(kind, title), kind, item.item_id), way, duration_phrase(seconds)))
    return rows


def _game_rows(context: LayerContext) -> list[dict[str, Any]]:
    """@brief 场景声明的可玩游戏：方式「游玩」，没有时长说明。"""
    return [_row(context, (object_label("game", item.title), "game", item.game_id), _PLAY_WAY, None)
            for item in sorted(context.scenario.games, key=lambda value: value.game_id)]

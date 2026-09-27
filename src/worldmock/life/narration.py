"""@brief 提示侧渲染与字段黑名单：七层 JSON 的规范序列化，以及过渡期保留的场景渲染。

七层请求的每一层都是 JSON 对象（docs/dev/SPEC-persona-prompt.md 第一章「通用格式」）；宿主用
render_layer_json 做唯一的紧凑序列化（键序即构建顺序，不排序，中文不转义），序列化前执行字段黑名单：
内部身份、哈希、账户、数值身体状态、合同全表与私有绑定、进度层私有证据都不得进入提示。本模块是纯函数库，
只依赖标准库。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

#: persona_card 人物卡片汉字上限（「观察投影与提示合同」，合同常数）。
PERSONA_CARD_HANZI_BUDGET = 600

#: 不得进入提示的字面键（「不进入提示的字段」）。activity_snapshot 与 activities 对应"完整活动快照"；
#: affordance_contract 和 interaction_menu 是合同全表；*_bindings 是名称到内部身份的私有绑定；
#: persona_views、progress_meta、progress_event_ids、presented_message_ids 与 continuation 是观察私有证据
#: （docs/dev/SPEC-persona-prompt.md 第一章、第二章「同秒续接」）。七层字段 activity（记忆层 self_notes 与结果
#: outcome 的活动名）是可读名称，不在禁键之列。
FORBIDDEN_OBSERVATION_KEYS = frozenset({
    "world_state_version",
    "end_time",
    "affordance_contract",
    "interaction_menu",
    "interaction_menus_by_location_kind",
    "observation_event_id",
    "motivation_hints",
    "profile.motivation",
    "profile.entertainment_preferences",
    "game_catalog",
    "activity_snapshot",
    "activities",
    "reference_id",
    "reference_assessments",
    "oracle",
    "oracle_path",
    "input_path",
    "expected",
    "accounts",
    "account_id",
    "balance_account_id",
    "balance_fen",
    "action_id",
    "event_id",
    "session_id",
    "activity_id",
    "task_id",
    "route_id",
    "product_id",
    "supply_id",
    "order_id",
    "message_id",
    "contact_id",
    "price_version",
    "resource_changes",
    "energy_kcal",
    "sleep_pressure",
    "sleep_deficit_hours",
    "chronotype_midsleep",
    "sleep_need_hours",
    "taste_weights",
    "agent_id",
    "source_event_id",
    "event_seq",
    "location_id",
    "destination_id",
    "content_id",
    "interaction_bindings",
    "task_bindings",
    "goal_bindings",
    "activity_bindings",
    "session_bindings",
    "message_bindings",
    "persona_views",
    "progress_meta",
    "progress_event_ids",
    "presented_message_ids",
    "continuation",
})

#: 体感分级允许进入提示，同名的数值身体状态禁止进入。
BODY_VALUE_KEYS = frozenset({
    "appetite", "sleepiness", "entertainment_need", "social_need", "mood_valence",
})

#: 嵌套路径禁键：(父键, 子键)。profile 的动机与娱乐偏好不得进入提示。
FORBIDDEN_PROFILE_FIELDS = frozenset({
    ("profile", "motivation"),
    ("profile", "entertainment_preferences"),
})

_WEEKDAY_NAMES = (
    "星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日",
)


class RenderBudgetExceededError(ValueError):
    """@brief 人物卡片超出身份段的汉字上限。"""


class ForbiddenObservationFieldError(ValueError):
    """@brief 渲染输入含黑名单字段（观察投影合同「不进入提示的字段」）。"""


def count_hanzi(text: str) -> int:
    """@brief 统一汉字计数口径：只计 CJK 基本区字符。

    @param text 待统计文本
    @return 汉字个数（标点、数字、拉丁字母不计）
    """
    return sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")


def _close_sentence(text: str) -> str:
    """@brief 给缺句号的断言补句号，保证场景段落每句完整收尾。"""
    return text if text.endswith(("。", "！", "？")) else f"{text}。"


def _time_sentence(observation: dict) -> str:
    """@brief 渲染本地时间与星期，仅披露现有时间合同的当前 epoch 秒。"""
    current_time = observation.get("current_time")
    if current_time is None:
        return ""
    moment = (
        current_time if isinstance(current_time, datetime)
        else datetime.fromisoformat(str(current_time))
    )
    weekday = _WEEKDAY_NAMES[moment.weekday()]
    sentence = (f"现在是 {moment.year} 年 {moment.month} 月 {moment.day} 日"
                f"{weekday} {moment.strftime('%H:%M')}")
    current_epoch = observation.get("current_epoch")
    if isinstance(current_epoch, int):
        sentence += f"（Unix epoch：当前 {current_epoch}）"
    return sentence + "。"


def _location_sentence(observation: dict) -> str:
    """@brief 渲染所在地点与在场的人句；只组装给定字段，不虚构旁人。"""
    location = observation.get("location")
    people = [name for name in (observation.get("people_here") or []) if name]
    if not location and not people:
        return ""
    parts = [f"你在{location}"] if location else []
    if people:
        parts.append(f"{'、'.join(people)}也在")
    return _close_sentence("，".join(parts))


def _felt_sentence(observation: dict) -> str:
    """@brief 渲染体感分级句："你有点饿、略感疲惫。"（值即分级短语）。"""
    bands = [band for band in (observation.get("felt") or {}).values() if band]
    return _close_sentence(f"你{'、'.join(bands)}") if bands else ""


def _activity_sentences(observation: dict) -> str:
    """@brief 渲染当前活动与暂停活动的进度句（只写进度短语，不写快照）。"""
    sentences = []
    for current in observation.get("current_activities", []):
        progress = f"，{current['progress']}" if current.get("progress") else ""
        verb = current.get("verb", "进行")
        sentences.append(_close_sentence(f"你正在{verb}《{current['title']}》{progress}"))
    paused = [item for item in (observation.get("paused_activities") or [])
              if item.get("title")]
    if paused:
        described = "、".join(
            f"《{item['title']}》（{item['progress']}）" if item.get("progress")
            else f"《{item['title']}》" for item in paused)
        sentences.append(_close_sentence(f"之前做到一半的{described}还没收尾"))
    return "".join(sentences)


def _plan_sentence(observation: dict) -> str:
    """@brief 按意图、推进状态与提醒时刻（「提醒 HH:MM」）描述当天目标，不宣称文字目标已完成。"""
    day_plan = observation.get("day_plan")
    if day_plan is None:
        return ""
    goals = day_plan["goals"]
    if not goals:
        return "今天没有刻意目标。"
    labels = [f"{goal['intent_kind']}（{'已推进' if goal['progressed'] else '未推进'}"
              f"{'，' + goal['reminder'] if goal.get('reminder') else ''}）" for goal in goals]
    return _close_sentence(f"今天想推进：{'、'.join(labels)}")


def _inbox_sentence(observation: dict) -> str:
    """@brief 渲染收件箱头句：只列给定的头部条目，不虚构未读数。"""
    head = [line for line in (observation.get("inbox_head") or []) if line]
    latest = _close_sentence(f"收件箱最新消息：{'；'.join(head)}") if head else ""
    older = "更早还有未读消息。" if observation.get("inbox_has_older_unread") else ""
    return latest + older


def _places_sentence(observation: dict) -> str:
    """@brief 渲染可达地点与营业状态句，路线分钟数由事实清单提供。"""
    places = [place for place in (observation.get("reachable_places") or [])
              if place.get("name")]
    if not places:
        return ""
    states = {"open": "营业中", "closed": "未营业", "unknown": "营业信息未知"}
    described = "、".join(
        f"{place['name']}（{states[place['opening_status']]}）" for place in places)
    return _close_sentence(f"可达地点有{described}")


def _inventory_sentence(observation: dict) -> str:
    """@brief 渲染手边持有物句："你手边有手机、工卡、钱包。"。"""
    items = [item for item in (observation.get("inventory") or []) if item]
    return _close_sentence(f"你手边有{'、'.join(items)}") if items else ""


def _precondition_sentence(observation: dict) -> str:
    """@brief 同一地点的工作前置条件只提醒一次。"""
    hints = observation.get("precondition_hints") or []
    sentences = []
    grouped: dict[str, list[str]] = {}
    for hint in hints:
        title = hint.get("task_title") or ""
        loc_name = hint.get("required_location_name") or ""
        if not loc_name:
            continue
        grouped.setdefault(loc_name, []).append(title)
    for loc_name, titles in grouped.items():
        if len(titles) == 1 and titles[0]:
            sentences.append(_close_sentence(f"「{titles[0]}」要在{loc_name}做，先去{loc_name}"))
        else:
            sentences.append(_close_sentence(f"有{len(titles)}项工作要在{loc_name}做，先去{loc_name}"))
    return "".join(sentences)


def _just_ended_sentence(observation: dict) -> str:
    """@brief 渲染刚结束、要先收尾的活动：「刚结束：<活动名>。」（docs/dev/SPEC-time-and-energy.md 第七章）。

    @param observation 引擎侧计算好的观察字段字典（just_ended 为待收尾活动名列表）
    @return 句子文本；没有待收尾活动时返回空串
    """
    names = [name for name in (observation.get("just_ended") or []) if name]
    return _close_sentence(f"刚结束：{'、'.join(names)}") if names else ""


def render_scene(observation: dict) -> str:
    """@brief 渲染中文自然语言场景段落（旧观察形状的过渡渲染）。

    内容依次为时间与星期、所在地点与在场的人、体感分级、当前与暂停活动
    的进度、刚结束待收尾的活动、「刚才」、今天想推进的目标与推进状态、收件箱头、待办一句话、可达地点与营业
    状态、手边持有物、当地餐食句或吃饭途径句，同刻上限的受限轮末尾另加一句说明；
    缺失或为空的字段跳过对应句子，渲染器不虚构事实。

    @param observation 引擎侧计算好的旧观察 scene 字段字典
    @return 场景段落文本
    @exception ForbiddenObservationFieldError observation 含黑名单字段
    """
    enforce_field_blacklist(observation)
    todos = observation.get("todos_summary")
    parts = [
        _time_sentence(observation),
        _location_sentence(observation),
        _felt_sentence(observation),
        _activity_sentences(observation),
        _just_ended_sentence(observation),
        _close_sentence(observation["just_now"]) if observation.get("just_now") else "",
        _plan_sentence(observation),
        _inbox_sentence(observation),
        _close_sentence(todos) if todos else "",
        _places_sentence(observation),
        _inventory_sentence(observation),
        _close_sentence(observation["local_food_summary"]) if observation.get("local_food_summary") else "",
        _precondition_sentence(observation),
        _close_sentence(observation["same_moment_note"]) if observation.get("same_moment_note") else "",
    ]
    scene = "".join(part for part in parts if part)
    return scene


def render_drive_summary(
    felt_bands: dict[str, str],
    work_gap_summary: str,
    todos_summary: str,
) -> str:
    """@brief 由体感分级、今日工作缺口与待办拼成一句中文驱动摘要。

    例："你有点饿，今天的工作还差两小时，晚上想放松一下。"数字权重
    不得出现（「观察投影与提示合同」）：分级与缺口须以文字短语（含中文数字词）传入，
    组装结果含阿拉伯数字时抛 ValueError 如实报告。

    @param felt_bands 维度到体感分级短语的映射（如 {"hunger": "有点饿"}）
    @param work_gap_summary 今日工作缺口文字摘要，可为空串
    @param todos_summary 待办文字摘要，可为空串
    @return 一句中文驱动摘要；三段皆空时返回空串
    @exception ValueError 组装结果含阿拉伯数字（违反无数字权重合同）
    """
    segments = []
    bands = [band for band in felt_bands.values() if band]
    if bands:
        segments.append(f"你{'、'.join(bands)}")
    for summary in (work_gap_summary, todos_summary):
        if summary:
            segments.append(summary)
    if not segments:
        return ""
    text = _close_sentence("，".join(segments))
    digits = [ch for ch in text if ch.isdigit()]
    if digits:
        logging.error("Drive summary contains forbidden digit weights")
        raise ValueError(
            f"drive summary must not contain digit weights: {digits} in {text!r}")
    return text


def _raise_if_forbidden(key: str, path: str, parent_key: str | None) -> None:
    """@brief 按字面键集与 (父键, 子键) 嵌套规则检查单个键，违规即抛错。"""
    if key in FORBIDDEN_OBSERVATION_KEYS or key.endswith("_sha256"):
        logging.error("Forbidden observation field rejected: %s", path)
        raise ForbiddenObservationFieldError(
            f"forbidden observation field {path!r} must not enter the prompt")
    if parent_key is not None and (parent_key, key) in FORBIDDEN_PROFILE_FIELDS:
        logging.error("Forbidden profile field rejected: %s", path)
        raise ForbiddenObservationFieldError(
            f"forbidden observation field {parent_key}.{key} at {path!r} "
            "must not enter the prompt")


def _walk_blacklist(node: object, path: str, parent_key: str | None) -> None:
    """@brief 深度优先遍历渲染输入，逐键执行黑名单与数值身体状态检查。"""
    if isinstance(node, dict):
        for key, value in node.items():
            child_key = str(key)
            child_path = f"{path}.{child_key}"
            _raise_if_forbidden(child_key, child_path, parent_key)
            if child_key in BODY_VALUE_KEYS and isinstance(value, (int, float)):
                logging.error("Numeric body state rejected: %s", child_path)
                raise ForbiddenObservationFieldError(
                    f"numeric body state {child_path!r} must not enter the prompt")
            _walk_blacklist(value, child_path, child_key)
    elif isinstance(node, (list, tuple)):
        for index, item in enumerate(node):
            _walk_blacklist(item, f"{path}[{index}]", parent_key)


def enforce_field_blacklist(payload: object) -> None:
    """@brief 校验渲染输入不含黑名单字段（多层递归，含列表内嵌套）。

    覆盖「不进入提示的字段」的全部禁项：字面键（world_state_version、end_time、affordance_contract、
    observation_event_id、motivation_hints、game_catalog、activity_snapshot/activities 即完整活动快照）、
    工作隐藏参考与答案、哈希、内部身份与账户、私有绑定与观察私有证据、数值身体状态、profile 嵌套键与
    点号扁平键。通过时静默返回。

    @param payload 渲染输入（一层 JSON 对象或层数组）
    @exception ForbiddenObservationFieldError 命中任一禁项，消息含完整路径
    """
    _walk_blacklist(payload, "observation", None)


def render_facts(facts: dict) -> str:
    """@brief 把旧观察的 facts 字典渲染为确定性紧凑 JSON 文本（键排序、中文不转义；过渡保留）。

    facts 是工具需要的精简结构（「观察投影与提示合同」的字段表），同一字典必须渲染出同一
    文本；渲染前先执行字段黑名单校验，泄漏字段在此拦截。

    @param facts 已计算好的 facts 字段字典
    @return 确定性 JSON 文本
    @exception ForbiddenObservationFieldError facts 含黑名单字段
    """
    enforce_field_blacklist(facts)
    return json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def render_layer_json(value: object) -> str:
    """@brief 七层 JSON 的唯一规范序列化：先执行字段黑名单，再紧凑序列化（键序即构建顺序，中文不转义）。

    docs/dev/SPEC-persona-prompt.md 第一章「请求组成」：system 为系统层 JSON，user 为六层 JSON 数组，
    都以紧凑形式发送，键序固定、不排序。

    @param value 一层 JSON 对象或按固定层序排列的层数组
    @return 紧凑 JSON 文本
    @exception ForbiddenObservationFieldError value 含黑名单字段
    """
    enforce_field_blacklist(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

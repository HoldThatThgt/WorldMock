"""@brief 观察投影与提示合同：场景渲染、驱动摘要、字符预算与字段黑名单。

设计文档《人物真实性修订合同》"观察投影与提示合同"（docs/agentsociety2-design.md
L1529-1549）：给模型的 user 消息由 persona_card、scene、facts 三部分组成，
全部由确定性代码渲染、不调用模型。本模块是纯函数库——只依赖标准库与
调用方计算好的观察字段字典，不 import 本项目其他模块。

本模块逐条执行的合同要点：
- scene 为中文自然语言段落，内容覆盖时间与星期、所在地点与在场的人、
  体感分级、当前与暂停活动的进度、今天的计划与当前段、收件箱头、待办
  一句话、可达地点与营业状态、手边持有物、最近一次动作结果，上限
  800 汉字（L1531-1533）；超限按合同是输入侧问题，渲染器如实抛错，
  不静默截断。
- user 消息 40000 字符、system 消息 3000 字符；超限是运行故障，
  不截断、不删字段（L1547）。
- 独白一到三句、不超过 200 汉字、不得为空；违反是协议错误
  （"两阶段决策"表，L1551-1566）。
- 黑名单字段不得进入提示（L1537-1540）：world_state_version、end_time、
  affordance_contract、energy_feasibility、observation_event_id、
  motivation_hints、profile.motivation、profile.entertainment_preferences、
  game_catalog 全表、完整活动快照（activity_snapshot/activities）、
  路线坐标与几何（routes 条目仅保留目的地/方式/分钟/票价/是否可行）。
- drive_summary 只含体感分级与文字摘要，不含数字权重（L1535）。

观察输入字典的键名约定（与 facts 合同字段名同族，引擎侧据此对齐）：
current_time（ISO 8601 或 datetime）、location（地点显示名）、people_here
（在场人物名列表）、felt（维度到体感分级短语）、current_activity（title/
progress）、paused_activities（title/progress 列表）、day_plan（str 或
summary/current_segment）、inbox_head（收件箱头字符串列表）、todos_summary、
reachable_places（name/open/minutes 列表）、inventory、recent_action_results
（一句话列表，取最近一条）。
"""

from __future__ import annotations

import json
from datetime import datetime

#: user 消息字符预算（设计文档 L1547，合同常数，非实验假设）。
USER_MESSAGE_CHAR_BUDGET = 40000
#: system 消息字符预算（设计文档 L1547，合同常数，非实验假设）。
SYSTEM_MESSAGE_CHAR_BUDGET = 3000
#: scene 场景段落汉字上限（设计文档 L1533，合同常数）。
SCENE_HANZI_BUDGET = 800
#: persona_card 人物卡片汉字上限（设计文档 L1531，合同常数）。
PERSONA_CARD_HANZI_BUDGET = 600
#: 独白汉字上限（设计文档 L1556"不超过 200 汉字"，合同常数）。
MONOLOGUE_HANZI_BUDGET = 200
#: 场景文本语言（设计文档 L1549：提示与场景文本使用 language=zh-CN）。
SCENE_LANGUAGE = "zh-CN"

#: 不得进入提示的字面键（设计文档 L1537-1540）。activity_snapshot 与
#: activities 对应"完整活动快照"——观察只允许进度短语，不允许整表。
FORBIDDEN_OBSERVATION_KEYS = frozenset({
    "world_state_version",
    "end_time",
    "affordance_contract",
    "energy_feasibility",
    "observation_event_id",
    "motivation_hints",
    "profile.motivation",
    "profile.entertainment_preferences",
    "game_catalog",
    "activity_snapshot",
    "activities",
})

#: 嵌套路径禁键：(父键, 子键)。profile 的动机与娱乐偏好不得进入提示。
FORBIDDEN_PROFILE_FIELDS = frozenset({
    ("profile", "motivation"),
    ("profile", "entertainment_preferences"),
})

#: routes 条目白名单：目的地/方式/分钟/票价/是否可行（设计文档 L1535、
#: L1540"路线坐标与几何"不得进入提示）。键名与 facts 合同英文口径一致。
ROUTE_ALLOWED_FIELDS = frozenset({
    "destination",
    "mode",
    "minutes",
    "fare",
    "feasible",
})

_WEEKDAY_NAMES = (
    "星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日",
)


class PromptBudgetExceededError(RuntimeError):
    """@brief user/system 消息超出字符预算的运行故障（合同：不截断、不删字段）。"""


class RenderBudgetExceededError(ValueError):
    """@brief 渲染部件超出汉字上限（scene 800 / persona_card 600），输入侧问题。"""


class MonologueValidationError(ValueError):
    """@brief 独白违反协议：为空或超过 200 汉字（两阶段决策合同）。"""


class ForbiddenObservationFieldError(ValueError):
    """@brief 渲染输入含黑名单字段（观察投影合同 L1537-1540）。"""


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
    """@brief 渲染时间与星期句："现在是 2026 年 9 月 18 日星期五 08:30。"。"""
    current_time = observation.get("current_time")
    if current_time is None:
        return ""
    moment = (
        current_time if isinstance(current_time, datetime)
        else datetime.fromisoformat(str(current_time))
    )
    weekday = _WEEKDAY_NAMES[moment.weekday()]
    return (f"现在是 {moment.year} 年 {moment.month} 月 {moment.day} 日"
            f"{weekday} {moment.strftime('%H:%M')}。")


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
    current = observation.get("current_activity") or {}
    if current.get("title"):
        progress = f"，{current['progress']}" if current.get("progress") else ""
        sentences.append(_close_sentence(f"你正在进行《{current['title']}》{progress}"))
    paused = [item for item in (observation.get("paused_activities") or [])
              if item.get("title")]
    if paused:
        described = "、".join(
            f"《{item['title']}》（{item['progress']}）" if item.get("progress")
            else f"《{item['title']}》" for item in paused)
        sentences.append(_close_sentence(f"暂停中的活动有{described}"))
    return "".join(sentences)


def _plan_sentence(observation: dict) -> str:
    """@brief 渲染今天的计划与当前段句；day_plan 接受 str 或 summary/current_segment。"""
    day_plan = observation.get("day_plan")
    if not day_plan:
        return ""
    if isinstance(day_plan, str):
        return _close_sentence(f"今天的计划：{day_plan}")
    summary = day_plan.get("summary")
    segment = day_plan.get("current_segment")
    if summary and segment:
        return _close_sentence(f"今天的计划是{summary}，当前处于「{segment}」这一段")
    if summary:
        return _close_sentence(f"今天的计划是{summary}")
    return _close_sentence(f"今天当前处于「{segment}」这一段") if segment else ""


def _inbox_sentence(observation: dict) -> str:
    """@brief 渲染收件箱头句：只列给定的头部条目，不虚构未读数。"""
    head = [line for line in (observation.get("inbox_head") or []) if line]
    return _close_sentence(f"收件箱最新消息：{'；'.join(head)}") if head else ""


def _places_sentence(observation: dict) -> str:
    """@brief 渲染可达地点与营业状态句："公司食堂（营业中，约 5 分钟）"。"""
    places = [place for place in (observation.get("reachable_places") or [])
              if place.get("name")]
    if not places:
        return ""
    described = "、".join(
        f"{place['name']}（{'营业中' if place.get('open') else '未营业'}"
        + (f"，约 {place['minutes']} 分钟" if place.get("minutes") is not None else "")
        + "）" for place in places)
    return _close_sentence(f"可达地点有{described}")


def _inventory_sentence(observation: dict) -> str:
    """@brief 渲染手边持有物句："你手边有手机、工卡、钱包。"。"""
    items = [item for item in (observation.get("inventory") or []) if item]
    return _close_sentence(f"你手边有{'、'.join(items)}") if items else ""


def _recent_result_sentence(observation: dict) -> str:
    """@brief 渲染最近一次动作结果的一句话（列表取最近一条，原文照录）。"""
    results = observation.get("recent_action_results") or []
    if isinstance(results, str):
        return _close_sentence(results) if results else ""
    return _close_sentence(results[-1]) if results else ""


def render_scene(observation: dict, language: str = SCENE_LANGUAGE) -> str:
    """@brief 渲染中文自然语言场景段落（scene 合同，上限 800 汉字）。

    内容依次为时间与星期、所在地点与在场的人、体感分级、当前与暂停活动
    的进度、今天的计划与当前段、收件箱头、待办一句话、可达地点与营业
    状态、手边持有物、最近一次动作结果；缺失的字段跳过对应句子，渲染器
    不虚构事实。输出超过 800 汉字按合同是输入侧问题：抛
    RenderBudgetExceededError 如实报告，不静默截断。

    @param observation 引擎侧计算好的观察字段字典（键名见模块 docstring）
    @param language 场景语言，合同只支持 zh-CN，其他值抛 ValueError
    @return 场景段落文本
    @exception ValueError language 不是 zh-CN
    @exception RenderBudgetExceededError 渲染结果超过 800 汉字
    @exception ForbiddenObservationFieldError observation 含黑名单字段
    """
    if language != SCENE_LANGUAGE:
        raise ValueError(f"scene language must be {SCENE_LANGUAGE}, got: {language}")
    enforce_field_blacklist(observation)
    todos = observation.get("todos_summary")
    parts = [
        _time_sentence(observation),
        _location_sentence(observation),
        _felt_sentence(observation),
        _activity_sentences(observation),
        _plan_sentence(observation),
        _inbox_sentence(observation),
        _close_sentence(todos) if todos else "",
        _places_sentence(observation),
        _inventory_sentence(observation),
        _recent_result_sentence(observation),
    ]
    scene = "".join(part for part in parts if part)
    hanzi = count_hanzi(scene)
    if hanzi > SCENE_HANZI_BUDGET:
        raise RenderBudgetExceededError(
            f"rendered scene exceeds {SCENE_HANZI_BUDGET} hanzi budget: "
            f"{hanzi} hanzi; input observation is over-specified")
    return scene


def render_drive_summary(
    felt_bands: dict[str, str],
    work_gap_summary: str,
    todos_summary: str,
) -> str:
    """@brief 由体感分级、今日工作缺口与待办拼成一句中文驱动摘要。

    例："你有点饿，今天的工作还差两小时，晚上想放松一下。"数字权重
    不得出现（L1535）：分级与缺口须以文字短语（含中文数字词）传入，
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
        raise ValueError(
            f"drive summary must not contain digit weights: {digits} in {text!r}")
    return text


def enforce_budgets(user_message_len: int, system_message_len: int) -> None:
    """@brief 执行提示字符预算：user 40000、system 3000，超限即运行故障。

    合同（L1547）：超限是运行故障，不截断、不删字段——本函数只判定并
    抛错，恰好等于预算视为通过。

    @param user_message_len user 消息字符数
    @param system_message_len system 消息字符数
    @exception PromptBudgetExceededError 任一消息超出预算
    """
    if user_message_len > USER_MESSAGE_CHAR_BUDGET:
        raise PromptBudgetExceededError(
            f"user message exceeds {USER_MESSAGE_CHAR_BUDGET} chars: "
            f"{user_message_len}")
    if system_message_len > SYSTEM_MESSAGE_CHAR_BUDGET:
        raise PromptBudgetExceededError(
            f"system message exceeds {SYSTEM_MESSAGE_CHAR_BUDGET} chars: "
            f"{system_message_len}")


def validate_monologue(text: str) -> str:
    """@brief 判定独白是否满足两阶段决策协议并原样返回。

    协议（L1556）：一到三句第一人称中文，不超过 200 汉字，不得为空；
    超长或为空是协议错误。空串与纯空白同样视为为空。

    @param text 独白文本
    @return 通过校验的原文本
    @exception MonologueValidationError 独白为空或超过 200 汉字
    """
    if not text or not text.strip():
        raise MonologueValidationError("monologue is empty")
    hanzi = count_hanzi(text)
    if hanzi > MONOLOGUE_HANZI_BUDGET:
        raise MonologueValidationError(
            f"monologue exceeds {MONOLOGUE_HANZI_BUDGET} hanzi: {hanzi}")
    return text


def _raise_if_forbidden(key: str, path: str, parent_key: str | None) -> None:
    """@brief 按字面键集与 (父键, 子键) 嵌套规则检查单个键，违规即抛错。"""
    if key in FORBIDDEN_OBSERVATION_KEYS:
        raise ForbiddenObservationFieldError(
            f"forbidden observation field {path!r} must not enter the prompt")
    if parent_key is not None and (parent_key, key) in FORBIDDEN_PROFILE_FIELDS:
        raise ForbiddenObservationFieldError(
            f"forbidden observation field {parent_key}.{key} at {path!r} "
            "must not enter the prompt")


def _check_route_entries(value: object, path: str) -> None:
    """@brief 检查 routes 条目只含目的地/方式/分钟/票价/是否可行五键。"""
    entries = value if isinstance(value, (list, tuple)) else [value]
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        extra = sorted(str(key) for key in set(entry) - ROUTE_ALLOWED_FIELDS)
        if extra:
            raise ForbiddenObservationFieldError(
                f"route entry {path}[{index}] carries non-contract fields "
                f"{extra}; allowed fields: {sorted(ROUTE_ALLOWED_FIELDS)}")


def _walk_blacklist(node: object, path: str, parent_key: str | None) -> None:
    """@brief 深度优先遍历渲染输入，逐键执行黑名单与 routes 白名单检查。"""
    if isinstance(node, dict):
        for key, value in node.items():
            child_key = str(key)
            child_path = f"{path}.{child_key}"
            _raise_if_forbidden(child_key, child_path, parent_key)
            if child_key == "routes":
                _check_route_entries(value, child_path)
            _walk_blacklist(value, child_path, child_key)
    elif isinstance(node, (list, tuple)):
        for index, item in enumerate(node):
            _walk_blacklist(item, f"{path}[{index}]", parent_key)


def enforce_field_blacklist(payload: dict) -> None:
    """@brief 校验渲染输入字典不含黑名单字段（多层递归，含列表内嵌套）。

    覆盖设计文档 L1537-1540 的全部禁项：字面键（world_state_version、
    end_time、affordance_contract、energy_feasibility、observation_event_id、
    motivation_hints、game_catalog、activity_snapshot/activities 即完整活动
    快照）、profile 嵌套键（motivation、entertainment_preferences，兼容
    点号扁平键）、以及 routes 条目只保留目的地/方式/分钟/票价/是否可行
    （路线坐标与几何不得进入提示）。通过时静默返回。

    @param payload 渲染输入字典（observation 或 facts）
    @exception ForbiddenObservationFieldError 命中任一禁项，消息含完整路径
    """
    _walk_blacklist(payload, "observation", None)


def render_facts(facts: dict) -> str:
    """@brief 把 facts 字典渲染为确定性紧凑 JSON 文本（键排序、中文不转义）。

    facts 是工具需要的精简结构（L1535 字段表），同一字典必须渲染出同一
    文本；渲染前先执行字段黑名单校验，泄漏字段在此拦截。

    @param facts 已计算好的 facts 字段字典
    @return 确定性 JSON 文本
    @exception ForbiddenObservationFieldError facts 含黑名单字段
    """
    enforce_field_blacklist(facts)
    return json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compose_user_message(persona_card: str, scene: str, facts: str) -> str:
    """@brief 按 scene 合同顺序组装 user 消息三段：persona_card、scene、facts。

    段与段之间以空行分隔，空段跳过；组装前执行 persona_card 600 汉字
    上限，组装后执行 user 消息 40000 字符预算——两者超限均如实抛错，
    不截断、不删字段。

    @param persona_card 人物卡片渲染文本
    @param scene render_scene 的输出
    @param facts render_facts 的输出
    @return user 消息全文
    @exception RenderBudgetExceededError persona_card 超 600 汉字
    @exception PromptBudgetExceededError 组装结果超 40000 字符
    """
    hanzi = count_hanzi(persona_card)
    if hanzi > PERSONA_CARD_HANZI_BUDGET:
        raise RenderBudgetExceededError(
            f"persona card exceeds {PERSONA_CARD_HANZI_BUDGET} hanzi budget: "
            f"{hanzi} hanzi")
    message = "\n\n".join(part for part in (persona_card, scene, facts) if part)
    enforce_budgets(len(message), 0)
    return message

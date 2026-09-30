"""@brief 提示侧渲染与字段黑名单：七层 JSON 的规范序列化。

七层请求的每一层都是 JSON 对象（docs/dev/SPEC-persona-prompt.md 第一章「通用格式」）；宿主用
render_layer_json 做唯一的紧凑序列化（键序即构建顺序，不排序，中文不转义），序列化前执行字段黑名单：
内部身份、哈希、账户、数值身体状态、合同全表与私有绑定、进度层私有证据都不得进入提示。本模块是纯函数库，
只依赖标准库。
"""

from __future__ import annotations

import json
import logging

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
    "person_bindings",
    "catalog_bindings",
    "home_location_id",
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

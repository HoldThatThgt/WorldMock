"""@brief 管理 app 内容抽取、全局消费进度与生活活动、邮件内容。"""

from __future__ import annotations

from worldmock.corpora.manifest import safe_file
import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Mapping
from types import MappingProxyType

from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.catalog.models import Catalog, Content
from worldmock.exchange.types import CONTENT_KIND_LABELS
from worldmock.life import randomness
from worldmock.life.app_push import open_app_push, release_app_pushes
from worldmock.life.models import ActivityState, AgentState, ContentProgress, WorldState
from worldmock.life.scenario import MessageConfig


def validate_lifestyle_effects(effects: Mapping[str, float]) -> None:
    """@brief 环境与冻结活动共用三个效果的键、数值类型、有限性和范围校验。"""
    expected = {"entertainment", "social", "health"}
    if not isinstance(effects, Mapping) or set(effects) != expected:
        logging.error("Lifestyle effects must declare entertainment, social, and health")
        raise IntegrityError("Lifestyle effects are missing or invalid")
    if any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1
           for value in effects.values()):
        logging.error("Lifestyle effects must be finite numbers between zero and one")
        raise IntegrityError("Lifestyle effects are missing or invalid")


@dataclass(frozen=True, slots=True)
class LifestyleItem:
    """@brief 生活活动目录（corpora/lifestyle/items.json）的一条公开条目。"""

    activity_id: str
    name: str
    category: str
    typical_duration_minutes: int
    description: str
    met_value: float
    #: 当前环境声明的娱乐、社交与健康效果，构造后不可变。
    effects: Mapping[str, float]
    #: 目录声明的时间窗（start_hour, end_hour），场景时区的本地小时，半开区间；没有声明时为 None。
    #: 只作候选排序的相关度（docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-生活活动的地点类资格与排序 ），不构成资格。
    time_window: tuple[int, int] | None = None


    def __post_init__(self) -> None:
        """@brief 校验当前环境的三个效果并冻结独立副本。"""
        validate_lifestyle_effects(self.effects)
        object.__setattr__(self, "effects", MappingProxyType(dict(self.effects)))


def _time_window(row: dict) -> tuple[int, int] | None:
    """@brief 目录条目的时间窗：{start_hour, end_hour} 两个整数小时写成 (start, end)；没有声明时为 None。"""
    window = row.get("time_window")
    if window is None:
        return None
    return (int(window["start_hour"]), int(window["end_hour"]))


#: 生活活动目录语料路径（相对 corpus root）。
LIFESTYLE_ITEMS_PATH = "lifestyle/items.json"


def load_lifestyle_items(corpus_root: Path, activity_met: dict[str, float]) -> tuple[LifestyleItem, ...]:
    """@brief 装载当前环境的生活活动并核对身体 MET 表的身份闭集。"""
    path = safe_file(corpus_root, LIFESTYLE_ITEMS_PATH, "Content file path is invalid")
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        logging.error("Lifestyle catalog items are missing")
        raise IntegrityError("Lifestyle catalog items are missing")
    result: list[LifestyleItem] = []
    seen: set[str] = set()
    for row in items:
        item = _lifestyle_item(row)
        if item.activity_id in seen:
            logging.error("Lifestyle catalog id is duplicated: %s", item.activity_id)
            raise IntegrityError(f"Lifestyle catalog id {item.activity_id!r} is duplicated")
        seen.add(item.activity_id)
        result.append(item)
    expected = {key.removeprefix("lifestyle:") for key in activity_met if key.startswith("lifestyle:")}
    if seen != expected:
        logging.error("Lifestyle catalog ids do not match the declared activity MET keys")
        raise IntegrityError(
            f"Lifestyle catalog ids do not match activity_met lifestyle keys: "
            f"{sorted(expected - seen)} missing, {sorted(seen - expected)} unknown")
    return tuple(result)


def _lifestyle_item(row: object) -> LifestyleItem:
    """@brief 把当前环境的一条活动记录转换为不可变内容条目。"""
    if not isinstance(row, dict):
        logging.error("Lifestyle catalog row must be an object")
        raise IntegrityError("Lifestyle catalog row must be an object")
    try:
        return LifestyleItem(
            activity_id=str(row["activity_id"]), name=str(row["name_zh"]), category=str(row["category"]),
            typical_duration_minutes=int(row["typical_duration_minutes"]), description=str(row["description_zh"]),
            met_value=float(row["met_value"]), effects=row["effects"], time_window=_time_window(row))
    except (KeyError, TypeError, ValueError) as error:
        logging.error("Lifestyle catalog row is invalid: %s", error)
        raise IntegrityError(f"Lifestyle catalog row is invalid: {error}") from error


def load_content_titles(items: tuple[Content, ...]) -> dict[str, str]:
    """@brief 直接投影登记内容的可读标题。"""
    return {item.content_id: item.title for item in items}




def choose_app_content(catalog: Catalog, app_id: str, random_state: str, key: str) -> Content:
    """@brief 只从所选 app 排序后的绑定内容均匀抽取，不排除已完成内容。"""
    candidates = sorted(catalog.contents_by_app[app_id])
    if not candidates:
        logging.error("Selected app has no content: %s", app_id)
        raise BusinessRuleError("APP_CONTENT_EMPTY")
    stream = randomness.random_stream(random_state, key)
    return catalog.contents[candidates[stream.randrange(len(candidates))]]


def _content_snapshot(agent: AgentState, activity: ActivityState, item: Content,
                      pinned_by_push_id: str | None) -> ActivityState:
    """@brief 从全局位置建立当前内容快照，并立即排出静默内容边界。"""
    progress = agent.content_progress.get(item.content_id)
    position = progress.position_seconds if progress is not None else 0
    repeat = progress is not None and progress.completed_rounds >= 1
    data = {**activity.data, "app_id": activity.target_id, "content_id": item.content_id,
            "content_kind": item.kind.value, "summary": item.summary, "start_position_seconds": position,
            "pinned_by_push_id": pinned_by_push_id, "repeat": repeat}
    next_at = min(activity.last_settled_at + math.ceil(item.duration_seconds - position), activity.planned_end_at)
    return activity.model_copy(update={"data": data, "media_position_seconds": position,
                                       "next_interaction_at": next_at, "boundary_type": None}, deep=True)


def initialize_app_usage(state: WorldState, activity: ActivityState, catalog: Catalog,
                         action_id: str) -> WorldState:
    """@brief 准入成功后打开当前会话推送，或按打开键选择内容并创建快照。"""
    changed, push = open_app_push(state, activity.agent_id, activity.target_id)
    changed = changed.model_copy(deep=True)
    item = (catalog.contents[push.content_id] if push is not None else choose_app_content(
        catalog, activity.target_id, state.random_state, f"{action_id}:{activity.target_id}"))
    activity = activity.model_copy(update={"data": {"draw_index": 0, "played": []}}, deep=True)
    changed.activities[activity.activity_id] = _content_snapshot(
        changed.agents[activity.agent_id], activity, item, push.push_id if push is not None else None)
    return changed


def app_content_result(activity: ActivityState, catalog: Catalog) -> dict:
    """@brief 把已经实际选中的内容投影为封闭的公开动作结果。"""
    item = catalog.contents[activity.data["content_id"]]
    return {"app": catalog.apps[activity.target_id].name, "title": item.title,
            "kind": CONTENT_KIND_LABELS[item.kind], "summary": item.summary,
            "pushed": activity.data["pinned_by_push_id"] is not None}


def resume_app_usage(state: WorldState, activity: ActivityState, catalog: Catalog) -> WorldState:
    """@brief 保留当前内容，从最新全局位置恢复；恢复本身不消费也不释放推送。"""
    changed = state.model_copy(deep=True)
    item = catalog.contents[activity.data["content_id"]]
    changed.activities[activity.activity_id] = _content_snapshot(
        changed.agents[activity.agent_id], activity, item, activity.data["pinned_by_push_id"])
    return changed


def store_app_progress(agent: AgentState, activity: ActivityState) -> AgentState:
    """@brief 把当前真实游标写入全局进度，保留已经完成的轮数。"""
    content_id = activity.data["content_id"]
    previous = agent.content_progress.get(content_id)
    progress = ContentProgress(content_id=content_id, position_seconds=activity.media_position_seconds,
                               completed_rounds=previous.completed_rounds if previous is not None else 0)
    return agent.model_copy(update={"content_progress": {**agent.content_progress, content_id: progress}}, deep=True)


def settle_app_usage(state: WorldState, activity: ActivityState, catalog: Catalog,
                     position_before: float) -> tuple[WorldState, dict]:
    """@brief 每个真实推进区间写全局进度；真实完成增加一轮、解除推送并静默接续。"""
    changed = state.model_copy(deep=True)
    item = catalog.contents[activity.data["content_id"]]
    completed = activity.media_position_seconds >= item.duration_seconds
    agent = store_app_progress(changed.agents[activity.agent_id], activity)
    progress = agent.content_progress[item.content_id]
    if completed:
        progress = progress.model_copy(update={"position_seconds": 0,
                                               "completed_rounds": progress.completed_rounds + 1})
        agent.content_progress[item.content_id] = progress
        activity = activity.model_copy(update={"data": {**activity.data, "played": [*activity.data["played"], {
            "content_id": item.content_id, "kind": item.kind.value,
            "from_position": activity.data["start_position_seconds"], "to_position": item.duration_seconds}]}},
            deep=True)
    changed.agents[agent.agent_id] = agent
    evidence = {"content_id": item.content_id, "app_id": activity.target_id, "kind": item.kind.value,
                "position_before_seconds": position_before, "position_after_seconds": activity.media_position_seconds,
                "completed": completed, "completed_rounds_after": progress.completed_rounds}
    if completed:
        changed = release_app_pushes(changed, agent.agent_id, item.content_id)
        if activity.last_settled_at < activity.planned_end_at:
            draw_index = activity.data["draw_index"] + 1
            activity.data["draw_index"] = draw_index
            following = choose_app_content(catalog, activity.target_id, changed.random_state,
                                           f"{activity.activity_id}:{draw_index}")
            activity = _content_snapshot(agent, activity, following, None)
    changed.activities[activity.activity_id] = activity
    return changed, evidence


def reveal_mail(activity: ActivityState, agent: AgentState,
                messages: dict[str, MessageConfig]) -> tuple[AgentState, dict]:
    """@brief 阅读结束后才把邮件正文加入人物知识。"""
    message_id = activity.target_id or ""
    if message_id not in agent.visible_message_ids or message_id not in messages:
        raise BusinessRuleError("MESSAGE_NOT_VISIBLE")
    read = set(agent.read_message_ids)
    read.add(message_id)
    changed = agent.model_copy(update={"read_message_ids": read}, deep=True)
    message = messages[message_id]
    data = {"message_id": message_id, "thread_id": message.thread_id, "subject": message.subject,
            "body": message.body,
            "attachment_sha256": message.attachment_sha256}
    return changed, data


def mail_headers(agent: AgentState, messages: dict[str, MessageConfig]) -> list[dict]:
    """@brief 观察只展示已到达邮件的标题，已读邮件才展示正文。"""
    result = []
    for message_id in sorted(agent.visible_message_ids):
        message = messages[message_id]
        record = {"message_id": message_id, "thread_id": message.thread_id,
                  "subject": message.subject, "read": message_id in agent.read_message_ids}
        if record["read"]:
            record["body"] = message.body
        result.append(record)
    return result

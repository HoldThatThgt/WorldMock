"""@brief 按实际接触区间揭示字幕、邮件和商品目录内容。"""

from __future__ import annotations

from worldmock.corpora.manifest import safe_file
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from worldmock.catalog.affordances import default_affordances
from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.life.models import ActivityState, AgentState, ContentProgress
from worldmock.life.scenario import ContentItemConfig, MessageConfig


@dataclass(frozen=True, slots=True)
class SubtitleCue:
    """@brief SRT 时间轴中的一个可揭示字幕片段。"""

    cue_id: int
    start_seconds: float
    end_seconds: float
    text: str


@dataclass(frozen=True, slots=True)
class LifestyleItem:
    """@brief 生活活动目录（corpora/lifestyle/items.json）的一条公开条目。"""

    activity_id: str
    name: str
    category: str
    typical_duration_minutes: int
    description: str
    met_value: float
    #: 目录声明的时间窗（start_hour, end_hour），场景时区的本地小时，半开区间；没有声明时为 None。
    #: 只作候选排序的相关度（docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-生活活动的地点类资格与排序 ），不构成资格。
    time_window: tuple[int, int] | None = None


def _time_window(row: dict) -> tuple[int, int] | None:
    """@brief 目录条目的时间窗：{start_hour, end_hour} 两个整数小时写成 (start, end)；没有声明时为 None。"""
    window = row.get("time_window")
    if window is None:
        return None
    return (int(window["start_hour"]), int(window["end_hour"]))


#: 「接着看」的最短剩余：已看完或剩余不足 60 秒的内容给全长并从头派生（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第八章「呈现」）。
CONTENT_RESUME_MINIMUM_SECONDS = 60


def restarts_from_beginning(position_seconds: float, duration_seconds: float) -> bool:
    """@brief 内容是否从头再看：已看完或剩余不足 60 秒；否则接着看，从上次位置开始、时长取剩余。"""
    return duration_seconds - position_seconds < CONTENT_RESUME_MINIMUM_SECONDS


#: 生活活动目录语料路径（相对 corpus root）。
LIFESTYLE_ITEMS_PATH = "lifestyle/items.json"


def load_lifestyle_items(corpus_root: Path, activity_met: dict[str, float]) -> tuple[LifestyleItem, ...]:
    """@brief 装载生活活动目录并对照身体 MET 表核对身份闭集（fail-closed）。

    观察不再逐条展开生活活动：目录按品类计数进 activity_summary，明细由
    呈现窗口揭示。items.json 与 activity_met 的 lifestyle 键
    必须一一对应（多、少、重复都按 IntegrityError 终止），保证呈现窗口
    与 start_activity(action="do") 的受理闭集一致。
    """
    path = safe_file(corpus_root, LIFESTYLE_ITEMS_PATH, "Content file path is invalid")
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise IntegrityError("Lifestyle catalog items are missing")
    result: list[LifestyleItem] = []
    seen: set[str] = set()
    for row in items:
        if not isinstance(row, dict):
            raise IntegrityError("Lifestyle catalog row must be an object")
        try:
            item = LifestyleItem(
                activity_id=str(row["activity_id"]),
                name=str(row["name_zh"]),
                category=str(row["category"]),
                typical_duration_minutes=int(row["typical_duration_minutes"]),
                description=str(row["description_zh"]),
                met_value=float(row["met_value"]), time_window=_time_window(row))
        except (KeyError, TypeError, ValueError) as error:
            raise IntegrityError(f"Lifestyle catalog row is invalid: {error}") from error
        if item.activity_id in seen:
            raise IntegrityError(f"Lifestyle catalog id {item.activity_id!r} is duplicated")
        seen.add(item.activity_id)
        result.append(item)
    expected = {key.removeprefix("lifestyle:") for key in activity_met
                if key.startswith("lifestyle:")}
    if seen != expected:
        raise IntegrityError(
            f"Lifestyle catalog ids do not match activity_met lifestyle keys: "
            f"{sorted(expected - seen)} missing, {sorted(seen - expected)} unknown")
    return tuple(result)



#: 内容条目标题的语料来源（内容类别 → (语料相对路径, 条目身份键)）由可供性
#: 注册表派生（families.*.content_kinds.*.title_source，M0 影子等价已证与原
#: 字面字典一致）；身份键为空串表示单记录身份文件（其 title 字段直接适用），
#: 信息流和游戏会话使用时间轴同目录的身份目录，避免对人物展示内部编号。


def load_content_titles(
    items: tuple[ContentItemConfig, ...],
    corpus_root: Path,
    title_sources: Mapping[str, tuple[str, str]] | None = None,
) -> dict[str, str]:
    """@brief 从内容身份目录读取每个内容对象的人类可读名称。"""
    sources = (default_affordances().title_sources
               if title_sources is None else title_sources)
    titles: dict[str, str] = {}
    payloads: dict[str, object] = {}
    for item in items:
        source = _content_title_source(item, sources)
        relative, id_key = source
        if relative not in payloads:
            payloads[relative] = json.loads(
                safe_file(corpus_root, relative, "Content file path is invalid").read_text(encoding="utf-8"))
        payload = payloads[relative]
        title = _content_title(payload, id_key, item.item_id)
        if not isinstance(title, str) or not title.strip():
            raise IntegrityError(f"Content title is missing for {item.item_id!r}")
        titles[item.item_id] = title.strip()
    return titles


def _content_title_source(
    item: ContentItemConfig, sources: Mapping[str, tuple[str, str]],
) -> tuple[str, str]:
    """@brief 为内容对象定位其身份目录与条目键。"""
    if item.kind in {"news_feed", "joke_feed"}:
        return ((Path(item.timeline_path).parent / "feed-items.json").as_posix(), "item_id")
    if item.kind == "game_session":
        return ((Path(item.timeline_path).parent / "items.json").as_posix(), "item_id")
    source = sources.get(item.kind)
    if source is None:
        raise IntegrityError(f"Content title source is missing for {item.kind!r}")
    return source


def _content_title(payload: object, id_key: str, item_id: str) -> object:
    """@brief 从单记录或条目目录取已声明的标题。"""
    if id_key:
        if not isinstance(payload, list):
            raise IntegrityError("Content title catalog must be a list")
        matches = [row for row in payload if isinstance(row, dict) and row.get(id_key) == item_id]
        if len(matches) != 1:
            raise IntegrityError(f"Content title identity is missing or duplicated: {item_id!r}")
        return matches[0].get("title")
    return payload.get("title") if isinstance(payload, dict) else None


def load_subtitles(item: ContentItemConfig, corpus_root: Path) -> tuple[SubtitleCue, ...]:
    """@brief 校验字幕身份并解析完整 SRT 时间轴。"""
    if item.kind == "podcast_episode":
        audio = safe_file(corpus_root, item.audio_path, "Content file path is invalid")
        if hashlib.sha256(audio.read_bytes()).hexdigest() != item.audio_sha256:
            raise IntegrityError("Podcast audio hash mismatch")
    path = safe_file(corpus_root, item.timeline_path, "Content file path is invalid")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != item.timeline_sha256:
        raise IntegrityError("Content timeline hash mismatch")
    blocks = re.split(r"\r?\n\r?\n", content.decode("utf-8-sig").strip())
    try:
        cues = tuple(_parse_block(block) for block in blocks)
    except Exception as error:
        raise IntegrityError("Subtitle timeline is invalid") from error
    if not cues or any(left.end_seconds > right.start_seconds for left, right in zip(cues, cues[1:])):
        raise IntegrityError("Subtitle timeline is empty or overlaps")
    return cues


def reveal_watch(activity: ActivityState, agent: AgentState, item: ContentItemConfig,
                 cues: tuple[SubtitleCue, ...]) -> tuple[AgentState, dict]:
    """@brief 只返回本次实际媒体位置新跨过的字幕。"""
    previous = agent.content_progress.get(item.item_id)
    start = activity.data.get("last_revealed_position_seconds",
                              activity.data.get("start_position_seconds", 0))
    stop = min(item.duration_seconds, activity.media_position_seconds)
    if item.kind == "podcast_episode":
        visible = [cue for cue in cues if start < cue.end_seconds <= stop]
    else:
        visible = [cue for cue in cues if cue.start_seconds < stop and cue.end_seconds > start]
    count = activity.data["exposure_count"]
    progress = ContentProgress(item_id=item.item_id, position_seconds=stop, exposure_count=count)
    mapping = dict(agent.content_progress)
    mapping[item.item_id] = progress
    changed = agent.model_copy(update={"content_progress": mapping}, deep=True)
    activity.data["last_revealed_position_seconds"] = stop
    data = {"experience": item.experience_label, "position_before_seconds": start,
            "position_after_seconds": stop,
            "cues": [{"cue_id": cue.cue_id, "start_seconds": cue.start_seconds,
                      "end_seconds": cue.end_seconds, "text": cue.text} for cue in visible],
            "exposure_count": count}
    return changed, data


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
            "body": message.body, "related_task_id": message.related_task_id,
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


def _parse_block(block: str) -> SubtitleCue:
    """@brief 解析一个严格三段式 SRT block。"""
    lines = block.splitlines()
    if len(lines) < 3 or " --> " not in lines[1]:
        raise ValueError("invalid SRT block")
    start, end = lines[1].split(" --> ", 1)
    return SubtitleCue(int(lines[0]), _srt_seconds(start), _srt_seconds(end), "\n".join(lines[2:]))


def _srt_seconds(value: str) -> float:
    """@brief 将 SRT 时间戳转换为秒。"""
    hours, minutes, tail = value.split(":")
    seconds, millis = tail.split(",")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000

"""@brief 按实际接触区间揭示字幕、邮件和商品目录内容。"""

from __future__ import annotations

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


#: 生活活动目录语料路径（相对 corpus root）。
LIFESTYLE_ITEMS_PATH = "lifestyle/items.json"


def load_lifestyle_items(corpus_root: Path, activity_met: dict[str, float]) -> tuple[LifestyleItem, ...]:
    """@brief 装载生活活动目录并对照身体 MET 表核对身份闭集（fail-closed）。

    观察不再逐条展开生活活动：目录按品类计数进 activity_summary，明细由
    browse_activities 下钻揭示。items.json 与 activity_met 的 lifestyle 键
    必须一一对应（多、少、重复都按 IntegrityError 终止），保证 browse 结果
    与 start_basic_activity("lifestyle:<id>") 的受理闭集一致。
    """
    payload = json.loads(_safe_file(corpus_root, LIFESTYLE_ITEMS_PATH).read_text(encoding="utf-8"))
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
                met_value=float(row["met_value"]))
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
#: 未列出的类别（news_feed/joke_feed/game_session）以 experience_label /
#: game_id 自带身份。


def load_content_titles(
    items: tuple[ContentItemConfig, ...],
    corpus_root: Path,
    title_sources: Mapping[str, tuple[str, str]] | None = None,
) -> dict[str, str]:
    """@brief 按内容类别从语料 items.json 读取展示标题。

    标题是观察展示元数据：身份文件缺失/越界仍按 IntegrityError 失败，
    单条目在 items.json 中缺行或无标题时只是不注入 title（部分元数据合法）。
    来源映射缺省取仓库默认注册表的 title_sources（M2：字面字典已删，注册表
    即事实；显式传入时以传入映射为准）。
    """
    sources = (default_affordances().title_sources
               if title_sources is None else title_sources)
    titles: dict[str, str] = {}
    payloads: dict[str, object] = {}
    for item in items:
        source = sources.get(item.kind)
        if source is None:
            continue
        relative, id_key = source
        if relative not in payloads:
            payloads[relative] = json.loads(
                _safe_file(corpus_root, relative).read_text(encoding="utf-8"))
        payload = payloads[relative]
        if id_key:
            entry = next((row for row in payload
                          if isinstance(row, dict) and row.get(id_key) == item.item_id), None)
            title = entry.get("title") if entry else None
        else:
            title = payload.get("title") if isinstance(payload, dict) else None
        if isinstance(title, str) and title.strip():
            titles[item.item_id] = title
    return titles


def load_subtitles(item: ContentItemConfig, corpus_root: Path) -> tuple[SubtitleCue, ...]:
    """@brief 校验字幕身份并解析完整 SRT 时间轴。"""
    if item.kind == "podcast_episode":
        audio = _safe_file(corpus_root, item.audio_path)
        if hashlib.sha256(audio.read_bytes()).hexdigest() != item.audio_sha256:
            raise IntegrityError("Podcast audio hash mismatch")
    path = _safe_file(corpus_root, item.timeline_path)
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


def _safe_file(root: Path, relative: str) -> Path:
    """@brief 拒绝任意路径和符号链接内容。"""
    base = Path(root).resolve()
    candidate = base / relative
    path = candidate.resolve()
    if _has_symlink(candidate, base) or base not in path.parents or not path.is_file():
        raise IntegrityError("Content file path is invalid")
    return path


def _has_symlink(path: Path, root: Path) -> bool:
    """@brief 在解析前拒绝文件及父目录符号链接。"""
    current = path
    while current != root and root in current.parents:
        if current.is_symlink():
            return True
        current = current.parent
    return current != root

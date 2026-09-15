"""@brief 按实际接触区间揭示字幕、邮件和商品目录内容。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

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


def load_subtitles(item: ContentItemConfig, corpus_root: Path) -> tuple[SubtitleCue, ...]:
    """@brief 校验字幕身份并解析完整 SRT 时间轴。"""
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

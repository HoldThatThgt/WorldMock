"""@brief WorldMock 人物的步尾记忆保存与工作区视图。"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from worldmock.agents.memory.life_memory_store import WorldMockMemoryStore
from worldmock.recording_engine.events.memory_evidence import MemoryUpdateEvidence
from worldmock.state.decisions.round import DecisionContext
from worldmock.state.model.memory import valid_subjective_snapshot

#: 记忆更新快照的四个基本字段（与 SUBJECTIVE_SNAPSHOT_FIELDS 同一组，按快照的写出次序）。
_SNAPSHOT_FIELDS = ("id", "type", "text", "epistemic_status")
#: 整份 MEMORY.md 视图的字数上限（Python len）。
_VIEW_CHARACTER_BUDGET = 4000


@dataclass(frozen=True, slots=True)
class MemoryStep:
    """@brief 一次决策步尾保存的全部输入。"""

    identity: str                              # 人物卡片文本，MEMORY.md「身份」段
    facts: tuple[dict[str, Any], ...]          # 本决策冻结观察的已核验经历，至多五条，已按事件序排好
    observation_event_id: str                  # 本决策冻结观察的事件身份
    memories: tuple[Mapping[str, Any], ...]    # conclude_activity 经 control_items 读出的存储形状记忆，原样
    now: datetime                              # 本决策的观察时刻（DecisionContext.observed_at）


@dataclass(frozen=True, slots=True)
class MemoryStepOutcome:
    """@brief 步尾保存的结果。"""

    created_episodes: tuple[dict[str, Any], ...]   # 本步新建的条目
    merged_episodes: tuple[dict[str, Any], ...]    # 本步合并的既有条目
    memory_before_sha256: str | None               # 写入前 MEMORY.md 的摘要，文件不存在为 None
    observation_event_id: str                      # 本决策冻结观察
    verified_event_ids: tuple[str, ...]            # 进入事实视图的来源事件身份


def save_step(store: WorldMockMemoryStore, step: MemoryStep) -> MemoryStepOutcome:
    """@brief 步尾保存：记下写入前的视图摘要与条目身份，先渲染视图（预算不通过时一个文件都不写），再写条目、写视图，
    按步前的条目身份把返回的条目分成新建与合并；不调用模型，同步执行（docs/design/06-agent.md「宿主模型协议」）。"""
    before = _file_sha256(store.memory_md_path) if store.memory_md_path.is_file() else None
    known = store.episode_ids()
    view = render_memory_view(step.identity, step.facts)
    written = store.append_episodes(step.memories, step.now)
    store.write_memory_view(view)
    created, merged = _classify_episodes(written, known)
    return MemoryStepOutcome(created, merged, before, step.observation_event_id,
                             tuple(dict.fromkeys(item["source_event_id"] for item in step.facts)))


def render_memory_view(identity: str, facts: Sequence[Mapping[str, Any]]) -> str:
    """@brief 用固定三个区段装配工作区视图：身份与人物自述都在引用块里，自述段恒为「暂无人物自述。」；纯函数，不碰文件。

    整份视图超过 4000 字时先写错误日志再拒绝，不截断已核验经历。
    """
    factual = "\n".join(_fact_line(item) for item in facts) or "- 暂无已核验经历。"
    view = ("# Memory\n\n## 身份\n" + _quote(identity) + "\n\n## 已核验经历\n" + factual
            + "\n\n## 人物自述（未经核验）\n> 暂无人物自述。\n")
    if len(view) > _VIEW_CHARACTER_BUDGET:
        logging.error("Verified memory view exceeds its character budget")
        raise ValueError("Verified memory view exceeds its character budget")
    return view


def _quote(text: str) -> str:
    """@brief 将自由文字封入 Markdown 引用，不能跳成顶层事实标题。"""
    return "\n".join("> " + line for line in text.splitlines())


def _fact_line(fact: dict[str, Any]) -> str:
    """@brief 只用封闭类型载荷和来源身份渲染完成事实。"""
    kind, source = fact["kind"], fact["source_event_id"]
    if kind == "waited":
        detail = f"等待 {fact['duration_seconds']} 秒"
    elif kind == "received":
        detail = f"收到 {fact['goods_id']} {_amount(fact['quantity'])} 份"
        if "quality" in fact:
            detail += f"，商铺 {fact['store_id']}，这份品质{fact['quality']}"
    elif kind == "arrived":
        detail = f"到达 {fact['location_id']}，进入状态 {fact['access_status']}"
    elif kind == "played":
        app = f"使用应用 {fact['app_id']}，" if fact["app_id"] is not None else ""
        detail = (f"{app}内容 {fact['content_id']} 进度 "
                  f"{_amount(fact['position_before_seconds'])} 至 {_amount(fact['position_after_seconds'])} 秒")
    else:
        logging.error("Verified experience kind is invalid")
        raise ValueError("Verified experience kind is invalid")
    return f"- {fact['occurred_at']}：{detail}。来源事件 {source}。"


def _amount(value: int | float) -> str:
    """@brief 以固定九位份额精度表达真实数值，不补造更高精度。"""
    return f"{value:.9f}".rstrip("0").rstrip(".")


def _classify_episodes(
    written: list[dict[str, Any]], known: set[str],
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    """@brief 按本步前的稳定身份区分新建与合并。"""
    by_id = {str(item["id"]): item for item in written}
    created = tuple(item for identity, item in by_id.items() if identity not in known)
    merged = tuple(item for identity, item in by_id.items() if identity in known)
    return created, merged


def memory_update_evidence(context: DecisionContext, store: WorldMockMemoryStore,
                           outcome: MemoryStepOutcome) -> MemoryUpdateEvidence | None:
    """@brief 步尾已落盘记忆的证据（docs/design/05-recording-engine.md「事件与来源」）；既没有新增或合并条目、视图也
    没有变化时返回 None（不登记）。写入前摘要原样交出：人物第一次保存时为 None。
    """
    memory_sha256 = _file_sha256(store.memory_md_path)
    created = tuple(str(item["id"]) for item in outcome.created_episodes)
    merged = tuple(str(item["id"]) for item in outcome.merged_episodes)
    if not created and not merged and outcome.memory_before_sha256 == memory_sha256:
        return None
    snapshots = tuple(_episode_snapshot(item) for item in outcome.created_episodes + outcome.merged_episodes)
    return MemoryUpdateEvidence(
        context=context, operation="append_episodes" if created or merged else "refresh_summary",
        created_episode_ids=created, merged_episode_ids=merged, episodes=snapshots,
        memory_before_sha256=outcome.memory_before_sha256, memory_sha256=memory_sha256,
        episodes_sha256=_file_sha256(store.episodes_path), observation_event_id=outcome.observation_event_id,
        verified_event_ids=outcome.verified_event_ids)


def _episode_snapshot(item: dict[str, Any]) -> dict[str, str]:
    """@brief 一条主观条目的最小快照：恰为四个基本字段。"""
    snapshot = {key: str(item[key]) for key in _SNAPSHOT_FIELDS}
    if not valid_subjective_snapshot(snapshot):
        logging.error("Subjective memory snapshot is invalid")
        raise RuntimeError("Subjective memory snapshot is invalid")
    return snapshot


def _file_sha256(path: Any) -> str:
    """@brief 文件内容的 SHA-256。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()

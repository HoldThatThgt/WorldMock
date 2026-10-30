"""@brief WorldMock 人物的主观记忆与已提交经历分区运行时。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from agentsociety2.agent.base.react import MemoryUpdateEvidence
from agentsociety2.agent.memory_runtime import MemoryRuntimeConfig, MemoryStepOutcome, PersonMemoryRuntime
from worldmock.agents.memory.life_memory_store import WorldMockMemoryStore
from worldmock.state.model.memory import SUBJECTIVE_TYPES, valid_subjective_snapshot

if TYPE_CHECKING:
    from custom.agents.life_agent import LifeAgent

#: 结束活动写入点的记忆来源（系统固定，与 memory_updated.data.trigger 的取值同名）。
_CONCLUSION_SOURCE = "activity_conclusion"
#: 记忆更新快照的基本字段（与 AS2 PersonAgent._record_memory_update 同一组）。
_SNAPSHOT_FIELDS = ("id", "type", "text", "epistemic_status")
#: 记忆运行时模型入口的失败说法。
_NO_MODEL_CALL = "WorldMock memory runtime does not call the model"


async def _refuse_model_call(**request: Any) -> Any:
    """@brief WorldMock 记忆运行时不调用模型：AS2 的通用巩固入口已被步尾覆写，到达这里是宿主编排错误。"""
    logging.error(_NO_MODEL_CALL)
    raise RuntimeError(_NO_MODEL_CALL)


class WorldMockMemoryRuntime(PersonMemoryRuntime):
    """@brief 保留 AS2 存储与检查点，替换步尾保存与视图装配；人物一侧的记忆不调用模型。"""

    def __init__(self, owner: LifeAgent, config: MemoryRuntimeConfig) -> None:
        """@brief 用本人物的工作区入口构造专用运行时；模型入口只会失败。"""
        super().__init__(
            agent_id=owner.id, agent_name=owner.name, config=config,
            get_model_name=lambda: owner._model_name,
            dispatch_llm=_refuse_model_call,
            get_profile=owner.get_profile, logger=owner._logger, trace_span=owner.trace_span,
        )
        self._owner = owner

    def ensure_store(self, workspace_root: Any) -> WorldMockMemoryStore | None:
        """@brief 初次运行绑定宿主记忆存储，再创建框架检查点文件。"""
        if not self.enabled:
            return None
        if self._store is None:
            self._store = WorldMockMemoryStore(workspace_root, agent_id=self.agent_id)
        self._store.ensure()
        return self._store

    def restore_store(self, workspace_root: Any) -> WorldMockMemoryStore | None:
        """@brief 恢复必须绑定宿主存储并通过完整框架检查点验证。"""
        if not self.enabled:
            return None
        if self._store is not None:
            logging.error("Memory store was already bound")
            raise RuntimeError("Memory store was already bound")
        store = WorldMockMemoryStore(workspace_root, agent_id=self.agent_id)
        store.validate_existing()
        self._store = store
        return store

    def build_context(self) -> dict[str, Any]:
        """@brief 记忆上下文只给最近条目窗口与文件路径，不给主观归纳；生活决策提示由七层组成，不读这里的条目与摘要。

        recent_limit 为 0 时返回空列表：AS2 存储会把 0 改回 8 条（docs/design/06-agent.md）。
        """
        store = self.store
        if store is None:
            return {}
        limit = self.config.recent_limit
        return {
            "recent_episodes": store.recent(limit=limit) if limit > 0 else [],
            "files": {"summary": "MEMORY.md", "episodes": "memory/episodes.jsonl"},
        }

    async def after_step(
        self, *, tick: int, t: datetime, step_count: int,
        finish_memories: list[dict[str, Any]] | None = None, sleep_boundary: bool = False,
    ) -> MemoryStepOutcome:
        """@brief 步尾：保存结束活动写下的主观条目，再确定性渲染当前事实窗口；不调用模型。

        finish_memories 承载宿主展平的 conclude_activity 逐项记忆与看法（经 control_items 读出，看法的 about 即存储的
        subject，写入存储前剥掉）。锁定的 AS2 仍传 sleep_boundary，这里不读它
        （docs/design/06-agent.md「宿主模型协议」）。
        """
        store = self.store
        if store is None:
            return MemoryStepOutcome((), (), None)
        facts, observation_id = self._current_facts()
        before = hashlib.sha256(store.memory_md_path.read_bytes()).hexdigest()
        known = store.episode_ids()
        memories = self._validate_subjective_memories(finish_memories)
        written = await self._append_subjective(memories, tick=tick, t=t, step_count=step_count)
        view = render_memory_view(
            self._owner._persona_card_text(), facts, store.read_subjective_summary(),
        )
        await asyncio.to_thread(store.write_memory_view, view)
        created, merged = _classify_episodes(written, known)
        return MemoryStepOutcome(created, merged, before, False, observation_id,
                                 tuple(dict.fromkeys(item["source_event_id"] for item in facts)))

    def _current_facts(self) -> tuple[list[dict[str, Any]], str]:
        """@brief 只取 LifeAgent 从本轮结构化观察冻结的五条事实。"""
        facts = getattr(self._owner, "_life_verified_experiences", None)
        observation_id = getattr(self._owner, "_life_observation_event_id", None)
        if (not isinstance(facts, list) or len(facts) > 5 or not isinstance(observation_id, str)
                or not observation_id):
            logging.error("Current verified experience window is unavailable")
            raise RuntimeError("Current verified experience window is unavailable")
        if any(not isinstance(item, dict) or not isinstance(item.get("source_event_id"), str)
               or item.get("agent_id") != self.agent_id for item in facts):
            logging.error("Current verified experience window is invalid")
            raise RuntimeError("Current verified experience window is invalid")
        if [item["event_seq"] for item in facts] != sorted(item["event_seq"] for item in facts):
            logging.error("Current verified experience order is invalid")
            raise RuntimeError("Current verified experience order is invalid")
        return facts, observation_id

    def _validate_subjective_memories(self, raw: Any) -> list[dict[str, Any]]:
        """@brief 只接受结束活动写下的主观条目：九种主观类型、subjective、来源由系统固定为 activity_conclusion。"""
        try:
            memories = self._validate_finish_memories(raw)
        except (TypeError, ValueError):
            logging.error("WorldMock conclusion memory payload is invalid")
            raise
        if any(item["type"] not in SUBJECTIVE_TYPES or item["epistemic_status"] != "subjective"
               or item["source"] != _CONCLUSION_SOURCE for item in memories):
            logging.error("WorldMock conclusion memory must be subjective and system-sourced")
            raise ValueError("WorldMock conclusion memory must be subjective and system-sourced")
        return memories

    async def _append_subjective(
        self, memories: list[dict[str, Any]], *, tick: int, t: datetime, step_count: int,
    ) -> list[dict[str, Any]]:
        """@brief 宿主存储执行相似合并，任何静默漏写均失败。"""
        if not memories:
            return []
        store = self._require_store()
        written = await asyncio.to_thread(store.append_episodes, memories, tick=tick, t=t, step_count=step_count)
        if not written:
            logging.error("Memory store did not persist finish memories")
            raise RuntimeError("Memory store did not persist finish memories")
        return written


def render_memory_view(identity: str, facts: list[dict[str, Any]], summary: str) -> str:
    """@brief 用固定三个区段装配工作区视图，主观文字始终留在引用块。"""
    factual = "\n".join(_fact_line(item) for item in facts) or "- 暂无已核验经历。"
    header = ("# Memory\n\n## 身份\n" + _quote(identity) +
              "\n\n## 已核验经历\n" + factual + "\n\n## 人物自述（未经核验）\n")
    if len(header) >= 3996:
        logging.error("Verified memory view exceeds its character budget")
        raise ValueError("Verified memory view exceeds its character budget")
    subjective = _quote(summary or "暂无人物自述。")
    return header + subjective[:3999 - len(header)].rstrip() + "\n"


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


def memory_update_evidence(context: Any, store: Any, outcome: MemoryStepOutcome) -> MemoryUpdateEvidence | None:
    """@brief 步尾已落盘记忆的证据（与 AS2 PersonAgent._record_memory_update 同一组字段，
    docs/design/05-recording-engine.md「事件与来源」）；既没有新增或合并条目、视图也没有变化时返回 None（不登记）。
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
        memory_before_sha256=outcome.memory_before_sha256 or "", memory_sha256=memory_sha256,
        episodes_sha256=_file_sha256(store.episodes_path), state_sha256=_file_sha256(store.state_path),
        consolidated=outcome.consolidated, observation_event_id=outcome.observation_event_id,
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

"""@brief WorldMock 人物的主观记忆与已提交经历分区运行时。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from agentsociety2.agent.memory_runtime import MemoryRuntimeConfig, MemoryStepOutcome, PersonMemoryRuntime

if TYPE_CHECKING:
    from custom.agents.life_agent import LifeAgent

_SUBJECTIVE_TYPES = frozenset({
    "commitment", "relationship", "preference", "goal", "conflict",
    "resolution", "identity", "routine", "reflection",
})
#: 结束活动写入点的记忆来源（系统固定，与 memory_updated.data.trigger 同一组取值）。
_CONCLUSION_SOURCE = "activity_conclusion"
_SUBJECTIVE_PROMPT = (
    "Summarize only this person's subjective feelings, intentions, relationships and preferences in Chinese. "
    "The output is a subjective self-report, even when the person believes a concrete event occurred. "
    "Do not write objective facts, headings, source identifiers, JSON or code fences. "
    "Return plain first-person prose suitable for a short memory summary."
)


class WorldMockMemoryRuntime(PersonMemoryRuntime):
    """@brief 保留 AS2 存储和检索，仅替换 WorldMock 的归纳与视图装配。"""

    def __init__(self, owner: LifeAgent, config: MemoryRuntimeConfig) -> None:
        """@brief 用本人物现有模型调用和工作区入口构造专用运行时。"""
        super().__init__(
            agent_id=owner.id, agent_name=owner.name, config=config,
            get_model_name=lambda: owner._model_name,
            dispatch_llm=owner._dispatch_memory_llm,
            get_profile=owner.get_profile, logger=owner._logger, trace_span=owner.trace_span,
        )
        self._owner = owner

    def build_context(self) -> dict[str, Any]:
        """@brief 当前提示只给冻结身份与主观摘要，不回灌上一轮事实窗口。"""
        store = self.store
        if store is None:
            return {}
        summary = store.read_subjective_summary()
        return {
            "memory_md": _prompt_memory(self._owner._persona_card_text(), summary),
            "subjective_summary": summary,
            "recent_episodes": store.recent(limit=self.config.recent_limit),
            "files": {"summary": "MEMORY.md", "episodes": "memory/episodes.jsonl"},
        }

    async def after_step(
        self, *, tick: int, t: datetime, step_count: int,
        finish_memories: list[dict[str, Any]] | None = None, sleep_boundary: bool = False,
    ) -> MemoryStepOutcome:
        """@brief 保存结束活动写下的主观条目、按阈值归纳，再确定性渲染当前事实窗口。

        finish_memories 承载宿主按 task 展平的 conclude_activity.memories（签名沿用 AS2，不改锁定提交）；
        阈值巩固只计结束活动写入的条目，就寝反思的条目不计（docs/dev/SPEC-time-and-energy.md 第七章）。
        """
        store = self.store
        if store is None:
            return MemoryStepOutcome((), (), None)
        facts, observation_id = self._current_facts()
        before = hashlib.sha256(store.memory_md_path.read_bytes()).hexdigest()
        known = {str(item["id"]) for item in store.iter_episodes()}
        memories = self._validate_subjective_memories(finish_memories)
        written = await self._append_subjective(memories, tick=tick, t=t, step_count=step_count)
        should, reason = await asyncio.to_thread(self._consolidation_due, sleep_boundary)
        consolidated = False
        if should:
            consolidated = await self._consolidate_subjective(tick, t, step_count, reason)
        if sleep_boundary:
            written.extend(await self._run_reflection(tick=tick, t=t, step_count=step_count))
        view = render_memory_view(
            self._owner._persona_card_text(), facts, store.read_subjective_summary(),
        )
        await asyncio.to_thread(store.write_memory_view, view)
        created, merged = _classify_episodes(written, known)
        return MemoryStepOutcome(created, merged, before, consolidated, observation_id,
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

    def _consolidation_due(self, sleep_boundary: bool) -> tuple[bool, str]:
        """@brief 阈值巩固判定：就寝边界照常触发；否则只按未巩固条目中来源不是就寝反思的条目计条数与重要度。

        阈值沿用 MemoryConsolidationConfig（10 条、重要度合计 3.0）；不调用 AS2 的 should_consolidate，因为它把
        反思条目也计入待巩固条数与重要度。
        """
        if sleep_boundary:
            return True, "sleep boundary"
        config = self.config.consolidation
        pending = [item for item in self._require_store().unconsolidated() if item.get("source") != "reflection"]
        if sum(float(item.get("importance") or 0.0) for item in pending) >= config.importance_accumulation_threshold:
            return True, "importance accumulation threshold"
        if len(pending) >= config.pending_threshold:
            return True, "pending episode threshold"
        return False, ""

    def _validate_subjective_memories(self, raw: Any) -> list[dict[str, Any]]:
        """@brief 只接受结束活动写下的主观条目：九种主观类型、subjective、来源由系统固定为 activity_conclusion。"""
        try:
            memories = self._validate_finish_memories(raw)
        except (TypeError, ValueError):
            logging.error("WorldMock conclusion memory payload is invalid")
            raise
        if any(item["type"] not in _SUBJECTIVE_TYPES or item["epistemic_status"] != "subjective"
               or item["source"] != _CONCLUSION_SOURCE for item in memories):
            logging.error("WorldMock conclusion memory must be subjective and system-sourced")
            raise ValueError("WorldMock conclusion memory must be subjective and system-sourced")
        return memories

    async def _append_subjective(
        self, memories: list[dict[str, Any]], *, tick: int, t: datetime, step_count: int,
    ) -> list[dict[str, Any]]:
        """@brief 复用 AS2 相似合并，任何静默漏写均失败。"""
        if not memories:
            return []
        store = self._require_store()
        written = await asyncio.to_thread(store.append_episodes, memories, tick=tick, t=t, step_count=step_count)
        if not written:
            logging.error("Memory store did not persist finish memories")
            raise RuntimeError("Memory store did not persist finish memories")
        return written

    async def _consolidate_subjective(
        self, tick: int, t: datetime, step_count: int, reason: str,
    ) -> bool:
        """@brief 模型仅归纳主观 episode，绝不接收事实视图作为可重写正文。"""
        store = self._require_store()
        episodes = store.unconsolidated()
        if not episodes:
            return False
        payload = {"reason": reason, "tick": tick, "time": t.isoformat(),
                   "persona_card": self._owner._persona_card_text(),
                   "current_subjective_summary": store.read_subjective_summary(),
                   "new_subjective_episodes": [{key: item.get(key) for key in ("type", "text", "time")}
                                               for item in episodes[-30:]]}
        response = await self._dispatch_llm(
            model=self._require_model_name(),
            messages=[{"role": "system", "content": _SUBJECTIVE_PROMPT},
                      {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            stream=False,
        )
        content = self._consolidation_content(response)
        await asyncio.to_thread(store.mark_subjective_consolidated,
                                content[:4000], step_count=step_count, tick=tick)
        return True


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


def _prompt_memory(identity: str, summary: str) -> str:
    """@brief 当前提示不复用前一轮视图中的事实窗口。"""
    return "## 身份\n" + _quote(identity) + "\n\n## 人物自述（未经核验）\n" + _quote(summary or "暂无人物自述。")


def _quote(text: str) -> str:
    """@brief 将自由文字封入 Markdown 引用，不能跳成顶层事实标题。"""
    return "\n".join("> " + line for line in text.splitlines())


def _fact_line(fact: dict[str, Any]) -> str:
    """@brief 只用封闭类型载荷和来源身份渲染完成事实。"""
    kind, source = fact["kind"], fact["source_event_id"]
    if kind == "waited":
        detail = f"等待 {fact['duration_seconds']} 秒"
    elif kind == "consumed":
        detail = f"在 {fact['location_id']} 食用 {fact['product_id']} {_amount(fact['interval_quantity'])} 份"
    elif kind == "arrived":
        detail = f"到达 {fact['location_id']}，进入状态 {fact['access_status']}"
    elif kind == "played":
        detail = (f"播放 {fact['content_id']} 的媒体位置 "
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
    """@brief 按本步前的稳定身份区分新建与合并，去重反思重复命中。"""
    by_id = {str(item["id"]): item for item in written}
    created = tuple(item for identity, item in by_id.items() if identity not in known)
    merged = tuple(item for identity, item in by_id.items() if identity in known)
    return created, merged

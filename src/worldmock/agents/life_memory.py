"""@brief WorldMock 人物的主观记忆与已提交经历分区运行时。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from agentsociety2.agent.base.react import MemoryUpdateEvidence
from agentsociety2.agent.memory_runtime import MemoryRuntimeConfig, MemoryStepOutcome, PersonMemoryRuntime
from agentsociety2.agent.person_prompt import xml_block
from worldmock.agents.life_memory_store import WorldMockMemoryStore
from worldmock.agents.life_reflection import (REFLECTION_PROMPT, ReflectionOutputError,
                                           reflection_correction, reflection_insights, reflection_tool)
from worldmock.life.memory_evidence import SUBJECTIVE_TYPES, valid_subjective_snapshot
from worldmock.errors import ProtocolError

if TYPE_CHECKING:
    from custom.agents.life_agent import LifeAgent

#: 结束活动主观记忆的固定巩固阈值，与框架通用巩固配置无关。
_PENDING_THRESHOLD = 10
_IMPORTANCE_THRESHOLD = 3.0
#: 结束活动写入点与就寝反思的记忆来源（系统固定，与 memory_updated.data.trigger 同一组取值）。
_CONCLUSION_SOURCE = "activity_conclusion"
_REFLECTION_SOURCE = "reflection"
#: 记忆更新快照的基本字段（与 AS2 PersonAgent._record_memory_update 同一组）。
_SNAPSHOT_FIELDS = ("id", "type", "text", "epistemic_status")
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
        """@brief 记忆上下文只给最近条目窗口与文件路径，不给主观归纳；生活决策提示由七层组成，不读这里的条目与摘要
        （记忆层由环境派生，阈值巩固的摘要不进提示，裁决 #36）。

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
        """@brief 步尾：保存结束活动写下的主观条目、按阈值归纳，再确定性渲染当前事实窗口；不做就寝反思。

        finish_memories 承载宿主展平的 conclude_activity 逐项记忆与看法（经 control_items 读出，看法的 about 即存储的
        subject，写入存储前剥掉）；
        阈值巩固只计结束活动写入的条目，就寝反思的条目不计（docs/design/06-agent.md「进度、待办与记忆」）。
        锁定的 AS2 仍传 sleep_boundary，这里不读它：就寝反思与随它的巩固在取观察之前由 wake_reflection 完成。
        结果不含来源为就寝反思的条目：收尾记忆被存储并进此前相似的感想时，没有新的收尾条目可登记。
        """
        store = self.store
        if store is None:
            return MemoryStepOutcome((), (), None)
        facts, observation_id = self._current_facts()
        before = hashlib.sha256(store.memory_md_path.read_bytes()).hexdigest()
        known = store.episode_ids()
        memories = self._validate_subjective_memories(finish_memories)
        written = await self._append_subjective(memories, tick=tick, t=t, step_count=step_count)
        should, reason = await asyncio.to_thread(self._consolidation_due)
        consolidated = False
        if should:
            consolidated = await self._consolidate_subjective(tick, t, step_count, reason)
        view = render_memory_view(
            self._owner._persona_card_text(), facts, store.read_subjective_summary(),
        )
        await asyncio.to_thread(store.write_memory_view, view)
        concluded = [item for item in written if item.get("source") != _REFLECTION_SOURCE]
        created, merged = _classify_episodes(concluded, known)
        return MemoryStepOutcome(created, merged, before, consolidated, observation_id,
                                 tuple(dict.fromkeys(item["source_event_id"] for item in facts)))

    async def wake_reflection(self, *, tick: int, t: datetime, step_count: int) -> MemoryStepOutcome:
        """@brief 当天第一次睡醒、取观察之前的反思阶段：有未归纳的记录时先巩固一次，再做就寝反思。

        这个阶段不重写 MEMORY.md（巩固出的摘要由同一步的步尾渲染），也不绑定观察：返回的结果没有来源观察与事实窗口。
        人物的记忆里一条记录都没有时不调用模型，返回空结果。结果只含来源为就寝反思的条目：
        感想被存储并进人物此前自己写下的相似记录时，没有新的感想可登记。
        """
        store = self.store
        if store is None:
            return MemoryStepOutcome((), (), None)
        before = hashlib.sha256(store.memory_md_path.read_bytes()).hexdigest()
        known = store.episode_ids()
        consolidated = await self._consolidate_subjective(tick, t, step_count, "daily reflection")
        written = [item for item in await self._run_reflection(tick=tick, t=t, step_count=step_count)
                   if item.get("source") == _REFLECTION_SOURCE]
        created, merged = _classify_episodes(written, known)
        return MemoryStepOutcome(created, merged, before, consolidated)

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

    async def _run_reflection(self, *, tick: int, t: datetime, step_count: int) -> list[dict[str, Any]]:
        """@brief 就寝反思经唯一严格工具提交：没有可反思的记忆时不调用模型，校验通过的洞见才交给 AS2 存储。

        输出不合合同且重试耗尽时由 _validated_reflection 抛出运行故障：此前不写入任何洞见，也不返回空列表冒充空反思
        （docs/design/06-agent.md）。
        """
        store = self._require_store()
        episodes = await asyncio.to_thread(self._reflection_episodes, store)
        if not episodes:
            return []
        insights = await self._validated_reflection(episodes, t)
        return await asyncio.to_thread(store.append_episodes, insights, tick=tick, t=t, step_count=step_count)

    async def _validated_reflection(self, episodes: list[dict[str, Any]], t: datetime) -> list[dict[str, Any]]:
        """@brief 有界校验重试：输出不合合同时附回英文校验错误重新请求，首次之外至多三次，耗尽即运行故障。

        每次尝试是 AS2 记忆调度下的一次独立模型调用（思考关闭、无内部重试，请求与响应各自留证）；附回消息总是接在原始
        两条消息之后，不逐次累积。传输异常原样上抛，不进校验重试。

        :param episodes: 本次交给模型的输入记忆。
        :param t: 当前模拟时间。
        :returns: 校验通过的洞见条目。
        :raises ProtocolError: 首次之外第三次重试仍不合合同；消息带最后一次校验错误原文。
        """
        original = [{"role": "system", "content": REFLECTION_PROMPT}, self._reflection_messages(episodes, t)[1]]
        retries = self._owner.get_profile()["model_identity"]["reflection_validation_max_retries"]
        messages, attempt = original, 1
        while True:
            response = await self._dispatch_llm(model=self._require_model_name(), messages=messages, stream=False,
                                                tools=[reflection_tool()], tool_choice="required")
            try:
                return reflection_insights(response, episodes)
            except ReflectionOutputError as error:
                if attempt > retries:
                    logging.error("Memory reflection validation retry budget exhausted")
                    raise ProtocolError(
                        f"memory reflection output remained invalid after {retries} retries: {error}") from error
                logging.warning("Memory reflection output is invalid on attempt %d: %s", attempt, error)
                messages, attempt = reflection_correction(original, response, error), attempt + 1

    def _consolidation_due(self) -> tuple[bool, str]:
        """@brief 步尾的阈值巩固判定：只按未巩固条目中来源不是就寝反思的条目计条数与重要度。

        宿主固定阈值为十条、重要度合计三；反思条目不计入这两个触发条件。读缓存的只读视图，不复制条目。
        """
        pending = [item for item in self._require_store().unconsolidated_view() if item.get("source") != "reflection"]
        if sum(float(item.get("importance") or 0.0) for item in pending) >= _IMPORTANCE_THRESHOLD:
            return True, "importance accumulation threshold"
        if len(pending) >= _PENDING_THRESHOLD:
            return True, "pending episode threshold"
        return False, ""

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

    async def _consolidate_subjective(
        self, tick: int, t: datetime, step_count: int, reason: str,
    ) -> bool:
        """@brief 模型仅归纳主观 episode，绝不接收事实视图作为可重写正文。"""
        store = self._require_store()
        episodes = store.unconsolidated_view()
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

    @staticmethod
    def _reflection_episodes(store: WorldMockMemoryStore) -> list[dict[str, Any]]:
        """@brief 就寝反思只读取最近三十条宿主主观记忆。"""
        return store.iter_episodes()[-30:]

    @staticmethod
    def _reflection_messages(episodes: list[dict[str, Any]], t: datetime) -> list[dict[str, str]]:
        """@brief 由宿主形成严格反思请求，引用仅来自交给模型的记忆。"""
        payload = {"time": t.isoformat(), "episodes": [
            {key: str(item.get(key) or "") for key in ("id", "type", "time", "text")} for item in episodes]}
        return [{"role": "system", "content": REFLECTION_PROMPT}, {"role": "user", "content": xml_block(
            "memory_reflection_input", json.dumps(payload, ensure_ascii=False, indent=2, default=str))}]


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
    """@brief 按本步前的稳定身份区分新建与合并，去重反思重复命中。"""
    by_id = {str(item["id"]): item for item in written}
    created = tuple(item for identity, item in by_id.items() if identity not in known)
    merged = tuple(item for identity, item in by_id.items() if identity in known)
    return created, merged


def memory_update_evidence(context: Any, store: Any, outcome: MemoryStepOutcome) -> MemoryUpdateEvidence | None:
    """@brief 已落盘记忆的证据（与 AS2 PersonAgent._record_memory_update 同一组字段）；就寝反思的快照条目另带
    source=reflection（docs/design/05-recording-engine.md「事件与来源」）。

    步尾与反思阶段共用：反思阶段的结果没有来源观察，证据的 observation_event_id 为空、事实窗口为空。
    既没有新增或合并条目、视图也没有变化时返回 None（不登记）。
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
    """@brief 一条主观条目的最小快照：四个基本字段，就寝反思另带来源。"""
    snapshot = {key: str(item[key]) for key in _SNAPSHOT_FIELDS}
    if item.get("source") == _REFLECTION_SOURCE:
        snapshot["source"] = _REFLECTION_SOURCE
    if not valid_subjective_snapshot(snapshot):
        logging.error("Subjective memory snapshot is invalid")
        raise RuntimeError("Subjective memory snapshot is invalid")
    return snapshot


def _file_sha256(path: Any) -> str:
    """@brief 文件内容的 SHA-256。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()

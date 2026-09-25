"""@brief 执行工作机会、材料揭示、合同校验和一次性双向付款。"""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from worldmock.errors import BusinessRuleError, IntegrityError, PreconditionError
from worldmock.life.models import (
    ActivityKind,
    ActivityStatus,
    AgentState,
    SubmitWorkRequest,
    WorldState,
    active_activities,
)
from worldmock.life.scenario import RuntimeScenario, WorkTaskConfig
from worldmock.life.settlement import settle_money_transfer

if TYPE_CHECKING:
    from worldmock.life.semantics import ScenarioSemantics


@dataclass(frozen=True, slots=True)
class WorkOutcome:
    """@brief 独立验收后可审计的付款和产出证据。"""

    state: WorldState
    payment_fen: int
    effective_seconds: float
    worked_seconds: int


@dataclass(frozen=True, slots=True)
class WorkMetrics:
    """@brief 付款转换所需的工作量、有效产出和金额。"""

    payment_fen: int
    effective_seconds: float
    worked_seconds: int


@dataclass(frozen=True, slots=True)
class WorkSubmission:
    """@brief 已通过确定性合同前置条件并可交给语义验收的提交。"""

    task: WorkTaskConfig
    agent: AgentState
    request: SubmitWorkRequest
    metrics: WorkMetrics
    oracle: dict[str, Any]
    source: str


def validate_work_start(agent: AgentState, task: WorkTaskConfig, now: int) -> None:
    """@brief 只有已可见、有效期内且技能合格、且在指定地点的工作才能开始。"""
    if task.task_id not in agent.visible_task_ids:
        raise BusinessRuleError("WORK_TASK_NOT_VISIBLE")
    if not _within(task, now):
        raise BusinessRuleError("WORK_TASK_NOT_AVAILABLE")
    unmet = []
    if agent.skill_levels.get(task.required_skill, 0) < task.minimum_skill:
        unmet.append("required_skill")
    if agent.location_id != task.required_location_id:
        unmet.append("required_location_id")
    if unmet:
        raise PreconditionError("ACTIVITY_PRECONDITION_UNMET", unmet=unmet)


def validate_work_duration(state: WorldState, task: WorkTaskConfig, agent_id: int,
                           requested_seconds: int) -> None:
    """@brief 拒绝通过重复启动累计超过合同最大工时。"""
    worked, _ = work_totals(state, agent_id, task.task_id)
    if worked + requested_seconds > task.maximum_duration_seconds:
        raise BusinessRuleError("WORK_DURATION_EXCEEDS_CONTRACT")


def load_work_input(root: Path, task: WorkTaskConfig) -> str:
    """@brief 读取冻结工作输入并校验路径、身份和文本编码。"""
    content = _read_hashed_file(root, task.input_path, task.input_sha256, "Work input")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise IntegrityError("Work input is not UTF-8") from error


def visible_work_material(state: WorldState, root: Path, task: WorkTaskConfig, agent_id: int) -> dict:
    """@brief 按人物已经结算的有效工作量返回可见正文前缀。"""
    source = load_work_input(root, task)
    _, effective = work_totals(state, agent_id, task.task_id)
    ratio = min(1.0, effective / task.required_effective_seconds)
    end = min(len(source), int(len(source) * ratio))
    return {"input_sha256": task.input_sha256, "start_char": 0, "end_char": end,
            "total_chars": len(source), "text": source[:end]}


def reveal_work_progress(state: WorldState, root: Path, task: WorkTaskConfig,
                         agent_id: int, activity_id: str) -> dict:
    """@brief 形成本次工作区间新取得的正文片段并推进活动揭示位置。"""
    material = visible_work_material(state, root, task, agent_id)
    positions = [int(item.data.get("material_position_chars", 0)) for item in state.activities.values()
                 if item.agent_id == agent_id and item.kind == ActivityKind.WORK
                 and item.target_id == task.task_id]
    start = max(positions, default=0)
    end = material["end_char"]
    source = material["text"]
    activity = state.activities[activity_id]
    activity.data["material_position_chars"] = end
    return {**material, "start_char": start, "text": source[start:end]}


def prepare_work_submission(state: WorldState, semantics: "ScenarioSemantics", corpus_root: Path,
                            agent_id: int, request: SubmitWorkRequest) -> WorkSubmission:
    """@brief 校验提交时序、进度、公开身份和付款能力，供一次语义验收。"""
    task = _task(semantics.scenario, request.task_id)
    agent = state.agents[agent_id]
    _validate_submission_contract(state, agent, task)
    worked, effective = work_totals(state, agent_id, task.task_id)
    if effective < task.required_effective_seconds:
        raise BusinessRuleError("WORK_INCOMPLETE")
    semantics.contract_match(task)
    source = load_work_input(corpus_root, task)
    oracle = load_work_oracle(corpus_root, task)
    pieces = oracle.get("piece_count")
    payment = semantics.contract_payment(task, worked, pieces)
    if state.accounts[task.employer_account_id] < payment:
        raise BusinessRuleError("EMPLOYER_FUNDS_INSUFFICIENT")
    return WorkSubmission(task, agent, request, WorkMetrics(payment, effective, worked), oracle, source)


def settle_work(state: WorldState, submission: WorkSubmission, accepted: bool) -> WorkOutcome:
    """@brief 只为已经通过独立语义验收的提交执行一次合同付款。"""
    if not accepted:
        raise BusinessRuleError("WORK_REQUIRES_REWORK")
    return _pay(state, submission.agent, submission.task, submission.metrics)


def load_work_oracle(root: Path, task: WorkTaskConfig) -> dict[str, Any]:
    """@brief 读取人物不可见的官方摘要、决定和行动项。"""
    content = _read_hashed_file(root, task.oracle_path, task.oracle_sha256, "Work oracle")
    try:
        value = json.loads(content)
    except Exception as error:
        raise IntegrityError("Work oracle is invalid JSON") from error
    if not isinstance(value.get("expected"), dict):
        raise IntegrityError("Work oracle expected object is missing")
    value["expected"]["summary"] = _load_reference_summary(root, task, value)
    return value


def _validate_submission_contract(
    state: WorldState,
    agent: AgentState,
    task: WorkTaskConfig,
) -> None:
    """@brief 拒绝已付款、不可见、迟交或正在做的任务；评审准备与提交共用（docs/dev/SPEC-time-and-energy.md「仍须修复」第 4 条）。"""
    if task.task_id in state.paid_task_ids:
        raise BusinessRuleError("WORK_TASK_ALREADY_PAID")
    if task.task_id not in agent.visible_task_ids:
        raise BusinessRuleError("WORK_TASK_NOT_VISIBLE")
    if state.current_time > int(task.due_at.timestamp()):
        raise BusinessRuleError("WORK_SUBMISSION_LATE")
    if any(item.kind == ActivityKind.WORK and item.target_id == task.task_id
           for item in active_activities(state, agent.agent_id)):
        raise BusinessRuleError("WORK_TASK_IN_PROGRESS")


def _task(scenario: RuntimeScenario, task_id: str) -> WorkTaskConfig:
    """@brief 从封闭任务目录取得唯一合同。"""
    matches = [item for item in scenario.tasks if item.task_id == task_id]
    if len(matches) != 1:
        raise BusinessRuleError("WORK_TASK_NOT_FOUND")
    return matches[0]


def _within(task: WorkTaskConfig, now: int) -> bool:
    """@brief 判断当前时刻是否在合同开放区间。"""
    return int(task.available_at.timestamp()) <= now <= int(task.due_at.timestamp())


def work_totals(state: WorldState, agent_id: int, task_id: str) -> tuple[int, float]:
    """@brief 汇总真实结算且未失败的工作活动。"""
    activities = [item for item in state.activities.values() if item.agent_id == agent_id
                  and item.kind == ActivityKind.WORK and item.target_id == task_id
                  and item.status != ActivityStatus.FAILED]
    return sum(item.elapsed_seconds for item in activities), sum(item.effective_work_seconds for item in activities)


def _load_reference_summary(root: Path, task: WorkTaskConfig, oracle: dict) -> list[str]:
    """@brief 按 oracle 指向装载参考摘要句：AMI XML 或 scenario JSON。"""
    source = oracle.get("source_reference")
    if not isinstance(source, str) or not source:
        raise IntegrityError("Work oracle source reference is missing")
    collection = Path(task.oracle_path).parts[0]
    content = _read_regular_file(root, str(Path(collection) / source), "Work reference")
    if source.endswith(".json"):
        return _load_scenario_reference_summary(content)
    return _load_ami_reference_summary(content)


def _load_scenario_reference_summary(content: bytes) -> list[str]:
    """@brief 从 scenario 材料的 JSON 参考读取显式声明的摘要句。"""
    try:
        document = json.loads(content)
    except Exception as error:
        raise IntegrityError("Work reference is invalid JSON") from error
    sentences = document.get("summary_sentences") if isinstance(document, dict) else None
    if not isinstance(sentences, list) or not sentences or any(
            not isinstance(sentence, str) or not sentence.strip() for sentence in sentences):
        raise IntegrityError("Work reference summary sentences are missing")
    return sentences


def _load_ami_reference_summary(content: bytes) -> list[str]:
    """@brief 从官方 AMI XML 提取抽象摘要句（既有语料路径，保留原样）。"""
    try:
        document = ET.fromstring(content)
    except ET.ParseError as error:
        raise IntegrityError("Work reference is invalid XML") from error
    abstract = next((node for node in document if _local_name(node.tag) == "abstract"), None)
    nodes = list(abstract) if abstract is not None else []
    sentences = [" ".join((node.text or "").split()) for node in nodes
                 if _local_name(node.tag) == "sentence"]
    if not sentences or any(not sentence for sentence in sentences):
        raise IntegrityError("Work reference abstract is missing")
    return sentences


def _pay(
    state: WorldState,
    agent: AgentState,
    task: WorkTaskConfig,
    metrics: WorkMetrics,
) -> WorkOutcome:
    """@brief 原子投影雇主扣款、人物收款和一次任务付款。"""
    paid = set(state.paid_task_ids)
    paid.add(task.task_id)
    settled = settle_money_transfer(state, task.employer_account_id, agent.balance_account_id,
                                    metrics.payment_fen, f"work:settle:{task.task_id}")
    changed = settled.model_copy(update={"paid_task_ids": paid}, deep=True)
    return WorkOutcome(changed, metrics.payment_fen, metrics.effective_seconds,
                       metrics.worked_seconds)


def _read_hashed_file(root: Path, relative: str, digest: str, label: str) -> bytes:
    """@brief 读取根目录内普通文件并核对预期身份。"""
    content = _read_regular_file(root, relative, label)
    if hashlib.sha256(content).hexdigest() != digest:
        raise IntegrityError(f"{label} hash mismatch")
    return content


def _read_regular_file(root: Path, relative: str, label: str) -> bytes:
    """@brief 在解析前拒绝越界路径和任意符号链接。"""
    base = Path(root).resolve()
    candidate = base / relative
    path = candidate.resolve()
    if _has_symlink(candidate, base) or base not in path.parents or not path.is_file():
        raise IntegrityError(f"{label} path is invalid")
    return path.read_bytes()


def _has_symlink(path: Path, root: Path) -> bool:
    """@brief 在解析前拒绝文件及父目录符号链接。"""
    current = path
    while current != root and root in current.parents:
        if current.is_symlink():
            return True
        current = current.parent
    return current != root


def _local_name(tag: str) -> str:
    """@brief 去掉 XML 命名空间以比较 AMI 元素名称。"""
    return tag.rsplit("}", 1)[-1]

"""@brief 执行工作机会、材料揭示、合同校验和一次性双向付款。"""

from __future__ import annotations

import hashlib
import json
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from worldmock.corpora.manifest import safe_file
from worldmock.errors import BusinessRuleError, IntegrityError, PreconditionError
from worldmock.life.guidance import Refusal, refusal_for_code
from worldmock.life.models import (
    ActivityKind,
    DERIVED_DURATION_CAP_SECONDS,
    ActivityStatus,
    AgentState,
    SubmitWorkRequest,
    WorldState,
    active_activities,
)
from worldmock.life.readable import duration_phrase, iso_time, local_date
from worldmock.life.scenario import RuntimeScenario, RuntimeWorkTask
from worldmock.life.settlement import settle_money_transfer

if TYPE_CHECKING:
    from worldmock.life.semantics import ScenarioSemantics

#: 开工的最短时长：距截止与剩余合同都不少于 60 秒（docs/dev/SPEC-time-and-energy.md 第八章「工作」）。
WORK_START_MINIMUM_SECONDS = 60
#: 工具层任务目标 contract.outlook 的取值，只在时长上界不足时出现（docs/dev/SPEC-persona-prompt.md「工具层」）。
COMPLETION_OUTLOOK_SHORT = "到截止前做不完"
#: 过了截止仍列出的未付款任务的 contract.outlook（docs/dev/SPEC-persona-prompt.md 第六章「逾期工作从观察和工具中消失」）。
COMPLETION_OUTLOOK_LATE = "已过截止"
#: 正在做这项工作时提交的原因：工时达标、活动状态与提交条件分开说明（docs/dev/SPEC-persona-prompt.md 第六章）。
_IN_PROGRESS_REFUSAL = Refusal("WORK_TASK_IN_PROGRESS", "This work activity is still in progress.",
                             "Conclude or pause this work activity before submitting.")
#: 开工前提不满足时逐条的原因（docs/dev/SPEC-dayrun-fidelity.md 第四章：拒绝要写出具体原因）。
_WORK_START_UNMET = {"required_skill": "The required skill level has not been met.",
                     "required_location_id": "Work requires presence at the declared workplace."}


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

    task: RuntimeWorkTask
    agent: AgentState
    request: SubmitWorkRequest
    metrics: WorkMetrics
    oracle: dict[str, Any]
    source: str


def validate_work_start(agent: AgentState, task: RuntimeWorkTask, now: int, present_location: str | None) -> None:
    """@brief 只有已可见、有效期内且技能合格、且人在指定地点（途中不算）的工作才能开始。

    :param present_location: 人物的现场地点；有进行中出行（途中）时为 None。
    """
    if task.task_id not in agent.visible_task_ids:
        raise BusinessRuleError("WORK_TASK_NOT_VISIBLE")
    if not _within(task, now):
        logging.error("Work start rejected outside the opening interval")
        raise PreconditionError("WORK_TASK_NOT_AVAILABLE", unmet=["available_at"],
                                public_reason=f"Work opens at {task.available_at.isoformat()}.")
    unmet = []
    if agent.skill_levels.get(task.required_skill, 0) < task.minimum_skill:
        unmet.append("required_skill")
    if present_location != task.required_location_id:
        unmet.append("required_location_id")
    if unmet:
        raise PreconditionError("ACTIVITY_PRECONDITION_UNMET", unmet=unmet,
                                public_reason="; ".join(_WORK_START_UNMET[item] for item in unmet))


def work_start_seconds(task: RuntimeWorkTask, worked_seconds: int, now: int) -> int:
    """@brief 开工的派生时长：剩余合同秒数、距截止秒数与活动上限 86400 秒中的最小值。"""
    return min(task.maximum_duration_seconds - worked_seconds, int(task.due_at.timestamp()) - now,
               DERIVED_DURATION_CAP_SECONDS)


def work_start_refusal(task: RuntimeWorkTask, worked_seconds: int, now: int) -> Refusal | None:
    """@brief 开工与恢复共用距截止、剩余合同的阻断原因和下一步。"""
    if int(task.due_at.timestamp()) - now < WORK_START_MINIMUM_SECONDS:
        reason = "Less than one minute remains before the deadline; work cannot start."
    elif task.maximum_duration_seconds - worked_seconds < WORK_START_MINIMUM_SECONDS:
        reason = "Less than one minute remains in the contract; work cannot start."
    else:
        return None
    return Refusal("ACTIVITY_DURATION_INVALID", reason, "Submit the completed work.")


def work_resume_cap(task: RuntimeWorkTask, worked_seconds: int, now: int) -> int:
    """@brief 恢复工作的时长上限：min(距截止秒数, 合同上限减已累计秒数)，恢复不越过合同截止（规格第六章）。"""
    return min(int(task.due_at.timestamp()) - now, task.maximum_duration_seconds - worked_seconds)


def completion_outlook(task: RuntimeWorkTask, worked_seconds: int, effective_seconds: float, now: int) -> str | None:
    """@brief 过了截止为「已过截止」；否则剩余要求的有效秒数大于 min(距截止秒数, 剩余合同秒数) 时为「到截止前做不完」，
    其余为 None。

    按时长上界判定（有效秒数不超过实际做的秒数），不暴露效率；截止同秒不算过了截止。
    """
    if now > int(task.due_at.timestamp()):
        return COMPLETION_OUTLOOK_LATE
    needed = task.required_effective_seconds - effective_seconds
    available = min(int(task.due_at.timestamp()) - now, task.maximum_duration_seconds - worked_seconds)
    return COMPLETION_OUTLOOK_SHORT if needed > available else None


def task_listed(task: RuntimeWorkTask, now: int, timezone_name: str) -> bool:
    """@brief 可见的未付款任务是否仍在观察与工具层中：截止前，或截止当日的本地日结束前（截止当天过了截止仍保留）。

    逾期保留只为说明原因：contract.outlook 写「已过截止」，submit_work 标不可用，提交以 WORK_SUBMISSION_LATE 业务拒绝；
    不新增迟交规则（docs/dev/SPEC-persona-prompt.md 第六章「逾期工作从观察和工具中消失」）。
    """
    due = int(task.due_at.timestamp())
    return now <= due or local_date(now, timezone_name) == local_date(due, timezone_name)


def contract_view(task: RuntimeWorkTask, totals: tuple[int, float], now: int,
                  context: tuple[str, str]) -> dict:
    """@brief 工具层任务目标的 contract：截止时刻、有效工时的达标情况与到截止前能否做完。

    remaining 例：「有效工时已达标（约 6.4/4 小时）」「有效工时还差约 1.5 小时（约 2.5/4 小时）」「有效工时还差约 1 分钟
    （约 0/0.02 小时）」；有效工时取一位小数，要求至多两位小数（docs/dev/SPEC-persona-prompt.md「工具层」与「通用格式」：
    时长写成人读短语，与 readable 的小数口径一致）。

    :param totals: work_totals 的（已做秒数，有效秒数）。
    """
    timezone_name, place = context
    worked, effective = totals
    required = task.required_effective_seconds
    hours = f"约 {f'{effective / 3600:.1f}'.removesuffix('.0')}/{round(required / 3600, 2):g} 小时"
    if effective >= required:
        remaining = f"有效工时已达标（{hours}）"
    else:
        remaining = f"有效工时还差{duration_phrase(required - effective)}（{hours}）"
    return {"opens": iso_time(int(task.available_at.timestamp()), timezone_name),
            "deadline": iso_time(int(task.due_at.timestamp()), timezone_name), "place": place,
            "remaining": remaining, "outlook": completion_outlook(task, worked, effective, now)}


def validate_work_duration(state: WorldState, task: RuntimeWorkTask, agent_id: int,
                           requested_seconds: int) -> None:
    """@brief 拒绝通过重复启动累计超过合同最大工时。"""
    worked, _ = work_totals(state, agent_id, task.task_id)
    if worked + requested_seconds > task.maximum_duration_seconds:
        raise BusinessRuleError("WORK_DURATION_EXCEEDS_CONTRACT")


def load_work_input(root: Path, task: RuntimeWorkTask) -> str:
    """@brief 读取冻结工作输入并校验路径、身份和文本编码。"""
    content = _read_hashed_file(root, task.input_path, task.input_sha256, "Work input")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise IntegrityError("Work input is not UTF-8") from error


def visible_work_material(state: WorldState, root: Path, task: RuntimeWorkTask, agent_id: int) -> dict:
    """@brief 按人物已经结算的有效工作量返回可见正文前缀。"""
    source = load_work_input(root, task)
    _, effective = work_totals(state, agent_id, task.task_id)
    ratio = min(1.0, effective / task.required_effective_seconds)
    end = min(len(source), int(len(source) * ratio))
    return {"input_sha256": task.input_sha256, "start_char": 0, "end_char": end,
            "total_chars": len(source), "text": source[:end]}


def reveal_work_progress(state: WorldState, root: Path, task: RuntimeWorkTask,
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
    refusal = submission_refusal(state, agent_id, task)
    if refusal is not None:
        raise refusal.error()
    worked, effective = work_totals(state, agent_id, task.task_id)
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


def load_work_oracle(root: Path, task: RuntimeWorkTask) -> dict[str, Any]:
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


def submission_refusal(state: WorldState, agent_id: int, task: RuntimeWorkTask) -> Refusal | None:
    """@brief 提交一项工作时第一条不成立的条件：已结算、不可见、已过截止、正在做、有效工时不够；成立时为 None。

    评审准备、受理与工具层 submit_work 的可用性共用这一判定（docs/dev/SPEC-time-and-energy.md「仍须修复」第 4 条，
    docs/dev/SPEC-persona-prompt.md 第六章）：正在做时写「先结束正在做的工作」，与达标情况分开说明。
    """
    if task.task_id in state.paid_task_ids:
        return refusal_for_code("WORK_TASK_ALREADY_PAID")
    if task.task_id not in state.agents[agent_id].visible_task_ids:
        return refusal_for_code("WORK_TASK_NOT_VISIBLE")
    if state.current_time > int(task.due_at.timestamp()):
        return refusal_for_code("WORK_SUBMISSION_LATE")
    if any(item.kind == ActivityKind.WORK and item.target_id == task.task_id
           for item in active_activities(state, agent_id)):
        return _IN_PROGRESS_REFUSAL
    if work_totals(state, agent_id, task.task_id)[1] < task.required_effective_seconds:
        return refusal_for_code("WORK_INCOMPLETE")
    return None


def _task(scenario: RuntimeScenario, task_id: str) -> RuntimeWorkTask:
    """@brief 从封闭任务目录取得唯一合同。"""
    matches = [item for item in scenario.tasks if item.task_id == task_id]
    if len(matches) != 1:
        raise BusinessRuleError("WORK_TASK_NOT_FOUND")
    return matches[0]


def _within(task: RuntimeWorkTask, now: int) -> bool:
    """@brief 判断当前时刻是否在合同开放区间。"""
    return int(task.available_at.timestamp()) <= now <= int(task.due_at.timestamp())


def work_totals(state: WorldState, agent_id: int, task_id: str) -> tuple[int, float]:
    """@brief 汇总真实结算且未失败的工作活动。"""
    activities = [item for item in state.activities.values() if item.agent_id == agent_id
                  and item.kind == ActivityKind.WORK and item.target_id == task_id
                  and item.status != ActivityStatus.FAILED]
    return sum(item.elapsed_seconds for item in activities), sum(item.effective_work_seconds for item in activities)


def _load_reference_summary(root: Path, task: RuntimeWorkTask, oracle: dict) -> list[str]:
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
    task: RuntimeWorkTask,
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
    """@brief 通过唯一安全文件规则读取工作来源。"""
    return safe_file(root, relative, f"{label} path is invalid").read_bytes()


def _local_name(tag: str) -> str:
    """@brief 去掉 XML 命名空间以比较 AMI 元素名称。"""
    return tag.rsplit("}", 1)[-1]

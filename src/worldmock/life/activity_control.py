"""@brief 活动名额、待收尾、可读名称、客观进度与控制记录。

依据 docs/dev/SPEC-time-and-energy.md 第七章「活动的生命周期」与第八章「活动」：
- 每人两个活动名额，进行中、暂停中与待收尾的活动各占一个，只有 conclude_activity 之后才释放；
- 待收尾是状态为 completed、failed 或 cancelled 且 concluded_at 为空的活动（truncated 不收尾），ActivityStatus 闭集不变；
- 活动名称是「<做什么><对象>（<M月D日> HH:MM 起）」；同一轮可见的名称重复时追加「（第二次）」这类序号，不抛错；
- 进度层写活动客观进度的人读时长，自述标「你自己记下的，未经核验」；
- conclude_activity、pause_activity 与 report_status 的恢复每改变一项活动就写一条 environment_event
  （activity_concluded、activity_paused、activity_resumed，带活动快照与含本次请求事件的 caused_by）；
  原始动作的全部子活动都终结时按自然结束的同一路径写一次终态并更新 processed_actions；
- 实验截止截断的每项活动（进行中或暂停）写一条带截断快照的 environment_event（activity_truncated）：同源两项的
  聚合终态不带快照，导出据逐项快照重建，与最终检查点逐项一致。
引导性拒绝（ACTIVITY_CONCLUSION_REQUIRED、SESSION_LIMIT_EXCEEDED、ACTIVITY_PRECONDITION_UNMET、
ACTIVITY_PAUSE_UNAVAILABLE）以链式原因写出要先处理的活动或地点，拒绝消息据此点名。引擎只接线。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from worldmock.errors import BusinessRuleError
from worldmock.footprints.models import EventDraft
from worldmock.life.guidance import Refusal, guided
from worldmock.life.action_evidence import (_origin_terminal_draft, _origin_terminal_result,
                                            _terminal_action_records)
from worldmock.life.models import (CONCLUDABLE_TERMINAL_STATUSES, ActivityKind, ActivityState, ActivityStatus,
                                   PendingAction, WorldState)
from worldmock.life.sessions import at_stay, moment_label
from worldmock.life.work import work_totals

#: 每人活动名额（常量，不设场景参数）。
SLOT_LIMIT = 2
#: 人物自述在提示中的标注。
SELF_REPORT_MARK = "你自己记下的，未经核验"
#: 活动阶段的人读说法：进行中、已暂停、刚结束（待收尾）、已收尾、已结束（实验截止截断）。
PHASE_ACTIVE, PHASE_PAUSED, PHASE_PENDING, PHASE_CONCLUDED, PHASE_ENDED = (
    "进行中", "已暂停", "刚结束", "已收尾", "已结束")
#: 控制工具 → 其改变活动时写的 environment_event 种类（播报只在恢复暂停活动时留记录）。
_LIFECYCLE_KINDS = {"conclude_activity": "activity_concluded", "pause_activity": "activity_paused",
                    "report_status": "activity_resumed"}
#: 实验截止截断活动时逐项写的 environment_event 种类。
TRUNCATED_KIND = "activity_truncated"
_TERMINAL_STATUSES = frozenset({*CONCLUDABLE_TERMINAL_STATUSES, ActivityStatus.TRUNCATED})
_CHINESE_DIGITS = "零一二三四五六七八九"


@dataclass(frozen=True, slots=True)
class RecordContext:
    """@brief 写活动记录所需的引擎投影：活动快照与内容标题。"""

    snapshot: Callable[[ActivityState], dict]
    title: Callable[[ActivityState], str | None]


def is_pending_conclusion(activity: ActivityState) -> bool:
    """@brief 待收尾：由世界或人物结束（completed、failed、cancelled）而尚未 conclude_activity。"""
    return activity.status in CONCLUDABLE_TERMINAL_STATUSES and activity.concluded_at is None


def pending_conclusions(state: WorldState, agent_id: int) -> tuple[ActivityState, ...]:
    """@brief 人物全部待收尾活动，按开始时刻与身份稳定排序。"""
    return _agent_activities(state, agent_id, is_pending_conclusion)


def slot_activities(state: WorldState, agent_id: int) -> tuple[ActivityState, ...]:
    """@brief 占名额的活动：进行中、暂停中与待收尾。"""
    return _agent_activities(state, agent_id, occupies_slot)


def occupies_slot(activity: ActivityState) -> bool:
    """@brief 活动是否占一个名额（进行中、暂停中或待收尾）。"""
    return activity.status in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED} or is_pending_conclusion(activity)


def free_slots(state: WorldState, agent_id: int) -> int:
    """@brief 人物剩余的活动名额。"""
    return SLOT_LIMIT - len(slot_activities(state, agent_id))


def new_activity_count(actions: list[str], labels: list[str]) -> int:
    """@brief 一个 start_activity 请求将占用的名额：到店购买与远程下单不占，同短语的两份食用合成一餐。"""
    if len(set(labels)) == 1 and actions[0] in {"purchase", "consume"}:
        return 0 if actions[0] == "purchase" else 1
    return len(actions) - actions.count("purchase")


def resumable_here(state: WorldState, activity: ActivityState) -> bool:
    """@brief 暂停的活动此刻能否恢复：人物正停留在它的开始地点（路上或别处都不能恢复，可以直接结束）。"""
    return activity.status == ActivityStatus.PAUSED and at_stay(state, activity.agent_id, activity.location_id)


def activity_phase(activity: ActivityState) -> str:
    """@brief 进度层与回看的活动阶段说法。"""
    if activity.status == ActivityStatus.ACTIVE:
        return PHASE_ACTIVE
    if activity.status == ActivityStatus.PAUSED:
        return PHASE_PAUSED
    if is_pending_conclusion(activity):
        return PHASE_PENDING
    return PHASE_CONCLUDED if activity.concluded_at is not None else PHASE_ENDED


def activity_name(label: str, object_name: str, epoch: int, timezone_name: str) -> str:
    """@brief 活动名称「<做什么><对象>（<M月D日> HH:MM 起）」。"""
    return f"{label}{object_name}（{moment_label(epoch, timezone_name)} 起）"


def object_display_name(binding: dict) -> str:
    """@brief 交互对象在活动名称中的说法：「自己」不写对象，带类别前缀的对象只取名称部分。"""
    if binding["kind"] == "self":
        return ""
    label = binding["object"]
    return label.split("：", 1)[1] if "：" in label else label


def readable_duration(seconds: float) -> str:
    """@brief 人读时长：不到一分钟、约 N 分钟或约 N 小时（半小时精度）。"""
    if seconds < 60:
        return "不到1分钟"
    if seconds < 3600:
        return f"约{round(seconds / 60)}分钟"
    return f"约{round(seconds / 1800) / 2:g}小时"


def objective_progress(activity: ActivityState, remaining_required: float | None = None) -> str:
    """@brief 活动客观进度的人读时长：已做、工作的有效时长与还差、进行或暂停中的还剩。

    :param remaining_required: 工作活动所属任务还差的有效秒数（取 work_totals 累计），非工作活动为 None。
    """
    parts = [f"已做{readable_duration(activity.elapsed_seconds)}"]
    if activity.kind == ActivityKind.WORK:
        parts.append(f"有效{readable_duration(activity.effective_work_seconds)}")
        if remaining_required is not None and remaining_required > 0:
            parts.append(f"还差{readable_duration(remaining_required)}")
    if activity.status in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}:
        remaining = max(0, activity.planned_duration_seconds - activity.elapsed_seconds)
        parts.append(f"还剩{readable_duration(remaining)}")
    return "，".join(parts)


def remaining_required(state: WorldState, activity: ActivityState, required: dict[str, float]) -> float | None:
    """@brief 工作活动所属任务还差的有效秒数：合同要求减去真实结算的有效工时累计（work_totals）。

    :param required: 任务身份→合同要求的有效秒数；非工作活动或未知任务返回 None。
    """
    if activity.kind != ActivityKind.WORK or activity.target_id not in required:
        return None
    _, effective = work_totals(state, activity.agent_id, activity.target_id)
    return max(0.0, required[activity.target_id] - effective)


def visible_names(state: WorldState, agent_id: int, taken: Iterable[str]) -> dict[str, dict[str, str]]:
    """@brief 本轮人物可见的会话名与活动名；与任务名、目标名或彼此重名时追加「（第二次）」这类序号。

    :returns: {"sessions": 会话身份→名称, "activities": 活动身份→名称}，按开始时刻与身份的稳定顺序分配序号。
    """
    seen: Counter[str] = Counter(taken)
    sessions = sorted((item for item in state.sessions.values() if item.agent_id == agent_id),
                      key=lambda item: (item.started_at, item.session_id))
    session_names = {item.session_id: _unique(item.name, seen) for item in sessions}
    activities = _agent_activities(state, agent_id, lambda item: True)
    activity_names = {item.activity_id: _unique(item.name or _kind_name(item, state.timezone), seen)
                      for item in activities}
    return {"sessions": session_names, "activities": activity_names}


def with_note(activity: ActivityState, summary: str, now: int, anchor: bool = False) -> ActivityState:
    """@brief 写入新的人物自述并保留前一条；播报另把播报锚点移到当前时刻。"""
    history = list(activity.note_history)
    if activity.progress_note:
        history.append({"note": activity.progress_note, "updated_at": activity.note_updated_at})
    update = {"progress_note": summary, "note_updated_at": now, "note_history": history}
    if anchor:
        update["report_anchor_at"] = now
    return activity.model_copy(update=update, deep=True)


def concluded(activity: ActivityState, summary: str, now: int) -> ActivityState:
    """@brief 收尾：写入总结自述并置 concluded_at，释放名额；终态不变。"""
    return with_note(activity, summary, now).model_copy(update={"concluded_at": now}, deep=True)


def conclusion_required(activities: Iterable[ActivityState], names: dict[str, str]) -> BusinessRuleError:
    """@brief 有待收尾活动时做别的：ACTIVITY_CONCLUSION_REQUIRED，原因与下一步点名刚结束的活动。"""
    listed = "、".join(names.get(item.activity_id, item.name) for item in activities)
    return guided("ACTIVITY_CONCLUSION_REQUIRED", f"先给刚结束的{listed}收尾", f"给{listed}收尾")


def require_start_admission(state: WorldState, agent_id: int, request: tuple[list[str], list[str]],
                            names: dict[str, str]) -> None:
    """@brief 开始请求的名额与出发前判定：名额不足报 SESSION_LIMIT_EXCEEDED，出发前仍有进行中活动报前置条件。

    名额不足的原因分两种说法：两个名额都占着时请人物先结束一件；只剩一个名额却要同时开始两项时说明只剩一个名额
    （docs/dev/SPEC-time-and-energy.md 第五章「并行准入」）。两种都点名占着名额的活动，下一步都是先结束其中一项。

    :param request: (内部标准动作列表, 方式短语列表)，与请求 targets 逐项对应。
    :param names: 活动身份→本轮可读名称，用于在原因与下一步中点名要先处理的活动。
    """
    refusal = start_admission_refusal(state, agent_id, request, names)
    if refusal is not None:
        raise refusal.error()


def start_admission_refusal(state: WorldState, agent_id: int, request: tuple[list[str], list[str]],
                            names: dict[str, str]) -> Refusal | None:
    """@brief require_start_admission 的判定本身：不成立时返回原因与下一步，成立时为 None（交互对象层据此写说明）。"""
    actions, labels = request
    free = free_slots(state, agent_id)
    if new_activity_count(actions, labels) > free:
        listed = "、".join(names.get(item.activity_id, item.name) for item in slot_activities(state, agent_id))
        reason = (f"只剩一个名额，不能同时开始两项（{listed}占着一个）" if free > 0
                  else f"两件事都在做，先结束一件：{listed}")
        return Refusal("SESSION_LIMIT_EXCEEDED", reason, f"先结束一项：{listed}")
    running = [item for item in _agent_activities(state, agent_id, _is_active)]
    if "travel_to" in actions and running:
        listed = [names.get(item.activity_id, item.name) for item in running]
        return Refusal("ACTIVITY_PRECONDITION_UNMET", f"需要先结束或暂停手上的事：{'、'.join(listed)}",
                       unmet=("activities_before_departure",), suggested={"conclude_or_pause": listed})
    return None


def record_changes(before: WorldState, after: WorldState, pending: PendingAction,
                   context: RecordContext) -> tuple[WorldState, list[EventDraft]]:
    """@brief 本次控制改变的每项活动写一条 environment_event；原始动作全部子活动终结时写一次终态。"""
    drafts = _lifecycle_drafts(before, after, pending, context.snapshot)
    changed, terminal = _terminal_records(before, after, pending.requested_event_id, context.title)
    return changed, drafts + terminal


def truncation_record(activity: ActivityState, snapshot: dict, now: int) -> EventDraft:
    """@brief 实验截止截断的一项活动（此前进行中或暂停）写一条带截断快照的 environment_event。

    同源两项的聚合终态不带快照，暂停的活动此前只有暂停时的快照；逐项写截断快照，导出的活动才与最终检查点逐项一致
    （docs/dev/SPEC-time-and-energy.md 第八章「活动」）。原因回链开始该活动的请求事件。

    :param snapshot: 截断后的活动权威快照（引擎的活动快照投影）。
    """
    return EventDraft(event_type="environment_event", occurred_at=now, agent_id=activity.agent_id,
                      activity_id=activity.activity_id, caused_by=[activity.origin_request_event_id],
                      source_kind="environment",
                      data={"kind": TRUNCATED_KIND, "activity_id": activity.activity_id, "activity": snapshot})


def _lifecycle_drafts(before: WorldState, after: WorldState, pending: PendingAction,
                      snapshot: Callable[[ActivityState], dict]) -> list[EventDraft]:
    """@brief 控制工具改变（状态或收尾时刻变化）的每项本人活动各写一条带快照的 environment_event。"""
    kind = _LIFECYCLE_KINDS.get(pending.tool_name)
    if kind is None:
        return []
    drafts = []
    for activity_id in sorted(after.activities):
        old, new = before.activities.get(activity_id), after.activities[activity_id]
        if (old is None or new.agent_id != pending.agent_id
                or (old.status, old.concluded_at) == (new.status, new.concluded_at)):
            continue
        drafts.append(EventDraft(
            event_type="environment_event", occurred_at=after.current_time, agent_id=new.agent_id,
            decision_id=pending.decision_id, action_id=pending.action_id, activity_id=activity_id,
            caused_by=[pending.requested_event_id], source_kind="environment",
            data={"kind": kind, "activity_id": activity_id, "activity": snapshot(new)}))
    return drafts


def _terminal_records(before: WorldState, after: WorldState, request_event_id: str,
                      title: Callable[[ActivityState], str | None]) -> tuple[WorldState, list[EventDraft]]:
    """@brief 本次新终结的活动：其原始动作的全部子活动都终结时更新 processed_actions 并写一次终态。"""
    newly = [item for key, item in sorted(after.activities.items()) if item.status in _TERMINAL_STATUSES
             and key in before.activities and before.activities[key].status not in _TERMINAL_STATUSES]
    drafts = []
    for origin in dict.fromkeys(item.origin_action_id for item in newly):
        activity = next(item for item in newly if item.origin_action_id == origin)
        result = _origin_terminal_result(after, activity, after.current_time, title(activity))
        if result is None:
            continue
        after = after.model_copy(update={"processed_actions": _terminal_action_records(after, result)})
        drafts.append(_origin_terminal_draft(after, activity, after.current_time, [request_event_id],
                                             title(activity)))
    return after, drafts


def _agent_activities(state: WorldState, agent_id: int,
                      predicate: Callable[[ActivityState], bool]) -> tuple[ActivityState, ...]:
    """@brief 人物满足条件的活动，按开始时刻与身份稳定排序。"""
    return tuple(sorted((item for item in state.activities.values()
                         if item.agent_id == agent_id and predicate(item)),
                        key=lambda item: (item.started_at, item.activity_id)))


def _is_active(activity: ActivityState) -> bool:
    """@brief 活动是否正在进行。"""
    return activity.status == ActivityStatus.ACTIVE


def _unique(name: str, seen: Counter[str]) -> str:
    """@brief 首次出现保留原名，重复时追加「（第N次）」并登记新名，保证同轮名称唯一。"""
    count = seen[name]
    seen[name] += 1
    if count == 0:
        return name
    candidate = f"{name}（第{_chinese_count(count + 1)}次）"
    seen[candidate] += 1
    return candidate


def _chinese_count(number: int) -> str:
    """@brief 两位以内的中文序数：二、十、十一、二十三。"""
    if number < 10:
        return _CHINESE_DIGITS[number]
    tens, ones = divmod(number, 10)
    prefix = "十" if tens == 1 else f"{_CHINESE_DIGITS[tens]}十"
    return prefix + (_CHINESE_DIGITS[ones] if ones else "")


def _kind_name(activity: ActivityState, timezone_name: str) -> str:
    """@brief 未经受理路径命名的活动按种类与开始时刻称呼。"""
    return f"{activity.kind.value}（{moment_label(activity.started_at, timezone_name)} 起）"

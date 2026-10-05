"""@brief 活动名额、待收尾、可读名称、客观进度与控制记录。

依据 docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第七章「活动的生命周期」与第八章「活动」：
- 每人两个活动名额，进行中、暂停中与待收尾的活动各占一个，只有 conclude_activity 之后才释放；
- 待收尾是状态为 completed、failed 或 cancelled 且 concluded_at 为空的活动（truncated 不收尾），ActivityStatus 闭集不变；
- 活动名称是「<做什么><对象>（<M月D日> HH:MM 起）」；同一轮可见的名称重复时追加「（第二次）」这类序号，不抛错；
- 工具层活动目标与待办层写活动客观进度的人读短语（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「工具层」「待办层」）：工作不写
  「还差」，剩余只在任务目标的 contract；刚结束的活动另写终局说法；
- 控制工具（播报与恢复、暂停、结束）的准入由 control_refusal 判定，受理与工具层可用性共用同一 Refusal；所写的活动名
  逐项按统一名称解析，解析不出的各项合并成一次拒绝（activity_name_refusal，docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-统一名称解析  第
   5 条）；
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
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from worldmock.errors import BusinessRuleError
from worldmock.footprints.models import EventDraft
from worldmock.life import errands
from worldmock.life.activities import at_decision_boundary
from worldmock.life.error_contract import ErrorItem, items_fix
from worldmock.life.guidance import Refusal, items_refusal, refusal_for_code
from worldmock.life.vocabulary import GIVE_WAY
from worldmock.life.action_evidence import (_origin_terminal_draft, _origin_terminal_result,
                                            _terminal_action_records)
from worldmock.life.models import (CONCLUDABLE_TERMINAL_STATUSES, ActivityKind, ActivityState, ActivityStatus,
                                   PendingAction, WorldState, active_activities)
from worldmock.life.name_resolution import near_names
from worldmock.life.object_labels import resolve_name
from worldmock.life.readable import duration_phrase, portion_phrase
from worldmock.life.sessions import at_stay, moment_label, on_road
from worldmock.life.work import work_start_refusal, work_totals

if TYPE_CHECKING:
    from worldmock.life.admission import Context

#: 每人活动名额（常量，不设场景参数）。
SLOT_LIMIT = 2
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
    """@brief 一个 start_activity 请求将占用的名额：到店购买、远程下单与给予是瞬时的，不占；同短语的两份食用合成一餐。"""
    if len(set(labels)) == 1 and actions[0] in {"purchase", "consume"} and labels[0] != GIVE_WAY:
        return 0 if actions[0] == "purchase" else 1
    return sum(1 for action, label in zip(actions, labels) if action != "purchase" and label != GIVE_WAY)


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


def objective_progress(activity: ActivityState) -> str:
    """@brief 活动客观进度的人读短语：已做时长、工作的有效时长、进行或暂停中的还剩时长；食用只写「已吃 X 份，剩 Y 份」
    （docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「待办层」，不另写还剩时长）。

    工作不写「还差」：合同剩余只在工具层任务目标的 contract（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「工具层」）。
    """
    if activity.kind == ActivityKind.CONSUME and activity.data.get("request", {}).get("use_mode") == "eat":
        planned = sum(item["quantity"] for item in activity.data["request"]["items"])
        eaten = sum(activity.data.get("consumed", {}).values())
        return f"已吃 {portion_phrase(eaten)}，剩 {portion_phrase(max(0.0, planned - eaten))}"
    parts = [f"已做{duration_phrase(activity.elapsed_seconds)}"]
    if activity.kind == ActivityKind.WORK:
        parts.append(f"有效{duration_phrase(activity.effective_work_seconds)}")
    if activity.status in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}:
        remaining = max(0, activity.planned_duration_seconds - activity.elapsed_seconds)
        parts.append(f"还剩{duration_phrase(remaining)}")
    return "，".join(parts)


def ending_phrase(activity: ActivityState) -> str:
    """@brief 已结束活动的终局说法：到达、按计划结束、提前结束、对方结束或失败（工具层刚结束的状态与 settled 句共用）。"""
    if activity.status == ActivityStatus.FAILED:
        return "失败"
    if activity.boundary_type == "partner_ended":
        return "对方结束"
    if activity.kind == ActivityKind.TRAVEL and activity.status == ActivityStatus.COMPLETED:
        return "到达"
    if activity.status == ActivityStatus.COMPLETED and activity.boundary_type == "planned_end":
        return "按计划结束"
    return "提前结束"


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


def conclusion_refusal(activities: Iterable[ActivityState], names: dict[str, str]) -> Refusal:
    """@brief 有待收尾活动时做别的：ACTIVITY_CONCLUSION_REQUIRED，原因与下一步点名刚结束的活动。"""
    listed = "、".join(names.get(item.activity_id, item.name) for item in activities)
    return Refusal("ACTIVITY_CONCLUSION_REQUIRED", f"Conclude the finished activities {listed} first.",
                   f"Conclude {listed}.")


def conclusion_required(activities: Iterable[ActivityState], names: dict[str, str]) -> BusinessRuleError:
    """@brief conclusion_refusal 换成可抛出的业务拒绝（受理路径）。"""
    return conclusion_refusal(activities, names).error()


def activity_name_refusal(written: Sequence[str], visible: Sequence[str]) -> Refusal | None:
    """@brief 控制工具（播报、暂停、结束）逐项所写的活动名在本轮可见的活动名中按统一名称解析（精确或无损归一后唯一，
    object_labels.resolve_name，与宿主本地校验同一函数）；解析不出的各项合并成的一次拒绝，都唯一解析时为 None。

    名称类失败逐项报全部（docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-统一名称解析  第 5 条、第四章）：找不到的明细码为 NAME_NOT_FOUND，
    原因
    列出最接近的几个活动名；归一后（如没写开始时刻）对应不止一项的为 NAME_AMBIGUOUS，原因列出它们。明细的参数名为
    activity_name、原文照录，不带候选（可照填的候选是 start_activity 形状的请求），与宿主纠错中活动名的明细相同；原因逐项写
    「第 N 项「原文」：…」（guidance.items_refusal），改法按码各写一句。宿主已在本轮纠正这些名称，这里只防绕过宿主的请求，
    所以顶层码与 start_activity 的防御拒绝（session_actions._unresolved_start）一样取 ACTION_REQUEST_INVALID，下一步取该码的
    默认做法：它不在名称类拒绝码闭集（error_contract.NAME_REJECTION_CODES）中，照常计入同刻轮数、照常施加情绪脉冲。

    :param written: 请求逐项所写的活动名，按请求中的先后。
    :param visible: 本轮冻结观察中人物所见的活动名（activity_bindings 的 name）。
    """
    total = len(written)
    entries = [entry for index, name in enumerate(written)
               if (entry := _name_entry(index, name, visible)) is not None]
    if not entries:
        return None
    merged = items_refusal(entries, total)
    default = refusal_for_code("ACTION_REQUEST_INVALID")
    return Refusal("ACTION_REQUEST_INVALID", merged.reason, default.next_step,
                   fix=items_fix(merged.items, total, _name_fixes(total)) or "", items=merged.items)


def _name_entry(index: int, written: str, visible: Sequence[str]) -> tuple[ErrorItem, str] | None:
    """@brief 一项所写活动名的名称类失败：（明细，这一项的一句原因）；唯一解析时为 None。"""
    match = resolve_name(written, visible)
    if match.name is not None:
        return None
    if match.matches:
        return ErrorItem(("items", index, "activity_name"), written, "NAME_AMBIGUOUS",
                         expected="An unambiguous known activity name is required.",
                         why="The activity name matches several activities."), (
                             f"The name identifies several activities: {_quoted(match.matches)}.")
    near = near_names(written, visible)
    closest = f" Nearby known names: {_quoted(near)}." if near else ""
    return ErrorItem(("items", index, "activity_name"), written, "NAME_NOT_FOUND",
                     expected="A known activity name is required.", why="The activity name is not known."), (
                         f"The activity name is not known.{closest}")


def _name_fixes(total: int) -> dict[str, str]:
    """@brief 活动名的名称类失败按明细码的改法：找不到的照工具层、待办层中的活动名填写，多项请求也可以去掉这一项（与宿主
    纠错一样）；有歧义的改写成原因里列出的某一个完整名称。"""
    return {"NAME_NOT_FOUND": "Use an exact known activity name.",
            "NAME_AMBIGUOUS": "Use an unambiguous complete activity name."}


def _quoted(names: Iterable[str]) -> str:
    """@brief 几个名称的说法「A」、「B」。"""
    return "、".join(f"「{name}」" for name in names)


def control_refusal(context: Context, tool: str, activity: ActivityState) -> Refusal | None:
    """@brief 控制工具点名一项活动时第一条不成立的准入，成立时为 None；受理与工具层、待办层的可用性共用它。

    report_status：只接受进行中或暂停的活动；暂停的活动只在开始地点恢复，工作的恢复与开工同一截止约束（距截止与剩余
    合同都不少于 60 秒），恢复时与进行中活动的占用不能冲突。
    pause_activity：只暂停进行中的活动；路上的活动、交谈与通话不能暂停；须在活动的决策边界。
    conclude_activity：只结束占名额的活动；有别的待收尾活动时先收尾；进行中的活动须在决策边界（交谈与通话随时可挂断）。

    :param context: 准入判定的世界与可读名称（地点名、本轮活动名）。
    :param tool: report_status、pause_activity 或 conclude_activity。
    """
    if tool == "report_status":
        return _report_refusal(context, activity)
    if tool == "pause_activity":
        return _pause_refusal(context, activity)
    return _conclude_refusal(context, activity)


def _report_refusal(context: Context, activity: ActivityState) -> Refusal | None:
    """@brief 播报与恢复：状态、恢复地点、工作的截止约束与恢复后的占用（按这个顺序）。"""
    if activity.status not in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}:
        return refusal_for_code("ACTION_REQUEST_INVALID")
    if activity.status == ActivityStatus.ACTIVE:
        return None
    if not at_stay(context.state, activity.agent_id, activity.location_id):
        place = context.place_names[activity.location_id]
        name = context.activity_names.get(activity.activity_id, activity.name)
        return Refusal("ACTIVITY_PRECONDITION_UNMET",
                       f"Resume {name} at its start location {place}; it can be concluded elsewhere.",
                       unmet=("activity_location",), suggested={"travel_to": place})
    if activity.kind == ActivityKind.WORK:
        worked, _ = work_totals(context.state, activity.agent_id, activity.target_id)
        refusal = work_start_refusal(context.tasks[activity.target_id], worked, context.state.current_time)
        if refusal is not None:
            return refusal
    blocking = [item.name for item in active_activities(context.state, activity.agent_id)
                if item.activity_id != activity.activity_id and item.resource_claims & activity.resource_claims]
    if blocking:
        listed = "、".join(blocking)
        return Refusal("RESOURCE_CONFLICT", f"This activity conflicts with the running activities {listed}.",
                       f"Conclude or pause {listed} first.",
                       ("resource_claims",))
    return None


def _pause_refusal(context: Context, activity: ActivityState) -> Refusal | None:
    """@brief 暂停：进行中、不在路上、不是交谈或通话，并在决策边界。"""
    if activity.status != ActivityStatus.ACTIVE:
        return refusal_for_code("ACTION_REQUEST_INVALID")
    if on_road(context.state, activity):
        name = context.activity_names.get(activity.activity_id, activity.name)
        return Refusal("ACTIVITY_PAUSE_UNAVAILABLE", f"The activity {name} cannot be paused while travelling.",
                       f"Conclude {name}.")
    if activity.kind in {ActivityKind.CHAT, ActivityKind.CALL}:
        return refusal_for_code("SOCIAL_ACTIVITY_CANNOT_PAUSE")
    if not at_decision_boundary(activity, context.state.current_time):
        return refusal_for_code("ACTIVITY_FEEDBACK_NOT_DUE")
    return None


def _conclude_refusal(context: Context, activity: ActivityState) -> Refusal | None:
    """@brief 结束：占名额；有别的待收尾活动时先收尾；进行中的非社交活动须在决策边界。"""
    if not occupies_slot(activity):
        return refusal_for_code("ACTION_REQUEST_INVALID")
    waiting = pending_conclusions(context.state, activity.agent_id)
    if waiting and activity.activity_id not in {item.activity_id for item in waiting}:
        return conclusion_refusal(waiting, dict(context.activity_names))
    if (activity.status == ActivityStatus.ACTIVE and activity.kind not in {ActivityKind.CHAT, ActivityKind.CALL}
            and not at_decision_boundary(activity, context.state.current_time)):
        return refusal_for_code("ACTIVITY_FEEDBACK_NOT_DUE")
    return None


def require_start_admission(state: WorldState, agent_id: int, request: tuple[list[str], list[str]],
                            names: dict[str, str]) -> None:
    """@brief 开始请求的名额与出发前判定：名额不足报 SESSION_LIMIT_EXCEEDED，出发前仍有进行中活动报前置条件。

    名额不足的原因分两种说法：两个名额都占着时请人物先结束一件；只剩一个名额却要同时开始两项时说明只剩一个名额
    （docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章「并行准入」）。两种都点名占着名额的活动，下一步都是先结束其中一项。

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
        reason = (f"Only one activity slot remains because {listed} occupies a slot." if free > 0
                  else f"Both activity slots are occupied by {listed}; conclude one first.")
        return Refusal("SESSION_LIMIT_EXCEEDED", reason, f"Conclude one of {listed} first.")
    running = [item for item in _agent_activities(state, agent_id, _is_active)]
    if "travel_to" in actions and running:
        listed = [names.get(item.activity_id, item.name) for item in running]
        return Refusal("ACTIVITY_PRECONDITION_UNMET",
                       f"Conclude or pause the current activities {', '.join(listed)} before departure.",
                       unmet=("activities_before_departure",), suggested={"conclude_or_pause": listed})
    return None


def record_changes(before: WorldState, after: WorldState, pending: PendingAction,
                   context: RecordContext) -> tuple[WorldState, list[EventDraft]]:
    """@brief 本次控制改变的每项活动写一条 environment_event；原始动作全部子活动终结时写一次终态；控制请求里每条被受理
    的个人事务变更各写一条事务事件（errands.request_drafts，只在这条成功分支生成，docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-记下更新取消
    「原子性」）。"""
    drafts = _lifecycle_drafts(before, after, pending, context.snapshot)
    changed, terminal = _terminal_records(before, after, pending.requested_event_id, context.title)
    return changed, drafts + terminal + errands.request_drafts(before, after, pending)


def truncation_record(activity: ActivityState, snapshot: dict, now: int) -> EventDraft:
    """@brief 实验截止截断的一项活动（此前进行中或暂停）写一条带截断快照的 environment_event。

    同源两项的聚合终态不带快照，暂停的活动此前只有暂停时的快照；逐项写截断快照，导出的活动才与最终检查点逐项一致
    （docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第八章「活动」）。原因回链开始该活动的请求事件。

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

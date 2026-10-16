"""@brief 活动的固定槽位、生命周期、客观进度与控制工具共享准入。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from worldmock.errors import BusinessRuleError
from worldmock.footprints.models import EventDraft
from worldmock.life import errands
from worldmock.life.activities import at_decision_boundary
from worldmock.life.activity_evidence import OUTCOME_APP_USE_ENDED, app_use_ended
from worldmock.life.activity_titles import TitleIndex
from worldmock.life.error_contract import ErrorItem, items_fix
from worldmock.life.guidance import Refusal, items_refusal, refusal_for_code
from worldmock.life.action_evidence import _activity_terminal_draft, require_origin_action
from worldmock.life.content_select import next_episode
from worldmock.life.models import (CONCLUDABLE_TERMINAL_STATUSES, ActivityKind, ActivityState, ActivityStatus,
                                   PendingAction, WorldState, active_activities)
from worldmock.life.name_resolution import near_names
from worldmock.life.object_labels import resolve_name
from worldmock.life.readable import duration_phrase
from worldmock.life.sessions import at_stay, moment_label, on_road

if TYPE_CHECKING:
    from worldmock.life.admission import Context

#: 每人活动槽位数（常量，不设场景参数）。
SLOT_LIMIT = 2
#: 在地点做的事离计划结束不足这么多秒时不能暂停，只能结束或等它做完；恰好还剩这么多秒可以暂停。一次 app 使用随时可以暂停。
PAUSE_MIN_REMAINING_SECONDS = 180
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

    #: 当前目录生成的权威活动快照。
    snapshot: Callable[[ActivityState], dict]
    #: 实际内容标题索引。
    index: TitleIndex


def is_pending_conclusion(activity: ActivityState) -> bool:
    """@brief 待收尾：由世界或人物结束（completed、failed、cancelled）而尚未 conclude_activity。"""
    return activity.status in CONCLUDABLE_TERMINAL_STATUSES and activity.concluded_at is None


def finished_at(activity: ActivityState, now: int) -> bool:
    """@brief 活动是否在这一秒做完、要请人物决策：待收尾且结束于这一秒，或是结束于这一秒、已完成的查询。

    查询做完自己结束、不经待收尾，所以要单列；种类判断只写在这里，唤醒一侧只调用它。

    :param activity: 人物的一项活动。
    :param now: 当前时钟边界。
    """
    if activity.ended_at != now:
        return False
    return is_pending_conclusion(activity) or (activity.kind == ActivityKind.QUERY
                                               and activity.status == ActivityStatus.COMPLETED)


def pending_conclusions(state: WorldState, agent_id: int) -> tuple[ActivityState, ...]:
    """@brief 人物全部待收尾活动，按开始时刻与身份稳定排序。"""
    return _agent_activities(state, agent_id, is_pending_conclusion)


def slot_activities(state: WorldState, agent_id: int) -> tuple[ActivityState, ...]:
    """@brief 占槽位的活动：进行中与待收尾（暂停的不占）。"""
    return _agent_activities(state, agent_id, occupies_slot)


def occupies_slot(activity: ActivityState) -> bool:
    """@brief 活动是否占一个槽位：进行中或待收尾；暂停的活动不占槽位。"""
    return activity.status == ActivityStatus.ACTIVE or is_pending_conclusion(activity)


def concludable(activity: ActivityState) -> bool:
    """@brief 活动能否用 conclude_activity 结束：进行中、暂停中或待收尾（暂停的活动在别处也可直接结束）。"""
    return activity.status in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED} or is_pending_conclusion(activity)


def pausable(state: WorldState, activity: ActivityState) -> bool:
    """@brief 活动能否暂停的唯一判定：进行中的交互活动，且是一次 app 使用（随时可停）或离计划结束不少于 PAUSE_MIN_REMAINING_SECONDS。

    暂停的受理、工具层 pause_activity 一行的可用性与原因、同槽位占用与槽位超限的下一步写不写「暂停」都读它。
    计划结束取活动此刻的计划结束（系列是正在做的这一集）；决策边界只关乎暂停的时机，不在此判定。
    """
    return (activity.status == ActivityStatus.ACTIVE and activity.kind == ActivityKind.INTERACT
            and (activity.data["app_id"] is not None
                 or activity.planned_end_at - state.current_time >= PAUSE_MIN_REMAINING_SECONDS))


def free_slots(state: WorldState, agent_id: int) -> int:
    """@brief 人物剩余的活动槽位。"""
    return SLOT_LIMIT - len(slot_activities(state, agent_id))


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


def oversleep_phrase(activity: ActivityState) -> str:
    """@brief 睡过头的说法「比打算的多睡了 N 分钟」：只有已完成且多睡了的睡眠才有，其余为空串。"""
    if activity.kind != ActivityKind.SLEEP or activity.status != ActivityStatus.COMPLETED:
        return ""
    seconds = activity.data["oversleep_seconds"]
    return f"比打算的多睡了 {seconds // 60} 分钟" if seconds > 0 else ""


def objective_progress(activity: ActivityState) -> str:
    """@brief 活动客观进度：一次 app 使用写「已用…，放到第 K 条」（见 _app_use_progress）；别的活动写已做时长，
    进行或暂停中的活动另写剩余时长，睡过头的睡眠另写多睡了多久。

    工具层活动行的 state、待办层暂停行的 progress 与回看任务的 objective_progress 都读这一句。
    """
    if activity.kind == ActivityKind.INTERACT and activity.data["app_id"] is not None:
        return _app_use_progress(activity)
    parts = [f"已做{duration_phrase(activity.elapsed_seconds)}"]
    if activity.status in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}:
        remaining = max(0, activity.planned_duration_seconds - activity.elapsed_seconds)
        parts.append(f"还剩{duration_phrase(remaining)}")
    overslept = oversleep_phrase(activity)
    if overslept:
        parts.append(overslept)
    return "，".join(parts)


def _app_use_progress(activity: ActivityState) -> str:
    """@brief 一次 app 使用的客观进度「已用<不含暂停的已用时长>，放到第 K 条」。

    K 是放完的条数（data.played 的项数），状态不是 completed 时再加 1：进行中、暂停中、被收尾结束、被截断的使用都还有
    一条正放着或停在半截；放完一条后没有能接的内容而停下（completed）的使用没有这一条。
    """
    count = len(activity.data["played"]) + (0 if activity.status == ActivityStatus.COMPLETED else 1)
    return f"已用{duration_phrase(activity.elapsed_seconds)}，放到第 {count} 条"


def ending_phrase(activity: ActivityState) -> str:
    """@brief 已结束活动的终局说法：到达、按计划结束、睡过头、结束使用（被收尾的 app 使用）、提前结束或失败。"""
    if activity.status == ActivityStatus.FAILED:
        return "失败"
    if activity.kind == ActivityKind.TRAVEL and activity.status == ActivityStatus.COMPLETED:
        return "到达"
    if activity.status == ActivityStatus.COMPLETED and activity.boundary_type == "planned_end":
        return oversleep_phrase(activity) or "按计划结束"
    if app_use_ended(activity):
        return OUTCOME_APP_USE_ENDED
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
    """@brief 收尾：写入总结自述并置 concluded_at（待收尾的活动由此释放槽位）；终态不变。"""
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

    名称类失败逐项报全部（docs/design/04-simulation-engine.md）：找不到的明细码为 NAME_NOT_FOUND，
    原因
    列出最接近的几个活动名；归一后（如没写开始时刻）对应不止一项的为 NAME_AMBIGUOUS，原因列出它们。明细的参数名为
    activity_name、原文照录，不带候选（可照填的候选是开始工具的完整调用），与宿主纠错中活动名的明细相同；原因逐项写
    「第 N 项「原文」：…」（guidance.items_refusal），改法按码各写一句。宿主已在本轮纠正这些名称，这里只防绕过宿主的请求，
    所以顶层码与开始组名称问题的防御拒绝（start_group）一样取 ACTION_REQUEST_INVALID，下一步取该码的
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

    report_status：只接受进行中或暂停的活动；暂停的活动只在实际开始地点恢复。
    恢复要有空槽位且与进行中活动的占用不能冲突（按这个顺序）。
    pause_activity：只暂停进行中的交互活动：一次 app 使用随时可以，在地点做的事离计划结束须不少于三分钟；须在决策边界。
    conclude_activity：只结束能结束的活动（进行中、暂停中或待收尾）；有别的待收尾活动时先收尾；进行中的活动须在决策边界
    。

    :param context: 准入判定的世界与可读名称（地点名、本轮活动名）。
    :param tool: report_status、pause_activity 或 conclude_activity。
    """
    if tool == "report_status":
        return _report_refusal(context, activity)
    if tool == "pause_activity":
        return _pause_refusal(context, activity)
    return _conclude_refusal(context, activity)


def _report_refusal(context: Context, activity: ActivityState) -> Refusal | None:
    """@brief 播报与恢复按状态、实际开始地点、空槽位与恢复后的占用依次核验。"""
    if activity.status not in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}:
        return refusal_for_code("ACTION_REQUEST_INVALID")
    if activity.status == ActivityStatus.ACTIVE:
        return None
    if not at_stay(context.state, activity.agent_id, activity.location_id):
        place = context.place_names[activity.location_id]
        name = context.activity_names.get(activity.activity_id, activity.name)
        return Refusal("ACTIVITY_PRECONDITION_UNMET",
                       f"Resume {name} at its start location {place}; it can be concluded elsewhere.",
                       next_step=f"Travel to {place}.", unmet=("activity_location",))
    refusal = slot_refusal(context.state, activity.agent_id, 1, context.activity_names)
    if refusal is not None:
        return refusal
    blocking = [item for item in active_activities(context.state, activity.agent_id)
                if item.activity_id != activity.activity_id and item.resource_claims & activity.resource_claims]
    if blocking:
        listed = "、".join(item.name for item in blocking)
        step = "Conclude or pause" if all(pausable(context.state, item) for item in blocking) else "Conclude"
        return Refusal("RESOURCE_CONFLICT", f"This activity conflicts with the running activities {listed}.",
                       f"{step} {listed} first.", ("resource_claims",))
    return None


def _pause_refusal(context: Context, activity: ActivityState) -> Refusal | None:
    """@brief 暂停：能暂停（进行中的 app 使用，或离计划结束不少于三分钟的在地点做的事，pausable），并在决策边界。"""
    if not pausable(context.state, activity):
        return _unpausable_refusal(context, activity)
    if not at_decision_boundary(activity, context.state.current_time):
        return refusal_for_code("ACTIVITY_FEEDBACK_NOT_DUE")
    return None


def _unpausable_refusal(context: Context, activity: ActivityState) -> Refusal:
    """@brief 不能暂停的活动按第一条不成立的条件拒绝：不在进行中；是交互活动而离计划结束不足三分钟；其余种类。

    离计划结束不足三分钟的交互活动有两种文字：做完这一集还会接下一集时（_continues_with_next_episode），原因说这一集
    快完了、之后接着做下一集，下一步与改法是结束它或让它继续；不再接续时原因说这件事快做完了，下一步与改法是结束它或
    等它做完。
    """
    if activity.status != ActivityStatus.ACTIVE:
        return refusal_for_code("ACTION_REQUEST_INVALID")
    name = context.activity_names.get(activity.activity_id, activity.name)
    if activity.kind == ActivityKind.INTERACT and _continues_with_next_episode(context, activity):
        return Refusal("ACTIVITY_PAUSE_UNAVAILABLE",
                       f"{name} cannot be paused while its current episode ends in under three minutes; "
                       "it then continues with the next episode.",
                       f"Conclude {name}, or keep it running.",
                       fix="Conclude this activity or keep it running instead.")
    if activity.kind == ActivityKind.INTERACT:
        return Refusal("ACTIVITY_PAUSE_UNAVAILABLE",
                       f"The activity {name} ends in under three minutes and cannot be paused.",
                       f"Conclude {name} or let it finish.", fix="Conclude this activity or let it finish instead.")
    if on_road(context.state, activity):
        return Refusal("ACTIVITY_PAUSE_UNAVAILABLE", f"The activity {name} cannot be paused while travelling.",
                       f"Conclude {name}.")
    return refusal_for_code("ACTIVITY_PAUSE_UNAVAILABLE")


def _continues_with_next_episode(context: Context, activity: ActivityState) -> bool:
    """@brief 交互活动做完当前这一集后是否接着做下一集：与系列接续同一个判定 content_select.next_episode。

    轮数取当前内容此刻的完成轮数（没有进度记录按 0 算）加一，即这一集做完之后的完成轮数，与接续时传入的是同一个数。
    独立内容、系列最后一集、后面各集轮数都不低于这个数的系列都不接续。读准入上下文的环境输入与人物此刻的状态。
    """
    catalog = context.inputs.catalog
    agent = context.state.agents[activity.agent_id]
    content = catalog.contents[activity.data["content_id"]]
    progress = agent.content_progress.get(content.content_id)
    rounds = (0 if progress is None else progress.completed_rounds) + 1
    return next_episode(catalog, agent, content, rounds) is not None


def _conclude_refusal(context: Context, activity: ActivityState) -> Refusal | None:
    """@brief 结束：能结束（进行中、暂停中或待收尾）；有别的待收尾活动时先收尾；进行中的非社交活动须在决策边界。"""
    if not concludable(activity):
        return refusal_for_code("ACTION_REQUEST_INVALID")
    if activity.kind == ActivityKind.SLEEP and activity.status == ActivityStatus.ACTIVE:
        return refusal_for_code("ACTIVITY_PAUSE_UNAVAILABLE")
    waiting = pending_conclusions(context.state, activity.agent_id)
    if waiting and activity.activity_id not in {item.activity_id for item in waiting}:
        return conclusion_refusal(waiting, dict(context.activity_names))
    if activity.status == ActivityStatus.ACTIVE and not at_decision_boundary(activity, context.state.current_time):
        return refusal_for_code("ACTIVITY_FEEDBACK_NOT_DUE")
    return None


def slot_refusal(state: WorldState, agent_id: int, needed: int, names: Mapping[str, str]) -> Refusal | None:
    """@brief 槽位判定（开始、出行与恢复暂停的活动共用）：需要的槽位多于空槽位时以 SESSION_LIMIT_EXCEEDED 拒绝，够用时为 None。

    原因点名占槽位的活动（进行中与待收尾）：只剩一个槽位时说明只剩一个，槽位占满时说明两个都被占着；下一步按被点名
    活动能否暂停写（docs/design/04-simulation-engine.md）。

    :param needed: 恢复所需的活动槽位。
    :param names: 活动身份→本轮可读名称。
    """
    free = free_slots(state, agent_id)
    if needed <= free:
        return None
    occupying = slot_activities(state, agent_id)
    listed = _listed(occupying, names)
    reason = (f"Only one activity slot remains because {listed} occupies a slot." if free > 0
              else f"Both activity slots are occupied by {listed}.")
    return Refusal("SESSION_LIMIT_EXCEEDED", reason, _slot_next_step(state, occupying, names))


def _slot_next_step(state: WorldState, occupying: Sequence[ActivityState], names: Mapping[str, str]) -> str:
    """@brief 槽位超限的下一步：都能暂停时写结束或暂停其中一项，都不能时只写结束，混合时暂停只点名能暂停的。"""
    listed = _listed(occupying, names)
    can_pause = [item for item in occupying if pausable(state, item)]
    if len(can_pause) == len(occupying):
        return f"Conclude or pause one of {listed} first."
    if not can_pause:
        return f"Conclude one of {listed} first."
    return f"Conclude one of {listed}, or pause {_listed(can_pause, names)}, first."


def _listed(activities: Iterable[ActivityState], names: Mapping[str, str]) -> str:
    """@brief 几项活动的本轮可读名称，以「、」连接（没有本轮名称的取活动自身的名称）。"""
    return "、".join(names.get(item.activity_id, item.name) for item in activities)


def record_changes(before: WorldState, after: WorldState, pending: PendingAction,
                   context: RecordContext) -> list[EventDraft]:
    """@brief 本次控制改变的每项活动写一条 environment_event；活动终结时给开始它的动作写一次终态；控制请求里每条被受理
    的个人事务变更各写一条事务事件（errands.request_drafts，只在这条成功分支生成，docs/design/04-simulation-engine.md
    「唤醒、日计划与个人事务」）。"""
    drafts = _lifecycle_drafts(before, after, pending, context.snapshot)
    terminal = _terminal_records(before, after, pending.requested_event_id, context.index)
    return drafts + terminal + errands.request_drafts(before, after, pending)


def truncation_record(activity: ActivityState, snapshot: dict, now: int) -> EventDraft:
    """@brief 实验截止截断的一项活动（此前进行中或暂停）写一条带截断快照的 environment_event。

    同源两项的聚合终态不带快照，暂停的活动此前只有暂停时的快照；逐项写截断快照，导出的活动才与最终检查点逐项一致
    （docs/design/04-simulation-engine.md）。原因回链开始该活动的请求事件。

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
                      index: TitleIndex) -> list[EventDraft]:
    """@brief 本次新终结的活动各写一次终态；开始它的那条动作必须仍在 processed_actions 里。"""
    newly = [item for key, item in sorted(after.activities.items()) if item.status in _TERMINAL_STATUSES
             and key in before.activities and before.activities[key].status not in _TERMINAL_STATUSES]
    drafts = []
    for activity in newly:
        require_origin_action(after, activity)
        drafts.append(_activity_terminal_draft(after, activity, after.current_time, [request_event_id],
                                               index))
    return drafts


def _agent_activities(state: WorldState, agent_id: int,
                      predicate: Callable[[ActivityState], bool]) -> tuple[ActivityState, ...]:
    """@brief 人物满足条件的活动，按开始时刻与身份稳定排序。"""
    return tuple(sorted((item for item in state.activities.values()
                         if item.agent_id == agent_id and predicate(item)),
                        key=lambda item: (item.started_at, item.activity_id)))


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

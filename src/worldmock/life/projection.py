"""@brief 观察投影引擎侧组装：scene 场景输入 + facts 精简结构（docs/dev/SPEC-life-runtime.md「观察投影与提示合同」）。

给模型的 user 消息由 persona_card、scene、facts 三部分组成，全部由确定性代码
渲染（life.narration 渲染，不调用模型）；本模块只负责从世界状态计算两层投影
的输入字典与 body 数值/分级两组证据（数值只进事件供评估，分级进提示，docs/dev/SPEC-life-runtime.md「数值与分级的分离」）。

黑名单字段（world_state_version、end_time、affordance_contract、energy_
feasibility、observation_event_id、motivation_hints、game_catalog 全表、完整
活动快照、路线坐标与几何）在本层就不再产生——narration 的 enforce_field_
blacklist 是第二道防线，不是第一道。

供给可见期：仅实际呈现的供给具有购买凭据，位置未变且呈现后未超 1800 秒；
供给身份与商品身份分别保留。工作材料只揭示自上次决策以来新读区间、单次至多 2000
字符（docs/dev/SPEC-life-runtime.md「观察投影行为规则」）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from worldmock.life import felt, narration, social
from worldmock.life.models import ActivityKind, ActivityStatus, AgentState, WorldState
from worldmock.life.result_projection import public_action_result
from worldmock.life.scenario import RuntimeScenario
from worldmock.life.sleep import SLEEPINESS_CLIP_MAX, sleepiness

#: 供给浏览可见期（秒）：距浏览超过该值清空（docs/dev/SPEC-life-runtime.md「观察投影行为规则」）。
SUPPLY_VISIBILITY_TTL_SECONDS = 1800
#: 工作材料单次揭示上限（字符，docs/dev/SPEC-life-runtime.md「观察投影行为规则」）。
MATERIAL_REVEAL_CHAR_CAP = 2000
#: 收件箱头在 scene 中展示的条数上限（收件箱头合同，防溢出）。
INBOX_HEAD_LIMIT = 3


@dataclass(frozen=True, slots=True)
class ProjectionInputs:
    """@brief 一次观察投影的只读输入束（引擎在观察边界装配）。"""

    state: WorldState
    scenario: RuntimeScenario
    agent: AgentState
    #: 上次观察后全部按事件顺序排列的动作结算证据。
    recent_results: tuple[dict[str, Any], ...]
    #: 各任务的剩余合同工时与标题（engine._visible_task 裁剪后的行）。
    task_rows: tuple[dict[str, Any], ...]
    #: 人物档案餐时窗口（felt.MealWindows）。
    meal_windows: felt.MealWindows | None = None
    #: 引擎内部动机仪表结果（写入 motivation_internal，不进提示，docs/dev/SPEC-life-realism.md「动机层」）。
    motivation_internal: dict[str, Any] | None = None
    extras: dict[str, Any] = field(default_factory=dict)


def hour_of_day(epoch: int, timezone_name: str) -> float:
    """@brief 场景时区自午夜起的小时数（含小数）。"""
    moment = local_datetime(epoch, timezone_name)
    return moment.hour + moment.minute / 60 + moment.second / 3600


def local_datetime(epoch: int, timezone_name: str) -> datetime:
    """@brief 场景时区本地时刻（供 scene 时间句渲染）。"""
    from zoneinfo import ZoneInfo

    return datetime.fromtimestamp(epoch, ZoneInfo(timezone_name))


def visible_supplies(state: WorldState, agent: AgentState, scenario) -> list[str]:
    """@brief 仅返回实际呈现、位置未变且未超过可见期的供给凭据。"""
    declared = {supply.supply_id for supply in scenario.supplies}
    return sorted(supply_id for supply_id in agent.visible_supply_ids & declared
                  if agent.supply_browsed_location[supply_id] == agent.location_id
                  and 0 <= state.current_time - agent.supply_browsed_at[supply_id]
                  <= SUPPLY_VISIBILITY_TTL_SECONDS)


def other_presences(state: WorldState, agent: AgentState) -> list[social.AgentPresence]:
    """@brief 同世界其他人物的在场事实（姓名、活动家族、可交谈推导的输入）。"""
    presences: list[social.AgentPresence] = []
    for other in state.agents.values():
        if other.agent_id == agent.agent_id:
            continue
        activity = state.activities.get(other.current_activity_id or "")
        kind = activity.kind if activity is not None else None
        active = activity is not None and activity.status == ActivityStatus.ACTIVE
        presences.append(social.AgentPresence(
            agent_id=f"agent:{other.agent_id}", name=other.name, location=other.location_id,
            activity_family=_activity_family(kind, active),
            awake=not (active and kind == ActivityKind.SLEEP),
            actively_gaming=active and kind == ActivityKind.PLAY_GAME))
    return presences


def _activity_family(kind: ActivityKind | None, active: bool) -> str:
    """@brief 活动家族：进行中取活动 kind 文本，否则 idle。"""
    if not active or kind is None:
        return "idle"
    return kind.value


def people_here_views(state: WorldState, agent: AgentState) -> list[dict[str, Any]]:
    """@brief 同一地点他人的可见投影闭集：姓名、活动家族、可交谈（docs/dev/SPEC-life-runtime.md「社交最小可行版」）。"""
    views = social.people_here(agent.location_id, other_presences(state, agent))
    return [view.model_dump(mode="json") for view in views]


def body_projection(agent: AgentState, hour: float,
                    meal_windows: felt.MealWindows | None = None) -> dict[str, Any]:
    """@brief body 两组投影：levels 分级（进提示）与 values 数值（只进事件）。"""
    values = felt_values(agent, hour, meal_windows)
    levels = felt.summarize_felt_levels(**values)
    return {"levels": levels, "values": {**values, "sleep_pressure": agent.body.sleep_pressure,
                                         "energy_kcal": agent.body.energy_kcal,
                                         "sleep_deficit_hours": agent.body.sleep_deficit_hours}}


def felt_values(agent: AgentState, hour: float,
                meal_windows: felt.MealWindows | None = None) -> dict[str, float]:
    """@brief 五个体感信号数值（appetite/sleepiness/entertainment/social/mood）。"""
    return {"appetite": felt.compute_appetite(agent.body.energy_kcal, hour, meal_windows),
            "sleepiness": min(sleepiness(agent.body.sleep_pressure, hour,
                                         agent.body.chronotype_midsleep), SLEEPINESS_CLIP_MAX),
            "entertainment_need": agent.body.entertainment_need,
            "social_need": agent.body.social_need,
            "mood_valence": agent.body.mood_valence}


def new_material_ranges(agent: AgentState) -> dict[str, list[dict[str, Any]]]:
    """@brief 自上次决策以来新读到的工作材料区间，按任务累计 ≤2000 字符。"""
    cutoff = agent.last_decision_time or 0
    per_task: dict[str, list[dict[str, Any]]] = {}
    used: dict[str, int] = {}
    for entry in agent.material_reveals:
        if int(entry.get("at", 0)) <= cutoff:
            continue
        task_id = str(entry.get("task_id", ""))
        budget = MATERIAL_REVEAL_CHAR_CAP - used.get(task_id, 0)
        chars = min(int(entry.get("chars", 0)), max(0, budget))
        if chars <= 0:
            continue
        used[task_id] = used.get(task_id, 0) + chars
        per_task.setdefault(task_id, []).append(
            {"start_char": entry.get("start_char"), "end_char": entry.get("end_char"),
             "chars": chars})
    return per_task


def drive_summary(levels: dict[str, str], task_rows: tuple[dict[str, Any], ...],
                  now: int) -> str:
    """@brief 由体感分级、今日工作缺口与待办拼一句中文驱动摘要（docs/dev/SPEC-life-realism.md「动机层」）。

    不含数字权重（render_drive_summary 对阿拉伯数字 fail-closed）。
    """
    bands = {key: value for key, value in levels.items()
             if value not in ("不饿", "清醒", "不闷", "不想找人", "平静")}
    gap = "今天的工作还差一些工时" if any(row["remaining_contract_seconds"] > 0 for row in task_rows) else ""
    todos = "有待办任务临近" if _task_due_soon(task_rows, now) else ""
    return narration.render_drive_summary(bands, gap, todos)


def _task_due_soon(task_rows: tuple[dict[str, Any], ...], now: int) -> bool:
    """@brief 最近 2 小时内有可见任务截止则提示待办。"""
    for row in task_rows:
        due = row.get("due_at")
        if due is None:
            continue
        remaining = int(due) - now
        if 0 < remaining <= 7200:
            return True
    return False


def _declared_bedtime_end_hour(scenario: RuntimeScenario, agent_id: int) -> float | None:
    """@brief 人物声明的就寝窗口末端小时（与 engine._plan_day 的取值链同源）。

    AgentConfig.bedtime_window 的末端（二元组第二位）；未声明返回 None，
    由 bedtime_anchor 落到 persona-mapping 缺省锚点。
    """
    config = next((item for item in scenario.agents if item.agent_id == agent_id), None)
    declared = getattr(config, "bedtime_window", None)
    return declared[1] if declared else None


def _epoch_anchors(now: int, timezone_name: str,
                   declared_end_hour: float | None) -> dict[str, int]:
    """@brief 当前时刻、当日本地 00:00、当日结束（次日 00:00）与当日就寝锚点。

    plan_day/revise_day_plan 的段起点/终点合同是 epoch 秒；与 ISO 本地时间
    并排披露锚点，模型可直接换算（docs/CONTRACTS.md「日程骨架请求」）。
    day_end_epoch 与 DayPlan 校验的拒绝边界同源：同一 day_end_seconds
    （段 end 越过即 PLAN_SEGMENTS_INVALID），披露值即校验值。bedtime_epoch
    与 DayPlan 校验的就寝覆盖边界同源：同一 bedtime_anchor（人物声明末端或
    persona-mapping 缺省，跨午夜钳制 ≤ 当日结束；engine._plan_day 对同一
    人物取同一 declared_end_hour），最末段 end 低于它即 PLAN_SEGMENTS_INVALID
    （"plan does not cover until bedtime"），披露值即校验值。
    """
    from worldmock.life.schedule import bedtime_anchor, day_end_seconds

    return {"current_epoch": now,
            "day_start_epoch": now - int(hour_of_day(now, timezone_name) * 3600),
            "day_end_epoch": day_end_seconds(now, timezone_name),
            "bedtime_epoch": bedtime_anchor(now, timezone_name, declared_end_hour)}


def build_observation(inputs: ProjectionInputs) -> dict[str, Any]:
    """@brief 组装一次观察的两层投影与事件级证据（scene + facts + body）。

    引擎把 route/task 行与完整 recent_result 证据经 ProjectionInputs 传入；本函数
    只做投影组装，不做 IO 与模型调用。返回字典进入 observation 事件 data 与
    LifeAgent 侧渲染（narration.render_scene / render_facts）。
    """
    state, agent, scenario = inputs.state, inputs.agent, inputs.scenario
    now = state.current_time
    hour = hour_of_day(now, scenario.timezone)
    local = local_datetime(now, scenario.timezone)
    body_data = body_projection(agent, hour, inputs.meal_windows)
    levels = body_data["levels"]
    people = people_here_views(state, agent)
    inbox = _inbox_rows(inputs)
    activity_rows = inputs.extras["activity_rows"]
    data: dict[str, Any] = {
        "agent_id": agent.agent_id, "current_time": now,
        "scene": _scene_projection(inputs, local, levels, people, inbox),
        "facts": _facts_projection(inputs, local, levels, people, inbox),
        "body": body_data, "balance_fen": state.accounts[agent.balance_account_id],
        "active_activity": activity_rows["control"],
        "paused_activities": activity_rows.get("paused_control"),
    }
    if inputs.motivation_internal is not None:
        data["motivation_internal"] = inputs.motivation_internal
    return data


def _scene_projection(inputs: ProjectionInputs, local: datetime, levels: dict,
                      people: list[dict], inbox: list[dict]) -> dict[str, Any]:
    """@brief 组装场景事实，最近收件头与完整收件事实分开选择。"""
    agent, now = inputs.agent, inputs.state.current_time
    location = _location_index(inputs.scenario).get(agent.location_id)
    activity_rows = inputs.extras["activity_rows"]
    return {
        "current_time": local.isoformat(),
        **_epoch_anchors(now, inputs.scenario.timezone,
                         _declared_bedtime_end_hour(inputs.scenario, agent.agent_id)),
        "location": location.name if location else agent.location_id,
        "people_here": [item["name"] for item in people],
        "felt": levels,
        "current_activity": activity_rows["current"] or {},
        "paused_activities": activity_rows["paused"],
        "day_plan": _plan_row(agent, now, local),
        "inbox_head": [entry["head"] for entry in inbox[-INBOX_HEAD_LIMIT:]],
        "inbox_has_older_unread": any(not entry["read"] for entry in inbox[:-INBOX_HEAD_LIMIT]),
        "todos_summary": _todos_summary(inputs.task_rows, now),
        "reachable_places": inputs.extras.get("reachable_places", []),
        "inventory": _inventory_names(agent, inputs.scenario),
        "recent_action_results": [row["message"] for row in inputs.recent_results[-1:]],
    }


def _facts_projection(inputs: ProjectionInputs, local: datetime, levels: dict,
                      people: list[dict], inbox: list[dict]) -> dict[str, Any]:
    """@brief 组装完整事实清单，全部新结果经过公开字段投影。"""
    agent, now = inputs.agent, inputs.state.current_time
    return {
        "current_time": local.isoformat(),
        **_epoch_anchors(now, inputs.scenario.timezone,
                         _declared_bedtime_end_hour(inputs.scenario, agent.agent_id)),
        "location_id": agent.location_id,
        "people_here": people,
        "contacts": _contact_rows(inputs.scenario, agent),
        "inbox": inbox,
        "tasks": _task_fact_rows(inputs),
        "routes": inputs.extras.get("route_rows", []),
        "products": inputs.extras.get("product_rows", []),
        "inventory": agent.inventory,
        "day_plan": _plan_fact(agent, now, local),
        "felt": levels,
        "drive_summary": drive_summary(levels, inputs.task_rows, now),
        "recent_action_results": [public_action_result(row) for row in inputs.recent_results],
    }


def _location_index(scenario: RuntimeScenario) -> dict[str, Any]:
    """@brief 地点身份索引。"""
    return {item.location_id: item for item in scenario.locations}


def _contact_rows(scenario: RuntimeScenario, agent: AgentState) -> list[dict[str, Any]]:
    """@brief 人物自己声明的联系人 facts 行：不透明 contact_id + 称谓 + 关系。

    联系人是本人的通讯录（docs/dev/SPEC-life-runtime.md「社交最小可行版」），不是他人隐私闭集（docs/dev/SPEC-life-runtime.md「社交最小可行版」）约束的对象；
    亲密度、渠道与回复策略是引擎结算内部参数，不进 facts。call 的对象集
    与 send_message 的联系人收件人都以该 contact_id 为身份锚点。
    """
    config = next((item for item in scenario.agents if item.agent_id == agent.agent_id), None)
    contacts = getattr(config, "contacts", ()) or ()
    return [{"contact_id": item.contact_id, "salutation": item.salutation,
             "relation": item.relation} for item in contacts]


def _inbox_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 收件箱行：场景邮件头 + 社交消息（直发与交谈邀请）。"""
    agent, rows = inputs.agent, []
    for message_id in sorted(agent.visible_message_ids):
        config = inputs.extras.get("scenario_messages", {}).get(message_id)
        if config is None:
            continue
        rows.append({"id": message_id, "kind": "mail", "sender_id": None,
                     "head": config.subject, "read": message_id in agent.read_message_ids,
                     "created_at": int(config.sent_at.timestamp())})
    for message_id in sorted(inputs.agent.inbox):
        message = agent.inbox[message_id]
        head = ("邀请你交谈" if message.kind == "chat_invite" else message.text[:40])
        rows.append({"id": message_id, "kind": message.kind, "sender_id": message.sender_id,
                     "head": head, "read": message_id in agent.read_inbox_ids,
                     "created_at": message.created_at})
    rows.sort(key=lambda row: (row["created_at"], row["id"]))
    return rows


def _todos_summary(task_rows: tuple[dict[str, Any], ...], now: int) -> str:
    """@brief 待办任务一句话（scene 合同，无数字权重）。"""
    pending = [row for row in task_rows if row["remaining_contract_seconds"] > 0]
    if not pending:
        return ""
    return ("有待办任务：最早的一份快到截止" if _task_due_soon(task_rows, now)
            else "有待办任务可以推进")


def _plan_row(agent: AgentState, now: int, local: datetime) -> dict[str, Any] | None:
    """@brief scene 的今日计划行：summary + 当前段（schedule.current_segment）。"""
    from worldmock.life import schedule as schedule_module

    plan = agent.day_plan
    if plan is None or plan.bedtime <= now:
        return None
    current = schedule_module.current_segment(plan, now)
    summary = "；".join(f"{segment.intent_kind}" for segment in plan.segments)
    return {"summary": summary,
            "current_segment": current.intent_kind if current else "段间自由时间"}


def _plan_fact(agent: AgentState, now: int, local: datetime) -> dict[str, Any] | None:
    """@brief facts 的今日计划行：段列表与当前段身份；就寝后与 scene 同口径隐藏。"""
    from worldmock.life import schedule as schedule_module

    plan = agent.day_plan
    if plan is None or plan.bedtime <= now:
        return None
    current = schedule_module.current_segment(plan, now)
    return {"segments": [{"start": segment.start, "end": segment.end,
                          "intent_kind": segment.intent_kind, "note": segment.note}
                         for segment in plan.segments],
            "current_segment": ({"start": current.start, "end": current.end,
                                 "intent_kind": current.intent_kind}
                                if current else None)}


def _task_fact_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief facts 任务行：task_id/标题/截止/剩余合同工时/新揭示材料区间。"""
    ranges = new_material_ranges(inputs.agent)
    rows = []
    for row in inputs.task_rows:
        rows.append({"task_id": row["task_id"], "title": row["title"],
                     "due_at": row["due_at"],
                     "remaining_contract_seconds": row["remaining_contract_seconds"],
                     "new_material_ranges": ranges.get(row["task_id"], [])})
    return rows


def _inventory_names(agent: AgentState, scenario: RuntimeScenario) -> list[str]:
    """@brief 手边持有物展示名（scene 合同）：目录名称，缺名时回退标识。"""
    names = {item.product_id: item.name
             for item in (*scenario.products, *scenario.inventory_items)}
    holding_ids = {product_id for holding in agent.inventory.values()
                   for product_id, quantity in holding.items() if quantity > 0}
    return sorted({names.get(product_id, product_id) for product_id in holding_ids})

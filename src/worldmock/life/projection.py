"""@brief 观察投影引擎侧组装：scene 场景输入 + facts 精简结构（docs/dev/SPEC-life-runtime.md「观察投影与提示合同」）。

给模型的 user 消息由 persona_card、scene、facts 三部分组成，全部由确定性代码
渲染（life.narration 渲染，不调用模型）；本模块只负责从世界状态计算两层投影
的输入字典与 body 数值/分级两组证据（数值只进事件供评估，分级进提示，docs/dev/SPEC-life-runtime.md「数值与分级的分离」）。

黑名单字段（world_state_version、end_time、affordance_contract、observation_
event_id、motivation_hints、game_catalog 全表、完整活动快照、路线坐标与几何）
在本层就不再产生——narration 的 enforce_field_blacklist 是第二道防线，不是第一道。
能量只以饥饿分级、食品每份热量与路线费力的可读标签出现，身体数值不进提示；
饥饿为「饿」及以上时，场景的 local_food_summary 在饥饿分级得出之后生成吃饭途径句
（docs/dev/SPEC-time-and-energy.md「能量感知」「让人物趁早吃」）。

供给可见期：仅实际呈现的供给具有购买凭据，位置未变且呈现后未超 1800 秒；
供给身份与商品身份分别保留。工作材料只揭示自上次决策以来新读区间、单次至多 2000
字符（docs/dev/SPEC-life-runtime.md「观察投影行为规则」）。
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from worldmock.catalog.affordances import default_affordances
from worldmock.life import activity_control, admission, dining, felt, narration, social
from worldmock.life import reminders, social_actions
from worldmock.life.activity_control import readable_duration
from worldmock.life.content import restarts_from_beginning
from worldmock.life.interaction_menu import (ACTION_WAYS, CONTENT_WAYS, PURCHASE_WAYS, TRAVEL_WAYS, USE_WAYS,
                                             binding_action, public_action)
from worldmock.life.models import (ActivityKind, AgentState, SameMomentStage, TravelMode, WorldState,
                                   active_activities)
from worldmock.life.result_projection import public_action_result
from worldmock.life.retrieval import feasible_kinds
from worldmock.life.scenario import BodyConfig, RuntimeScenario
from worldmock.life.schedule import remind_clock
from worldmock.life.sleep import SLEEPINESS_CLIP_MAX, sleepiness
from worldmock.life.work import completion_outlook, work_start_blocker, work_totals

#: 供给浏览可见期（秒）：距浏览超过该值清空（docs/dev/SPEC-life-runtime.md「观察投影行为规则」）。
SUPPLY_VISIBILITY_TTL_SECONDS = 1800
#: 工作材料单次揭示上限（字符，docs/dev/SPEC-life-runtime.md「观察投影行为规则」）。
MATERIAL_REVEAL_CHAR_CAP = 2000
#: 收件箱头在 scene 中展示的条数上限（收件箱头合同，防溢出）。
INBOX_HEAD_LIMIT = 3
#: 同刻上限受限轮的场景说明句（docs/dev/SPEC-time-and-energy.md 第一章「实现约定」）：可读、不写次数。
SAME_MOMENT_NOTE = "你已经想了好几轮，先定下一件事。"
#: 路线费力分档的代谢当量分界：MET < 2 为「轻松」，2 ≤ MET < 3 为「一般」，≥ 3 为「费力」。
#: experiment_assumption：登记在 docs/EXPERIMENT.md「能量感知标签」。
ROUTE_EFFORT_MODERATE_MET = 2.0
ROUTE_EFFORT_STRENUOUS_MET = 3.0
#: 食品每份热量的取整粒度与「几乎不含热量」的上界（千卡）：按 5 进位取整到 10 千卡。
#: experiment_assumption：登记在 docs/EXPERIMENT.md「能量感知标签」。
FOOD_ENERGY_ROUNDING_KCAL = 10
FOOD_ENERGY_NEGLIGIBLE_KCAL = 5.0


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
    """@brief 同世界其他人物的在场事实（姓名、活动家族、可交谈推导的输入）；途中的人物不在任何地点，不算在场。"""
    presences: list[social.AgentPresence] = []
    for other in state.agents.values():
        if other.agent_id == agent.agent_id or admission.en_route(state, other.agent_id):
            continue
        kinds = {item.kind for item in active_activities(state, other.agent_id)}
        presences.append(social.AgentPresence(
            agent_id=f"agent:{other.agent_id}", name=other.name, location=other.location_id,
            activity_family=" + ".join(sorted(kind.value for kind in kinds)) if kinds else "idle",
            awake=ActivityKind.SLEEP not in kinds,
            actively_gaming=ActivityKind.PLAY_GAME in kinds))
    return presences


def people_here_views(state: WorldState, agent: AgentState) -> list[dict[str, Any]]:
    """@brief 同一地点他人的可见投影闭集：姓名、活动家族、可交谈（docs/dev/SPEC-life-runtime.md「社交最小可行版」）。

    按现场地点判定：人物自己在途中时身边没有人（docs/dev/SPEC-time-and-energy.md 第五章「并行准入」）。
    """
    if admission.en_route(state, agent.agent_id):
        return []
    views = social.people_here(agent.location_id, other_presences(state, agent))
    return [view.model_dump(mode="json") for view in views]


def body_projection(agent: AgentState, hour: float, body: BodyConfig,
                    meal_windows: felt.MealWindows | None = None) -> dict[str, Any]:
    """@brief body 两组投影：levels 分级（进提示）与 values 数值（只进事件）。

    饥饿分级按当前能量与身体配置的紧急阈值叠加两条低能量规则；决策参照快照与唤醒判定
    传入同一身体配置，保证分级同源（docs/dev/SPEC-time-and-energy.md「饥饿在低能量时不失灵」）。
    """
    values = felt_values(agent, hour, meal_windows)
    levels = felt.summarize_felt_levels(
        values, felt.EnergyReading(agent.body.energy_kcal, body.emergency_threshold_kcal))
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


def route_effort(met: float) -> str:
    """@brief 按出行方式的代谢当量给路线费力标签（docs/dev/SPEC-time-and-energy.md「商品热量与路线费力」）。"""
    if met >= ROUTE_EFFORT_STRENUOUS_MET:
        return "费力"
    if met >= ROUTE_EFFORT_MODERATE_MET:
        return "一般"
    return "轻松"


def food_energy_label(kcal_per_unit: float) -> str:
    """@brief 食品每份热量的可读标签：按 5 进位取整到 10 千卡，不足 5 千卡写「几乎不含热量」。"""
    if kcal_per_unit < FOOD_ENERGY_NEGLIGIBLE_KCAL:
        return "几乎不含热量"
    rounded = math.floor(kcal_per_unit / FOOD_ENERGY_ROUNDING_KCAL + 0.5) * FOOD_ENERGY_ROUNDING_KCAL
    return f"每份约 {rounded} 千卡"


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
    body_data = body_projection(agent, hour, scenario.body, inputs.meal_windows)
    levels = body_data["levels"]
    people = people_here_views(state, agent)
    inbox = _inbox_rows(inputs)
    activity_rows = inputs.extras["activity_rows"]
    bindings = _interaction_bindings(inputs, people, inbox)
    public_context = {"people": people, "inbox": inbox, "bindings": bindings}
    data: dict[str, Any] = {
        "agent_id": agent.agent_id, "current_time": now,
        "scene": _scene_projection(inputs, local, levels, people, inbox),
        "facts": _facts_projection(inputs, local, levels, public_context),
        "interaction_bindings": bindings,
        "body": body_data, "balance_fen": state.accounts[agent.balance_account_id],
        "active_activities": activity_rows["control"],
        "paused_activities": activity_rows.get("paused_control"),
        "pending_conclusions": activity_rows["pending"],
    }
    if inputs.motivation_internal is not None:
        data["motivation_internal"] = inputs.motivation_internal
    return data


def _scene_projection(inputs: ProjectionInputs, local: datetime, levels: dict,
                      people: list[dict], inbox: list[dict]) -> dict[str, Any]:
    """@brief 组装场景事实，最近收件头与完整收件事实分开选择；待收尾活动写「刚结束」，受限轮另加一句说明（不写次数）。

    「正在做」只来自进行中活动（current_activities）；人物自述不进场景，只在事实清单的进度层与目标行出现。
    """
    agent, now = inputs.agent, inputs.state.current_time
    location = _location_index(inputs.scenario).get(agent.location_id)
    activity_rows = inputs.extras["activity_rows"]
    # 场景层只暴露展示名与营业状态，去掉 location_id（location_id 仅供交互对象层使用）
    reachable_scene = [{"name": r["name"], "opening_status": r["opening_status"]}
                       for r in inputs.extras.get("reachable_places", [])]
    names = inputs.extras["visible_names"]["activities"]
    return {
        "current_time": local.isoformat(),
        "current_epoch": now,
        "location": location.name if location else agent.location_id,
        "people_here": [item["name"] for item in people],
        "felt": levels,
        "current_activities": activity_rows["current"],
        "paused_activities": activity_rows["paused"],
        "just_ended": [names[item.activity_id]
                       for item in activity_control.pending_conclusions(inputs.state, agent.agent_id)],
        "day_plan": _plan_row(agent, now, inputs.scenario.timezone),
        "inbox_head": [entry["head"] for entry in inbox[-INBOX_HEAD_LIMIT:]],
        "inbox_has_older_unread": any(not entry["read"] for entry in inbox[:-INBOX_HEAD_LIMIT]),
        "todos_summary": _todos_summary(inputs.task_rows, now),
        "reachable_places": reachable_scene,
        "inventory": _inventory_names(inputs),
        "local_food_summary": _local_food_summary(inputs, levels["appetite"]),
        "precondition_hints": _precondition_hints(inputs),
        "same_moment_note": (SAME_MOMENT_NOTE if agent.same_moment_stage != SameMomentStage.OPEN
                             else ""),
        "just_now": _just_now(inputs),
    }


def _activity_fact_row(inputs: ProjectionInputs, activity: Any, name: str) -> dict[str, Any]:
    """@brief 进度层的一行活动：名称、阶段、客观进度（人读时长）与标注「你自己记下的，未经核验」的自述。"""
    required = {task.task_id: task.required_effective_seconds for task in inputs.scenario.tasks}
    remaining = activity_control.remaining_required(inputs.state, activity, required)
    row = {"name": name, "phase": activity_control.activity_phase(activity),
           "objective_progress": activity_control.objective_progress(activity, remaining)}
    if activity.progress_note:
        row.update(subjective_progress=activity.progress_note,
                   subjective_status=activity_control.SELF_REPORT_MARK)
    return row


def _session_fact_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 进度层按会话（地点停留或一段路）分组，会话按开始时刻排序；活动列在其最近所属的会话下。

    容量压缩在请求边界执行（life.prompt_capacity 按会话合并已收尾活动）。
    """
    state, agent_id = inputs.state, inputs.agent.agent_id
    names = inputs.extras["visible_names"]
    grouped: dict[str, list] = {session_id: [] for session_id in names["sessions"]}
    for activity_id, name in names["activities"].items():
        activity = state.activities[activity_id]
        if activity.session_ids:
            grouped[activity.session_ids[-1]].append((activity, name))
    sessions = sorted((state.sessions[session_id] for session_id in grouped),
                      key=lambda item: (item.started_at, item.session_id))
    return [{"name": names["sessions"][session.session_id],
             "activities": [_activity_fact_row(inputs, activity, name)
                            for activity, name in grouped[session.session_id]]}
            for session in sessions if session.agent_id == agent_id]


def _experience_sentences(inputs: ProjectionInputs) -> list[str]:
    """@brief 本人最近五条已核验经历写成的中文句子（与私有 verified_experiences 同一窗口与顺序，不含内部身份）。

    例：「14:17 开车到了张江办公室」「13:57–14:17 吃了番茄炒蛋」；地点、商品与内容取场景名称，出行方式取自所属出行活动。
    """
    return [_experience_sentence(inputs, fact) for fact in inputs.extras.get("verified_experiences", [])]


def _experience_sentence(inputs: ProjectionInputs, fact: dict[str, Any]) -> str:
    """@brief 一条已核验经历的确定性句子：等待、食用、播放带起止时刻，到达带到达时刻与方式。"""
    zone = inputs.scenario.timezone
    if fact["kind"] == "arrived":
        travel = inputs.state.activities[fact["activity_id"]]
        mode = "走路" if travel.travel_mode == TravelMode.WALKING else "开车"
        place = _location_index(inputs.scenario)[fact["location_id"]].name
        denied = "，但没能进去" if fact["access_status"] == "denied" else ""
        return f"{_clock(fact['occurred_at'], zone)} {mode}到了{place}{denied}"
    span = f"{_clock(fact['interval_start'], zone)}–{_clock(fact['interval_end'], zone)}"
    if fact["kind"] == "waited":
        return f"{span} 等待了{activity_control.readable_duration(fact['duration_seconds'])}"
    if fact["kind"] == "consumed":
        return f"{span} 吃了{_product_names(inputs.scenario)[fact['product_id']]}"
    podcast = any(item.item_id == fact["content_id"] and item.kind == "podcast_episode"
                  for item in inputs.scenario.contents)
    title = inputs.extras["content_titles"][fact["content_id"]]
    return f"{span} {'听了' if podcast else '看了'}《{title}》"


def _clock(epoch: int, timezone_name: str) -> str:
    """@brief 场景时区的本地时刻 HH:MM。"""
    return f"{local_datetime(epoch, timezone_name):%H:%M}"


def _progress_alarms(inputs: ProjectionInputs) -> list[str]:
    """@brief 本轮临近截止提醒，以及当日目标提醒触发那一秒的「到了你给『<目标>』定的 HH:MM」（第四章）。"""
    now = inputs.state.current_time
    deadlines = [f"{row['title']}即将截止" for row in inputs.task_rows
                 if row.get("due_at") is not None and 0 < int(row["due_at"]) - now <= 7200]
    return deadlines + [reminders.alarm_sentence(goal, inputs.scenario.timezone)
                        for goal in reminders.alarm_goals(inputs.state, inputs.agent)]


def _progress_projection(inputs: ProjectionInputs) -> dict[str, Any]:
    """@brief 将按会话分组的活动进度、最新结算和截止提醒归入同一个进度层。"""
    return {"sessions": _session_fact_rows(inputs),
            "results": [_progress_result(inputs, row) for row in inputs.recent_results],
            "alarms": _progress_alarms(inputs)}


def _progress_result(inputs: ProjectionInputs, row: dict[str, Any]) -> dict[str, Any]:
    """@brief 公开动作结果的语义字段，不要求人物处理事件身份。"""
    public = public_action_result(row)
    keys = ("occurred_at", "status", "error_code", "message", "work_review")
    result = {key: public[key] for key in keys if key in public}
    details = _progress_result_details(inputs, public)
    if details:
        result["details"] = details
    return result


def _progress_result_details(inputs: ProjectionInputs, public: dict[str, Any]) -> dict[str, Any]:
    """@brief 把订单、到达、浏览与回看结果转为名称与金额，省去所有内部身份。"""
    details: dict[str, Any] = {}
    products = {item.product_id: item.name for item in inputs.scenario.products}
    places = {item.location_id: item.name for item in inputs.scenario.locations}
    if "orders" in public:
        details["purchases"] = [{"product": products[item["product_id"]],
                                 "quantity": item["quantity"], "charged_fen": item["total_price_fen"]}
                                for item in public["orders"]]
    if "arrival" in public:
        arrival = public["arrival"]
        details["arrival"] = {"place": places[arrival["location_id"]],
                              "access_status": arrival["access_status"]}
    if "browse_result" in public:
        items = public["browse_result"].get("items", [])
        details["browse_items"] = [products[item["product_id"]] for item in items]
    if "recall" in public:
        details["recall"] = public["recall"]
    if "reason" in public:
        details["reason"] = public["reason"]
        details["next_step"] = public.get("next_step", "")
    if public.get("planned"):
        details["planned"] = [{"name": item["name"], "duration": readable_duration(item["planned_seconds"])}
                              for item in public["planned"]]
    return details


def _just_now(inputs: ProjectionInputs) -> str:
    """@brief 同刻轮次的「刚才」：一句中文写上一轮本人世界请求的公开结果；每秒首轮为空串，不含内部身份。

    已受理写开始了什么，被拒绝写原因与下一步，消息写已送出，购买写已下单（docs/dev/SPEC-time-and-energy.md
    第一章「同一秒内的呈现与「刚才」」）。
    """
    row = inputs.extras.get("previous_request_result")
    if inputs.agent.same_moment_rounds == 0 or row is None:
        return ""
    public = public_action_result(row)
    details = _progress_result_details(inputs, public)
    if public["status"] == "rejected":
        step = f"；下一步：{details['next_step']}" if details.get("next_step") else ""
        return f"刚才的请求没有成功：{details['reason']}{step}。"
    if "purchases" in details:
        return f"刚才已下单：{'、'.join(item['product'] for item in details['purchases'])}。"
    if "message_id" in row["result"]["data"]:
        return "刚才的消息已送出。"
    if details.get("planned"):
        return f"刚才已开始：{'、'.join(item['name'] for item in details['planned'])}。"
    return "刚才的请求已受理。"


def _facts_projection(inputs: ProjectionInputs, local: datetime, levels: dict,
                      context: dict[str, Any]) -> dict[str, Any]:
    """@brief 组装完整事实清单，全部新结果经过公开字段投影。"""
    agent, now = inputs.agent, inputs.state.current_time
    people, inbox, bindings = context["people"], context["inbox"], context["bindings"]
    return {
        "current_time": local.isoformat(),
        "current_epoch": now,
        "location": _location_index(inputs.scenario)[agent.location_id].name,
        "people_here": [{"name": row["name"], "can_talk": row["can_talk"]} for row in people],
        "contacts": _contact_rows(inputs.scenario, agent),
        "inbox": _public_inbox(inputs, inbox),
        "tasks": _task_fact_rows(inputs),
        "progress": _progress_projection(inputs),
        "interactive_objects": [_public_interaction(row) for row in bindings],
        "routes": _public_routes(inputs),
        "products": _public_products(inputs),
        "inventory": _public_inventory(inputs),
        "day_plan": _plan_fact(agent, now, inputs.scenario.timezone),
        "felt": levels,
        "drive_summary": drive_summary(levels, inputs.task_rows, now),
        "experiences": _experience_sentences(inputs),
    }


def _location_index(scenario: RuntimeScenario) -> dict[str, Any]:
    """@brief 地点身份索引。"""
    return {item.location_id: item for item in scenario.locations}


def _contact_rows(scenario: RuntimeScenario, agent: AgentState) -> list[dict[str, Any]]:
    """@brief 人物通讯录只公开称谓与关系，内部身份留在观察绑定中。"""
    config = next((item for item in scenario.agents if item.agent_id == agent.agent_id), None)
    contacts = getattr(config, "contacts", ()) or ()
    return [{"salutation": item.salutation, "relation": item.relation} for item in contacts]


def _public_inbox(inputs: ProjectionInputs, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """@brief 将收件人可见消息转成标题、发件人和时间，不公开消息身份。"""
    people = {f"agent:{item.agent_id}": item.name for item in inputs.state.agents.values()}
    config = next(item for item in inputs.scenario.agents if item.agent_id == inputs.agent.agent_id)
    people.update({item.contact_id: item.salutation for item in config.contacts})
    return [{"kind": row["kind"], "sender": people[row["sender_id"]]
             if row["sender_id"] is not None else None,
             "head": row["head"], "read": row["read"], "created_at": row["created_at"]}
            for row in rows]


def _public_routes(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 路线按地点名称公开，时长、票价与费力由系统告知；feasible 只表示可达。"""
    places = _location_index(inputs.scenario)
    return [{"destination": places[row["destination"]].name, "mode": row["mode"],
             "minutes": row["minutes"], "fare_fen": row["fare"], "effort": row["effort"],
             "feasible": row["feasible"], "status": row["status"], "reason": row["reason"]}
            for row in inputs.extras.get("route_rows", [])]


def _public_products(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 供给只公开购物决策所需名称、报价与履约事实；食品带每份热量，配送写人读送达时刻。"""
    places = _location_index(inputs.scenario)
    foods = _food_items(inputs.scenario)
    arrivals = inputs.extras.get("delivery_expected_at", {})
    rows = []
    for row in inputs.extras.get("product_rows", []):
        public = {"object": _interaction_object_label(inputs, {"kind": "supply", "name": row["name"],
                                                               "object_id": row["supply_id"]}),
                  "product": row["name"], "place": row["location_name"],
                  "price_fen": row["purchase_price_fen"], "quote_status": row["quote_status"],
                  "current_stock": row["current_stock"], "purchase_allowed": row["purchase_allowed"],
                  "fulfillment_modes": row["fulfillment_modes"],
                  "delivery_destinations": [places[item].name for item in row["delivery_destination_ids"]]}
        if row["product_id"] in foods:
            public["energy"] = food_energy_label(foods[row["product_id"]].energy_kcal_per_unit)
        if "delivery" in row["fulfillment_modes"]:
            public["delivery_eta"] = _delivery_eta(inputs, arrivals.get(row["supply_id"]))
        rows.append(public)
    return rows


def _delivery_eta(inputs: ProjectionInputs, expected_at: int | None) -> str:
    """@brief 人读送达时刻，例如「约 13 小时后送达，明天 11:16」；送不到当前地点时写「暂时送不到这里」。"""
    if expected_at is None:
        return "暂时送不到这里"
    now = inputs.state.current_time
    when = dining.readable_clock(now, expected_at, inputs.scenario.timezone)
    return f"{dining.readable_duration(expected_at - now)}后送达，{when}"


def _public_inventory(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 持有物按地点和商品名称公开正数持有量；食品带每份热量，不在现场地点的持有物注明只能在当地使用。

    现场地点取 admission.present_location：途中不在任何地点，出发地的持有物同样不在手边（第五章「并行准入」物品状态）。
    """
    places = _location_index(inputs.scenario)
    items = {item.product_id: item for item in (*inputs.scenario.products, *inputs.scenario.inventory_items)}
    present = admission.present_location(inputs.state, inputs.agent.agent_id)
    rows = []
    for place_id, holding in sorted(inputs.agent.inventory.items()):
        for product_id, quantity in sorted(holding.items()):
            if quantity <= 0:
                continue
            item, place = items[product_id], places[place_id].name
            row: dict[str, Any] = {"place": place, "product": item.name, "quantity": quantity}
            if "eat" in item.use_modes:
                row["energy"] = food_energy_label(item.energy_kcal_per_unit)
            if place_id != present:
                row["note"] = f"不在手边，只能在{place}使用"
            rows.append(row)
    return rows


def _food_items(scenario: RuntimeScenario) -> dict[str, Any]:
    """@brief 食品（use_modes 含 eat）的商品身份索引，覆盖在售商品与独立库存物品。"""
    return {item.product_id: item for item in (*scenario.products, *scenario.inventory_items)
            if "eat" in item.use_modes}


def _local_food_summary(inputs: ProjectionInputs, hunger: str) -> str:
    """@brief 场景的当地餐食句或吃饭途径句：饥饿分级得出后生成，只引用本轮观察的事实。"""
    facts = dining.FoodPathFacts(
        now=inputs.state.current_time, timezone=inputs.scenario.timezone,
        local_sentence=inputs.extras["local_meal_sentence"],
        on_hand=_meals_on_hand(inputs), pending=_meals_on_the_way(inputs),
        here=_meals_here(inputs), deliverable=_meals_deliverable_here(inputs),
        nearby=_food_nearby(inputs), browse_available=_browse_available(inputs))
    return dining.local_food_summary(hunger, facts)


def _meals_on_hand(inputs: ProjectionInputs) -> tuple[dining.MealOption, ...]:
    """@brief 手边能吃的：现场地点正数持有的食品（与场景手边持有物同一判定）。"""
    foods = _food_items(inputs.scenario)
    return tuple(dining.MealOption(foods[product_id].name, foods[product_id].energy_kcal_per_unit)
                 for product_id in sorted(_holding_ids(inputs.state, inputs.agent)) if product_id in foods)


def _meals_on_the_way(inputs: ProjectionInputs) -> tuple[dining.PendingMeal, ...]:
    """@brief 在路上的：本人待送达的食品订单（state.deliveries，与观察事件 pending_deliveries 同源）。"""
    foods, places, agent = _food_items(inputs.scenario), _location_index(inputs.scenario), inputs.agent
    return tuple(dining.PendingMeal(foods[item.product_id].name, item.expected_at,
                                    None if item.destination_id == agent.location_id
                                    else places[item.destination_id].name)
                 for item in inputs.state.deliveries.values()
                 if item.agent_id == agent.agent_id and item.status == "pending" and item.product_id in foods)


def _meals_here(inputs: ProjectionInputs) -> tuple[dining.MealOption, ...]:
    """@brief 这里就能买的：现场地点本轮已呈现、可购买且可到店取得的食品供给。"""
    foods = _food_items(inputs.scenario)
    return tuple(dining.MealOption(row["name"], foods[row["product_id"]].energy_kcal_per_unit)
                 for row in inputs.extras.get("product_rows", [])
                 if row["product_id"] in foods and row["purchase_allowed"] and row["pickup_allowed"])


def _meals_deliverable_here(inputs: ProjectionInputs) -> tuple[int, ...]:
    """@brief 能送到这里的：本轮呈现、可购买、含配送且能送到当前地点的食品的预计送达时刻。"""
    foods = _food_items(inputs.scenario)
    arrivals = inputs.extras.get("delivery_expected_at", {})
    return tuple(arrivals[row["supply_id"]] for row in inputs.extras.get("product_rows", [])
                 if row["product_id"] in foods and row["purchase_allowed"]
                 and arrivals.get(row["supply_id"]) is not None)


def _food_nearby(inputs: ProjectionInputs) -> tuple[dining.NearbyFood, ...]:
    """@brief 附近能买的：本轮已呈现供给所在、此刻营业且能到店买到食品的地点，取可达路线的分钟数。"""
    foods = _food_items(inputs.scenario)
    stores = {row["location_id"] for row in inputs.extras.get("product_rows", [])
              if row["product_id"] in foods and row["purchase_allowed"] and "pickup" in row["fulfillment_modes"]}
    places = _location_index(inputs.scenario)
    return tuple(dining.NearbyFood(places[route["destination"]].name, route["minutes"], route["mode"])
                 for route in inputs.extras.get("route_rows", [])
                 if route["destination"] in stores and route["status"] == "reachable")


def _browse_available(inputs: ProjectionInputs) -> bool:
    """@brief 本轮能否浏览商品：有效菜单允许浏览、有目录对象，且浏览与进行中活动的占用不冲突。"""
    return bool(inputs.extras.get("browse_allowed") and inputs.extras.get("browse_targets")
                and _resource_available(inputs, {"canonical_action": "browse", "travel_mode": None}))


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
        head = (_chat_invite_head(inputs.state, message.sender_id) if message.kind == "chat_invite"
                else message.text[:40])
        rows.append({"id": message_id, "kind": message.kind, "sender_id": message.sender_id,
                     "head": head, "read": message_id in agent.read_inbox_ids,
                     "created_at": message.created_at})
    rows.sort(key=lambda row: (row["created_at"], row["id"]))
    return rows


def _chat_invite_head(state: WorldState, sender_id: str) -> str:
    """@brief 交谈邀请的头句写明回应方式：「对<发起人>选择交谈即接受」（第五章「交谈」）。"""
    sender = next((item.name for item in state.agents.values() if f"agent:{item.agent_id}" == sender_id), "对方")
    return f"{sender}邀请你交谈，对{sender}选择交谈即接受"


def _todos_summary(task_rows: tuple[dict[str, Any], ...], now: int) -> str:
    """@brief 待办任务一句话（scene 合同，无数字权重）。"""
    pending = [row for row in task_rows if row["remaining_contract_seconds"] > 0]
    if not pending:
        return ""
    return ("有待办任务：最早的一份快到截止" if _task_due_soon(task_rows, now)
            else "有待办任务可以推进")


def _reminder(goal: Any, timezone_name: str) -> dict[str, str]:
    """@brief 目标行的提醒时刻「提醒 HH:MM」（没有提醒的目标不写）。"""
    return {"reminder": f"提醒 {remind_clock(goal.remind_at, timezone_name)}"} if goal.remind_at is not None else {}


def _plan_row(agent: AgentState, now: int, timezone_name: str) -> dict[str, Any] | None:
    """@brief 场景按目标顺序展示意图、推进状态与提醒时刻，完整文字只进入事实。"""
    plan = agent.day_plan
    if plan is None or plan.day_end <= now:
        return None
    return {"goals": [{"intent_kind": goal.intent_kind,
                       "progressed": goal.progressed_at is not None, **_reminder(goal, timezone_name)}
                      for goal in plan.goals]}


def _plan_fact(agent: AgentState, now: int, timezone_name: str) -> dict[str, Any] | None:
    """@brief 事实保留当日目标文字、推进状态与提醒时刻，隐藏内部活动证据；目标由零点计划生成，自述标未经核验。"""
    plan = agent.day_plan
    if plan is None or plan.day_end <= now:
        return None
    return {"goals": [{"task_name": f"目标：{goal.intent_kind}：{goal.goal}",
                       "intent_kind": goal.intent_kind,
                       "progressed": goal.progressed_at is not None,
                       **_reminder(goal, timezone_name),
                       "progress_note": goal.progress_note,
                       "subjective_status": activity_control.SELF_REPORT_MARK}
                      for goal in plan.goals]}


def _task_fact_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief facts 任务行：公开任务名称、前置条件与剩余合同工时；时长上界不足时另写「到截止前做不完」。"""
    ranges = new_material_ranges(inputs.agent)
    places = _location_index(inputs.scenario)
    tasks = {item.task_id: item for item in inputs.scenario.tasks}
    rows = []
    for row in inputs.task_rows:
        fact = {
            "task_name": row["title"],
            "due_at": row["due_at"],
            "remaining_contract_seconds": row["remaining_contract_seconds"],
            "required_location": (places[row["required_location_id"]].name
                                  if row.get("required_location_id") else None),
            "required_skill": row.get("required_skill"),
            "minimum_skill": row.get("minimum_skill"),
            "maximum_duration_seconds": row.get("maximum_duration_seconds"),
            "required_effective_seconds": row.get("required_effective_seconds"),
            "new_material_chars": sum(item["chars"] for item in ranges.get(row["task_id"], [])),
        }
        worked, effective = work_totals(inputs.state, inputs.agent.agent_id, row["task_id"])
        outlook = completion_outlook(tasks[row["task_id"]], worked, effective, inputs.state.current_time)
        if outlook is not None:
            fact["completion_outlook"] = outlook
        rows.append(fact)
    return rows


def _product_names(scenario: RuntimeScenario) -> dict[str, str]:
    """@brief 商品身份到目录展示名的索引（库存条目与商品目录合并）。"""
    return {item.product_id: item.name
            for item in (*scenario.products, *scenario.inventory_items)}


def _inventory_names(inputs: ProjectionInputs) -> list[str]:
    """@brief 手边持有物展示名（scene 合同）：始终来自目录名称；途中没有手边持有物。"""
    names = _product_names(inputs.scenario)
    return sorted({names[product_id] for product_id in _holding_ids(inputs.state, inputs.agent)})


def _holding_ids(state: WorldState, agent: AgentState) -> set[str]:
    """@brief 手边持有量为正的商品身份集合：只取现场地点，途中（有进行中出行）为空。"""
    present = admission.present_location(state, agent.agent_id)
    holding = agent.inventory.get(present, {}) if present is not None else {}
    return {product_id for product_id, quantity in holding.items() if quantity > 0}


def _interaction_bindings(inputs: ProjectionInputs, people: list[dict[str, Any]],
                          inbox: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """@brief 将本轮可见对象冻结为可读选择及仅供引擎使用的身份映射。

    不按人物状态剪裁：可见的全部对象与动作都列出，条件未满足的动作带一句说明（docs/dev/SPEC-time-and-energy.md
    第五章「动作照常列出，做错了再说明」）；按世界事实过滤后没有动作的对象也列出，动作为空列表。
    """
    context = _admission_context(inputs)
    rows = _interactive_objects_rows(inputs, people, inbox)
    bindings = [_interaction_binding(inputs, row, context) for row in rows]
    labels = [row["object"] for row in bindings]
    if len(labels) != len(set(labels)):
        repeated = sorted({label for label in labels if labels.count(label) > 1})
        logging.error("Interactive object labels are ambiguous: %s", repeated)
        raise ValueError("interactive object labels must be unique")
    return bindings


def _admission_context(inputs: ProjectionInputs) -> admission.Context:
    """@brief 交互对象层说明所用的准入判定上下文（与受理同一套判定）。"""
    return admission.Context(state=inputs.state, agent_id=inputs.agent.agent_id,
                             place_names={item.location_id: item.name for item in inputs.scenario.locations},
                             activity_names=inputs.extras.get("visible_names", {}).get("activities", {}))


def _world_facts(scenario: RuntimeScenario) -> admission.WorldFacts:
    """@brief 场景冻结目录的准入视图：任务、供给、可持有物与内容。"""
    return admission.WorldFacts(tasks={item.task_id: item for item in scenario.tasks},
                                supplies={item.supply_id: item for item in scenario.supplies},
                                items={item.product_id: item for item in (*scenario.products,
                                                                          *scenario.inventory_items)},
                                contents={item.item_id: item for item in scenario.contents})


def _interaction_binding(inputs: ProjectionInputs, row: dict[str, Any],
                         context: admission.Context) -> dict[str, Any]:
    """@brief 将一个内部对象身份和本轮动作绑定到可读短语；每个动作带未满足条件的说明（没有时为 None）。"""
    identity, kind = row["object_id"], row["kind"]
    menu = inputs.extras.get("interaction_menu")
    actions = [action for action in _interaction_actions(inputs, row)
               if menu is None or action["canonical_action"] in menu.allowed_actions]
    result = {"object": _interaction_object_label(inputs, row), "kind": kind, "object_id": identity,
              "actions": actions}
    facts = _world_facts(inputs.scenario)
    for action in actions:
        action["note"] = _action_note(context, admission.selection_item(result, action, facts), row)
    if kind == "supply":
        supply = next(item for item in inputs.extras["product_rows"] if item["supply_id"] == identity)
        result["quote"] = {"price_version": supply.get("price_version"),
                           "price_fen": supply.get("price_fen"),
                           "purchase_price_fen": supply.get("purchase_price_fen")}
    return result


def _action_note(context: admission.Context, item: admission.Item, row: dict[str, Any]) -> str | None:
    """@brief 一个动作的说明：先报准入不成立的原因，再报对象自身的条件（开工 60 秒、对方不便交谈）；
    内容对象另写时长（「从头再看」为全长，「接着看」为剩余）。"""
    note = admission.action_note(context, item, context.activity_names) or row.get("note")
    detail = row.get("duration_note")
    if detail:
        return f"{note}；{detail}" if note else detail
    return note


def _resource_available(inputs: ProjectionInputs, action: dict[str, Any]) -> bool:
    """@brief 按执行器同一资源声明核对一项动作与本人正在执行的活动是否不冲突（吃饭途径句判定浏览是否可行）。"""
    occupied = set().union(*(item.resource_claims for item in
                             active_activities(inputs.state, inputs.agent.agent_id)))
    canonical = action["canonical_action"]
    if canonical == "purchase":
        return True
    if canonical in {"chat", "call"}:
        claims = social_actions.SOCIAL_RESOURCE_CLAIMS
    else:
        affordances = default_affordances()
        kinds = [name for name, spec in affordances.kinds.items() if spec.action == canonical]
        if len(kinds) != 1:
            logging.error("Interaction action has no unique resource declaration: %s", canonical)
            raise ValueError("interaction action has no unique resource declaration")
        claims = affordances.resource_claims(kinds[0], action.get("travel_mode"))
    return not bool(occupied & claims)


def _interaction_object_label(inputs: ProjectionInputs, row: dict[str, Any]) -> str:
    """@brief 用人能区分的类别、名称和场所表达对象，不输出内部编号。"""
    kind, name = row["kind"], row["name"]
    if kind == "supply":
        supply = next(item for item in inputs.extras["product_rows"]
                      if item["supply_id"] == row["object_id"])
        place = supply["location_name"]
        return f"{place}出售的{name}"
    if kind == "catalog":
        return "商品目录"
    if kind == "self":
        return "自己"
    prefixes = {"work_task": "工作", "location": "地点", "holding": "持有物",
                "content": "内容", "game": "游戏", "lifestyle": "活动",
                "person": "人物", "contact": "联系人", "mail": "邮件"}
    return f"{prefixes[kind]}：{name}"


def _interaction_actions(inputs: ProjectionInputs, row: dict[str, Any]) -> list[dict[str, Any]]:
    """@brief 将内部动作映射为动词与方式：路线按目的地冻结可用方式，供给按履约方式，持有物按使用方式与配方。

    按世界事实过滤（不可达或未核实的路线、不出售的供给）的动作不列出；其余照常列出。
    """
    if row["kind"] == "location":
        return [binding_action(TRAVEL_WAYS[route["mode"]], "travel_to", travel_mode=route["mode"])
                for route in inputs.extras.get("route_rows", [])
                if route["destination"] == row["object_id"] and route["status"] == "reachable"]
    if row["kind"] == "holding":
        product = next(item for item in (*inputs.scenario.products, *inputs.scenario.inventory_items)
                       if item.product_id == row["object_id"])
        recipes = inputs.extras.get("recipe_product_ids", frozenset())
        return [binding_action(_use_way(mode, product.product_id in recipes), "consume", use_mode=mode)
                for mode in product.use_modes]
    if row["kind"] == "supply":
        return _supply_actions(inputs, row)
    if row.get("way") is not None:
        return [binding_action(row["way"], "watch")]
    return [binding_action(ACTION_WAYS[action], action) for action in row["actions"]]


def _use_way(mode: str, recipe: bool) -> str:
    """@brief 持有物的方式：食用；consume:use 是库存转换配方时为 obtain 的「制作」，否则为「使用」。"""
    if mode == "eat":
        return USE_WAYS["eat"]
    return "制作" if recipe else USE_WAYS["use"]


def _supply_actions(inputs: ProjectionInputs, row: dict[str, Any]) -> list[dict[str, Any]]:
    """@brief 供给的取得方式：不可购买（未营业、缺货、无报价等世界事实）时不列；供给支持到店就列「到店」，支持配送
    就列「远程下单」，不按人物此刻在哪里剪裁（docs/dev/SPEC-time-and-energy.md 第五章「动作照常列出，做错了再说明」）：人不在店里、
    此地送不到时由准入判定写说明（「需要在<店>」「暂时送不到这里」），选了以 ACTIVITY_PRECONDITION_UNMET 返回下一步。"""
    supply = next(item for item in inputs.extras["product_rows"] if item["supply_id"] == row["object_id"])
    if not supply["purchase_allowed"]:
        return []
    return [binding_action(PURCHASE_WAYS[mode], "purchase", fulfillment=mode)
            for mode in ("pickup", "delivery") if mode in supply["fulfillment_modes"]]


def _public_interaction(binding: dict[str, Any]) -> dict[str, Any]:
    """@brief 仅展示可读对象与「动词：方式」加说明的动作，不泄露内部映射。"""
    return {"object": binding["object"], "actions": [public_action(action) for action in binding["actions"]]}


def _interactive_objects_rows(inputs: ProjectionInputs, people: list[dict[str, Any]],
                              inbox: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """@brief 交互对象层：本轮可见对象及其内部动作，对象自身的条件说明（note）与内容时长（duration_note）。

    动作按对象绑定（docs/dev/SPEC-agent-interaction.md「对象与动作选择」）：每个对象只列出对它自己合法的动作，
    start_activity 的动词与方式取值即这些枚举的并集投影。「自己」（等待、睡觉）恒在列表中。
    """
    rows = _task_object_rows(inputs)
    rows.extend(_place_object_rows(inputs))
    rows.extend(_supply_object_rows(inputs))
    rows.extend(_holding_object_rows(inputs))
    rows.extend(_catalog_object_rows(inputs))
    rows.extend(_content_object_rows(inputs))
    rows.extend(_game_object_rows(inputs))
    rows.extend(_lifestyle_object_rows(inputs))
    rows.extend(_person_object_rows(inputs, people))
    rows.extend(_mail_object_rows(inputs, inbox))
    rows.append(_self_object_row(inputs))
    return rows


def _task_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 工作任务对象：开工动作照常列出；距截止或剩余合同不足 60 秒时带说明（第八章「工作」）。

    技能不够的任务不列开工动作：技能是人物不变的事实，没有能满足条件的状态。
    """
    agent, now = inputs.agent, inputs.state.current_time
    tasks = {item.task_id: item for item in inputs.scenario.tasks}
    rows: list[dict[str, Any]] = []
    for row in inputs.task_rows:
        skill_ok = (agent.skill_levels.get(row.get("required_skill"), 0)
                    >= (row.get("minimum_skill") or 0.0))
        worked, _ = work_totals(inputs.state, agent.agent_id, row["task_id"])
        rows.append({
            "object_id": row["task_id"],
            "kind": "work_task",
            "name": row["title"],
            "actions": ["work"] if skill_ok else [],
            "note": work_start_blocker(tasks[row["task_id"]], worked, now),
        })
    return rows


def _place_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 可达地点对象：只接受前往动作。"""
    return [{
        "object_id": place["location_id"],
        "kind": "location",
        "name": place["name"],
        "actions": ["travel_to"],
    } for place in inputs.extras.get("reachable_places", [])]


def _supply_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 本轮呈现的供给对象：只接受取得动作，资格沿用供给自身的购买声明。"""
    names = _product_names(inputs.scenario)
    return [{
        "object_id": row["supply_id"],
        "kind": "supply",
        "name": names[row.get("product_id", "")],
        "actions": ["purchase"],
    } for row in inputs.extras.get("product_rows", [])]


def _holding_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 手边持有的商品对象：按目录支持食用、使用或制作；途中没有手边持有物。"""
    names = _product_names(inputs.scenario)
    return [{
        "object_id": product_id,
        "kind": "holding",
        "name": names[product_id],
        "actions": ["consume"],
    } for product_id in sorted(_holding_ids(inputs.state, inputs.agent))]


def _catalog_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 目录对象：只接受浏览动作，身份沿用既有 browse_targets 闭集。"""
    return [{
        "object_id": target,
        "kind": "catalog",
        "name": target,
        "actions": ["browse"],
    } for target in inputs.extras.get("browse_targets", [])]


def _content_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 本轮呈现的内容对象：方式为「接着看」或「从头再看」（播客为「接着听」或「从头再听」），都映射为观看。

    已看完或剩余不足 60 秒的给全长并从头派生，否则给剩余时长；时长写在说明里，不放进方式短语
    （docs/dev/SPEC-time-and-energy.md 第八章「呈现」）。
    """
    contents = {item.item_id: item for item in inputs.scenario.contents}
    return [{**_watch_row(inputs, contents[row["item_id"]]), "kind": "content", "name": row["title"]}
            for row in inputs.extras.get("content_rows", [])]


def _watch_row(inputs: ProjectionInputs, item: Any) -> dict[str, Any]:
    """@brief 一条可观看内容的动作与时长说明：「接着看」或「从头再看」（播客为「接着听」或「从头再听」），映射为观看；
    已看完或剩余不足 60 秒的写全长并从头派生，未看过的写全长，其余写剩余时长（第八章「呈现」）。"""
    progress = inputs.agent.content_progress.get(item.item_id)
    position = progress.position_seconds if progress else 0
    restart = restarts_from_beginning(position, item.duration_seconds)
    duration = readable_duration(item.duration_seconds if restart else item.duration_seconds - position)
    return {"object_id": item.item_id, "actions": ["watch"],
            "way": CONTENT_WAYS[(item.kind == "podcast_episode", restart)],
            "duration_note": f"全长{duration}" if restart or position == 0 else f"还剩{duration}"}


def _game_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 本轮呈现的游戏对象：可玩的游戏只接受游玩；内容侧的游戏会话只能观看，方式与时长说明同内容对象
    （「接着看」或「从头再看」，全长或剩余时长）。场景声明身份与内容侧会话同池。"""
    contents = {item.item_id: item for item in inputs.scenario.contents}
    rows = []
    for row in inputs.extras.get("game_rows", []):
        if "game_id" in row:
            rows.append({"object_id": row["game_id"], "kind": "game", "name": row["title"], "actions": ["play"]})
        else:
            rows.append({**_watch_row(inputs, contents[row["item_id"]]), "kind": "game", "name": row["title"]})
    return rows


def _lifestyle_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 本轮呈现的生活活动目录条目：只接受进行动作；地点环境不合适（含途中）时带说明。"""
    present = admission.present_location(inputs.state, inputs.agent.agent_id)
    place = _location_index(inputs.scenario).get(present) if present is not None else None
    place_kinds = frozenset(place.location_kinds) if place else frozenset()
    rows = []
    for row in inputs.extras.get("lifestyle_rows", []):
        needed = inputs.extras["lifestyle_location_kinds"][row["activity_id"]]
        rows.append({
            "object_id": row["activity_id"],
            "kind": "lifestyle",
            "name": row["name"],
            "actions": ["do"],
            "note": None if feasible_kinds(place_kinds, needed) else "这里不适合做这件事",
        })
    return rows


def _person_object_rows(inputs: ProjectionInputs,
                        people: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """@brief 同场人物只列交谈（对方睡着或正在游戏时带说明）；通话只对通讯录联系人开放。"""
    rows = [{
        "object_id": view["person_id"],
        "kind": "person",
        "name": view["name"],
        "actions": ["chat"],
        "note": None if view["can_talk"] else "对方现在不方便交谈",
    } for view in people]
    config = next(item for item in inputs.scenario.agents if item.agent_id == inputs.agent.agent_id)
    rows.extend({
        "object_id": item.contact_id,
        "kind": "contact",
        "name": item.salutation,
        "actions": ["call"],
    } for item in config.contacts)
    return rows


def _mail_object_rows(inputs: ProjectionInputs, inbox: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """@brief 场景邮件对象：只收 kind 为 mail 的邮件，用标题和到达时间区分同主题邮件；交谈邀请、人物消息与
    联系人回复只在 facts.inbox 中呈现，不作为阅读对象。"""
    zone = ZoneInfo(inputs.scenario.timezone)
    return [{
        "object_id": row["id"],
        "kind": "mail",
        "name": f"{row['head']}（{datetime.fromtimestamp(row['created_at'], zone):%Y年%m月%d日 %H:%M:%S}）",
        "actions": ["read"],
    } for row in inbox if row["kind"] == "mail"]


def _self_object_row(inputs: ProjectionInputs) -> dict[str, Any]:
    """@brief 「自身」对象：等待与睡眠两个受保护动作恒可见。"""
    return {
        "object_id": f"agent:{inputs.agent.agent_id}",
        "kind": "self",
        "name": inputs.agent.name,
        "actions": ["wait", "sleep"],
    }


def _precondition_hints(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 可见任务中因地点前置条件未满足的提示列表（供 scene 渲染中文说明句）。"""
    agent = inputs.agent
    loc_index = _location_index(inputs.scenario)
    hints = []
    for row in inputs.task_rows:
        req_loc = row.get("required_location_id")
        if not req_loc or agent.location_id == req_loc:
            continue
        loc = loc_index.get(req_loc)
        hints.append({
            "task_title": row["title"],
            "required_location_id": req_loc,
            "required_location_name": loc.name if loc else req_loc,
        })
    return hints

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

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from worldmock.life import felt, narration, social
from worldmock.life.models import ActivityKind, AgentState, SessionStatus, WorldState, active_activities
from worldmock.life.result_projection import public_action_result
from worldmock.life.retrieval import feasible_kinds
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
        kinds = {item.kind for item in active_activities(state, other.agent_id)}
        presences.append(social.AgentPresence(
            agent_id=f"agent:{other.agent_id}", name=other.name, location=other.location_id,
            activity_family=" + ".join(sorted(kind.value for kind in kinds)) if kinds else "idle",
            awake=ActivityKind.SLEEP not in kinds,
            actively_gaming=ActivityKind.PLAY_GAME in kinds))
    return presences


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
        "active_activities": activity_rows["control"],
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
    # 场景层只暴露展示名与营业状态，去掉 location_id（location_id 仅供交互对象层使用）
    reachable_scene = [{"name": r["name"], "opening_status": r["opening_status"]}
                       for r in inputs.extras.get("reachable_places", [])]
    return {
        "current_time": local.isoformat(),
        "current_epoch": now,
        "location": location.name if location else agent.location_id,
        "people_here": [item["name"] for item in people],
        "felt": levels,
        "current_activities": activity_rows["current"],
        "paused_activities": activity_rows["paused"],
        "day_plan": _plan_row(agent, now),
        "inbox_head": [entry["head"] for entry in inbox[-INBOX_HEAD_LIMIT:]],
        "inbox_has_older_unread": any(not entry["read"] for entry in inbox[:-INBOX_HEAD_LIMIT]),
        "todos_summary": _todos_summary(inputs.task_rows, now),
        "reachable_places": reachable_scene,
        "inventory": _inventory_names(agent, inputs.scenario),
        "local_food_summary": inputs.extras["local_food_summary"],
        "recent_action_results": [row["message"] for row in inputs.recent_results[-1:]],
        "current_sessions": _active_session_scene_rows(inputs.state, agent.agent_id),
        "precondition_hints": _precondition_hints(inputs),
    }


def _session_fact_row(session: Any) -> dict[str, Any]:
    """@brief 把单个 SessionState 投影为 facts 行（排除 note_history，进度层合同）。"""
    return {
        "session_id": session.session_id,
        "task_kind": session.task_kind,
        "task_ref": session.task_ref,
        "status": session.status,
        "started_at": session.started_at,
        "ended_at": session.ended_at,
        "targets": list(session.targets),
        "activity_ids": list(session.activity_ids),
        "segments": [list(seg) for seg in session.segments],
        "invested_seconds": session.invested_seconds,
        "effective_seconds": session.effective_seconds,
        "remaining_contract_seconds": session.remaining_contract_seconds,
        "preconditions_unmet": list(session.preconditions_unmet),
        "progress_note": session.progress_note,
        "note_updated_at": session.note_updated_at,
    }


def _session_fact_rows(state: WorldState, agent_id: int) -> list[dict[str, Any]]:
    """@brief 投影当前 agent 的全部会话为 facts.sessions 行（进度层合同）。"""
    rows = []
    for session in state.sessions.values():
        if session.agent_id != agent_id:
            continue
        row = _session_fact_row(session)
        budget = narration.SESSION_PROGRESS_LAYER_BUDGET
        row_json = json.dumps(row, ensure_ascii=False)
        if len(row_json) > budget and row.get("progress_note"):
            excess = len(row_json) - budget
            note = row["progress_note"]
            trimmed = note[: max(0, len(note) - excess)]
            row["progress_note"] = trimmed
        rows.append(row)
    return rows


def _active_session_scene_rows(state: WorldState, agent_id: int) -> list[dict[str, Any]]:
    """@brief 取当前 agent 的 active 会话渲染为 scene 短句所需的最小行（至多 2 条）。"""
    rows = []
    for session in state.sessions.values():
        if session.agent_id != agent_id:
            continue
        if session.status != SessionStatus.ACTIVE:
            continue
        label = session.targets[0] if session.targets else session.session_id
        rows.append({
            "label": label,
            "invested_seconds": session.invested_seconds,
            "progress_note": session.progress_note,
        })
        if len(rows) >= 2:
            break
    return rows


def _facts_projection(inputs: ProjectionInputs, local: datetime, levels: dict,
                      people: list[dict], inbox: list[dict]) -> dict[str, Any]:
    """@brief 组装完整事实清单，全部新结果经过公开字段投影。"""
    agent, now = inputs.agent, inputs.state.current_time
    return {
        "current_time": local.isoformat(),
        "current_epoch": now,
        "location_id": agent.location_id,
        "people_here": people,
        "contacts": _contact_rows(inputs.scenario, agent),
        "inbox": inbox,
        "tasks": _task_fact_rows(inputs),
        "sessions": _session_fact_rows(inputs.state, agent.agent_id),
        "interactive_objects": _interactive_objects_rows(inputs, people, inbox),
        "routes": inputs.extras.get("route_rows", []),
        "products": inputs.extras.get("product_rows", []),
        "inventory": agent.inventory,
        "day_plan": _plan_fact(agent, now),
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


def _plan_row(agent: AgentState, now: int) -> dict[str, Any] | None:
    """@brief 场景按目标顺序展示意图和推进状态，完整文字只进入事实。"""
    plan = agent.day_plan
    if plan is None or plan.day_end <= now:
        return None
    return {"goals": [{"intent_kind": goal.intent_kind,
                       "progressed": goal.progressed_at is not None}
                      for goal in plan.goals]}


def _plan_fact(agent: AgentState, now: int) -> dict[str, Any] | None:
    """@brief 事实保留当日完整目标与推进证据，空清单保持已提交身份。"""
    plan = agent.day_plan
    if plan is None or plan.day_end <= now:
        return None
    return plan.model_dump(mode="json")


def _task_fact_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief facts 任务行：含五项前置条件字段、剩余合同工时与新揭示材料区间。"""
    ranges = new_material_ranges(inputs.agent)
    rows = []
    for row in inputs.task_rows:
        rows.append({
            "task_id": row["task_id"],
            "title": row["title"],
            "due_at": row["due_at"],
            "remaining_contract_seconds": row["remaining_contract_seconds"],
            "required_location_id": row.get("required_location_id"),
            "required_skill": row.get("required_skill"),
            "minimum_skill": row.get("minimum_skill"),
            "maximum_duration_seconds": row.get("maximum_duration_seconds"),
            "required_effective_seconds": row.get("required_effective_seconds"),
            "new_material_ranges": ranges.get(row["task_id"], []),
        })
    return rows


def _product_names(scenario: RuntimeScenario) -> dict[str, str]:
    """@brief 商品身份到目录展示名的索引（库存条目与商品目录合并）。"""
    return {item.product_id: item.name
            for item in (*scenario.products, *scenario.inventory_items)}


def _inventory_names(agent: AgentState, scenario: RuntimeScenario) -> list[str]:
    """@brief 手边持有物展示名（scene 合同）：目录名称，缺名时回退标识。"""
    names = _product_names(scenario)
    return sorted({names.get(product_id, product_id)
                   for product_id in _holding_ids(agent)})


def _holding_ids(agent: AgentState) -> set[str]:
    """@brief 手边持有量为正的商品身份集合。"""
    return {product_id for holding in agent.inventory.values()
            for product_id, quantity in holding.items() if quantity > 0}


def _interactive_objects_rows(inputs: ProjectionInputs, people: list[dict[str, Any]],
                              inbox: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """@brief 交互对象层：本轮可见对象及其动作枚举与前置条件满足状态。

    动作按对象绑定（docs/dev/SPEC-agent-interaction.md「动作参数：能力如何收进 action」）：
    每个对象只列出对它自己合法的动作，start_activity 的 action 取值即这些枚举的并集投影。
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
    rows.extend(_mail_object_rows(inbox))
    rows.append(_self_object_row(inputs))
    return rows


def _task_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 工作任务对象：推进与提交两个动作，前置条件按技能与地点判定。"""
    agent = inputs.agent
    rows: list[dict[str, Any]] = []
    for row in inputs.task_rows:
        skill_ok = (agent.skill_levels.get(row.get("required_skill"), 0)
                    >= (row.get("minimum_skill") or 0.0))
        location_ok = (agent.location_id == row.get("required_location_id"))
        rows.append({
            "object_id": row["task_id"],
            "kind": "work_task",
            "name": row["title"],
            "actions": ["work", "submit"],
            "preconditions_met": bool(skill_ok and location_ok),
        })
    return rows


def _place_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 可达地点对象：只接受前往动作。"""
    return [{
        "object_id": place["location_id"],
        "kind": "location",
        "name": place["name"],
        "actions": ["travel_to"],
        "preconditions_met": True,
    } for place in inputs.extras.get("reachable_places", [])]


def _supply_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 本轮呈现的供给对象：只接受购买动作，资格沿用供给自身的购买声明。"""
    names = _product_names(inputs.scenario)
    rows = []
    for row in inputs.extras.get("product_rows", []):
        product_id = row.get("product_id", "")
        rows.append({
            "object_id": row["supply_id"],
            "kind": "supply",
            "name": names.get(product_id, product_id or row["supply_id"]),
            "actions": ["purchase"],
            "preconditions_met": bool(row.get("purchase_allowed")),
        })
    return rows


def _holding_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 手边持有的商品对象：只接受进食动作。"""
    names = _product_names(inputs.scenario)
    return [{
        "object_id": product_id,
        "kind": "holding",
        "name": names.get(product_id, product_id),
        "actions": ["consume"],
        "preconditions_met": True,
    } for product_id in sorted(_holding_ids(inputs.agent))]


def _catalog_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 目录对象：只接受浏览动作，身份沿用既有 browse_targets 闭集。"""
    return [{
        "object_id": target,
        "kind": "catalog",
        "name": target,
        "actions": ["browse"],
        "preconditions_met": True,
    } for target in inputs.extras.get("browse_targets", [])]


def _content_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 本轮呈现的内容对象：只接受观看动作，进度由 content_progress 单独承载。"""
    return [{
        "object_id": row["item_id"],
        "kind": "content",
        "name": row.get("title", row["item_id"]),
        "actions": ["watch"],
        "preconditions_met": True,
    } for row in inputs.extras.get("content_rows", [])]


def _game_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 本轮呈现的游戏对象：只接受游玩动作，场景声明身份与内容侧会话同池。"""
    rows = []
    for row in inputs.extras.get("game_rows", []):
        object_id = row.get("game_id") or row["item_id"]
        rows.append({
            "object_id": object_id,
            "kind": "game",
            "name": row.get("title", object_id),
            "actions": ["play"],
            "preconditions_met": True,
        })
    return rows


def _lifestyle_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 本轮呈现的生活活动目录条目：只接受执行动作，前置条件按地点环境类判定。"""
    place = _location_index(inputs.scenario).get(inputs.agent.location_id)
    place_kinds = frozenset(place.location_kinds) if place else frozenset()
    rows = []
    for row in inputs.extras.get("lifestyle_rows", []):
        needed = inputs.extras["lifestyle_location_kinds"][row["activity_id"]]
        rows.append({
            "object_id": row["activity_id"],
            "kind": "lifestyle",
            "name": row["name"],
            "actions": ["do"],
            "preconditions_met": feasible_kinds(place_kinds, needed),
        })
    return rows


def _person_object_rows(inputs: ProjectionInputs,
                        people: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """@brief 同场人物接受交谈通话留言；通讯录联系人不在场，只接受通话与留言。"""
    rows = [{
        "object_id": view["person_id"],
        "kind": "person",
        "name": view["name"],
        "actions": ["chat", "call", "message"],
        "preconditions_met": bool(view["can_talk"]),
    } for view in people]
    rows.extend({
        "object_id": item["contact_id"],
        "kind": "contact",
        "name": item["salutation"],
        "actions": ["call", "message"],
        "preconditions_met": True,
    } for item in _contact_rows(inputs.scenario, inputs.agent))
    return rows


def _mail_object_rows(inbox: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """@brief 收件箱对象：只接受阅读动作，已读条目仍可重读。"""
    return [{
        "object_id": row["id"],
        "kind": "mail",
        "name": row["head"],
        "actions": ["read"],
        "preconditions_met": True,
    } for row in inbox]


def _self_object_row(inputs: ProjectionInputs) -> dict[str, Any]:
    """@brief 「自身」对象：等待与睡眠两个受保护动作恒可见。"""
    return {
        "object_id": f"agent:{inputs.agent.agent_id}",
        "kind": "self",
        "name": inputs.agent.name,
        "actions": ["wait", "sleep"],
        "preconditions_met": True,
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

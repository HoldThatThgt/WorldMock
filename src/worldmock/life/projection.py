"""@brief 观察投影引擎侧组装：身体两组证据、在场人物与本轮交互对象的私有绑定（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第一章）。

发给人物模型的七层 JSON 由各层构建器生成（life.tool_layer、todo_layer、environment_layer、progress_layer）；本模块只从
世界状态计算观察的证据段（身体数值与分级、余额、活动控制行）与交互对象的私有绑定：每个本轮可见对象的可读名称、内部身份
与动词和方式映射。绑定动作的说明（note）由工具层按可用性单一来源填写（「工具层」）。身体数值只进事件供评估，分级进环境层
（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「数值与分级的分离」）。

供给可见期：仅实际呈现的供给具有购买凭据，位置未变且呈现后未超 1800 秒；供给身份与商品身份分别保留。
零点计划输入（life.day_plan_inputs）另取本模块的联系人与收件箱。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from worldmock.life import admission, felt, social
from worldmock.life.vocabulary import ACTION_WAYS, PURCHASE_WAYS
from worldmock.life.readable import TRAVEL_WAYS
from worldmock.life.interaction_menu import binding_action
from worldmock.life.models import ActivityKind, AgentState, WorldState, active_activities
from worldmock.life.object_labels import (SELF_LABEL, mail_name, object_label,
                                          supply_label)
from worldmock.life.scenario import BodyConfig, RuntimeScenario
from worldmock.life.sleep import SLEEPINESS_CLIP_MAX, sleepiness

#: 供给浏览可见期（秒）：距浏览超过该值清空（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「观察投影行为规则」）。
#: 路线费力分档的代谢当量分界：MET < 2 为「轻松」，2 ≤ MET < 3 为「一般」，≥ 3 为「费力」。
#: experiment_assumption：登记在 docs/EXPERIMENT.md#experiment-worldmock-实验设置「能量感知标签」。
ROUTE_EFFORT_MODERATE_MET = 2.0
ROUTE_EFFORT_STRENUOUS_MET = 3.0
#: 食品每份热量的取整粒度与「几乎不含热量」的上界（千卡）：按 5 进位取整到 10 千卡。
#: experiment_assumption：登记在 docs/EXPERIMENT.md#experiment-worldmock-实验设置「能量感知标签」。
FOOD_ENERGY_ROUNDING_KCAL = 10
FOOD_ENERGY_NEGLIGIBLE_KCAL = 5.0


@dataclass(frozen=True, slots=True)
class ProjectionInputs:
    """@brief 一次观察投影的只读输入束（引擎在观察边界装配）。"""

    state: WorldState
    scenario: RuntimeScenario
    agent: AgentState
    #: 当前人物已分配、尚未完成的工作主题行。
    task_rows: tuple[dict[str, Any], ...] = ()
    #: 人物档案餐时窗口（felt.MealWindows）。
    meal_windows: felt.MealWindows | None = None
    #: 当前观察所需的只读附加事实。
    extras: dict[str, Any] = field(default_factory=dict)


def hour_of_day(epoch: int, timezone_name: str) -> float:
    """@brief 场景时区自午夜起的小时数（含小数）。"""
    moment = local_datetime(epoch, timezone_name)
    return moment.hour + moment.minute / 60 + moment.second / 3600


def local_datetime(epoch: int, timezone_name: str) -> datetime:
    """@brief 场景时区本地时刻。"""
    return datetime.fromtimestamp(epoch, ZoneInfo(timezone_name))




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
            awake=ActivityKind.SLEEP not in kinds))
    return presences


def people_here_views(state: WorldState, agent: AgentState) -> list[dict[str, Any]]:
    """@brief 同一地点他人的可见投影闭集：姓名、活动家族、可交谈（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「社交最小可行版」）
    。

    按现场地点判定：人物自己在途中时身边没有人（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章「并行准入」）。
    """
    if admission.en_route(state, agent.agent_id):
        return []
    views = social.people_here(agent.location_id, other_presences(state, agent))
    return [view.model_dump(mode="json") for view in views]


def body_projection(agent: AgentState, hour: float, body: BodyConfig,
                    meal_windows: felt.MealWindows | None = None) -> dict[str, Any]:
    """@brief body 两组投影：levels 分级（进环境层）与 values 数值（只进事件）。

    饥饿分级按当前能量与身体配置的紧急阈值叠加两条低能量规则；决策参照快照与唤醒判定
    传入同一身体配置，保证分级同源（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动「饥饿在低能量时不失灵」）。
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
    """@brief 按出行方式的代谢当量给路线费力标签（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动「商品热量与路线费力」）。"""
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


def build_observation(inputs: ProjectionInputs) -> dict[str, Any]:
    """@brief 组装一次观察的证据段与交互对象的私有绑定（七层由各层构建器另行生成）。

    返回字典进入 observation 事件 data：body 两组证据、余额、活动控制行与待收尾行、interaction_bindings（动作说明
    note 尚未填写）以及可选的内部动机仪表。本函数只做投影组装，不做 IO 与模型调用。
    """
    state, agent, scenario = inputs.state, inputs.agent, inputs.scenario
    hour = hour_of_day(state.current_time, scenario.timezone)
    activity_rows = inputs.extras["activity_rows"]
    data: dict[str, Any] = {
        "agent_id": agent.agent_id, "current_time": state.current_time,
        "interaction_bindings": interaction_bindings(inputs),
        "body": body_projection(agent, hour, scenario.body, inputs.meal_windows),
        "balance_fen": state.accounts[agent.balance_account_id],
        "active_activities": activity_rows["control"],
        "pending_conclusions": activity_rows["pending"],
    }
    return data


def _location_index(scenario: RuntimeScenario) -> dict[str, Any]:
    """@brief 地点身份索引。"""
    return {item.location_id: item for item in scenario.locations}


def _contact_rows(scenario: RuntimeScenario, agent: AgentState) -> list[dict[str, Any]]:
    """@brief 人物通讯录只公开称谓与关系，内部身份留在观察绑定中（零点计划输入）。"""
    config = next((item for item in scenario.agents if item.agent_id == agent.agent_id), None)
    contacts = getattr(config, "contacts", ()) or ()
    return [{"salutation": item.salutation, "relation": item.relation} for item in contacts]


def _public_inbox(inputs: ProjectionInputs, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """@brief 将收件人可见消息转成标题、发件人和时间，不公开消息身份（零点计划输入）。"""
    people = {f"agent:{item.agent_id}": item.name for item in inputs.state.agents.values()}
    config = next(item for item in inputs.scenario.agents if item.agent_id == inputs.agent.agent_id)
    people.update({item.contact_id: item.salutation for item in config.contacts})
    return [{"kind": row["kind"], "sender": people[row["sender_id"]]
             if row["sender_id"] is not None else None,
             "head": row["head"], "read": row["read"], "created_at": row["created_at"]}
            for row in rows]


def _inbox_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 收件箱行（零点计划输入）：场景邮件头 + 社交消息（直发与交谈邀请）。"""
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


def _product_names(scenario: RuntimeScenario) -> dict[str, str]:
    """@brief 实际商品身份与登记显示名称。"""
    return {goods.goods_id: goods.name for goods in scenario.goods}




def interaction_bindings(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 将本轮可见对象冻结为可读选择及仅供引擎使用的身份映射（动作 note 由工具层按可用性填写）。

    不按人物状态剪裁：可见的全部对象与动作都列出，此刻不能做的由工具层标 available=false 并写原因；永久不能满足的
    （没有路线、技能不够、没有购买规则或资格）不列（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「工具层」）。名称须唯一。
    """
    bindings = [_interaction_binding(inputs, row) for row in _interactive_objects_rows(inputs)]
    labels = [row["object"] for row in bindings]
    if len(labels) != len(set(labels)):
        repeated = sorted({label for label in labels if labels.count(label) > 1})
        logging.error("Interactive object labels are ambiguous: %s", repeated)
        raise ValueError("interactive object labels must be unique")
    return bindings


def _interaction_binding(inputs: ProjectionInputs, row: dict[str, Any]) -> dict[str, Any]:
    """@brief 从真实菜单形成可读动作绑定。"""
    identity, kind = row["object_id"], row["kind"]
    menu = inputs.extras.get("interaction_menu")
    actions = [action for action in _interaction_actions(inputs, row)
               if kind == "app" or menu is None or action["canonical_action"] in menu.allowed_actions]
    result = {"object": _interaction_object_label(inputs, row), "kind": kind, "object_id": identity,
              "actions": actions}
    if kind == "supply":
        result.update(store_id=row["store_id"], goods_id=row["goods_id"], app_id=row["app_id"])
    if row.get("location_id") is not None:
        result["location_id"] = row["location_id"]
    return result


def _interaction_object_label(inputs: ProjectionInputs, row: dict[str, Any]) -> str:
    """@brief 商品店铺名称带实际登记地点，名称与指名购买一致。"""
    kind, name = row["kind"], row["name"]
    if kind == "supply":
        return supply_label(row["location_name"], name)
    if kind == "self":
        return SELF_LABEL
    return object_label(kind, name)


def _interaction_actions(inputs: ProjectionInputs, row: dict[str, Any]) -> list[dict[str, Any]]:
    """@brief 地点列图上方式，商品只列真实购买。"""
    if row["kind"] == "location":
        return [binding_action(TRAVEL_WAYS[route["mode"]], "travel_to", travel_mode=route["mode"])
                for route in inputs.extras.get("route_rows", []) if route["destination"] == row["object_id"]]
    if row["kind"] == "supply":
        return _supply_actions(inputs, row)
    return [binding_action(ACTION_WAYS[action], action) for action in row["actions"]]




def _supply_actions(inputs: ProjectionInputs, row: dict[str, Any]) -> list[dict[str, Any]]:
    """@brief 每个真实店铺都可到店，绑定 app 才列远程下单。"""
    modes = ("pickup", "delivery") if row["app_id"] is not None else ("pickup",)
    return [binding_action(PURCHASE_WAYS[mode], "purchase", fulfillment=mode) for mode in modes]


def _interactive_objects_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 交互对象层：本轮可见对象及其内部动作。

    动作按对象绑定（docs/dev/SPEC-agent-interaction.md#spec-agent-interaction-特性规格数字人交互会话层上下文分层与稳定工具面「对象与动作选择」）
    ：每个对象只列出对它自己合法的动作。
    「自己」（等待、睡觉）恒在列表中。
    """
    rows = _task_object_rows(inputs)
    rows.extend(_place_object_rows(inputs))
    rows.extend(_supply_object_rows(inputs))
    rows.extend(_app_object_rows(inputs))
    rows.extend(_lifestyle_object_rows(inputs))
    rows.extend(_person_object_rows(inputs))
    rows.extend(_mail_object_rows(inputs))
    rows.append(_self_object_row(inputs))
    return rows


def _task_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 工作任务对象：开工动作照常列出；过了截止仍列出的任务不列开工动作（永久不能满足）。技能不够的任务
    不在 task_rows 中（观察侧已去掉，不列目标）。"""
    return [{"object_id": row["topic_id"], "kind": "work_topic", "name": row["topic"],
             "actions": ["work"]} for row in inputs.task_rows]


def _place_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 可达地点对象：只接受前往动作。"""
    return [{"object_id": place["location_id"], "kind": "location", "name": place["name"],
             "actions": ["travel_to"]} for place in inputs.extras.get("reachable_places", [])]


def _supply_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 本轮实际固定菜单商品，各店商品身份独立绑定。"""
    return [{"object_id": f"{row['store_id']}:{row['goods_id']}", "kind": "supply", "name": row["name"],
             "store_id": row["store_id"], "goods_id": row["goods_id"], "location_name": row["location_name"],
             "app_id": min(row["app_ids"]) if row["app_ids"] else None, "actions": ["purchase"]}
            for row in inputs.extras.get("product_rows", [])]












def _lifestyle_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 本轮呈现的生活活动目录条目：只接受进行动作（地点环境是否合适由工具层写明）。"""
    return [{"object_id": row["activity_id"], "kind": "lifestyle", "name": row["name"], "actions": ["do"]}
            for row in inputs.extras.get("lifestyle_rows", [])]


def _person_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 同场人物只列交谈；通话只对通讯录联系人开放。"""
    rows = [{"object_id": view["person_id"], "kind": "person", "name": view["name"], "actions": ["chat"]}
            for view in people_here_views(inputs.state, inputs.agent)]
    config = next(item for item in inputs.scenario.agents if item.agent_id == inputs.agent.agent_id)
    rows.extend({"object_id": item.contact_id, "kind": "contact", "name": item.salutation, "actions": ["call"]}
                for item in config.contacts)
    return rows


def _mail_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 场景邮件对象：用标题和到达时间区分同主题邮件；人物消息与交谈邀请不是阅读对象（全文在进度层）。"""
    messages = inputs.extras.get("scenario_messages", {})
    rows = []
    for message_id in sorted(inputs.agent.visible_message_ids):
        config = messages.get(message_id)
        if config is None:
            continue
        sent = int(config.sent_at.timestamp())
        rows.append((sent, message_id, {
            "object_id": message_id, "kind": "mail", "actions": ["read"],
            "name": mail_name(config.subject, sent, inputs.scenario.timezone)}))
    return [row for _, _, row in sorted(rows, key=lambda item: (item[0], item[1]))]


def _self_object_row(inputs: ProjectionInputs) -> dict[str, Any]:
    """@brief 「自身」对象：等待与睡眠两个受保护动作恒可见。"""
    return {"object_id": f"agent:{inputs.agent.agent_id}", "kind": "self", "name": inputs.agent.name,
            "actions": ["wait", "sleep"]}


def _app_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 已登记应用始终列出，只提供打开动作，内容由应用使用规则选择。"""
    return [{"object_id": app.app_id, "kind": "app", "name": app.name, "actions": ["use_app"]}
            for app in sorted(inputs.scenario.apps, key=lambda item: item.app_id)]

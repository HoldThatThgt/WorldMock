"""@brief 观察投影引擎侧组装：身体两组证据、在场人物与本轮交互对象的私有绑定（docs/dev/SPEC-persona-prompt.md 第一章）。

发给人物模型的七层 JSON 由各层构建器生成（life.tool_layer、todo_layer、environment_layer、progress_layer）；本模块只从
世界状态计算观察的证据段（身体数值与分级、余额、活动控制行）与交互对象的私有绑定：每个本轮可见对象的可读名称、内部身份
与动词和方式映射。绑定动作的说明（note）由工具层按可用性单一来源填写（「工具层」）。身体数值只进事件供评估，分级进环境层
（docs/dev/SPEC-life-runtime.md「数值与分级的分离」）。

供给可见期：仅实际呈现的供给具有购买凭据，位置未变且呈现后未超 1800 秒；供给身份与商品身份分别保留。
零点计划输入（life.day_plan_inputs）另取本模块的联系人、收件箱与已核验经历句子。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from worldmock.life import admission, felt, social
from worldmock.life.content import restarts_from_beginning
from worldmock.life.interaction_menu import (ACTION_WAYS, CONTENT_WAYS, GIVE_WAY, PURCHASE_WAYS, TRAVEL_WAYS,
                                             USE_WAYS, binding_action)
from worldmock.life.models import ActivityKind, AgentState, TravelMode, WorldState, active_activities
from worldmock.life.readable import duration_phrase
from worldmock.life.scenario import BodyConfig, RuntimeScenario
from worldmock.life.sleep import SLEEPINESS_CLIP_MAX, sleepiness

#: 供给浏览可见期（秒）：距浏览超过该值清空（docs/dev/SPEC-life-runtime.md「观察投影行为规则」）。
SUPPLY_VISIBILITY_TTL_SECONDS = 1800
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
    #: 可见未结清且未过截止的任务行（engine._visible_task_rows）。
    task_rows: tuple[dict[str, Any], ...] = ()
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
    """@brief 场景时区本地时刻。"""
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
    """@brief body 两组投影：levels 分级（进环境层）与 values 数值（只进事件）。

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
    if inputs.motivation_internal is not None:
        data["motivation_internal"] = inputs.motivation_internal
    return data


def _experience_sentence(inputs: ProjectionInputs, fact: dict[str, Any]) -> str:
    """@brief 一条已核验经历的确定性句子（零点计划输入）：等待、食用、播放带起止时刻，到达带到达时刻与方式。

    例：「14:17 开车到了张江办公室」「13:57–14:17 吃了番茄炒蛋」；地点、商品与内容取场景名称，出行方式取自所属出行活动。
    """
    zone = inputs.scenario.timezone
    if fact["kind"] == "arrived":
        travel = inputs.state.activities[fact["activity_id"]]
        mode = "走路" if travel.travel_mode == TravelMode.WALKING else "开车"
        place = _location_index(inputs.scenario)[fact["location_id"]].name
        denied = "，但没能进去" if fact["access_status"] == "denied" else ""
        return f"{_clock(fact['occurred_at'], zone)} {mode}到了{place}{denied}"
    span = f"{_clock(fact['interval_start'], zone)}–{_clock(fact['interval_end'], zone)}"
    if fact["kind"] == "waited":
        return f"{span} 等待了{duration_phrase(fact['duration_seconds'])}"
    if fact["kind"] == "consumed":
        return f"{span} 吃了{_product_names(inputs.scenario)[fact['product_id']]}"
    podcast = any(item.item_id == fact["content_id"] and item.kind == "podcast_episode"
                  for item in inputs.scenario.contents)
    title = inputs.extras["content_titles"][fact["content_id"]]
    return f"{span} {'听了' if podcast else '看了'}《{title}》"


def _clock(epoch: int, timezone_name: str) -> str:
    """@brief 场景时区的本地时刻 HH:MM。"""
    return f"{local_datetime(epoch, timezone_name):%H:%M}"


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
    """@brief 商品身份到目录展示名的索引（库存条目与商品目录合并）。"""
    return {item.product_id: item.name
            for item in (*scenario.products, *scenario.inventory_items)}


def holding_ids(state: WorldState, agent: AgentState) -> set[str]:
    """@brief 手边持有量为正的商品身份集合：只取现场地点，途中（有进行中出行）为空。"""
    present = admission.present_location(state, agent.agent_id)
    holding = agent.inventory.get(present, {}) if present is not None else {}
    return {product_id for product_id, quantity in holding.items() if quantity > 0}


def interaction_bindings(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 将本轮可见对象冻结为可读选择及仅供引擎使用的身份映射（动作 note 由工具层按可用性填写）。

    不按人物状态剪裁：可见的全部对象与动作都列出，此刻不能做的由工具层标 available=false 并写原因；永久不能满足的
    （没有路线、技能不够、没有购买规则或资格）不列（docs/dev/SPEC-persona-prompt.md「工具层」）。名称须唯一。
    """
    bindings = [_interaction_binding(inputs, row) for row in _interactive_objects_rows(inputs)]
    labels = [row["object"] for row in bindings]
    if len(labels) != len(set(labels)):
        repeated = sorted({label for label in labels if labels.count(label) > 1})
        logging.error("Interactive object labels are ambiguous: %s", repeated)
        raise ValueError("interactive object labels must be unique")
    return bindings


def _interaction_binding(inputs: ProjectionInputs, row: dict[str, Any]) -> dict[str, Any]:
    """@brief 将一个内部对象身份和本轮动作绑定到可读名称；供给另带冻结报价，他处持有物另带所在地点。"""
    identity, kind = row["object_id"], row["kind"]
    menu = inputs.extras.get("interaction_menu")
    actions = [action for action in _interaction_actions(inputs, row)
               if menu is None or action["canonical_action"] in menu.allowed_actions]
    result = {"object": _interaction_object_label(inputs, row), "kind": kind, "object_id": identity,
              "actions": actions}
    if kind == "supply":
        supply = next(item for item in inputs.extras["product_rows"] if item["supply_id"] == identity)
        result["quote"] = {"price_version": supply.get("price_version"),
                           "price_fen": supply.get("price_fen"),
                           "purchase_price_fen": supply.get("purchase_price_fen")}
    if row.get("location_id") is not None:
        result["location_id"] = row["location_id"]
    return result


def _interaction_object_label(inputs: ProjectionInputs, row: dict[str, Any]) -> str:
    """@brief 用人能区分的类别、名称和场所表达对象，不输出内部编号；他处持有物的名称带地点。"""
    kind, name = row["kind"], row["name"]
    if kind == "supply":
        supply = next(item for item in inputs.extras["product_rows"]
                      if item["supply_id"] == row["object_id"])
        return f"{supply['location_name']}出售的{name}"
    if kind == "catalog":
        return "商品目录"
    if kind == "self":
        return "自己"
    if kind == "holding" and row.get("location_id") is not None:
        return f"持有物：{name}（{_location_index(inputs.scenario)[row['location_id']].name}）"
    prefixes = {"work_task": "工作", "location": "地点", "holding": "持有物",
                "content": "内容", "game": "游戏", "lifestyle": "活动",
                "person": "人物", "contact": "联系人", "mail": "邮件"}
    return f"{prefixes[kind]}：{name}"


def _interaction_actions(inputs: ProjectionInputs, row: dict[str, Any]) -> list[dict[str, Any]]:
    """@brief 将内部动作映射为动词与方式：路线按目的地冻结可用方式，供给按履约方式，持有物按使用方式与配方，另列给予。

    按世界事实过滤（不可达或未核实的路线、没有购买规则或资格的供给）的动作不列出；其余照常列出。
    """
    if row["kind"] == "location":
        return [binding_action(TRAVEL_WAYS[route["mode"]], "travel_to", travel_mode=route["mode"])
                for route in inputs.extras.get("route_rows", [])
                if route["destination"] == row["object_id"] and route["status"] == "reachable"]
    if row["kind"] == "holding":
        product = next(item for item in (*inputs.scenario.products, *inputs.scenario.inventory_items)
                       if item.product_id == row["object_id"])
        recipes = inputs.extras.get("recipe_product_ids", frozenset())
        return [*(binding_action(_use_way(mode, product.product_id in recipes), "consume", use_mode=mode)
                  for mode in product.use_modes), binding_action(GIVE_WAY, "consume")]
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
    """@brief 供给的取得方式：支持到店就列「到店」，支持配送就列「远程下单」。

    没有购买规则（品类配对未定义）、人物缺少供给或地点资格时永久不能买，不列；营业、报价、库存、在不在店里与能否
    送到这里都可能在运行中改变，照常列出，由工具层写明不可用的原因（docs/dev/SPEC-persona-prompt.md「工具层」）。
    """
    supply = next(item for item in inputs.extras["product_rows"] if item["supply_id"] == row["object_id"])
    if (supply["purchase_pairing_state"] != "defined" or not supply["service_allowed"]
            or not supply["access_allowed"]):
        return []
    return [binding_action(PURCHASE_WAYS[mode], "purchase", fulfillment=mode)
            for mode in ("pickup", "delivery") if mode in supply["fulfillment_modes"]]


def _interactive_objects_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 交互对象层：本轮可见对象及其内部动作。

    动作按对象绑定（docs/dev/SPEC-agent-interaction.md「对象与动作选择」）：每个对象只列出对它自己合法的动作。
    「自己」（等待、睡觉）恒在列表中。
    """
    rows = _task_object_rows(inputs)
    rows.extend(_place_object_rows(inputs))
    rows.extend(_supply_object_rows(inputs))
    rows.extend(_holding_object_rows(inputs))
    rows.extend(_catalog_object_rows(inputs))
    rows.extend(_content_object_rows(inputs))
    rows.extend(_game_object_rows(inputs))
    rows.extend(_lifestyle_object_rows(inputs))
    rows.extend(_person_object_rows(inputs))
    rows.extend(_mail_object_rows(inputs))
    rows.append(_self_object_row(inputs))
    return rows


def _task_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 工作任务对象：开工动作照常列出；技能不够的任务不列开工动作（技能是人物不变的事实）。"""
    agent = inputs.agent
    return [{"object_id": row["task_id"], "kind": "work_task", "name": row["title"],
             "actions": ["work"] if agent.skill_levels.get(row.get("required_skill"), 0)
             >= (row.get("minimum_skill") or 0.0) else []}
            for row in inputs.task_rows]


def _place_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 可达地点对象：只接受前往动作。"""
    return [{"object_id": place["location_id"], "kind": "location", "name": place["name"],
             "actions": ["travel_to"]} for place in inputs.extras.get("reachable_places", [])]


def _supply_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 本轮呈现的供给对象：只接受取得动作。"""
    names = _product_names(inputs.scenario)
    return [{"object_id": row["supply_id"], "kind": "supply", "name": names[row.get("product_id", "")],
             "actions": ["purchase"]} for row in inputs.extras.get("product_rows", [])]


def _holding_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 持有的商品对象：手边的按目录支持食用、使用或制作；他处（含途中的全部）持有物名称带地点，另记所在地点。"""
    names = _product_names(inputs.scenario)
    present = admission.present_location(inputs.state, inputs.agent.agent_id)
    rows = [{"object_id": product_id, "kind": "holding", "name": names[product_id], "actions": ["consume"]}
            for product_id in sorted(holding_ids(inputs.state, inputs.agent))]
    for place_id, holding in sorted(inputs.agent.inventory.items()):
        if place_id == present:
            continue
        rows.extend({"object_id": product_id, "kind": "holding", "name": names[product_id], "actions": ["consume"],
                     "location_id": place_id}
                    for product_id, quantity in sorted(holding.items()) if quantity > 0)
    return rows


def _catalog_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 目录对象：只接受浏览动作，身份沿用既有 browse_targets 闭集。"""
    return [{"object_id": target, "kind": "catalog", "name": target, "actions": ["browse"]}
            for target in inputs.extras.get("browse_targets", [])]


def _content_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 本轮呈现的内容对象：方式为「接着看」或「从头再看」（播客为「接着听」或「从头再听」），都映射为观看。"""
    contents = {item.item_id: item for item in inputs.scenario.contents}
    return [{**_watch_row(inputs, contents[row["item_id"]]), "kind": "content", "name": row["title"]}
            for row in inputs.extras.get("content_rows", [])]


def _watch_row(inputs: ProjectionInputs, item: Any) -> dict[str, Any]:
    """@brief 一条可观看内容的动作：已看完或剩余不足 60 秒的从头派生（「从头再看」），否则「接着看」（第八章「呈现」）。"""
    progress = inputs.agent.content_progress.get(item.item_id)
    position = progress.position_seconds if progress else 0
    restart = restarts_from_beginning(position, item.duration_seconds)
    return {"object_id": item.item_id, "actions": ["watch"],
            "way": CONTENT_WAYS[(item.kind == "podcast_episode", restart)]}


def _game_object_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
    """@brief 本轮呈现的游戏对象：可玩的游戏只接受游玩；内容侧的游戏会话只能观看，方式同内容对象。"""
    contents = {item.item_id: item for item in inputs.scenario.contents}
    rows = []
    for row in inputs.extras.get("game_rows", []):
        if "game_id" in row:
            rows.append({"object_id": row["game_id"], "kind": "game", "name": row["title"], "actions": ["play"]})
        else:
            rows.append({**_watch_row(inputs, contents[row["item_id"]]), "kind": "game", "name": row["title"]})
    return rows


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
    zone = ZoneInfo(inputs.scenario.timezone)
    messages = inputs.extras.get("scenario_messages", {})
    rows = []
    for message_id in sorted(inputs.agent.visible_message_ids):
        config = messages.get(message_id)
        if config is None:
            continue
        sent = int(config.sent_at.timestamp())
        rows.append((sent, message_id, {
            "object_id": message_id, "kind": "mail", "actions": ["read"],
            "name": f"{config.subject}（{datetime.fromtimestamp(sent, zone):%Y年%m月%d日 %H:%M:%S}）"}))
    return [row for _, _, row in sorted(rows, key=lambda item: (item[0], item[1]))]


def _self_object_row(inputs: ProjectionInputs) -> dict[str, Any]:
    """@brief 「自身」对象：等待与睡眠两个受保护动作恒可见。"""
    return {"object_id": f"agent:{inputs.agent.agent_id}", "kind": "self", "name": inputs.agent.name,
            "actions": ["wait", "sleep"]}

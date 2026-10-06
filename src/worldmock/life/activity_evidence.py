"""@brief 活动结算后的观察证据与人物投影辅助函数，以及收尾时进食的丢弃与收尾结果的终局。"""

from __future__ import annotations

from collections.abc import Mapping

from worldmock.catalog.models import Food
from worldmock.exchange.operators import evaluate_food_energy
from worldmock.life.sleep import food_utilization_efficiency
from worldmock.life.models import ActivityKind, ActivityState, ActivityStatus, AgentState, ConsumeRequest
from worldmock.life.readable import portion_phrase

#: 收尾结果终局的说法（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第五章「进食」）：自然吃完、主动提前结束、失败。
OUTCOME_COMPLETED, OUTCOME_ENDED_EARLY, OUTCOME_FAILED = "完成", "提前结束", "失败"


def is_eating(activity: ActivityState) -> bool:
    """@brief 活动是否是本人进食（使用方式为食用的消费活动）。"""
    return activity.kind == ActivityKind.CONSUME and activity.data.get("request", {}).get("use_mode") == "eat"


def discard_opened(activity: ActivityState) -> ActivityState:
    """@brief 收尾时丢弃进食开吃份量中没吃的部分（opened 减已吃），写入活动数据 discarded；不补算摄入。

    自然吃完丢弃为 0；非进食活动原样返回。实验截止截断不经收尾，因此不丢弃（第五章「进食」）。
    """
    if not is_eating(activity):
        return activity
    eaten = activity.data.get("consumed", {})
    discarded = {food_id: round(max(0.0, opened - eaten.get(food_id, 0.0)), 9)
                 for food_id, opened in activity.data["opened"].items()}
    return activity.model_copy(update={"data": {**activity.data, "discarded": discarded}}, deep=True)


def conclusion_outcome(activity: ActivityState, name: str, food_names: dict[str, str],
                       agent: AgentState) -> dict:
    """@brief 收尾结果 details.outcome 的一项：进食逐件写终局与已吃、丢弃、剩余份量。

    终局只取结算状态（完成、提前结束、失败），人物自述不改变它；剩余是该食品在进食地点仍持有的份数。
    :param name: 本次收尾点名该活动所用的可读名称。
    """
    ending = {ActivityStatus.COMPLETED: OUTCOME_COMPLETED, ActivityStatus.FAILED: OUTCOME_FAILED}.get(
        activity.status, OUTCOME_ENDED_EARLY)
    if not is_eating(activity):
        return {"activity": name, "outcome": ending, "items": [], "note": "No item changes."}
    eaten, discarded = activity.data.get("consumed", {}), activity.data.get("discarded", {})
    held = agent.inventory.get(activity.location_id, {})
    items = [{"product": food_names[food_id], "eaten": portion_phrase(eaten.get(food_id, 0.0)),
              "discarded": portion_phrase(discarded.get(food_id, 0.0)),
              "remaining": portion_phrase(held.get(food_id, 0.0))}
             for food_id in activity.data["opened"]]
    return {"activity": name, "outcome": ending, "items": items, "note": None}


def meal_consumption_data(activity: ActivityState, after_agent: AgentState,
                          before_consumed: dict[str, float], foods: Mapping[str, Food]) -> dict:
    """@brief 以实际食品目录与已结算份量输出本区间、累计份额及原始摄入。"""
    request = ConsumeRequest.model_validate(activity.data["request"])
    consumed = dict(activity.data.get("consumed", {}))
    rows = [
        {
            "food_id": item.product_id,
            "category": foods[item.product_id].category.value,
            "interval_portion": round(max(0.0, consumed.get(item.product_id, 0.0)
                                           - before_consumed.get(item.product_id, 0.0)), 9),
            "cumulative_portion": consumed.get(item.product_id, 0.0),
        }
        for item in request.items
    ]
    for row in rows:
        row["interval_kcal"] = evaluate_food_energy(row["category"], row["interval_portion"])
    return {"location_id": activity.location_id, "items": rows, "use_mode": request.use_mode,
            "food_efficiency": food_utilization_efficiency(after_agent.body.sleep_deficit_hours)}


def dedupe_revealed_cues(activity) -> None:
    """@brief 旧媒体只保留在本区间首次出现的字幕段。"""
    revealed = activity.data.get("revealed")
    if not isinstance(revealed, dict):
        return
    start = revealed.get("position_before_seconds")
    cues = revealed.get("cues")
    if not isinstance(start, (int, float)) or not isinstance(cues, list):
        return
    revealed["cues"] = [cue for cue in cues
                        if isinstance(cue, dict) and cue.get("start_seconds", start - 1) >= start]


def record_material_reveal(agent, task_id, material, occurred_at):
    """@brief 登记工作材料本区间新读取的字符范围。"""
    if not task_id:
        return agent
    entry = {
        "task_id": task_id,
        "at": occurred_at,
        "start_char": material.get("start_char", 0),
        "end_char": material.get("end_char", 0),
        "chars": max(0, int(material.get("end_char", 0)) - int(material.get("start_char", 0))),
    }
    reveals = list(agent.material_reveals)[-63:] + [entry]
    return agent.model_copy(update={"material_reveals": reveals}, deep=True)

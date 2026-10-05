"""@brief 活动结算后的观察证据与人物投影辅助函数，以及收尾时进食的丢弃与收尾结果的终局。"""

from __future__ import annotations

from worldmock.life.basket import held_quantity
from worldmock.life.models import ActivityKind, ActivityState, ActivityStatus, AgentState, ConsumeRequest
from worldmock.life.readable import portion_phrase

#: 收尾结果终局的说法（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第五章「进食」）：自然吃完、主动提前结束、失败。
OUTCOME_COMPLETED, OUTCOME_ENDED_EARLY, OUTCOME_FAILED = "完成", "提前结束", "失败"
#: 未完成的浏览在收尾结果中的说明（第六章「未完成浏览被写成采购完成」）。
BROWSE_UNFINISHED_NOTE = "Browsing did not finish. No item changes."


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
    discarded = {product_id: round(max(0.0, opened - eaten.get(product_id, 0.0)), 9)
                 for product_id, opened in activity.data["opened"].items()}
    return activity.model_copy(update={"data": {**activity.data, "discarded": discarded}}, deep=True)


def conclusion_outcome(activity: ActivityState, name: str, product_names: dict[str, str],
                       agent: AgentState) -> dict:
    """@brief 收尾结果 details.outcome 的一项：进食逐件写终局与已吃、丢弃、剩余份量，未完成的浏览写未取得浏览结果。

    终局只取结算状态（完成、提前结束、失败），人物在 summary 里写的话不改变它；剩余是该商品在进食地点仍持有的份数。
    制作只投影结算确认的真实输入与产物；没有库存变化时明确说明。

    :param name: 本次收尾点名该活动所用的可读名称。
    """
    ending = {ActivityStatus.COMPLETED: OUTCOME_COMPLETED, ActivityStatus.FAILED: OUTCOME_FAILED}.get(
        activity.status, OUTCOME_ENDED_EARLY)
    if activity.kind == ActivityKind.BROWSE and activity.status != ActivityStatus.COMPLETED:
        return {"activity": name, "outcome": ending, "items": [], "note": BROWSE_UNFINISHED_NOTE}
    if not is_eating(activity):
        return _item_change_outcome(activity, name, product_names, agent, ending)
    eaten, discarded = activity.data.get("consumed", {}), activity.data.get("discarded", {})
    held = agent.inventory.get(activity.location_id, {})
    items = [{"product": product_names[product_id], "eaten": portion_phrase(eaten.get(product_id, 0.0)),
              "discarded": portion_phrase(discarded.get(product_id, 0.0)),
              "remaining": portion_phrase(held.get(product_id, 0.0))}
             for product_id in activity.data["opened"]]
    return {"activity": name, "outcome": ending, "items": items, "note": None}


def _item_change_outcome(activity: ActivityState, name: str, names: dict[str, str],
                         agent: AgentState, ending: str) -> dict:
    """@brief 库存转换完成才声称产出，人物自述与未完成的制作不能创造物品。"""
    recipe = activity.data.get("inventory_recipe")
    if recipe is None or not activity.data.get("recipe_applied"):
        items = [{"product": names[identity], "consumed": portion_phrase(quantity), "produced": "0 份",
                  "remaining": portion_phrase(agent.inventory.get(activity.location_id, {}).get(identity, 0))}
                 for identity, quantity in activity.data.get("consumed", {}).items() if quantity > 0]
        return {"activity": name, "outcome": ending, "items": items, "note": None if items else "No item changes."}
    held = agent.inventory.get(activity.location_id, {})
    items = [{"product": names[recipe[f"{side}_object_id"]],
              "consumed": portion_phrase(float(recipe["input_quantity"])) if side == "input" else "0 份",
              "produced": portion_phrase(float(recipe["output_quantity"])) if side == "output" else "0 份",
              "remaining": portion_phrase(held.get(recipe[f"{side}_object_id"], 0))}
             for side in ("input", "output")]
    return {"activity": name, "outcome": ending, "items": items, "note": None}


def meal_consumption_data(activity, after_agent, before_consumed: dict[str, float]) -> dict:
    """@brief 区分本区间实际使用增量与累计使用量。"""
    request = ConsumeRequest.model_validate(activity.data["request"])
    consumed = dict(activity.data.get("consumed", {}))
    rows = [
        {
            "product_id": item.product_id,
            "interval_quantity": max(0.0, consumed.get(item.product_id, 0.0)
                                     - before_consumed.get(item.product_id, 0.0)),
            "cumulative_quantity": consumed.get(item.product_id, 0.0),
            "held_after": held_quantity(after_agent, item.product_id),
        }
        for item in request.items
    ]
    data = {"location_id": after_agent.location_id, "items": rows,
            "use_mode": request.use_mode}
    recipe = activity.data.get("inventory_recipe")
    if recipe is not None and activity.data.get("recipe_applied"):
        data["inventory_transform"] = dict(recipe)
    return data


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

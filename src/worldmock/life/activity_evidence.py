"""@brief 活动结算后的观察证据与人物投影辅助函数。"""

from __future__ import annotations

from worldmock.life.basket import held_quantity
from worldmock.life.models import ConsumeRequest


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

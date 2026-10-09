"""@brief 七层请求的宿主侧：生活与只读系统层、原生工具用途说明、六层 user 组装与提示指纹。

docs/design/06-agent.md 「请求组成」「系统层」：system 为系统层 JSON（整次运行逐字节不变），
user 为工具层、人设层、记忆层、待办层、环境层、进度层六个对象组成的 JSON 数组；工具定义只经原生 tools，
description 只写用途，世界交互规则只在系统层 rules 写一次。只读问答的 user 为人设层与记忆层，外部问题
另起一条 user 消息原样给出。
"""

from __future__ import annotations
from worldmock.life.prompt_schemas import LAYER_SCHEMAS

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from worldmock.life.narration import render_layer_json

#: 生活决策的系统层（规格第一章「系统层」定稿文案，逐字）。
LIFE_SYSTEM_LAYER: dict[str, Any] = {
    "layer": "系统层",
    "role": "你是模拟世界中的一个人物，依据人设层选择下一步行动；世界的时间、状态与结算由环境负责。",
    "rules": [
        "工具层写此刻的对象、动作、条件与代价；人设层写你的作息与习惯；记忆层写此前经历；待办层写暂停活动、个人事务和今日计划；"
        "环境层写身体感觉、位置、天气和工作安排；进度层写这段停留或路程中已经发生的事。",
        "天气只以环境层给出的事实为准，不用记忆、常识或外部服务猜测天气。",
        "每次决策单独提交一个世界请求。提交前可以查询记忆、回看任务或查询世界。只读查询可以同批，世界请求提交即结束本次决策。",
        "start_activity 每项只填 action 和 target，action 只能是 walk、drive、take_taxi、take_bus、purchase、sleep、interact；"
        "target 使用完整对象名称。地点、商品和内容 app 可以直接按名称调用，即使此刻没有展示。写得不准会返回候选，不会替你更改。",
        "出行以地点为目标；sleep 以本人家为目标且须在家；purchase 以商品为目标；interact 以当前地点或内容 app 为目标。"
        "内容由环境选择，标题不是动作目标，功能 app 不由你选择。",
        "同时最多两项活动。出行与睡眠使用同一个名额，购买与交互使用另一个名额；睡眠要求没有其他活动。"
        "进行中和刚结束待收尾的活动都占名额，暂停释放名额。多个商品项共用一个购买过程。睡眠期间只有睡够了才会请你决策。",
        "出发前先暂停或结束必须在原地的活动；app 互动和快递购买可以随出行继续。地点活动到达别处前不能继续。",
        "活动自然结束后先用 conclude_activity 收尾；report_status 写进展；pause_activity 暂停交互；"
        "conclude_activity 结束活动并写总结、想记住的新情况与看法。report_status 点名暂停活动时恢复它；"
        "恢复须满足实际开始地点和空闲名额条件，读取最新共享进度；暂停活动在别处也可以结束。",
        "世界请求登记后由环境结算。进度层结果和记忆层 settled 是核验事实；自己写的 summary、self_note、self_notes 与看法"
        "只是自己的想法，不能改变身体、资金、内容进度或订单结果。",
        "商品实际收到时自动吃掉或用掉，不保存库存。到店商品在购买完成付款时收到，外卖等实际送达，快递先到驿站；"
        "本人到站 interact 后才领取。购买过程结束时统一付款，付不起时整次取消。",
        "作息与习惯照人设层过，当天特别安排照今日计划；没有相应习惯时按身体感觉安排。大致钟点只是大概，不会自动提醒。"
        "工作安排由环境给出，今日计划不能改变班次。",
        "工具层 available 为 false 的动作仍可提交，会按 reason 拒绝；先照原因处理。查询只读，不推进时间。",
        "个人事务在三个活动控制工具的 errands 中维护，提醒到点只提醒一次且不会叫醒睡眠。办成由真实结算确认，自己说办完不算。",
        "正文可以留空；写了只作为本次调用的自述留到这段停留或路程结束。思考只在同一时刻连续决策之间保留，时间推进后丢弃。",
    ],
}

#: 只读问答的系统层（规格第一章「系统层」末段）。
READONLY_SYSTEM_LAYER: dict[str, Any] = {
    "layer": "系统层",
    "role": "你是模拟世界中的一个人物，依据人设层与记忆层回答外部问题。",
    "rules": [
        "请求由人设层、记忆层与外部问题组成。",
        "记忆层的 settled 是环境核验的事实，self_notes 与人设层的看法、偏好是你自己写下的，未经核验。",
        "只能查询自己的记忆，不能改变世界，也不写入记忆。",
        "回答完成后单独调用 finish。",
    ],
}

#: 原生工具的用途说明（只写用途；协议与阶段规则只在系统层）。
TOOL_DESCRIPTIONS: dict[str, str] = {
    "start_activity": "开始一项或两项活动：逐项填写具体动作和完整对象名称。",
    "query_world": "按对象名称、具体动作或商品品类查询世界，可只看此刻能做的，按返回的偏移量翻页。",
    "recall_task_summary": "按名称回看一项任务、目标或活动的进展，不改变世界。",
    "report_status": "按活动名写下一两项活动的进展或恢复暂停活动，并写下个人事务的变更。",
    "pause_activity": "按活动名暂停一两项进行中的活动并写下进展与个人事务的变更。",
    "conclude_activity": "按活动名结束一两项活动，写下总结、想记住的事、形成的看法与个人事务的变更。",
    "revise_day_plan": "用完整的新清单替换今日计划中的目标。",
    "memory_recent": "读取自己最近的记忆。",
    "memory_search": "按关键词检索自己的记忆。",
    "memory_time_range": "读取某一时间段内自己的记忆。",
    "finish": "给出外部问题的回答。",
}

#: 观察中由环境构建的五层所在的键（人设层由宿主从人物档案构建），与 user 数组的层序一一对应。
_OBSERVATION_LAYER_KEYS = (("tool_layer", "工具层"), ("memory_layer", "记忆层"), ("todo_layer", "待办层"),
                           ("environment_layer", "环境层"), ("progress_layer", "进度层"))


def system_message(readonly: bool) -> dict[str, str]:
    """@brief 生活决策或只读问答的 system 消息：对应系统层的紧凑 JSON。"""
    layer = READONLY_SYSTEM_LAYER if readonly else LIFE_SYSTEM_LAYER
    return {"role": "system", "content": render_layer_json(layer)}


def empty_memory_layer() -> dict[str, Any]:
    """@brief 两个列表都为空的记忆层（运行开始后尚无内容时的记忆层）。"""
    return {"layer": "记忆层", "earlier_days": [], "earlier_today": []}


def life_user_layers(observation: Mapping[str, Any], persona: dict[str, Any]) -> list[dict[str, Any]]:
    """@brief 按固定层序取六层：工具层、人设层、记忆层、待办层、环境层、进度层。

    五层取自环境观察的同名键，人设层由宿主构建；缺失或层名不符的观察是损坏的宿主观察。
    """
    layers = {}
    for key, name in _OBSERVATION_LAYER_KEYS:
        layer = observation.get(key)
        if not isinstance(layer, dict) or layer.get("layer") != name:
            logging.error("LifeAgent observation layer %s is unavailable", key)
            raise RuntimeError(f"LifeAgent observation layer {key} is unavailable")
        layers[name] = layer
    return [layers["工具层"], persona, layers["记忆层"], layers["待办层"], layers["环境层"], layers["进度层"]]


def user_message(layers: Sequence[dict[str, Any]]) -> dict[str, str]:
    """@brief 由层数组形成 user 消息：层数组的紧凑 JSON。"""
    return {"role": "user", "content": render_layer_json(list(layers))}


def readonly_messages(persona: dict[str, Any], memory: dict[str, Any], question: str | None) -> list[dict[str, str]]:
    """@brief 只读问答的消息：只读系统层、人设层与记忆层组成的 user，以及原样给出的外部问题。"""
    messages = [system_message(True), user_message([persona, memory])]
    if question is not None:
        messages.append({"role": "user", "content": question})
    return messages


def life_prompt_sha256() -> str:
    """@brief 提示指纹：绑定生活与只读两份系统层以及七层 Schema（prompt_schemas.LAYER_SCHEMAS）。"""

    templates = {"life": LIFE_SYSTEM_LAYER, "readonly": READONLY_SYSTEM_LAYER, "layer_schemas": LAYER_SCHEMAS}
    encoded = json.dumps(templates, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()

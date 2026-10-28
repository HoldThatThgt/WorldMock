"""@brief 七层请求的宿主侧：生活与只读系统层、原生工具用途说明、六层 user 组装与提示指纹。

docs/design/06-agent.md「提示与工具」：system 为系统层 JSON（整次运行逐字节不变），
user 为工具层、人设层、记忆层、待办层、环境层、进度层六个对象组成的 JSON 数组；工具定义只经原生 tools，
description 只写用途，世界交互规则只在系统层 rules 写一次。只读问答的 user 为人设层与记忆层，外部问题
另起一条 user 消息原样给出。
"""

from __future__ import annotations
from worldmock.agents.prompt.prompt_schemas import LAYER_SCHEMAS

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from worldmock.agents.prompt.narration import render_layer_json

#: 生活决策的系统层（逐字原文只在这里；话题见 docs/design/06-agent.md「提示与工具」）。
LIFE_SYSTEM_LAYER: dict[str, Any] = {
    "layer": "系统层",
    "role": "你是模拟世界中的一个人物，依据人设层选择下一步行动；世界的时间、状态与结算由环境负责。",
    "rules": [
        "工具层写此刻的对象、动作、条件与代价；人设层写你的作息与习惯；记忆层写此前经历；"
        "待办层写暂停活动、个人事务和今日计划；环境层写身体感觉、位置、天气和工作安排；"
        "进度层写这段停留或路程中已经发生的事。",
        "天气只以环境层给出的事实为准，不用记忆、常识或外部服务猜测天气。",
        "每次决策以世界请求结束：要么提交一个控制请求（report_status、pause_activity、conclude_activity、"
        "revise_day_plan 之一），要么开始活动。开始活动时 start_main_activity 与 start_side_activity 各至多调用一次；"
        "要同时开始两项，就在同一次回复里各调用一次，两项一起核验，任何一项不成立两项都不开始。"
        "提交前可以查询记忆或回看任务，只读查询可以同批；世界请求不与只读查询同批，开始活动也不与控制请求同批，"
        "提交即结束本次决策。没有进行中的活动时时间不会往前走，同一时刻会接着请你决策；"
        "等外卖或等某个钟点时，此刻能做的事都可以做，包括用工具层里可用的内容 app。"
        "醒着时，手上的事做完、下单的东西送到或到站可取、提醒到点、app 推送、身体感觉变了、"
        "到了上下班时刻或写明的休息时段开始或结束，都会再请你决策；一件事做得久，中途隔一阵也会请一次；"
        "没做完的活动照常进行。",
        "start_main_activity 开始占槽位 1 的活动，填 action、target、detail 三项：travel 是出行，"
        "target 写目的地地点名，detail 写出行方式名称，照工具层该目的地下列出的名称写；"
        "interact 是在当前地点做一件事，target 写当前地点名，detail 写这件事的名称，照工具层当前地点下列出的名称写；"
        "sleep 是睡觉，target 写要睡的分钟数（只写数字），detail 写 null。",
        "start_side_activity 开始占槽位 2 的活动，填 action、target、prefer 三项，target 始终是名称数组："
        "purchase 是购买，逐份写商品名，同一个名称写两次就是两份，全部商品共用一个购买过程，"
        "prefer 写想用来下单的 app；interact 是用内容 app，数组里只写一个内容 app 名称，prefer 写 null；"
        "query 是查询商品，数组里写商品名或商品类别名，prefer 写想在哪个 app 里查。"
        "prefer 照工具层 prefer_apps 列出的名称写，没有想法写 null。",
        "名称要写完整。地点、商品和内容 app 可以直接按名称调用，即使此刻没有展示；"
        "在地点做的事只能写当前地点下列出的名称。写得不准会返回候选，不会替你更改。"
        "prefer 例外：它只是意向，写得不准或那个 app 接不了这一单时环境另选一个，不退回也不给候选。",
        "在地点做哪件事由你点名：系列只写系列名称，从第一集做起，做完一集自动接下一集。"
        "用内容 app 时看到什么由环境选择，打开后放完一条自动接下一条，直到你结束它；没有可接的内容时停下，等你收尾。"
        "app 里的内容标题不是可写的名称。功能 app 不由你选择。",
        "用内容 app 只是看这个 app 里的内容，不显示商品，也不会下单；看到的内容像菜单或商品介绍，也不等于买了。"
        "只列在 prefer_apps 里的 app 用来下单和查询，不能用 interact 打开。"
        "买东西只有 purchase：你所在的地点就卖这件商品时直接到店买，否则经 app 下单；"
        "一次买几件时每件各自判断，可能分成几张订单。实际是到店买的还是用了哪个 app，写在购买结果的 channel 里。",
        "不知道买什么、在哪买，用 query。查询要 5 到 10 分钟，不能暂停；"
        "答案在查询做完时才写进这次请求的结果，写明是在哪个 app 里查的，中途结束就没有答案。"
        "查询做完自己结束，不用收尾，可以接着下单。查询不下单，要买再用 purchase 写答案里的商品名。"
        "人在路上不能查询，也不能和出行在同一次回复里开始。",
        "同时最多两项活动，槽位 1 和槽位 2 各放一项：出行、睡眠和在地点做事占槽位 1，购买、查询和用内容 app 占槽位 2。"
        "在地点做事可以同时购买、查询或用内容 app；睡眠要求槽位 2 空着。"
        "进行中和刚结束待收尾的活动都占槽位，暂停让出槽位。",
        "sleep 写的分钟数在 10 到 720 之间；就在当前地点睡，地点须能睡。"
        "睡着后到你写的时长才会再请你决策，提醒、推送和送达都不会叫醒你；"
        "过去一天睡得太少时，可能再多睡二十分钟到一小时。工具层 sleep 一行的 note 写现在睡大约多久能补满欠的觉，"
        "只供参考，睡多久由你自己写。",
        "出发前先暂停或结束必须在原地的活动，没做完的查询也要先结束；app 互动和快递购买可以随出行继续。"
        "地点活动到达别处前不能继续。",
        "活动自然结束后先用 conclude_activity 收尾，查询除外；report_status 写进展；"
        "pause_activity 暂停在地点做的事或内容 app 的使用；conclude_activity 结束活动并写总结、想记住的新情况与看法。"
        "在地点做的事用 conclude_activity 结束就算做完，下次点名从头做起，只是先放下、回头接着做用 pause_activity；"
        "内容 app 里没看完的内容，结束后仍留着进度。恢复暂停的活动不用开始工具：report_status 点名暂停活动时恢复它，"
        "它回到原来的槽位；恢复须回到实际开始地点且该槽位空着，读取最新共享进度；暂停活动在别处也可以结束。",
        "世界请求登记后由环境结算。进度层结果和记忆层 settled 是核验事实；"
        "自己写的 summary、self_note、self_notes 与看法（人设层 preferences 和关系里的看法也是）只是自己的想法，"
        "不能改变身体、资金、内容进度或订单结果，环境也不按它们限制你此刻能做什么；想法变了，就照现在的想法选，"
        "收尾时写下新的看法。",
        "商品实际收到时自动吃掉或用掉，不保存库存。到店商品在购买完成付款时收到，外卖等实际送达，快递先到驿站；"
        "本人到站后在驿站开始一件事（start_main_activity 的 interact）时领取本站全部到站包裹。"
        "购买过程结束时统一付款，付不起时整次取消。",
        "解饿只有两种办法：一是收到买来的食品或零食，收到的那一刻吃掉；"
        "二是在地点做完一件工具层 note 写着「供一餐」或「供一份零食」的事，做完的那一刻算吃了，"
        "系列的 note 或进行中活动的 state 写的是第几集供一餐或供一份零食，做完那一集时算。"
        "中途用 conclude_activity 结束的不算，"
        "暂停后恢复做完的照算。note 没写这两种说法的事不解饿，名称像吃饭也一样。",
        "作息与习惯照人设层过，当天特别安排照今日计划；没有相应习惯时按身体感觉安排。大致钟点只是大概，不会自动提醒。"
        "工作安排由环境给出，今日计划不能改变班次。今日计划只是自己的打算，环境不判定做没做完；"
        "发现计划里的事在工具层和拒绝原因里都找不到做法时，用 revise_day_plan 把它删掉或换成能做的事。",
        "工具层 available 为 false 的动作仍可提交，会按 reason 拒绝；先照原因处理。"
        "查询记忆和回看任务当场完成，不推进时间。"
        "available 为 true 的动作单独提交都会受理；同一件事做几次、先做哪件，环境都不另作处理。",
        "个人事务在三个活动控制工具的 errands 中维护，提醒到点只提醒一次且不会叫醒睡眠。"
        "办成由真实结算确认，自己说办完不算。"
        "个人事务只记要买到或取到的商品，item 写商品名；打电话、做家务这类没有商品的打算不记成个人事务。",
        "正文可以留空；写了只作为本次调用的自述留到这段停留或路程结束。"
        "思考只在同一时刻连续决策之间保留，时间推进后丢弃。",
    ],
}

#: 只读问答的系统层（docs/design/06-agent.md「提示与工具」）。
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
    "start_main_activity": "开始一项占槽位 1 的活动：出行、在当前地点做一件事，或睡觉。",
    "start_side_activity": "开始一项占槽位 2 的活动：购买商品、查询商品，或使用一个内容 app。",
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

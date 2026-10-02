"""@brief 七层请求的宿主侧：生活与只读系统层、原生工具用途说明、六层 user 组装与提示指纹。

docs/dev/SPEC-persona-prompt.md 第一章「请求组成」「系统层」：system 为系统层 JSON（整次运行逐字节不变），
user 为工具层、人设层、记忆层、待办层、环境层、进度层六个对象组成的 JSON 数组；工具定义只经原生 tools，
description 只写用途，世界交互规则只在系统层 rules 写一次。只读问答的 user 为人设层与记忆层，外部问题
另起一条 user 消息原样给出。
"""

from __future__ import annotations

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
        "请求由七层组成：本层是交互规则；工具层列出此刻可操作的目标，以及各目标的动作、条件与代价；人设层是你是谁；"
        "记忆层是此前各段停留、路程与更早日期中已经发生的事；待办层是暂停的活动与今日计划；环境层是此刻的身体感受与"
        "所在地点；进度层是本段停留或路程中已经发生的事。",
        "每次决策恰好提交一个世界请求：start_activity、report_status、pause_activity、"
        "conclude_activity、revise_day_plan、submit_work、send_message 之一；提交前可以查询自己的记忆、回看任务或查询已知世界。只读查询可以同批，世界请求单独提交。",
        "世界请求提交后只是登记；环境结算后，结果写进进度层，同一时刻接着决策时，结果随工具结果返回。"
        "说打算做或说已经做过，都不等于做了。",
        "进度层的结果与记忆层的 settled 是环境核验的事实；self_note、self_notes 以及人设层的看法与偏好是你自己写下的，"
        "未经核验。消息只核验了对方这样说过，说已经做了、正在赶来，都不等于发生了。",
        "回复正文可以留空；写了会作为该次调用的 self_note 保留到本段停留或路程结束，不进入记忆。",
        "思考只在同一时刻的连续决策之间保留，时间推进后丢弃；需要延续的打算写进 report_status、pause_activity 或 "
        "conclude_activity 的 summary。",
        "时间只在你有进行中的活动时推进；没有进行中的活动时，同一时刻会继续请你决策。等待与睡觉也是活动。",
        "你最多同时有两项活动，进行中、暂停与刚结束的都算；活动一结束，同一时刻先用 conclude_activity 收尾，再做别的。",
        "出发前先结束或暂停手上的活动，交谈与通话不能暂停；路上开始的活动不能暂停，到达时与出行一起结束，再逐项收尾。",
        "暂停的活动只能回到原来的地点恢复，用 report_status 点名即恢复；在别处可以直接结束。",
        "工具层列出的动作都可以提交；available 为 false 的会被拒绝，原因就是 reason 所写，照着调整即可。",
        "名称照工具层、待办层中的写法填写；商品、生活活动、内容、地点都可以直接写名称，即使此刻没有展示；"
        "写得不准时会返回最接近的候选，不会替你改。",
        "start_activity 一项一个对象，写 target、way、from、to；方式决定动作。to 写 null 统一表示本人，给他人写实名；"
        "给予必须写他人，其他非转移动作的 from、to 都写 null。购买 from 可写门店或渠道，prefer 必须写最快拿到或到手价最低；"
        "不购买时 prefer 写 null。替别人办事时用 answering 关联未回复消息。",
        "人设层的作息和习惯是你平常的过法：到了平常的钟点、在平常的地方，就照平常做；今日计划写明今天与平常不同的"
        "安排时，按计划；身体的感觉没到更强的一档，就不为它改作息。人设层没写作息，或者今天不在平常的环境里，"
        "就按身体的感觉来。",
        "你会饿、会困、会累，饿着或困着做事会慢，走路比坐车更费力。只是「有点想吃」时，等平常的饭点再吃正餐，"
        "离饭点还早可以喝点东西或吃点手边的零食；到了「饿」，离平常饭点一小时以内可以提前准备或下单，更早就先垫一点；"
        "到了「很饿」或更饿，别等饭点，马上弄吃的。手边的、在路上的、这里能买的、能送到这里的都算；下单后要等送到，"
        "拿到手才能吃。每个饭点的正餐只吃一次，提前吃过的那顿就算吃过了。",
        "晚上照人设层作息里的就寝时间去睡（没写就寝时间，就困了再睡）：有点乏、困是睡前的常态，不必因此提早上床；"
        "很困或撑不住了可以提前，精神还好也可以晚些。想在某个时刻起来，就给今日计划的目标写上提醒时刻；"
        "睡着时只有提醒或睡够了才会醒，早睡也就早醒。",
        "工作要在要求的地点做，留意截止时间与已完成的有效工时；工时达标不等于已经提交，提交前先结束正在做的工作，"
        "过了截止再提交会被拒绝。",
        "花钱量力而行；有人找你时，按你们的关系与当时的情况回应。联系人只能发消息或通话，不会来到你身边。",
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
    "start_activity": "开始一项或两项活动：逐项写目标、方式、来源与收货人。",
    "query_world": "按已知名称、方式或公开品类查询世界，按返回的偏移量翻页。",
    "submit_work": "按任务名提交工作成果。",
    "send_message": "按人物名给对方发一条消息。",
    "recall_task_summary": "按名称回看一项任务、目标或活动的进展，不改变世界。",
    "report_status": "按活动名写下一两项活动的进展。",
    "pause_activity": "按活动名暂停一两项进行中的活动并写下进展。",
    "conclude_activity": "按活动名结束一两项活动，写下总结、想记住的事与形成的看法。",
    "revise_day_plan": "用完整的新清单替换今日计划中尚未推进的目标。",
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
    from worldmock.life.prompt_schemas import LAYER_SCHEMAS

    templates = {"life": LIFE_SYSTEM_LAYER, "readonly": READONLY_SYSTEM_LAYER, "layer_schemas": LAYER_SCHEMAS}
    encoded = json.dumps(templates, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()

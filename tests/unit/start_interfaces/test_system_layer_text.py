"""系统层恰二十条，逐字、按序钉住；旧文字不再出现；工具用途句恰十一个键；提示指纹随文字改变。"""

from __future__ import annotations

import copy

from worldmock.agents import life_prompt
from worldmock.agents.life_prompt import LIFE_SYSTEM_LAYER, TOOL_DESCRIPTIONS, life_prompt_sha256
from worldmock.life.prompt_schemas import validate_layer

#: 系统层二十条的逐字定稿，次序即下标；下标 0、1、3、10、13、14、16、18、19 九条与改动之前逐字相同。
_RULES = (
    ("工具层写此刻的对象、动作、条件与代价；人设层写你的作息与习惯；记忆层写此前经历；"
     "待办层写暂停活动、个人事务和今日计划；环境层写身体感觉、位置、天气和工作安排；"
     "进度层写这段停留或路程中已经发生的事。"),
    "天气只以环境层给出的事实为准，不用记忆、常识或外部服务猜测天气。",
    ("每次决策以世界请求结束：要么提交一个控制请求（report_status、pause_activity、conclude_activity、"
     "revise_day_plan 之一），要么开始活动。开始活动时 start_main_activity 与 start_side_activity 各至多调用一次；"
     "要同时开始两项，就在同一次回复里各调用一次，两项一起核验，任何一项不成立两项都不开始。"
     "提交前可以查询记忆或回看任务，只读查询可以同批；世界请求不与只读查询同批，开始活动也不与控制请求同批，"
     "提交即结束本次决策。"),
    ("start_main_activity 开始占槽位 1 的活动，填 action、target、detail 三项：travel 是出行，target 写目的地地点名，"
     "detail 写出行方式名称，照工具层该目的地下列出的名称写；interact 是在当前地点做一件事，target 写当前地点名，"
     "detail 写这件事的名称，照工具层当前地点下列出的名称写；sleep 是睡觉，target 写要睡的分钟数（只写数字），"
     "detail 写 null。"),
    ("start_side_activity 开始占槽位 2 的活动，填 action、target、prefer 三项，target 始终是名称数组："
     "purchase 是购买，逐份写商品名，同一个名称写两次就是两份，全部商品共用一个购买过程，prefer 写想用来下单的 app；"
     "interact 是用内容 app，数组里只写一个内容 app 名称，prefer 写 null；"
     "query 是查询商品，数组里写商品名或商品类别名，prefer 写想在哪个 app 里查。"
     "prefer 照工具层 prefer_apps 列出的名称写，没有想法写 null。"),
    ("名称要写完整。地点、商品和内容 app 可以直接按名称调用，即使此刻没有展示；"
     "在地点做的事只能写当前地点下列出的名称。写得不准会返回候选，不会替你更改。"
     "prefer 例外：它只是意向，写得不准或那个 app 接不了这一单时环境另选一个，不退回也不给候选。"),
    ("在地点做哪件事由你点名：系列只写系列名称，从第一集做起，做完一集自动接下一集。"
     "用内容 app 时看到什么由环境选择，app 里的内容标题不是可写的名称。功能 app 不由你选择。"),
    ("用内容 app 只是看这个 app 里的内容，不显示商品，也不会下单；看到的内容像菜单或商品介绍，也不等于买了。"
     "只列在 prefer_apps 里的 app 用来下单和查询，不能用 interact 打开。"
     "买东西只有 purchase：你所在的地点就卖这件商品时直接到店买，否则经 app 下单；"
     "一次买几件时每件各自判断，可能分成几张订单。实际是到店买的还是用了哪个 app，写在购买结果的 channel 里。"),
    ("不知道买什么、在哪买，用 query。查询要 5 到 10 分钟，不能暂停；"
     "答案在查询做完时才写进这次请求的结果，写明是在哪个 app 里查的，中途结束就没有答案。"
     "查询做完自己结束，不用收尾，可以接着下单。查询不下单，要买再用 purchase 写答案里的商品名。"
     "人在路上不能查询，也不能和出行在同一次回复里开始。"),
    ("同时最多两项活动，槽位 1 和槽位 2 各放一项：出行、睡眠和在地点做事占槽位 1，购买、查询和用内容 app 占槽位 2。"
     "在地点做事可以同时购买、查询或用内容 app；睡眠要求槽位 2 空着。"
     "进行中和刚结束待收尾的活动都占槽位，暂停让出槽位。"),
    ("sleep 写的分钟数在 10 到 720 之间；就在当前地点睡，地点须能睡。"
     "睡着后到你写的时长才会再请你决策，提醒、推送和送达都不会叫醒你；"
     "过去一天睡得太少时，可能再多睡二十分钟到一小时。工具层 sleep 一行的 note 写现在睡大约多久能补满欠的觉，"
     "只供参考，睡多久由你自己写。"),
    ("出发前先暂停或结束必须在原地的活动，没做完的查询也要先结束；app 互动和快递购买可以随出行继续。"
     "地点活动到达别处前不能继续。"),
    ("活动自然结束后先用 conclude_activity 收尾，查询除外；report_status 写进展；"
     "pause_activity 暂停在地点做的事或内容 app 的使用；conclude_activity 结束活动并写总结、想记住的新情况与看法。"
     "在地点做的事用 conclude_activity 结束就算做完，下次点名从头做起，只是先放下、回头接着做用 pause_activity；"
     "内容 app 里没看完的内容，结束后仍留着进度。恢复暂停的活动不用开始工具：report_status 点名暂停活动时恢复它，"
     "它回到原来的槽位；恢复须回到实际开始地点且该槽位空着，读取最新共享进度；暂停活动在别处也可以结束。"),
    ("世界请求登记后由环境结算。进度层结果和记忆层 settled 是核验事实；"
     "自己写的 summary、self_note、self_notes 与看法只是自己的想法，不能改变身体、资金、内容进度或订单结果。"),
    ("商品实际收到时自动吃掉或用掉，不保存库存。到店商品在购买完成付款时收到，外卖等实际送达，快递先到驿站；"
     "本人到站后在驿站开始一件事（start_main_activity 的 interact）时领取本站全部到站包裹。"
     "购买过程结束时统一付款，付不起时整次取消。"),
    ("解饿只有两种办法：一是收到买来的食品或零食，收到的那一刻吃掉；"
     "二是在地点做完一件工具层 note 写着「供一餐」或「供一份零食」的事，做完的那一刻算吃了，"
     "系列的 note 写的是第几集供一餐或供一份零食，做完那一集时算。中途用 conclude_activity 结束的不算，"
     "暂停后恢复做完的照算。note 没写这两种说法的事不解饿，名称像吃饭也一样。"),
    ("作息与习惯照人设层过，当天特别安排照今日计划；没有相应习惯时按身体感觉安排。大致钟点只是大概，不会自动提醒。"
     "工作安排由环境给出，今日计划不能改变班次。"),
    ("工具层 available 为 false 的动作仍可提交，会按 reason 拒绝；先照原因处理。"
     "查询记忆和回看任务当场完成，不推进时间。"),
    ("个人事务在三个活动控制工具的 errands 中维护，提醒到点只提醒一次且不会叫醒睡眠。"
     "办成由真实结算确认，自己说办完不算。"),
    ("正文可以留空；写了只作为本次调用的自述留到这段停留或路程结束。"
     "思考只在同一时刻连续决策之间保留，时间推进后丢弃。"),
)


def test_system_layer_is_the_twenty_rules_verbatim_in_order():
    """二十条整表相等、互不相同；layer 与 role 不变；整层通过系统层 Schema。"""
    assert LIFE_SYSTEM_LAYER["rules"] == list(_RULES)
    assert len(_RULES) == 20 and len(set(_RULES)) == 20
    assert LIFE_SYSTEM_LAYER["layer"] == "系统层"
    assert LIFE_SYSTEM_LAYER["role"] == "你是模拟世界中的一个人物，依据人设层选择下一步行动；世界的时间、状态与结算由环境负责。"
    validate_layer(LIFE_SYSTEM_LAYER)


def test_retired_wording_is_gone_from_the_system_layer_and_tool_descriptions():
    """旧工具名、四个旧出行动作、名额的说法和两句旧睡眠文字都不再出现；用途句恰为十个原生工具加 finish。"""
    text = "".join(LIFE_SYSTEM_LAYER["rules"]) + "".join(TOOL_DESCRIPTIONS.values())
    for retired in ("start_activity", "walk", "drive", "take_taxi", "take_bus", "名额",
                    "sleep 以本人家为目标且须在家", "睡眠期间只有睡够了才会请你决策", "睡眠要求没有其他活动",
                    "内容由环境选择，标题不是动作目标"):
        assert retired not in text
    assert set(TOOL_DESCRIPTIONS) == {
        "start_main_activity", "start_side_activity", "recall_task_summary", "report_status",
        "pause_activity", "conclude_activity", "revise_day_plan", "memory_recent", "memory_search",
        "memory_time_range", "finish"}


def test_prompt_fingerprint_is_recomputable_and_follows_the_sleep_rule(monkeypatch):
    """提示指纹可重算；改动讲睡眠的那一条（rules[10]），指纹随之改变。"""
    current = life_prompt_sha256()
    assert current == life_prompt_sha256() and len(current) == 64
    changed = copy.deepcopy(LIFE_SYSTEM_LAYER)
    changed["rules"][10] = changed["rules"][10].replace("10 到 720", "10 到 600")
    assert changed["rules"][10] != LIFE_SYSTEM_LAYER["rules"][10]
    monkeypatch.setattr(life_prompt, "LIFE_SYSTEM_LAYER", changed)
    assert life_prompt_sha256() != current

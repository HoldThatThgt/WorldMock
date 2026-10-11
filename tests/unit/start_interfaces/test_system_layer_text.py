"""系统层讲两个开始工具、槽位、点名与睡眠的条目逐字，旧文字不再出现；提示指纹随文字改变。"""

from __future__ import annotations

import copy

from worldmock.agents import life_prompt
from worldmock.agents.life_prompt import LIFE_SYSTEM_LAYER, TOOL_DESCRIPTIONS, life_prompt_sha256
from worldmock.life.prompt_schemas import validate_layer

_DECISION = (
    "每次决策以世界请求结束：要么提交一个控制请求（report_status、pause_activity、conclude_activity、revise_day_plan 之一），"
    "要么开始活动。开始活动时 start_main_activity 与 start_side_activity 各至多调用一次；要同时开始两项，"
    "就在同一次回复里各调用一次，两项一起核验，任何一项不成立两项都不开始。提交前可以查询记忆、回看任务或查询世界，"
    "只读查询可以同批；世界请求不与只读查询同批，开始活动也不与控制请求同批，提交即结束本次决策。")
_MAIN = (
    "start_main_activity 开始占槽位 1 的活动，填 action、target、detail 三项：travel 是出行，target 写目的地地点名，"
    "detail 写出行方式名称，照工具层该目的地下列出的名称写；interact 是在当前地点做一件事，target 写当前地点名，"
    "detail 写这件事的名称，照工具层当前地点下列出的名称写；sleep 是睡觉，target 写要睡的分钟数（只写数字），detail 写 null。")
_SIDE = (
    "start_side_activity 开始占槽位 2 的活动，填 action、target 两项，target 始终是名称数组：purchase 是购买，"
    "逐份写商品名，同一个名称写两次就是两份，全部商品共用一个购买过程；interact 是用内容 app，数组里只写一个内容 app 名称。")
_NAMES = (
    "名称要写完整。地点、商品和内容 app 可以直接按名称调用，即使此刻没有展示；在地点做的事只能写当前地点下列出的名称。"
    "写得不准会返回候选，不会替你更改。")
_UNITS = (
    "在地点做哪件事由你点名：系列只写系列名称，做到第几集由环境接着排，做完一集自动接下一集。"
    "用内容 app 时看到什么由环境选择，app 里的内容标题不是可写的名称。功能 app 不由你选择。")
_SLOTS = (
    "同时最多两项活动，槽位 1 和槽位 2 各放一项：出行、睡眠和在地点做事占槽位 1，购买和用内容 app 占槽位 2。"
    "在地点做事可以同时购买或用内容 app；睡眠要求槽位 2 空着。进行中和刚结束待收尾的活动都占槽位，暂停让出槽位。")
_SLEEP = (
    "sleep 写的分钟数在 10 到 720 之间；就在当前地点睡，地点须能睡。睡着后到你写的时长才会再请你决策，"
    "提醒、推送和送达都不会叫醒你。工具层 sleep 一行的 note 写现在睡大约多久能补满欠的觉，只供参考，睡多久由你自己写。")
_DEPART = "出发前先暂停或结束必须在原地的活动；app 互动和快递购买可以随出行继续。地点活动到达别处前不能继续。"
_CONTROL = (
    "活动自然结束后先用 conclude_activity 收尾；report_status 写进展；pause_activity 暂停在地点做的事或内容 app 的使用；"
    "conclude_activity 结束活动并写总结、想记住的新情况与看法。恢复暂停的活动不用开始工具："
    "report_status 点名暂停活动时恢复它，它回到原来的槽位；恢复须回到实际开始地点且该槽位空着，"
    "读取最新共享进度；暂停活动在别处也可以结束。")
_GOODS = (
    "商品实际收到时自动吃掉或用掉，不保存库存。到店商品在购买完成付款时收到，外卖等实际送达，快递先到驿站；"
    "本人到站后在驿站开始一件事（start_main_activity 的 interact）时领取本站全部到站包裹。"
    "购买过程结束时统一付款，付不起时整次取消。")


def test_rewritten_rules_are_verbatim_and_in_the_declared_order():
    """改写的条目逐字相符：原「每项只填」一条拆成三条放在原位置，讲睡眠的一条接在槽位那一条之后。"""
    rules = LIFE_SYSTEM_LAYER["rules"]
    assert rules[2:11] == [_DECISION, _MAIN, _SIDE, _NAMES, _UNITS, _SLOTS, _SLEEP, _DEPART, _CONTROL]
    assert rules[12] == _GOODS and len(rules) == 17 and len(set(rules)) == 17
    validate_layer(LIFE_SYSTEM_LAYER)


def test_untouched_rules_keep_their_text():
    """其余条目逐字不变。"""
    rules = LIFE_SYSTEM_LAYER["rules"]
    assert rules[0].startswith("工具层写此刻的对象、动作、条件与代价；")
    assert rules[1] == "天气只以环境层给出的事实为准，不用记忆、常识或外部服务猜测天气。"
    assert rules[11].startswith("世界请求登记后由环境结算。")
    assert rules[13].startswith("作息与习惯照人设层过，")
    assert rules[14] == "工具层 available 为 false 的动作仍可提交，会按 reason 拒绝；先照原因处理。查询只读，不推进时间。"
    assert rules[15] == ("个人事务在三个活动控制工具的 errands 中维护，提醒到点只提醒一次且不会叫醒睡眠。"
                         "办成由真实结算确认，自己说办完不算。")
    assert rules[16].startswith("正文可以留空；")


def test_retired_wording_is_gone_from_the_system_layer_and_tool_descriptions():
    """旧工具名、四个旧出行动作、名额的说法和两句旧睡眠文字都不再出现。"""
    text = "".join(LIFE_SYSTEM_LAYER["rules"]) + "".join(TOOL_DESCRIPTIONS.values())
    for retired in ("start_activity", "walk", "drive", "take_taxi", "take_bus", "名额",
                    "sleep 以本人家为目标且须在家", "睡眠期间只有睡够了才会请你决策", "睡眠要求没有其他活动",
                    "内容由环境选择，标题不是动作目标"):
        assert retired not in text
    assert set(TOOL_DESCRIPTIONS) == {
        "start_main_activity", "start_side_activity", "query_world", "recall_task_summary", "report_status",
        "pause_activity", "conclude_activity", "revise_day_plan", "memory_recent", "memory_search",
        "memory_time_range", "finish"}


def test_prompt_fingerprint_is_recomputable_and_follows_the_sleep_rule(monkeypatch):
    """提示指纹可重算；改动讲睡眠的那一条，指纹随之改变。"""
    current = life_prompt_sha256()
    assert current == life_prompt_sha256() and len(current) == 64
    changed = copy.deepcopy(LIFE_SYSTEM_LAYER)
    changed["rules"][8] = changed["rules"][8].replace("10 到 720", "10 到 600")
    monkeypatch.setattr(life_prompt, "LIFE_SYSTEM_LAYER", changed)
    assert life_prompt_sha256() != current

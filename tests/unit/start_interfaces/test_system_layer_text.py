"""系统层恰二十二条，逐字、按序钉住；新五条与提案原文逐字节相同，保留的十七条与基线逐字相同；只读系统层四条；旧文字与已删除的机制不再出现；用途句恰九个键；唤醒表每一行都有系统层的措辞说出；规则引用的记忆层键都在 Schema 里；提示指纹随文字改变。"""

from __future__ import annotations

import copy
import hashlib
import json
import re

from worldmock.agents.prompt import life_prompt
from worldmock.agents.prompt.life_prompt import (
    LIFE_SYSTEM_LAYER,
    READONLY_SYSTEM_LAYER,
    TOOL_DESCRIPTIONS,
    life_prompt_sha256,
)
from worldmock.agents.prompt.prompt_schemas import MEMORY_LAYER_SCHEMA, validate_layer
from worldmock.state.interaction.requests import QUERY_MODELS
from worldmock.state.planning.schedule import WAKE_TRIGGERS

#: 系统层二十二条的逐字定稿，次序即下标；下标 3–14、16–20 十七条与改动之前（原下标 1–12、14–18）逐字相同。
_RULES = (
    ("人设层写你的身份、作息与习惯；记忆层写已经过去的事；待办层写暂停活动、个人事务、在途订单和今日计划；"
     "工具层写此刻的对象、动作、条件与代价；环境层写身体感觉、位置、天气和工作安排；"
     "进度层写此刻还没了结的事：进行中或待收尾的活动、上次决策之后新到的结果与消息、这段停留或路程里查到的答案，"
     "以及今天被拒绝的请求。"),
    ("记忆层按饮食、工作、生活三类记已经过去的事。「今天」按时刻列出今天的每次决策，一次决策一条，写明类别；"
     "一次决策提交后，下一次决策就能看到它的条目，同一时刻接着的决策也一样。「核验」是环境确认的经过；"
     "「自述」是你当时写下的话：「名称：」开头的是收尾或暂停时写的总结，「进展·名称：」是 report_status 写的进展，"
     "「看法·对象：」是看法，「记住：」是想记住的事，「正文：」是回复正文。每条至多 120 字，写不下的地方以「…」结尾；"
     "当天条目太多时，较早的条目会只留核验，或按小时合成一行，写明「条数」；全文用 search_memory 查「今天」。"
     "过了零点，每一类当天的条目会整理成一段「回顾」，写那天这类事的经过，以及你的感受、看法和打算；"
     "「近三天」每类列最近三天的回顾，饮食和生活取最近三天，工作取最近三个有工作的日子。"
     "回顾像日记，只记要紧的事和当时的感受，可能记错或漏记，它是整理出来的回忆，不是核验事实；"
     "某一格写着「未整理」，是那天这一类的回顾没有整理出来，不代表那天没有这类事。"
     "日子以零点为界：一件事在哪天结束就记在哪天，跨过零点还没结束的事，前一天只记它开始。"
     "更早的事不在请求里，「可查」写出能查到的最早日期；要用时调用 search_memory，写类别和时间："
     "时间写某一天（年-月-日）、一段日子（年-月-日~年-月-日，至多 31 天）、某个月（年-月）、某一年（年）、"
     "「今天」或今天的一段时刻（今天 时:分~时:分）；查到的往日是那一天、那个月或那一年的回顾，"
     "某天某一类「未整理」时给那天的原始条目；可以加关键词，多个关键词用空格隔开，都出现才算查到；"
     "时间写「全部」时必须写关键词，比如查上次吃某样东西是哪天、以前对某样东西怎么看。"
     "按名称回看一项任务或活动的进展用 recall_task_summary。一次决策至多查三次记忆。"),
    ("拿不准时可以先看记忆再定：选吃什么，看看最近几顿吃了什么、当时觉得怎样；接着做工作，看看前几个工作日做到哪里；"
     "想照以前的某个看法或习惯做，先看看它是哪天的回顾写到的、后来有没有变，近三天以外的用 search_memory 查。"
     "记忆只说明过去发生了什么、你当时怎么想，不替你决定；照旧、重复还是换一换都可以，照人设层的习惯和你现在的想法选。"),
    "天气只以环境层给出的事实为准，不用记忆、常识或外部服务猜测天气。",
    ("每次决策以世界请求结束：要么提交一个控制请求（report_status、pause_activity、conclude_activity、"
     "revise_day_plan 之一），要么开始活动。开始活动时 start_main_activity 与 start_side_activity 各至多调用一次；"
     "要同时开始两项，就在同一次回复里各调用一次，两项一起核验，任何一项不成立两项都不开始。"
     "提交前可以查询记忆或回看任务，只读查询可以同批；世界请求不与只读查询同批，开始活动也不与控制请求同批，"
     "提交即结束本次决策。没有进行中的活动时时间不会往前走，同一时刻会接着请你决策；"
     "等外卖或等某个钟点时，此刻能做的事都可以做，包括用工具层里可用的内容 app。"
     "醒着时，手上的事做完、下单的东西送到或到站可取、提醒到点、app 推送、身体感觉变了、"
     "到了上下班时刻或写明的休息时段开始或结束，都会再请你决策；一件事做得久，中途隔一阵也会请一次；"
     "没做完的活动照常进行。"),
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
     "用内容 app 时看到什么由环境选择，打开后放完一条自动接下一条，直到你结束它；没有可接的内容时停下，等你收尾。"
     "app 里的内容标题不是可写的名称。功能 app 不由你选择。"),
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
    ("世界请求登记后由环境结算。进度层的结果和记忆层「今天」里的「核验」是核验事实；"
     "自己写的 summary、正文，记忆层的「自述」和「回顾」，都只是你的想法和回忆，不能改变身体、资金、内容进度或订单结果，"
     "环境也不按它们限制你此刻能做什么；想法变了，就照现在的想法选，收尾时写下新的看法。"),
    ("商品实际收到时自动吃掉或用掉，不保存库存。到店商品在购买完成付款时收到，外卖等实际送达，快递先到驿站；"
     "本人到站后在驿站开始一件事（start_main_activity 的 interact）时领取本站全部到站包裹。"
     "购买过程结束时统一付款，付不起时整次取消。"),
    ("解饿只有两种办法：一是收到买来的食品或零食，收到的那一刻吃掉；"
     "二是在地点做完一件工具层 note 写着「供一餐」或「供一份零食」的事，做完的那一刻算吃了，"
     "系列的 note 或进行中活动的 state 写的是第几集供一餐或供一份零食，做完那一集时算。"
     "中途用 conclude_activity 结束的不算，"
     "暂停后恢复做完的照算。note 没写这两种说法的事不解饿，名称像吃饭也一样。"),
    ("作息与习惯照人设层过，当天特别安排照今日计划；没有相应习惯时按身体感觉安排。大致钟点只是大概，不会自动提醒。"
     "工作安排由环境给出，今日计划不能改变班次。今日计划只是自己的打算，环境不判定做没做完；"
     "发现计划里的事在工具层和拒绝原因里都找不到做法时，用 revise_day_plan 把它删掉或换成能做的事。"),
    ("工具层 available 为 false 的动作仍可提交，会按 reason 拒绝；先照原因处理。"
     "查询记忆和回看任务当场完成，不推进时间。"
     "available 为 true 的动作单独提交都会受理；同一件事做几次、先做哪件，环境都不另作处理。"),
    ("个人事务在三个活动控制工具的 errands 中维护，提醒到点只提醒一次且不会叫醒睡眠。"
     "办成由真实结算确认，自己说办完不算。"
     "个人事务只记要买到或取到的商品，item 写商品名；打电话、做家务这类没有商品的打算不记成个人事务。"),
    ("正文可以留空；写了就作为这次决策的自述，从下一次决策起记在记忆层「今天」里。"
     "思考只在同一时刻连续决策之间保留，时间推进后丢弃。"),
)

#: 新五条（下标 0、1、2、15、21）各自的 len 与 UTF-8 编码的 SHA-256，由提案「系统层规则原文」的终稿直接算出。
_NEW_RULE_DIGESTS = {
    0: (153, "d57aa60d0239073aa22189ba89b75416a8ea57f84560c230a51cf0c7d70e6b51"),
    1: (780, "19699a5038ca2008db45920f14d62b7eb2e6c421c9398d07615b114a2bc75d88"),
    2: (170, "c1b35ffa989aca497c9b1083f0d008a1386dd1b9220bf3fcb846e973ca813023"),
    15: (141, "c9536d64fe6dc081e4a33d4ff9844c88c0914d23b70cd39faf79c305b316e923"),
    21: (63, "73ec4117944341fff516e75765cce52c4a5cbc8781833c2599d72d030d00c928"),
}

#: 只读系统层四条的逐字定稿，次序即下标。
_READONLY_RULES = (
    "请求由人设层、记忆层与外部问题组成。",
    "记忆层的「核验」是环境核验的事实；「自述」和「回顾」是你自己写下或整理的，未经核验。",
    "只依据人设层与记忆层回答，不能改变世界，也不写入记忆。",
    "回答完成后单独调用 finish。",
)

#: 唤醒表每一行由系统层哪一条（下标）的哪段措辞说出；唤醒表增删行或改名时，这里与措辞一起改。
_WAKE_PHRASES = {
    "planned_end": (4, "手上的事做完"),
    "felt_band_change": (4, "身体感觉变了"),
    "delivery_arrival": (4, "下单的东西送到或到站可取"),
    "reconsideration_interval": (4, "中途隔一阵也会请一次"),
    "rejection_feedback": (4, "同一时刻会接着请你决策"),
    "session_open": (12, "睡着后到你写的时长才会再请你决策"),
    "progress_report_due": (4, "中途隔一阵也会请一次"),
    "no_active_activity": (4, "没有进行中的活动时时间不会往前走"),
    "reminder_due": (4, "提醒到点"),
    "duty_boundary": (4, "到了上下班时刻或写明的休息时段开始或结束"),
    "app_push": (4, "app 推送"),
}

#: 「规则里没有已删除的机制」：三个旧记忆工具名、旧记忆层与人设层的键、反思与巩固、基线的旧说法，以及旧只读规则。
_RETIRED_MECHANISMS = (
    "memory_recent", "memory_search", "memory_time_range", "settled", "self_note", "self_notes", "earlier_days",
    "earlier_today", "preferences", "noted_on", "反思", "巩固", "就寝", "此前经历", "这段停留或路程中已经发生的事",
    "本次调用的自述", "只能查询自己的记忆")


def _digest(value) -> str:
    """紧凑 JSON 的 UTF-8 编码求 SHA-256。"""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _property_keys(node) -> set[str]:
    """Schema 里各层 properties 的全部键（递归收集）。"""
    if isinstance(node, list):
        return {key for item in node for key in _property_keys(item)}
    if not isinstance(node, dict):
        return set()
    keys = set(node.get("properties", {})) if isinstance(node.get("properties"), dict) else set()
    return keys | {key for value in node.values() for key in _property_keys(value)}


def test_system_layer_is_the_twenty_two_rules_verbatim_in_order():
    """二十二条整表相等、互不相同；layer 与 role 不变；整层通过系统层 Schema。"""
    assert LIFE_SYSTEM_LAYER["rules"] == list(_RULES)
    assert len(_RULES) == 22 and len(set(_RULES)) == 22
    assert LIFE_SYSTEM_LAYER["layer"] == "系统层"
    assert LIFE_SYSTEM_LAYER["role"] == "你是模拟世界中的一个人物，依据人设层选择下一步行动；世界的时间、状态与结算由环境负责。"
    validate_layer(LIFE_SYSTEM_LAYER)


def test_new_rules_are_the_proposal_text_byte_for_byte():
    """新五条（各层写什么、记忆层怎样组织怎样查、拿不准时怎样看记忆、核验与回忆、正文）的长度与摘要等于提案终稿。"""
    rules = LIFE_SYSTEM_LAYER["rules"]
    for index, (length, digest) in _NEW_RULE_DIGESTS.items():
        assert len(rules[index]) == length, index
        assert hashlib.sha256(rules[index].encode()).hexdigest() == digest, index


def test_kept_rules_and_the_whole_list_match_their_digests():
    """保留的十七条与改动之前同一摘要（只是下标顺移），整份二十二条的摘要也钉住。"""
    rules = LIFE_SYSTEM_LAYER["rules"]
    assert _digest([*rules[3:15], *rules[16:21]]) == "f4e25c9f0fdce4b05da6b5fe0d20605734ebcd854715c0a621d448875b2fccc9"
    assert _digest(rules) == "962509d9b376b0bd44af8208e761429333682ffbae2ac3b2561b284c36ec2e9b"


def test_wake_sentence_names_every_wake_trigger():
    """唤醒表的每一行都由系统层的一段措辞说出：对照表的键恰是唤醒表的全部名称，每段措辞都在所注的那一条里。"""
    names = [trigger.name for trigger in WAKE_TRIGGERS]
    assert set(_WAKE_PHRASES) == set(names) and len(_WAKE_PHRASES) == len(names)
    for name, (index, phrase) in _WAKE_PHRASES.items():
        assert phrase in LIFE_SYSTEM_LAYER["rules"][index], name


def test_readonly_system_layer_is_the_four_rules_verbatim():
    """只读问答系统层：role 不变，规则恰为四条终稿，摘要钉住，整层通过系统层 Schema。"""
    assert READONLY_SYSTEM_LAYER["role"] == "你是模拟世界中的一个人物，依据人设层与记忆层回答外部问题。"
    assert READONLY_SYSTEM_LAYER["rules"] == list(_READONLY_RULES)
    assert _digest(READONLY_SYSTEM_LAYER["rules"]) == "824b1da806e387c3f1e3726c6a431e828e715f9cf9a46b6c1df4b292bc557a3f"
    validate_layer(READONLY_SYSTEM_LAYER)


def test_retired_wording_is_gone_from_the_system_layers_and_tool_descriptions():
    """旧工具名、四个旧出行动作、名额的说法、两句旧睡眠文字都不再出现；两份系统层与用途句也不提已删除的机制。"""
    text = "".join(LIFE_SYSTEM_LAYER["rules"]) + "".join(READONLY_SYSTEM_LAYER["rules"]) + "".join(
        TOOL_DESCRIPTIONS.values())
    for retired in ("start_activity", "walk", "drive", "take_taxi", "take_bus", "名额",
                    "sleep 以本人家为目标且须在家", "睡眠期间只有睡够了才会请你决策", "睡眠要求没有其他活动",
                    "内容由环境选择，标题不是动作目标", *_RETIRED_MECHANISMS):
        assert retired not in text, retired


def test_tool_descriptions_are_the_nine_purpose_sentences():
    """用途句恰为八个原生工具加 finish，次序固定；search_memory 的用途句与它的参数模型说明逐字相同。"""
    assert list(TOOL_DESCRIPTIONS) == [
        "start_main_activity", "start_side_activity", "recall_task_summary", "report_status", "pause_activity",
        "conclude_activity", "revise_day_plan", "search_memory", "finish"]
    assert TOOL_DESCRIPTIONS["search_memory"] == "按类别和时间查自己过去的记忆，可以加关键词，不改变世界。"
    assert QUERY_MODELS["search_memory"].__doc__.strip() == "@brief " + TOOL_DESCRIPTIONS["search_memory"]


def test_memory_rules_quote_existing_memory_layer_keys():
    """讲记忆的规则（生活系统层下标 0、1、2、15、21 与只读系统层四条）在「」里引用的词，去掉以「：」结尾的前缀写法与
    「…」「全部」之后恰为八个，都是记忆层 Schema 里的键。"""
    rules = [LIFE_SYSTEM_LAYER["rules"][index] for index in (0, 1, 2, 15, 21)] + READONLY_SYSTEM_LAYER["rules"]
    quoted = {word for rule in rules for word in re.findall(r"「([^」]*)」", rule)}
    keys = {word for word in quoted if not word.endswith("：") and word not in {"…", "全部"}}
    assert keys == {"今天", "核验", "自述", "条数", "回顾", "近三天", "未整理", "可查"}
    assert keys <= _property_keys(MEMORY_LAYER_SCHEMA)


def test_prompt_fingerprint_is_recomputable_and_follows_the_sleep_rule(monkeypatch):
    """提示指纹可重算；改动讲睡眠的那一条（rules[12]），指纹随之改变。"""
    current = life_prompt_sha256()
    assert current == life_prompt_sha256() and len(current) == 64
    changed = copy.deepcopy(LIFE_SYSTEM_LAYER)
    changed["rules"][12] = changed["rules"][12].replace("10 到 720", "10 到 600")
    assert changed["rules"][12] != LIFE_SYSTEM_LAYER["rules"][12]
    monkeypatch.setattr(life_prompt, "LIFE_SYSTEM_LAYER", changed)
    assert life_prompt_sha256() != current

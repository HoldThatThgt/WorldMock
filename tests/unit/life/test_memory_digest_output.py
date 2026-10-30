"""日、月、年回顾的请求、提示与输出守卫：都是纯函数，DigestInputs 由测试手工构造（docs/design/06-agent.md「日、月、年回顾」）。

目录名称、商品名称照「守卫用到的概念」从夹具环境目录 tests/fixtures/dataset-environment 写出；夹具没有食材包状态，
用到食材包的用例另给食材包名称。
"""

from __future__ import annotations

import json
import logging

import pytest

from tests.life.conftest import memory_digest_response
from worldmock.configuration.inputs.models import ModelMetadata
from worldmock.simulation_engine.evidence.memory_digest import (
    DAY_DIGEST_PROMPT,
    DIGEST_PROMPTS,
    MONTH_DIGEST_PROMPT,
    YEAR_DIGEST_PROMPT,
    DigestInputs,
    DigestOutcome,
    DigestParagraph,
    DigestPeriod,
    DigestRow,
    MemoryDigestOutputError,
    MemoryDigestRequest,
    check_attempt,
    digest_outcome,
    memory_digest_tool,
    prepare_digest_correction,
    prepare_digest_request,
)
from worldmock.state.interaction.requests import contains_hex_identity, validate_memory_text
from worldmock.state.interaction.schema import deepseek_strict_schema

#: 夹具目录的目录名称（len ≥ 3，升序）与其中的商品名称。
NAMES = ("Keep", "一心面馆", "三杯咖啡", "二味小炒", "五里公园", "哔哩哔哩", "四季便利", "季度销售数据整理", "平台笔记本",
         "快递驿站", "扑克牌", "支付宝", "柠檬茶", "测试休息", "测试剧集", "测试剧集第一集", "测试剧集第三集", "测试剧集第二集",
         "测试图文", "测试实况", "测试慢跑", "测试文章", "测试短视频", "测试短视频二", "测试课程", "测试长视频", "测试闲聊",
         "牛肉面", "饿了么", "驿站取件", "高德地图")
GOODS = frozenset({"牛肉面", "平台笔记本", "扑克牌", "柠檬茶"})
#: 本章「提示原文」的日回顾模板（逐字写死）。
_DAY_PROMPT = (
    "你替模拟世界里的一个人物把他昨天的记录整理成回顾。输入按饮食、工作、生活三类给出他昨天的记录：「核验」是环境确认的经过，「当时查到」是他当时查询看到的结果，「自述」是他当时写下的话（「名称：」是收尾或"
    "暂停的总结，「进展·」是中途写的进展，「看法·」是看法，「记住：」是想记住的事，「正文：」是回复正文），每条带编号。每类写一段不超过 200 字的话，用他的第一人称：先写那天这类事经过了什么，以核验为"
    "准；再写他对具体的东西、地方、人和做法的看法、习惯和打算，以自述里的「看法」「记住」和总结为准：每条看法用一句短话写出对象的名字和态度（喜不喜欢、还想不想再来、打算保持还是改掉）；写不下时先写说了以后"
    "要照做、要换掉或不再做的，再写当天写过不止一次的；只写自述里有的看法，不从经过推测感受。在 refs 里写所依据的记录编号，至少一个。提到的东西只用输入里出现过的名字；饮食类写到的商品名只能取自核验，"
    "「当时查到」和自述里提到、核验里没有的商品不写名字，写看法时也一样；时刻、价钱和数量只写输入里出现过的，一律用阿拉伯数字；时长和次数用输入里的起止时刻表达，不要自己换算；写某个时刻时，同一句里提到的名"
    "字要和这个时刻出自同一条记录；饮食类要写出核验里当天收到的每样吃的、喝的，以及做饭用的那份食材包，写做饭的那一句里只写当天做饭用的那一份；不要补造输入里没有的经过、看法或别人说的话，也不要拿别的日子作"
    "比较。某一类没有记录时写 null。只调用一次 submit_memory_digest。"
)
#: 本章「提示原文」的月回顾模板（逐字写死）。
_MONTH_PROMPT = (
    "你替模拟世界里的一个人物把他上个月的日回顾整理成月回顾。输入按饮食、工作、生活三类给出那个月每一天的日回顾，每段带日期和编号，没整理出来的日子写着「未整理」；"
    "另附代码从事件算出的次数统计。每类写一段不超过 800 字的话，用他的第一人称，写这个月这类事的规律、习惯和变化，以及值得记住的事；把日回顾里反复出现、或说了以后要照"
    "做或要换掉的看法、习惯和打算，各用一句短话写出对象和态度，写明最早、最近是哪天的回顾写到的（日期照抄输入）、后来有没有变。在 refs 里写所依据的日回顾编号，至少一"
    "个。提到的东西只用输入里出现过的名字；时刻、价钱、数量和日期只写输入里出现过的，一律用阿拉伯数字；写到次数时照抄统计里的那一行，不要自己数；不要补造输入里没有的"
    "经过、看法或别人说的话。某一类没有输入时写 null。只调用一次 submit_memory_digest。"
)
#: 本章「提示原文」的年回顾模板（逐字写死）。
_YEAR_PROMPT = (
    "你替模拟世界里的一个人物把他上一年的月回顾整理成年回顾。输入按饮食、工作、生活三类给出那一年每个月的月回顾，每段带月份和编号，没整理出来的月份写着「未整理」；"
    "另附代码从事件算出的全年次数统计。每类写一段不超过 2000 字的话，用他的第一人称，写这一年这类事固定下来的习惯、口味和做法，哪些看法在哪个月变了；到年底仍成立的"
    "看法各用一句短话写出对象和态度，注明出自哪个月的回顾（月份照抄输入）。在 refs 里写所依据的月回顾编号，至少一个。提到的东西只用输入里出现过的名字；时刻、价钱、"
    "数量、日期和月份只写输入里出现过的，一律用阿拉伯数字；写到次数时照抄统计里的那一行，不要自己数；不要补造输入里没有的经过、看法或别人说的话。某一类没有输入时写 "
    "null。只调用一次 submit_memory_digest。"
)
#: 本章「请求」的参数 Schema（逐值写死）。
_SCHEMA = json.loads(
    '{"additionalProperties":false,"properties":{"food":{"anyOf":[{"additionalProperties":false,"properties":'
    '{"text":{"title":"Text","type":"string"},"refs":{"items":{"type":"string"},"title":"Refs","type":"array"}},'
    '"required":["text","refs"],"title":"DigestParagraph","type":"object"},{"type":"null"}]},"work":{"anyOf":['
    '{"additionalProperties":false,"properties":{"text":{"title":"Text","type":"string"},"refs":{"items":'
    '{"type":"string"},"title":"Refs","type":"array"}},"required":["text","refs"],"title":"DigestParagraph",'
    '"type":"object"},{"type":"null"}]},"life":{"anyOf":[{"additionalProperties":false,"properties":{"text":'
    '{"title":"Text","type":"string"},"refs":{"items":{"type":"string"},"title":"Refs","type":"array"}},'
    '"required":["text","refs"],"title":"DigestParagraph","type":"object"},{"type":"null"}]}},'
    '"required":["food","work","life"],"title":"MemoryDigestRequest","type":"object"}')
_KEYS = (("food", "饮食"), ("work", "工作"), ("life", "生活"))


def _row(number: str, sources: tuple[str, ...] | None = None, **fields) -> DigestRow:
    """一行输入：编号与给定的键（列表值逐项是字符串值），背后的事件缺省是 event-{编号}。"""
    payload = {"编号": number, **fields}
    values = [item for value in fields.values() for item in (value if isinstance(value, list) else [value])
              if isinstance(item, str)]
    return DigestRow(number, payload, sources or (f"event-{number}",), "\n".join(values),
                     "\n".join(fields.get("核验", [])))


def _inputs(food=(), work=(), life=(), tier="day", counts=None, required=(), cooked=(), kits=frozenset()):
    """手工构造的一期输入：日回顾带人物卡片，月、年回顾的类别值带下一级回顾与次数统计。"""
    rows = {"food": tuple(food), "work": tuple(work), "life": tuple(life)}
    counts = {key: tuple((counts or {}).get(key, ())) for key, _ in _KEYS}
    lower = {"month": "日回顾", "year": "月回顾"}.get(tier)
    payload = {"人物卡片": "产品经理，32岁，周末常去五里公园。"} if tier == "day" else {}
    for key, name in _KEYS:
        lines = [row.payload for row in rows[key]]
        payload[name] = lines if tier == "day" else {lower: lines, "次数统计": list(counts[key])}
    period = DigestPeriod(tier, {"day": "2026-01-05", "month": "2026-01", "year": "2026"}[tier])
    return DigestInputs(period, payload, rows, counts, tuple(required), tuple(cooked), NAMES, GOODS, kits)


def _answer(**categories) -> dict:
    """一次合法工具调用的原始响应；没写的类为 null，值是 (正文, refs)。"""
    arguments = {key: None for key, _ in _KEYS}
    arguments.update({key: {"text": text, "refs": list(refs)} for key, (text, refs) in categories.items()})
    return memory_digest_response(arguments)


def _problems(inputs: DigestInputs, accepted=None, **categories) -> str | None:
    """一次尝试的错误文字，全部通过时为 None。"""
    _, error = check_attempt(inputs, _answer(**categories), accepted or {})
    return None if error is None else str(error)


def _metadata(window: int = 262144, output: int = 32000) -> ModelMetadata:
    """冻结的模型元数据。"""
    return ModelMetadata(requested_model="test-model", metadata_url="https://model.example.invalid/v1/models",
                         context_window=window, max_output_tokens=output)


_LIFE = _row("n1", 时刻="20:00", 核验=["开始测试闲聊"], 自述=["测试闲聊：收尾。"])


def test_digest_prompts_match_the_frozen_texts():
    """日、月、年三份模板逐字等于本章「提示原文」，各为一行、没有首尾空白；按级取模板的映射恰为三级。"""
    assert (DAY_DIGEST_PROMPT, MONTH_DIGEST_PROMPT, YEAR_DIGEST_PROMPT) == (_DAY_PROMPT, _MONTH_PROMPT, _YEAR_PROMPT)
    for prompt in (DAY_DIGEST_PROMPT, MONTH_DIGEST_PROMPT, YEAR_DIGEST_PROMPT):
        assert "\n" not in prompt and prompt == prompt.strip()
    assert tuple(DIGEST_PROMPTS) == ("day", "month", "year")
    assert (DIGEST_PROMPTS["day"], DIGEST_PROMPTS["month"], DIGEST_PROMPTS["year"]) == (
        DAY_DIGEST_PROMPT, MONTH_DIGEST_PROMPT, YEAR_DIGEST_PROMPT)


def test_submit_memory_digest_tool_and_schema_are_pinned():
    """唯一严格工具与参数 Schema 逐值等于本章的写法：三个类别键内联、不产生 $defs，没有长度与条数上限。"""
    assert deepseek_strict_schema(MemoryDigestRequest) == _SCHEMA
    assert memory_digest_tool() == {"type": "function", "function": {
        "name": "submit_memory_digest", "strict": True,
        "description": "Submit one digest paragraph with its record references for each category, "
                       "or null for a category without records.",
        "parameters": _SCHEMA}}


@pytest.mark.parametrize("output,cap", [(32000, 8000), (4096, 4096)])
def test_request_caps_output_and_disables_thinking(output, cap):
    """请求的全部键与值：系统消息是这一级的模板，用户消息是紧凑、不排序的 JSON，输出上限取 8000 与元数据上限中较小的。"""
    inputs = _inputs(life=[_LIFE])
    request = prepare_digest_request(inputs, "test-model", _metadata(output=output))
    content = json.dumps(inputs.payload, ensure_ascii=False, separators=(",", ":"))
    assert request == {"model": "test-model",
                       "messages": [{"role": "system", "content": DAY_DIGEST_PROMPT},
                                    {"role": "user", "content": content}],
                       "stream": False, "tools": [memory_digest_tool()], "tool_choice": "required",
                       "max_tokens": cap, "max_retries": 0, "extra_body": {"thinking": {"type": "disabled"}}}
    assert content.startswith('{"人物卡片":') and '{"编号":"n1","时刻":"20:00","核验"' in content
    assert ": " not in content and ", " not in content


def test_request_over_capacity_fails_before_any_call(caplog):
    """整次请求（含输出预留）超过窗口的 85%：写错误日志，抛运行故障，输入不裁剪。"""
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="^memory digest request capacity exceeded$"):
        prepare_digest_request(_inputs(life=[_LIFE]), "test-model", _metadata(window=9000, output=8000))
    assert "Memory digest request exceeds the model context" in caplog.messages


def test_call_ids_name_agent_tier_period_and_attempt():
    """调用身份写人物、级、期与第几次尝试。"""
    assert DigestPeriod("day", "2026-10-09").call_id(1, 1) == "memory-digest:1:day:2026-10-09:attempt:1"
    assert DigestPeriod("month", "2026-10").call_id(1, 2) == "memory-digest:1:month:2026-10:attempt:2"
    assert DigestPeriod("year", "2026").call_id(1, 1) == "memory-digest:1:year:2026:attempt:1"


def test_correction_appends_one_message_to_the_original_request():
    """每次重试都是原始请求的消息加一条纠正消息：前次响应、英文问题与固定指令；其余键与原始请求相同。"""
    original = prepare_digest_request(_inputs(life=[_LIFE]), "test-model", _metadata())
    frozen = json.loads(json.dumps(original))
    for number in (2, 3):
        raw = _answer(life=(f"第{number}次", ["n1"]))
        error = MemoryDigestOutputError(f"problem {number}")
        corrected = prepare_digest_correction(original, raw, error)
        assert corrected["messages"][:-1] == original["messages"] and len(corrected["messages"]) == 3
        assert {key: value for key, value in corrected.items() if key != "messages"} == {
            key: value for key, value in original.items() if key != "messages"}
        message = corrected["messages"][-1]
        assert message["role"] == "user"
        assert json.loads(message["content"]) == {
            "PREVIOUS_INVALID_RESPONSE": raw, "VALIDATION_ERROR": f"problem {number}",
            "INSTRUCTION": "Return a complete new submit_memory_digest tool call using the unchanged schema."}
    assert original == frozen


def _with_calls(calls) -> dict:
    """唯一 choice 带给定 tool_calls 的原始响应。"""
    return {"choices": [{"message": {"role": "assistant", "tool_calls": calls}}]}


def _call(name: str, arguments: str) -> list[dict]:
    """恰一个工具调用。"""
    return [{"id": "call", "type": "function", "function": {"name": name, "arguments": arguments}}]


@pytest.mark.parametrize("raw,message", [
    ({"choices": []}, "response must contain exactly one choice"),
    ({"choices": [{"message": {}}, {"message": {}}]}, "response must contain exactly one choice"),
    (_with_calls(None), "response must contain exactly one submit_memory_digest tool call"),
    (_with_calls(_call("submit_day_plan", "{}")), "the only tool call must be submit_memory_digest"),
    (_with_calls(_call("submit_memory_digest", "not json")), "arguments: Invalid JSON: expected ident at line 1 column 2"),
    (_with_calls(_call("submit_memory_digest", '{"food":null,"work":null}')), "life: Field required"),
    (_with_calls(_call("submit_memory_digest", '{"food":null,"work":null,"life":null,"x":1}')),
     "x: Extra inputs are not permitted"),
], ids=["no-choice", "two-choices", "no-tool-call", "other-tool", "invalid-json", "missing-key", "extra-key"])
def test_response_level_errors(raw, message):
    """响应级的第一处不合就是这次尝试的错误（文字逐字），这次尝试不接受任何类，此前接受的类原样保留。"""
    earlier = {"life": DigestParagraph(text="早先接受的。", refs=["n1"])}
    kept, error = check_attempt(_inputs(food=[_row("n1", 核验=["送达柠檬茶"])], life=[_row("n2", 核验=["开始睡觉"])]),
                                raw, earlier)
    assert str(error) == message and isinstance(error, MemoryDigestOutputError)
    assert kept == earlier


def test_truncated_output_is_a_validation_failure(caplog):
    """在 max_tokens 截断的响应即使工具调用合法也不合格，错误恰为截断的文字，不接受任何类。"""
    raw = memory_digest_response({"food": None, "work": None, "life": {"text": "睡了一觉。", "refs": ["n1"]}},
                                 finish_reason="length")
    with caplog.at_level(logging.ERROR):
        kept, error = check_attempt(_inputs(life=[_LIFE]), raw, {})
    assert str(error) == "memory digest output was truncated at max_tokens" and kept == {}
    assert "Memory digest output was truncated at max_tokens" in caplog.messages


def test_null_rules_follow_category_input():
    """有输入的类给 null 与无输入的类给段落各报一条；无输入的类给 null 不报，有输入的类给合格段落就接受。"""
    inputs = _inputs(food=[_row("n1", 时刻="08:28", 核验=["送达柠檬茶"])], life=[_row("n2", 核验=["开始睡觉"])])
    kept, error = check_attempt(inputs, _answer(work=("上班。", ["n1"]), life=("睡了一觉。", ["n2"])), {})
    assert str(error) == ("digests.food must not be null: the input has food records; "
                          "digests.work must be null: the input has no work records")
    assert set(kept) == {"life"}
    kept, error = check_attempt(inputs, _answer(food=("收到了柠檬茶。", ["n1"]), life=("又睡了。", ["n2"])), {})
    assert error is None and kept["food"].text == "收到了柠檬茶。" and kept["life"].text == "又睡了。"


@pytest.mark.parametrize("tier,text,message", [
    ("day", "我" * 200, None),
    ("day", "我" * 201, "digests.life.text has 201 characters; the day digest limit is 200"),
    ("month", "我" * 800, None),
    ("month", "我" * 801, "digests.life.text has 801 characters; the month digest limit is 800"),
    ("year", "我" * 2000, None),
    ("year", "我" * 2001, "digests.life.text has 2001 characters; the year digest limit is 2000"),
    ("day", "", "digests.life.text must not be empty; digests.life.text must contain Chinese"),
    ("day", " \n ", "digests.life.text must not be empty; digests.life.text must contain Chinese"),
    ("day", "slept well.", "digests.life.text must contain Chinese"),
    ("day", "我记得deadbeefcafe。", "digests.life.text contains a hexadecimal identifier"),
], ids=["day-200", "day-201", "month-800", "month-801", "year-2000", "year-2001", "empty", "blank", "no-chinese",
        "hex"])
def test_text_guards(tier, text, message):
    """正文非空、长度不超过这一级的上限、含中文、不含八位以上十六进制串。"""
    number = {"day": "n1", "month": "d1", "year": "m1"}[tier]
    row = _row(number, 时刻="20:00", 核验=["开始睡觉"]) if tier == "day" else _row(number, 日期="2026-01-05", 回顾="睡觉。")
    assert _problems(_inputs(life=[row], tier=tier), life=(text, [number])) == message


def test_refs_must_cite_this_categorys_numbers():
    """refs 不能为空；别的类的编号、不存在的编号各报一条，同一个错编号只报一次，按 refs 的次序。"""
    inputs = _inputs(food=[_row("n1", 核验=["送达柠檬茶"])], work=[_row("n2", 核验=["到公司"])],
                     life=[_row("n3", 核验=["开始睡觉"])])
    done = {"food": DigestParagraph(text="收到了柠檬茶。", refs=["n1"]), "work": DigestParagraph(text="上班。", refs=["n2"])}
    assert _problems(inputs, done, life=("睡了一觉。", [])) == "digests.life.refs must cite at least one record number"
    assert _problems(inputs, done, life=("睡了一觉。", ["n1", "n9", "n1", "n3"])) == (
        "digests.life.refs: n1 is not a life record number; digests.life.refs: n9 is not a life record number")
    assert _problems(inputs, done, life=("睡了一觉。", ["n3"])) is None


def test_name_guard_reads_only_this_categorys_input():
    """正文写到的目录名称必须出自这一类的输入：只在别的类或人物卡片里时报名称问题；短于 3 字的名称不查。"""
    inputs = _inputs(food=[_row("n1", 核验=["送达柠檬茶"])], life=[_row("n2", 核验=["开始测试闲聊"])])
    done = {"food": DigestParagraph(text="收到了柠檬茶。", refs=["n1"])}
    assert _problems(inputs, done, life=("五里公园没去，喝了柠檬茶。", ["n2"])) == (
        "digests.life: the name 五里公园 does not appear in the life input; "
        "digests.life: the name 柠檬茶 does not appear in the life input")
    assert _problems(inputs, done, life=("测试闲聊挺好，吃了饭团。", ["n2"])) is None


def test_food_goods_names_must_come_from_verified_lines():
    """饮食类正文的商品名必须出自饮食核验，只在「当时查到」或自述里的不算；工作类不跑这一守卫。"""
    food = _row("n1", 核验=["查完饿了么·柠檬茶：有可买"], 当时查到=["在饿了么查柠檬茶", "牛肉面 便宜"], 自述=["正文：想吃牛肉面。"])
    inputs = _inputs(food=[food], work=[_row("n2", 自述=["正文：午饭的牛肉面还行。"])])
    done = {"work": DigestParagraph(text="说起牛肉面。", refs=["n2"])}
    assert _problems(inputs, done, food=("想吃牛肉面。", ["n1"])) == (
        "digests.food: the goods name 牛肉面 does not appear in the food verified lines")
    assert _problems(inputs, done, food=("查了柠檬茶。", ["n1"])) is None
    assert _problems(inputs, {}, food=("查了柠檬茶。", ["n1"]), work=("牛肉面还行。", ["n2"])) is None


def test_food_coverage_requires_every_received_food_and_cooked_kit():
    """饮食覆盖：漏写收到的饮料、漏写做饭用的食材包各报；漏写的必写名称在一条问题里按必写名称的次序列出、用「、」连接。"""
    food = [_row("n1", 核验=["送达柠檬茶"]), _row("n2", 核验=["做饭，用收到的扑克牌"])]
    inputs = _inputs(food=food, required=("柠檬茶", "扑克牌"), cooked=("扑克牌",), kits=frozenset({"扑克牌"}))
    message = "digests.food must name every food received and every meal kit cooked that day; missing: "
    assert _problems(inputs, food=("这一天吃喝照常。", ["n1"])) == message + "柠檬茶、扑克牌"
    assert _problems(inputs, food=("收到了柠檬茶。", ["n1"])) == message + "扑克牌"
    assert _problems(inputs, food=("做饭用了扑克牌。", ["n2"])) == message + "柠檬茶"
    assert _problems(inputs, food=("收到了柠檬茶，做饭用了扑克牌。", ["n1", "n2"])) is None


def test_cooking_sentence_names_only_the_kit_cooked_that_day():
    """含「做饭」的句子写了当天收到却没做的食材包时报问题，写明当天做饭用的那份；分在两句时不报；当天没做饭时写 none。"""
    food = [_row("n1", 核验=["送达平台笔记本"]), _row("n2", 核验=["做饭，用收到的扑克牌"])]
    kits = frozenset({"扑克牌", "平台笔记本"})
    inputs = _inputs(food=food, cooked=("扑克牌",), kits=kits)
    assert _problems(inputs, food=("做饭用了扑克牌和平台笔记本。", ["n2"])) == (
        "digests.food: the sentence about 做饭 names 平台笔记本, but the meal kit cooked that day was 扑克牌")
    assert _problems(inputs, food=("收到了平台笔记本。做饭用了扑克牌。", ["n1", "n2"])) is None
    idle = _inputs(food=food, kits=kits)
    assert _problems(idle, food=("做饭用了平台笔记本。", ["n1"])) == (
        "digests.food: the sentence about 做饭 names 平台笔记本, but the meal kit cooked that day was none")


def test_sentences_end_at_full_width_terminators():
    """句以。！？结束，末尾没有标点的剩余也算一句；半角的 ! 与 ? 不切句。"""
    food = [_row("n1", 核验=["送达平台笔记本"]), _row("n2", 核验=["做饭，用收到的扑克牌"])]
    inputs = _inputs(food=food, cooked=("扑克牌",), kits=frozenset({"扑克牌", "平台笔记本"}))
    problem = "digests.food: the sentence about 做饭 names 平台笔记本, but the meal kit cooked that day was 扑克牌"
    for text in ("做饭用了扑克牌。收到了平台笔记本", "做饭用了扑克牌！收到了平台笔记本", "做饭用了扑克牌？收到了平台笔记本",
                 "收到了平台笔记本。做饭用了扑克牌"):
        assert _problems(inputs, food=(text, ["n1", "n2"])) is None, text
    for text in ("做饭用了扑克牌!收到了平台笔记本", "做饭用了扑克牌?收到了平台笔记本"):
        assert _problems(inputs, food=(text, ["n1", "n2"])) == problem, text


def _life_tokens(text: str, *rows: DigestRow, food=()) -> str | None:
    """生活类只有给定行时这段正文的错误文字。"""
    return _problems(_inputs(food=food, life=rows), {"food": DigestParagraph(text="嗯。", refs=["n0"])} if food else {},
                     life=(text, [rows[0].number]))


def test_number_tokens_match_complete_forms():
    """数字记号按完整写法整段比较、不规范化；记号对每个字符串值分别提取；其余阿拉伯数字串各成一个记号。"""
    row = _row("n1", 时刻="08:30", 核验=["花了18.3元", "买了1份"])
    assert _life_tokens("08:30出门，花了18.3元，买了1份。", row) is None
    assert _life_tokens("8:30出门。", row) == "digests.life: 8:30 does not appear in the life input"
    assert _life_tokens("花了18.3 元。", row) == "digests.life: 18.3 元 does not appear in the life input"
    assert _life_tokens("买了2份。", row) == "digests.life: 2份 does not appear in the life input"
    other = (_row("n0", 时刻="12:00", 核验=["送达柠檬茶"]),)
    assert _life_tokens("12:00吃饭。", row, food=other) == "digests.life: 12:00 does not appear in the life input"
    split = _row("n1", 核验=["等了10", "份量很足"])
    assert _life_tokens("吃了10份。", split) == "digests.life: 10份 does not appear in the life input"
    line = _row("n1", 自述=["正文：坐3号线回家。"])
    assert _life_tokens("坐了3号线。", line) is None
    month = _inputs(life=[_row("d1", 日期="2026-10-09", 回顾="睡觉。"), _row("d2", 日期="2026-10-10", 未整理=True)],
                    tier="month")
    assert _problems(month, life=("2026-10-09那天睡得早。", ["d1"])) is None
    assert _problems(month, life=("2026-10-10那天没整理出来。", ["d2"])) is None
    assert _problems(month, life=("10月9日那天睡得早。", ["d1"])) == (
        "digests.life: 10月9日 does not appear in the life input")
    year = _inputs(life=[_row("m1", 月份="2026-03", 回顾="睡觉。")], tier="year")
    assert _problems(year, life=("2026-03那个月睡得早。", ["m1"])) is None
    food = _inputs(food=[_row("n1", 时刻="10:30", 核验=["送达柠檬茶"])])
    head = "digests.food: "
    assert _problems(food, food=("吃了2顿。", ["n1"])) == head + "2 does not appear in the food input"
    assert _problems(food, food=("10点出门。", ["n1"])) == head + "10 does not appear in the food input"
    assert _problems(food, food=("10-12点出门。", ["n1"])) == (
        head + "10 does not appear in the food input; " + head + "12 does not appear in the food input")


def test_numbers_must_share_an_input_record_with_a_named_object():
    """写到目录名称的句子里，每个记号都要与这句的某个名称出自同一行；句中没有名称时只查记号存在。"""
    food = [_row("n1", 时刻="08:36", 核验=["买柠檬茶1份，17元"]), _row("n2", 时刻="12:10", 核验=["送达牛肉面"])]
    inputs = _inputs(food=food)
    assert _problems(inputs, food=("08:36买了柠檬茶，12:10收到牛肉面。", ["n1", "n2"])) is None
    assert _problems(inputs, food=("12:10喝了柠檬茶，后来收到牛肉面。", ["n1", "n2"])) is None
    assert _problems(inputs, food=("12:10喝了柠檬茶。收到牛肉面。", ["n1", "n2"])) == (
        "digests.food: 12:10 does not appear in the same input record as a name in its sentence")
    assert _problems(inputs, food=("12:10到了。收到柠檬茶和牛肉面。", ["n1", "n2"])) is None


@pytest.mark.parametrize("tier", ["month", "year"])
def test_count_lines_must_be_copied_whole(tier):
    """月、年回顾里「N 次」只能出现在照抄整行的次数统计里：单写「3 次」报次数照抄问题（它本身在统计行里，数字守卫
    不报），照抄整行通过；日回顾不跑这一守卫，同样的「3 次」只按数字守卫报。"""
    number, key = {"month": ("d1", "日期"), "year": ("m1", "月份")}[tier]
    row = _row(number, **{key: "2026-01-05" if tier == "month" else "2026-01"}, 回顾="在家吃面。")
    inputs = _inputs(food=[row], tier=tier, counts={"food": ["牛肉面 3 次", "柠檬茶 1 次"]})
    copied = "digests.food: the count 3 次 must be copied with its whole count line"
    assert _problems(inputs, food=("这段时间吃了 3 次。", [number])) == copied
    assert _problems(inputs, food=("吃得最多的是牛肉面 3 次。", [number])) is None
    assert _problems(inputs, food=("牛肉面 3 次，柠檬茶 1 次。", [number])) is None
    assert _problems(inputs, food=("牛肉面 3 次，后来又吃了 3 次。", [number])) == copied
    day = _inputs(food=[_row("n1", 时刻="12:00", 核验=["送达牛肉面"])])
    assert _problems(day, food=("这一天吃了 3 次。", ["n1"])) == "digests.food: 3 次 does not appear in the food input"


def test_problems_are_joined_in_category_and_guard_order():
    """多类多守卫同时不合：按类别次序、再按守卫表次序，用 "; " 连接。"""
    inputs = _inputs(food=[_row("n1", 核验=["送达柠檬茶"])], work=[_row("n2", 核验=["到公司"])])
    assert _problems(inputs, food=("8:30去五里公园。", []), life=("嗯。", ["n1"])) == "; ".join((
        "digests.food.refs must cite at least one record number",
        "digests.food: the name 五里公园 does not appear in the food input",
        "digests.food: 8:30 does not appear in the food input",
        "digests.food: 8:30 does not appear in the same input record as a name in its sentence",
        "digests.work must not be null: the input has work records",
        "digests.life must be null: the input has no life records"))


def test_accepted_categories_survive_later_attempts():
    """第一次 food 通过、work 不通过；第二次 food 不合格、work 通过：两类都按各自通过的那次接受，第二次没有错误。"""
    inputs = _inputs(food=[_row("n1", 核验=["送达柠檬茶"])], work=[_row("n2", 核验=["到公司"])])
    kept, error = check_attempt(inputs, _answer(food=("收到了柠檬茶。", ["n1"]), work=("", ["n2"])), {})
    assert set(kept) == {"food"} and str(error).startswith("digests.work.text must not be empty")
    kept, error = check_attempt(inputs, _answer(food=("", ["n1"]), work=("上班了。", ["n2"])), kept)
    assert error is None
    assert (kept["food"].text, kept["work"].text) == ("收到了柠檬茶。", "上班了。")


def test_outcome_marks_unaccepted_categories_as_fallback():
    """用尽后有输入而没被接受的类为 null 且按 food、work、life 次序列入 fallback；没有输入的类为 null 且不列入。"""
    rows = {key: [_row(number, 核验=["开始睡觉"])] for key, number in (("food", "n1"), ("work", "n2"), ("life", "n3"))}
    outcome = digest_outcome(_inputs(**rows), {"work": DigestParagraph(text="上班了。", refs=["n2"])})
    assert isinstance(outcome, DigestOutcome) and outcome.period == DigestPeriod("day", "2026-01-05")
    assert dict(outcome.digests) == {"food": None, "life": None, "work": {
        "text": "上班了。", "source_event_ids": ["event-n2"], "cited_event_ids": ["event-n2"]}}
    assert outcome.fallback == ("food", "life")
    partial = digest_outcome(_inputs(food=rows["food"]), {"food": DigestParagraph(text="嗯。", refs=["n1"])})
    assert partial.digests["work"] is None and partial.digests["life"] is None and partial.fallback == ()


def test_outcome_sources_and_citations_follow_rows():
    """来源是这一类各行背后的事件按行序去重，依据是 refs 各行背后的事件按 refs 次序去重、是来源的子集；月回顾的两者是
    日回顾事件身份。"""
    food = [_row("n1", ("a", "b"), 核验=["送达柠檬茶"]), _row("n2", ("b", "c"), 核验=["送达牛肉面"])]
    outcome = digest_outcome(_inputs(food=food), {"food": DigestParagraph(text="嗯。", refs=["n2", "n1", "n2"])})
    assert outcome.digests["food"]["source_event_ids"] == ["a", "b", "c"]
    assert outcome.digests["food"]["cited_event_ids"] == ["b", "c", "a"]
    month = [_row("d1", ("day-digest-1",), 日期="2026-01-05", 回顾="睡了。"),
             _row("d2", ("day-digest-2",), 日期="2026-01-06", 未整理=True)]
    outcome = digest_outcome(_inputs(life=month, tier="month"), {"life": DigestParagraph(text="嗯。", refs=["d2"])})
    assert outcome.digests["life"] == {"text": "嗯。", "source_event_ids": ["day-digest-1", "day-digest-2"],
                                       "cited_event_ids": ["day-digest-2"]}


def test_contains_hex_identity_is_shared_with_memory_text():
    """回顾正文与主观记忆文字对十六进制身份串的判定是同一条规则。"""
    for text in ("今天吃了饭", "deadbeef", "订单 0123456789abcdef", "DEADBEE", "7654321", "a1b2c3d4e"):
        try:
            validate_memory_text(text)
        except ValueError as error:
            refused = "hexadecimal" in str(error)
        else:
            refused = False
        assert contains_hex_identity(text) is refused, text
    assert contains_hex_identity("deadbeef") and not contains_hex_identity("DEADBEE")

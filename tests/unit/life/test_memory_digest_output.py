"""日、月、年回顾的请求、提示与输出守卫：都是纯函数，DigestInputs 由测试手工构造（docs/design/06-agent.md「日、月、年回顾」）。

第四批裁定之后回顾是日记式的回忆，输出只查格式与长度：守卫表只有正文非空、正文长度、依据非空、依据归属四行，正文写的名称、
时刻、价钱、日期、次数、十六进制串与有没有中文都不影响接受。
"""

from __future__ import annotations

import dataclasses
import json
import logging

import pytest

from tests.life.conftest import fixture_goods, memory_digest_response
from worldmock.configuration.inputs.models import ModelMetadata
from worldmock.simulation_engine.evidence import memory_digest
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

#: 本章「提示原文」的日回顾模板（日记式，逐字写死）。
_DAY_PROMPT = (
    "你替模拟世界里的一个人物写他昨天的日记。输入按饮食、工作、生活三类给出他昨天的记录：「核验」是环境确认的经过，「当时查到」是他当时查询看到的结果，「自述」是他当时写下的话（「名称：」是收尾或暂停的总结，「进展·」是中途写的进展，"
    "「看法·」是看法，「记住：」是想记住的事，「正文：」是回复正文），每条带编号。每类写一段不超过 200 字的日记，用他的第一人称，像人回想昨天那样只记要紧的事和当时的感受，不记流水账：经过以核验为准，挑要紧的写，吃了什么、"
    "做了什么、去了哪里、和谁打了交道，这些东西写出名字；时间说早上、中午、下午、晚上，不写几点几分；价钱、数量和花了多久只在对他要紧时顺口带一句，比如「有点贵」「等了挺久」，不逐项列出；感受和看法以自述为准，写他喜不喜欢、累不累、"
    "还想不想再来、打算保持还是改掉，不替他编没写过的感受。只凭昨天的记录写，不补造没发生的事或别人说的话，不拿别的日子作比较，不写编号、订单号或其他内部标识。在 refs 里写所依据的记录编号，至少一个。"
    "某一类没有记录时写 null。只调用一次 submit_memory_digest。"
)
#: 本章「提示原文」的月回顾模板（逐字写死）。
_MONTH_PROMPT = (
    "你替模拟世界里的一个人物把他上个月的日记整理成这个月的回忆。输入按饮食、工作、生活三类给出那个月每一天的日回顾，每段带日期和编号，没整理出来的日子写着「未整理」；另附代码从事件算出的次数统计，让你知道哪些事常有。"
    "每类写一段不超过 800 字的话，用他的第一人称，像人回想上个月那样写：这类事有什么规律和习惯，常吃什么、常做什么、常去哪里，有什么变化，哪些事值得记住；他对具体的东西、地方、人和做法的看法和打算，喜欢的、不喜欢的、"
    "说了要保持或要改掉的，后来有没有变。时间说月初、月中、月底、某个周末这样的说法，次数说几乎每天、常常、偶尔一两次这样的说法，不照抄日期和统计行。只凭输入写，不补造输入里没有的经过、看法或别人说的话，不写编号或其他内部标识。"
    "在 refs 里写所依据的日回顾编号，至少一个。某一类没有输入时写 null。只调用一次 submit_memory_digest。"
)
#: 本章「提示原文」的年回顾模板（逐字写死）。
_YEAR_PROMPT = (
    "你替模拟世界里的一个人物把他上一年的月回顾整理成这一年的回忆。输入按饮食、工作、生活三类给出那一年每个月的月回顾，每段带月份和编号，没整理出来的月份写着「未整理」；另附代码从事件算出的全年次数统计，让你知道哪些事常有。"
    "每类写一段不超过 2000 字的话，用他的第一人称，像人回想过去一年那样写：这一年这类事固定下来的习惯、口味和做法，哪些看法后来变了、大约什么时候变的，到年底还成立的看法和打算，哪些事值得记住。时间说年初、春天、几月份、"
    "年底这样的说法，次数说几乎每天、常常、偶尔这样的说法，不照抄统计行。只凭输入写，不补造输入里没有的经过、看法或别人说的话，不写编号或其他内部标识。在 refs 里写所依据的月回顾编号，至少一个。某一类没有输入时写 null。"
    "只调用一次 submit_memory_digest。"
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
    """一行输入：编号与给定的键，背后的事件缺省是 event-{编号}。"""
    return DigestRow(number, {"编号": number, **fields}, sources or (f"event-{number}",))


def _inputs(food=(), work=(), life=(), tier="day", counts=None) -> DigestInputs:
    """手工构造的一期输入：日回顾带人物卡片，月、年回顾的类别值带下一级回顾与次数统计（统计行只在用户消息里）。"""
    rows = {"food": tuple(food), "work": tuple(work), "life": tuple(life)}
    lower = {"month": "日回顾", "year": "月回顾"}.get(tier)
    payload = {"人物卡片": "产品经理，32岁，周末常去五里公园。"} if tier == "day" else {}
    for key, name in _KEYS:
        lines = [row.payload for row in rows[key]]
        payload[name] = lines if tier == "day" else {lower: lines, "次数统计": list((counts or {}).get(key, ()))}
    period = DigestPeriod(tier, {"day": "2026-01-05", "month": "2026-01", "year": "2026"}[tier])
    return DigestInputs(period, payload, rows)


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
    ("day", "", "digests.life.text must not be empty"),
    ("day", " \n ", "digests.life.text must not be empty"),
], ids=["day-200", "day-201", "month-800", "month-801", "year-2000", "year-2001", "empty", "blank"])
def test_text_guards(tier, text, message):
    """正文非空、正文 len 不超过这一级的上限：恰在上限时通过，多一字时报正文长度；空串与全空白只报正文非空。"""
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


#: 日回顾饮食类的一行输入：只有柠檬茶，时刻 08:28，价钱 17 元。
_FOOD = _row("n1", 时刻="08:28", 核验=["买柠檬茶1份，17元", "08:28 送达柠檬茶"], 自述=["看法·柠檬茶：好喝。"])
#: 月回顾饮食类的一行输入与次数统计。
_MONTH_FOOD = _row("d1", 日期="2026-01-05", 回顾="早上喝了柠檬茶，挺好喝。")


@pytest.mark.parametrize("tier,text", [
    ("day", "早上8:09喝了柠檬茶，挺解渴。"),
    ("day", "柠檬茶35元，有点贵，不过好喝。"),
    ("day", "10月9日早上喝了柠檬茶。"),
    ("month", "这个月柠檬茶喝了5 次，几乎每天都想喝。"),
    ("day", "中午吃了牛肉面，味道一般。"),
    ("day", "订单a1b2c3d4e5的柠檬茶到了。"),
    ("day", "ok"),
], ids=["clock-time", "price", "date", "count", "absent-name", "hex", "no-chinese"])
def test_text_within_the_limit_is_accepted_whatever_it_mentions(tier, text):
    """不超过字数上限、refs 都是这一类编号的一段，正文写了输入里没有的时刻、价钱、日期、次数、商品名、十六进制串，或全文
    没有中文：这一类照样被接受，错误为 None，登记的正文逐字是原文（守卫表只有四行）。"""
    inputs = (_inputs(food=[_FOOD]) if tier == "day"
              else _inputs(food=[_MONTH_FOOD], tier="month", counts={"food": ["柠檬茶 1 次"]}))
    number = inputs.rows["food"][0].number
    message = json.dumps(inputs.payload, ensure_ascii=False)
    assert not any(mention in message for mention in ("8:09", "35元", "10月9日", "5 次", "牛肉面", "a1b2c3d4e5"))
    assert "牛肉面" in fixture_goods() and contains_hex_identity("a1b2c3d4e5")
    kept, error = check_attempt(inputs, _answer(food=(text, [number])), {})
    assert error is None and kept["food"].text == text
    assert digest_outcome(inputs, kept).digests["food"]["text"] == text


def test_removed_guard_names_no_longer_exist():
    """已撤销守卫的数字记号与数量单位不在模块里；输入只剩期次、用户消息与行，行只剩编号、那一行与来源。"""
    assert not hasattr(memory_digest, "NUMBER_TOKEN") and not hasattr(memory_digest, "QUANTITY_UNITS")
    assert [item.name for item in dataclasses.fields(DigestInputs)] == ["period", "payload", "rows"]
    assert [item.name for item in dataclasses.fields(DigestRow)] == ["number", "payload", "sources"]
    removed = {"names", "goods_names", "kit_names", "food_required", "kits_cooked", "counts", "text", "verified"}
    assert removed.isdisjoint(item.name for kind in (DigestInputs, DigestRow) for item in dataclasses.fields(kind))


def test_problems_are_joined_in_category_and_guard_order():
    """日回顾三类同时不合：按类别次序、再按守卫表次序，用 "; " 连接；正文里输入没有的时刻、价钱与商品名不报问题。"""
    inputs = _inputs(food=[_row("n1", 核验=["送达柠檬茶"])], work=[_row("n2", 核验=["到公司"])],
                     life=[_row("n3", 核验=["开始睡觉"])])
    food = "早上8:09买了牛肉面，35元。"
    food += "我" * (201 - len(food))
    assert _problems(inputs, food=(food, ["n1", "n2"]), work=(" \n ", [])) == "; ".join((
        "digests.food.text has 201 characters; the day digest limit is 200",
        "digests.food.refs: n2 is not a food record number",
        "digests.work.text must not be empty",
        "digests.work.refs must cite at least one record number",
        "digests.life must not be null: the input has life records"))


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
    """contains_hex_identity 与主观记忆文字的校验对同一组字符串判定一致；回顾的守卫不用它（见上面的 [hex]）。"""
    for text in ("今天吃了饭", "deadbeef", "订单 0123456789abcdef", "DEADBEE", "7654321", "a1b2c3d4e"):
        try:
            validate_memory_text(text)
        except ValueError as error:
            refused = "hexadecimal" in str(error)
        else:
            refused = False
        assert contains_hex_identity(text) is refused, text
    assert contains_hex_identity("deadbeef") and not contains_hex_identity("DEADBEE")

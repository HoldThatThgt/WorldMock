"""@brief 就寝反思经唯一严格工具提交的验收证据（docs/dev/SPEC-as2-integration.md#errands-workday-运行修复入库-就寝反思经严格工具提交，裁决 R1、R2）。

三类证据：

1. 经真实记忆运行时 WorldMockMemoryRuntime.after_step（就寝边界）：恰一次 submit_reflection、一至五条、引用与正文的本地
   校验；每种合同违约都附回英文校验错误重试；首次之外至多三次，耗尽即运行故障，此前不写入任何洞见；传输异常不进校验
   重试；没有可反思的记忆时不调用模型；实际发送的 system 文案与接口合同登记块、模块常量逐字相同。
2. 经真实引擎、SQLite、LifeEnv 与直接导入的 LifeAgent 跑一次醒来决策：反思的每次请求与响应照常留证（思考关闭、不带
   purpose、不是行动请求），校验错误随下一次请求的附回消息留证；耗尽时本轮中止、没有 memory_updated，最后一次错误写进
   运行故障原因。
3. 只调用 reflection_insights、reflection_tool、reflection_correction 的用例是补充证据，不单独支撑台账行。
"""

import copy
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.documentation_refs import section_text

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from worldmock.agents.life_reflection import MEMORY_REFLECTION_MAX_INSIGHTS
from worldmock.agents import life_reflection
from custom.agents.life_agent import LifeAgent
from worldmock.agents.life_reflection import (REFLECTION_PROMPT, REFLECTION_VALIDATION_RETRIES, ReflectionOutputError,
                                           ReflectionRequest, reflection_correction, reflection_insights,
                                           reflection_tool)
from custom.envs.life_env import LifeEnv
from tests.integration.test_life_agent_protocol import _ModelResponse, _MultipleModelResponse, _TextResponse
from tests.integration.test_verified_memory import _TIME, _concluded, _runtime
from tests.engine_fixture import (engine_factory)  # noqa: F401
from tests.life.conftest import (context, stage_day_plan)  # noqa: F401
from tests.life.test_memory_evidence import _started, _until_ended
from worldmock.errors import ProtocolError
from worldmock.life.day_planning import PLAN_DAY_VALIDATION_RETRIES
from worldmock.life.decision_start import is_action_request
from worldmock.life.models import deepseek_strict_schema

ROOT = Path(__file__).resolve().parents[2]
_TOOL = "submit_reflection"
_CORRECTED = "我想把和朋友的联系放在心上。"
#: 条目身份的真实形态（AS2 记忆存储生成；其中八位数字命中十六进制身份校验）。
_KNOWN = "m_00000060_000001"
#: 五条互不相似的洞见（相似的同类条目会被存储合并）。
_FIVE = ("我其实很在意家里整不整洁。", "和家人的联系让我踏实。", "工作上的承诺我都记在心里。",
         "独处的时候我最放松。", "我想让日子过得更有规律。")
_ONE_CALL = "response must contain exactly one submit_reflection tool call"
_COUNT = "insights: Value error, Reflection must contain one to five insights"
_REFS = "insights.0.refs: Value error, Reflection references must be nonempty and unique"


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    """脚本模型使用固定供应商窗口，不请求外部模型元数据。"""
    from worldmock.life import prompt_capacity

    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1_048_576)


def _insight(ref: str, text: str = "我想休息。") -> dict:
    """一条洞见：正文与引用的一条输入记忆。"""
    return {"insight": text, "refs": [ref]}


def _submit(insights: list, name: str = _TOOL) -> _ModelResponse:
    """只含一次工具调用的原生响应；正文为空。"""
    return _ModelResponse("call-reflection", name, json.dumps({"insights": insights}, ensure_ascii=False))


def _valid(known: str) -> _ModelResponse:
    """合乎合同的一条反思：引用输入里的记忆。"""
    return _submit([_insight(known, _CORRECTED)])


def _free_text(known: str) -> _TextResponse:
    """旧通道的应答：正文是一份本来合法的洞见数组，没有工具调用。"""
    return _TextResponse(json.dumps([_insight(known, "自由文本里的洞见。")], ensure_ascii=False))


def _twice(known: str) -> _MultipleModelResponse:
    """同一响应里两次 submit_reflection，各自的参数本来合法。"""
    arguments = json.dumps({"insights": [_insight(known)]}, ensure_ascii=False)
    return _MultipleModelResponse([("call-a", _TOOL, arguments), ("call-b", _TOOL, arguments)])


#: 两个选择各自带的那条洞见的正文：哪个选择都不该被读取，也不回显给模型。
_TWO_CHOICE_TEXT = "两个选择里的洞见。"


def _two_choices(known: str) -> _ModelResponse:
    """同一响应里两个选择，各带一次本来合法的 submit_reflection 调用（合起来是两次调用，不是恰好一次）。"""
    response = _submit([_insight(known, _TWO_CHOICE_TEXT)])
    second = copy.deepcopy(response.choices[0])
    second.message.tool_calls[0].id = "call-reflection-b"
    response.choices = [response.choices[0], second]
    return response


#: 每种合同违约：由输入记忆身份构造不合法响应的函数，与附回模型的英文校验错误里必有的一段。
_VIOLATIONS = {
    "free_text": (_free_text, _ONE_CALL),
    "other_tool": (lambda known: _submit([_insight(known)], name="memory_recent"),
                   "the only tool call must be submit_reflection"),
    "two_calls": (_twice, _ONE_CALL),
    "arguments_not_json": (lambda known: _ModelResponse("call-reflection", _TOOL, "not json"),
                           "arguments: Invalid JSON"),
    "no_insights": (lambda known: _submit([]), _COUNT),
    "six_insights": (lambda known: _submit([_insight(known, f"第{index}条想法。") for index in range(6)]), _COUNT),
    "unknown_ref": (lambda known: _submit([_insight("m_unknown")]),
                    "insights.0.refs: memory ids ['m_unknown'] are not among the supplied episodes"),
    "known_and_unknown_refs": (lambda known: _submit([{"insight": "我想休息。", "refs": [known, "m_unknown"]}]),
                               "insights.0.refs: memory ids ['m_unknown'] are not among the supplied episodes"),
    "empty_refs": (lambda known: _submit([{"insight": "我想休息。", "refs": []}]), _REFS),
    "duplicate_refs": (lambda known: _submit([{"insight": "我想休息。", "refs": [known, known]}]), _REFS),
    "blank_ref": (lambda known: _submit([{"insight": "我想休息。", "refs": [known, "  "]}]), _REFS),
    "blank_insight": (lambda known: _submit([_insight(known, "   ")]), "insights.0.insight: String should match"),
    "text_over_1000_characters": (lambda known: _submit([_insight(known, "想" * 1001)]),
                                  "insights.0.insight: Value error, memory text exceeds 1000 characters"),
    "identity_in_text": (lambda known: _submit([_insight(known, f"我还记得 {known} 那件事。")]),
                         "insights.0.insight: Value error, memory text contains a hexadecimal identifier"),
    "extra_key": (lambda known: _submit([{**_insight(known), "importance": 0.9}]),
                  "insights.0.importance: Extra inputs are not permitted"),
    "missing_refs": (lambda known: _submit([{"insight": "我想休息。"}]), "insights.0.refs: Field required"),
    "ref_is_not_text": (lambda known: _submit([{"insight": "我想休息。", "refs": [7]}]),
                        "insights.0.refs.0: Input should be a valid string"),
    # 第一条洞见的引用合法、未知身份在第二条：每条洞见的引用都要属于本次输入，定位到出错的那一条。
    "unknown_ref_in_later_insight": (
        lambda known: _submit([_insight(known, "我想休息。"), _insight("m_unknown", "我想早点睡。")]),
        "insights.1.refs: memory ids ['m_unknown'] are not among the supplied episodes"),
    # 八位以上十六进制串不分大小写：全大写与大小写混写（恰好八位）的身份串同样不进记忆正文。
    "upper_case_identity_in_text": (lambda known: _submit([_insight(known, "我还记得 3E71A031BB3D 那件事。")]),
                                    "insights.0.insight: Value error, memory text contains a hexadecimal identifier"),
    "mixed_case_identity_in_text": (lambda known: _submit([_insight(known, "我还记得 3e71A031 那件事。")]),
                                    "insights.0.insight: Value error, memory text contains a hexadecimal identifier"),
}


def _known_id(request: dict) -> str:
    """本次反思输入里第一条记忆的身份（取自实际请求的原始 user 消息）。"""
    user = request["messages"][1]["content"]
    body = user.split("<memory_reflection_input>", 1)[1].split("</memory_reflection_input>", 1)[0]
    return json.loads(body)["episodes"][0]["id"]


def _tool_names(request: dict) -> list[str]:
    """一次模型请求实际开放的工具名。"""
    return [item["function"]["name"] for item in request.get("tools", [])]


class _ReflectionScript:
    """只替换远程模型传输：就寝反思的请求按脚本依次作答并原样留存；不带工具的请求是主观归纳，回一句话。

    脚本的每一项是「由输入记忆身份构造响应的函数」或一个要抛出的异常（模拟传输失败）。脚本用尽后再被调用即失败：
    校验重试有上限，多出来的调用不该发生。
    """

    def __init__(self, *replies) -> None:
        self.replies = list(replies)
        self.requests: list[dict] = []
        self.responses: list = []
        self.consolidations = 0

    async def __call__(self, **request):
        """按请求的工具分流并返回下一个脚本响应。"""
        if not _tool_names(request):
            self.consolidations += 1
            return _TextResponse("我想和朋友保持联系。")
        self.requests.append(copy.deepcopy(request))
        if not self.replies:
            pytest.fail(f"reflection model was called {len(self.requests)} times, beyond its script")
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        self.responses.append(reply(_known_id(request)))
        return self.responses[-1]


async def _sleep_boundary(runtime):
    """就寝边界的一步：收尾写下一条看法，随后主观归纳与就寝反思。"""
    return await runtime.after_step(
        tick=60, t=_TIME, step_count=1, sleep_boundary=True,
        finish_memories=_concluded("想多联系朋友", "relationship", 0.6, about="朋友"))


def _reflections(runtime) -> list[dict]:
    """记忆存储里来源为就寝反思的条目。"""
    return [item for item in runtime.store.iter_episodes() if item["source"] == "reflection"]


def _feedback(request: dict) -> dict:
    """重试请求末尾那条附回消息的内容。"""
    message = request["messages"][-1]
    assert message["role"] == "user"
    return json.loads(message["content"])


def _echo(response) -> list[dict]:
    """一次响应里各工具调用的名称与参数（附回消息回显的形状）。"""
    return [{"name": call.function.name, "arguments": call.function.arguments}
            for call in response.choices[0].message.tool_calls]


def _assert_retry_request(original: dict, retry: dict, previous, expected_error: str) -> None:
    """重试请求：工具与强制调用不变，前两条就是原始消息，其后只有一条附回消息（不累积），带上一次的工具调用与英文错误。"""
    assert retry["tools"] == original["tools"] == [reflection_tool()]
    assert retry["tool_choice"] == original["tool_choice"] == "required"
    assert len(retry["messages"]) == 3 and retry["messages"][:2] == original["messages"]
    feedback = _feedback(retry)
    assert set(feedback) == {"PREVIOUS_INVALID_RESPONSE", "VALIDATION_ERROR", "INSTRUCTION"}
    assert feedback["PREVIOUS_INVALID_RESPONSE"] == _echo(previous)
    assert expected_error in feedback["VALIDATION_ERROR"]
    assert feedback["INSTRUCTION"] == "Return a complete new submit_reflection tool call using the unchanged schema."


@pytest.mark.asyncio
async def test_runtime_submits_reflection_through_the_only_strict_tool(tmp_path) -> None:
    """就寝边界的反思只带 submit_reflection 一个严格工具并强制调用，请求不带 purpose、不是行动请求；一次调用提交五条洞见
    （上限），不同洞见引用同一条记忆；通过后的条目固定为主观的 reflection、来源 reflection、重要度 0.8、无关键词。"""
    texts = list(_FIVE)
    script = _ReflectionScript(lambda known: _submit([_insight(known, text) for text in texts]))
    runtime, _ = _runtime(tmp_path, script)

    outcome = await _sleep_boundary(runtime)

    [request] = script.requests
    assert script.consolidations == 1
    assert request["tools"] == [reflection_tool()] and request["tool_choice"] == "required"
    assert request["stream"] is False and "purpose" not in request
    assert is_action_request(request) is False
    assert [message["role"] for message in request["messages"]] == ["system", "user"]
    known = _known_id(request)
    assert [item["source"] for item in runtime.store.iter_episodes() if item["id"] == known] == ["activity_conclusion"]
    stored = _reflections(runtime)
    assert [item["text"] for item in stored] == texts
    assert all((item["type"], item["importance"], item["keywords"], item["epistemic_status"], item["refs"])
               == ("reflection", 0.8, [], "subjective", [known]) for item in stored)
    assert {item["id"] for item in stored} <= {item["id"] for item in outcome.created_episodes}


@pytest.mark.asyncio
@pytest.mark.parametrize("violation", sorted(_VIOLATIONS))
async def test_runtime_feeds_each_contract_violation_back_and_stores_only_the_corrected_reflection(
        tmp_path, violation: str) -> None:
    """每种合同违约（自由文本、其他工具、两次调用、参数不是 JSON、零条或六条、未知引用、引用为空、重复或空白、正文空白、
    超过一千字符或含条目身份、多余或缺少的键、引用不是文本）都不被接受：运行时附回英文校验错误重新请求一次，第二次合法
    后只写入改正后的那一条；不合法的那一次没有任何内容进入记忆，自由文本的正文也不回显给模型。"""
    invalid, expected_error = _VIOLATIONS[violation]
    script = _ReflectionScript(invalid, _valid)
    runtime, _ = _runtime(tmp_path, script)

    outcome = await _sleep_boundary(runtime)

    assert len(script.requests) == 2
    first, retry = script.requests
    assert len(first["messages"]) == 2
    _assert_retry_request(first, retry, script.responses[0], expected_error)
    assert "自由文本里的洞见" not in retry["messages"][-1]["content"]
    stored = _reflections(runtime)
    assert [(item["text"], item["refs"]) for item in stored] == [(_CORRECTED, [_known_id(first)])]
    assert stored[0]["id"] in {item["id"] for item in outcome.created_episodes}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["padded_ref", "padded_twin_of_a_known_ref"])
async def test_runtime_rejects_a_padded_copy_of_a_supplied_identity(tmp_path, case: str) -> None:
    """引用必须就是本次输入的记忆身份本身（规格 1.1 校验第三条）：前后带空白的写法不是输入里的身份。单独写它，或与原身份
    并列（两个字符串不同、去掉空白却是同一条记忆，放行就会在一条洞见内把同一条记忆引用两次），都经真实记忆运行时附回
    「不属于本次输入」的英文错误重新请求，错误原样带出那个带空白的写法；只写入改正后的那一条，引用恰为该身份一次。"""
    def padded(known: str) -> str:
        """输入身份的带空白写法：单独出现时两侧都有空格，与原身份并列时只在末尾多一个空格。"""
        return f" {known} " if case == "padded_ref" else f"{known} "

    def invalid(known: str) -> _ModelResponse:
        """只含一条洞见的响应，其引用是带空白的写法（并列的用例里前面还有原身份）。"""
        refs = [padded(known)] if case == "padded_ref" else [known, padded(known)]
        return _submit([{"insight": "我想休息。", "refs": refs}])

    script = _ReflectionScript(invalid, _valid)
    runtime, _ = _runtime(tmp_path, script)

    await _sleep_boundary(runtime)

    assert len(script.requests) == 2
    first, retry = script.requests
    known = _known_id(first)
    expected_error = f"insights.0.refs: memory ids {[padded(known)]} are not among the supplied episodes"
    _assert_retry_request(first, retry, script.responses[0], expected_error)
    assert _feedback(retry)["VALIDATION_ERROR"] == expected_error
    assert [(item["text"], item["refs"]) for item in _reflections(runtime)] == [(_CORRECTED, [known])]


@pytest.mark.asyncio
async def test_runtime_rejects_a_response_with_two_choices_as_more_than_one_call(tmp_path) -> None:
    """响应带两个选择、各有一次本来合法的 submit_reflection 调用：合起来不是「恰好一次工具调用」（规格 1.1 校验第一条，
    多次调用不接受），运行时不取第一个选择了事。经真实记忆运行时附回「恰好一次」的英文错误重新请求，工具与强制调用不变、
    附回接在原始两条消息之后；回显为空列表，哪个选择的内容都不读、不回给模型；两个选择里的洞见都不进记忆，只写入改正
    后的那一条。"""
    script = _ReflectionScript(_two_choices, _valid)
    runtime, _ = _runtime(tmp_path, script)

    await _sleep_boundary(runtime)

    assert len(script.requests) == 2
    first, retry = script.requests
    assert len(script.responses[0].choices) == 2
    assert retry["tools"] == first["tools"] == [reflection_tool()]
    assert retry["tool_choice"] == first["tool_choice"] == "required"
    assert len(retry["messages"]) == 3 and retry["messages"][:2] == first["messages"]
    feedback = _feedback(retry)
    assert set(feedback) == {"PREVIOUS_INVALID_RESPONSE", "VALIDATION_ERROR", "INSTRUCTION"}
    assert feedback["VALIDATION_ERROR"] == _ONE_CALL and feedback["PREVIOUS_INVALID_RESPONSE"] == []
    assert _TWO_CHOICE_TEXT not in retry["messages"][-1]["content"]
    assert [(item["text"], item["refs"]) for item in _reflections(runtime)] == [(_CORRECTED, [_known_id(first)])]


@pytest.mark.asyncio
async def test_runtime_stops_with_a_run_fault_after_three_validation_retries_and_stores_no_reflection(
        tmp_path) -> None:
    """首次之外至多三次校验重试：四次都不合合同时恰好调用四次模型（第五次调用脚本直接失败），抛出运行故障，原因带最后
    一次校验错误原文；每次重试的附回都接在原始两条消息之后（消息数恒为三，不逐次累积），带的是紧邻上一次的错误；
    没有任何洞见写入，也没有降级为空反思，此前写下的收尾记忆仍在。"""
    names = ("free_text", "unknown_ref", "six_insights", "text_over_1000_characters")
    script = _ReflectionScript(*(_VIOLATIONS[name][0] for name in names))
    runtime, _ = _runtime(tmp_path, script)

    with pytest.raises(ProtocolError) as failure:
        await _sleep_boundary(runtime)

    last_error = _VIOLATIONS[names[-1]][1]
    assert str(failure.value) == f"memory reflection output remained invalid after three retries: {last_error}"
    assert type(failure.value.__cause__) is ReflectionOutputError and str(failure.value.__cause__) == last_error
    assert len(script.requests) == 4 and script.replies == []
    original = script.requests[0]
    assert len(original["messages"]) == 2
    for index in (1, 2, 3):
        _assert_retry_request(original, script.requests[index], script.responses[index - 1],
                              _VIOLATIONS[names[index - 1]][1])
    assert _VIOLATIONS[names[0]][1] not in script.requests[2]["messages"][-1]["content"]
    assert _reflections(runtime) == []
    assert [item["source"] for item in runtime.store.iter_episodes()] == ["activity_conclusion"]


@pytest.mark.asyncio
async def test_runtime_propagates_transport_failures_without_validation_retries(tmp_path) -> None:
    """传输异常原样上抛，不进校验重试：反思只被调用一次，脚本里排在后面的合法响应没有被取用，没有洞见写入。"""
    transport = OSError("injected reflection transport failure")
    script = _ReflectionScript(transport, _valid)
    runtime, _ = _runtime(tmp_path, script)

    with pytest.raises(OSError) as failure:
        await _sleep_boundary(runtime)

    assert failure.value is transport
    assert len(script.requests) == 1 and len(script.replies) == 1
    assert _reflections(runtime) == []


@pytest.mark.asyncio
async def test_runtime_makes_no_model_call_when_there_is_nothing_to_reflect_on(tmp_path) -> None:
    """没有可反思的主观记忆时不调用模型：就寝边界、没有收尾记忆、存储为空——任何模型调用都会让本用例失败。"""
    runtime, _ = _runtime(tmp_path)

    outcome = await runtime.after_step(tick=60, t=_TIME, step_count=1, finish_memories=[], sleep_boundary=True)

    assert outcome.created_episodes == () and outcome.merged_episodes == ()
    assert runtime.store.iter_episodes() == []


def _first_text_block(document: Path, lead: str) -> str:
    """文档里某个引导句之后的第一个 text 代码块。"""
    text = section_text(document.read_text(encoding="utf-8"), "contracts-model-request")
    block = re.search(r"```text\n(.*?)\n```", text.split(lead, 1)[1], flags=re.DOTALL)
    assert block is not None
    return block.group(1)


#: 规格 3.3 节的标题：其后第一个 text 块是阶段 2 追加在反思文案末尾的两句。
#: 阶段 2 追加的两句（docs/dev/SPEC-life-realism.md#errands-workday-提示信息分工-就寝反思文案补句），在文案里是不折行的一行。
_ERRAND_SENTENCES = ("Reflection does not register, finish or cancel errands. The supplied episodes are the person's "
                     "own notes, not verified results; do not state as a fact that something was obtained, eaten or "
                     "finished.")


@pytest.mark.asyncio
async def test_reflection_prompt_is_the_contract_text(tmp_path) -> None:
    """反思的 system 文案由 WorldMock 自持：经记忆运行时实际发出的 system、模块常量与接口合同「请求组成」的登记块三者
    逐字相同，由唯一合同登记完整正文与补句（docs/dev/SPEC-life-realism.md#errands-workday-提示信息分工-就寝反思文案补句，阶段 2 追加）；
    文案写的 one to five 与 AS2 的条数上限常量绑在一起（常量变而文案不变即失败）。"""
    script = _ReflectionScript(_valid)
    runtime, _ = _runtime(tmp_path, script)
    await _sleep_boundary(runtime)

    [request] = script.requests
    contract = _first_text_block(ROOT / "docs" / "CONTRACTS.md", "就寝反思的 system 文案（逐字）：")
    assert request["messages"][0] == {"role": "system", "content": REFLECTION_PROMPT}
    assert request["messages"][0]["content"] == REFLECTION_PROMPT == contract
    assert "Call submit_reflection exactly once with one to five insights." in REFLECTION_PROMPT
    assert MEMORY_REFLECTION_MAX_INSIGHTS == 5
    assert [line for line in REFLECTION_PROMPT.splitlines() if line[:2] in {"1.", "2.", "3."}] == [
        "1. 最近一段时间，哪些人和事对这个人影响最大？",
        "2. 这个人对自己、对身边的人有了哪些新的认识？",
        "3. 接下来这个人最应该关心或改变什么？"]
    assert request["messages"][1]["content"].startswith("<memory_reflection_input>")


@pytest.mark.asyncio
async def test_reflection_prompt_ends_with_the_errand_sentences(tmp_path) -> None:
    """就寝反思文案的补句（docs/dev/SPEC-life-realism.md#errands-workday-提示信息分工-就寝反思文案补句，按裁决 Q1 的写法）：经真实记忆运行时在就寝边界实际发出
    的 system 以空一行接两句结尾——反思不记下、不办成也不取消个人事务；输入的记忆是人物自己的记录、不是核验过的结果，
    不把取得、吃掉或办完写成事实。两句是不折行的一行，与规格 3.3 的文本块逐字相同；它前面逐行就是阶段 1 的文案（规格
    1.1 的文本块，原有各行不重新折行）；接口合同「请求组成」的登记块以同样的空行与这一行结尾。补句不借用「已结算」的
    说法，也不点名任何工具。"""
    script = _ReflectionScript(_valid)
    runtime, _ = _runtime(tmp_path, script)
    await _sleep_boundary(runtime)

    [request] = script.requests
    lines = request["messages"][0]["content"].splitlines()
    assert lines[-1] == _ERRAND_SENTENCES and lines[-2] == ""
    contract = _first_text_block(ROOT / "docs" / "CONTRACTS.md", "就寝反思的 system 文案（逐字）：")
    assert contract.splitlines() == lines and contract.splitlines()[-2:] == ["", _ERRAND_SENTENCES]
    assert "settle" not in _ERRAND_SENTENCES.lower() and "_" not in _ERRAND_SENTENCES
    assert _ERRAND_SENTENCES.count(". ") == 1 and _ERRAND_SENTENCES.endswith("finished.")


class _WakingModel:
    """醒来那一轮的脚本模型：行动请求结束刚睡完的那一觉并写下一条记忆；不带工具的请求是主观归纳；就寝反思按脚本作答。"""

    model_name = "deepseek-flash"

    def __init__(self, sleep_name: str, *replies) -> None:
        self.sleep_name = sleep_name
        self.reflection = _ReflectionScript(*replies)

    async def call(self, **request):
        """按请求的工具分流：submit_reflection、行动请求或主观归纳。"""
        names = _tool_names(request)
        if names == [_TOOL] or not names:
            return await self.reflection(**request)
        assert "start_activity" in names
        memory = {"type": "routine", "text": "这一觉睡得很沉。", "importance": 0.4, "keywords": ["睡眠"]}
        arguments = {"items": [{"activity_name": self.sleep_name, "summary": "睡醒了。", "memories": [memory],
                                "views": []}], "errands": []}
        return _ModelResponse("call-conclude", "conclude_activity", json.dumps(arguments, ensure_ascii=False))


async def _waking_decision(engine_factory, tmp_path, *replies) -> SimpleNamespace:
    """真实引擎上睡完一觉，开出含 wake_from_sleep 的那一轮决策，并备好真实 LifeEnv 与直接导入的 LifeAgent。"""
    engine = engine_factory()
    stage_day_plan(engine)
    slept = _started(engine, "睡觉", "sleep")
    _until_ended(engine, slept, "sleep")
    decision = context(engine, "woke")
    environment = LifeEnv()
    environment.engine = engine
    await environment.begin_decision_round((decision,))
    model = _WakingModel(engine.context.state.activities[slept].name, *replies)
    workspace = tmp_path / "people" / "agent_0001"
    LifeAgent.create(workspace, {"id": 1, "name": "Person 1", "occupation": "engineer"},
                     {"enable_memory": True, "enable_todo_list": False, "max_react_turns": 16})
    service = SimpleNamespace(env=environment, llm=SimpleNamespace(default=model), trace=None)
    agent = await LifeAgent.from_workspace(workspace, service)
    assert type(agent) is LifeAgent
    agent.bind_decision_context(decision)
    return SimpleNamespace(engine=engine, environment=environment, agent=agent, decision=decision,
                           model=model, slept=slept)


def _decision_events(run: SimpleNamespace, event_type: str) -> list:
    """库里属于醒来那次决策的某类事件（按事件序号）。"""
    events = run.engine.context.store.read_events(run.engine.context.store.last_event_seq)
    return [event for event in events
            if event.decision_id == run.decision.decision_id and event.event_type == event_type]


def _reflection_exchanges(run: SimpleNamespace) -> tuple[list, list]:
    """库里醒来那次决策的就寝反思请求事件，与按调用身份对上的响应事件。"""
    requests = [event for event in _decision_events(run, "model_request")
                if [tool["function"]["name"] for tool in event.data["request"].get("tools") or []] == [_TOOL]]
    calls = [event.data["call_id"] for event in requests]
    responses = [event for event in _decision_events(run, "model_response") if event.data["call_id"] in calls]
    return requests, responses


def _assert_reflection_request_events(run: SimpleNamespace, requests: list) -> None:
    """留证的反思请求：思考关闭、强制调用、不带 purpose、不是行动请求；这次决策仍只有随首个行动请求写入的一条
    decision_started，反思请求都排在已登记的动作之后。"""
    [started] = _decision_events(run, "decision_started")
    [action] = _decision_events(run, "action_requested")
    assert action.data["tool_name"] == "conclude_activity"
    for event in requests:
        request = event.data["request"]
        assert request["extra_body"] == {"thinking": {"type": "disabled"}}
        assert request["tool_choice"] == "required" and request["max_retries"] == 0
        assert not event.data.get("purpose") and "purpose" not in request
        assert is_action_request(request) is False
        assert started.event_seq < action.event_seq < event.event_seq


@pytest.mark.asyncio
async def test_exhausted_reflection_leaves_evidence_but_no_memory_update_in_the_run_database(
        engine_factory, tmp_path) -> None:
    """真实引擎、SQLite、LifeEnv 与直接导入的 LifeAgent：醒来那一轮世界请求已登记、收尾记忆已写入工作区之后，就寝反思
    四次都不合合同——人物步骤以运行故障中止；库里有这次决策的 action_requested、四条反思请求与四条响应（第二至四条请求
    的末条消息带上一次的校验错误），没有这次决策的 memory_updated；中止本轮后动作未结算，最后一次错误写进故障原因。"""
    names = ("free_text", "unknown_ref", "six_insights", "identity_in_text")
    run = await _waking_decision(engine_factory, tmp_path, *(_VIOLATIONS[name][0] for name in names))
    last_error = _VIOLATIONS[names[-1]][1]

    with pytest.raises(ProtocolError, match="remained invalid after three retries") as failure:
        await run.agent.step(0, run.decision.observed_at)

    assert last_error in str(failure.value)
    requests, responses = _reflection_exchanges(run)
    assert len(requests) == 4 and len(responses) == 4
    assert all(event.data["phase"] == "response" and event.data["error"] is None for event in responses)
    _assert_reflection_request_events(run, requests)
    original = requests[0].data["request"]
    assert len(original["messages"]) == 2 and original["messages"][0]["content"] == REFLECTION_PROMPT
    for index in (1, 2, 3):
        retry = requests[index].data["request"]
        assert len(retry["messages"]) == 3 and retry["messages"][:2] == original["messages"]
        assert _VIOLATIONS[names[index - 1]][1] in _feedback(retry)["VALIDATION_ERROR"]
    assert _decision_events(run, "memory_updated") == []
    assert _decision_events(run, "action_result") == []
    stored = run.agent._memory_runtime.store.iter_episodes()
    assert [item["source"] for item in stored] == ["activity_conclusion"]

    await run.environment.abort_decision_round(run.decision.round_id, str(failure.value))

    [result] = _decision_events(run, "action_result")
    assert (result.data["status"], result.data["error_code"]) == ("failed", "DECISION_ROUND_ABORTED")
    assert last_error in result.data["message"]
    assert _decision_events(run, "memory_updated") == []
    assert run.engine.context.state.activities[run.slept].concluded_at is None


@pytest.mark.asyncio
async def test_corrected_reflection_is_recorded_once_by_the_real_engine(engine_factory, tmp_path) -> None:
    """同一条真实路径上，第一次反思不合合同、第二次合法：人物步骤照常登记世界请求，库里有两条反思请求与两条响应，
    这次决策恰好一条 memory_updated，写入点为收尾与反思，快照里带来源 reflection 的只有改正后的那一条；本轮照常结算。"""
    run = await _waking_decision(engine_factory, tmp_path, _VIOLATIONS["free_text"][0], _valid)

    assert await run.agent.step(0, run.decision.observed_at) == "conclude_activity registered"

    requests, responses = _reflection_exchanges(run)
    assert len(requests) == 2 and len(responses) == 2
    _assert_reflection_request_events(run, requests)
    assert _ONE_CALL in _feedback(requests[1].data["request"])["VALIDATION_ERROR"]
    [update] = _decision_events(run, "memory_updated")
    assert update.data["trigger"] == ["activity_conclusion", "reflection"]
    reflected = [row for row in update.data["episodes"] if row.get("source") == "reflection"]
    assert [(row["type"], row["text"], row["epistemic_status"]) for row in reflected] == [
        ("reflection", _CORRECTED, "subjective")]
    [result] = await run.environment.commit_decision_round(run.decision.round_id)
    assert result.status == "completed"
    assert run.engine.context.state.activities[run.slept].concluded_at is not None


def test_reflection_tool_schema_is_inline_strict_and_bound_to_the_host_limit() -> None:
    """补充证据：工具声明 strict，参数就是 ReflectionRequest 的严格 Schema（生成后不改写），两层对象全部属性必填、封闭，
    序列化后没有引用定义；条数上限取宿主常量且为五。"""
    function = reflection_tool()["function"]
    assert (function["name"], function["strict"]) == (_TOOL, True)
    parameters = function["parameters"]
    assert parameters == deepseek_strict_schema(ReflectionRequest)
    serialized = json.dumps(reflection_tool())
    assert "$ref" not in serialized and "$defs" not in serialized
    items = parameters["properties"]["insights"]["items"]
    for node, keys in ((parameters, ["insights"]), (items, ["insight", "refs"])):
        assert node["type"] == "object" and node["additionalProperties"] is False
        assert node["required"] == keys == list(node["properties"])
    assert MEMORY_REFLECTION_MAX_INSIGHTS == 5
    assert len(_FIVE) == MEMORY_REFLECTION_MAX_INSIGHTS
    five = _submit([_insight(_KNOWN, text) for text in _FIVE])
    assert [item["text"] for item in reflection_insights(five, [{"id": _KNOWN}])] == list(_FIVE)


def test_reflection_records_only_valid_subjective_insights() -> None:
    """补充证据：工具参数中的洞见保留来源与引用，响应正文不读取；不同洞见可以引用同一条记忆。"""
    response = _submit([_insight("known", "我想保持规律作息"), _insight("known", "我想早点睡")])
    response.choices[0].message.content = "```json\nIgnored commentary\n```"
    assert reflection_insights(response, [{"id": "known"}, {"id": "other"}]) == [
        {"type": "reflection", "importance": 0.8, "keywords": [], "text": text, "source": "reflection",
         "refs": ["known"]} for text in ("我想保持规律作息", "我想早点睡")]


@pytest.mark.parametrize("violation", sorted(_VIOLATIONS))
def test_invalid_reflection_cannot_enter_memory(violation: str) -> None:
    """补充证据：每种合同违约都以 ReflectionOutputError（ValueError 的子类）拒绝，英文原因是一行、指出位置。"""
    invalid, expected_error = _VIOLATIONS[violation]
    with pytest.raises(ReflectionOutputError) as failure:
        reflection_insights(invalid(_KNOWN), [{"id": _KNOWN}])
    assert isinstance(failure.value, ValueError)
    assert expected_error in str(failure.value) and "\n" not in str(failure.value)


@pytest.mark.parametrize("response", [
    SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content='[{"insight":"我想休息","refs":["known"]}]', tool_calls=None))]),
    SimpleNamespace(choices=[]),
    SimpleNamespace(choices=None),
    SimpleNamespace(choices=[SimpleNamespace(message=None)]),
    SimpleNamespace(),
    _two_choices("known"),
], ids=["text_only", "no_choice", "choices_missing", "no_message", "no_choices_attribute", "two_choices"])
def test_reflection_requires_exactly_one_tool_call(response) -> None:
    """补充证据：没有工具调用的响应（纯文本、没有选择、没有消息）都不是规范反思，附回消息回显为空列表、不读正文。"""
    with pytest.raises(ReflectionOutputError, match=_ONE_CALL) as failure:
        reflection_insights(response, [{"id": "known"}])
    corrected = reflection_correction([{"role": "system", "content": "s"}], response, failure.value)
    assert json.loads(corrected[-1]["content"])["PREVIOUS_INVALID_RESPONSE"] == []
    assert "我想休息" not in corrected[-1]["content"]


def test_reflection_tool_arguments_must_be_a_json_string() -> None:
    """补充证据：参数不是 JSON 文本（供应商直接给出对象）同样是输出错误，回显里该调用的参数为 null。"""
    function = SimpleNamespace(name=_TOOL, arguments={"insights": []})
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=None, tool_calls=[SimpleNamespace(function=function)]))])
    with pytest.raises(ReflectionOutputError, match="submit_reflection arguments must be a JSON string") as failure:
        reflection_insights(response, [{"id": "known"}])
    corrected = reflection_correction([], response, failure.value)
    assert json.loads(corrected[-1]["content"])["PREVIOUS_INVALID_RESPONSE"] == [{"name": _TOOL, "arguments": None}]


def test_reflection_correction_appends_one_compact_message_without_touching_the_original() -> None:
    """补充证据：附回消息是接在给定消息之后的一条 user 消息，内容为紧凑、键有序的 JSON；传入的消息不被修改。"""
    original = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    snapshot = copy.deepcopy(original)
    response = _submit([_insight("unknown")])
    corrected = reflection_correction(original, response, ReflectionOutputError("bad refs"))
    assert original == snapshot and corrected[:2] == snapshot and corrected is not original
    assert corrected[2]["role"] == "user"
    feedback = json.loads(corrected[2]["content"])
    assert corrected[2]["content"] == json.dumps(feedback, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert list(feedback) == ["INSTRUCTION", "PREVIOUS_INVALID_RESPONSE", "VALIDATION_ERROR"]
    assert feedback["PREVIOUS_INVALID_RESPONSE"] == _echo(response) and feedback["VALIDATION_ERROR"] == "bad refs"


def test_reflection_retry_bound_is_its_own_constant_with_the_plan_day_value() -> None:
    """补充证据：反思的校验重试次数另设常量，数值与零点计划的相同（3），本模块不引用零点计划的那一个。"""
    assert REFLECTION_VALIDATION_RETRIES == PLAN_DAY_VALIDATION_RETRIES == 3
    assert not any(name.startswith("PLAN_DAY") for name in vars(life_reflection))

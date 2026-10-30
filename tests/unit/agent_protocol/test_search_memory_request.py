"""search_memory 的参数模型（docs/design/06-agent.md「检索记忆」）：Schema 照 DeepSeek strict 逐字节钉住；类别闭集是记忆的
三类加「全部」；时间只由 pattern 校验形状；关键词的三条上限是本地校验，经宿主现行的参数纠错回给模型；QUERY_MODELS 只有
search_memory，不进 REQUEST_MODELS 与 ActionRequest。"""

from __future__ import annotations

import json
import logging
import os
from typing import get_args

import pytest
from pydantic import ValidationError

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")

from agentsociety2.agent.base import ReactDecision  # noqa: E402
from tests.integration.runtime.test_life_agent_protocol import _prepared  # noqa: E402
from worldmock.agents.protocol.life_feedback import feedback_report  # noqa: E402
from worldmock.state.interaction.requests import (  # noqa: E402
    QUERY_MODELS,
    REQUEST_MODELS,
    ActionRequest,
    SearchMemoryRequest,
)
from worldmock.state.interaction.schema import deepseek_strict_schema  # noqa: E402
from worldmock.state.model.memory import MEMORY_CATEGORIES  # noqa: E402

#: 规格钉住的 Schema 紧凑 JSON（键按生成次序，不排序）。
_PINNED_SCHEMA = (
    '{"additionalProperties":false,"properties":{"category":{"enum":["饮食","工作","生活","全部"],"title":"Category",'
    '"type":"string"},"time":{"pattern":"^(今天( \\\\d{2}:\\\\d{2}(~\\\\d{2}:\\\\d{2})?)?|全部|\\\\d{4}(-\\\\d{2}'
    '(-\\\\d{2}(~\\\\d{4}-\\\\d{2}-\\\\d{2})?)?)?)$","title":"Time","type":"string"},"keyword":{"anyOf":'
    '[{"type":"string"},{"type":"null"}],"title":"Keyword"}},"required":["category","time","keyword"],'
    '"title":"SearchMemoryRequest","type":"object"}')


def _request(time: str = "今天", keyword: str | None = None, category: str = "全部") -> SearchMemoryRequest:
    """按严格 JSON 校验一次检索参数。"""
    payload = json.dumps({"category": category, "time": time, "keyword": keyword}, ensure_ascii=False)
    return SearchMemoryRequest.model_validate_json(payload, strict=True)


def test_search_memory_schema_is_deepseek_strict_and_pinned() -> None:
    """DeepSeek strict 构建器接受这份 Schema，紧凑序列化后与规格逐字节相同。"""
    schema = deepseek_strict_schema(SearchMemoryRequest)
    assert json.dumps(schema, ensure_ascii=False, separators=(",", ":")) == _PINNED_SCHEMA


def test_search_memory_categories_are_the_memory_categories_and_all() -> None:
    """类别的字面闭集恰为记忆的三类加「全部」，次序相同。"""
    assert get_args(SearchMemoryRequest.model_fields["category"].annotation) == (*MEMORY_CATEGORIES, "全部")


@pytest.mark.parametrize(("time", "accepted"), [
    *((time, True) for time in ("今天", "今天 08:03", "今天 08:03~09:00", "全部", "2026", "2026-10", "2026-10-09",
                                "2026-10-01~2026-10-31")),
    *((time, False) for time in ("今天08:03", "今天 8:03", "昨天", "2026/10/09", "2026-10-09~2026-10", "")),
])
def test_search_memory_time_pattern_accepts_the_declared_forms(time: str, accepted: bool) -> None:
    """九种写法的形状都通过 Schema 的 pattern（日历与先后由环境判定）；别的写法以 string_pattern_mismatch 拒绝。"""
    if accepted:
        assert _request(time).time == time
        return
    with pytest.raises(ValidationError) as failure:
        _request(time)
    assert [(item["type"], item["loc"]) for item in failure.value.errors()] == [("string_pattern_mismatch", ("time",))]


@pytest.mark.parametrize(("keyword", "message", "log"), [
    (None, None, None),
    ("早饭 午饭 晚饭 夜宵", None, None),
    ("  " + "饭" * 40 + "  ", None, None),
    ("", "memory search keyword must contain a word or be null", "Memory search keyword is blank"),
    ("\u3000 \t", "memory search keyword must contain a word or be null", "Memory search keyword is blank"),
    ("饭" * 41, "memory search keyword exceeds 40 characters", "Memory search keyword exceeds its character limit"),
    ("早饭 午饭 晚饭 夜宵 零食", "memory search keyword has more than four words",
     "Memory search keyword has too many words"),
])
def test_search_memory_keyword_bounds_are_local_checks(keyword, message, log, caplog) -> None:
    """null、四个词、首尾带空白的 40 字都通过，关键词原样保留；空串、全空白（含全角空格）、41 字、五个词各得自己的错误
    文字，位置是 keyword，并各写一条英文错误日志。"""
    if message is None:
        assert _request(keyword=keyword).keyword == keyword
        return
    with caplog.at_level(logging.ERROR), pytest.raises(ValidationError) as failure:
        _request(keyword=keyword)
    [error] = failure.value.errors()
    assert error["loc"] == ("keyword",) and error["msg"] == f"Value error, {message}"
    assert [record.getMessage() for record in caplog.records if record.levelno == logging.ERROR] == [log]


def test_search_memory_argument_errors_use_the_host_argument_contract() -> None:
    """五个词的关键词经 LifeAgent 的现行参数纠错回给模型：ARGUMENTS_INVALID，路径指到 keyword。"""
    agent = _prepared()
    decision = ReactDecision("", "search_memory", {"category": "饮食", "time": "今天",
                                                   "keyword": "早饭 午饭 晚饭 夜宵 零食"}, "", "call-1")
    report = feedback_report(agent.validate_react_decisions([decision], False)).payload()
    assert report["error_code"] == "ARGUMENTS_INVALID"
    assert [item["path"] for item in report["items"]] == [["tool_calls", 0, "function", "arguments", "keyword"]]


def test_query_models_hold_only_search_memory_outside_request_models() -> None:
    """QUERY_MODELS 只有 search_memory；它与 REQUEST_MODELS 不相交，也不是 ActionRequest 联合类型的成员。"""
    assert QUERY_MODELS == {"search_memory": SearchMemoryRequest}
    assert set(QUERY_MODELS).isdisjoint(REQUEST_MODELS)
    assert SearchMemoryRequest not in get_args(get_args(ActionRequest)[0])

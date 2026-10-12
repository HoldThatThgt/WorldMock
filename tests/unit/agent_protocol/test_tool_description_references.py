"""工具描述只写用途的门禁（docs/design/06-agent.md 第一章「请求组成」「唯一出现原则」）。

世界交互规则、登记与结果的区别只在系统层 rules 写一次；原生工具的 description 只写用途，不含协议与阶段规则、
不指路任何观察字段（名称一律取自工具层与待办层，参数不带逐轮枚举）。参数 Schema 里的说明同样只写用途：不带开发文档、
规则、错误码与数量上限的说法（上限由 Schema 与本地校验执行），参数对象本身不写说明（用途只在工具 description）。

两个开始工具的用途句与字段说明逐字出自规格（docs/dev/environment-contract-revision/SPEC-environment-contract-revision.md
「活动接口与槽位」）：字段说明逐个写出该工具 action 的取值（主工具另写 null），副工具写「内容 app」，副工具的 target
先用一句说明它是名称数组。门禁只对这几处放行这几个词与这一句，其余说明照旧不得出现拉丁字母单词、只写一句。
"""

from __future__ import annotations

import os
import re

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")

from custom.agents.life_agent import (MemoryRecentRequest, MemorySearchRequest, MemoryTimeRangeRequest,  # noqa: E402
                                      ReadonlyFinishRequest)
from worldmock.agents.life_prompt import LIFE_SYSTEM_LAYER, TOOL_DESCRIPTIONS  # noqa: E402
from worldmock.life.action_contract import MAIN_ACTIVITY_ACTIONS, SIDE_ACTIVITY_ACTIONS, START_TOOLS  # noqa: E402
from worldmock.life.control_items import CONTROL_TOOLS  # noqa: E402
from worldmock.life.models import REQUEST_MODELS, deepseek_strict_schema  # noqa: E402
from worldmock.life.narration import FORBIDDEN_OBSERVATION_KEYS  # noqa: E402
from worldmock.life.world_query import QueryWorldRequest  # noqa: E402

#: 字段引用形状：`名称.字段`、`名称[].字段`、`名称[0].字段`。
_FIELD_REFERENCE = re.compile(
    r"(?<![\w])([A-Za-z_][A-Za-z0-9_]*)(?:\[\d*\])?\.([A-Za-z_][A-Za-z0-9_]*)")
#: 协议与阶段规则的说法（旧英文描述与系统层规则中的机制句）：出现在描述里即违规。
_PROTOCOL_PHRASES = ("exactly one", "only tool call", "registering", "registered", "must", "at most",
                     "equally sized", "世界请求", "登记", "恰好", "只能", "不能", "会被拒绝", "同一时刻", "available",
                     "reason")
#: 规则、上限与校验的说法（旧参数说明来自请求模型的开发 docstring）：出现在任何说明里即违规。
_RULE_PHRASES = ("不超过", "至多", "合计", "名额", "拒绝", "必填", "照填", "由系统", "不合法", "释放")
#: 发给模型的全部原生工具参数模型，包含世界查询、记忆查询与只读结束。
_PARAMETER_MODELS = {**REQUEST_MODELS, "memory_recent": MemoryRecentRequest, "memory_search": MemorySearchRequest,
                     "memory_time_range": MemoryTimeRangeRequest, "query_world": QueryWorldRequest,
                     "finish": ReadonlyFinishRequest}
MAIN_TOOL, SIDE_TOOL = START_TOOLS
#: 用途句里放行的拉丁字母单词：只有副工具写「内容 app」。
_PURPOSE_WORDS = {SIDE_TOOL: frozenset({"app"})}
#: 字段说明里放行的拉丁字母单词：该工具 action 的取值，主工具另有 detail 要写的 null，副工具另有「内容 app」与
#: prefer 要写的 null。不放 prefer 这类键名。
_FIELD_WORDS = {MAIN_TOOL: frozenset({*MAIN_ACTIVITY_ACTIONS, "null"}),
                SIDE_TOOL: frozenset({*SIDE_ACTIVITY_ACTIONS, "app", "null"})}
#: 字段说明的句数：都是一句，只有副工具的 target 先用一句说明它是名称数组。
_FIELD_SENTENCES = {f"{SIDE_TOOL}.properties.target": 2}


def _latin_words(text: str) -> list[str]:
    """@brief 说明里三个字母以上的拉丁字母单词。"""
    return re.findall(r"[A-Za-z]{3,}", text)


def _violations(text: str, allowed: frozenset[str] = frozenset()) -> list[str]:
    """@brief 描述中的字段引用、观察禁键、放行之外的拉丁字母单词与协议说法。"""
    found = [f"{parent}.{field}" for parent, field in _FIELD_REFERENCE.findall(text)]
    found += [phrase for phrase in _PROTOCOL_PHRASES + _RULE_PHRASES if phrase in text]
    found += [key for key in FORBIDDEN_OBSERVATION_KEYS if key in text]
    found += [word for word in _latin_words(text) if word not in allowed]
    return found


def test_every_native_tool_has_a_purpose_only_description() -> None:
    """所有原生工具都有一句中文用途说明，不含协议句与字段指路；拉丁字母单词只有副工具用途句里的「app」。"""
    assert set(_PARAMETER_MODELS) == set(TOOL_DESCRIPTIONS) and "start_activity" not in TOOL_DESCRIPTIONS
    offenders = {name: _violations(text, _PURPOSE_WORDS.get(name, frozenset()))
                 for name, text in TOOL_DESCRIPTIONS.items()}
    assert not {name: found for name, found in offenders.items() if found}
    assert {name: set(_latin_words(text)) for name, text in TOOL_DESCRIPTIONS.items() if _latin_words(text)} == {
        name: set(words) for name, words in _PURPOSE_WORDS.items()}
    assert all(text.endswith("。") and text.count("。") == 1 for text in TOOL_DESCRIPTIONS.values())


def test_interaction_rules_live_only_in_the_system_layer() -> None:
    """登记即结束、可用性与 reason 的机制只在系统层写一次，任何描述都不复述。"""
    rules = LIFE_SYSTEM_LAYER["rules"]
    assert sum("世界请求登记后由环境结算" in rule for rule in rules) == 1
    assert sum("available 为 false" in rule for rule in rules) == 1
    for text in TOOL_DESCRIPTIONS.values():
        assert "登记" not in text and "available" not in text and not any(text in rule for rule in rules)


def test_description_scan_has_teeth() -> None:
    """扫描器有牙齿：旧描述的协议句、字段指路与观察禁键原样都被同一判据抓住，放行的词不放过它们；放行只对所在的
    工具有效，动作取值、null 与 app 出现在别处照样是违规，四个旧出行动作在哪里都是违规。"""
    widest = frozenset().union(*_PURPOSE_WORDS.values(), *_FIELD_WORDS.values())
    for tool in START_TOOLS:
        base = TOOL_DESCRIPTIONS[tool]
        for poison in (" During one decision submit exactly one life action.",
                       " play_game uses game_catalog[].game_id", "照 interaction_bindings 填写",
                       "提交后只是登记。"):
            assert _violations(base + poison, widest)
    assert _violations(TOOL_DESCRIPTIONS[SIDE_TOOL]) == ["app"]
    assert _violations("sleep 写 null。") == ["sleep", "null"]
    assert _violations("sleep 写 null。", _FIELD_WORDS[SIDE_TOOL]) == ["sleep"]
    assert _violations("purchase 逐份写商品的完整名称，或使用内容 app。", _FIELD_WORDS[MAIN_TOOL]) == ["purchase", "app"]
    for action in ("walk", "drive", "take_taxi", "take_bus"):
        assert _violations(f"{action} 写目的地地点的完整名称。", widest)


def _descriptions(node, path: str) -> list[tuple[str, str]]:
    """@brief Schema 中全部 description 字符串及其路径（不含 title 与属性名）。"""
    if isinstance(node, list):
        return [pair for index, item in enumerate(node) for pair in _descriptions(item, f"{path}[{index}]")]
    if not isinstance(node, dict):
        return []
    found = [(path, node["description"])] if isinstance(node.get("description"), str) else []
    for key, value in node.items():
        if key != "description":
            found += _descriptions(value, f"{path}.{key}")
    return found


def test_parameter_descriptions_are_purpose_only() -> None:
    """原生工具参数 Schema 的说明只写用途：参数对象本身没有说明，嵌套对象的说明是一句中文用途，不带开发文档（@brief、
    :param）、规则、错误码与上限说法；旧参数说明（路上不能暂停、结束后释放名额、合计不超过八条、不合法时业务拒绝）都被抓住。
    拉丁字母单词恰好是两个开始工具字段说明里放行的那几个（action 的取值、null、app），别的工具的说明里一个也没有；
    句数恰好一句，只有副工具的 target 是规格逐字的两句。"""
    assert set(_PARAMETER_MODELS) == set(TOOL_DESCRIPTIONS)
    schemas = {name: deepseek_strict_schema(model) for name, model in _PARAMETER_MODELS.items()}
    assert all("description" not in schema for schema in schemas.values())
    found = [pair for name, schema in schemas.items() for pair in _descriptions(schema, name)]
    assert found, "nested objects keep a purpose sentence"
    offenders = {path: _violations(text, _FIELD_WORDS.get(path.split(".", 1)[0], frozenset())) for path, text in found}
    assert not {path: words for path, words in offenders.items() if words}
    assert all(text.endswith("。") and text.count("。") == _FIELD_SENTENCES.get(path, 1) for path, text in found)
    assert set(_FIELD_SENTENCES) <= {path for path, _ in found}
    used = {tool: {word for path, text in found if path.split(".", 1)[0] == tool for word in _latin_words(text)}
            for tool in _PARAMETER_MODELS}
    assert {tool: words for tool, words in used.items() if words} == {
        tool: set(words) for tool, words in _FIELD_WORDS.items()}
    for retired in ("逐项播报人物自述并暂停所选进行中活动；暂停仍占名额，路上的活动不能暂停。",
                    "逐项写下自述与记忆并结束所选活动（进行中、暂停中或刚结束待收尾），结束后释放名额。",
                    ":param memories: 与 task 等长；memories[i] 是关于 task[i] 的零至多条记忆，合计不超过八条。",
                    "私有绑定把（对象，动词，方式）映射为内部标准动作，不合法时业务拒绝 ACTION_REQUEST_INVALID。"):
        assert _violations(retired)


def test_control_tool_descriptions_explain_activity_and_errand_purposes() -> None:
    """@brief 控制工具与实际参数描述说明活动和事务用途，三个工具使用同一种事务结构。"""
    purposes = {"report_status": ("活动", "进展"), "pause_activity": ("暂停", "进展"),
                "conclude_activity": ("结束", "总结", "想记住")}
    assert set(purposes) == set(CONTROL_TOOLS)
    for tool, words in purposes.items():
        assert all(word in TOOL_DESCRIPTIONS[tool] for word in (*words, "个人事务", "变更"))
    schemas = {tool: deepseek_strict_schema(REQUEST_MODELS[tool]) for tool in CONTROL_TOOLS}
    memories = schemas["conclude_activity"]["properties"]["items"]["items"]["properties"]["memories"]
    assert all(word in memories["description"] for word in ("活动", "以后做决定", "新情况"))
    errands = schemas["report_status"]["properties"]["errands"]
    assert all(schema["properties"]["errands"] == errands for schema in schemas.values())
    assert all(word in errands["description"] for word in ("新增", "更新", "取消", "个人事务"))
    assert "个人事务的变更" in errands["items"]["description"]
    fields = errands["items"]["properties"]
    purposes = {"change": ("变更", "做什么"), "name": ("事务", "名称"), "item": ("商品", "名称"),
                "quantity": ("份数",), "memo": ("怎么办", "去哪办"), "remind_time": ("提醒", "钟点"),
                "reason": ("不办", "原因")}
    assert set(fields) == set(purposes)
    for name, words in purposes.items():
        assert all(word in fields[name]["description"] for word in words)


def test_query_world_purpose_describes_filters_and_changes_only_the_tool_fingerprint(monkeypatch) -> None:
    """@brief 查询用途覆盖筛选与翻页；更改描述仅改变工具指纹，严格参数保持六个必填键。"""
    from custom.agents.life_agent import life_tool_schema_sha256
    from worldmock.agents.life_prompt import life_prompt_sha256

    sentence = TOOL_DESCRIPTIONS["query_world"]
    assert all(word in sentence for word in ("名称", "具体动作", "商品品类", "此刻能做", "偏移量", "翻页"))
    assert not _violations(sentence)
    tools, prompt = life_tool_schema_sha256(), life_prompt_sha256()
    monkeypatch.setitem(TOOL_DESCRIPTIONS, "query_world", "按已知名称、具体动作或商品品类查询世界，按返回的偏移量翻页。")
    assert life_tool_schema_sha256() != tools and life_prompt_sha256() == prompt
    schema = deepseek_strict_schema(QueryWorldRequest)
    assert schema["required"] == ["name", "action", "category", "available_only", "offset"]
    assert list(schema["properties"]) == schema["required"]
    assert schema["properties"]["action"]["anyOf"] == [{"type": "string"}, {"type": "null"}]
    assert schema["properties"]["available_only"]["type"] == "boolean"
    assert _descriptions(schema, "query_world") == []

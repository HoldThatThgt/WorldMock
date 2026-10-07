"""工具描述只写用途的门禁（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第一章「请求组成」「唯一出现原则」）。

世界交互规则、登记与结果的区别只在系统层 rules 写一次；原生工具的 description 只写用途，不含协议与阶段规则、
不指路任何观察字段（名称一律取自工具层与待办层，参数不带逐轮枚举）。参数 Schema 里的说明同样只写用途：不带开发文档、
规则、错误码与数量上限的说法（上限由 Schema 与本地校验执行），参数对象本身不写说明（用途只在工具 description）。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")

from custom.agents.life_agent import (MemoryRecentRequest, MemorySearchRequest, MemoryTimeRangeRequest,  # noqa: E402
                                      ReadonlyFinishRequest)
from worldmock.agents.life_prompt import LIFE_SYSTEM_LAYER, TOOL_DESCRIPTIONS  # noqa: E402
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


def _violations(text: str) -> list[str]:
    """@brief 描述中的字段引用、观察禁键、拉丁字母单词与协议说法。"""
    found = [f"{parent}.{field}" for parent, field in _FIELD_REFERENCE.findall(text)]
    found += [phrase for phrase in _PROTOCOL_PHRASES + _RULE_PHRASES if phrase in text]
    found += [key for key in FORBIDDEN_OBSERVATION_KEYS if key in text]
    found += re.findall(r"[A-Za-z]{3,}", text)
    return found


def test_every_native_tool_has_a_purpose_only_description() -> None:
    """所有原生工具都有一句中文用途说明，不含协议句与字段指路。"""
    assert set(_PARAMETER_MODELS) == set(TOOL_DESCRIPTIONS)
    offenders = {name: _violations(text) for name, text in TOOL_DESCRIPTIONS.items() if _violations(text)}
    assert not offenders
    assert all(text.endswith("。") and text.count("。") == 1 for text in TOOL_DESCRIPTIONS.values())


def test_interaction_rules_live_only_in_the_system_layer() -> None:
    """登记即结束、可用性与 reason 的机制只在系统层写一次，任何描述都不复述。"""
    rules = LIFE_SYSTEM_LAYER["rules"]
    assert sum("只是登记" in rule for rule in rules) == 1
    assert sum("available 为 false 的会被拒绝" in rule for rule in rules) == 1
    for text in TOOL_DESCRIPTIONS.values():
        assert "登记" not in text and "available" not in text and not any(text in rule for rule in rules)


def test_description_scan_has_teeth() -> None:
    """扫描器有牙齿：旧描述的协议句、字段指路与观察禁键原样都被同一判据抓住。"""
    base = TOOL_DESCRIPTIONS["start_activity"]
    for poison in (" During one decision submit exactly one life action.",
                   " play_game uses game_catalog[].game_id", "照 interaction_bindings 填写",
                   "提交后只是登记。"):
        assert _violations(base + poison)


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
    :param）、规则、错误码与上限说法；旧参数说明（路上不能暂停、结束后释放名额、合计不超过八条、不合法时业务拒绝）都被抓住。"""
    assert set(_PARAMETER_MODELS) == set(TOOL_DESCRIPTIONS)
    schemas = {name: deepseek_strict_schema(model) for name, model in _PARAMETER_MODELS.items()}
    assert all("description" not in schema for schema in schemas.values())
    found = [pair for name, schema in schemas.items() for pair in _descriptions(schema, name)]
    assert found, "nested objects keep a purpose sentence"
    offenders = {path: _violations(text) for path, text in found if _violations(text)}
    assert not offenders
    assert all(text.endswith("。") and text.count("。") == 1 for _, text in found)
    for retired in ("逐项播报人物自述并暂停所选进行中活动；暂停仍占名额，路上的活动不能暂停。",
                    "逐项写下自述与记忆并结束所选活动（进行中、暂停中或刚结束待收尾），结束后释放名额。",
                    ":param memories: 与 task 等长；memories[i] 是关于 task[i] 的零至多条记忆，合计不超过八条。",
                    "私有绑定把（对象，动词，方式）映射为内部标准动作，不合法时业务拒绝 ACTION_REQUEST_INVALID。"):
        assert _violations(retired)


#: 接口合同「人物工具与管理接口」里三个控制工具用途句与字段说明的定稿表（docs/dev/SPEC-life-realism.md#errands-workday-提示信息分工-工具用途句与字段说明）。
_CONTRACTS = Path(__file__).resolve().parents[2] / "docs" / "CONTRACTS.md"
_CONTROL_TEXT_LEAD = "三个控制工具的用途句与字段说明定稿（与代码逐字相同）："
#: 定稿表的位置列，按规格 3.2 的行序：三个用途句、收尾记忆、请求级 errands、每条变更的对象用途与它的八个键。
_CONTROL_TEXT_POSITIONS = ["report_status", "pause_activity", "conclude_activity", "items[].memories", "errands",
                           "每条变更", "change", "name", "outcome", "item", "quantity", "memo", "remind_time", "reason"]


def _control_contract_texts() -> list[tuple[str, str]]:
    """@brief 接口合同里导语之后的那张两列表：（位置，定稿）逐行，位置去掉代码标记。"""
    lines = _CONTRACTS.read_text(encoding="utf-8").splitlines()
    rows, start = [], lines.index(_CONTROL_TEXT_LEAD) + 1
    for line in lines[start:]:
        if line.startswith("|"):
            rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
        elif rows:
            break
    header, separator, *body = rows
    assert header == ["位置", "定稿"] and set("".join(separator)) == {"-"}
    return [(position.strip("`"), text) for position, text in body]


def test_control_tool_purposes_and_errand_descriptions_are_the_contract_text() -> None:
    """三个控制工具的用途句与十一处参数说明（收尾记忆、请求级 errands、每条变更的对象用途与它的八个键）与接口合同的
    定稿表逐字相同（docs/dev/SPEC-life-realism.md#errands-workday-提示信息分工-工具用途句与字段说明）：定稿表恰好十四行、按规格的行序；每个工具的实际 Schema 都带
    同样的 errands 说明；十四句都通过只写用途的扫描（一句中文、不含规则说法与拉丁单词）。"""
    rows = _control_contract_texts()
    assert [position for position, _ in rows] == _CONTROL_TEXT_POSITIONS
    texts = dict(rows)
    assert {tool: TOOL_DESCRIPTIONS[tool] for tool in CONTROL_TOOLS} == {tool: texts[tool] for tool in CONTROL_TOOLS}
    schemas = {tool: deepseek_strict_schema(REQUEST_MODELS[tool]) for tool in CONTROL_TOOLS}
    memories = schemas["conclude_activity"]["properties"]["items"]["items"]["properties"]["memories"]
    assert memories["description"] == texts["items[].memories"]
    keys = _CONTROL_TEXT_POSITIONS[6:]
    for schema in schemas.values():
        errands = schema["properties"]["errands"]
        assert errands["description"] == texts["errands"]
        assert errands["items"]["description"] == texts["每条变更"]
        assert {key: field["description"] for key, field in errands["items"]["properties"].items()} == {
            key: texts[key] for key in keys}
    assert {text: _violations(text) for _, text in rows if _violations(text)} == {}
    assert all(text.endswith("。") and text.count("。") == 1 for _, text in rows)


#: 个人事务与工作安排规格 5.6 定稿 query_world 用途句的那一行的引导语。
_PURPOSE_LEAD = "`query_world` 的用途句定稿（与代码逐字相同）：「"


def test_query_world_purpose_is_the_contract_text_and_moves_only_the_tool_fingerprint(monkeypatch) -> None:
    """query_world 的用途句逐字等于 docs/dev/SPEC-interaction-menu.md#errands-workday-查询筛选与返回精简-执行位置与保留约束 定稿的那一句（从唯一接口合同读出）；换回旧句时只有
    工具指纹变、提示指纹不变（本阶段不改系统层与层 Schema）；严格 Schema 的六个键全部必填、按 name, way, category, from,
    available_only, offset 的顺序，from 是字符串或 null、available_only 是布尔，任何位置都没有字段说明。"""
    from custom.agents.life_agent import life_tool_schema_sha256
    from worldmock.agents.life_prompt import life_prompt_sha256

    [line] = [line for line in _CONTRACTS.read_text(encoding="utf-8").splitlines() if _PURPOSE_LEAD in line]
    sentence = line.split(_PURPOSE_LEAD, 1)[1].split("」", 1)[0]
    assert sentence and TOOL_DESCRIPTIONS["query_world"] == sentence
    assert not _violations(sentence)
    tools, prompt = life_tool_schema_sha256(), life_prompt_sha256()
    monkeypatch.setitem(TOOL_DESCRIPTIONS, "query_world", "按已知名称、方式或公开品类查询世界，按返回的偏移量翻页。")
    assert life_tool_schema_sha256() != tools and life_prompt_sha256() == prompt
    schema = deepseek_strict_schema(QueryWorldRequest)
    assert schema["required"] == ["name", "way", "category", "from", "available_only", "offset"]
    assert list(schema["properties"]) == schema["required"]
    assert schema["properties"]["from"]["anyOf"] == [{"type": "string"}, {"type": "null"}]
    assert schema["properties"]["available_only"]["type"] == "boolean"
    assert _descriptions(schema, "query_world") == []

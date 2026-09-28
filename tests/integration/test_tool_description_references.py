"""工具描述只写用途的门禁（docs/dev/SPEC-persona-prompt.md 第一章「请求组成」「唯一出现原则」）。

世界交互规则、登记与结果的区别只在系统层 rules 写一次；原生工具的 description 只写用途，不含协议与阶段规则、
不指路任何观察字段（名称一律取自工具层与待办层，参数不带逐轮枚举）。
"""

from __future__ import annotations

import os
import re

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")

from custom.agents.life_prompt import LIFE_SYSTEM_LAYER, TOOL_DESCRIPTIONS  # noqa: E402
from worldmock.life.models import REQUEST_MODELS  # noqa: E402
from worldmock.life.narration import FORBIDDEN_OBSERVATION_KEYS  # noqa: E402

#: 字段引用形状：`名称.字段`、`名称[].字段`、`名称[0].字段`。
_FIELD_REFERENCE = re.compile(
    r"(?<![\w])([A-Za-z_][A-Za-z0-9_]*)(?:\[\d*\])?\.([A-Za-z_][A-Za-z0-9_]*)")
#: 协议与阶段规则的说法（旧英文描述与系统层规则中的机制句）：出现在描述里即违规。
_PROTOCOL_PHRASES = ("exactly one", "only tool call", "registering", "registered", "must", "at most",
                     "equally sized", "世界请求", "登记", "恰好", "只能", "不能", "会被拒绝", "同一时刻", "available",
                     "reason")


def _violations(text: str) -> list[str]:
    """@brief 描述中的字段引用、观察禁键、拉丁字母单词与协议说法。"""
    found = [f"{parent}.{field}" for parent, field in _FIELD_REFERENCE.findall(text)]
    found += [phrase for phrase in _PROTOCOL_PHRASES if phrase in text]
    found += [key for key in FORBIDDEN_OBSERVATION_KEYS if key in text]
    found += re.findall(r"[A-Za-z]{3,}", text)
    return found


def test_every_native_tool_has_a_purpose_only_description() -> None:
    """八个人物工具、三个记忆查询与只读 finish 都有一句中文用途说明，不含协议句与字段指路。"""
    assert set(REQUEST_MODELS) | {"memory_recent", "memory_search", "memory_time_range", "finish"} == set(
        TOOL_DESCRIPTIONS)
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

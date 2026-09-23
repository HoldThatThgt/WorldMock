"""工具描述字段引用与观察合同的一致性门禁。

`docs/VERIFICATION.md`「工程门禁」的「候选与呈现一致性」节要求：工具描述里
出现的字段引用（形如 `名称.字段`）全部不在观察黑名单内；描述与观察合同指向
的字段必须一致。工具描述与观察合同是同一份事实的两个面——描述指向一个未被
下发的路径，模型就会去读一个它拿不到的东西，而失败发生在运行期，因此这里
做静态一致性断言。

判据用「描述说这个键在，观察就真的下发这个键」表达：观察的顶层键先按黑名单
过滤，再把黑名单键名从允许集里剔掉——这样断言既有黑名单面的牙齿，也不会
因为允许集里恰好列出某个黑名单键而失效。
"""

from __future__ import annotations

import os
import re

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")

from custom.agents.life_agent import _TOOL_DESCRIPTIONS  # noqa: E402
from worldmock.life.models import REQUEST_MODELS  # noqa: E402
from worldmock.life.narration import FORBIDDEN_OBSERVATION_KEYS  # noqa: E402

#: 字段引用形状：`名称.字段`、`名称[].字段`、`名称[0].字段` 三类下标变体统一归一化；
#: 前导边界只排除标识符与点号，不排除 `uses ` 这类正文分隔，否则最典型的漂移形态会漏检。
_FIELD_REFERENCE = re.compile(
    r"(?<![\w])([A-Za-z_][A-Za-z0-9_]*)(?:\[\d*\])?\.([A-Za-z_][A-Za-z0-9_]*)")

#: 顶层键白名单：`LifeEngine` 观察事件真实下发的键（`src/worldmock/life/observe.py` 的
#: `_observation_data` 与 `projection.build_observation` 两处装配的并集）。
#: 工具描述只能指路这些键；新增下发键时必须同步这里，否则描述指路会被判为悬空。
#: 注意 `game_catalog` 刻意不在表内：它是事件级工具支持字段，绝不进提示，
#: 描述指路它即是漂移（这正是本条门禁要抓的那一例）。
DELIVERED_OBSERVATION_KEYS = frozenset({
    "current_time", "current_epoch", "location", "people_here", "contacts", "inbox",
    "tasks", "progress", "interactive_objects", "routes", "products", "inventory",
    "day_plan", "felt", "drive_summary", "scene", "facts",
})

#: 非字段引用的点号形态：CamelCase 类型名、常见文件/主机后缀、Python 属性白名单。
_NOT_A_FIELD_PARENT = frozenset({
    "observations", "data", "self", "cls", "requests", "datetime", "zoneinfo",
})
_NOT_A_FIELD_SUFFIX = frozenset({"md", "py", "json", "toml", "txt", "com", "cn", "org"})


def _action_descriptions() -> dict[str, str]:
    """@brief 读取实际发送给模型的生活动作工具描述。"""
    return {name: _TOOL_DESCRIPTIONS[name] for name in REQUEST_MODELS}


def _field_references(text: str) -> set[tuple[str, str]]:
    """@brief 提取 `(父名, 字段名)` 形状的引用，过滤非字段点号形态。"""
    references: set[tuple[str, str]] = set()
    for parent, field in _FIELD_REFERENCE.findall(text):
        if parent in _NOT_A_FIELD_PARENT or field in _NOT_A_FIELD_SUFFIX:
            continue
        references.add((parent, field))
    return references


def _blacklisted_delivered_keys() -> frozenset[str]:
    """@brief 允许集里被黑名单禁止的顶层键：描述提到它们即违规。"""
    return frozenset(DELIVERED_OBSERVATION_KEYS) & FORBIDDEN_OBSERVATION_KEYS


def test_tool_description_references_are_delivered_and_not_forbidden() -> None:
    """@brief 工具描述的每个 `*.field` 引用都指向真实下发的非黑名单观察键。"""
    constants = _action_descriptions()
    assert constants, "life_agent must describe each life action tool"
    banned = _blacklisted_delivered_keys() | {key.rsplit(".", 1)[-1]
                                              for key in FORBIDDEN_OBSERVATION_KEYS}
    offenders: dict[str, list[str]] = {}
    for name, text in sorted(constants.items()):
        bad = sorted({parent for parent, _ in _field_references(text)
                      if parent not in DELIVERED_OBSERVATION_KEYS}
                     | {field for _, field in _field_references(text) if field in banned})
        if bad:
            offenders[name] = bad
    assert not offenders, (
        "tool descriptions must point only at delivered, non-blacklisted observation keys: "
        f"{offenders}")


#: 曾经的漂移原样：描述指路黑名单字段 `game_catalog`（`docs/VERIFICATION.md`「工程门禁」
#: 的「工具描述与提示黑名单一致」整行记的就是这一例）。扫描器必须能抓住它。
_HISTORICAL_DRIFT_SAMPLE = " play_game uses game_catalog[].game_id"


def test_tool_description_scan_has_teeth() -> None:
    """@brief 扫描器有牙齿：历史漂移原样与悬空键都被同一条判据抓住。"""
    constants = _action_descriptions()
    sample = sorted(constants)[0]
    banned = _blacklisted_delivered_keys() | {key.rsplit(".", 1)[-1]
                                              for key in FORBIDDEN_OBSERVATION_KEYS}
    for poison in (_HISTORICAL_DRIFT_SAMPLE, " work uses nonexistent_root[].task_id"):
        references = _field_references(constants[sample] + poison)
        assert ({parent for parent, _ in references
                 if parent not in DELIVERED_OBSERVATION_KEYS}
                | {field for _, field in references if field in banned})

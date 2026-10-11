"""@brief 运行输入警告：闭集形状与两种警告的计算（docs/design/08-configuration.md「校验与输入冻结」）。

纯函数模块：只按显式输入计算，不读文件、不调模型、不改输入。新建运行（run_setup.creation_inputs）把警告冻结进运行清单
input_warnings、计入质量报告 input_warnings 一节并逐条写一条 ops 警告；运行永不因警告失败。

每条警告的形状固定为 {code, agent_id, message, details}：code 取警告码闭集 INPUT_WARNING_CODES；agent_id 为人物编号，
环境级警告（地点显示名）为 null；message 是给数据集维护者看的一句说明；details 的键按警告码固定（见各函数）。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal, get_args

from pydantic import Field, ValidationError

from worldmock.base_models import FrozenModel
from worldmock.errors import IntegrityError
from worldmock.input_models import PersonaConfig

#: 运行输入警告码闭集。
InputWarningCode = Literal[
    "CONTENT_EMPTY_WITH_MEDIA_TASTES",
    "LOCATION_NAME_SUSPICIOUS",
]
INPUT_WARNING_CODES: tuple[str, ...] = get_args(InputWarningCode)
#: 「看、刷、听」用于识别习惯中的媒体使用；口味的媒体列表本身就是显式偏好。
_MEDIA_VERBS = ("看", "刷", "听")
#: 坐标形态：成对的小数经纬度（如「（31.2, 121.5）」「东经121.50°、北纬31.23°」）或单个带经纬前缀的数值。
_COORDINATE = r"(?:[东西]经|[南北]纬)?\s*[-+]?\d{1,3}\.\d+\s*°?"
_COORDINATE_PAIR = re.compile(rf"{_COORDINATE}\s*[,，、;；]\s*{_COORDINATE}")
_LABELED_COORDINATE = re.compile(r"(?:[东西]经|[南北]纬)\s*[-+]?\d+(?:\.\d+)?\s*°?")
#: 标识形态：以连字符或下划线相连的小写 ASCII 段（如「-sim-01」「poi-node-1」）。
_IDENTIFIER = re.compile(r"(?<![A-Za-z0-9])[a-z][a-z0-9]*(?:[-_][a-z0-9]+)+(?![A-Za-z0-9])")


class InputWarning(FrozenModel):
    """@brief 一条运行输入警告（清单 input_warnings 的条目形状，拒绝未知字段）。"""

    code: InputWarningCode
    agent_id: int | None = Field(gt=0)
    message: str = Field(min_length=1)
    details: dict[str, Any]


def frozen_input_warnings(value: Iterable[Mapping[str, Any]] | None) -> list[dict[str, Any]] | None:
    """@brief 冻结进清单前逐条按 InputWarning 校验；None 保持 None（引擎直驱，没有计算警告）。

    @exception IntegrityError 条目不是闭集形状（运行时与引擎之间的内部合同违约，不是一条输入警告）
    """
    if value is None:
        return None
    try:
        return [InputWarning.model_validate(item).model_dump(mode="json") for item in value]
    except ValidationError as error:
        raise IntegrityError("Input warnings do not match the closed warning shape") from error


def content_empty_warnings(persona: PersonaConfig, catalog: Any) -> list[dict[str, Any]]:
    """@brief 没有任何应用绑定内容而人设表写了媒体口味或带「看、刷、听」的习惯时警告；不读内容题材。

    @param persona 已通过加载检查的人设表。
    @param catalog 真实登记目录，内容绑定已通过登记校验。
    @return 警告 details 仅含 media 与 habits。
    """
    if any(item.app_ids for item in catalog.contents.values()):
        return []
    media = list(persona.tastes.media)
    habits = [habit.response for habit in persona.habits
              if _mentions_media_verb(habit.response) or _mentions_media_verb(habit.trigger)]
    if not (media or habits):
        return []
    return [_warning("CONTENT_EMPTY_WITH_MEDIA_TASTES", persona.agent_id,
                     f"No app has bound content, but the persona mentions media use: media {_listing(media)}, "
                     f"habits {_listing(habits)}.", media=media, habits=habits)]


def location_name_warnings(catalog: Any) -> list[dict[str, Any]]:
    """@brief LOCATION_NAME_SUSPICIOUS：地点显示名带坐标或标识形态（只警告，人物会照抄这个名字）。

    @return details 键：location_id、name、patterns（coordinates / identifier）
    """
    found = []
    for location in sorted(catalog.locations.values(), key=lambda item: item.location_id):
        patterns = suspicious_name_patterns(location.name)
        if patterns:
            kinds = ", ".join(patterns)
            found.append(_warning("LOCATION_NAME_SUSPICIOUS", None,
                                  f"Location {location.location_id} display name '{location.name}' contains {kinds}; "
                                  "use a natural place name because the person repeats the display name.",
                                  location_id=location.location_id, name=location.name, patterns=patterns))
    return found


def suspicious_name_patterns(name: str) -> list[str]:
    """@brief 显示名里的可疑形态：coordinates（成对小数经纬度或带经纬前缀的数值）、identifier（小写 ASCII 段以连字符或
    下划线相连，且带纯数字段或至少三段，如 sim-01、poi-node-1；「7-Eleven」「e-bike」这类名字不算）。"""
    patterns = []
    if _COORDINATE_PAIR.search(name) or _LABELED_COORDINATE.search(name):
        patterns.append("coordinates")
    for match in _IDENTIFIER.finditer(name):
        parts = re.split(r"[-_]", match.group())
        if any(part.isdigit() for part in parts) or len(parts) >= 3:
            patterns.append("identifier")
            break
    return patterns


def _mentions_media_verb(text: str) -> bool:
    """@brief 文本里是否有「看、刷、听」。"""
    return any(verb in text for verb in _MEDIA_VERBS)


def _listing(values: Sequence[str]) -> str:
    """@brief 正文里的列表写法：[甲, 乙]，空列表写 [none]。"""
    return "[" + (", ".join(values) if values else "none") + "]"


def _warning(code: str, agent_id: int | None, message: str, **details: Any) -> dict[str, Any]:
    """@brief 按闭集形状组装一条警告（经 InputWarning 校验后的 JSON 形状）。"""
    return InputWarning(code=code, agent_id=agent_id, message=message, details=details).model_dump(mode="json")

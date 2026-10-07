"""@brief 商品候选按人物冻结习惯的名称或类别命中计算相关度，其他排序由呈现模块统一执行。"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from worldmock.errors import IntegrityError

#: 人设层习惯签名的反应文本命中条目名或品类的相关度。
HABIT_POINTS = 1
#: 运行清单中冻结习惯签名反应文本的键。
MANIFEST_KEY = "persona_habit_responses"












def entry_relevance(entry: Any, habit_texts: Sequence[str]) -> int:
    """@brief 商品名称或类别逐字命中人物自己的冻结习惯时加一分，大小写不敏感。"""
    words = (entry.row.get("name") or entry.row.get("title"), entry.category)
    matched = any(str(word).casefold() in text.casefold()
                  for text in habit_texts for word in words if word)
    return HABIT_POINTS if matched else 0


def pool_relevance(entries: Iterable[Any], habit_texts: Sequence[str]) -> dict[str, int]:
    """@brief 当前商品池的对象身份映射到习惯相关度，不生成活动家族或时间窗。"""
    return {entry.object_id: entry_relevance(entry, habit_texts) for entry in entries}


def frozen_habit_responses(value: Mapping[Any, Sequence[str]] | None) -> dict[str, list[str]] | None:
    """@brief 冻结进运行清单的习惯签名反应文本：人物编号（写成字符串）→ 反应文本列表；None 原样保留（引擎直驱）。

    :raises IntegrityError: 人物编号不是正整数，或反应文本不是非空字符串。
    """
    if value is None:
        return None
    frozen: dict[str, list[str]] = {}
    for key, texts in value.items():
        if not str(key).isdigit() or int(key) <= 0 or isinstance(texts, str):
            logging.error("Persona habit responses are keyed by a non-agent id or are not a list")
            raise IntegrityError("persona habit responses must map agent ids to text lists")
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            logging.error("Persona habit responses contain an empty or non-text item")
            raise IntegrityError("persona habit responses must be non-empty texts")
        frozen[str(int(key))] = list(texts)
    return dict(sorted(frozen.items(), key=lambda item: int(item[0])))


def manifest_habit_texts(manifest: Mapping[str, Any], agent_id: int) -> tuple[str, ...]:
    """@brief 运行清单中该人物的习惯签名反应文本；键缺失或为 null（引擎直驱）时为空。"""
    return tuple((manifest.get(MANIFEST_KEY) or {}).get(str(agent_id), ()))

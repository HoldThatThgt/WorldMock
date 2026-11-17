"""@brief WorldMock 主观记忆存储：条目的构造与归一化、近二十四小时同类型相似文本合并、条目文件的写出与严格读回。

来源：本模块部分代码改写自 AgentSociety2 2.8.7（上游提交 670c94ff），按 Apache-2.0 使用，许可全文见 LICENSES/AgentSociety2-Apache-2.0.txt。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from worldmock.foundation.errors import reject_value
from worldmock.state.model.memory import SUBJECTIVE_TYPES

#: 同类型主观正文达到此相似度才合并。
MEMORY_MERGE_JACCARD_THRESHOLD = 0.6
#: 同类型主观正文只在最近二十四小时合并。
MEMORY_MERGE_WINDOW = timedelta(hours=24)
#: 工作区里的记忆视图与条目文件（相对工作区根，POSIX 写法）。
MEMORY_VIEW_FILE = "MEMORY.md"
EPISODES_FILE = "memory/episodes.jsonl"
#: 条目的来源，由系统按写入点固定（与 memory_updated.data.trigger 的取值同名）。
EPISODE_SOURCE = "activity_conclusion"
#: 一条条目的十个键，按写出次序。
PERSISTED_EPISODE_FIELDS = ("id", "agent_id", "time", "type", "importance", "keywords",
                            "text", "source", "merge_count", "epistemic_status")


def text_bigrams(text: str) -> set[tuple[str, str]]:
    """@brief 计算一段文本规范化后的相邻字符 bigram 集合。

    :param text: 任意文本。
    :returns: 小写、空白折叠后的相邻字符对集合。
    """
    normalized = " ".join(str(text or "").lower().split())
    return {
        (normalized[i], normalized[i + 1])
        for i in range(len(normalized) - 1)
    }


def bigram_jaccard(left: str, right: str) -> float:
    """@brief 计算两段文本的 bigram Jaccard 相似度。

    :param left: 左侧文本。
    :param right: 右侧文本。
    :returns: [0, 1] 相似度；任一侧无 bigram 时为零。
    """
    left_grams = text_bigrams(left)
    right_grams = text_bigrams(right)
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / len(left_grams | right_grams)


def read_episodes(workspace: Path, agent_id: int) -> tuple[dict[str, Any], ...]:
    """@brief 严格读回人物工作区的条目文件：按 UTF-8 解码、以换行符切行（不用 splitlines，关键词里的 U+2028 等
    字符不能拆行），逐行核对；不修复、不补默认值、不跳过任何一行。

    :param workspace: 人物工作区根。
    :param agent_id: 工作区所属人物的编号。
    :returns: 按文件次序的条目。
    :raises ValueError: 空行或任一行不合格式；不能解码与不是 JSON 的行照原异常抛出。
    """
    lines = (Path(workspace) / EPISODES_FILE).read_bytes().decode("utf-8").split("\n")
    if lines[-1] == "":
        lines.pop()
    records = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            reject_value(f"Memory episode line {line_number} is blank")
        item = json.loads(line)
        _validate_record(item, line_number, agent_id)
        records.append(item)
    return tuple(records)


class WorldMockMemoryStore:
    """@brief 一个人物的主观记忆存储：整次运行共用，条目留在内存里，只在字节变了时整份写出条目文件与视图。"""

    def __init__(self, workspace: Path, agent_id: int, records: Sequence[Mapping[str, Any]]) -> None:
        """@brief 绑定人物工作区与人物编号，深拷贝已校验的条目；不读任何文件。

        :param workspace: 人物工作区根。
        :param agent_id: 人物编号。
        :param records: 新建运行为空；恢复时是唯一一次严格读回得到的条目。
        """
        self.workspace = Path(workspace)
        self.agent_id = agent_id
        self._records = [deepcopy(dict(item)) for item in records]

    @property
    def memory_md_path(self) -> Path:
        """@brief 工作区里的记忆视图 MEMORY.md。"""
        return self.workspace / MEMORY_VIEW_FILE

    @property
    def episodes_path(self) -> Path:
        """@brief 工作区里的条目文件 memory/episodes.jsonl。"""
        return self.workspace / EPISODES_FILE

    def episode_ids(self) -> set[str]:
        """@brief 存储里全部条目的身份集合：直接读内存里的条目，不复制条目。"""
        return {str(item["id"]) for item in self._records}

    def append_episodes(self, memories: Sequence[Mapping[str, Any]], now: datetime) -> list[dict[str, Any]]:
        """@brief 保存本步主观记忆：近二十四小时同类型高相似的合并进既有条目，其余新建；返回新建与合并目标的副本。

        合并与新建都在工作副本上进行，本次调用里先新建的条目也参加后面记忆的合并判定；新建条目先过读回用的同一个
        校验，任何一条不通过时文件与存储都保持调用之前的样子；文件写成之后工作副本才换下存储里的条目。

        :param memories: conclude_activity 经 control_items 读出的存储形状记忆 {type, text, importance, keywords,
            subject}，按输入次序处理。
        :param now: 本次决策的观察时刻。
        :returns: 新建条目在前、合并目标在后的独立副本。
        """
        records = deepcopy(self._records)
        created: list[dict[str, Any]] = []
        merged: list[dict[str, Any]] = []
        for memory in memories:
            text = " ".join(memory["text"].split())
            target = self._find_merge_target(records, (text, memory["type"], now))
            if target is not None:
                target["merge_count"] += 1
                target["time"] = now.isoformat()
                merged.append(target)
                continue
            record = self._new_record(memory, len(records) + 1, now)
            _validate_record(record, len(records) + 1, self.agent_id)
            records.append(record)
            created.append(record)
        _write_if_changed(self.episodes_path,
                          "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records))
        self._records = records
        return deepcopy([*created, *merged])

    def write_memory_view(self, view: str) -> bool:
        """@brief 视图字节与现有 MEMORY.md 不同（或文件不存在）时写出并返回真，相同时不动文件、返回假。"""
        return _write_if_changed(self.memory_md_path, view)

    def _new_record(self, memory: Mapping[str, Any], number: int, now: datetime) -> dict[str, Any]:
        """@brief 由一条存储形状的记忆构造文件第 number 行的条目：来源与认知状态由系统固定，正文空白折叠，关键词去空串、
        按首次出现去重；不写 subject。"""
        keywords = [text for text in (str(item).strip() for item in memory["keywords"]) if text]
        return {"id": f"m_{number:06d}", "agent_id": self.agent_id, "time": now.isoformat(),
                "type": memory["type"], "importance": float(memory["importance"]),
                "keywords": list(dict.fromkeys(keywords)), "text": " ".join(memory["text"].split()),
                "source": EPISODE_SOURCE, "merge_count": 1, "epistemic_status": "subjective"}

    def _find_merge_target(
        self, records: list[dict[str, Any]], context: tuple[str, str, datetime],
    ) -> dict[str, Any] | None:
        """@brief 从最新一条往前寻找类型、时间窗口与正文相似度均合格的合并目标。"""
        text, episode_type, t = context
        for record in reversed(records):
            if record["type"] != episode_type:
                continue
            record_time = datetime.fromisoformat(record["time"])
            if not self._within_merge_window(t, record_time):
                continue
            if bigram_jaccard(record["text"], text) >= MEMORY_MERGE_JACCARD_THRESHOLD:
                return record
        return None

    @staticmethod
    def _within_merge_window(new_time: datetime, record_time: datetime) -> bool:
        """@brief 判断既有记录时间是否落在最近合并窗口内。

        :param new_time: 新记忆的模拟时间。
        :param record_time: 既有记录时间。
        :returns: 时差在 [0, 24h] 内为真；无法比较按窗口外处理。
        """
        try:
            age = new_time - record_time
        except TypeError:
            return False
        return timedelta(0) <= age <= MEMORY_MERGE_WINDOW


def _validate_record(item: Any, line_number: int, agent_id: int) -> None:
    """@brief 按次序核对一条条目（文件第 line_number 行）：十个键、连续编号、人物、计数、类型、认知状态、来源、
    重要度、文字与关键词；任一不成立先写同文英文错误日志，再抛 ValueError。写出之前的新条目与严格读回共用这一个函数。"""
    prefix = f"Memory episode line {line_number}"
    if not isinstance(item, dict) or set(item) != set(PERSISTED_EPISODE_FIELDS):
        reject_value(f"{prefix} has an invalid schema")
    if item["id"] != f"m_{line_number:06d}":
        reject_value(f"{prefix} has a non-contiguous identity")
    if type(item["agent_id"]) is not int or item["agent_id"] != agent_id:
        reject_value(f"{prefix} has inconsistent identity")
    if type(item["merge_count"]) is not int or item["merge_count"] < 1:
        reject_value(f"{prefix} has invalid counters")
    if not isinstance(item["type"], str) or item["type"] not in SUBJECTIVE_TYPES:
        reject_value(f"{prefix} has an invalid type")
    if item["epistemic_status"] != "subjective":
        reject_value(f"{prefix} has an invalid epistemic status")
    if item["source"] != EPISODE_SOURCE:
        reject_value(f"{prefix} has an invalid source")
    importance = item["importance"]
    if isinstance(importance, bool) or not isinstance(importance, (int, float)) or not 0.0 <= importance <= 1.0:
        reject_value(f"{prefix} has invalid importance")
    if any(not isinstance(item[name], str) or not item[name] for name in ("time", "text")):
        reject_value(f"{prefix} has empty text fields")
    if not isinstance(item["keywords"], list) or any(not isinstance(word, str) for word in item["keywords"]):
        reject_value(f"{prefix} has invalid string lists")


def _write_if_changed(path: Path, text: str) -> bool:
    """@brief 存储里唯一的写文件处：按 UTF-8 编码，文件存在且字节相同时不动文件并返回假，否则建出上级目录、写出字节
    并返回真。"""
    raw = text.encode("utf-8")
    if path.is_file() and path.read_bytes() == raw:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return True

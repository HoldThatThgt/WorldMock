"""@brief WorldMock 主观记忆存储，承载近二十四小时同类型相似文本合并规则。"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any

from agentsociety2.agent.memory import AgentMemoryStore

#: 同类型主观正文达到此相似度才合并。
MEMORY_MERGE_JACCARD_THRESHOLD = 0.6
#: 同类型主观正文只在最近二十四小时合并。
MEMORY_MERGE_WINDOW = timedelta(hours=24)


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


class WorldMockMemoryStore(AgentMemoryStore):
    """@brief 复用框架检查点与检索，仅由宿主决定主观条目相似合并。"""

    def append_episodes(
        self, episodes: list[dict[str, Any]], *, tick: int, t: datetime, step_count: int,
    ) -> list[dict[str, Any]]:
        """@brief 保存本步主观记忆，近二十四小时同类型高相似内容合并。

        :param episodes: 已验证的本步记忆。
        :param tick: 当前模拟跨度秒数。
        :param t: 当前模拟时间。
        :param step_count: 当前人物步骤号。
        :returns: 新建和合并记录的独立副本。
        """
        self.ensure()
        state = self.read_state()
        counter = int(state.get("episode_counter") or 0)
        records = self._load_episodes_cache()
        written: list[dict[str, Any]] = []
        merged_targets: list[dict[str, Any]] = []
        for raw in episodes:
            text = " ".join(str(raw.get("text") or "").strip().split())
            if not text:
                logging.error("Memory store received an empty memory text")
                raise RuntimeError("Memory store received an empty memory text")
            episode_type = str(raw.get("type") or "observation").strip() or "observation"
            target = self._find_merge_target(
                records, (text, episode_type, t),
            )
            if target is not None:
                target["merge_count"] = int(target.get("merge_count") or 1) + 1
                target["time"] = t.isoformat()
                merged_targets.append(target)
                continue
            counter += 1
            record = self._build_episode_record(
                raw, text, episode_type, (tick, t, step_count, counter),
            )
            records.append(record)
            written.append(record)
        self._persist_episode_records(records, written, merged_targets)
        state["episode_counter"] = counter
        state["pending_episode_count"] = int(
            state.get("pending_episode_count") or 0
        ) + len(written)
        self.write_state(state)
        return deepcopy([*written, *merged_targets])

    def episode_ids(self) -> set[str]:
        """@brief 已存全部条目的身份集合：直接读已载入的条目缓存，不复制条目。"""
        return {str(item["id"]) for item in self._load_episodes_cache()}

    def _persist_episode_records(
        self, records: list[dict[str, Any]], written: list[dict[str, Any]], merged: list[dict[str, Any]],
    ) -> None:
        """@brief 合并时重写既有记录，仅新建时追加。"""
        if merged:
            self._rewrite_episodes_file(records)
        elif written:
            with self.episodes_path.open("a", encoding="utf-8") as output:
                for record in written:
                    output.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def _find_merge_target(
        self, records: list[dict[str, Any]], context: tuple[str, str, datetime],
    ) -> dict[str, Any] | None:
        """@brief 在最近记录中寻找类型、时间窗口与正文相似度均合格的合并目标。"""
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

    def _rewrite_episodes_file(self, records: list[dict[str, Any]]) -> None:
        """@brief 以合并结果整体重写 episodes.jsonl。

        :param records: 合并后的全部记忆记录。
        """
        payload = "".join(
            json.dumps(item, ensure_ascii=False, default=str) + "\n"
            for item in records
        )
        self.episodes_path.write_text(payload, encoding="utf-8")

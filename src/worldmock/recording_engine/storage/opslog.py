"""@brief 运行期结构化 JSON-lines 操作日志，用于长运行问题定位，不参与审计。"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO
from zoneinfo import ZoneInfo

#: 操作日志的封闭组件与级别集合，供埋点复用保持取值稳定。
OPS_COMPONENTS = frozenset(
    {"engine", "agent_runtime", "memory", "commerce", "travel", "checkpoint", "runtime", "push"})
OPS_LEVELS = frozenset({"info", "warn", "error"})


def transport_wait_record(call_id: str, attempts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """@brief 一次模型往来在结果之前失败发送的运维记录正文：失败次数、累计等待毫秒数与最后一次的错误链。"""
    waited = sum(item["wait_ms"] for item in attempts if item["wait_ms"] is not None)
    return {"code": "MODEL_TRANSPORT_WAIT",
            "detail": f"Model call {call_id} failed {len(attempts)} sends and waited {waited} ms",
            "call_id": call_id, "failed_sends": len(attempts), "waited_ms": waited,
            "last_error": attempts[-1]["error"]}


class OpsLogger:
    """@brief 按模拟日切分文件的追加式操作日志写入器；写入失败只记 stderr。"""

    def __init__(self, run_dir: Path, timezone_name: str = "UTC") -> None:
        """@brief 绑定运行目录与场景时区；文件句柄延迟到首次写入创建。"""
        self._root = Path(run_dir).resolve() / "logs"
        self._timezone = ZoneInfo(timezone_name)
        self._handles: dict[str, TextIO] = {}

    def log(self, sim_time: int | datetime | None, component: str, level: str, message: dict[str, Any]) -> None:
        """@brief 追加一条记录并立即 flush；任何自身失败都被吞掉不影响主流程。

        :param sim_time: 模拟时间（epoch 秒或带时区 datetime），None 时退化为墙钟。
        :param component: 稳定组件名，取值见 :data:`OPS_COMPONENTS`。
        :param level: info、warn 或 error。
        :param message: 必含 code 英文短码与 detail 英文说明，可包含事件关联键；None 值不落盘。
        """
        try:
            moment = self._moment(sim_time)
            record = {"at": datetime.now(timezone.utc).isoformat(),
                      "sim_time": moment.isoformat(), "component": component,
                      "level": level, "code": message["code"], "detail": message["detail"]}
            record.update({key: value for key, value in message.items() if value is not None})
            handle = self._handle(moment)
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
        except Exception as error:
            print(f"opslog write failed: {error!r}", file=sys.stderr)

    def close(self) -> None:
        """@brief 关闭全部日文件句柄；关闭失败同样只记 stderr。"""
        while self._handles:
            _, handle = self._handles.popitem()
            try:
                handle.close()
            except Exception as error:
                print(f"opslog close failed: {error!r}", file=sys.stderr)

    def _moment(self, sim_time: int | datetime | None) -> datetime:
        """@brief 将任意模拟时间输入统一为场景时区的带时区时刻。"""
        if isinstance(sim_time, datetime):
            return sim_time if sim_time.tzinfo else sim_time.replace(tzinfo=timezone.utc)
        if sim_time is None:
            return datetime.now(timezone.utc).astimezone(self._timezone)
        return datetime.fromtimestamp(sim_time, self._timezone)

    def _handle(self, moment: datetime) -> TextIO:
        """@brief 返回模拟日期对应文件的打开句柄，跨实例保持追加模式。"""
        day = moment.astimezone(self._timezone).strftime("%Y%m%d")
        handle = self._handles.get(day)
        if handle is None:
            self._root.mkdir(parents=True, exist_ok=True)
            handle = (self._root / f"ops-{day}.jsonl").open("a", encoding="utf-8")
            self._handles[day] = handle
        return handle

#!/usr/bin/env python3
"""按记录比较两次运行：精确路径屏蔽、双射身份映射、声明的记录形式重建规则；只用标准库，不导入被比较的代码。

@file     compare_runs.py
@brief    字段分类见 docs/design/05-recording-engine.md「运行比对」。两侧都以只读方式打开，比较事件（读导出的
          events.jsonl，即生产解码器写出的形式）、各段运行清单、重启点与日终世界、终态、（可选）全部检查点和导出的
          每个工件。身份只在声明路径上映射到序号占位，屏蔽只在精确路径上生效，记录形式的变化只能由 RULES 里声明的
          规则对齐，规则只能落在 ALLOWED_RULE_TARGETS 上。

用法：
  python scripts/compare_runs.py runs --base-run RUN_DIR [RUN_DIR ...] --base-export DIR
         --head-run RUN_DIR [RUN_DIR ...] --head-export DIR [--cutoff SEQ] [--rule NAME[=BASE:HEAD] ...]
         [--every-checkpoint] --report FILE
  python scripts/compare_runs.py events --base-run RUN_DIR --base-seq SEQ --head-run RUN_DIR --head-seq SEQ
         --report FILE
"""

from __future__ import annotations

import argparse
import functools
import gzip
import hashlib
import io
import json
import logging
import re
import sqlite3
import tarfile
import zlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import NoReturn
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

#: 屏蔽占位；键保留，存在与否照常比较。
MASK = "<masked>"
#: 事件只屏蔽顶层两项壁钟。
EVENT_MASKS = frozenset({"recorded_at", "wall_elapsed_ms"})
#: 模型响应信封里的失败发送记录（壁钟与传输错误）。
MODEL_RESPONSE_MASKS = frozenset({"data.transport_attempts"})
#: 运行清单的代码身份、模型地址与 lineage 的壁钟和丢弃尾段。
MANIFEST_MASKS = frozenset({
    "code_sha256", "git_commit", "git_dirty",
    "model_identity.base_url", "stage_model_identity.base_url", "stage_model_identity.metadata_url",
    "stage_model_identity.model_metadata.metadata_url", "model_metadata.metadata_url",
    "lineage[].last_recorded_at", "lineage[].branched_at",
    "lineage[].last_event_seq", "lineage[].last_event_time", "lineage[].discarded_events", "lineage[].end_reason",
    "lineage[].end_error",
})
#: 工作区 AGENT.json 的壁钟与地址：初始化时刻、模型服务地址、工作区所在的机器路径（AS2 agent/base/agent.py:750）。
AGENT_MASKS = frozenset({"initialized_at", "profile.model_identity.base_url", "profile.model_identity.metadata_url",
                         "profile.model_identity.model_metadata.metadata_url", "workspace.root"})
#: 质量报告里由壁钟导出的项与丢弃尾段。
QUALITY_MASKS = frozenset({
    "acceleration.observed_wall_seconds", "acceleration.speedup", "acceleration.wall_seconds_by_run",
    "model_usage.estimated_cost.known_estimate_usd", "model_usage.estimated_cost.complete_estimate_usd",
    "model_usage.by_actual_model[].known_estimated_cost_usd",
    "model_usage.by_call_class[].peak_responses", "model_usage.by_call_class[].peak_share",
    "model_usage.by_call_class[].known_estimated_cost_usd",
    "lineage.downtime_seconds", "lineage.parents[].downtime_seconds",
    "lineage.end_reason_counts", "lineage.parents[].discarded_events", "lineage.parents[].end_reason",
    "lineage.parents[].end_error",
})
#: 导出清单的屏蔽：根上的 lineage 各项，以及冻结运行清单下的全部运行清单屏蔽。
EXPORT_MANIFEST_MASKS = frozenset({path for path in MANIFEST_MASKS if path.startswith("lineage")}
                                  | {f"frozen_run_manifest.{path}" for path in MANIFEST_MASKS})
#: 声明的事件身份与引用路径。
EVENT_REFERENCES = {
    "event_id": "event", "run_id": "run", "caused_by[]": "event",
    "data.checkpoint_id": "checkpoint", "data.parent_checkpoint_id": "checkpoint",
    "data.activity.origin_request_event_id": "event", "data.activity.data.accepted_event_id": "event",
    "data.data.activity.origin_request_event_id": "event", "data.data.activity.data.accepted_event_id": "event",
    "data.data.activities[].origin_request_event_id": "event",
    "data.data.activities[].data.accepted_event_id": "event",
    "data.data.memory_sources[].verified_event_ids[]": "event",
    "data.body_settlement_event_id": "event", "data.source_event_ids[]": "event",
    "data.errand.origin_event_id": "event",
    "data.observation_event_id": "event", "data.verified_event_ids[]": "event", "data.progress_event_ids[]": "event",
    "data.active_activities[].origin_request_event_id": "event",
    "data.active_activities[].data.accepted_event_id": "event",
    "data.verified_experiences[].source_event_id": "event",
    "data.memory_sha256": "memory-digest", "data.memory_before_sha256": "memory-digest",
}
#: 按首次出现编号的派生摘要：MEMORY.md 的已核验经历行写着来源事件身份（agents/life_memory.py:306），
#: 它的文件摘要随事件身份变化，只按出现次序成双射比较；内容在每个比较的工作区快照里逐行比较。
FIRST_SEEN_KINDS = frozenset({"memory-digest"})
#: MEMORY.md 里已核验经历行的来源事件身份。
MEMORY_SOURCE = re.compile(r"来源事件 ([0-9a-f]{32})")
#: 声明的世界状态身份与引用路径（基一侧的记录形式）。
STATE_REFERENCES = {
    "run_id": "run",
    "activities.*.origin_request_event_id": "event", "activities.*.data.accepted_event_id": "event",
    "processed_actions.*.result.data.activity.origin_request_event_id": "event",
    "processed_actions.*.result.data.activity.data.accepted_event_id": "event",
    "processed_actions.*.result.data.memory_sources[].verified_event_ids[]": "event",
}
#: 检查点记录与 life 快照的身份路径。
CHECKPOINT_REFERENCES = {"checkpoint_id": "checkpoint", "run_id": "run"}
#: 声明的运行清单身份与引用路径。
MANIFEST_REFERENCES = {
    "run_id": "run", "parent_run_id": "run", "source_group_id": "run", "source_run_ids[]": "run",
    "lineage[].run_id": "run", "parent_checkpoint_id": "checkpoint", "lineage[].checkpoint_id": "checkpoint",
}
#: 声明的导出工件身份与引用路径，按工件文件名分列。
EXPORT_REFERENCES = {
    "manifest.json": {"run_id": "run", "source_group_id": "run", "source_run_ids[]": "run", "lineage[].run_id": "run",
                      "lineage[].checkpoint_id": "checkpoint",
                      **{f"frozen_run_manifest.{path}": kind for path, kind in MANIFEST_REFERENCES.items()}},
    "activities.jsonl": {"origin_request_event_id": "event", "data.accepted_event_id": "event",
                         "source_event_ids[]": "event"},
    "app-usage.jsonl": {"source_event_id": "event"},
    "verified-experiences.jsonl": {"source_event_id": "event"},
    "purchases.jsonl": {"request_event_id": "event", "source_event_id": "event"},
    "location-stays.jsonl": {"enter_source_event_id": "event", "leave_source_event_id": "event"},
    "asset-index.jsonl": {},
    "quality.json": {
        "run_id": "run", "source_group_id": "run", "lineage.parents[].run_id": "run",
        "lineage.parents[].checkpoint_id": "checkpoint",
        "run_outcome.run_ended_events[]": "event", "run_outcome.run_failed_events[]": "event",
        "memory_references.problems[].event_id": "event",
        "day_plan_metrics.*.external_constraints.source_event_ids[]": "event",
        "day_plan_metrics.*.external_constraints.other_rejections.source_event_ids[]": "event",
    },
}
#: 导出里按行比较的 JSONL 工件。
JSONL_ARTIFACTS = ("activities.jsonl", "verified-experiences.jsonl", "purchases.jsonl", "app-usage.jsonl",
                   "location-stays.jsonl", "asset-index.jsonl")
#: 重建规则唯一允许的（范围, 路径）。
ALLOWED_RULE_TARGETS = frozenset({
    ("event:model_response", "data.request"), ("checkpoint-row", "body"), ("workspace-asset", "content"),
    ("state", "activities"), ("state", "processed_actions"), ("state", "schema_version"),
    ("run-manifest", "schema_version"), ("export-manifest", "schema_version"), ("quality", "schema_version"),
    ("run-manifest", "lineage"), ("export-manifest", "lineage"), ("export-manifest", "frozen_run_manifest.lineage"),
    ("quality", "lineage"), ("event:model_response", "data.transport_attempts"),
    ("quality", "model_usage.by_call_class"),
})
#: 链组身份不是任何一段运行身份时（测试里直接建的引擎运行），它在运行占位里的序号。
GROUP_ORDINAL = 0
#: 报告里详细列出的差异条数、每条的叶子路径数与取值截断长度。
DETAIL_LIMIT, PATH_LIMIT, VALUE_LIMIT = 50, 20, 300
#: 活动与已处理动作投影里的终态集合。
_ENDED_ACTIVITY = frozenset({"completed", "cancelled", "failed", "truncated"})
_ENDED_RESULT = frozenset({"completed", "cancelled", "failed"})
_SHA_FIELDS = frozenset({"sha256", "config_sha256", "manifest_sha256"})
_KIND = "\0kind"
_SIDES = ("base", "head")


class ComparisonError(RuntimeError):
    """@brief 输入不完整或损坏，比较不能进行。"""


@dataclass(frozen=True)
class Rule:
    """@brief 一条声明的重建规则。"""

    name: str  #: 规则名，命令行 --rule 用它启用
    kind: str  #: rebuild、decode、projection、value、added 或 replaced
    scope: str  #: 规则作用的记录范围
    path: str  #: 范围内的字段路径
    reader_search: str  #: 支撑这条规则的读取者检索
    chapter: str  #: 引入这种记录形式的章节


@dataclass(frozen=True)
class AppliedRule:
    """@brief 本次启用的规则及数值规则的两端取值；counts 记本次比较里的应用次数。"""

    rule: Rule  #: 启用的规则
    base_value: object = None  #: 数值规则基一侧的值
    head_value: object = None  #: 数值规则头一侧的值
    counts: Counter = field(default_factory=Counter, compare=False)  #: applied 与 terminal_overwrites 计数


def _rule(name: str, kind: str, target: tuple[str, str], reader_search: str, chapter: str) -> tuple[str, Rule]:
    """@brief 构造规则表的一项。"""
    return name, Rule(name, kind, target[0], target[1], reader_search, chapter)


#: 规则表，每条带读取者检索。
RULES = dict([
    _rule("request-copy", "rebuild", ("event:model_response", "data.request"),
          "Chapter 03 inventory: continuation.py:105-117, progress_layer.py:305-335, decision_coordinator.py:597-608, "
          "store.py:186-200, quality.py:583-643, behavior_metrics.py:239-249, status.py:86-99, "
          "scripts/inspect_run.py:526-600, tools/run-viewer-context-view.js", "03"),
    _rule("checkpoint-zlib", "decode", ("checkpoint-row", "body"),
          "Readers of checkpoint rows: store.get_checkpoint (store.py:334-360), status.py:39-40, "
          "the export embed (export.py:86-92)", "02"),
    _rule("workspace-gzip", "decode", ("workspace-asset", "content"),
          "Readers of workspace snapshots: restore_directory (checkpoint.py:54-73), export copy (export.py:90-91)",
          "02"),
    _rule("activities-array", "projection", ("state", "activities"),
          "Same content, order added: the base form sorts keys, so the base never recorded an order", "02"),
    _rule("processed-actions-pruned", "projection", ("state", "processed_actions"),
          "Readers of processed actions: decision_coordinator.py:297, :585, same_moment.py:228, time_advance.py:306, "
          "activity_control.py:468-474; every pruned entry is contained in its events (chapter 02)", "02"),
    _rule("world-schema-version", "value", ("state", "schema_version"), "Structure version, not a fact", "02"),
    _rule("run-manifest-version", "value", ("run-manifest", "schema_version"), "Structure version, not a fact",
          "05"),
    _rule("export-manifest-version", "value", ("export-manifest", "schema_version"),
          "Structure version, not a fact", "03"),
    _rule("quality-version", "value", ("quality", "schema_version"), "Structure version, not a fact", "05, 06"),
    _rule("lineage", "added", ("run-manifest", "lineage"),
          "New field: readers are status, export and quality only (chapter 05)", "05"),
    _rule("transport-attempts", "added", ("event:model_response", "data.transport_attempts"),
          "Never read by a prompt: kept out of data.response (chapter 05)", "05"),
    _rule("cost-by-class", "replaced", ("quality", "model_usage.by_call_class"),
          "Report only, recomputed from the compared events by export certification; the class rows sum to the "
          "old purpose rows (chapter 06)", "06"),
])


def _refuse(message: str) -> NoReturn:
    """@brief 记录英文错误日志后以 ComparisonError 拒绝。"""
    logger.error("%s", message)
    raise ComparisonError(message)


def canonical_bytes(value: object) -> bytes:
    """@brief 与 canonical_json 相同的唯一编码。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _sha(content: bytes) -> str:
    """@brief 字节的 SHA-256 十六进制摘要。"""
    return hashlib.sha256(content).hexdigest()


def _connect(run: Path) -> sqlite3.Connection:
    """@brief 只读打开一个运行库（URI 形式同时处理 Windows 盘符与空格）。"""
    database = Path(run) / "footprints.sqlite"
    if not database.is_file():
        _refuse(f"Run directory {run} has no footprints.sqlite")
    return sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)


def _read_json(path: Path) -> dict:
    """@brief 读 UTF-8 JSON 文件；缺失即拒绝。"""
    if not path.is_file():
        _refuse(f"Required file {path} is missing")
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Chain:
    """@brief 按次序读一条运行链并核对父子链接；只读打开。"""

    runs: tuple[Path, ...]  #: 各段运行目录，按链次序
    manifests: tuple[dict, ...]  #: 各段 manifest.json
    restored: tuple[dict | None, ...]  #: 各段 restored-checkpoint.json（第一段为 None）
    positions: tuple[int, ...]  #: 各边界的链位置 L_k（k = 1..n-1）
    timezone: str  #: 运行时区（最后一段清单的 window.timezone）

    @classmethod
    def load(cls, runs: list[Path]) -> Chain:
        """@brief 读各段清单与恢复点，核对链接后返回；任何断链以 ComparisonError 拒绝。"""
        runs = tuple(Path(run) for run in runs)
        if not runs:
            _refuse("Run chain is empty")
        manifests = tuple(_read_json(run / "manifest.json") for run in runs)
        if manifests[0].get("parent_run_id") is not None:
            _refuse("Run chain is broken at segment 1: the first directory is a branch; "
                    "pass every segment directory in order")
        restored, positions = [None], []
        final = _connect(runs[-1])
        try:
            for index in range(1, len(runs)):
                record = _segment_link(index + 1, runs[index], (manifests[index - 1], manifests[index]), final)
                restored.append(record)
                positions.append(record["last_event_seq"])
        finally:
            final.close()
        return cls(runs, manifests, tuple(restored), tuple(positions), manifests[-1]["window"]["timezone"])

    def database(self, index: int = -1) -> sqlite3.Connection:
        """@brief 只读打开第 index 段（0 起，缺省最后一段）的运行库。"""
        return _connect(self.runs[index])

    def segment_of_run(self, run_id: str) -> int:
        """@brief 清单 run_id 等于 run_id 的段次序（1 起）；不在链里即拒绝。"""
        for index, manifest in enumerate(self.manifests, start=1):
            if manifest["run_id"] == run_id:
                return index
        _refuse(f"Run {run_id} is not a segment of the chain")


def _segment_link(segment: int, run: Path, manifests: tuple[dict, dict], final: sqlite3.Connection) -> dict:
    """@brief 核对第 segment 段与前一段的链接，返回第 segment 段恢复的检查点记录。"""
    parent, manifest = manifests
    path = run / "restored-checkpoint.json"
    record = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    reason = None
    if manifest.get("parent_run_id") != parent["run_id"]:
        reason = "parent run id differs"
    elif record is None:
        reason = "restored checkpoint is missing"
    elif ((record.get("checkpoint_id"), record.get("run_id"))
          != (manifest.get("parent_checkpoint_id"), parent["run_id"])):
        reason = "restored checkpoint differs from the manifest"
    elif not _branch_event_matches(final, record, manifest["run_id"]):
        reason = "branch event is missing"
    if reason is not None:
        _refuse(f"Run chain is broken at segment {segment}: {reason}")
    return record


def _branch_event_matches(final: sqlite3.Connection, record: dict, run_id: str) -> bool:
    """@brief 最后一段库里 L_{k-1}+1 处必须是本段的 branch_created，指向恢复的检查点。"""
    row = final.execute("SELECT body FROM events WHERE event_seq=?", (record["last_event_seq"] + 1,)).fetchone()
    if row is None:
        return False
    event = json.loads(row[0])
    data = event.get("data") or {}
    return (event.get("event_type") == "environment_event" and data.get("kind") == "branch_created"
            and data.get("parent_checkpoint_id") == record["checkpoint_id"] and event.get("run_id") == run_id)


@dataclass(frozen=True)
class Side:
    """@brief 比较的一侧。"""

    chain: Chain  #: 这一侧的运行链
    export: Path  #: 这一侧的导出目录


@dataclass(frozen=True)
class Options:
    """@brief 比较选项。"""

    cutoff: int | None  #: 比较到的事件序号；None 为最后一段库的末事件
    rules: tuple[AppliedRule, ...]  #: 启用的规则
    every_checkpoint: bool  #: 是否比较链上每个检查点


def _last_seq(chain: Chain) -> int:
    """@brief 最后一段库的末事件序号。"""
    with _closing(chain.database()) as connection:
        return connection.execute("SELECT COALESCE(MAX(event_seq),0) FROM events").fetchone()[0]


class _closing:
    """@brief 用完即关的只读连接（sqlite3 连接的 with 只管事务，不关连接）。"""

    def __init__(self, connection: sqlite3.Connection):
        """@brief 接管一个连接。"""
        self.connection = connection

    def __enter__(self) -> sqlite3.Connection:
        """@brief 交出连接。"""
        return self.connection

    def __exit__(self, *_exc) -> None:
        """@brief 关闭连接。"""
        self.connection.close()


_CHECKPOINT_EVENTS = ("SELECT event_seq, json_extract(body,'$.occurred_at'), "
                      "json_extract(body,'$.data.checkpoint_id'), "
                      "json_extract(body,'$.run_id') FROM events WHERE json_extract(body,'$.agent_id') IS NULL "
                      "AND json_extract(body,'$.event_type')='checkpoint_created' AND event_seq<=? ORDER BY event_seq")


class IdMaps:
    """@brief 两侧身份到序号占位的映射，记下不是身份的引用。"""

    def __init__(self):
        """@brief 空映射；用 load 构造。"""
        self.ordinals = {kind: {side: {} for side in _SIDES}
                         for kind in ("event", "checkpoint", "run", *FIRST_SEEN_KINDS)}
        self.events = {side: [] for side in _SIDES}
        self.checkpoints = {side: [] for side in _SIDES}
        self.problems: list[dict] = []
        self.cutoff = 0

    @classmethod
    def load(cls, base: Chain, head: Chain, cutoff: int) -> IdMaps:
        """@brief 只用 SQL 读两侧最后一段库的事件身份与检查点事件，按段次序登记运行身份。"""
        maps = cls()
        maps.cutoff = cutoff
        for side, chain in zip(_SIDES, (base, head)):
            with _closing(chain.database()) as connection:
                rows = connection.execute("SELECT event_seq, event_id FROM events WHERE event_seq<=? "
                                          "ORDER BY event_seq", (cutoff,)).fetchall()
                points = connection.execute(_CHECKPOINT_EVENTS, (cutoff,)).fetchall()
            maps.events[side] = rows
            maps.ordinals["event"][side] = {event_id: seq for seq, event_id in rows}
            maps.checkpoints[side] = points
            maps.ordinals["checkpoint"][side] = {checkpoint_id: seq for seq, _, checkpoint_id, _ in points}
            runs = {manifest["run_id"]: index for index, manifest in enumerate(chain.manifests, start=1)}
            runs.setdefault(chain.manifests[0].get("source_group_id"), GROUP_ORDINAL)
            maps.ordinals["run"][side] = runs
        return maps

    def normalize(self, value: object, kind: str, side: str, path: str) -> object:
        """@brief 声明路径上的身份换成 <kind:序号>；None 原样，不是该种身份的值原样保留并记为问题。"""
        if value is None:
            return None
        if kind in FIRST_SEEN_KINDS and isinstance(value, str):
            seen = self.ordinals[kind][side]
            seen.setdefault(value, len(seen) + 1)
        ordinal = self.ordinals[kind][side].get(value) if isinstance(value, str) else None
        if ordinal is None:
            self.problems.append({"kind": "REFERENCE_NOT_AN_ID", "id_kind": kind, "side": side, "path": path,
                                  "value": value})
            return value
        return f"<{kind}:{ordinal}>"


class Digests:
    """@brief 原始摘要到规范化摘要的映射（素材、清单、导出工件），两侧各一份。"""

    def __init__(self, base: Side, head: Side):
        """@brief 绑定两侧；规范化上下文由 compare 在建好后交给 contexts。"""
        self.sides = {"base": base, "head": head}
        self.maps: dict[str, dict[str, tuple[str, int]]] = {side: {} for side in _SIDES}
        self.contexts: dict[str, NormalizeContext] = {}

    def asset(self, reference: dict, side: str) -> tuple[str, int]:
        """@brief 读出并核对一份素材，按来源规范化，返回（规范化摘要, 规范化字节数）；结果按原始摘要缓存。"""
        raw_sha = reference["sha256"]
        known = self.maps[side].get(raw_sha)
        if known is not None:
            return known
        content = self._read(raw_sha, side)
        if _sha(content) != raw_sha or len(content) != reference.get("size_bytes", len(content)):
            _refuse(f"Asset {raw_sha} does not match its reference")
        normalized = normalize_asset(content, reference, self.contexts[side])
        result = (_sha(normalized), len(normalized))
        self.maps[side][raw_sha] = result
        return result

    def _read(self, raw_sha: str, side: str) -> bytes:
        """@brief 从这一侧第一个存有该素材的运行目录读取，都没有时读导出的 assets/。"""
        owner = self.sides[side]
        for run in (*owner.chain.runs, owner.export):
            path = Path(run) / "assets" / raw_sha
            if path.is_file():
                return path.read_bytes()
        _refuse(f"Asset {raw_sha} is missing from the {side} side")

    def register(self, raw_sha: str, side: str, normalized: bytes) -> str:
        """@brief 登记清单或导出工件的原始摘要到规范化摘要与字节数。"""
        result = (_sha(normalized), len(normalized))
        self.maps[side][raw_sha] = result
        return result[0]

    def manifest(self, raw_sha: str, side: str) -> str:
        """@brief 运行清单原始摘要（即检查点里的 manifest_sha256）对应的规范化摘要；未登记时原样。"""
        return self.maps[side].get(raw_sha, (raw_sha, 0))[0]

    def artifact(self, raw_sha: str, side: str) -> str:
        """@brief 导出工件原始摘要对应的规范化摘要；未登记时原样。"""
        return self.maps[side].get(raw_sha, (raw_sha, 0))[0]


@dataclass
class NormalizeContext:
    """@brief 一侧规范化所需的映射、规则与待配对的请求。"""

    side: str  #: base 或 head
    ids: IdMaps  #: 两侧身份映射
    digests: Digests  #: 两侧摘要映射
    rules: tuple[AppliedRule, ...]  #: 启用的规则
    requests: dict[str, dict]  #: 头一侧按事件身份暂存的 model_request 请求，等到其响应
    urls: tuple[str, str] = ("", "")  #: 这一侧运行清单的 base_url 与 metadata_url
    code_sha256: str | None = None  #: 这一侧最后一段清单的代码摘要
    masks: dict[str, Counter] = field(default_factory=dict)  #: 每种记录里每条屏蔽路径生效的次数

    def mask_counts(self, record: str) -> Counter:
        """@brief 一种记录的屏蔽计数。"""
        return self.masks.setdefault(record, Counter())


@dataclass
class CompareContext:
    """@brief 一次比较的共享上下文。"""

    options: Options  #: 比较选项（规则为本次比较的新副本）
    ids: IdMaps  #: 两侧身份映射
    base: NormalizeContext  #: 基一侧
    head: NormalizeContext  #: 头一侧


def _enabled(rules: tuple[AppliedRule, ...], name: str) -> AppliedRule | None:
    """@brief 按名称取启用的规则。"""
    return next((rule for rule in rules if rule.rule.name == name), None)


def flatten(value: object, path: str = "", out: dict | None = None) -> dict[str, object]:
    """@brief obsdiff 写法的叶子展开：对象键接 .键，列表给出 #len 与 [i]；空对象本身算一个叶子。"""
    out = {} if out is None else out
    if isinstance(value, dict) and value:
        for key, item in value.items():
            flatten(item, f"{path}.{key}", out)
    elif isinstance(value, list):
        out[f"{path}#len"] = len(value)
        for index, item in enumerate(value):
            flatten(item, f"{path}[{index}]", out)
    else:
        out[path] = value
    return out


def _shown(value: object) -> str:
    """@brief 报告里的一个取值：JSON 文本截到 VALUE_LIMIT 字符；缺失写 <missing>。"""
    if value is _MISSING:
        return "<missing>"
    return json.dumps(value, ensure_ascii=False, sort_keys=True)[:VALUE_LIMIT]


_MISSING = object()


def leaf_diff(base: object, head: object, limit: int | None = PATH_LIMIT) -> list[dict]:
    """@brief 两值的不同叶子路径（按路径排序），取值按 JSON 文本比较，最多 limit 条（None 不限）。"""
    left, right = flatten(base), flatten(head)
    paths = []
    for path in sorted(set(left) | set(right)):
        first, second = left.get(path, _MISSING), right.get(path, _MISSING)
        if first is _MISSING or second is _MISSING or canonical_bytes(first) != canonical_bytes(second):
            paths.append({"path": path, "base": _shown(first), "head": _shown(second)})
            if limit is not None and len(paths) >= limit:
                break
    return paths


def mask_paths(value: object, paths: frozenset[str], path: str = "", counts: Counter | None = None) -> object:
    """@brief 只在精确路径上屏蔽：值换成 MASK，键保留；counts 记每条路径生效的次数。"""
    if path in paths:
        if counts is not None:
            counts[path] += 1
        return MASK
    if isinstance(value, dict):
        return {key: mask_paths(item, paths, f"{path}.{key}" if path else key, counts) for key, item in value.items()}
    if isinstance(value, list):
        return [mask_paths(item, paths, f"{path}[]", counts) for item in value]
    return value


def _split(path: str) -> list[str]:
    """@brief 把声明路径拆成键、[] 与 * 记号（a.b[].c[] 拆为 a b [] c []）。"""
    tokens = []
    for part in path.split("."):
        name = part.removesuffix("[]").removesuffix("[]")
        tokens.append(name)
        tokens.extend(["[]"] * ((len(part) - len(name)) // 2))
    return tokens


@functools.lru_cache(maxsize=None)
def _trie(fields: tuple[tuple[str, str], ...]) -> dict:
    """@brief 声明路径表编成前缀树，叶子记身份种类。"""
    root: dict = {}
    for path, kind in fields:
        node = root
        for token in _split(path):
            node = node.setdefault(token, {})
        node[_KIND] = kind
    return root


def _merged(first: dict | None, second: dict | None) -> dict | None:
    """@brief 合并字面键与 * 两个子树。"""
    if first is None or second is None:
        return first if second is None else second
    merged = dict(second)
    for key, child in first.items():
        merged[key] = _merged(child, merged.get(key)) if isinstance(child, dict) else child
    return merged


def map_ids(value: object, fields: dict[str, str], context: NormalizeContext, path: str = "") -> object:
    """@brief 只在声明路径上做身份映射。"""
    return _map_node(value, _trie(tuple(sorted(fields.items()))), context, path)


def _map_node(value: object, node: dict, context: NormalizeContext, path: str) -> object:
    """@brief 沿前缀树映射一个值；叶子交给 IdMaps.normalize。"""
    kind = node.get(_KIND)
    if kind is not None:
        return context.ids.normalize(value, kind, context.side, path)
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            child = _merged(node.get(key), node.get("*"))
            result[key] = item if child is None else _map_node(item, child, context, f"{path}.{key}")
        return result
    if isinstance(value, list) and "[]" in node:
        return [_map_node(item, node["[]"], context, f"{path}[{index}]") for index, item in enumerate(value)]
    return value


def _is_asset_reference(value: dict) -> bool:
    """@brief 是否是一份素材引用（AssetRef 的四个字段）。"""
    return (isinstance(value.get("sha256"), str) and isinstance(value.get("size_bytes"), int)
            and isinstance(value.get("source"), dict) and "media_type" in value)


def _map_digests(value: object, context: NormalizeContext) -> object:
    """@brief 素材引用换成规范化摘要与字节数；sha256、config_sha256、manifest_sha256 字段按摘要映射替换。"""
    if isinstance(value, list):
        return [_map_digests(item, context) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _map_digests(item, context) for key, item in value.items()}
    if _is_asset_reference(value):
        result["sha256"], result["size_bytes"] = context.digests.asset(value, context.side)
        result["media_type"] = _aligned_media_type(value, context)
        return result
    known = context.digests.maps[context.side]
    for name in _SHA_FIELDS & set(value):
        raw = value[name]
        if isinstance(raw, str) and raw in known:
            result[name] = known[raw][0]
            if name == "sha256" and "size_bytes" in value:
                result["size_bytes"] = known[raw][1]
    return result


def _aligned_media_type(reference: dict, context: NormalizeContext) -> str:
    """@brief 规则 workspace-gzip 下头一侧工作区快照的 application/gzip 按基一侧的 application/x-tar 比较。"""
    rule = _enabled(context.rules, "workspace-gzip")
    if (rule is not None and context.side == "head" and reference["source"].get("kind") == "workspace"
            and reference["media_type"] == "application/gzip"):
        return "application/x-tar"
    return reference["media_type"]


def normalize_asset(content: bytes, reference: dict, context: NormalizeContext) -> bytes:
    """@brief 按素材来源规范化内容：配置文本去掉两个模型地址，工作区归档列成员摘要，life 快照映射身份。"""
    source = reference.get("source") or {}
    kind = source.get("kind")
    if kind == "environment_input" and source.get("path") == "config.toml":
        return _config_content(content, context.urls)
    if kind == "workspace":
        rule = _enabled(context.rules, "workspace-gzip")
        if rule is not None and context.side == "head":
            content = _gunzip(content, reference["sha256"])
            rule.counts["applied"] += 1
        return _workspace_listing(content, context)
    if kind == "life_checkpoint":
        snapshot = map_ids(json.loads(content), CHECKPOINT_REFERENCES, context)
        return canonical_bytes(_map_digests(snapshot, context))
    return content


def _config_content(content: bytes, urls: tuple[str, str]) -> bytes:
    """@brief config.toml 里这一侧的两个带引号的模型地址各恰好一次，换成 "<model-url>"。"""
    text = content.decode("utf-8")
    for url in urls:
        quoted = json.dumps(url)
        if text.count(quoted) != 1:
            _refuse("Frozen config.toml must contain each model URL exactly once")
        text = text.replace(quoted, '"<model-url>"')
    return text.encode("utf-8")


def _gunzip(content: bytes, raw_sha: str) -> bytes:
    """@brief 解开头一侧的 gzip 工作区快照。"""
    try:
        return gzip.decompress(content)
    except (OSError, EOFError, zlib.error):
        _refuse(f"Workspace asset {raw_sha} is not a gzip archive")


def _workspace_listing(content: bytes, context: NormalizeContext) -> bytes:
    """@brief 归档成员按归档次序列成 name、mode、mtime 与规范化内容摘要；AGENT.json 屏蔽壁钟与地址。

    不是 tar 的内容（例如没有启用 workspace-gzip 时头一侧的压缩快照）原样返回，于是作为不同的素材报出。
    """
    rows = []
    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:") as archive:
            for member in archive.getmembers():
                raw = archive.extractfile(member).read() if member.isfile() else b""
                if member.name == "AGENT.json":
                    masked = mask_paths(json.loads(raw), AGENT_MASKS, counts=context.mask_counts("agent"))
                    raw = canonical_bytes(masked)
                elif member.name == "MEMORY.md":
                    raw = _memory_text(raw, context)
                rows.append({"name": member.name, "mode": member.mode, "mtime": member.mtime, "sha256": _sha(raw)})
    except tarfile.TarError:
        logger.exception("Workspace snapshot is not a tar archive; it is compared as raw bytes")
        return content
    return canonical_bytes(rows)


def _memory_text(raw: bytes, context: NormalizeContext) -> bytes:
    """@brief MEMORY.md 里已核验经历行的来源事件身份换成 <event:序号>。"""
    def mapped(match: re.Match) -> str:
        """@brief 一个来源事件身份。"""
        return f"来源事件 {context.ids.normalize(match.group(1), 'event', context.side, 'MEMORY.md')}"

    return MEMORY_SOURCE.sub(mapped, raw.decode("utf-8")).encode("utf-8")


def normalize_event(event: dict, context: NormalizeContext) -> dict:
    """@brief 屏蔽并映射一条事件：顶层壁钟、模型响应的传输记录、声明的身份路径与素材摘要。"""
    context.mask_counts("event").update(EVENT_MASKS & event.keys())
    event = mask_paths(event, EVENT_MASKS)
    if event.get("event_type") == "model_response":
        event = mask_paths(event, MODEL_RESPONSE_MASKS, counts=context.mask_counts("model_response"))
    event = map_ids(event, EVENT_REFERENCES, context)
    return _map_digests(event, context)


def apply_request_copy(event: dict, requests: dict[str, dict], copy_expected: bool) -> dict:
    """@brief 用 caused_by 指向的请求重建响应里的请求副本；model_request 先暂存，响应来时取用。"""
    if event.get("event_type") == "model_request":
        requests[event["event_id"]] = (event.get("data") or {}).get("request")
        return event
    if event.get("event_type") != "model_response":
        return event
    causes = event.get("caused_by") or []
    request = requests.pop(causes[0], None) if causes else None
    if not copy_expected or request is None:
        return event
    data = dict(event["data"])
    data["request"] = request
    return {**event, "data": data}


def drop_added(head: dict, base: dict, paths: tuple[str, ...]) -> dict:
    """@brief 基一侧没有的新增字段从头一侧移除；路径是点分的对象键。没有移除时返回同一个对象。"""
    result = head
    for path in paths:
        result = _drop_path(result, base, path.split("."))
    return result


def _drop_path(head: object, base: object, keys: list[str]) -> object:
    """@brief 沿一条键路径移除头一侧有、基一侧没有的末端键，只复制路径上的对象。"""
    if not isinstance(head, dict) or keys[0] not in head:
        return head
    if len(keys) == 1:
        if isinstance(base, dict) and keys[0] in base:
            return head
        return {key: value for key, value in head.items() if key != keys[0]}
    child = _drop_path(head[keys[0]], base.get(keys[0]) if isinstance(base, dict) else None, keys[1:])
    return head if child is head[keys[0]] else {**head, keys[0]: child}


def align_events(base: dict, head: dict, context: CompareContext) -> tuple[dict, dict]:
    """@brief 一对事件按启用的规则对齐头一侧记录形式（请求副本重建、新增字段移除），在 normalize_event 之前调用。"""
    rules = context.options.rules
    copy_rule = _enabled(rules, "request-copy")
    if copy_rule is not None:
        expected = ((base.get("data") or {}).get("request") is not None
                    and (head.get("data") or {}).get("request") is None)
        rebuilt = apply_request_copy(head, context.head.requests, expected)
        if rebuilt is not head and head.get("event_type") == "model_response":
            copy_rule.counts["applied"] += 1
        head = rebuilt
    attempts = _enabled(rules, "transport-attempts")
    if attempts is not None and head.get("event_type") == "model_response":
        dropped = drop_added(head, base, ("data.transport_attempts",))
        if dropped is not head:
            attempts.counts["applied"] += 1
        head = dropped
    return base, head


def apply_value_rule(base_value: object, head_value: object, rule: AppliedRule) -> object:
    """@brief 结构号只在声明的两值上对齐：头一侧取基一侧的值，其余原样。"""
    if (base_value, head_value) != (rule.base_value, rule.head_value):
        return head_value
    rule.counts["applied"] += 1
    return base_value


def _value_rule(base: dict, head: dict, rule: AppliedRule | None, path: str) -> dict:
    """@brief 在点分路径上应用一条数值规则，返回头一侧的新副本。"""
    if rule is None:
        return head
    *parents, name = path.split(".")
    base_node, result = base, dict(head)
    node = result
    for key in parents:
        base_node = base_node.get(key) if isinstance(base_node, dict) else None
        if not isinstance(node.get(key), dict):
            return result
        node[key] = dict(node[key])
        node = node[key]
    if isinstance(base_node, dict) and name in base_node and name in node:
        node[name] = apply_value_rule(base_node[name], node[name], rule)
    return result


def align_manifests(base: dict, head: dict, scope: str, rules: tuple[AppliedRule, ...]) -> tuple[dict, dict]:
    """@brief 一对运行清单或导出清单按启用的规则对齐（结构号、lineage），在 normalize_manifest 与清单摘要之前调用。"""
    run_version = _enabled(rules, "run-manifest-version")
    if scope == "run-manifest":
        head = _value_rule(base, head, run_version, "schema_version")
        paths = ("lineage",)
    else:
        head = _value_rule(base, head, _enabled(rules, "export-manifest-version"), "schema_version")
        head = _value_rule(base, head, run_version, "frozen_run_manifest.schema_version")
        paths = ("lineage", "frozen_run_manifest.lineage")
    lineage = _enabled(rules, "lineage")
    if lineage is not None:
        dropped = drop_added(head, base, paths)
        if dropped is not head:
            lineage.counts["applied"] += 1
        head = dropped
    return base, head


def project_activities(head_activities: list[dict]) -> dict[str, dict]:
    """@brief 有序活动数组还原成以活动身份为键的对象；身份重复即拒绝。"""
    projected = {}
    for entry in head_activities:
        if entry["activity_id"] in projected:
            _refuse("Head activities repeat an activity identity")
        projected[entry["activity_id"]] = entry
    return projected


def _activity_values(state: dict) -> list[dict]:
    """@brief 世界里的活动，无论对象形式还是数组形式。"""
    activities = state.get("activities") or {}
    return list(activities.values()) if isinstance(activities, dict) else list(activities)


def _terminal_overwrite(key: str, base_entry: dict, head_entry: dict, activities: dict) -> bool:
    """@brief 基一侧在活动结束那一秒用终态结果覆盖了起始结果，而头一侧仍是起始结果。"""
    base_result, head_result = base_entry.get("result") or {}, head_entry.get("result") or {}
    activity = activities.get(base_result.get("activity_id"))
    return (base_result != head_result and base_result.get("action_id") == key and activity is not None
            and activity.get("origin_action_id") == key and activity.get("status") in _ENDED_ACTIVITY
            and base_result.get("status") in _ENDED_RESULT
            and head_result.get("activity_id") == base_result.get("activity_id"))


def project_processed_actions(base_state: dict, head_actions: dict) -> tuple[dict, list[str], int]:
    """@brief 基一侧只保留头一侧仍有的已处理动作，对齐终态覆盖，列出头一侧缺少或多留的条目。

    @return 投影后的基一侧映射（缺少的键保留基一侧条目、多留的键不在其中，使普通比较报出差异）、
            差异路径 .processed_actions.<key> 列表、终态覆盖的次数。
    """
    base_actions = base_state["processed_actions"]
    projected = {key: base_actions[key] for key in head_actions if key in base_actions}
    activities = {item["activity_id"]: item for item in _activity_values(base_state)}
    overwrites = 0
    for key, entry in projected.items():
        if _terminal_overwrite(key, entry, head_actions[key], activities):
            projected[key] = {**entry, "result": head_actions[key]["result"]}
            overwrites += 1
    required = {key for key, entry in base_actions.items()
                if (entry.get("result") or {}).get("occurred_at") == base_state["current_time"]}
    required |= {item["origin_action_id"] for item in activities.values() if item.get("status") in ("active", "paused")}
    missing = sorted(key for key in required if key not in head_actions)
    extra = sorted(key for key in head_actions if key not in required)
    for key in missing:
        if key in base_actions:
            projected[key] = base_actions[key]
    for key in extra:
        projected.pop(key, None)
    return projected, [f".processed_actions.{key}" for key in (*missing, *extra)], overwrites


def align_states(base: dict, head: dict, rules: tuple[AppliedRule, ...]) -> tuple[dict, dict]:
    """@brief 两侧规范化之前按启用的规则对齐记录形式：活动数组还原、已处理动作投影、结构号；读原始身份。"""
    base, head = dict(base), dict(head)
    array = _enabled(rules, "activities-array")
    if array is not None and isinstance(head.get("activities"), list):
        head["activities"] = project_activities(head["activities"])
        array.counts["applied"] += 1
    pruned = _enabled(rules, "processed-actions-pruned")
    if pruned is not None and isinstance(base.get("processed_actions"), dict):
        projected, paths, overwrites = project_processed_actions(base, head.get("processed_actions") or {})
        base["processed_actions"] = projected
        pruned.counts["applied"] += 1
        pruned.counts["terminal_overwrites"] += overwrites
        pruned.counts["difference_paths"] += len(paths)
    head = _value_rule(base, head, _enabled(rules, "world-schema-version"), "schema_version")
    return base, head


def normalize_state(state: dict, context: NormalizeContext) -> dict:
    """@brief 一侧世界状态的身份映射与摘要映射。"""
    return _map_digests(map_ids(state, STATE_REFERENCES, context), context)


def normalize_checkpoint(record: dict, context: NormalizeContext) -> dict:
    """@brief 规范化检查点记录：record_sha256 换成 <verified>，摘要换成规范化摘要，状态交给 normalize_state。"""
    result = map_ids(dict(record), CHECKPOINT_REFERENCES, context)
    state = normalize_state(result.pop("state"), context) if "state" in result else None
    result = _map_digests(result, context)
    if "manifest_sha256" in result:
        result["manifest_sha256"] = context.digests.manifest(record["manifest_sha256"], context.side)
    result["record_sha256"] = "<verified>"
    if state is not None:
        result["state"] = state
    return result


def normalize_manifest(manifest: dict, scope: str, context: NormalizeContext) -> dict:
    """@brief 规范化运行清单或导出清单：精确屏蔽、声明身份、摘要映射，导出清单里的检查点按检查点记录处理。"""
    if scope == "run-manifest":
        masked = mask_paths(manifest, MANIFEST_MASKS, counts=context.mask_counts("run-manifest"))
        return _map_digests(map_ids(masked, MANIFEST_REFERENCES, context), context)
    masked = mask_paths(manifest, EXPORT_MANIFEST_MASKS, counts=context.mask_counts("export-manifest"))
    checkpoint = masked.pop("checkpoint", None)
    result = _map_digests(map_ids(masked, EXPORT_REFERENCES["manifest.json"], context), context)
    if checkpoint is not None:
        result["checkpoint"] = checkpoint
    return result


def require_allowed(rule: Rule) -> None:
    """@brief 规则只能落在允许的记录形式上；落在提示、决策、准入或导出读取的字段上即拒绝。"""
    if (rule.scope, rule.path) not in ALLOWED_RULE_TARGETS:
        _refuse(f"Rule {rule.name} targets {rule.scope}:{rule.path}, which a prompt, decision, admission or export "
                "path reads")


def _rule_value(text: str) -> object:
    """@brief 数值规则的一端：能按 JSON 读就按 JSON（整数），否则是字符串。"""
    try:
        return json.loads(text)
    except ValueError:
        return text


def parse_rules(texts: list[str]) -> tuple[AppliedRule, ...]:
    """@brief 按名称启用规则并核对数值。"""
    applied = []
    for text in texts:
        name, separator, values = text.partition("=")
        rule = RULES.get(name)
        if rule is None:
            _refuse(f"Rule {name} is unknown")
        require_allowed(rule)
        if rule.kind != "value":
            if separator:
                _refuse(f"Rule {name} takes no values")
            applied.append(AppliedRule(rule))
            continue
        base_text, colon, head_text = values.partition(":")
        if not separator or not colon or not base_text or not head_text:
            _refuse(f"Rule {name} needs BASE:HEAD values")
        applied.append(AppliedRule(rule, _rule_value(base_text), _rule_value(head_text)))
    return tuple(applied)


class _Layer:
    """@brief 一层比较的计数与前若干条差异。"""

    def __init__(self):
        """@brief 空计数。"""
        self.compared, self.different, self.first = 0, 0, []

    def add(self, key: str, base: object, head: object, extra: list[dict] | None = None) -> bool:
        """@brief 比较一对规范化记录；不同则计数并在前 DETAIL_LIMIT 条里记下叶子路径。"""
        self.compared += 1
        if canonical_bytes(base) == canonical_bytes(head) and not extra:
            return False
        self.different += 1
        if len(self.first) < DETAIL_LIMIT:
            paths = [*(extra or []), *leaf_diff(base, head)][:PATH_LIMIT]
            self.first.append({"key": key, "paths": paths or [{"path": "", "base": "", "head": ""}]})
        return True

    def report(self) -> dict:
        """@brief 报告里的一层。"""
        return {"compared": self.compared, "different": self.different, "first": self.first}


def _event_key(event: dict) -> str:
    """@brief 报告里事件的键：序号、类型与环境事件的种类。"""
    kind = (event.get("data") or {}).get("kind") if event.get("event_type") == "environment_event" else None
    return f"{event.get('event_seq')} {event.get('event_type')}" + (f"/{kind}" if kind else "")


def _export_lines(side: Side, context: CompareContext, side_name: str):
    """@brief 逐行读一侧导出的 events.jsonl，核对它覆盖这一侧运行库的 (event_seq, event_id) 前缀。"""
    expected = context.ids.events[side_name]
    path = side.export / "events.jsonl"
    if not path.is_file():
        _refuse(f"Export {side.export} has no events.jsonl")
    with path.open("rb") as stream:
        for seq, event_id in expected:
            line = stream.readline()
            event = json.loads(line) if line.strip() else None
            if event is None or event.get("event_seq") != seq or event.get("event_id") != event_id:
                _refuse(f"Export {path} does not cover the run database prefix at event {seq}")
            yield event


def compare_events(base: Side, head: Side, context: CompareContext) -> dict:
    """@brief 逐行流式比较两份 events.jsonl；累计两侧规范化行的摘要作为导出清单的事件工件摘要。"""
    layer = _Layer()
    digests = {"base": hashlib.sha256(), "head": hashlib.sha256()}
    for base_event, head_event in zip(_export_lines(base, context, "base"), _export_lines(head, context, "head")):
        aligned = align_events(base_event, head_event, context)
        normalized = (normalize_event(aligned[0], context.base), normalize_event(aligned[1], context.head))
        encoded = [canonical_bytes(value) for value in normalized]
        for name, value in zip(_SIDES, encoded):
            digests[name].update(value + b"\n")
        if encoded[0] == encoded[1]:
            layer.compared += 1
        else:
            layer.add(_event_key(base_event), *normalized)
    for name, side in (("base", base), ("head", head)):
        manifest = _read_json(side.export / "manifest.json")
        raw = manifest.get("artifacts", {}).get("events", {}).get("sha256")
        if raw:
            context.base.digests.maps[name][raw] = (digests[name].hexdigest(), 0)
    return layer.report()


def _checkpoint_row(chain: Chain, point: tuple, context: NormalizeContext) -> dict:
    """@brief 从创建它的那一段库读检查点记录，按规则解码并核对 record_sha256。"""
    _, _, checkpoint_id, run_id = point
    segment = chain.segment_of_run(run_id)
    with _closing(chain.database(segment - 1)) as connection:
        row = connection.execute("SELECT body, record_sha256 FROM checkpoints WHERE checkpoint_id=?",
                                 (checkpoint_id,)).fetchone()
    if row is None:
        _refuse(f"Checkpoint {checkpoint_id} is missing from segment {segment}")
    body, digest = row
    raw = body.encode() if isinstance(body, str) else bytes(body)
    rule = _enabled(context.rules, "checkpoint-zlib")
    if rule is not None and context.side == "head":
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            _refuse(f"Checkpoint {checkpoint_id} record digest does not match its body")
        rule.counts["applied"] += 1
    if _sha(raw) != digest:
        _refuse(f"Checkpoint {checkpoint_id} record digest does not match its body")
    return {**json.loads(raw), "record_sha256": digest}


def _verified_record(record: dict) -> dict:
    """@brief 恢复点文件或导出清单里的检查点记录：record_sha256 必须是去掉它之后的规范 JSON 的摘要。"""
    body = {key: value for key, value in record.items() if key != "record_sha256"}
    if _sha(canonical_bytes(body)) != record.get("record_sha256"):
        _refuse(f"Checkpoint {record.get('checkpoint_id')} record digest does not match its body")
    return record


def _compare_records(layer: _Layer, key: str, records: tuple[dict, dict], context: CompareContext) -> None:
    """@brief 一对检查点记录：先按规则对齐状态，再各自规范化后比较。"""
    base, head = records
    base_state, head_state = align_states(base["state"], head["state"], context.options.rules)
    layer.add(key, normalize_checkpoint({**base, "state": base_state}, context.base),
              normalize_checkpoint({**head, "state": head_state}, context.head))


def _day_end_points(points: list[tuple], timezone: str) -> list[tuple]:
    """@brief 每个本地日期取 current_time 落在当天、序号最高的检查点事件。"""
    zone, chosen = ZoneInfo(timezone), {}
    for point in points:
        chosen[datetime.fromtimestamp(point[1], zone).date()] = point
    return [chosen[day] for day in sorted(chosen)]


def compare_states(base: Side, head: Side, context: CompareContext) -> dict:
    """@brief 比较重启点、日终、终态与（可选）全部检查点。"""
    layers = {"restart_states": _Layer(), "day_end_states": _Layer()}
    for index, records in enumerate(zip(base.chain.restored[1:], head.chain.restored[1:]), start=2):
        _compare_records(layers["restart_states"], f"segment {index}",
                         (_verified_record(records[0]), _verified_record(records[1])), context)
    _compare_points(layers["day_end_states"], (base, head), context, every=False)
    every = None
    if context.options.every_checkpoint:
        every = _Layer()
        _compare_points(every, (base, head), context, every=True)
    final = _compare_final(base, head, context)
    return {**{name: layer.report() for name, layer in layers.items()},
            "every_checkpoint": every.report() if every else None, "final_state": final}


def _compare_points(layer: _Layer, sides: tuple[Side, Side], context: CompareContext, every: bool) -> None:
    """@brief 比较日终（或全部）检查点；两侧位置列表不同记为问题 CHECKPOINT_POSITIONS。"""
    base, head = sides
    points = [context.ids.checkpoints[name] if every
              else _day_end_points(context.ids.checkpoints[name], side.chain.timezone)
              for name, side in zip(_SIDES, sides)]
    if [point[0] for point in points[0]] != [point[0] for point in points[1]]:
        context.ids.problems.append({"kind": "CHECKPOINT_POSITIONS", "base": [point[0] for point in points[0]],
                                     "head": [point[0] for point in points[1]]})
    for base_point, head_point in zip(*points):
        records = (_checkpoint_row(base.chain, base_point, context.base),
                   _checkpoint_row(head.chain, head_point, context.head))
        _compare_records(layer, f"checkpoint {base_point[0]}", records, context)


def _compare_final(base: Side, head: Side, context: CompareContext) -> dict | None:
    """@brief 截止是两侧最后一段库的末事件时比较两侧 world_state 行。"""
    if any(_last_seq(side.chain) != context.ids.cutoff for side in (base, head)):
        return None
    states = []
    for side in (base, head):
        with _closing(side.chain.database()) as connection:
            states.append(json.loads(connection.execute("SELECT body FROM world_state").fetchone()[0]))
    base_state, head_state = align_states(states[0], states[1], context.options.rules)
    layer = _Layer()
    layer.add("final", normalize_state(base_state, context.base), normalize_state(head_state, context.head))
    return layer.report()


def compare_manifests(base: Side, head: Side, context: CompareContext) -> dict:
    """@brief 各段运行清单成对对齐、规范化后比较；规范化清单的摘要登记为各侧 manifest_sha256 的目标。"""
    layer = _Layer()
    for index, pair in enumerate(zip(base.chain.manifests, head.chain.manifests), start=1):
        aligned = align_manifests(pair[0], pair[1], "run-manifest", context.options.rules)
        normalized = []
        for name, side, manifest in zip(_SIDES, (base, head), aligned):
            ctx = getattr(context, name)
            value = normalize_manifest(manifest, "run-manifest", ctx)
            raw = (side.chain.runs[index - 1] / "manifest.json").read_bytes()
            for raw_sha in {_sha(raw), _sha(canonical_bytes(pair[_SIDES.index(name)]))}:
                ctx.digests.register(raw_sha, name, canonical_bytes(value))
            normalized.append(value)
        layer.add(f"segment {index}", *normalized)
    return layer.report()


def _artifact_bytes(path: Path, digest: dict | None, name: str) -> bytes:
    """@brief 读一个导出工件并核对导出清单记下的摘要。"""
    raw = path.read_bytes()
    if digest is not None and digest.get("sha256") != _sha(raw):
        _refuse(f"Export artifact {name} does not match its manifest digest")
    return raw


def _manifest_digests(manifest: dict) -> dict[str, dict]:
    """@brief 导出清单里各工件文件的摘要记录，按相对路径。"""
    digests = {entry["path"]: entry for entry in manifest.get("artifacts", {}).values()}
    for key in ("asset_index", "quality_report"):
        if key in manifest:
            digests[manifest[key]["path"]] = manifest[key]
    digests.update({entry["path"]: entry for entry in manifest.get("timeline", [])})
    return digests


def _normalize_quality(report: dict, context: NormalizeContext) -> dict:
    """@brief 质量报告：屏蔽壁钟项，以本侧代码摘要为键的计划指标改用占位键，映射声明身份与摘要。"""
    masked = mask_paths(report, QUALITY_MASKS, counts=context.mask_counts("quality"))
    metrics = masked.get("day_plan_metrics")
    if isinstance(metrics, dict) and context.code_sha256 in metrics:
        masked["day_plan_metrics"] = {("<code-identity>" if key == context.code_sha256 else key): value
                                      for key, value in metrics.items()}
    return _map_digests(map_ids(masked, EXPORT_REFERENCES["quality.json"], context), context)


def drop_replaced(base: dict, head: dict) -> tuple[dict, dict]:
    """@brief 基一侧按用途、头一侧按调用类的模型用量分列同时移除；其余组合原样比较。"""
    base_usage, head_usage = base.get("model_usage"), head.get("model_usage")
    if not (isinstance(base_usage, dict) and isinstance(head_usage, dict)):
        return base, head
    if ("by_purpose" in base_usage and "by_call_class" not in base_usage
            and "by_call_class" in head_usage and "by_purpose" not in head_usage):
        base = {**base, "model_usage": {k: v for k, v in base_usage.items() if k != "by_purpose"}}
        head = {**head, "model_usage": {k: v for k, v in head_usage.items() if k != "by_call_class"}}
    return base, head


def _align_quality(base: dict, head: dict, rules: tuple[AppliedRule, ...]) -> tuple[dict, dict]:
    """@brief 质量报告按规则对齐：结构号、lineage 节、按用途换成按调用类。"""
    head = _value_rule(base, head, _enabled(rules, "quality-version"), "schema_version")
    lineage = _enabled(rules, "lineage")
    if lineage is not None:
        dropped = drop_added(head, base, ("lineage",))
        lineage.counts["applied"] += int(dropped is not head)
        head = dropped
    replaced = _enabled(rules, "cost-by-class")
    if replaced is not None:
        aligned = drop_replaced(base, head)
        replaced.counts["applied"] += int(aligned != (base, head))
        base, head = aligned
    return base, head


def _export_files(root: Path) -> list[str]:
    """@brief 导出目录里除清单、事件与素材外的全部文件（相对路径）。"""
    names = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative not in ("manifest.json", "events.jsonl") and not relative.startswith("assets/"):
            names.append(relative)
    return names


def _jsonl_rows(raw: bytes) -> list[dict]:
    """@brief JSONL 文本的各行。"""
    return [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]


def _normalized_artifacts(name: str, raws: tuple[bytes, bytes], context: CompareContext) -> list[object]:
    """@brief 一个导出工件两侧的规范化内容：JSONL 为行列表，质量报告为对齐后的对象，其余为原始字节。"""
    sides = (context.base, context.head)
    if name in JSONL_ARTIFACTS:
        fields = EXPORT_REFERENCES[name]
        return [[_map_digests(map_ids(row, fields, side), side) for row in _jsonl_rows(raw)]
                for raw, side in zip(raws, sides)]
    if name == "quality.json":
        aligned = _align_quality(json.loads(raws[0]), json.loads(raws[1]), context.options.rules)
        return [_normalize_quality(report, side) for report, side in zip(aligned, sides)]
    return list(raws)


def _artifact_bytes_of(name: str, normalized: object) -> bytes:
    """@brief 规范化工件的字节：JSONL 一行一个规范对象，质量报告为规范 JSON，其余为原始字节。"""
    if name in JSONL_ARTIFACTS:
        return b"".join(canonical_bytes(row) + b"\n" for row in normalized)
    if name == "quality.json":
        return canonical_bytes(normalized)
    return normalized


def _compare_artifact(name: str, sides: tuple[Side, Side], digests: tuple[dict, dict],
                      context: CompareContext) -> dict:
    """@brief 比较一个导出工件：JSONL 逐行，质量报告整份，时间线与其他文件按字节；登记规范化摘要。"""
    layer = _Layer()
    paths = [side.export / name for side in sides]
    if not all(path.is_file() for path in paths):
        layer.add(name, paths[0].is_file(), paths[1].is_file())
        return layer.report()
    raws = tuple(_artifact_bytes(path, digest.get(name), name) for path, digest in zip(paths, digests))
    values = _normalized_artifacts(name, raws, context)
    for side_name, raw, value in zip(_SIDES, raws, values):
        context.base.digests.register(_sha(raw), side_name, _artifact_bytes_of(name, value))
    if name in JSONL_ARTIFACTS:
        for index in range(max(len(values[0]), len(values[1]))):
            rows = [value[index] if index < len(value) else None for value in values]
            layer.add(f"row {index + 1}", *rows)
    elif name == "quality.json":
        layer.add(name, *values)
    else:
        layer.add(name, _sha(values[0]), _sha(values[1]))
    return layer.report()


def _compare_export_manifest(manifests: list[dict], context: CompareContext) -> dict:
    """@brief 比较两份导出清单：嵌入的检查点先核对摘要，按规则对齐状态后按检查点记录规范化。"""
    for manifest in manifests:
        if "checkpoint" in manifest:
            _verified_record(manifest["checkpoint"])
    base, head = align_manifests(manifests[0], manifests[1], "export-manifest", context.options.rules)
    if "checkpoint" in base and "checkpoint" in head:
        states = align_states(base["checkpoint"]["state"], head["checkpoint"]["state"], context.options.rules)
        base = {**base, "checkpoint": {**base["checkpoint"], "state": states[0]}}
        head = {**head, "checkpoint": {**head["checkpoint"], "state": states[1]}}
    values = []
    for manifest, ctx in ((base, context.base), (head, context.head)):
        value = normalize_manifest(manifest, "export-manifest", ctx)
        if "checkpoint" in manifest:
            value["checkpoint"] = normalize_checkpoint(manifest["checkpoint"], ctx)
        values.append(value)
    layer = _Layer()
    layer.add("manifest.json", *values)
    return layer.report()


def _export_assets(side: Side, manifest: dict, context: NormalizeContext) -> list[str]:
    """@brief 一侧导出 assets/ 里每个文件的规范化摘要（引用取自 asset-index.jsonl），按摘要排序。"""
    index_path = side.export / manifest.get("asset_index", {}).get("path", "asset-index.jsonl")
    rows = _jsonl_rows(index_path.read_bytes()) if index_path.is_file() else []
    references = {row["sha256"]: row for row in rows}
    digests = []
    root = side.export / "assets"
    for path in sorted(root.iterdir()) if root.is_dir() else []:
        if _sha(path.read_bytes()) != path.name:
            _refuse(f"Asset {path.name} does not match its reference")
        reference = references.get(path.name) or {"sha256": path.name, "size_bytes": path.stat().st_size,
                                                  "media_type": "", "source": {}}
        digests.append(context.digests.asset(reference, context.side)[0])
    return sorted(digests)


def compare_exports(base: Side, head: Side, context: CompareContext) -> tuple[dict, dict]:
    """@brief 比较导出的每个工件，返回（按工件的层, 素材层）；素材按规范化摘要的多重集比较。"""
    manifests = [_read_json(side.export / "manifest.json") for side in (base, head)]
    digests = (_manifest_digests(manifests[0]), _manifest_digests(manifests[1]))
    layers = {}
    for name in sorted(set(_export_files(base.export)) | set(_export_files(head.export))):
        layers[name] = _compare_artifact(name, (base, head), digests, context)
    layers["manifest.json"] = _compare_export_manifest(manifests, context)
    assets = [_export_assets(side, manifest, ctx)
              for side, manifest, ctx in zip((base, head), manifests, (context.base, context.head))]
    layer = _Layer()
    remaining = Counter(assets[1])
    for digest in assets[0]:
        layer.add(digest, digest, digest if remaining[digest] > 0 else None)
        remaining[digest] -= 1
    for digest, count in sorted(remaining.items()):
        for _ in range(max(count, 0)):
            layer.add(digest, None, digest)
    return layers, layer.report()


def _resolve_cutoff(base: Side, head: Side, requested: int | None) -> tuple[int, list[dict]]:
    """@brief 比较的截止序号；缺省时两侧末事件不同记 EVENT_COUNT 并比较公共前缀。"""
    lasts = {"base": _last_seq(base.chain), "head": _last_seq(head.chain)}
    if requested is not None:
        for name, last in lasts.items():
            if requested > last:
                _refuse(f"Cutoff {requested} is beyond the recorded events of {name}")
        return requested, []
    if lasts["base"] != lasts["head"]:
        return min(lasts.values()), [{"kind": "EVENT_COUNT", **lasts}]
    return lasts["base"], []


def _model_urls(chain: Chain) -> tuple[str, str]:
    """@brief 一侧最后一段清单冻结的 base_url 与 metadata_url。"""
    identity = chain.manifests[-1].get("stage_model_identity") or {}
    return identity.get("base_url", ""), identity.get("metadata_url", "")


def _contexts(base: Side, head: Side, ids: IdMaps, rules: tuple[AppliedRule, ...]) -> dict[str, NormalizeContext]:
    """@brief 两侧规范化上下文，先登记各段冻结的环境素材。"""
    digests = Digests(base, head)
    contexts = {name: NormalizeContext(name, ids, digests, rules, {}, _model_urls(side.chain),
                                       side.chain.manifests[-1].get("code_sha256"))
                for name, side in zip(_SIDES, (base, head))}
    digests.contexts = contexts
    for name, side in zip(_SIDES, (base, head)):
        for manifest in side.chain.manifests:
            for reference in (manifest.get("environment_assets") or {}).values():
                digests.asset(reference, name)
    return contexts


def _layer_items(layers: dict) -> list[tuple[str, dict]]:
    """@brief 报告各层（导出按工件展开），跳过未比较的层。"""
    flat = []
    for name, layer in layers.items():
        if name == "export":
            flat.extend((f"export:{artifact}", value) for artifact, value in layer.items())
        elif layer is not None:
            flat.append((name, layer))
    return flat


def compare(base: Side, head: Side, options: Options) -> dict:
    """@brief 库入口：返回报告，verdict 为 equal 或 different。"""
    rules = tuple(AppliedRule(item.rule, item.base_value, item.head_value) for item in options.rules)
    options = Options(options.cutoff, rules, options.every_checkpoint)
    problems = []
    if len(base.chain.runs) != len(head.chain.runs):
        problems.append({"kind": "SEGMENT_COUNT", "base": len(base.chain.runs), "head": len(head.chain.runs)})
    cutoff, count_problems = _resolve_cutoff(base, head, options.cutoff)
    ids = IdMaps.load(base.chain, head.chain, cutoff)
    contexts = _contexts(base, head, ids, rules)
    context = CompareContext(options, ids, contexts["base"], contexts["head"])
    layers = {"manifests": compare_manifests(base, head, context), "events": compare_events(base, head, context)}
    layers.update(compare_states(base, head, context))
    layers["export"], layers["assets"] = compare_exports(base, head, context)
    problems += count_problems + ids.problems
    masks = Counter()
    for ctx in contexts.values():
        for record, counts in ctx.masks.items():
            masks.update({f"{record}:{path}": count for path, count in counts.items()})
    different = any(layer["different"] for _, layer in _layer_items(layers))
    return {"cutoff": cutoff, "layers": layers, "masks": dict(sorted(masks.items())), "problems": problems,
            "rules": {item.rule.name: _rule_report(item) for item in rules},
            "schema_version": 1, "verdict": "different" if different or problems else "equal"}


def _rule_report(item: AppliedRule) -> dict:
    """@brief 报告里一条启用规则的应用次数、取值与附加计数。"""
    values = None if item.rule.kind != "value" else f"{item.base_value}:{item.head_value}"
    entry = {"applied": item.counts["applied"], "values": values}
    if item.rule.name == "processed-actions-pruned":
        entry["terminal_overwrites"] = item.counts["terminal_overwrites"]
        entry["difference_paths"] = item.counts["difference_paths"]
    return entry


def _is_state_layer(name: str) -> bool:
    """@brief 是否是世界状态或检查点的一层。"""
    return name.endswith("states") or name in ("every_checkpoint", "final_state")


def summary_line(report: dict) -> str:
    """@brief 命令输出的一行结论。"""
    layers = _layer_items(report["layers"])
    events = report["layers"]["events"]
    if report["verdict"] == "equal":
        checkpoints = sum(layer["compared"] for name, layer in layers if _is_state_layer(name))
        artifacts = sum(1 for name, _ in layers if name.startswith("export:"))
        return f"Runs are equal: {events['compared']} events, {checkpoints} checkpoints, {artifacts} export artifacts"
    states = sum(layer["different"] for name, layer in layers if _is_state_layer(name))
    artifacts = sum(layer["different"] for name, layer in layers if name.startswith("export:") or name == "assets")
    first = next(((name, layer["first"][0]) for name, layer in layers if layer["first"]), None)
    if first is None:
        where = f"problem {report['problems'][0]['kind']}"
    elif first[0] == "events":
        seq, _, kind = first[1]["key"].partition(" ")
        where = f"event {seq} ({kind}) {first[1]['paths'][0]['path']}"
    else:
        where = f"{first[0]} {first[1]['key']} {first[1]['paths'][0]['path']}"
    return (f"Runs differ: {events['different']} events, {states} states, {artifacts} artifacts; "
            f"first difference at {where}")


def compare_event_data(base_event: dict, head_event: dict) -> list[dict]:
    """@brief events 子命令：两条事件 data 的字面差异（不屏蔽、不映射），按路径排序。"""
    return leaf_diff(base_event.get("data"), head_event.get("data"), limit=None)


def _stored_event(run: Path, seq: int) -> dict:
    """@brief 读运行库里存下的一条事件正文。"""
    with _closing(_connect(run)) as connection:
        row = connection.execute("SELECT body FROM events WHERE event_seq=?", (seq,)).fetchone()
    if row is None:
        _refuse(f"Run {run} has no event {seq}")
    return json.loads(row[0])


def _write_report(path: Path, report: dict) -> None:
    """@brief 报告写成规范 JSON 加一个换行。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(report) + b"\n")


def _parser() -> argparse.ArgumentParser:
    """@brief 命令行：runs 比较两条运行链，events 比较两条存下的事件。"""
    parser = argparse.ArgumentParser(description="Compare two recorded runs fact by fact")
    commands = parser.add_subparsers(dest="command", required=True)
    runs = commands.add_parser("runs")
    runs.add_argument("--base-run", type=Path, nargs="+", required=True)
    runs.add_argument("--base-export", type=Path, required=True)
    runs.add_argument("--head-run", type=Path, nargs="+", required=True)
    runs.add_argument("--head-export", type=Path, required=True)
    runs.add_argument("--cutoff", type=int)
    runs.add_argument("--rule", action="append", default=[])
    runs.add_argument("--every-checkpoint", action="store_true")
    runs.add_argument("--report", type=Path, required=True)
    events = commands.add_parser("events")
    for side in _SIDES:
        events.add_argument(f"--{side}-run", type=Path, required=True)
        events.add_argument(f"--{side}-seq", type=int, required=True)
    events.add_argument("--report", type=Path, required=True)
    return parser


def _runs_command(arguments: argparse.Namespace) -> int:
    """@brief runs 子命令。"""
    options = Options(arguments.cutoff, parse_rules(arguments.rule), arguments.every_checkpoint)
    base = Side(Chain.load(arguments.base_run), arguments.base_export)
    head = Side(Chain.load(arguments.head_run), arguments.head_export)
    report = compare(base, head, options)
    _write_report(arguments.report, report)
    print(summary_line(report))
    return 0 if report["verdict"] == "equal" else 1


def _events_command(arguments: argparse.Namespace) -> int:
    """@brief events 子命令。"""
    base = _stored_event(arguments.base_run, arguments.base_seq)
    head = _stored_event(arguments.head_run, arguments.head_seq)
    paths = compare_event_data(base, head)
    _write_report(arguments.report, {"base": {"run": str(arguments.base_run), "seq": arguments.base_seq},
                                     "head": {"run": str(arguments.head_run), "seq": arguments.head_seq},
                                     "paths": paths, "schema_version": 1})
    print(f"Event data differ in {len(paths)} paths" if paths else "Event data are equal")
    return 1 if paths else 0


def main(argv: list[str] | None = None) -> int:
    """@brief 命令入口；0 相同、1 不同、2 输入错误。"""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    arguments = _parser().parse_args(argv)
    try:
        return _runs_command(arguments) if arguments.command == "runs" else _events_command(arguments)
    except ComparisonError:
        return 2
    except Exception:
        logger.exception("Run comparison failed")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

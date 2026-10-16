"""@brief 使用同一 SQLite 事务保存世界、结算、事件和共同检查点。"""

from __future__ import annotations

from worldmock.footprints.models import sha256_json
import hashlib
import json
import logging
import sqlite3
import time
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from worldmock.errors import IntegrityError
from worldmock.footprints.assets import AssetStore, atomic_write
from worldmock.footprints.ledger import validate_account_transition
from worldmock.footprints.models import AssetRef, Checkpoint, Event, EventDraft, EventType, canonical_json

SCHEMA = """
PRAGMA foreign_keys=ON;
PRAGMA synchronous=FULL;
CREATE TABLE events(event_seq INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, body TEXT NOT NULL);
CREATE INDEX events_agent_type ON events(json_extract(body,'$.agent_id'),json_extract(body,'$.event_type'),event_seq);
CREATE TABLE world_state(singleton INTEGER PRIMARY KEY CHECK(singleton=1),
version INTEGER NOT NULL, body TEXT NOT NULL);
CREATE TABLE artifacts(sha256 TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE checkpoints(checkpoint_id TEXT PRIMARY KEY, body BLOB NOT NULL, record_sha256 TEXT NOT NULL);
CREATE TRIGGER events_append_only BEFORE INSERT ON events
WHEN NEW.event_seq != (SELECT COALESCE(MAX(event_seq),0)+1 FROM events)
BEGIN SELECT RAISE(ABORT,'Event sequence must append exactly one record'); END;
CREATE TRIGGER events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'Events are immutable'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'Events are immutable'); END;
CREATE TRIGGER checkpoints_no_update BEFORE UPDATE ON checkpoints BEGIN
SELECT RAISE(ABORT,'Checkpoints are immutable'); END;
CREATE TRIGGER checkpoints_no_delete BEFORE DELETE ON checkpoints BEGIN
SELECT RAISE(ABORT,'Checkpoints are immutable'); END;
"""

#: 检查点行正文的 zlib 压缩级别（docs/design/05-recording-engine.md「冻结输入、检查点与分支」）。
_CHECKPOINT_ZLIB_LEVEL = 1


@dataclass(frozen=True)
class CommittedWorld:
    """@brief 单写入者最近一次成功提交（或第一次写入时读到）的世界摘要；只整体替换，从不原地修改。"""

    #: 已提交世界版本。
    version: int
    #: 已提交世界时钟（UTC 纪元秒）。
    current_time: int
    #: 已提交账户余额的副本；世界没有 accounts 字段时为 None（只有存储单元测试的最小世界如此）。
    accounts: dict[str, int] | None
    #: 已提交世界的规范 JSON 字节，与 world_state.body 的 UTF-8 编码逐字节相同。
    body: bytes

    @classmethod
    def of(cls, state: dict, body: bytes) -> CommittedWorld:
        """@brief 由已提交世界及其规范字节建立摘要；账户复制一份，不与调用方共享。"""
        accounts = dict(state["accounts"]) if "accounts" in state else None
        return cls(state["version"], state["current_time"], accounts, body)

    def ledger_view(self) -> dict:
        """@brief 账本核对用的已提交世界：只含 accounts；世界没有账户时为空字典，账本核对随之跳过。"""
        return {} if self.accounts is None else {"accounts": self.accounts}


def _validate_step(committed: CommittedWorld, state: dict, events: list[EventDraft]) -> None:
    """@brief 一步世界推进只能把版本恰好加一、时钟不回退，并且至少带一条因果事件；同版本写入只能走证据追加。"""
    if type(state["version"]) is not int or type(state["current_time"]) is not int:
        raise IntegrityError("World version and current time must be integers")
    if state["current_time"] < committed.current_time:
        raise IntegrityError("World time cannot move backwards")
    if state["version"] != committed.version + 1:
        raise IntegrityError("World state version is discontinuous")
    if not events:
        raise IntegrityError("World state changes require at least one causal event")


class FootprintStore:
    """@brief 环境 actor 独占写入并缓存已提交世界；其他消费者只读取固定导出。"""

    def __init__(self, run_dir: Path):
        """@brief 只打开已有运行；缺失数据不能隐式新建。"""
        self.run_dir = Path(run_dir).resolve()
        database = self.run_dir / "footprints.sqlite"
        if not database.is_file():
            raise IntegrityError("Footprint database does not exist")
        self.manifest = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.connection = sqlite3.connect(database)
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.assets = AssetStore(self.run_dir / "assets")
        self._started = time.monotonic()
        #: 已提交世界缓存：第一次走写入路径时载入（_committed_world），只在推进世界的事务成功后整体替换。
        self._committed: CommittedWorld | None = None

    @classmethod
    def create(cls, run_dir: Path, manifest: dict, initial_state: dict) -> FootprintStore:
        """@brief 为明确新运行创建数据库和冻结身份，不覆盖已有工件。"""
        root = Path(run_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        if any((root / name).exists() for name in ("footprints.sqlite", "manifest.json")):
            raise IntegrityError("Run artifacts already exist")
        if (not manifest.get("run_id") or type(initial_state["version"]) is not int
                or initial_state["version"] < 0):
            raise IntegrityError("Run identity or initial state version is invalid")
        if type(initial_state["current_time"]) is not int:
            raise IntegrityError("Initial world clock must be an integer")
        connection = sqlite3.connect(root / "footprints.sqlite")
        try:
            connection.executescript(SCHEMA)
            with connection:
                connection.execute("INSERT INTO world_state VALUES(1,?,?)", (
                    initial_state["version"], canonical_json(initial_state).decode(),
                ))
            atomic_write(root / "manifest.json", canonical_json(manifest))
        except Exception:
            logging.exception("New footprint database creation failed")
            raise
        finally:
            connection.close()
        return cls(root)

    @property
    def state(self) -> dict:
        """@brief 返回已经提交的世界副本。"""
        row = self.connection.execute("SELECT body FROM world_state WHERE singleton=1").fetchone()
        return json.loads(row[0])

    @property
    def last_event_seq(self) -> int:
        """@brief 返回持久事件最后序号，空库为零。"""
        return self.connection.execute("SELECT COALESCE(MAX(event_seq),0) FROM events").fetchone()[0]

    def _committed_world(self) -> CommittedWorld:
        """@brief 返回已提交世界缓存；第一次调用时读并解析 world_state 一次，只读的打开者从不付这笔开销。"""
        if self._committed is None:
            body = self.connection.execute("SELECT body FROM world_state WHERE singleton=1").fetchone()[0]
            self._committed = CommittedWorld.of(json.loads(body), body.encode())
        return self._committed

    def require_committed(self, world: dict) -> None:
        """@brief 核对调用方的世界与已提交世界的规范 JSON 逐字节相同（引擎世界等于已提交世界），不写任何东西。

        @param world 调用方的世界：WorldState.model_dump(mode="json")，或存储单元测试的最小世界
        @exception IntegrityError 字节不同（Engine world differs from the committed world）
        """
        committed = self._committed_world()
        if canonical_json(world) != committed.body:
            logging.error("Engine world differs from the committed world at version %d", committed.version)
            raise IntegrityError("Engine world differs from the committed world")

    def commit(self, base: dict, state: dict, events: list[EventDraft]) -> list[Event]:
        """@brief 原子提交一步世界推进：base 逐字节等于已提交世界，state 恰好推进一个版本并带至少一条因果事件。

        任一检查或写入失败则整个事务回滚、缓存不变；成功后缓存换成 state。
        @param base 这一步出发时的世界（引擎当前世界的 JSON 形态）
        @param state 已按加载口径复校的新世界（validated_payload 的结果）
        @param events 本步的因果事件，按提交顺序
        @return 分配了序号与前后版本的事件
        """
        committed = self._committed_world()
        self.require_committed(base)
        _validate_step(committed, state, events)
        validate_account_transition(committed.ledger_view(), state, events, self.last_event_seq == 0)
        records = self._build_events(committed.version, state["version"], state["current_time"], events)
        body = canonical_json(state)
        try:
            with self.connection:
                for record in records:
                    self._insert_event(record)
                self._require_stored_version(committed.version)
                self.connection.execute("UPDATE world_state SET version=?,body=? WHERE singleton=1",
                                        (state["version"], body.decode()))
        except Exception:
            logging.exception("Footprint transaction failed; no state or event was committed")
            raise
        self._committed = CommittedWorld.of(state, body)
        return records

    def append_evidence(self, events: list[EventDraft]) -> list[Event]:
        """@brief 同版本追加证据事件：不接收、不改写世界，事件的前后版本都是已提交版本。

        观察、模型请求与响应、记忆更新、决策开始与行动请求、运行失败和零点计划失败证据都走这里；证据不能带资金变化，
        初始注资只属于创建世界的那一步推进。
        @param events 证据事件，按提交顺序，至少一条
        @return 分配了序号的事件
        @exception IntegrityError 没有事件、带资金变化、时刻越界、原因缺失、素材损坏，或库里的世界被别的写入者改过
        """
        if not events:
            logging.error("Evidence append received no event")
            raise IntegrityError("Evidence append requires at least one event")
        committed = self._committed_world()
        validate_account_transition(committed.ledger_view(), committed.ledger_view(), events, False)
        records = self._build_events(committed.version, committed.version, committed.current_time, events)
        try:
            with self.connection:
                for record in records:
                    self._insert_event(record)
                self._require_stored_version(committed.version)
        except Exception:
            logging.exception("Evidence append failed; no event was committed")
            raise
        return records

    def _require_stored_version(self, version: int) -> None:
        """@brief 在调用方已开始的写事务内核对库里的世界版本仍是缓存版本（按预期版本写入）；不同即拒绝，事务随之回滚。"""
        stored = self.connection.execute("SELECT version FROM world_state WHERE singleton=1").fetchone()[0]
        if stored != version:
            logging.error("Stored world version %d is not the cached version %d", stored, version)
            raise IntegrityError("World state was changed outside this store")

    def _build_events(self, version_before: int, version_after: int, now: int, drafts: list[EventDraft]) -> list[Event]:
        """@brief 只给因果已存在且素材完整的事件分配严格全序；now 是事件时刻的上界，即写入后的世界时钟。"""
        sequence = self.last_event_seq
        known = set()
        last_time = self._last_event_time()
        records = []
        for draft in drafts:
            self._validate_references(draft, known)
            if draft.occurred_at < last_time or draft.occurred_at > now:
                raise IntegrityError("Event time is out of committed world order")
            if draft.observed_at is not None and not draft.occurred_at <= draft.observed_at <= now:
                raise IntegrityError("Observation time is outside its valid interval")
            for reference in draft.artifact_refs:
                self.assets.read(reference)
            sequence += 1
            records.append(Event(**draft.model_dump(), run_id=self.manifest["run_id"], event_seq=sequence,
                                 recorded_at=datetime.now(timezone.utc).isoformat(),
                                 wall_elapsed_ms=int((time.monotonic() - self._started) * 1000),
                                 state_version_before=version_before, state_version_after=version_after))
            known.add(draft.event_id)
            last_time = draft.occurred_at
        return records

    def _validate_references(self, draft: EventDraft, known: set[str]) -> None:
        """@brief 使用唯一索引核对直接原因，避免每次提交扫描整个历史。"""
        activity = draft.data.get("activity")
        if activity is not None and (not isinstance(activity, dict)
                                     or not activity.get("activity_id")
                                     or draft.activity_id != activity["activity_id"]):
            raise IntegrityError("Event activity reference disagrees with its snapshot")
        exists = self.connection.execute("SELECT 1 FROM events WHERE event_id=?", (draft.event_id,)).fetchone()
        if draft.event_id in known or exists:
            raise IntegrityError("Event identity is duplicated")
        for cause in draft.caused_by:
            exists = self.connection.execute("SELECT 1 FROM events WHERE event_id=?", (cause,)).fetchone()
            if cause not in known and not exists:
                raise IntegrityError("Event cause does not exist")

    def _last_event_time(self) -> int:
        """@brief 按主键取最近事件的发生时刻（只取这一个字段，不解析整条正文）；空库以已提交世界时钟为界。"""
        row = self.connection.execute(
            "SELECT json_extract(body,'$.occurred_at') FROM events ORDER BY event_seq DESC LIMIT 1").fetchone()
        return row[0] if row else self._committed_world().current_time

    def _insert_event(self, event: Event) -> None:
        """@brief 在调用方事务内插入事件和素材索引。"""
        for reference in event.artifact_refs:
            self.connection.execute("INSERT OR IGNORE INTO artifacts VALUES(?,?)", (
                reference.sha256, canonical_json(reference.model_dump()).decode(),
            ))
        self.connection.execute("INSERT INTO events VALUES(?,?,?)", (
            event.event_seq, event.event_id, canonical_json(event.model_dump()).decode(),
        ))

    def last_agent_event_seq(self, event_type: EventType, agent_id: int) -> int:
        """@brief 使用人物和类型索引查询最后事件序号，不加载全历史正文。"""
        row = self.connection.execute("""
            SELECT max(event_seq) FROM events WHERE json_extract(body,'$.agent_id')=?
            AND json_extract(body,'$.event_type')=?
        """, (agent_id, event_type)).fetchone()
        return row[0] if row[0] is not None else 0

    def last_agent_action_request_seq(self, agent_id: int) -> int:
        """@brief 最近一次行动请求的事件序号（没有时为 0）。

        行动请求的唯一判定（docs/design/06-agent.md「宿主模型协议」）：不带 purpose、thinking 开启、
        原生 tools 含 start_main_activity 的 model_request；零点计划、工作评审与只读问答都不算。不读提示文本。
        """
        row = self.connection.execute("""
            SELECT max(event_seq) FROM events WHERE json_extract(body,'$.agent_id')=?
            AND json_extract(body,'$.event_type')='model_request'
            AND json_extract(body,'$.data.purpose') IS NULL
            AND json_extract(body,'$.data.request.extra_body.thinking.type')='enabled'
            AND EXISTS (SELECT 1 FROM json_each(body,'$.data.request.tools')
                        WHERE json_extract(value,'$.function.name')='start_main_activity')
        """, (agent_id,)).fetchone()
        return row[0] if row[0] is not None else 0

    def last_event_seq_before(self, occurred_at: int) -> int:
        """@brief 发生时刻早于 occurred_at 的最后一个事件序号（没有时为 0），供按时间确定区间下界。

        事件按发生时刻单调追加（_build_events 拒绝早于上一事件的时刻），序号从 1 起连续，所以按序号二分查找，
        只读对数条事件的发生时刻，不随历史或区间长度增长。
        """
        low, high = 0, self.last_event_seq
        while low < high:
            middle = (low + high + 1) // 2
            moment = self.connection.execute(
                "SELECT json_extract(body,'$.occurred_at') FROM events WHERE event_seq=?", (middle,)).fetchone()[0]
            if moment < occurred_at:
                low = middle
            else:
                high = middle - 1
        return low

    def commit_last_seq(self, event_seq: int) -> int:
        """@brief event_seq 所在提交的末事件序号（docs/design/06-agent.md）。

        一次提交的事件在同一事务内连续分配序号，共享 state_version_before 与 state_version_after；推进版本的提交之后
        同版本追加的观察与模型证据版本对不同（before 等于 after），不属于该提交。只从 event_seq 向后读到提交结束。
        """
        row = self.connection.execute(
            "SELECT json_extract(body,'$.state_version_before'),json_extract(body,'$.state_version_after') "
            "FROM events WHERE event_seq=?", (event_seq,)).fetchone()
        if row is None:
            logging.error("Commit reference event %d does not exist", event_seq)
            raise IntegrityError("Commit reference event does not exist")
        following = self.connection.execute(
            "SELECT event_seq FROM events WHERE event_seq>? AND (json_extract(body,'$.state_version_before')!=? "
            "OR json_extract(body,'$.state_version_after')!=?) ORDER BY event_seq LIMIT 1",
            (event_seq, *row)).fetchone()
        return following[0] - 1 if following is not None else self.last_event_seq

    def read_event(self, event_id: str) -> Event:
        """@brief 按事件身份读取一条已提交事件（events.event_id 有唯一索引），不扫描其余事件。

        :raises IntegrityError: 库里没有这个身份（Event does not exist）。
        """
        row = self.connection.execute("SELECT body FROM events WHERE event_id=?", (event_id,)).fetchone()
        if row is None:
            logging.error("Event %s does not exist", event_id)
            raise IntegrityError("Event does not exist")
        return Event.model_validate_json(row[0])

    def read_agent_events(self, event_type: EventType, agent_id: int, after: int = 0) -> list[Event]:
        """@brief 只读取指定人物在已知边界之后的同类事件，避免反复解码原始模型调用。"""
        if after < 0 or after > self.last_event_seq:
            raise IntegrityError("Agent event boundary is outside the committed sequence")
        rows = self.connection.execute("""
            SELECT body FROM events WHERE json_extract(body,'$.agent_id')=?
            AND json_extract(body,'$.event_type')=? AND event_seq>? ORDER BY event_seq
        """, (agent_id, event_type, after))
        return [Event.model_validate_json(row[0]) for row in rows]

    def read_decision_observations(self, agent_id: int, decision_id: str, observed_at: int) -> list[Event]:
        """@brief 只解码人物、决策和发生时刻精确匹配的观察，保留全部匹配记录供唯一性复核。

        发生时刻随序号单调，匹配的观察都在 observed_at 之前最后一个事件之后；沿人物与类型索引只读这一段，不扫描更早的历史。
        """
        rows = self.connection.execute("""
            SELECT body FROM events WHERE json_extract(body,'$.agent_id')=?
            AND json_extract(body,'$.event_type')='observation' AND event_seq>?
            AND json_extract(body,'$.decision_id')=? AND json_extract(body,'$.occurred_at')=? ORDER BY event_seq
        """, (agent_id, self.last_event_seq_before(observed_at), decision_id, observed_at))
        return [Event.model_validate_json(row[0]) for row in rows]

    def read_recent_experience_sources(self, agent_id: int, before_seq: int, limit: int) -> list[Event]:
        """@brief 按倒序有限读取本人活动进度与收货候选事件。"""
        if (type(agent_id) is not int or agent_id <= 0 or type(before_seq) is not int
                or not 0 < before_seq <= self.last_event_seq + 1
                or type(limit) is not int or limit <= 0):
            raise IntegrityError("Experience source query boundary is invalid")
        rows = self.connection.execute("""
            SELECT body FROM events WHERE json_extract(body,'$.agent_id')=?
            AND (json_extract(body,'$.event_type')='activity_progress'
                 OR (json_extract(body,'$.event_type')='environment_event'
                     AND json_extract(body,'$.data.kind')='goods_received'))
            AND event_seq<? ORDER BY event_seq DESC LIMIT ?
        """, (agent_id, before_seq, limit))
        return [Event.model_validate_json(row[0]) for row in rows]

    def read_events(self, cutoff: int) -> list[Event]:
        """@brief 固定截止序号读取原始顺序，拒绝不存在的截止位置。"""
        if cutoff < 0 or cutoff > self.last_event_seq:
            raise IntegrityError("Event cutoff is outside the committed sequence")
        rows = self.connection.execute("SELECT body FROM events WHERE event_seq<=? ORDER BY event_seq", (cutoff,))
        return [Event.model_validate_json(row[0]) for row in rows]

    def checkpoint(self, state: dict, snapshots: dict[str, AssetRef]) -> Checkpoint:
        """@brief 调用方世界逐字节等于已提交世界、素材完整后，以一个事务提交共同检查点及其事件。

        载荷只按规范 JSON 编码一次：行正文是这份字节的 zlib 流（级别 1），record_sha256 是未压缩字节的 SHA-256
        （与 sha256_json(payload) 相同）。
        """
        self.require_committed(state)
        if not snapshots:
            logging.error("Checkpoint received no participant snapshot")
            raise IntegrityError("Checkpoint requires participant snapshots")
        self._validate_participants(snapshots)
        for reference in snapshots.values():
            self.assets.read(reference)
        payload = self._checkpoint_payload(state, snapshots)
        encoded = canonical_json(payload)
        digest = hashlib.sha256(encoded).hexdigest()
        checkpoint = Checkpoint(**payload, record_sha256=digest)
        committed = self._committed_world()
        draft = EventDraft(event_type="checkpoint_created", occurred_at=committed.current_time,
                           visibility="system", data={"checkpoint_id": checkpoint.checkpoint_id})
        records = self._build_events(committed.version, committed.version, committed.current_time, [draft])
        try:
            with self.connection:
                self.connection.execute("INSERT INTO checkpoints VALUES(?,?,?)", (
                    checkpoint.checkpoint_id, zlib.compress(encoded, _CHECKPOINT_ZLIB_LEVEL), digest,
                ))
                self._insert_event(records[0])
                for reference in snapshots.values():
                    self.connection.execute("INSERT OR IGNORE INTO artifacts VALUES(?,?)", (
                        reference.sha256, canonical_json(reference.model_dump()).decode(),
                    ))
                self._require_stored_version(committed.version)
        except Exception:
            logging.exception("Common checkpoint transaction failed")
            raise
        return checkpoint

    def _validate_participants(self, snapshots: dict[str, AssetRef]) -> None:
        """@brief 检查点必须包含冻结清单中的全部人物和生活环境。"""
        expected = {f"agent:{agent_id}" for agent_id in self.manifest["agent_ids"]} | {"life"}
        if set(snapshots) != expected:
            raise IntegrityError("Checkpoint participant set does not match the frozen manifest")

    def _checkpoint_payload(self, state: dict, snapshots: dict[str, AssetRef]) -> dict:
        """@brief 绑定冻结运行身份和全部不可变参与者快照。"""
        return {"schema_version": 1, "checkpoint_id": uuid4().hex, "run_id": self.manifest["run_id"],
                "current_time": state["current_time"], "state_version": state["version"],
                "last_event_seq": self.last_event_seq + 1, "state": state,
                "snapshots": {name: reference.model_dump() for name, reference in snapshots.items()},
                "manifest_sha256": sha256_json(self.manifest)}

    def get_checkpoint(self, checkpoint_id: str) -> Checkpoint:
        """@brief 解压并按未压缩载荷核对记录摘要，再校验运行身份、事件与每个素材，不读取松散 JSON 收据。"""
        row = self.connection.execute("SELECT body,record_sha256 FROM checkpoints WHERE checkpoint_id=?",
                                      (checkpoint_id,)).fetchone()
        payload = _decompressed_record(checkpoint_id, row)
        checkpoint = Checkpoint(**json.loads(payload), record_sha256=row[1])
        if (checkpoint.run_id != self.manifest["run_id"]
                or checkpoint.current_time != checkpoint.state["current_time"]
                or checkpoint.state_version != checkpoint.state["version"]):
            raise IntegrityError("Checkpoint identity, time, or version disagrees with its world snapshot")
        if checkpoint.manifest_sha256 != sha256_json(self.manifest):
            raise IntegrityError("Checkpoint and frozen manifest disagree")
        self._validate_participants(checkpoint.snapshots)
        event = self.connection.execute("SELECT body FROM events WHERE event_seq=?",
                                        (checkpoint.last_event_seq,)).fetchone()
        if event is None:
            raise IntegrityError("Checkpoint event is missing")
        saved = Event.model_validate_json(event[0])
        if (saved.event_type != "checkpoint_created" or saved.data.get("checkpoint_id") != checkpoint_id
                or saved.run_id != checkpoint.run_id or saved.occurred_at != checkpoint.current_time
                or saved.state_version_before != checkpoint.state_version
                or saved.state_version_after != checkpoint.state_version):
            raise IntegrityError("Checkpoint event and committed record disagree")
        for reference in checkpoint.snapshots.values():
            self.assets.read(reference)
        return checkpoint


    def close(self) -> None:
        """@brief 关闭单写入连接，不改变运行或事件状态。"""
        self.connection.close()


def _decompressed_record(checkpoint_id: str, row: tuple | None) -> bytes:
    """@brief 解压检查点行正文并核对 record_sha256 是未压缩规范载荷的摘要。

    @param checkpoint_id 检查点身份，只用于错误日志
    @param row 查询得到的 (body, record_sha256)；不存在时为 None
    @return 未压缩的规范载荷字节
    @exception IntegrityError 行不存在、正文不是 zlib 流或摘要不符（Checkpoint is missing or its record is damaged）
    """
    if row is None:
        logging.error("Checkpoint %s does not exist", checkpoint_id)
        raise IntegrityError("Checkpoint is missing or its record is damaged")
    body, digest = row
    if not isinstance(body, bytes):
        logging.error("Checkpoint %s body is not a compressed record", checkpoint_id)
        raise IntegrityError("Checkpoint is missing or its record is damaged")
    try:
        payload = zlib.decompress(body)
    except zlib.error as error:
        logging.error("Checkpoint %s body does not decompress: %s", checkpoint_id, error)
        raise IntegrityError("Checkpoint is missing or its record is damaged") from error
    if hashlib.sha256(payload).hexdigest() != digest:
        logging.error("Checkpoint %s record hash does not match its payload", checkpoint_id)
        raise IntegrityError("Checkpoint is missing or its record is damaged")
    return payload

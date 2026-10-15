#!/usr/bin/env python3
"""录音应答运行：用一次真实运行自己录下的模型应答，在 127.0.0.1 上让被测检出的代码重跑这次运行。

@file     recorded_response_run.py
@brief    每次请求的模型可见字段必须与录音逐值相等，任何偏差以 HTTP 400 拒绝并写进偏差报告；在录音的检查点位置
          重现重启；各段沿用来源的运行身份（动作随机流以带运行身份的动作身份为键）。只读来源目录，真实密钥不进入子进程。
          设计见 docs/design/01-architecture.md「工程验证」与 docs/design/10-runtime.md「原生模型与工具」。

用法：
  python scripts/recorded_response_run.py run --project PROJECT --source RUN_DIR [RUN_DIR ...] --work WORK
         [--segments N] [--restart-from K:SEQ]
  python scripts/recorded_response_run.py raw-order --base WORK --head WORK --report FILE
  python scripts/recorded_response_run.py export-source --project PROJECT --source RUN_DIR --cutoff SEQ --work WORK
"""

from __future__ import annotations

import argparse
import difflib
import email.message
import gzip
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import tomllib
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

TOOL_ROOT = Path(__file__).resolve().parents[1]
if str(TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOL_ROOT))

from scripts.compare_runs import Chain, ComparisonError, canonical_bytes  # noqa: E402

logger = logging.getLogger(__name__)

#: 子进程唯一可用的占位凭据。
DUMMY_CREDENTIAL = "recorded-response-run"
#: 请求顶层的模型可见字段，另加 extra_body 的各键。
VISIBLE_FIELDS = ("model", "messages", "tools", "tool_choice", "max_tokens")
#: 拒绝后等子进程退出的上限（秒）。
REFUSAL_WATCHDOG_SECONDS = 600
#: 偏差差异文本行数上限。
DIFF_LINE_LIMIT = 400
#: 项目里恢复出的 AGentSociety2 包源码的相对位置。
AS2_PACKAGE = Path("vendor/agentsociety2/packages/agentsociety2")
#: 在子进程里找 agentsociety2 包目录的代码。
FIND_AS2 = ("import importlib.util, json; s = importlib.util.find_spec('agentsociety2'); "
            "print(json.dumps(list(s.submodule_search_locations)))")
_HEX_RUN_ID = re.compile(r"^[0-9a-f]{32}$")
_REQUESTS_SQL = ("SELECT event_seq, event_id, json_extract(body,'$.occurred_at'), json_extract(body,'$.data.purpose') "
                 "FROM events WHERE json_extract(body,'$.agent_id')=? "
                 "AND json_extract(body,'$.event_type')='model_request' ORDER BY event_seq")
_RESPONSES_SQL = ("SELECT event_seq, json_extract(body,'$.caused_by[0]'), json_extract(body,'$.data.phase'), "
                  "json_type(body,'$.data.response') FROM events WHERE json_extract(body,'$.agent_id')=? "
                  "AND json_extract(body,'$.event_type')='model_response' ORDER BY event_seq")


class RecordedResponseError(RuntimeError):
    """@brief 录音应答运行无法进行或结论无效。"""


def _fail(message: str) -> RecordedResponseError:
    """@brief 记录英文错误日志并返回同文的异常，由调用方抛出。"""
    logger.error("%s", message)
    return RecordedResponseError(message)


@dataclass(frozen=True)
class RecordedCall:
    """@brief 一次录音调用。"""

    index: int  #: 调用序号，1 起，按请求事件序号
    request_seq: int  #: model_request 事件序号
    response_seq: int  #: 终态 model_response 事件序号
    segment: int  #: 持有请求事件的段（1 起）
    occurred_at: int  #: 请求的模拟时刻
    purpose: str | None  #: 请求事件的 data.purpose


@dataclass(frozen=True)
class Boundary:
    """@brief 一次重启边界。"""

    segment: int  #: 边界前的段 k
    checkpoint_seq: int  #: 链位置 L_k
    first_call: int  #: 第 k+1 段的第一次调用
    discarded_request: dict | None  #: 第 k 段丢弃尾段里的第一条请求 D_k
    discarded_seq: int | None  #: D_k 在第 k 段库里的事件序号
    next_run_id: str  #: 第 k+1 段的运行身份


@dataclass(frozen=True)
class Plan:
    """@brief 录音运行计划。"""

    chain: Chain  #: 来源运行链
    calls: tuple[RecordedCall, ...]  #: 全部录音调用
    boundaries: tuple[Boundary, ...]  #: 各重启边界
    metadata: dict  #: 冻结的模型元数据（requested_model、context_window、max_output_tokens、metadata_url）
    api_key_env: str  #: 冻结配置声明的凭据变量名
    continuation: bool  #: 冻结的同秒续接开关


def _open(run: Path) -> sqlite3.Connection:
    """@brief 只读打开一个运行库，可在服务线程间共用（访问都在录音带锁内）。"""
    database = Path(run) / "footprints.sqlite"
    return sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False)


def _segment_of(seq: int, positions: tuple[int, ...]) -> int:
    """@brief 事件序号所在的段：L_{k-1} < seq ≤ L_k。"""
    return 1 + sum(1 for position in positions if seq > position)


def _recorded_calls(chain: Chain, connection: sqlite3.Connection) -> tuple[RecordedCall, ...]:
    """@brief 两条索引查询配对每次请求与它唯一的提供商响应，按请求序号编号。"""
    requests, responses = [], {}
    for agent_id in chain.manifests[-1]["agent_ids"]:
        requests += connection.execute(_REQUESTS_SQL, (agent_id,)).fetchall()
        for seq, cause, phase, kind in connection.execute(_RESPONSES_SQL, (agent_id,)):
            if phase == "response" and kind == "object":
                responses.setdefault(cause, []).append(seq)
    calls = []
    for index, (seq, event_id, occurred_at, purpose) in enumerate(sorted(requests), start=1):
        paired = responses.get(event_id, [])
        if not paired:
            raise _fail(f"Recorded call {index} at event {seq} has no provider response")
        if len(paired) > 1:
            raise _fail(f"Recorded call {index} at event {seq} has more than one terminal response")
        calls.append(RecordedCall(index, seq, paired[0], _segment_of(seq, chain.positions), occurred_at, purpose))
    return tuple(calls)


def _boundaries(chain: Chain, calls: tuple[RecordedCall, ...]) -> tuple[Boundary, ...]:
    """@brief 每个边界的第一次调用与第 k 段丢弃尾段里的第一条请求。"""
    boundaries = []
    for segment, position in enumerate(chain.positions, start=1):
        first = next((call.index for call in calls if call.request_seq > position), None)
        if first is None:
            raise _fail(f"Segment {segment + 1} records no model call after its restart")
        connection = _open(chain.runs[segment - 1])
        try:
            row = connection.execute("SELECT event_seq, body FROM events WHERE event_seq > ? AND "
                                     "json_extract(body,'$.event_type')='model_request' ORDER BY event_seq LIMIT 1",
                                     (position,)).fetchone()
        finally:
            connection.close()
        request = json.loads(row[1])["data"]["request"] if row else None
        boundaries.append(Boundary(segment, position, first, request, row[0] if row else None,
                                   chain.manifests[segment]["run_id"]))
    return tuple(boundaries)


def _frozen_config(run: Path, manifest: dict) -> tuple[str, dict]:
    """@brief 运行目录里冻结的 config.toml 原文及其解析结果。"""
    reference = manifest["environment_assets"]["config.toml"]
    text = (Path(run) / "assets" / reference["sha256"]).read_bytes().decode("utf-8")
    return text, tomllib.loads(text)


def load_plan(sources: list[Path]) -> Plan:
    """@brief 读来源链、调用与边界。"""
    chain = Chain.load(sources)
    for manifest in chain.manifests:
        if not _HEX_RUN_ID.match(str(manifest["run_id"])):
            raise _fail(f"Source run id {manifest['run_id']} is not a 32-character hex identity")
    connection = _open(chain.runs[-1])
    try:
        calls = _recorded_calls(chain, connection)
    finally:
        connection.close()
    final = chain.manifests[-1]
    _, config = _frozen_config(chain.runs[-1], final)
    continuation = final["stage_model_identity"]["stages"]["action"]["same_second_continuation"]
    return Plan(chain, calls, _boundaries(chain, calls), dict(final["model_metadata"]),
                config["model"]["api_key_env"], bool(continuation))


def rewrite_model_urls(text: str, urls: tuple[str, str], port: int) -> str:
    """@brief 只改 base_url 与 metadata_url 到 127.0.0.1:port（保留路径），并核对其余不变。"""
    expected = tomllib.loads(text)
    rewritten = text
    for key, url in zip(("base_url", "metadata_url"), urls):
        quoted = json.dumps(url)
        if text.count(quoted) != 1:
            raise _fail(f"Config file must contain each model URL exactly once: {key}")
        local = f"http://127.0.0.1:{port}{urlsplit(url).path}"
        rewritten = rewritten.replace(quoted, json.dumps(local))
        expected["model"][key] = local
    if tomllib.loads(rewritten) != expected:
        raise _fail("Rewritten config differs from the source beyond the two model URLs")
    return rewritten


def write_input(plan: Plan, destination: Path, port: int) -> None:
    """@brief 从冻结素材复原输入并改写两个模型地址；任何素材摘要不符时什么都不写。"""
    run, manifest = plan.chain.runs[-1], plan.chain.manifests[-1]
    files = {}
    for reference in manifest["environment_assets"].values():
        source = reference["source"]
        if source.get("kind") != "environment_input":
            continue
        content = (Path(run) / "assets" / reference["sha256"]).read_bytes()
        if hashlib.sha256(content).hexdigest() != reference["sha256"] or len(content) != reference["size_bytes"]:
            raise _fail(f"Frozen input asset {source['path']} does not match its recorded digest")
        files[source["path"]] = content
    text, config = _frozen_config(run, manifest)
    identity = manifest["stage_model_identity"]
    urls = (config["model"]["base_url"], config["model"]["metadata_url"])
    if urls != (identity["base_url"], identity["metadata_url"]):
        raise _fail("Frozen config disagrees with the manifest model identity")
    files["config.toml"] = rewrite_model_urls(text, urls, port).encode("utf-8")
    for relative, content in files.items():
        target = Path(destination) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def expected_body(request: dict) -> dict:
    """@brief 录音请求应有的 HTTP 正文：五个可见顶层字段加 extra_body 的各键。"""
    return {key: request[key] for key in VISIBLE_FIELDS if key in request} | request.get("extra_body", {})


def _differs(expected: dict, received: dict, key: str) -> bool:
    """@brief 一个顶层键是否只在一边出现或规范字节不同。"""
    if (key in expected) != (key in received):
        return True
    return canonical_bytes(expected[key]) != canonical_bytes(received[key])


def _diff_text(recorded: object, received: object) -> str:
    """@brief 两个元素的统一差异文本，行数截到 DIFF_LINE_LIMIT。"""
    lines = [json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True).splitlines()
             for value in (recorded, received)]
    diff = difflib.unified_diff(lines[0], lines[1], fromfile="recorded", tofile="received", lineterm="")
    return "\n".join(list(diff)[:DIFF_LINE_LIMIT])


def _first_index(first: list, second: list) -> int:
    """@brief 两列首个规范字节不同的下标（一列是另一列前缀时为较短长度）。"""
    for index, (left, right) in enumerate(zip(first, second)):
        if canonical_bytes(left) != canonical_bytes(right):
            return index
    return min(len(first), len(second))


def _layers(content: object) -> list | None:
    """@brief 用户消息内容能解析成对象数组（提示层）时返回该数组。"""
    try:
        value = json.loads(content) if isinstance(content, str) else None
    except ValueError:
        return None
    return value if isinstance(value, list) and all(isinstance(item, dict) for item in value) else None


def _message_layer(expected: dict | None, received: dict | None) -> tuple[str, object, object]:
    """@brief 一对不同消息的层名与要比较的元素。"""
    message = expected if expected is not None else received
    role = message.get("role") if isinstance(message, dict) else None
    if role == "system":
        return "system", expected, received
    if role == "user":
        layers = [_layers((item or {}).get("content")) for item in (expected, received)]
        if layers[0] is not None and layers[1] is not None:
            index = _first_index(layers[0], layers[1])
            pair = [items[index] if index < len(items) else None for items in layers]
            element = pair[0] if pair[0] is not None else pair[1]
            name = element.get("layer") if isinstance(element.get("layer"), str) else f"user[{index}]"
            return name, pair[0], pair[1]
        return "user", expected, received
    return str(role), expected, received


def first_difference(expected: dict, received: dict) -> dict | None:
    """@brief 首个不同之处的字段、层与差异文本；相同时为 None。"""
    fields = sorted(key for key in set(expected) | set(received) if _differs(expected, received, key))
    if not fields:
        return None
    if "messages" not in fields:
        layer, pair = ",".join(fields), (expected.get(fields[0]), received.get(fields[0]))
        if "tools" in fields:
            tools = [expected.get("tools") or [], received.get("tools") or []]
            index = _first_index(tools[0], tools[1])
            pair = [items[index] if index < len(items) else None for items in tools]
            element = pair[0] if pair[0] is not None else pair[1]
            name = ((element or {}).get("function") or {}).get("name") if isinstance(element, dict) else None
            layer = f"tools:{name or f'tools[{index}]'}"
        return {"fields": fields, "layer": layer, "message_index": None, "diff": _diff_text(*pair)}
    messages = [expected.get("messages") or [], received.get("messages") or []]
    index = _first_index(messages[0], messages[1])
    pair = [items[index] if index < len(items) else None for items in messages]
    layer, recorded, sent = _message_layer(*pair)
    return {"fields": fields, "layer": layer, "message_index": index, "diff": _diff_text(recorded, sent)}


def _purpose(request: dict | None, data_purpose: str | None) -> str | None:
    """@brief 偏差记录里的调用用途（信息性）。"""
    if request is None:
        return None
    if data_purpose == "plan_day":
        return "plan_day"
    names = [((tool or {}).get("function") or {}).get("name") for tool in request.get("tools") or []]
    if "start_main_activity" in names:
        return "decision"
    if not names:
        return "consolidation"
    return "reflection" if names == ["submit_reflection"] else "other"


def _error_body(code: str, message: str) -> bytes:
    """@brief HTTP 400 正文：LiteLLM 把它读成 BadRequestError，AS2 不重试。"""
    return json.dumps({"error": {"type": "invalid_request_error", "code": code, "message": message}}).encode()


class Cassette:
    """@brief 录音带状态机与请求日志：一把锁串行处理每个请求，服务线程只搬运字节。"""

    def __init__(self, plan: Plan, work: Path):
        """@brief 打开来源各段库，建请求日志；next_call 从 1 起。"""
        self.plan, self.work = plan, Path(work)
        self.next_call, self.segment = 1, 1
        self.refused: set[int] = set()
        self.divergences: list[dict] = []
        self.refusal_at: float | None = None
        self.models_requests: Counter = Counter()
        self.served, self.arrival, self.error = 0, 0, None
        self.control: tuple[int, int] | None = None
        self.lock = threading.Lock()
        self.connections = [_open(run) for run in plan.chain.runs]
        (self.work / "server" / "bodies").mkdir(parents=True, exist_ok=True)
        self.metadata_path = urlsplit(plan.metadata.get("metadata_url", "/models")).path
        base_path = urlsplit(plan.chain.manifests[-1]["stage_model_identity"]["base_url"]).path.rstrip("/")
        self.chat_path = f"{base_path}/chat/completions"

    def begin_segment(self, segment: int) -> None:
        """@brief 父进程启动第 segment 段之前调用。"""
        with self.lock:
            self.segment, self.refusal_at = segment, None

    def rewind(self, seq: int) -> None:
        """@brief 负对照：下一次调用回到请求序号大于 seq 的第一次调用。"""
        with self.lock:
            self.next_call = next(call.index for call in self.plan.calls if call.request_seq > seq)

    def close(self) -> None:
        """@brief 关闭来源库连接。"""
        for connection in self.connections:
            connection.close()

    def _event(self, seq: int, connection: sqlite3.Connection | None = None) -> dict:
        """@brief 按主键读一条来源事件正文（缺省读最后一段库）。"""
        row = (connection or self.connections[-1]).execute("SELECT body FROM events WHERE event_seq=?",
                                                            (seq,)).fetchone()
        return json.loads(row[0])

    def _recorded_request(self, index: int) -> dict:
        """@brief 第 index 次录音调用的请求。"""
        return self._event(self.plan.calls[index - 1].request_seq)["data"]["request"]

    def _recorded_response(self, index: int) -> dict:
        """@brief 第 index 次录音调用的提供商响应。"""
        return self._event(self.plan.calls[index - 1].response_seq)["data"]["response"]

    def answer(self, method: str, path: str, headers: email.message.Message, body: bytes) -> tuple[int, bytes]:
        """@brief 处理一个 HTTP 请求，返回（状态码, 正文）；录音带自身的任何异常都答 400 cassette_error。"""
        with self.lock:
            self.arrival += 1
            try:
                if headers.get("Authorization") != f"Bearer {DUMMY_CREDENTIAL}":
                    logger.error("Refused a request without the recorded-response credential")
                    return self._reject("credential", (method, path, body), "credential")
                route = urlsplit(path).path
                if method == "GET" and route == self.metadata_path:
                    return self._models(method, path)
                if method == "POST" and route == self.chat_path:
                    return self._chat(method, path, body)
                logger.error("Refused an unexpected request %s %s", method, path)
                return self._reject("unexpected_path", (method, path, body), "unexpected_path")
            except Exception:
                logger.exception("Recorded-response cassette failed on %s %s", method, path)
                self.error = f"Recorded-response cassette failed on {method} {path}"
                return self._reject("cassette_error", (method, path, body), "cassette_error")

    def _models(self, method: str, path: str) -> tuple[int, bytes]:
        """@brief 模型元数据：冻结的容量，计入本段的元数据请求次数。"""
        self.models_requests[self.segment] += 1
        self._log(method, path, b"", None, "models")
        metadata = self.plan.metadata
        payload = {"object": "list", "data": [{"id": metadata["requested_model"], "object": "model",
                                               "context_window": metadata["context_window"],
                                               "max_output_tokens": metadata["max_output_tokens"]}]}
        return 200, json.dumps(payload).encode()

    def _chat(self, method: str, path: str, body: bytes) -> tuple[int, bytes]:
        """@brief 一次聊天请求按状态机核对、拒绝或回放。"""
        request = (method, path, body)
        if self.divergences:
            self._log(method, path, body, None, "divergence")
            return 400, _error_body("after_divergence", "The replay already diverged")
        try:
            received = json.loads(body)
        except ValueError:
            received = None
        if not isinstance(received, dict):
            return self._diverge("mismatch", request, {"fields": ["body"], "layer": "body", "message_index": None,
                                                       "diff": ""})
        if self.segment in self.refused:
            return self._diverge("after_refusal", request, None)
        if self.next_call > len(self.plan.calls):
            return self._diverge("extra_call", request, None)
        boundary = self._pending_boundary()
        if boundary is not None:
            difference = first_difference(expected_body(self._pre_restart_expected(boundary)), received)
            if difference is None:
                self._log(method, path, body, self.next_call, "planned_restart")
                return self._refuse_restart(boundary)
            return self._diverge("pre_restart_mismatch", request, difference, boundary)
        difference = first_difference(expected_body(self._recorded_request(self.next_call)), received)
        if difference is not None:
            return self._diverge("mismatch", request, difference)
        self._log(method, path, body, self.next_call, "served")
        response = self._recorded_response(self.next_call)
        self.next_call += 1
        self.served += 1
        return 200, json.dumps(response, ensure_ascii=False).encode("utf-8")

    def _pending_boundary(self) -> Boundary | None:
        """@brief 本段的边界还没拒绝过、而下一次调用正是下一段的第一次调用时返回该边界。"""
        for boundary in self.plan.boundaries:
            if (boundary.segment == self.segment and boundary.segment not in self.refused
                    and boundary.first_call == self.next_call):
                return boundary
        return None

    def _pre_restart_expected(self, boundary: Boundary) -> dict:
        """@brief 重启前那次调用应等于来源丢弃尾段里的请求 D_k；没有时等于重做的那次调用。"""
        if boundary.discarded_request is not None:
            return boundary.discarded_request
        return self._recorded_request(boundary.first_call)

    def _refuse_restart(self, boundary: Boundary) -> tuple[int, bytes]:
        """@brief 计划内重启：答 400 planned_restart，记下这个边界，next_call 不动。"""
        self.refused.add(boundary.segment)
        self.refusal_at = time.monotonic()
        logger.info("Planned restart refusal before segment %d at call %d", boundary.segment + 1, self.next_call)
        return 400, _error_body("planned_restart", f"Planned restart before segment {boundary.segment + 1}")

    def _reject(self, code: str, request: tuple[str, str, bytes], kind: str) -> tuple[int, bytes]:
        """@brief 凭据、路径或录音带故障：写日志、记一条无调用序号的偏差、答 400。"""
        method, path, body = request
        self._log(method, path, body, None, kind)
        self._record({"kind": kind, "call_index": None, "source_seq": None, "segment": self.segment,
                      "simulated_time": None, "purpose": None, "fields": [], "layer": None,
                      "message_index": None, "diff": "", "matches_discarded_request": None,
                      "body_sha256": hashlib.sha256(body).hexdigest() if body else None})
        return 400, _error_body(code, f"Recorded-response run refused the request: {kind}")

    def _diverge(self, kind: str, request: tuple[str, str, bytes], difference: dict | None,
                 boundary: Boundary | None = None) -> tuple[int, bytes]:
        """@brief 记一条带调用位置的偏差，答 400。"""
        method, path, body = request
        self._log(method, path, body, None if kind == "extra_call" else self.next_call,
                  "extra_call" if kind == "extra_call" else "divergence")
        record = self._call_position(kind, boundary)
        record.update(difference or {"fields": [], "layer": None, "message_index": None, "diff": ""})
        record.update(kind=kind, body_sha256=hashlib.sha256(body).hexdigest(),
                      matches_discarded_request=self._matches_discarded(kind, body))
        self._record(record)
        code = "extra_call" if kind == "extra_call" else "recorded_response_divergence"
        return 400, _error_body(code, f"Recorded-response divergence: {kind}")

    def _call_position(self, kind: str, boundary: Boundary | None) -> dict:
        """@brief 偏差记录里的调用序号、来源事件、段、模拟时刻与用途。"""
        if kind == "extra_call":
            return {"call_index": None, "source_seq": None, "segment": self.segment, "simulated_time": None,
                    "purpose": None}
        call = self.plan.calls[self.next_call - 1]
        if boundary is not None and boundary.discarded_seq is not None:
            event = self._event(boundary.discarded_seq, self.connections[boundary.segment - 1])
            seq, segment, occurred_at = boundary.discarded_seq, boundary.segment, event["occurred_at"]
            request, data_purpose = event["data"]["request"], event["data"].get("purpose")
        else:
            seq, segment, occurred_at = call.request_seq, call.segment, call.occurred_at
            request, data_purpose = self._recorded_request(call.index), call.purpose
        moment = datetime.fromtimestamp(occurred_at, ZoneInfo(self.plan.chain.timezone)).isoformat()
        return {"call_index": call.index, "source_seq": seq, "segment": segment, "simulated_time": moment,
                "purpose": _purpose(request, data_purpose)}

    def _matches_discarded(self, kind: str, body: bytes) -> bool | None:
        """@brief 重启后第一次调用偏离时，收到的正文是否等于来源丢弃的那条请求 D_k。"""
        for boundary in self.plan.boundaries:
            if (kind == "mismatch" and boundary.segment + 1 == self.segment and boundary.first_call == self.next_call
                    and boundary.discarded_request is not None):
                received = json.loads(body)
                return first_difference(expected_body(boundary.discarded_request), received) is None
        return None

    def _record(self, record: dict) -> None:
        """@brief 记一条偏差并写英文错误日志。"""
        if not self.divergences:
            self.refusal_at = time.monotonic()
        self.divergences.append(record)
        logger.error("Recorded-response divergence at call %s (segment %s, event %s, %s): %s differs",
                     record["call_index"], record["segment"], record["source_seq"], record["simulated_time"],
                     record["layer"])

    def _log(self, method: str, path: str, body: bytes, call: int | None, outcome: str) -> None:
        """@brief 请求日志一行（没有壁钟），原始正文按摘要存一次。"""
        digest = hashlib.sha256(body).hexdigest() if body else None
        if digest is not None:
            target = self.work / "server" / "bodies" / f"{digest}.json.gz"
            if not target.exists():
                target.write_bytes(gzip.compress(body, mtime=0))
        line = {"arrival": self.arrival, "body_bytes": len(body), "body_sha256": digest, "call": call,
                "method": method, "outcome": outcome, "path": path, "segment": self.segment}
        with (self.work / "server" / "requests.jsonl").open("ab") as stream:
            stream.write(canonical_bytes(line) + b"\n")


class _Handler(BaseHTTPRequestHandler):
    """@brief 只搬运字节：读正文、交给录音带、总是带 Content-Length 回写。"""

    cassette: Cassette

    def _handle(self) -> None:
        """@brief 处理任意方法的请求。"""
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        status, payload = self.cassette.answer(self.command, self.path, self.headers, body)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle

    def log_message(self, *args) -> None:
        """@brief 不写访问日志（请求日志由录音带写）。"""


def serve(cassette: Cassette) -> ThreadingHTTPServer:
    """@brief 在 127.0.0.1 的随机端口起服务线程。"""
    handler = type("CassetteHandler", (_Handler,), {"cassette": cassette})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="recorded-response-server", daemon=True).start()
    return server


def as2_location(root: Path, environment: Mapping[str, str]) -> list[Path]:
    """@brief 在 root 下用给定环境起子进程，返回 agentsociety2 解析出的全部包目录；是否恰好一个且在 root 内由调用方判定。"""
    result = subprocess.run([sys.executable, "-P", "-c", FIND_AS2], cwd=root, env=dict(environment),
                            capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        logger.error("No agentsociety2 package resolves in %s", root)
        return []
    return [Path(location) for location in json.loads(result.stdout)]


def _secret_name(name: str) -> bool:
    """@brief 凭据类变量名。"""
    upper = name.upper()
    return any(marker in upper for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD"))


def _routing_name(name: str) -> bool:
    """@brief 会把模型流量或 Ray 工作进程引到别处的变量名。"""
    return name.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "RAY_ADDRESS"}


def child_environment(parent: Mapping[str, str], project: Path, api_key_env: str) -> dict[str, str]:
    """@brief 子进程环境：去掉凭据、代理与外部 Ray 地址，只给占位凭据，只导入项目自己的代码。"""
    variables = {name: value for name, value in parent.items() if not _secret_name(name) and not _routing_name(name)}
    project = Path(project).resolve()
    variables.update({api_key_env: DUMMY_CREDENTIAL, "NO_PROXY": "127.0.0.1,localhost",
                      "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1", "LITELLM_LOCAL_MODEL_COST_MAP": "True",
                      "PYTHONPATH": os.pathsep.join(str(path) for path in (project / "src", project,
                                                                            project / AS2_PACKAGE))})
    return variables


def judge_segment(plan: Plan, cassette: Cassette, segment: int, exit_code: int) -> dict:
    """@brief 只凭录音带状态与退出码判定一段的结局（计划内重启、提前完成、缺调用或错误）。"""
    log = cassette.work / "logs" / f"segment-{segment:02d}.log"
    if cassette.error is not None:
        return {"ended_by": "failure", "verdict": "error", "error": cassette.error}
    if cassette.divergences:
        return {"ended_by": "divergence", "verdict": "divergence", "error": None}
    last = segment == len(plan.chain.runs)
    if not last and segment in cassette.refused and exit_code != 0:
        return {"ended_by": "planned_restart", "verdict": "zero_divergence", "error": None}
    if not last and exit_code == 0:
        cassette._record({**_plain("segment_completed_early", segment), "call_index": cassette.next_call})
        return {"ended_by": "completed", "verdict": "divergence", "error": None}
    if last and exit_code == 0 and cassette.next_call == len(plan.calls) + 1:
        return {"ended_by": "completed", "verdict": "zero_divergence", "error": None}
    if last and exit_code == 0:
        cassette._record({**_plain("missing_calls", segment), "call_index": cassette.next_call})
        return {"ended_by": "completed", "verdict": "divergence", "error": None}
    message = f"Segment {segment} failed before its planned restart; see {log}"
    logger.error("%s", message)
    return {"ended_by": "failure", "verdict": "error", "error": message}


def _plain(kind: str, segment: int) -> dict:
    """@brief 不来自某次请求的偏差记录。"""
    return {"kind": kind, "call_index": None, "source_seq": None, "segment": segment, "simulated_time": None,
            "purpose": None, "fields": [], "layer": None, "message_index": None, "diff": "",
            "matches_discarded_request": None, "body_sha256": None}


@dataclass(frozen=True)
class SegmentJob:
    """@brief 一段的输入。"""

    plan: Plan  #: 录音运行计划
    cassette: Cassette  #: 录音带（含负对照的重启位置）
    project: Path  #: 被测检出
    work: Path  #: 工作目录
    segment: int  #: 段次序，1 起


def _resume_checkpoint(job: SegmentJob) -> str:
    """@brief 第 k 段从工具自己第 k-1 段库里链位置 L_{k-1}（负对照为给定序号）的检查点恢复。"""
    previous = job.segment - 1
    control = job.cassette.control
    seq = control[1] if control is not None and control[0] == previous else job.plan.chain.positions[previous - 1]
    connection = _open(job.work / "segments" / f"{previous:02d}")
    try:
        row = connection.execute("SELECT body FROM events WHERE event_seq=?", (seq,)).fetchone()
    finally:
        connection.close()
    event = json.loads(row[0]) if row else {}
    if event.get("event_type") != "checkpoint_created":
        raise _fail(f"Segment {previous} has no checkpoint_created event at sequence {seq}")
    return event["data"]["checkpoint_id"]


def _segment_argv(job: SegmentJob) -> tuple[str, list[str], str | None]:
    """@brief 第 k 段子进程的模式、命令参数与恢复的检查点身份。"""
    work, project = job.work, str(job.project)
    output = str(work / "segments" / f"{job.segment:02d}")
    if job.segment == 1:
        switch = "on" if job.plan.continuation else "off"
        return "run", ["--project-root", project, "run", "--environment", str(work / "input"), "--output", output,
                       "--same-second-continuation", switch], None
    checkpoint = _resume_checkpoint(job)
    parent = str(work / "segments" / f"{job.segment - 1:02d}")
    return "resume", ["--project-root", project, "resume", "--environment", str(work / "input"), "--parent", parent,
                      "--checkpoint", checkpoint, "--output", output], checkpoint


def _kill_tree(process: subprocess.Popen) -> None:
    """@brief 结束子进程及其全部后代。"""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True, check=False)
    else:
        os.killpg(process.pid, signal.SIGKILL)


def _run_child(command: list[str], job: SegmentJob, log: Path) -> tuple[int, bool]:
    """@brief 起一段子进程（新进程组），拒绝或偏差之后超过看守时限仍未退出就结束整棵进程树。"""
    environment = child_environment(os.environ, job.project, job.plan.api_key_env)
    group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
             else {"start_new_session": True})
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("wb") as stream:
        process = subprocess.Popen(command, cwd=job.project, env=environment, stdout=stream,
                                   stderr=subprocess.STDOUT, **group)
        while True:
            try:
                return process.wait(timeout=5), False
            except subprocess.TimeoutExpired:
                since = job.cassette.refusal_at
                if since is not None and time.monotonic() - since > REFUSAL_WATCHDOG_SECONDS:
                    logger.error("Segment %d did not stop within %d s after the planned refusal", job.segment,
                                 REFUSAL_WATCHDOG_SECONDS)
                    _kill_tree(process)
                    return process.wait(), True


def _run_identity(job: SegmentJob, log: Path) -> tuple[str | None, str | None]:
    """@brief 第 k 段清单里的运行身份，与来源不同或没写清单时给出错误。"""
    path = job.work / "segments" / f"{job.segment:02d}" / "manifest.json"
    expected = job.plan.chain.manifests[job.segment - 1]["run_id"]
    if not path.is_file():
        message = f"Segment {job.segment} wrote no run manifest; see {log}"
        logger.error("%s", message)
        return None, message
    actual = json.loads(path.read_text(encoding="utf-8"))["run_id"]
    if actual != expected:
        message = f"Segment {job.segment} ran with run id {actual}, expected {expected}"
        logger.error("%s", message)
        return actual, message
    return actual, None


def run_segment(job: SegmentJob) -> dict:
    """@brief 起一段子进程、看守拒绝后的退出、交 judge_segment 判定并核对运行身份。"""
    cassette, segment = job.cassette, job.segment
    mode, argv, checkpoint = _segment_argv(job)
    spec = job.work / "segments" / f"{segment:02d}.json"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_bytes(canonical_bytes({"argv": argv, "mode": mode,
                                      "run_id": job.plan.chain.manifests[segment - 1]["run_id"]}) + b"\n")
    cassette.begin_segment(segment)
    log = job.work / "logs" / f"segment-{segment:02d}.log"
    started, first = time.monotonic(), cassette.next_call
    command = [sys.executable, "-P", str(TOOL_ROOT / "scripts" / "recorded_response_child.py"), str(spec)]
    exit_code, killed = _run_child(command, job, log)
    judged = judge_segment(job.plan, cassette, segment, exit_code)
    run_id, identity_error = _run_identity(job, log)
    if identity_error is not None and judged["verdict"] != "error":
        judged = {**judged, "verdict": "error", "error": identity_error}
    served = cassette.next_call > first
    entry = {"ended_by": "killed" if killed else judged["ended_by"], "exit_code": exit_code,
             "first_call": first if served else None, "index": segment,
             "last_call": cassette.next_call - 1 if served else None, "mode": mode,
             "models_requests": cassette.models_requests[segment], "parent_checkpoint_id": checkpoint,
             "run_dir": str(job.work / "segments" / f"{segment:02d}"), "run_id": run_id,
             "wall_seconds": round(time.monotonic() - started, 3)}
    return {"entry": entry, "verdict": judged["verdict"], "error": judged["error"]}


def _git_state(project: Path) -> tuple[str | None, bool | None]:
    """@brief 被测检出的提交与是否有未提交改动；git 失败时为 None。"""
    try:
        head = subprocess.run(["git", "-C", str(project), "rev-parse", "HEAD"], capture_output=True, text=True)
        status = subprocess.run(["git", "-C", str(project), "status", "--porcelain", "--untracked-files=normal"],
                                capture_output=True, text=True)
    except OSError:
        logger.exception("git is not available for %s", project)
        return None, None
    if head.returncode != 0 or status.returncode != 0:
        return None, None
    return head.stdout.strip(), bool(status.stdout.strip())


def _days(run: Path, cutoff: int, timezone: str) -> list[dict]:
    """@brief 每个本地日期的决策、调用、事件数与同一运行相邻事件的壁钟毫秒差之和（信息性）。"""
    zone, days, last = ZoneInfo(timezone), {}, {}
    connection = _open(run)
    try:
        rows = connection.execute("SELECT json_extract(body,'$.occurred_at'), json_extract(body,'$.event_type'), "
                                  "json_extract(body,'$.run_id'), json_extract(body,'$.wall_elapsed_ms') FROM events "
                                  "WHERE event_seq<=? ORDER BY event_seq", (cutoff,)).fetchall()
    finally:
        connection.close()
    for occurred_at, event_type, run_id, elapsed in rows:
        day = days.setdefault(datetime.fromtimestamp(occurred_at, zone).date().isoformat(),
                              {"calls": 0, "decisions": 0, "events": 0, "milliseconds": 0})
        day["events"] += 1
        day["decisions"] += event_type == "decision_started"
        day["calls"] += event_type == "model_request"
        if run_id in last:
            day["milliseconds"] += max(elapsed - last[run_id], 0)
        last[run_id] = elapsed
    return [{"calls": day["calls"], "date": date, "decisions": day["decisions"],
             "engine_seconds": round(day["milliseconds"] / 1000, 3),
             "engine_seconds_per_decision": (round(day["milliseconds"] / 1000 / day["decisions"], 3)
                                             if day["decisions"] else None), "events": day["events"]}
            for date, day in sorted(days.items())]


def _child_command(command: list[str], project: Path, api_key_env: str, log: Path) -> subprocess.CompletedProcess:
    """@brief 在被测检出里用子进程环境跑一条命令，输出写进日志。"""
    result = subprocess.run(command, cwd=project, env=child_environment(os.environ, project, api_key_env),
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(result.stdout + result.stderr, encoding="utf-8")
    return result


def finish(plan: Plan, work: Path, project: Path, report: dict) -> None:
    """@brief 导出、核验并写日表；只回放了前 N 段时按 L_N 截止导出，部分导出不做交付核验。"""
    count = len(report["segments"])
    last_run = Path(report["segments"][-1]["run_dir"])
    cutoff = plan.chain.positions[count - 1] if count < len(plan.chain.runs) else None
    command = [sys.executable, "-P", "-m", "worldmock.cli", "--project-root", str(project), "export", "--run-dir",
               str(last_run), "--output", str(work / "export"), *(["--cutoff", str(cutoff)] if cutoff else [])]
    exported = _child_command(command, project, plan.api_key_env, work / "logs" / "export.log")
    if exported.returncode != 0:
        report.update(verdict="error", error=str(_fail(f"Export failed; see {work / 'logs' / 'export.log'}")))
        return
    manifest = json.loads((work / "export" / "manifest.json").read_text(encoding="utf-8"))
    entry = {"check": "not_applicable", "cutoff": manifest["cutoff_event_seq"], "dir": str(work / "export"),
             "error": None}
    if cutoff is None:
        check = [sys.executable, "-P", "-c", "import sys; from pathlib import Path; from worldmock.footprints."
                 "validation import validate_export; validate_export(Path(sys.argv[1]))", str(work / "export")]
        checked = _child_command(check, project, plan.api_key_env, work / "logs" / "export-check.log")
        entry["check"] = "passed" if checked.returncode == 0 else "failed"
        if checked.returncode != 0:
            entry["error"] = (checked.stderr.strip().splitlines() or [""])[-1]
            report.update(verdict="error", error=str(_fail(f"Export check failed: {entry['error']}")))
    report["export"] = entry
    report["days"] = _days(last_run, manifest["cutoff_event_seq"], plan.chain.timezone)


def _plan_document(plan: Plan, port: int) -> dict:
    """@brief WORK/plan.json 的内容。"""
    boundaries = [{"checkpoint_seq": item.checkpoint_seq, "discarded_seq": item.discarded_seq,
                   "first_call": item.first_call, "next_run_id": item.next_run_id, "segment": item.segment}
                  for item in plan.boundaries]
    segments = [{"index": index, "run_dir": str(run), "run_id": manifest["run_id"]}
                for index, (run, manifest) in enumerate(zip(plan.chain.runs, plan.chain.manifests), start=1)]
    return {"api_key_env": plan.api_key_env, "boundaries": boundaries, "calls": len(plan.calls),
            "continuation": plan.continuation, "port": port, "requested_model": plan.metadata["requested_model"],
            "segments": segments, "timezone": plan.chain.timezone}


def _report(plan: Plan, project: Path, control: tuple[int, int] | None) -> dict:
    """@brief report.json 的骨架。"""
    commit, dirty = _git_state(project)
    positions = (*plan.chain.positions, None)
    source = [{"checkpoint_seq": positions[index - 1], "index": index, "run_dir": str(run),
               "run_id": manifest["run_id"]}
              for index, (run, manifest) in enumerate(zip(plan.chain.runs, plan.chain.manifests), start=1)]
    return {"calls_served": 0, "control": {"boundary": control[0], "seq": control[1]} if control else None,
            "days": [], "divergences": [], "error": None, "export": None,
            "project": {"code_sha256": None, "commit": commit, "dirty": dirty, "root": str(project)},
            "restarts_reproduced": 0, "schema_version": 1, "segments": [],
            "source": {"calls": len(plan.calls), "segments": source, "timezone": plan.chain.timezone},
            "verdict": "zero_divergence"}


def _replay(plan: Plan, cassette: Cassette, project: Path, work: Path, count: int) -> dict:
    """@brief 依次回放前 count 段，第一处偏差或错误即停；零偏差且不是负对照时导出并核验。"""
    report = _report(plan, project, cassette.control)
    for segment in range(1, count + 1):
        if cassette.control is not None and segment == cassette.control[0] + 1:
            cassette.rewind(cassette.control[1])
        try:
            outcome = run_segment(SegmentJob(plan, cassette, project, work, segment))
        except RecordedResponseError as error:
            report.update(verdict="error", error=str(error))
            break
        report["segments"].append(outcome["entry"])
        if outcome["verdict"] == "error":
            report.update(verdict="error", error=outcome["error"])
            break
        if cassette.divergences:
            break
    if report["verdict"] != "error" and cassette.divergences:
        report["verdict"] = "divergence"
    report.update(calls_served=cassette.served, divergences=cassette.divergences,
                  restarts_reproduced=sum(1 for item in report["segments"] if item["mode"] == "resume"))
    first = work / "segments" / "01" / "manifest.json"
    if first.is_file():
        report["project"]["code_sha256"] = json.loads(first.read_text(encoding="utf-8")).get("code_sha256")
    if report["verdict"] == "zero_divergence" and cassette.control is None and report["segments"]:
        finish(plan, work, project, report)
    return report


def _options(arguments: argparse.Namespace, plan: Plan) -> tuple[int, tuple[int, int] | None]:
    """@brief 回放的段数与负对照的（边界, 序号）。"""
    total = len(plan.chain.runs)
    count = total if arguments.segments is None else arguments.segments
    if not 1 <= count <= total:
        raise _fail(f"Segments must be between 1 and {total}")
    if arguments.restart_from is None:
        return count, None
    boundary, _, seq = arguments.restart_from.partition(":")
    if not (boundary.isdigit() and seq.isdigit()) or not 1 <= int(boundary) < count:
        raise _fail(f"Restart control {arguments.restart_from} names no replayed boundary")
    if int(seq) > plan.chain.positions[int(boundary) - 1]:
        raise _fail(f"Restart control sequence {seq} is after the recorded restart point")
    return count, (int(boundary), int(seq))


def _write_json(path: Path, value: dict) -> None:
    """@brief 规范 JSON 加一个换行。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value) + b"\n")


def _exit_code(report: dict) -> int:
    """@brief 0 零偏差且核验通过，1 偏差，2 错误。"""
    if report["verdict"] == "divergence":
        return 1
    return 0 if report["verdict"] == "zero_divergence" else 2


def _run(arguments: argparse.Namespace) -> int:
    """@brief run 子命令：预检、起服务、复原输入、逐段回放、导出核验并写报告。"""
    project, work = arguments.project.resolve(), arguments.work.resolve()
    if work.exists():
        raise _fail(f"Work directory {work} already exists")
    work.mkdir(parents=True)
    as2 = project / AS2_PACKAGE / "agentsociety2"
    if not as2.is_dir():
        raise _fail(f"Project root has no restored AS2 source: {as2}")
    plan = load_plan(arguments.source)
    locations = as2_location(project, child_environment(os.environ, project, plan.api_key_env))
    if len(locations) != 1 or not locations[0].resolve().is_relative_to(project):
        raise _fail(f"Project AS2 source resolves outside the project: {[str(item) for item in locations]}")
    count, control = _options(arguments, plan)
    cassette = Cassette(plan, work)
    cassette.control = control
    server = serve(cassette)
    try:
        write_input(plan, work / "input", server.server_address[1])
        _write_json(work / "plan.json", _plan_document(plan, server.server_address[1]))
        report = _replay(plan, cassette, project, work, count)
    finally:
        server.shutdown()
        server.server_close()
        cassette.close()
    _write_json(work / "report.json", report)
    print(f"Recorded-response run: {report['calls_served']} calls served, {report['restarts_reproduced']} restarts "
          f"reproduced, {len(report['divergences'])} divergences")
    return _exit_code(report)


def _request_bodies(work: Path) -> dict[tuple[int, str], str]:
    """@brief 一次重放里回放或计划拒绝的每次调用的原始正文摘要。"""
    bodies = {}
    for line in (work / "server" / "requests.jsonl").read_bytes().splitlines():
        row = json.loads(line)
        if row["outcome"] in ("served", "planned_restart"):
            bodies[(row["call"], row["outcome"])] = row["body_sha256"]
    return bodies


def _raw_body(work: Path, digest: str | None) -> object:
    """@brief 按摘要读回原始正文，对象读成键值对列表，保留键序。"""
    if digest is None:
        return None
    raw = gzip.decompress((work / "server" / "bodies" / f"{digest}.json.gz").read_bytes())
    return json.loads(raw, object_pairs_hook=list)


def _is_object(value: object) -> bool:
    """@brief object_pairs_hook=list 读出的对象：元素都是（键, 值）元组的列表。"""
    return isinstance(value, list) and all(isinstance(item, tuple) for item in value)


def _order_paths(base: object, head: object, path: str, out: list[dict]) -> None:
    """@brief 两份原始正文在键序或取值上不同的路径，最多二十条。"""
    if len(out) >= 20:
        return
    if _is_object(base) and _is_object(head) and (base or head):
        keys = [[key for key, _ in base], [key for key, _ in head]]
        if keys[0] != keys[1]:
            out.append({"path": path or ".", "base_keys": keys[0], "head_keys": keys[1]})
        values = dict(head)
        for key, value in base:
            if key in values:
                _order_paths(value, values[key], f"{path}.{key}", out)
    elif isinstance(base, list) and isinstance(head, list) and not _is_object(base) and len(base) == len(head):
        for index, (left, right) in enumerate(zip(base, head)):
            _order_paths(left, right, f"{path}[{index}]", out)
    elif base != head:
        out.append({"path": path or ".", "base_keys": None, "head_keys": None})


def raw_order(base: Path, head: Path, report: Path) -> int:
    """@brief 比较两次重放的原始请求正文（含键序）；0 相同、1 不同。"""
    plans = [json.loads((Path(work) / "plan.json").read_text(encoding="utf-8")) for work in (base, head)]
    if (plans[0]["calls"] != plans[1]["calls"]
            or [item["run_id"] for item in plans[0]["segments"]] != [item["run_id"] for item in plans[1]["segments"]]):
        raise _fail("Replays are of different recordings")
    bodies = [_request_bodies(Path(work)) for work in (base, head)]
    mismatches = []
    for key in sorted(set(bodies[0]) | set(bodies[1]), key=lambda item: (item[0] or 0, item[1])):
        digests = (bodies[0].get(key), bodies[1].get(key))
        if digests[0] != digests[1]:
            paths: list[dict] = []
            _order_paths(_raw_body(Path(base), digests[0]), _raw_body(Path(head), digests[1]), "", paths)
            mismatches.append({"base_sha256": digests[0], "call": key[0], "head_sha256": digests[1],
                               "outcome": key[1], "paths": paths})
    _write_json(Path(report), {"compared": len(set(bodies[0]) | set(bodies[1])), "mismatches": mismatches,
                               "schema_version": 1, "verdict": "different" if mismatches else "equal"})
    print(f"Raw request order: {len(mismatches)} differing calls")
    return 1 if mismatches else 0


def export_source(project: Path, source: Path, cutoff: int, work: Path) -> int:
    """@brief 不碰来源地导出来源某段：只读备份数据库到 WORK，再用被测检出的代码在截止处导出。"""
    project, source, work = Path(project).resolve(), Path(source).resolve(), Path(work).resolve()
    if work.exists():
        raise _fail(f"Work directory {work} already exists")
    copy = work / "source-run"
    copy.mkdir(parents=True)
    reader = _open(source)
    writer = sqlite3.connect(copy / "footprints.sqlite")
    try:
        reader.backup(writer)
    finally:
        writer.close()
        reader.close()
    shutil.copy2(source / "manifest.json", copy / "manifest.json")
    shutil.copytree(source / "assets", copy / "assets")
    manifest = json.loads((copy / "manifest.json").read_text(encoding="utf-8"))
    _, config = _frozen_config(copy, manifest)
    command = [sys.executable, "-P", "-m", "worldmock.cli", "--project-root", str(project), "export", "--run-dir",
               str(copy), "--cutoff", str(cutoff), "--output", str(work / "source-export")]
    result = _child_command(command, project, config["model"]["api_key_env"], work / "export-source.log")
    if result.returncode != 0:
        raise _fail(f"Source export failed; see {work / 'export-source.log'}")
    print(f"Source export made by project code at {_git_state(project)[0]}")
    return 0


def _parser() -> argparse.ArgumentParser:
    """@brief 命令行：run、raw-order、export-source。"""
    parser = argparse.ArgumentParser(description="Replay a recorded run against a project with its own answers")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--project", type=Path, required=True)
    run.add_argument("--source", type=Path, nargs="+", required=True)
    run.add_argument("--work", type=Path, required=True)
    run.add_argument("--segments", type=int)
    run.add_argument("--restart-from")
    order = commands.add_parser("raw-order")
    order.add_argument("--base", type=Path, required=True)
    order.add_argument("--head", type=Path, required=True)
    order.add_argument("--report", type=Path, required=True)
    exported = commands.add_parser("export-source")
    exported.add_argument("--project", type=Path, required=True)
    exported.add_argument("--source", type=Path, required=True)
    exported.add_argument("--cutoff", type=int, required=True)
    exported.add_argument("--work", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """@brief 命令入口；0 零偏差（或相同），1 偏差（或不同），2 错误。"""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "run":
            return _run(arguments)
        if arguments.command == "raw-order":
            return raw_order(arguments.base, arguments.head, arguments.report)
        return export_source(arguments.project, arguments.source, arguments.cutoff, arguments.work)
    except (RecordedResponseError, ComparisonError):
        return 2
    except Exception:
        logger.exception("Recorded-response run failed")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""录音应答运行端到端：真实命令录一条带一次重启的两段来源，再用工具在本机重放它，偏差为零、事实逐项相等；
改动一个系统层字符就在那次调用停下。"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import tomlkit

from tests.data_paths import ENVIRONMENT_ROOT

ROOT = Path(__file__).resolve().parents[3]
#: 夹具 config.toml 的凭据变量与脚本模型认的凭据。
API_KEY = ("WORLDMOCK_TEST_API_KEY", "test-only-key")
#: 每次开始的活动：在家点名「测试闲聊」，单元十分钟，不抽随机数。
START = ("start_main_activity", {"action": "interact", "target": "家", "detail": "测试闲聊"})
#: 一小时里六段闲聊：先开始，之后每十分钟收尾上一段再开始下一段。
DECISIONS = [START]
for _minute in range(0, 50, 10):
    DECISIONS += [("conclude_activity", {"items": [{"activity_name": f"进行测试闲聊（1月5日 08:{_minute:02d} 起）",
                                                    "summary": "聊完了。", "memories": [], "views": []}],
                                         "errands": []}), START]
#: 来源里录下的调用数：零点计划一次加全部决策。
SCRIPTED_CALLS = 1 + len(DECISIONS)


def _completion(name: str, arguments: dict, index: int) -> dict:
    """@brief 完整的聊天补全：唯一工具调用，usage 带缓存字段。"""
    return {"id": f"chatcmpl-{index}", "object": "chat.completion", "created": 1767571200, "model": "test-model",
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": "", "reasoning_content": "我想一想。",
                "tool_calls": [{"id": f"call-{index}", "type": "function", "function": {
                    "name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110,
                      "prompt_tokens_details": {"cached_tokens": 0}, "prompt_cache_hit_tokens": 0,
                      "prompt_cache_miss_tokens": 100}}


class ScriptedModel:
    """@brief 按请求种类回答的本机模型：零点计划交空计划，决策按脚本次序；可拒绝第二次决策请求一次。"""

    def __init__(self):
        """@brief 脚本从头开始。"""
        self.decisions, self.answered, self.refuse_second, self.failures = 0, 0, False, []
        self.lock = threading.Lock()

    def answer(self, body: dict) -> tuple[int, dict]:
        """@brief 一次聊天请求的回答。"""
        names = [tool["function"]["name"] for tool in body.get("tools", [])]
        kind = "plan_day" if "submit_day_plan" in names else "decision" if "start_main_activity" in names else "other"
        with self.lock:
            self.answered += 1
            if kind == "plan_day":
                return 200, _completion("submit_day_plan", {"goals": []}, self.answered)
            if kind != "decision" or self.decisions >= len(DECISIONS):
                self.failures.append(f"Scripted model got an unexpected {kind} request")
                return 400, {"error": {"type": "invalid_request_error", "code": "unexpected", "message": kind}}
            if self.refuse_second and self.decisions == 1:
                self.refuse_second = False
                return 400, {"error": {"type": "invalid_request_error", "code": "refused", "message": "restart"}}
            name, arguments = DECISIONS[self.decisions]
            self.decisions += 1
            return 200, _completion(name, arguments, self.answered)


def _server(model: ScriptedModel) -> ThreadingHTTPServer:
    """@brief 在 127.0.0.1 起脚本模型服务。"""
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, payload):
            raw = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self._send(200, {"object": "list", "data": [{"id": "test-model", "object": "model",
                                                        "context_window": 524288, "max_output_tokens": 32768}]})

        def do_POST(self):
            self._send(*model.answer(json.loads(self.rfile.read(int(self.headers["Content-Length"])))))

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _command(*arguments, timeout: int = 900) -> subprocess.CompletedProcess:
    """@brief 在仓库根起一个子进程（带夹具凭据）。"""
    environment = {**os.environ, API_KEY[0]: API_KEY[1]}
    return subprocess.run([sys.executable, *map(str, arguments)], cwd=ROOT, env=environment, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)


def _events(run: Path) -> list[dict]:
    """@brief 运行库里的全部事件正文。"""
    with sqlite3.connect(run / "footprints.sqlite") as connection:
        rows = connection.execute("SELECT body FROM events ORDER BY event_seq").fetchall()
    connection.close()
    return [json.loads(row[0]) for row in rows]


def _environment(root: Path, port: int) -> Path:
    """@brief 夹具环境：08:00 到 09:00（开始与截止都要整点），模型地址指向脚本模型。"""
    destination = root / "environment"
    shutil.copytree(ENVIRONMENT_ROOT, destination)
    path = destination / "config.toml"
    document = tomlkit.parse(path.read_text(encoding="utf-8"))
    document["stop_time"] = document["start_time"] + timedelta(hours=1)
    document["model"]["base_url"] = f"http://127.0.0.1:{port}/v1"
    document["model"]["metadata_url"] = f"http://127.0.0.1:{port}/v1/models"
    path.write_text(tomlkit.dumps(document), encoding="utf-8", newline="\n")
    return destination


def _assert_restarted_source(root: Path) -> None:
    """@brief 来源真的是一次重启：第一段失败、第二段完成、一个 branch_created、没有协议纠错、调用数是脚本的。"""
    first, second = _events(root / "one"), _events(root / "two")
    assert any(event["event_type"] == "run_failed" for event in first)
    assert json.loads((root / "two" / "status.json").read_text(encoding="utf-8"))["status"] == "completed"
    assert len([event for event in second if event["event_type"] == "environment_event"
                and event["data"]["kind"] == "branch_created"]) == 1
    assert not any("protocol_feedback" in json.dumps(event["data"]) for event in second
                   if event["event_type"] == "model_response")
    assert sum(event["event_type"] == "model_request" for event in second) == SCRIPTED_CALLS


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    """@brief 两段来源：第一段在第二次决策请求被拒后失败，第二段从第一段最后一个检查点恢复并跑完。"""
    root = tmp_path_factory.mktemp("recorded-source")
    model = ScriptedModel()
    server = _server(model)
    try:
        environment = _environment(root, server.server_port)
        model.refuse_second = True
        first = _command("-m", "worldmock.cli", "run", "--environment", environment, "--output", root / "one")
        assert first.returncode != 0, first.stdout + first.stderr
        checkpoint = [event["data"]["checkpoint_id"] for event in _events(root / "one")
                      if event["event_type"] == "checkpoint_created"][-1]
        second = _command("-m", "worldmock.cli", "resume", "--environment", environment, "--parent", root / "one",
                          "--checkpoint", checkpoint, "--output", root / "two")
        assert second.returncode == 0, second.stdout + second.stderr
    finally:
        server.shutdown()
        server.server_close()
    assert model.failures == []
    _assert_restarted_source(root)
    exported = _command("-m", "worldmock.cli", "export", "--run-dir", root / "two", "--output", root / "export")
    assert exported.returncode == 0, exported.stdout + exported.stderr
    return root


def test_recorded_response_run_reproduces_a_restarted_recording(source, tmp_path):
    """@brief 工具重放两段来源：零偏差、调用数相等、一次重启、第二段沿用来源运行身份、导出核验通过；
    与来源的导出逐项比对相等；同一次重放与自己比较原始请求正文也相等。"""
    work = tmp_path / "replay"
    result = _command("scripts/recorded_response_run.py", "run", "--project", ROOT, "--source", source / "one",
                      source / "two", "--work", work)
    report = json.loads((work / "report.json").read_text(encoding="utf-8"))
    assert result.returncode == 0, result.stdout + result.stderr + json.dumps(report["divergences"])[:3000]
    assert report["verdict"] == "zero_divergence" and report["divergences"] == []
    assert report["calls_served"] == SCRIPTED_CALLS and report["restarts_reproduced"] == 1
    source_manifest = json.loads((source / "two" / "manifest.json").read_text(encoding="utf-8"))
    assert report["segments"][1]["run_id"] == source_manifest["run_id"]
    assert report["export"]["check"] == "passed"
    compared = _command("scripts/compare_runs.py", "runs", "--base-run", source / "one", source / "two",
                        "--base-export", source / "export", "--head-run", work / "segments" / "01",
                        work / "segments" / "02", "--head-export", work / "export", "--report", tmp_path / "runs.json")
    assert compared.returncode == 0, compared.stdout + compared.stderr
    ordered = _command("scripts/recorded_response_run.py", "raw-order", "--base", work, "--head", work, "--report",
                       tmp_path / "order.json")
    assert ordered.returncode == 0, ordered.stdout + ordered.stderr


def test_one_changed_system_character_stops_the_run_at_that_call(source, tmp_path):
    """@brief 来源副本里第一条决策请求的系统层改一个字符：工具在第一段的那次调用以 system 层偏差停下。"""
    copy = tmp_path / "source"
    shutil.copytree(source / "one", copy / "one")
    shutil.copytree(source / "two", copy / "two")
    database = copy / "two" / "footprints.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER events_no_update")
        rows = connection.execute("SELECT event_seq, body FROM events ORDER BY event_seq").fetchall()
        seq, body = next((seq, json.loads(body)) for seq, body in rows
                         if json.loads(body)["event_type"] == "model_request"
                         and json.loads(body)["data"].get("purpose") is None)
        content = body["data"]["request"]["messages"][0]["content"]
        body["data"]["request"]["messages"][0]["content"] = content.replace("。", "，", 1)
        connection.execute("UPDATE events SET body=? WHERE event_seq=?",
                           (json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")), seq))
    connection.close()
    work = tmp_path / "replay"
    result = _command("scripts/recorded_response_run.py", "run", "--project", ROOT, "--source", copy / "one",
                      copy / "two", "--work", work)
    report = json.loads((work / "report.json").read_text(encoding="utf-8"))
    assert result.returncode == 1, result.stdout + result.stderr
    divergence = report["divergences"][0]
    assert (divergence["kind"], divergence["segment"], divergence["layer"]) == ("mismatch", 1, "system")
    assert divergence["source_seq"] == seq
    assert divergence["simulated_time"] == "2026-01-05T08:00:00+08:00"

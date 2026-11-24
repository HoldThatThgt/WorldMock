"""录音应答运行：只有逐值相等的请求才回放录音；计划内重启只拒绝一次且不前进；凭据与代理不进子进程。"""

from __future__ import annotations

import email.message
import gzip
import hashlib
import json
import shutil
import sqlite3
import sys
import tomllib
import uuid
from pathlib import Path

import pytest

import scripts.recorded_response_child as child
import scripts.recorded_response_run as tool
from scripts.compare_runs import ComparisonError
from scripts.recorded_response_run import (
    DUMMY_CREDENTIAL,
    Cassette,
    RecordedResponseError,
    expected_body,
    first_difference,
    load_plan,
    rewrite_model_urls,
    write_input,
)

RUN1, RUN2, CHECKPOINT = "1" * 32, "2" * 32, "c" * 32
BASE_URL, METADATA_URL = "https://api.example.invalid/beta", "https://api.example.invalid/models"
T0 = 1767571200
CONFIG = ('schema_version = 3\nseed = 7\n\n[models.reasoner]\nprovider = "openai_compatible"\n'
          f'base_url = "{BASE_URL}"\nrequested_model = "test-model"\napi_key_env = "TEST_MODEL_KEY"\n'
          f'metadata_url = "{METADATA_URL}"\ncontext_window = 262144\n'
          'thinking = true\nmax_output_tokens = 16000\n\n'
          '[model_routing]\naction = "reasoner"\nsearch = "reasoner"\nplan_day = "reasoner"\ndigest = "reasoner"\n')
PERSONA = 'schema_version = 3\nagent_id = 1\nname = "林舟"\n'
CHAT = "/beta/chat/completions"
TOOL_NAMES = ("start_main_activity", "start_side_activity", "report_status", "conclude_activity")
#: 已删除的项目根参数，由片段拼出。
PROJECT_ROOT_FLAG = "--project" + "-root"


def _tool(name: str, description: str = "做一件事。") -> dict:
    """@brief 一个工具定义。"""
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}}}}}


def _request(system: str = "你是测试人物。今天是周一。", persona: str = "林舟", tools: list | None = None) -> dict:
    """@brief 一条录下的决策请求：系统层、两层用户内容、工具与宿主侧字段。"""
    layers = [{"layer": "工具层", "groups": []}, {"layer": "人设层", "name": persona}]
    return {"model": "test-model", "messages": [{"role": "system", "content": system},
                                                {"role": "user", "content": json.dumps(layers, ensure_ascii=False)}],
            "tools": [_tool(name) for name in TOOL_NAMES] if tools is None else tools, "tool_choice": "auto",
            "max_tokens": 1600, "stream": False, "max_retries": 0, "transport_max_retries": 3,
            "extra_body": {"thinking": {"type": "enabled"}}}


def _response(index: int) -> dict:
    """@brief 一份提供商响应。"""
    return {"id": f"chatcmpl-{index}", "choices": [{"message": {"role": "assistant", "content": f"第 {index} 次。"}}]}


def _asset(root: Path, content: bytes, kind: str, path: str | None) -> dict:
    """@brief 写一份冻结素材并返回引用。"""
    digest = hashlib.sha256(content).hexdigest()
    (root / "assets").mkdir(parents=True, exist_ok=True)
    (root / "assets" / digest).write_bytes(content)
    source = {"kind": kind, **({"path": path} if path else {})}
    return {"media_type": "text/plain", "sha256": digest, "size_bytes": len(content), "source": source}


def _manifest(run: Path, run_id: str, parent: str | None) -> dict:
    """@brief 一段的运行清单（冻结配置、人设与地图素材，多模型档身份与元数据）。"""
    assets = {"config.toml": _asset(run, CONFIG.encode(), "environment_input", "config.toml"),
              "personas/persona_1.toml": _asset(run, PERSONA.encode(), "environment_input", "personas/persona_1.toml"),
              "map.json": _asset(run, b'{"nodes":[]}', "environment_map", None)}
    digest = hashlib.sha256(b"prompt").hexdigest()
    return {"run_id": run_id, "parent_run_id": parent, "parent_checkpoint_id": CHECKPOINT if parent else None,
            "source_group_id": RUN1, "agent_ids": [1], "window": {"start": T0, "end": T0 + 3600,
                                                                  "timezone": "Asia/Shanghai"},
            "environment_assets": assets,
            "model_identity": {
                "profiles": {
                    "reasoner": {
                        "provider": "openai_compatible",
                        "base_url": BASE_URL,
                        "requested_model": "test-model",
                        "metadata_url": METADATA_URL,
                        "context_window": 262144,
                        "thinking": True,
                        "max_output_tokens": 16000,
                        "max_retries": 0,
                        "transport_max_retries": 3,
                        "max_react_turns": 8,
                        "reflection_validation_max_retries": 1,
                        "day_plan_validation_max_retries": 1,
                        "effective_max_tokens": 16000,
                        "model_metadata": {
                            "requested_model": "test-model",
                            "context_window": 1048576,
                            "max_output_tokens": 393216,
                            "metadata_url": METADATA_URL,
                        },
                    },
                },
                "routing": {"action": "reasoner", "search": "reasoner",
                            "plan_day": "reasoner", "digest": "reasoner"},
                "prompt_sha256s": {"action": digest, "search": digest},
                "tool_schema_sha256s": {"action": digest, "search": digest},
            }}


def _event(seq: int, event_type: str, run_id: str, **fields) -> dict:
    """@brief 一条事件正文。"""
    return {"event_seq": seq, "event_id": f"{seq:032x}", "event_type": event_type, "run_id": run_id,
            "agent_id": fields.pop("agent_id", 1), "occurred_at": fields.pop("occurred_at", T0),
            "caused_by": fields.pop("caused_by", []), "data": fields.pop("data", {})}


def _calls(start: int, requests: list[dict], first_index: int, run_id: str) -> list[dict]:
    """@brief 从序号 start 起写每次调用的请求与响应事件。"""
    events, seq = [], start
    for offset, request in enumerate(requests):
        index = first_index + offset
        moment = T0 + 60 * index
        events.append(_event(seq, "model_request", run_id, occurred_at=moment, data={"request": request}))
        events.append(_event(seq + 1, "model_response", run_id, occurred_at=moment, caused_by=[f"{seq:032x}"],
                             data={"phase": "response", "request": request, "response": _response(index)}))
        seq += 2
    return events


def _database(run: Path, events: list[dict]) -> None:
    """@brief 写 events 表（store.py 的结构子集）。"""
    run.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(run / "footprints.sqlite") as connection:
        connection.execute("CREATE TABLE events(event_seq INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, "
                           "body TEXT NOT NULL)")
        connection.executemany("INSERT INTO events VALUES(?,?,?)",
                               [(event["event_seq"], event["event_id"], json.dumps(event, ensure_ascii=False))
                                for event in events])
    connection.close()


def _source(root: Path, requests: list[dict], restart_after: int | None = None,
            discarded: dict | None = None) -> list[Path]:
    """@brief 一条来源链：不重启时一段；restart_after 给出时第一段在那次调用后建检查点并丢弃一条请求，第二段恢复。"""
    one = root / "one"
    if restart_after is None:
        _database(one, _calls(1, requests, 1, RUN1))
        (one / "manifest.json").write_text(json.dumps(_manifest(one, RUN1, None)), encoding="utf-8")
        return [one]
    head = _calls(1, requests[:restart_after], 1, RUN1)
    position = len(head) + 1
    head.append(_event(position, "checkpoint_created", RUN1, agent_id=None, data={"checkpoint_id": CHECKPOINT}))
    tail = _calls(position + 1, [discarded or requests[restart_after]], restart_after + 1, RUN1)[:1]
    _database(one, head + tail)
    (one / "manifest.json").write_text(json.dumps(_manifest(one, RUN1, None)), encoding="utf-8")
    two = root / "two"
    branch = _event(position + 1, "environment_event", RUN2, agent_id=None,
                    data={"kind": "branch_created", "parent_checkpoint_id": CHECKPOINT})
    _database(two, head + [branch] + _calls(position + 2, requests[restart_after:], restart_after + 1, RUN2))
    (two / "manifest.json").write_text(json.dumps(_manifest(two, RUN2, RUN1)), encoding="utf-8")
    (two / "restored-checkpoint.json").write_text(json.dumps({"checkpoint_id": CHECKPOINT, "run_id": RUN1,
                                                              "last_event_seq": position}), encoding="utf-8")
    return [one, two]


def _headers(value: str = f"Bearer {DUMMY_CREDENTIAL}", name: str = "Authorization") -> email.message.Message:
    """@brief 请求头。"""
    headers = email.message.Message()
    headers[name] = value
    return headers


def _send(cassette: Cassette, body: dict, headers=None, method: str = "POST", path: str = CHAT) -> tuple[int, dict]:
    """@brief 向录音带发一个请求，返回（状态码, 解析后的正文）。"""
    raw = json.dumps(body, ensure_ascii=False).encode()
    status, payload = cassette.answer(method, path, headers or _headers(), raw)
    return status, json.loads(payload)


def _sent(request: dict) -> dict:
    """@brief LiteLLM 发出的正文：可见字段加 thinking。"""
    return expected_body(request)


@pytest.fixture
def two_calls(tmp_path):
    """@brief 一段两次调用的来源与它的录音带。"""
    requests = [_request(), _request(system="你是测试人物。今天是周二。")]
    plan = load_plan(_source(tmp_path / "source", requests))
    cassette = Cassette(plan, tmp_path / "work")
    yield cassette, requests
    cassette.close()


@pytest.fixture
def restarted(tmp_path):
    """@brief 两段来源：第一次调用后重启，丢弃的请求与重做的第二次调用不同。"""
    requests = [_request(), _request(persona="林舟（重做）"), _request(system="第三次。")]
    discarded = _request(persona="林舟（丢弃）")
    plan = load_plan(_source(tmp_path / "source", requests, restart_after=1, discarded=discarded))
    cassette = Cassette(plan, tmp_path / "work")
    yield cassette, requests, discarded
    cassette.close()


def test_expected_body_carries_exactly_the_visible_fields():
    assert set(expected_body(_request())) == {"model", "messages", "tools", "tool_choice", "max_tokens", "thinking"}
    memory = {key: value for key, value in _request().items() if key not in ("tools", "tool_choice")}
    assert set(expected_body(memory)) == {"model", "messages", "max_tokens", "thinking"}


def test_equal_values_in_another_key_order_match():
    expected = _sent(_request())
    reordered = {key: expected[key] for key in reversed(list(expected))}
    reordered["tools"] = [{key: tool[key] for key in reversed(list(tool))} for tool in expected["tools"]]
    assert first_difference(expected, reordered) is None


def test_one_character_in_the_system_layer_is_a_divergence_named_system():
    received = _sent(_request(system="你是测试人物。今天是周三。"))
    difference = first_difference(_sent(_request()), received)
    assert (difference["layer"], difference["message_index"], difference["fields"]) == ("system", 0, ["messages"])
    lines = difference["diff"].splitlines()
    assert any(line.startswith("-") and "今天是周一" in line for line in lines)
    assert any(line.startswith("+") and "今天是周三" in line for line in lines)


def test_a_changed_user_layer_is_named_by_its_layer():
    difference = first_difference(_sent(_request()), _sent(_request(persona="周舟")))
    assert (difference["layer"], difference["message_index"]) == ("人设层", 1)


def test_a_changed_tool_schema_is_a_divergence_named_tools():
    tools = [_tool(name) for name in TOOL_NAMES]
    tools[3] = _tool(TOOL_NAMES[3], "收尾一件事。")
    difference = first_difference(_sent(_request()), _sent(_request(tools=tools))) or {}
    assert (difference.get("fields"), difference.get("layer")) == (["tools"], "tools:conclude_activity")


def test_an_extra_request_field_is_a_divergence():
    difference = first_difference(_sent(_request()), {**_sent(_request()), "stream": True}) or {}
    assert difference.get("fields") == ["stream"]


def test_cassette_serves_recorded_responses_in_order(two_calls):
    cassette, requests = two_calls
    for index, request in enumerate(requests, start=1):
        status, payload = cassette.answer("POST", CHAT, _headers(), json.dumps(_sent(request)).encode())
        assert status == 200 and payload == json.dumps(_response(index), ensure_ascii=False).encode()
    assert cassette.next_call == 3 and cassette.served == 2


def test_a_call_after_the_last_is_refused_as_extra(two_calls):
    cassette, requests = two_calls
    for request in requests:
        _send(cassette, _sent(request))
    status, payload = _send(cassette, _sent(requests[1]))
    assert status == 400 and payload["error"]["code"] == "extra_call"
    assert [item["kind"] for item in cassette.divergences] == ["extra_call"]


def test_planned_restart_refuses_once_without_advancing(tmp_path):
    requests = [_request(), _request(persona="林舟（第二次）")]
    cassette = Cassette(load_plan(_source(tmp_path / "source", requests, restart_after=1)), tmp_path / "work")
    assert _send(cassette, _sent(requests[0]))[0] == 200
    status, payload = _send(cassette, _sent(requests[1]))
    assert status == 400 and payload["error"]["code"] == "planned_restart"
    assert cassette.next_call == 2 and cassette.refused == {1} and cassette.divergences == []
    cassette.begin_segment(2)
    assert _send(cassette, _sent(requests[1]))[0] == 200 and cassette.next_call == 3
    cassette.close()


def test_a_second_request_after_the_planned_refusal_is_a_divergence(restarted):
    cassette, requests, discarded = restarted
    _send(cassette, _sent(requests[0]))
    assert _send(cassette, _sent(discarded))[1]["error"]["code"] == "planned_restart"
    status, _ = _send(cassette, _sent(discarded))
    assert status == 400 and [item["kind"] for item in cassette.divergences] == ["after_refusal"]


def test_requests_after_a_divergence_add_no_record(two_calls):
    cassette, requests = two_calls
    _send(cassette, _sent(requests[1]))
    for _ in range(2):
        status, payload = _send(cassette, _sent(requests[0]))
        assert status == 400 and payload["error"]["code"] == "after_divergence"
    assert [item["kind"] for item in cassette.divergences] == ["mismatch"]
    lines = (cassette.work / "server" / "requests.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["outcome"] for line in lines] == ["divergence"] * 3


def test_a_cassette_failure_is_answered_with_400_and_an_error_verdict(two_calls, monkeypatch, caplog):
    cassette, requests = two_calls

    def broken(self, index):
        raise KeyError(index)

    monkeypatch.setattr(Cassette, "_recorded_request", broken)
    status, payload = _send(cassette, _sent(requests[0]))
    assert status == 400 and payload["error"]["code"] == "cassette_error"
    assert [item["kind"] for item in cassette.divergences] == ["cassette_error"]
    assert cassette.error is not None and "Recorded-response cassette failed" in caplog.text
    assert tool.judge_segment(cassette.plan, cassette, 1, 1)["verdict"] == "error"


def test_the_credential_header_is_read_without_regard_to_case(two_calls):
    cassette, requests = two_calls
    status, _ = _send(cassette, _sent(requests[0]), _headers(name="authorization"))
    assert status == 200


def test_pre_restart_call_is_matched_against_the_discarded_request(restarted, tmp_path):
    cassette, requests, discarded = restarted
    _send(cassette, _sent(requests[0]))
    assert _send(cassette, _sent(discarded))[1]["error"]["code"] == "planned_restart"
    again = Cassette(cassette.plan, tmp_path / "again")
    _send(again, _sent(requests[0]))
    status, _ = _send(again, _sent(requests[1]))
    assert status == 400 and [item["kind"] for item in again.divergences] == ["pre_restart_mismatch"]
    assert again.divergences[0]["source_seq"] == cassette.plan.boundaries[0].discarded_seq
    again.close()


def test_first_call_after_a_restart_reports_whether_it_equals_the_discarded_request(restarted):
    cassette, requests, discarded = restarted
    _send(cassette, _sent(requests[0]))
    _send(cassette, _sent(discarded))
    cassette.begin_segment(2)
    status, _ = _send(cassette, _sent(discarded))
    assert status == 400
    assert cassette.divergences[0]["kind"] == "mismatch"
    assert cassette.divergences[0]["matches_discarded_request"] is True


def test_a_body_that_is_not_a_json_object_is_a_mismatch(restarted):
    cassette, requests, discarded = restarted
    _send(cassette, _sent(requests[0]))
    _send(cassette, _sent(discarded))
    cassette.begin_segment(2)
    status, payload = cassette.answer("POST", CHAT, _headers(), b"[1, 2]")
    assert status == 400 and json.loads(payload)["error"]["code"] == "recorded_response_divergence"
    (divergence,) = cassette.divergences
    assert (divergence["kind"], divergence["fields"], divergence["matches_discarded_request"]) == (
        "mismatch", ["body"], False)
    assert cassette.error is None


def test_a_credential_other_than_the_dummy_is_refused_and_not_logged(two_calls, caplog):
    cassette, requests = two_calls
    status, payload = _send(cassette, _sent(requests[0]), _headers("Bearer sk-real"))
    assert status == 400 and payload["error"]["code"] == "credential"
    assert [item["kind"] for item in cassette.divergences] == ["credential"]
    assert "sk-real" not in (cassette.work / "server" / "requests.jsonl").read_text(encoding="utf-8")
    assert "sk-real" not in json.dumps(cassette.divergences) and "sk-real" not in caplog.text


def test_models_endpoint_serves_the_frozen_metadata(two_calls):
    from worldmock.configuration.inputs.models import ModelProfileConfig
    from worldmock.configuration.model.model_metadata import parse_model_metadata

    cassette, _ = two_calls
    status, payload = cassette.answer("GET", "/models", _headers(), b"")
    config = ModelProfileConfig.model_validate(tomllib.loads(rewrite_model_urls(CONFIG, (BASE_URL, METADATA_URL),
                                                                                5555))["models"]["reasoner"])
    metadata = parse_model_metadata(config, json.loads(payload))
    assert status == 200 and (metadata.context_window, metadata.max_output_tokens) == (1048576, 393216)
    assert cassette.models_requests[1] == 1 and cassette.divergences == []


def test_unexpected_path_is_refused(two_calls):
    cassette, requests = two_calls
    status, payload = _send(cassette, _sent(requests[0]), path="/v1/embeddings")
    assert status == 400 and payload["error"]["code"] == "unexpected_path"


def test_input_copy_rewrites_only_the_two_model_urls(two_calls, tmp_path):
    cassette, _ = two_calls
    write_input(cassette.plan, tmp_path / "input", 5555)
    written = sorted(path.relative_to(tmp_path / "input").as_posix()
                     for path in (tmp_path / "input").rglob("*") if path.is_file())
    assert written == ["config.toml", "personas/persona_1.toml"]
    raw = (tmp_path / "input" / "config.toml").read_bytes()
    assert b"\r" not in raw and (tmp_path / "input" / "personas" / "persona_1.toml").read_bytes() == PERSONA.encode()
    changed = [pair for pair in zip(CONFIG.splitlines(), raw.decode().splitlines()) if pair[0] != pair[1]]
    assert [old.split(" = ")[0] for old, _ in changed] == ["base_url", "metadata_url"]
    expected = tomllib.loads(CONFIG)
    expected["models"]["reasoner"].update(base_url="http://127.0.0.1:5555/beta",
                                          metadata_url="http://127.0.0.1:5555/models")
    assert tomllib.loads(raw.decode()) == expected


def test_url_rewrite_refuses_a_missing_url():
    text = CONFIG.replace(f'base_url = "{BASE_URL}"', 'base_url = "https://other.invalid/beta"')
    with pytest.raises(RecordedResponseError, match="at least once"):
        rewrite_model_urls(text, (BASE_URL, METADATA_URL), 5555)


def test_url_rewrite_refuses_a_config_whose_url_is_written_differently():
    text = CONFIG.replace(f'metadata_url = "{METADATA_URL}"', f"metadata_url = '{METADATA_URL}'")
    text += f'other = "{METADATA_URL}"\n'
    with pytest.raises(RecordedResponseError, match="beyond the model URLs"):
        rewrite_model_urls(text, (BASE_URL, METADATA_URL), 5555)


def test_input_copy_refuses_a_changed_frozen_asset(two_calls, tmp_path):
    cassette, _ = two_calls
    reference = cassette.plan.chain.manifests[-1]["environment_assets"]["personas/persona_1.toml"]
    path = cassette.plan.chain.runs[-1] / "assets" / reference["sha256"]
    path.write_bytes(path.read_bytes().replace(b"1", b"2"))
    with pytest.raises(RecordedResponseError, match="personas/persona_1.toml does not match its recorded digest"):
        write_input(cassette.plan, tmp_path / "input", 5555)
    assert not (tmp_path / "input").exists()


def test_frozen_config_must_agree_with_the_manifest_model_identity(tmp_path):
    runs = _source(tmp_path / "source", [_request()])
    manifest = json.loads((runs[0] / "manifest.json").read_text(encoding="utf-8"))
    manifest["model_identity"]["profiles"]["reasoner"]["base_url"] = "https://other.example.invalid/beta"
    (runs[0] / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RecordedResponseError, match="disagrees with the manifest model identity"):
        write_input(load_plan(runs), tmp_path / "input", 5555)


def test_child_environment_drops_credentials_and_proxies(tmp_path):
    proxies = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
    parent = {"DEEPSEEK_NODE_KEYS": "real", "OPENAI_API_KEY": "real", "GH_TOKEN": "real",
              **{name: "proxy" for name in proxies}, **{name.lower(): "proxy" for name in proxies},
              "RAY_ADDRESS": "auto", "PYTHONPATH": "x", "PATH": "bin"}
    environment = tool.child_environment(parent, tmp_path, "DEEPSEEK_NODE_KEYS")
    assert environment["DEEPSEEK_NODE_KEYS"] == DUMMY_CREDENTIAL and "real" not in environment.values()
    assert not {"OPENAI_API_KEY", "GH_TOKEN", *proxies, *(name.lower() for name in proxies)} & set(environment)
    assert environment["RAY_ADDRESS"] == "auto"
    assert (environment["NO_PROXY"], environment["PYTHONUTF8"], environment["LITELLM_LOCAL_MODEL_COST_MAP"]) == (
        "127.0.0.1,localhost", "1", "True")
    root = tmp_path.resolve()
    assert environment["PYTHONPATH"].split(tool.os.pathsep) == [str(root / "src"), str(root)]
    assert environment["PATH"] == "bin"


def test_a_project_without_the_removed_framework_source_is_accepted(tmp_path, monkeypatch, caplog):
    """没有 vendor 目录的检出照常起服务、复原输入并进入逐段回放；日志里没有源码位置的拒绝。"""
    runs = _source(tmp_path / "source", [_request()])
    project, work = tmp_path / "project", tmp_path / "work"
    project.mkdir()
    replayed = []

    def replay(plan, cassette, root, directory, count):
        """@brief 记下回放的检出与段数，不起子进程。"""
        replayed.append((root, directory, count, cassette.next_call))
        return {"calls_served": 0, "divergences": [], "restarts_reproduced": 0, "verdict": "zero_divergence"}

    monkeypatch.setattr(tool, "_replay", replay)
    code = tool.main(["run", "--project", str(project), "--source", *map(str, runs), "--work", str(work)])
    assert code == 0 and not (project / "vendor").exists()
    assert replayed == [(project.resolve(), work.resolve(), 1, 1)]
    assert (work / "input" / "config.toml").is_file() and (work / "input" / "personas" / "persona_1.toml").is_file()
    assert json.loads((work / "plan.json").read_text(encoding="utf-8"))["calls"] == 1
    assert "has no restored" not in caplog.text and "resolves outside the project" not in caplog.text


def test_child_command_lines_name_no_project_root(restarted, tmp_path, monkeypatch):
    """两段的子进程参数与两条导出命令都按新命令行组成，不带项目根参数。"""
    cassette, _, _ = restarted
    plan, work = cassette.plan, cassette.work
    (work / "segments" / "01").mkdir(parents=True)
    shutil.copy2(plan.chain.runs[0] / "footprints.sqlite", work / "segments" / "01" / "footprints.sqlite")
    first = tool._segment_argv(tool.SegmentJob(plan, cassette, tmp_path, work, 1))
    second = tool._segment_argv(tool.SegmentJob(plan, cassette, tmp_path, work, 2))
    assert first == ("run", ["run", "--environment", str(work / "input"),
                             "--output", str(work / "segments" / "01")], None)
    assert second == ("resume", ["resume", "--environment", str(work / "input"), "--parent",
                                 str(work / "segments" / "01"), "--checkpoint", CHECKPOINT, "--output",
                                 str(work / "segments" / "02")], CHECKPOINT)
    commands = []

    def child_command(command, project, api_key_env, log):
        """@brief 记下命令，答一次失败，让调用方在第一条命令后停下。"""
        commands.append(command)
        return tool.subprocess.CompletedProcess(command, 1, "", "")

    monkeypatch.setattr(tool, "_child_command", child_command)
    segments = [{"run_dir": str(work / "segments" / "01")}, {"run_dir": str(work / "segments" / "02")}]
    for count in (2, 1):
        report = {"segments": segments[:count]}
        tool.finish(plan, work, tmp_path, report)
        assert report["verdict"] == "error"
    export = tmp_path / "source-export-work"
    with pytest.raises(RecordedResponseError, match="Source export failed"):
        tool.export_source(tmp_path, plan.chain.runs[0], 3, export)
    export = export.resolve()
    prefix = [sys.executable, "-P", "-m", "worldmock.cli", "export", "--run-dir"]
    assert commands == [
        [*prefix, str(work / "segments" / "02"), "--output", str(work / "export")],
        [*prefix, str(work / "segments" / "01"), "--output", str(work / "export"), "--cutoff", "3"],
        [*prefix, str(export / "source-run"), "--cutoff", "3", "--output", str(export / "source-export")]]
    assert all(PROJECT_ROOT_FLAG not in command for command in [first[1], second[1], *commands])


@pytest.mark.parametrize("case", ["planned", "early", "missing", "failed"])
def test_judge_segment_outcomes(tmp_path, case):
    requests = [_request(), _request(persona="林舟（第二次）")]
    cassette = Cassette(load_plan(_source(tmp_path / "source", requests, restart_after=1)), tmp_path / "work")
    if case == "planned":
        cassette.refused.add(1)
        assert tool.judge_segment(cassette.plan, cassette, 1, 1)["ended_by"] == "planned_restart"
    elif case == "early":
        judged = tool.judge_segment(cassette.plan, cassette, 1, 0)
        assert judged["verdict"] == "divergence"
        assert [item["kind"] for item in cassette.divergences] == ["segment_completed_early"]
    elif case == "missing":
        cassette.next_call = 2
        tool.judge_segment(cassette.plan, cassette, 2, 0)
        assert [(item["kind"], item["call_index"]) for item in cassette.divergences] == [("missing_calls", 2)]
    else:
        judged = tool.judge_segment(cassette.plan, cassette, 1, 1)
        assert judged["verdict"] == "error" and "failed before its planned restart" in judged["error"]
    cassette.close()


def test_chain_plan_from_a_restarted_source(tmp_path):
    requests = [_request(), _request(persona="林舟（重做）"), _request(system="第三次。")]
    discarded = _request(persona="林舟（丢弃）")
    plan = load_plan(_source(tmp_path / "source", requests, restart_after=1, discarded=discarded))
    (boundary,) = plan.boundaries
    assert (boundary.segment, boundary.checkpoint_seq, boundary.first_call, boundary.next_run_id) == (1, 3, 2, RUN2)
    assert [call.segment for call in plan.calls] == [1, 2, 2]
    assert boundary.discarded_request == discarded and boundary.discarded_seq == 4
    assert (plan.api_key_envs, plan.requested_model) == (("TEST_MODEL_KEY",), "test-model")


def test_chain_plan_rejects_a_missing_segment_directory(tmp_path):
    runs = _source(tmp_path / "source", [_request(), _request(persona="二")], restart_after=1)
    with pytest.raises(ComparisonError, match="segment 1"):
        load_plan(runs[1:])


def test_recorded_call_without_provider_response_is_refused(tmp_path):
    run = tmp_path / "source" / "one"
    events = _calls(1, [_request(), _request(persona="二")], 1, RUN1)[:3]
    _database(run, events)
    (run / "manifest.json").write_text(json.dumps(_manifest(run, RUN1, None)), encoding="utf-8")
    with pytest.raises(RecordedResponseError, match="Recorded call 2 at event 3 has no provider response"):
        load_plan([run])


def test_one_shot_uuid_returns_the_run_id_once():
    draw = child.one_shot_uuid(RUN1, "m")
    assert draw() == uuid.UUID(hex=RUN1)
    with pytest.raises(RuntimeError, match="expected one run identity draw in m"):
        draw()


def _replay_work(root: Path, body: bytes) -> Path:
    """@brief 一次重放的工作目录：计划、请求日志与第三次调用的原始正文。"""
    (root / "server" / "bodies").mkdir(parents=True)
    plan = {"calls": 3, "segments": [{"index": 1, "run_id": RUN1}]}
    (root / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    digest = hashlib.sha256(body).hexdigest()
    (root / "server" / "bodies" / f"{digest}.json.gz").write_bytes(gzip.compress(body, mtime=0))
    line = {"arrival": 1, "body_bytes": len(body), "body_sha256": digest, "call": 3, "method": "POST",
            "outcome": "served", "path": CHAT, "segment": 1}
    (root / "server" / "requests.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")
    return root


def test_raw_order_detects_a_key_order_difference(tmp_path):
    request = _sent(_request())
    base = json.dumps(request, ensure_ascii=False).encode()
    function = request["tools"][0]["function"]
    request["tools"][0]["function"] = {key: function[key] for key in reversed(list(function))}
    head = json.dumps(request, ensure_ascii=False).encode()
    code = tool.raw_order(_replay_work(tmp_path / "base", base), _replay_work(tmp_path / "head", head),
                          tmp_path / "order.json")
    report = json.loads((tmp_path / "order.json").read_text(encoding="utf-8"))
    assert code == 1 and [item["call"] for item in report["mismatches"]] == [3]
    assert report["mismatches"][0]["paths"][0]["path"] == ".tools[0].function"

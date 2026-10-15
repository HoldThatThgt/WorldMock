"""录音应答运行：只有逐值相等的请求才回放录音；计划内重启只拒绝一次且不前进；凭据与代理不进子进程。"""

from __future__ import annotations

import email.message
import gzip
import hashlib
import json
import sqlite3
import tomllib
import uuid
from pathlib import Path

import pytest

import scripts.recorded_response_child as child
import scripts.recorded_response_run as tool
from scripts.compare_runs import ComparisonError
from scripts.recorded_response_run import (DUMMY_CREDENTIAL, Cassette, RecordedResponseError, expected_body,
                                           first_difference, load_plan, rewrite_model_urls, write_input)

RUN1, RUN2, CHECKPOINT = "1" * 32, "2" * 32, "c" * 32
BASE_URL, METADATA_URL = "https://api.example.invalid/beta", "https://api.example.invalid/models"
T0 = 1767571200
CONFIG = ('schema_version = 2\nseed = 7\n\n[model]\nprovider = "openai_compatible"\n'
          f'base_url = "{BASE_URL}"\nrequested_model = "test-model"\napi_key_env = "TEST_MODEL_KEY"\n'
          f'metadata_url = "{METADATA_URL}"\ncontext_window = 262144\n')
PERSONA = 'schema_version = 1\nname = "林舟"\n'
CHAT = "/beta/chat/completions"
TOOL_NAMES = ("start_main_activity", "start_side_activity", "report_status", "conclude_activity")


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
    """@brief 一段的运行清单（冻结配置、人设与地图素材，模型身份与元数据）。"""
    assets = {"config.toml": _asset(run, CONFIG.encode(), "environment_input", "config.toml"),
              "persona.toml": _asset(run, PERSONA.encode(), "environment_input", "persona.toml"),
              "map.json": _asset(run, b'{"nodes":[]}', "environment_map", None)}
    return {"run_id": run_id, "parent_run_id": parent, "parent_checkpoint_id": CHECKPOINT if parent else None,
            "source_group_id": RUN1, "agent_ids": [1], "window": {"start": T0, "end": T0 + 3600,
                                                                  "timezone": "Asia/Shanghai"},
            "environment_assets": assets,
            "stage_model_identity": {"base_url": BASE_URL, "metadata_url": METADATA_URL,
                                     "stages": {"action": {"same_second_continuation": True}}},
            "model_metadata": {"requested_model": "test-model", "context_window": 1048576,
                               "max_output_tokens": 393216, "metadata_url": METADATA_URL}}


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


def test_a_credential_other_than_the_dummy_is_refused_and_not_logged(two_calls, caplog):
    cassette, requests = two_calls
    status, payload = _send(cassette, _sent(requests[0]), _headers("Bearer sk-real"))
    assert status == 400 and payload["error"]["code"] == "credential"
    assert [item["kind"] for item in cassette.divergences] == ["credential"]
    assert "sk-real" not in (cassette.work / "server" / "requests.jsonl").read_text(encoding="utf-8")
    assert "sk-real" not in json.dumps(cassette.divergences) and "sk-real" not in caplog.text


def test_models_endpoint_serves_the_frozen_metadata(two_calls):
    from worldmock.input_models import ModelConfig
    from worldmock.model_endpoint import parse_model_metadata

    cassette, _ = two_calls
    status, payload = cassette.answer("GET", "/models", _headers(), b"")
    config = ModelConfig.model_validate(tomllib.loads(rewrite_model_urls(CONFIG, (BASE_URL, METADATA_URL),
                                                                         5555))["model"])
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
    written = sorted(path.relative_to(tmp_path / "input").as_posix() for path in (tmp_path / "input").rglob("*"))
    assert written == ["config.toml", "persona.toml"]
    raw = (tmp_path / "input" / "config.toml").read_bytes()
    assert b"\r" not in raw and (tmp_path / "input" / "persona.toml").read_bytes() == PERSONA.encode()
    changed = [pair for pair in zip(CONFIG.splitlines(), raw.decode().splitlines()) if pair[0] != pair[1]]
    assert [old.split(" = ")[0] for old, _ in changed] == ["base_url", "metadata_url"]
    expected = tomllib.loads(CONFIG)
    expected["model"].update(base_url="http://127.0.0.1:5555/beta", metadata_url="http://127.0.0.1:5555/models")
    assert tomllib.loads(raw.decode()) == expected


def test_url_rewrite_refuses_a_url_that_is_not_unique():
    text = CONFIG + f'# base_url = "{BASE_URL}"\n'
    with pytest.raises(RecordedResponseError, match="exactly once: base_url"):
        rewrite_model_urls(text, (BASE_URL, METADATA_URL), 5555)


def test_url_rewrite_refuses_a_config_whose_url_is_written_differently():
    text = CONFIG.replace(f'metadata_url = "{METADATA_URL}"', f"metadata_url = '{METADATA_URL}'")
    text += f'# previously "{METADATA_URL}"\n'
    with pytest.raises(RecordedResponseError, match="beyond the two model URLs"):
        rewrite_model_urls(text, (BASE_URL, METADATA_URL), 5555)


def test_input_copy_refuses_a_changed_frozen_asset(two_calls, tmp_path):
    cassette, _ = two_calls
    reference = cassette.plan.chain.manifests[-1]["environment_assets"]["persona.toml"]
    path = cassette.plan.chain.runs[-1] / "assets" / reference["sha256"]
    path.write_bytes(path.read_bytes().replace(b"1", b"2"))
    with pytest.raises(RecordedResponseError, match="persona.toml does not match its recorded digest"):
        write_input(cassette.plan, tmp_path / "input", 5555)
    assert not (tmp_path / "input").exists()


def test_frozen_config_must_agree_with_the_manifest_model_identity(tmp_path):
    runs = _source(tmp_path / "source", [_request()])
    manifest = json.loads((runs[0] / "manifest.json").read_text(encoding="utf-8"))
    manifest["stage_model_identity"]["base_url"] = "https://other.example.invalid/beta"
    (runs[0] / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RecordedResponseError, match="disagrees with the manifest model identity"):
        write_input(load_plan(runs), tmp_path / "input", 5555)


def test_child_environment_drops_credentials_and_proxies(tmp_path):
    parent = {"DEEPSEEK_NODE_KEYS": "real", "OPENAI_API_KEY": "real", "GH_TOKEN": "real", "HTTPS_PROXY": "proxy",
              "ray_address": "auto", "PYTHONPATH": "x", "PATH": "bin"}
    environment = tool.child_environment(parent, tmp_path, "DEEPSEEK_NODE_KEYS")
    assert environment["DEEPSEEK_NODE_KEYS"] == DUMMY_CREDENTIAL and "real" not in environment.values()
    assert not {"OPENAI_API_KEY", "GH_TOKEN", "HTTPS_PROXY", "ray_address"} & set(environment)
    assert (environment["NO_PROXY"], environment["PYTHONUTF8"], environment["LITELLM_LOCAL_MODEL_COST_MAP"]) == (
        "127.0.0.1,localhost", "1", "True")
    root = tmp_path.resolve()
    assert environment["PYTHONPATH"].split(tool.os.pathsep) == [
        str(root / "src"), str(root), str(root / "vendor" / "agentsociety2" / "packages" / "agentsociety2")]
    assert environment["PATH"] == "bin"


def test_project_without_restored_as2_is_refused(tmp_path, caplog):
    runs = _source(tmp_path / "source", [_request()])
    code = tool.main(["run", "--project", str(tmp_path / "project"), "--source", *map(str, runs),
                      "--work", str(tmp_path / "work")])
    assert code == 2 and "no restored AS2 source" in caplog.text


def test_as2_location_follows_the_child_pythonpath(tmp_path):
    (tmp_path / "agentsociety2").mkdir()
    (tmp_path / "agentsociety2" / "__init__.py").write_text("", encoding="utf-8")
    environment = {**tool.os.environ, "PYTHONPATH": str(tmp_path)}
    assert tool.as2_location(tmp_path, environment) == [tmp_path / "agentsociety2"]


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
    assert (plan.api_key_env, plan.continuation) == ("TEST_MODEL_KEY", True)


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

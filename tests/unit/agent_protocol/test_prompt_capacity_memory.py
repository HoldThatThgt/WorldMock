"""容量浓缩只改进度层：记忆层的字数由构建器的上限与兜底控制，容量预检从不改它，容量故障日志按层名记下记忆层。"""

import copy
import json

import pytest

from worldmock.agents.prompt import prompt_capacity
from worldmock.agents.prompt.life_prompt import system_message, user_message
from worldmock.agents.prompt.narration import render_layer_json
from worldmock.agents.prompt.prompt_capacity import ProgressBudget, fit_request
from worldmock.agents.prompt.prompt_schemas import USER_LAYER_NAMES
from worldmock.simulation_engine.observation.memory.layout import assemble_layer

METADATA = {"requested_model": "test-model", "metadata_url": "https://service.invalid/v1/models",
            "context_window": 262144, "max_output_tokens": 8000}


def _characters(*, model, messages=None, tools=None, text=None) -> int:
    """确定性的计数：按紧凑 JSON 的字符数计 token。"""
    del model
    return len(text if text is not None else json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False))


def _layers() -> list[dict]:
    """按 USER_LAYER_NAMES 的六层：记忆层「今天」有十条条目，进度层有四条未受保护、不带 self_note 的记录。"""
    today = [{"时刻": f"08:{index:02d}", "类别": "生活", "核验": [f"开始第{index}件事"], "自述": ["正文：" + "记" * 40]}
             for index in range(10)]
    records = [{"occurred_at": f"2026-01-05T09:0{index}:00+08:00", "type": "tool_call", "tool": "report_status",
                "arguments": {"items": [{"activity_name": "测试休息", "summary": "长" * 300}]}} for index in range(4)]
    built = {"人设层": {"layer": "人设层", "identity": {"name": "林舟"}},
             "记忆层": assemble_layer("2026-01-05", {}, today),
             "待办层": {"layer": "待办层", "paused_activities": [], "errands": [], "day_plan": None},
             "工具层": {"layer": "工具层", "targets": [], "callable_index": {"navigation_apps": [], "payment_apps": [],
                                                                  "goods": [], "prefer_apps": []}},
             "环境层": {"layer": "环境层", "observed_at": "2026-01-05T09:05:00+08:00"},
             "进度层": {"layer": "进度层", "session_started_at": "2026-01-05T08:00:00+08:00", "events": records}}
    return [built[name] for name in USER_LAYER_NAMES]


def _request(layers: list[dict]) -> tuple[dict, ProgressBudget]:
    """系统层与六层 user 组成的请求，和进度层四条记录的私有元数据。"""
    request = {"model": "test-model", "max_tokens": 100, "tools": [],
               "messages": [system_message(False), user_message(layers)]}
    meta = tuple({"protected": False, "lines": [f"第{index}条"]} for index in range(4))
    return request, ProgressBudget(tuple(layers), meta)


def test_condensation_rewrites_only_the_progress_layer(monkeypatch):
    """只有浓缩后才放得下：结果的前五层与原来逐值相同，记忆层的渲染文本逐字节相同，进度层四条记录折成一条 condensed。"""
    monkeypatch.setattr(prompt_capacity, "token_counter", _characters)
    layers = _layers()
    request, budget = _request(layers)
    folded = [*layers[:-1], {**layers[-1], "events": [{"occurred_at": "2026-01-05T09:00:00+08:00", "type": "condensed",
                                                      "until_at": "2026-01-05T09:03:00+08:00",
                                                      "lines": ["第0条", "第1条", "第2条", "第3条"]}]}]
    full = prompt_capacity.request_token_count(request)
    small = prompt_capacity.request_token_count({**request, "messages": [request["messages"][0], user_message(folded)]})
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: (full + small) / 2 / 0.85)
    content = fit_request(request, METADATA, budget).request["messages"][1]["content"]
    fitted, sent = json.loads(content), json.loads(request["messages"][1]["content"])
    assert fitted[:-1] == sent[:-1]
    memory = USER_LAYER_NAMES.index("记忆层")
    assert fitted[memory] == layers[memory] and render_layer_json(fitted[memory]) == render_layer_json(layers[memory])
    assert content == user_message(folded)["content"] and fitted[-1] == folded[-1]


def test_capacity_failure_logs_the_memory_layer_by_name(monkeypatch, caplog):
    """浓缩到底也放不下：以容量故障停止，日志的分层用量里有键「记忆层」；记忆层未被改动。"""
    monkeypatch.setattr(prompt_capacity, "token_counter", _characters)
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: 100)
    layers = _layers()
    original = copy.deepcopy(layers)
    request, budget = _request(layers)
    with pytest.raises(RuntimeError, match="^model request capacity exceeded$"):
        fit_request(request, METADATA, budget)
    (message,) = [record.getMessage() for record in caplog.records if "Model request capacity exceeded" in
                  record.getMessage()]
    assert "'记忆层':" in message
    assert layers == original

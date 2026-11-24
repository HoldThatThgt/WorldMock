"""原生运行夹具：真实运行宿主、人物工作区、LifeAgent 与引擎，模型客户端由可替换的工厂给出，元数据服务离线。"""

import copy
import json
import shutil
from datetime import timedelta

import pytest
import tomlkit
from litellm.exceptions import APIConnectionError

from tests.data_paths import ENVIRONMENT_ROOT
from tests.frozen_inputs import fixture_metadata
from tests.integration.runtime.current_contract_fixtures import ToolResponse, metadata_service
from tests.life.conftest import MAIN_TOOL, StubMemoryDigestClient, StubPlanDayClient, main_arguments
from worldmock.configuration.inputs.environment import load_environment
from worldmock.configuration.model import model_client
from worldmock.configuration.model.model_client import REQUEST_KEYS, ModelClient
from worldmock.configuration.model.model_registry import ModelRegistry


def _wakeup_handled(messages: list[dict]) -> tuple[set[str], set[str]]:
    """@brief 提取本次唤醒已经派发过的收尾与暂停活动名，避免沙盒提交前重复点名同一活动。"""
    concluded, paused = set(), set()
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for call in msg.get("tool_calls") or []:
            fn = call.get("function") or {}
            name = fn.get("name")
            if name not in {"conclude_activity", "pause_activity"}:
                continue
            args = json.loads(fn.get("arguments") or "{}")
            names = [item["activity_name"] for item in args.get("items") or ()]
            (concluded if name == "conclude_activity" else paused).update(names)
    return concluded, paused


class CurrentDecisionModel:
    """按世界现状作答的确定性模型客户端替身：先收尾已结束的活动，再播报或让位进行中的活动，否则在家闲聊。"""

    def __init__(self, prepared):
        self.engine, self.requests, self.closed = None, [], 0

    async def call(self, request):
        """记下请求并给出一个工具调用；结合当前唤醒消息流推进至终端开始动作。"""
        self.requests.append(copy.deepcopy(request))
        state = self.engine.context.state
        concluded, paused = _wakeup_handled(request["messages"][2:])
        ended = [item.name for item in state.activities.values()
                 if item.status not in {"active", "paused"} and item.concluded_at is None
                 and item.name not in concluded]
        if ended:
            return ToolResponse("conclude_activity", {"items": [{"activity_name": name, "summary": "聊完了。",
                "memories": [], "views": []} for name in ended], "errands": []}, f"call-{len(self.requests)}")
        active = [item for item in state.activities.values()
                  if item.status == "active" and item.name not in paused]
        if active:
            target = active[0]
            if target.data.get("app_id") is not None or target.planned_end_at - state.current_time >= 600:
                return ToolResponse("pause_activity", {"items": [{"activity_name": target.name, "summary": "先停一下。"}],
                    "errands": []}, f"call-{len(self.requests)}")
            return ToolResponse("start_side_activity", {"action": "interact", "target": ["微博"], "prefer": None},
                                f"call-{len(self.requests)}")
        return ToolResponse(MAIN_TOOL, main_arguments("interact", "家", "测试闲聊"), f"call-{len(self.requests)}")

    async def close(self):
        """记下运行宿主关闭它的次数。"""
        self.closed += 1


#: 夹具为每次运行构造模型客户端的工厂（以 runner 准备好的 prepared 调用）；用例可以 monkeypatch.setattr 换掉它。
MODEL_FACTORY = CurrentDecisionModel


def failing_client(monkeypatch, prepared, failures) -> ModelClient:
    """真实模型客户端，只换掉 litellm 发送：failures 的键是回答调用编号，值是该调用成功前的失败发送次数（失败发送不推进
    回答编号）；每次线路请求去掉网络客户端后深拷贝记在 wire_requests，回答交给同一个确定性模型。"""
    profile = prepared.inputs.config.models[prepared.inputs.config.model_routing.action]
    client = ModelClient(base_url=profile.base_url, api_key="offline", max_retries=profile.max_retries,
                         transport_max_retries=profile.transport_max_retries)
    inner, remaining = CurrentDecisionModel(prepared), dict(failures)
    client.engine, client.wire_requests = None, []

    async def acompletion(**arguments):
        """记下这次线路请求；指定调用的失败次数用完后才交给确定性模型作答。"""
        client.wire_requests.append(copy.deepcopy({key: value for key, value in arguments.items() if key != "client"}))
        call = len(inner.requests) + 1
        if remaining.get(call, 0):
            remaining[call] -= 1
            raise APIConnectionError(message="scripted connection failure", llm_provider="openai", model="test-model")
        inner.engine = client.engine
        return await inner.call({key: arguments[key] for key in REQUEST_KEYS})

    monkeypatch.setattr(model_client.litellm, "acompletion", acompletion)
    return client


@pytest.fixture
def native_runtime(tmp_path, monkeypatch):
    """一小时的真实原生运行环境：返回环境根、冻结输入与每次运行构造出的模型客户端列表。"""
    import worldmock.runtime.lifecycle.midnight as midnight
    import worldmock.runtime.lifecycle.run_host as run_host
    import worldmock.runtime.lifecycle.runner as runner
    from worldmock.simulation_engine.engine import LifeEngine

    root = tmp_path / "environment"
    shutil.copytree(ENVIRONMENT_ROOT, root)
    path = root / "config.toml"
    document = tomlkit.parse(path.read_text(encoding="utf-8"))
    document["stop_time"] = document["start_time"] + timedelta(hours=1)
    path.write_text(tomlkit.dumps(document), encoding="utf-8", newline="\n")
    frozen = load_environment(root, metadata_loader=fixture_metadata)
    metadata_service(monkeypatch, frozen)
    models = []

    def build(prepared):
        """以 prepared 调用当前工厂，记下并交出通过 ModelRegistry 绑定的模型客户端。"""
        model = MODEL_FACTORY(prepared)
        models.append(model)
        return ModelRegistry(prepared.inputs.config, prepared.inputs.model_metadata,
                             client_factory=lambda _profile: model)

    def attach(engine):
        """把刚建立或打开的引擎挂到本次运行的模型客户端上。"""
        models[-1].engine = engine
        return engine

    class _AttachingEngine:
        """真实 LifeEngine 的建立与打开；引擎交给运行宿主之前先挂到模型客户端上。"""

        @staticmethod
        def create(*arguments):
            """调用真实的 LifeEngine.create。"""
            return attach(LifeEngine.create(*arguments))

        @staticmethod
        def open(*arguments):
            """调用真实的 LifeEngine.open。"""
            return attach(LifeEngine.open(*arguments))

    monkeypatch.setattr(runner, "_model_registry", build)
    monkeypatch.setattr(run_host, "LifeEngine", _AttachingEngine)
    monkeypatch.setattr(midnight, "PlanDayClient", lambda client, identity: StubPlanDayClient(identity))
    monkeypatch.setattr(midnight, "MemoryDigestClient", lambda client, identity: StubMemoryDigestClient(identity))
    return root, frozen, models

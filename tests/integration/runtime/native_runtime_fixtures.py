"""原生运行夹具：真实 Society、工作区、LifeAgent 与 LifeEnv，模型由可替换的工厂给出，元数据服务与进程配置离线。"""

import copy
import shutil
from datetime import timedelta
from types import SimpleNamespace

import pytest
import tomlkit

from tests.data_paths import ENVIRONMENT_ROOT
from tests.frozen_inputs import fixture_metadata
from tests.integration.runtime.current_contract_fixtures import (ROOT, ToolResponse, aligned_process,
                                                                 metadata_service)
from tests.life.conftest import MAIN_TOOL, StubPlanDayClient, main_arguments
from worldmock.environment import load_environment


class CurrentDecisionModel:
    """按世界现状作答的确定性模型：先收尾已结束的活动，再播报进行中的活动，否则在家闲聊。"""

    model_name = "test-model"

    def __init__(self, env):
        self.env, self.requests = env, []

    async def call(self, **request):
        """记下请求并给出一个工具调用；调用身份按已答请求计数。"""
        self.requests.append(copy.deepcopy(request))
        state = self.env.engine.context.state
        ended = [item.name for item in state.activities.values()
                 if item.status not in {"active", "paused"} and item.concluded_at is None]
        if ended:
            return ToolResponse("conclude_activity", {"items": [{"activity_name": name, "summary": "聊完了。",
                "memories": [], "views": []} for name in ended], "errands": []}, f"call-{len(self.requests)}")
        active = [item.name for item in state.activities.values() if item.status == "active"]
        if active:
            return ToolResponse("report_status", {"items": [{"activity_name": name, "summary": "继续聊天。"}
                for name in active], "errands": []}, f"call-{len(self.requests)}")
        return ToolResponse(MAIN_TOOL, main_arguments("interact", "家", "测试闲聊"), f"call-{len(self.requests)}")


#: 夹具为每个 LifeEnv 构造模型的工厂（以 env 调用）；用例可以 monkeypatch.setattr 换掉它。
MODEL_FACTORY = CurrentDecisionModel


@pytest.fixture
def native_runtime(tmp_path, monkeypatch):
    """一小时的真实原生运行环境：返回环境根、冻结输入与每次构造出的模型列表。"""
    import worldmock.runtime as runtime
    import agentsociety2.society.society as society_module
    from agentsociety2.agent.runner import _step_agent_batch_async, _create_direct_agents_batch_async
    from agentsociety2.config import llm_dispatcher
    from agentsociety2.society import AgentSociety, AgentSocietyConfig
    from custom.envs.life_env import LifeEnv

    root = tmp_path / "environment"
    shutil.copytree(ENVIRONMENT_ROOT, root)
    path = root / "config.toml"
    document = tomlkit.parse(path.read_text(encoding="utf-8"))
    document["stop_time"] = document["start_time"] + timedelta(hours=1)
    path.write_text(tomlkit.dumps(document), encoding="utf-8", newline="\n")
    frozen = load_environment(root, metadata_loader=fixture_metadata)
    metadata_service(monkeypatch, frozen)
    aligned_process(monkeypatch, frozen)
    models = []

    async def initialize():
        return None

    async def step(request, service):
        return {"results": await _step_agent_batch_async(request, service), "token_stats": {}}

    async def build(prepared):
        specs = runtime._society_specs(prepared)
        env = LifeEnv(**prepared.env_kwargs)
        model = MODEL_FACTORY(env)
        models.append(model)
        service = SimpleNamespace(env=env, llm=SimpleNamespace(default=model), trace=None)
        return None, AgentSociety(AgentSocietyConfig(prepared.run_id, specs, "LifeAgent", env, service,
            prepared.start_time, prepared.inputs.config.stop_time, prepared.run_dir, ROOT, prepared.workspace_mode))

    monkeypatch.setattr(llm_dispatcher, "init_dispatchers", initialize)
    monkeypatch.setattr(runtime, "PlanDayClient", StubPlanDayClient)
    monkeypatch.setattr(runtime, "_build_society", build)
    monkeypatch.setattr(society_module, "create_direct_agents_batch",
                        SimpleNamespace(remote=_create_direct_agents_batch_async))
    monkeypatch.setattr(society_module, "step_agent_batch", SimpleNamespace(remote=step))
    monkeypatch.setenv("WORKSPACE_PATH", str(ROOT))
    return root, frozen, models

"""@brief 画像测试使用真实严格输入与脚本化独立请求。"""

import asyncio
import copy

from tests.data_paths import ENVIRONMENT_ROOT
from worldmock.environment import parse_persona_file, parse_run_config_file
from worldmock.input_models import ModelMetadata
from worldmock.profile.extractor import extract_persona


def persona():
    return parse_persona_file(ENVIRONMENT_ROOT / "persona.toml")[0]


def config(**updates):
    model = parse_run_config_file(ENVIRONMENT_ROOT / "config.toml")[0].model
    return model.model_copy(update=updates)


def metadata(model=None, **updates):
    model = model or config()
    return ModelMetadata(**{"requested_model": model.requested_model, "metadata_url": model.metadata_url,
                            "context_window": 524288, "max_output_tokens": 32768, **updates})


class Response:
    def __init__(self, content="{}"):
        self.content = content


class ScriptedClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def call(self, request):
        self.calls.append(copy.deepcopy(request))
        if not self.responses:
            raise AssertionError("ScriptedClient ran out of responses")
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return {"choices": [{"message": {"content": item.content}}]}


def extracted(story, *, n_runs=3, client, model_config=None):
    model = model_config or config()
    return asyncio.run(extract_persona(story, model, metadata(model), n_runs=n_runs, client=client))

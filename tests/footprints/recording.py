"""实际环境运行与严格导出所用的测试输入。"""

from datetime import timedelta

from tests.life.conftest import start_activity
from tests.life.environment_runtime import act, finish_activity, run_to_end
from worldmock.life.models import StartActivityRequest


def short_window(persona, config):
    """一小时完整运行，无需外部模型服务。"""
    persona["initial_location"] = "四季便利"
    config["stop_time"] = config["start_time"] + timedelta(hours=1)


def recorded_engine(engine_factory, *, purchases=True, content=True):
    """通过真实请求得到付款、本人收货和持续内容接触。"""
    engine = engine_factory(mutate=short_window)
    if purchases:
        request = StartActivityRequest(items=[{"action": "purchase", "target": target}
                                              for target in ("柠檬茶", "纸巾")])
        _, result = act(engine, "recorded-basket", request)
        assert result.status == "in_progress", result.error_code
        finish_activity(engine, result, "recorded-basket")
    if content:
        _, result = act(engine, "recorded-content", start_activity("interact", "哔哩哔哩"))
        assert result.status == "in_progress", result.error_code
    run_to_end(engine)
    store = engine.context.store
    snapshot = store.assets.put_json({"completed": True}, {"kind": "test_agent_snapshot"})
    return engine, engine.checkpoint({"agent:1": snapshot})

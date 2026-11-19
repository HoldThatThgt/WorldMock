"""多个人物同时就绪时，运行宿主按引擎给出的就绪顺序逐个决定，全部决定完才提交。

v1.0 的真实引擎只有一个人物，给不出两个就绪人物，所以这里用一个只实现宿主所调方法的记录型假引擎。
"""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from worldmock.runtime.lifecycle import run_host
from worldmock.state.decisions.round import SchedulingState

#: 假引擎的当前时刻与世界版本。
_NOW = datetime(2026, 1, 5, 8, tzinfo=timezone.utc)
_VERSION = 5


class _Engine:
    """只实现运行宿主一步里会调用的方法：第一次取调度状态时人物 2、1 依次就绪，之后没有人就绪也没有下一边界。"""

    def __init__(self, log: list):
        epoch = int(_NOW.timestamp())
        self.log = log
        self.context = SimpleNamespace(state=SimpleNamespace(run_id="run", current_time=epoch, end_time=epoch + 3600),
                                       config=SimpleNamespace(timezone="UTC"), store=SimpleNamespace(assets="assets"))
        self.decisions = SimpleNamespace(round=None)
        self.midnight_rhythm_pending = False
        self.states = 0

    def scheduling_state(self) -> SchedulingState:
        self.states += 1
        return SchedulingState(_NOW, (2, 1) if self.states == 1 else (), None, _VERSION)

    def begin_decision_round(self, contexts) -> None:
        self.log.append(("begin", [item.agent_id for item in contexts], [item.decision_id for item in contexts]))
        self.decisions.round = contexts[0].round_id

    def commit_decision_round(self, round_id: str) -> list:
        self.log.append(("commit", round_id))
        self.decisions.round = None
        return []

    def checkpoint(self, snapshots) -> None:
        self.log.append(("checkpoint", snapshots))


def _ordered_agent(log: list):
    """记下每次决策开始与结束的人物类；决策中途让出一次事件循环，若宿主并发执行，下一个人物会在此时插进来。"""

    class _OrderedAgent:
        def __init__(self, inputs, engine, model_client, memory):
            self.inputs = inputs

        async def decide(self, context) -> None:
            log.append(("decide-start", context.agent_id, self.inputs))
            await asyncio.sleep(0)
            log.append(("decide-end", context.agent_id))

    return _OrderedAgent


async def test_two_ready_agents_decide_in_engine_order_one_after_another(tmp_path, monkeypatch) -> None:
    """引擎给出就绪顺序 (2, 1)：开轮收到的上下文依次为人物 2、1，决策身份以 :order:0、:order:1 结尾；
    第二个人物开始决定时第一个已经返回；两人都决定完才提交。"""
    log: list = []
    snapshots: list = []

    async def settled(engine, client):
        return []

    def snapshot(run_dir, agent_ids, assets):
        snapshots.append((list(agent_ids), assets))
        return {}

    monkeypatch.setattr(run_host, "LifeAgent", _ordered_agent(log))
    monkeypatch.setattr(run_host, "snapshot_workspaces", snapshot)
    monkeypatch.setattr(run_host, "settle_pending_memory_digests", settled)
    monkeypatch.setattr(run_host, "settle_pending_day_plans", settled)
    host = run_host.RunHost(tmp_path, None, object(), {1: "inputs-1", 2: "inputs-2"}, {1: (), 2: ()})
    host.engine = _Engine(log)

    await host.step()

    round_id = f"run:round:{int(_NOW.timestamp())}:{_VERSION}"
    begin, *rest = log
    assert begin == ("begin", [2, 1], [f"{round_id}:agent:2:order:0", f"{round_id}:agent:1:order:1"])
    assert rest == [("decide-start", 2, "inputs-2"), ("decide-end", 2), ("decide-start", 1, "inputs-1"),
                    ("decide-end", 1), ("commit", round_id), ("checkpoint", {})]
    assert snapshots == [([1, 2], "assets")] and host.step_count == 1

"""以脚本化模型经过真实人物循环执行完整双人自然日并从检查点恢复。"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from custom.agents.life_agent import LifeAgent
from custom.envs.life_env import LifeEnv
from tests.integration.test_life_agent_protocol import _ModelResponse, _DecisionStatementResponse
from tests.life.conftest import context
from tests.life.test_scheduling import _document, _write_scenario, _identity
from worldmock.footprints.checkpoint import snapshot_directory
from worldmock.footprints.export import export_run
from worldmock.footprints.experiences import project_verified_experiences
from worldmock.life.engine import LifeEngine
from worldmock.life.models import ActivityStatus
from worldmock.runtime import two_stage_model_identity

_RESTAURANTS = ("菜饭骨头汤", "三十六道金牌煲仔饭")


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch):
    """脚本模型使用固定上下文窗口，不请求外部模型元数据。"""
    from worldmock.life import prompt_capacity

    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1_048_576)


class DayModel:
    """只从当前人物可见合同选脚本行动；模型响应仍经过真实原生协议校验。

    生活决策没有 finish（时间与能量规格第七章「finish 并入 conclude_activity」）：一轮决策恰好登记一个世界请求，
    登记即结束本轮。刚结束的活动当秒用 conclude_activity 收尾（第七章「活动一结束就要结束它」），唯一一条主观记忆
    随第一次收尾的 memories 写入。出发前先处理手上的事：午饭前暂停等待（暂停的活动留在家里占一个名额，回家后在
    开始地点接着等），晚饭前直接结束它，两个名额都空着时边走边看。
    """

    model_name = "deepseek-flash"

    def __init__(self, seed: int, agent_id: int):
        self.seed, self.agent_id = seed, agent_id
        self.phase = "trip"
        self.meals = 0
        self.morning_sleep_started = False
        self.agent = None
        self.invalid_action_sent = False
        self.invalid_parallel_sent = False
        self.subjective_written = False
        self.requests = 0
        self.actions = []

    async def call(self, **request):
        self.requests += 1
        if "tools" not in request:
            return self._text(request)
        functions = {item["function"]["name"]: item["function"] for item in request["tools"]}
        if not self.invalid_action_sent:
            self.invalid_action_sent = True
            return self._response("invented_wait_tool", {})
        if self.phase == "trip" and "start_activity" in functions and not self.invalid_parallel_sent:
            self.invalid_parallel_sent = True
            watch = self._watch()
            way = self._way(watch, "watch")
            return self._response(*self._start((watch, "interact", way), (watch, "interact", way)))
        action, payload = self._choose(functions)
        self.actions.append((action, payload))
        return self._response(action, payload)

    def _response(self, action, payload):
        return _ModelResponse(f"call-{self.agent_id}-{self.requests}", action,
                              json.dumps(payload, ensure_ascii=False))

    def _text(self, request):
        user = request["messages"][-1]["content"]
        if "memory_reflection_input" in user:
            payload = json.loads(user.split("<memory_reflection_input>")[1].split("</memory_reflection_input>")[0])
            response = [{"insight": "我希望继续留意自己的感受。", "refs": [payload["episodes"][0]["id"]]}]
            return _DecisionStatementResponse(json.dumps(response, ensure_ascii=False))
        if "current_subjective_summary" in user:
            return _DecisionStatementResponse("我希望把想法与实际经历分开。")
        return _DecisionStatementResponse("我按眼前的情况决定接下来做什么。")

    def _choose(self, functions):
        now = self.agent.require_decision_context().observed_at
        hour = now.hour + now.minute / 60 + now.second / 3600
        if "plan_day" in functions:
            return "plan_day", {"goals": [{"intent_kind": "用餐", "goal": "找一份餐食"},
                                            {"intent_kind": "娱乐", "goal": "听一会访谈"}]}
        pending = self._contracts()["pending_names"]
        if pending:
            return self._conclude(pending)
        if hour < 11:
            return self._before_lunch(hour)
        return getattr(self, "_" + self.phase)(functions, hour)

    def _contracts(self):
        return self.agent._life_action_contracts

    def _running(self):
        return self._contracts()["pausable_names"]

    def _objects(self):
        """本轮冻结的交互对象层：可读名称 → （动词，方式，内部标准动作）。"""
        return self.agent._life_visible_objects

    def _way(self, name, canonical):
        """对象列出的、映射为给定内部动作的方式短语（内容按进度是「接着看」「从头再看」等）。"""
        return next(way for _, way, action in self._objects()[name] if action == canonical)

    @staticmethod
    def _start(*items):
        """由（对象，动词，方式）逐项构造 start_activity 参数。"""
        return "start_activity", {"targets": [item[0] for item in items], "actions": [item[1] for item in items],
                                  "ways": [item[2] for item in items]}

    def _watch(self):
        return next(name for name in self._objects() if "Space Habitat" in name)

    def _content(self):
        return next(name for name, actions in self._objects().items()
                    if name.startswith("内容：") and any(action == "watch" for _, _, action in actions))

    def _place(self, label):
        return next(name for name in self._objects() if name.startswith("地点：") and label in name)

    def _control(self, action, names):
        return action, {"task": list(names), "summary": ["按当前进展继续。"] * len(names)}

    def _conclude(self, names):
        """收尾点名的活动；第一次收尾顺带写下唯一一条主观记忆（来源由系统登记，记忆不带任何身份）。"""
        memories = [[] for _ in names]
        if not self.subjective_written:
            memories[0] = [{"type": "reflection", "importance": 0.2, "keywords": ["猜想"],
                            "text": "我以为自己已经吃过一顿饭。"}]
            self.subjective_written = True
        return "conclude_activity", {"task": list(names), "summary": ["这件事先到这里。"] * len(names),
                                     "memories": memories}

    def _before_lunch(self, hour):
        running = self._running()
        if running:
            return self._control("report_status", running)
        sleep = hour < 8 and not self.morning_sleep_started
        if sleep:
            self.morning_sleep_started = True
        return self._start(("自己", "sleep", "睡觉") if sleep else ("自己", "interact", "等待"))

    def _trip(self, functions, hour):
        running = self._running()
        if running:
            # 消息、饥饿跨档与餐时窗口起止都会在进行中的等待里提供决策（时间与能量规格第一、三章）：出发前要先结束或
            # 暂停手上的事：午饭前暂停等待（留在家里，回来接着等），晚饭前直接结束；二者都是瞬时动作，同一秒再决策出发。
            return self._conclude(running) if self.meals else self._control("pause_activity", running)
        target = self._place(_RESTAURANTS[(self.agent_id + self.seed + self.meals) % len(_RESTAURANTS)])
        self.phase = "walk"
        if self._contracts()["reportable_names"]:
            # 暂停的等待仍占一个名额，只步行前往。
            return self._start((target, "travel", "步行"))
        content = self._content()
        return self._start((target, "travel", "步行"), (content, "interact", self._way(content, "watch")))

    def _walk(self, functions, hour):
        running = self._running()
        if running:
            return self._control("report_status", running)
        self.phase = "consume"
        supply = next(name for name, actions in self._objects().items()
                      if any(way == "到店" for _, way, _ in actions))
        return self._start((supply, "obtain", "到店"))

    def _consume(self, functions, hour):
        self.phase = "eat"
        item = next(name for name, actions in self._objects().items()
                    if name.startswith("持有物：") and any(way == "食用" for _, way, _ in actions))
        return self._start((item, "consume", "食用"))

    def _eat(self, functions, hour):
        running = self._running()
        if running:
            return self._control("report_status", running)
        self.meals += 1
        self.phase = "rest"
        return self._start((self._place("张江居所"), "travel", "步行"))

    def _rest(self, functions, hour):
        if self.meals < 2 and hour >= 18:
            self.phase = "trip"
            return self._trip(functions, hour)
        running = self._running()
        if running:
            return self._control("report_status", running)
        resumable = self._contracts()["resumable_names"]
        if resumable:
            # 回到家，在开始地点恢复出门前暂停的等待（report_status 点名暂停的活动即恢复它）。
            return self._control("report_status", resumable)
        until = 18 if self.meals < 2 else 22
        if hour < until:
            return self._start(("自己", "interact", "等待"))
        return self._start(("自己", "sleep", "睡觉"))


async def _people(environment, root: Path, models: dict, restored: bool = False):
    people = {}
    for agent_id, model in models.items():
        workspace = root / f"agent_{agent_id:04d}"
        if not restored:
            LifeAgent.create(workspace, {"id": agent_id, "name": f"Person {agent_id}", "occupation": "engineer"},
                             {"enable_memory": True, "enable_todo_list": False, "max_react_turns": 16})
        service = SimpleNamespace(env=environment, llm=SimpleNamespace(default=model), trace=None)
        people[agent_id] = await LifeAgent.from_workspace(workspace, service)
        model.agent = people[agent_id]
    return people


@pytest.mark.asyncio
@pytest.mark.parametrize("seed", [17, 54])
async def test_complete_two_person_day_uses_real_tools_memory_and_restoration(tmp_path, seed):
    document = _document("multi-person.json")
    document["random_seed"] = seed
    # 决策不耗时（时间与能量规格第一章）：人物总有进行中活动，等待结束即同秒收尾再开始下一段等待，进行中的活动按播报节律汇报。
    # 结算间隔（也是等待时长）与播报间隔放长到两小时只是运行时长的取舍：按场景原有节奏，两颗种子各要跑约 330–430 秒，
    # 又同在一个分片。它们不给同刻轮数设界：同一秒的轮数上限与无空闲区间由 _assert_day 的不变式断言。
    document["activity_feedback_seconds"] = 7200
    document["progress_report_interval_seconds"] = 7200
    for identity in (1, 2):
        document["messages"].append({
            **document["messages"][0], "message_id": f"walking-check-{identity}",
            "thread_id": f"walking-check-{identity}", "recipient_agent_id": identity,
            "sent_at": "2026-01-05T11:02:00+08:00", "subject": "Arrival reminder",
            "body": "Please send a note when you arrive.", "related_task_id": None})
    scenario_path = _write_scenario(document, tmp_path)
    environment = LifeEnv()
    environment.engine = engine = LifeEngine.create(scenario_path, tmp_path / "run",
                                                    _identity(document, f"day-{seed}"), two_stage_model_identity())
    models = {agent_id: DayModel(seed, agent_id) for agent_id in (1, 2)}
    people_root = tmp_path / "people"
    people = await _people(environment, people_root, models)
    start = engine.state.current_time
    restored = False
    rounds = 0
    try:
        while engine.state.current_time < engine.state.end_time:
            scheduling = engine.scheduling_state()
            if not scheduling.ready_agent_ids:
                assert scheduling.next_event_at is not None
                await environment.step(int(scheduling.next_event_at.timestamp()) - engine.state.current_time,
                                       scheduling.next_event_at)
                continue
            rounds += 1
            assert rounds < 250, (engine.state.current_time, {key: model.phase for key, model in models.items()})
            contexts = [context(engine, f"day-{rounds}", agent_id) for agent_id in scheduling.ready_agent_ids]
            await environment.begin_decision_round(tuple(contexts))
            for decision in contexts:
                person = people[decision.agent_id]
                person.bind_decision_context(decision)
                await person.step(0, decision.observed_at)
                await person.to_workspace(people_root / f"agent_{decision.agent_id:04d}")
            results = await environment.commit_decision_round(contexts[0].round_id)
            assert all(result.error_code is None for result in results), results
            if not restored and engine.state.current_time >= start + 13*3600:
                snapshots = {f"agent:{key}": snapshot_directory(people_root / f"agent_{key:04d}", engine.store.assets)
                             for key in people}
                engine.checkpoint(snapshots)
                engine.close()
                environment.engine = engine = LifeEngine.open(scenario_path, tmp_path / "run")
                people = await _people(environment, people_root, models, restored=True)
                restored = True
        snapshots = {f"agent:{key}": snapshot_directory(people_root / f"agent_{key:04d}", engine.store.assets)
                     for key in people}
        engine.checkpoint(snapshots)
        exported = export_run(engine.store, engine.store.last_event_seq, tmp_path / "export")
        _assert_day(engine, models, start, exported, tmp_path)
        assert restored
    finally:
        engine.close()


def _assert_day(engine, models, start, exported, tmp_path):
    events = engine.store.read_events(engine.store.last_event_seq)
    assert engine.state.current_time - start == 86400
    assert all(model.meals == 2 and model.invalid_parallel_sent for model in models.values())
    assert all(agent.body_settled_at == engine.state.end_time for agent in engine.state.agents.values())
    # 同刻上限（规则 3）：同一人物同一秒的决策轮数不超过上限加三。
    same_second = Counter((event.agent_id, event.occurred_at) for event in events
                          if event.event_type == "decision_started")
    assert max(same_second.values()) <= engine.scenario.max_same_moment_decisions + 3
    paused = 0
    for agent_id in (1, 2):
        body = [event for event in events if event.agent_id == agent_id
                and event.event_type == "environment_event" and event.data.get("kind") == "body_settlement"]
        assert sum(event.data["interval_end"]-event.data["interval_start"] for event in body) == 86400
        assert all(left.data["interval_end"] == right.data["interval_start"] for left, right in zip(body, body[1:]))
        # 时钟只在人人有事时推进（规则 1）：每个结算区间都有进行中活动，没有空闲区间。
        assert all(event.data["activity_ids"] for event in body)
        facts = project_verified_experiences(events, agent_id=agent_id, cutoff_seq=events[-1].event_seq)
        assert {fact["kind"] for fact in facts} >= {"consumed", "arrived", "played"}
        assert sum(fact["interval_quantity"] for fact in facts if fact["kind"] == "consumed") == pytest.approx(2)
        observations = [event for event in events if event.event_type == "observation" and event.agent_id == agent_id]
        for observation in observations:
            assert len(observation.data["verified_experiences"]) <= 5
            assert all(row["event_seq"] < observation.event_seq for row in observation.data["verified_experiences"])
        assert any(len(event.data.get("data", {}).get("activities", [])) == 2 for event in events
                   if event.agent_id == agent_id and event.event_type == "action_result")
        assert all(event.data["agent_id"] == agent_id for event in observations)
        paused += _assert_activity_protocol(engine, events, agent_id)
    assert paused, "a wait paused before departure must be resumed at home"
    updates = [event for event in events if event.event_type == "memory_updated"]
    assert updates and all(row["epistemic_status"] == "subjective" for event in updates
                           for row in event.data["episodes"])
    assert any(event.event_type == "model_response" and event.data.get("error") for event in events)
    assert exported["experiment_completed"]
    assert (tmp_path / "export" / "quality.json").is_file()


def _assert_activity_protocol(engine, events, agent_id):
    """第七章：生活决策没有 finish；唯一主观记忆经 conclude_activity.memories 写入并由收尾写入点对上；
    刚结束的活动都在结束那一秒收尾，暂停的等待回到开始地点后恢复，结束、暂停与恢复逐项留记录。

    :returns: 本人暂停（随后都已恢复）的次数。
    """
    own = [event for event in events if event.agent_id == agent_id]
    offered = [{tool["function"]["name"] for tool in event.data["request"]["tools"]} for event in own
               if event.event_type == "model_request" and event.data["request"].get("tools")]
    assert offered and all("finish" not in names for names in offered)
    written = [event for event in own if event.event_type == "memory_updated"
               and any(row["text"] == "我以为自己已经吃过一顿饭。" for row in event.data["episodes"])]
    assert written and written[0].data["trigger"][0] == "activity_conclusion"
    kinds = Counter(event.data["kind"] for event in own if event.event_type == "environment_event"
                    and event.data.get("kind") in {"activity_concluded", "activity_paused", "activity_resumed"})
    assert kinds["activity_paused"] == kinds["activity_resumed"] and kinds["activity_concluded"] > 2
    for activity in (item for item in engine.state.activities.values() if item.agent_id == agent_id):
        if activity.status == ActivityStatus.TRUNCATED:
            assert activity.concluded_at is None
        elif activity.interruption_reason != "agent_cancelled":
            assert activity.concluded_at == activity.ended_at, activity.name
        else:
            assert activity.concluded_at is not None, activity.name
    return kinds["activity_paused"]

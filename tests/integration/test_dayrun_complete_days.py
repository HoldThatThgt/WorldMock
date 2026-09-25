"""以脚本化模型经过真实人物循环执行完整双人自然日并从检查点恢复。"""

from __future__ import annotations

import json
import os
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
from worldmock.runtime import two_stage_model_identity

_RESTAURANTS = ("菜饭骨头汤", "三十六道金牌煲仔饭")


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch):
    """脚本模型使用固定上下文窗口，不请求外部模型元数据。"""
    from worldmock.life import prompt_capacity

    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1_048_576)


class DayModel:
    """只从当前人物可见合同选脚本行动；模型响应仍经过真实原生协议校验。"""

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
        if "finish" in functions and not any(name in functions for name in self.agent._life_action_models):
            return self._finish()
        if not self.invalid_action_sent:
            self.invalid_action_sent = True
            return self._response("invented_wait_tool", {})
        if self.phase == "trip" and "start_activity" in functions and not self.invalid_parallel_sent:
            self.invalid_parallel_sent = True
            watch = self._watch()
            return self._response("start_activity", {"targets": [watch, watch],
                                                      "actions": ["观看", "观看"]})
        action, payload = self._choose(functions)
        self.actions.append((action, payload))
        return self._response(action, payload)

    def _response(self, action, payload):
        return _ModelResponse(f"call-{self.agent_id}-{self.requests}", action, json.dumps(payload))

    def _text(self, request):
        user = request["messages"][-1]["content"]
        if "memory_reflection_input" in user:
            payload = json.loads(user.split("<memory_reflection_input>")[1].split("</memory_reflection_input>")[0])
            response = [{"insight": "我希望继续留意自己的感受。", "refs": [payload["episodes"][0]["id"]]}]
            return _DecisionStatementResponse(json.dumps(response, ensure_ascii=False))
        if "current_subjective_summary" in user:
            return _DecisionStatementResponse("我希望把想法与实际经历分开。")
        return _DecisionStatementResponse("我按眼前的情况决定接下来做什么。")

    def _finish(self):
        memories = []
        if not self.subjective_written:
            memories = [{"type": "reflection", "epistemic_status": "subjective", "importance": 0.2,
                         "keywords": ["猜想"], "text": "我以为自己已经吃过一顿饭。",
                         "source": "decision_observation", "refs": [self.agent._life_observation_event_id]}]
            self.subjective_written = True
        return self._response("finish", {"memories": memories})

    def _choose(self, functions):
        now = self.agent.require_decision_context().observed_at
        hour = now.hour + now.minute / 60 + now.second / 3600
        if "plan_day" in functions:
            return "plan_day", {"goals": [{"intent_kind": "用餐", "goal": "找一份餐食"},
                                            {"intent_kind": "娱乐", "goal": "听一会访谈"}]}
        if hour < 11:
            return self._before_lunch(hour)
        return getattr(self, "_" + self.phase)(functions, hour)

    def _watch(self):
        return next(name for name in self.agent._life_visible_objects if "Space Habitat" in name)

    def _place(self, label):
        return next(name for name in self.agent._life_visible_objects
                    if name.startswith("地点：") and label in name)

    def _session_action(self, action, names):
        return action, {"task": list(names), "summary": ["按当前进展继续。"] * len(names)}

    def _active(self):
        return self.agent._life_action_contracts["active_task_names"]

    def _reportable(self):
        return self.agent._life_action_contracts["reportable_names"]

    def _currently_doing(self):
        return "你正在" in self.agent._life_prompt_layers["scene"]

    def _before_lunch(self, hour):
        active = self._active()
        if active:
            action = "report_status" if self._currently_doing() else "conclude_activity"
            return self._session_action(action, active[:2])
        sleep = hour < 8 and not self.morning_sleep_started
        if sleep:
            self.morning_sleep_started = True
        return "start_activity", {"targets": ["自己"], "actions": ["睡觉" if sleep else "等待"]}

    def _trip(self, functions, hour):
        active = self._active()
        if active:
            # 出发前先结束仍在进行的等待（被消息唤醒时等待还没到点）；结束是瞬时动作，同一秒再决策出发。
            return self._session_action("conclude_activity", active[:2])
        target = _RESTAURANTS[(self.agent_id + self.seed + self.meals) % len(_RESTAURANTS)]
        self.phase = "pause"
        return "start_activity", {"targets": [self._place(target), self._watch()],
                                  "actions": ["步行前往", "观看"]}

    def _pause(self, functions, hour):
        travel = [name for name in self._active() if "步行前往" in name]
        if travel:
            action = "report_status" if "《travel》" in self.agent._life_prompt_layers["scene"] else "conclude_activity"
            return self._session_action(action, travel)
        self.phase = "purchase"
        return self._session_action("pause_activity", self._active())

    def _purchase(self, functions, hour):
        supply = next(name for name, actions in self.agent._life_visible_objects.items()
                      if "到店购买" in actions)
        self.phase = "consume"
        return "start_activity", {"targets": [supply], "actions": ["到店购买"]}

    def _consume(self, functions, hour):
        self.phase = "resume"
        item = next(name for name, actions in self.agent._life_visible_objects.items()
                    if name.startswith("持有物：") and "食用" in actions)
        return "start_activity", {"targets": [item], "actions": ["食用"]}

    def _resume(self, functions, hour):
        active = self._active()
        if active:
            action = "report_status" if self._currently_doing() else "conclude_activity"
            return self._session_action(action, active)
        self.phase = "finish_meal"
        return self._session_action("report_status", self._reportable())

    def _finish_meal(self, functions, hour):
        active = self._active()
        if active:
            action = "report_status" if self._currently_doing() else "conclude_activity"
            return self._session_action(action, active)
        self.meals += 1
        self.phase = "homeward"
        return self._homeward(functions, hour)

    def _homeward(self, functions, hour):
        self.phase = "rest"
        return "start_activity", {"targets": [self._place("张江居所")], "actions": ["步行前往"]}

    def _rest(self, functions, hour):
        active = self._active()
        if active:
            action = "report_status" if self._currently_doing() else "conclude_activity"
            return self._session_action(action, active[:2])
        if self.meals < 2 and hour >= 18:
            self.phase = "trip"
            return self._trip(functions, hour)
        until = 18 if self.meals < 2 else 22
        if hour < until:
            return "start_activity", {"targets": ["自己"], "actions": ["等待"]}
        return "start_activity", {"targets": ["自己"], "actions": ["睡觉"]}


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
    # 决策不耗时（时间与能量规格第一章）：人物总有进行中活动，等待结束即同秒收尾再开始下一段等待，
    # 会话按播报节律汇报；等待时长取结算间隔，结算间隔与播报间隔都放长到两小时，使整日决策轮数与运行时长保持有界。
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
    for agent_id in (1, 2):
        body = [event for event in events if event.agent_id == agent_id
                and event.event_type == "environment_event" and event.data.get("kind") == "body_settlement"]
        assert sum(event.data["interval_end"]-event.data["interval_start"] for event in body) == 86400
        assert all(left.data["interval_end"] == right.data["interval_start"] for left, right in zip(body, body[1:]))
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
    updates = [event for event in events if event.event_type == "memory_updated"]
    assert updates and all(row["epistemic_status"] == "subjective" for event in updates
                           for row in event.data["episodes"])
    assert any(event.event_type == "model_response" and event.data.get("error") for event in events)
    assert exported["experiment_completed"]
    assert (tmp_path / "export" / "quality.json").is_file()

"""以脚本化模型经过真实人物循环执行完整双人自然日并从检查点恢复。"""

from __future__ import annotations

from worldmock.life.tool_groups import expand_tool_layer

import json
import os
import re
from collections import Counter
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import worldmock.runtime as runtime_module
from agentsociety2.society import SocietyCheckpointRequest
from custom.agents.life_agent import _MEMORY_TOOL_MODELS, LifeAgent, _tool_schema
from worldmock.agents.life_prompt import LIFE_SYSTEM_LAYER
from custom.envs.life_env import LifeEnv
from tests.integration.test_life_agent_protocol import _ModelResponse, _TextResponse
from tests.integration.test_unique_presence_run import _assert_once_each, _assert_readable, _identities, _current
from tests.life.conftest import StubPlanDayClient, context, start_arguments
from tests.life.scenario_fixtures import read_scenario, run_identity, write_scenario
from worldmock.evaluation.behavior_metrics import behavior_metrics_report
from worldmock.footprints import timeline_labels
from worldmock.footprints.checkpoint import snapshot_directory
from worldmock.footprints.export import export_run
from worldmock.footprints.experiences import project_verified_experiences
from worldmock.footprints.validation import validate_export
from worldmock.life.decision_start import is_action_request
from worldmock.life.engine import LifeEngine
from worldmock.life.models import REQUEST_MODELS, ActivityStatus
from worldmock.life.narration import render_layer_json
from worldmock.life.prompt_schemas import USER_LAYER_NAMES, validate_layer
from worldmock.life.scenario import sha256_file
from worldmock.life.world_query import QueryWorldRequest
from worldmock.run_setup import stage_model_identity

#: 零点计划桩生成的当日目标（时间与能量规格第四章：计划由运行时后台任务生成，不是人物工具）。
_DAY_GOALS = [{"intent_kind": "用餐", "goal": "找一份餐食", "remind_time": None, "around_time": None},
              {"intent_kind": "娱乐", "goal": "用应用看一会内容", "remind_time": None, "around_time": None}]
#: 这些种子的就寝反思首次不合合同（自由文本、没有工具调用），经一次校验重试改正（docs/dev/SPEC-as2-integration.md#errands-workday-运行修复入库-就寝反思经严格工具提交 ）：
#: 两人的附回消息逐字相同、分处两条（人物，秒）续接链，观察重复门禁仍须为 1。
_REFLECTION_RETRY_SEEDS = frozenset({20260106})
#: 自由文本应答换来的英文校验错误（附回消息 VALIDATION_ERROR 的原文）。
_ONE_CALL = "response must contain exactly one submit_reflection tool call"


@pytest.fixture(autouse=True)
def _stable_day_baseline(monkeypatch):
    """完整日固定记录身份与壁钟，业务时间仍由真实引擎推进。"""
    from tests.dayrun_baseline import deterministic_day_runtime
    deterministic_day_runtime(monkeypatch)


class _PlanDayClient(StubPlanDayClient):
    """替换 worldmock.runtime.PlanDayClient 的确定性桩：LifeEnv 的检查点与零点推进经它生成当日计划。"""

    def __init__(self, model_identity):
        super().__init__(model_identity, goals=_DAY_GOALS)


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch):
    """脚本模型使用固定上下文窗口，不请求外部模型元数据。"""
    from worldmock.life import prompt_capacity

    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1_048_576)


class DayModel:
    """只从实际请求的六层 user（工具层与待办层）选脚本行动；模型响应仍经过真实原生协议校验。

    生活决策没有 finish（时间与能量规格第七章「finish 并入 conclude_activity」）：一轮决策恰好登记一个世界请求，
    登记即结束本轮。刚结束的活动当秒用 conclude_activity 收尾（第七章「活动一结束就要结束它」），唯一一条主观记忆
    随第一次收尾的 memories 写入。出发前先处理手上的事：午饭前暂停等待（暂停的活动留在家里，不占名额，回家后在
    开始地点接着等），晚饭前直接结束它。路上用真实 app，看过的内容与位置只认引擎结算。
    """

    model_name = "deepseek-flash"

    def __init__(self, seed: int, agent_id: int, delivery_place: str):
        self.seed, self.agent_id = seed, agent_id
        self.phase = "morning"
        self.delivery_place = delivery_place
        self.meals = 0
        self.morning_sleep_started = False
        self.offline_started = False
        self.pushed_started = False
        self.agent = None
        self.invalid_action_sent = False
        self.invalid_parallel_sent = False
        self.subjective_written = False
        self.requests = 0
        self.actions = []
        self.layers = []

    async def call(self, **request):
        self.requests += 1
        functions = {item["function"]["name"]: item["function"] for item in request.get("tools", [])}
        if list(functions) == ["submit_reflection"]:
            return self._reflection(request)
        if not functions:
            return self._text(request)
        self.layers = json.loads(request["messages"][1]["content"])
        self.layers[0] = expand_tool_layer(self.layers[0])
        self.seen = _current(request)
        self.layers[0]["targets"] = list(self.seen["targets"].values())
        self.layers[3]["paused_activities"] = list(self.seen["paused_activities"].values())
        if not self.invalid_action_sent:
            self.invalid_action_sent = True
            return self._response("invented_wait_tool", {})
        if self.phase in {"commute", "trip"} and "start_activity" in functions and not self.invalid_parallel_sent:
            self.invalid_parallel_sent = True
            item = self._action("刷推荐", "抖音", remember_push=False)
            return self._response(*self._start(item, item))
        action, payload = self._choose(functions)
        self.actions.append((action, payload))
        return self._response(action, payload)

    def _response(self, action, payload):
        return _ModelResponse(f"call-{self.agent_id}-{self.requests}", action,
                              json.dumps(payload, ensure_ascii=False))

    def _reflection(self, request):
        """就寝反思：工具唯一为 submit_reflection，以这一个工具调用作答，引用输入里的第一条记忆。种子在
        _REFLECTION_RETRY_SEEDS 中时，每次反思的首个请求（只有原始的 system 与 user 两条消息）先以没有工具调用的
        自由文本作答，带附回消息的校验重试再以工具调用作答。"""
        if self.seed in _REFLECTION_RETRY_SEEDS and len(request["messages"]) == 2:
            return _TextResponse("我希望继续留意自己的感受。")
        user = request["messages"][1]["content"]
        payload = json.loads(user.split("<memory_reflection_input>")[1].split("</memory_reflection_input>")[0])
        insights = [{"insight": "我希望继续留意自己的感受。", "refs": [payload["episodes"][0]["id"]]}]
        return self._response("submit_reflection", {"insights": insights})

    def _text(self, request):
        """不带工具的记忆运行时请求（阈值巩固）以一句纯文本作答。"""
        user = request["messages"][-1]["content"]
        if "current_subjective_summary" in user:
            return _TextResponse("我希望把想法与实际经历分开。")
        return _TextResponse("我按眼前的情况决定接下来做什么。")

    def _choose(self, functions):
        now = self.agent.require_decision_context().observed_at
        hour = now.hour + now.minute / 60 + now.second / 3600
        pending = self._activities("刚结束")
        if pending:
            return self._conclude(pending)
        return getattr(self, "_" + self.phase)(functions, hour)

    def _activities(self, phase):
        """工具层里处于给定阶段（进行中、刚结束）的活动目标名。"""
        return [target["name"] for target in self.layers[0]["targets"]
                if target["kind"] == "activity" and target["phase"] == phase]

    def _running(self):
        return self._activities("进行中")

    def _paused(self, resumable_only=False):
        """待办层的暂停活动名；resumable_only 时只取此处可以恢复（report_status 可用）的。"""
        return [item["name"] for item in self.layers[3]["paused_activities"]
                if not resumable_only or any(tool["tool"] == "report_status" and tool["available"]
                                             for tool in item["tools"])]

    @staticmethod
    def _item(target, way="进行", app=None, payment=None):
        """@brief 每次请求明确给出当前五字段，不由模型桩猜应用或支付。"""
        return {"target": target, "way": way, "app": app, "to": None, "pay_app": payment}

    @staticmethod
    def _start(*items):
        """@brief 当前逐项请求从本次真实公开名称构造。"""
        return "start_activity", {"items": list(items), "answering": None}

    def _action(self, label, app=None, remember_push=True):
        """@brief 选择真正可用的动作与已登记 app，不选系统删除的内容目标。"""
        target = next(item for item in self.layers[0]["targets"] if item["kind"] == "action"
                      and item["name"] == label)
        row = next(item for item in target["actions"] if item["app"] == app and item["available"])
        if remember_push and "有推送" in (row["note"] or ""):
            self.pushed_started = True
        return self._item(target["name"], app=app)

    def _place(self, label):
        return next(item["name"] for item in self.layers[0]["targets"]
                    if item["kind"] == "location" and label in item["name"])

    def _trip_item(self, label):
        return self._item(self._place(label), "步行", "高德地图")

    def _control(self, action, names):
        return action, {"items": [{"activity_name": name, "summary": "按当前进展继续。"} for name in names],
                        "errands": []}

    def _conclude(self, names):
        """@brief 第一项真实收尾写一条明确主观记忆，系统登记其来源。"""
        items = [{"activity_name": name, "summary": "这件事先到这里。", "memories": [], "views": []} for name in names]
        if not self.subjective_written:
            items[0]["memories"] = [{"type": "reflection", "importance": 0.2, "keywords": ["猜想"],
                                     "text": "我以为自己已经吃过一顿饭。"}]
            self.subjective_written = True
        return "conclude_activity", {"items": items, "errands": []}

    def _morning(self, functions, hour):
        running = self._running()
        departure = 8 if self.agent_id == 1 else 11
        if hour >= departure:
            self.phase = "commute" if self.agent_id == 1 else "trip"
            if running:
                return self._control("pause_activity", running)
            return getattr(self, "_" + self.phase)(functions, hour)
        if running:
            return self._control("report_status", running)
        if hour < 8 and not self.morning_sleep_started:
            self.morning_sleep_started = True
            return self._start(self._item("自己", "睡觉"))
        if not self.offline_started:
            self.offline_started = True
            return self._start(self._action("安静休息"))
        return self._start(self._item("自己", "等待"))

    def _commute(self, functions, hour):
        self.phase = "office_arrival"
        return self._start(self._trip_item("公司"), self._action("刷推荐", "抖音", remember_push=False))

    def _office_arrival(self, functions, hour):
        if self._running():
            if self.layers[4]["location"]["session_kind"] == "transit":
                return self._control("report_status", self._running())
            return self._conclude(self._running())
        self.phase = "work"
        return self._start(self._action("办公"))

    def _work(self, functions, hour):
        running = self._running()
        if hour >= 11:
            self.phase = "trip"
            return self._conclude(running)
        return self._control("report_status", running)

    def _trip(self, functions, hour):
        running = self._running()
        if running:
            return self._conclude(running) if self.meals else self._control("pause_activity", running)
        target = "一心面馆" if not self.meals else self.delivery_place
        self.phase = "walk"
        if target == self.layers[4]["location"]["name"]:
            return self._walk(functions, hour)
        return self._start(self._trip_item(target), self._action("刷推荐", "抖音", remember_push=False))

    def _walk(self, functions, hour):
        if self._running():
            if self.layers[4]["location"]["session_kind"] == "transit":
                return self._control("report_status", self._running())
            return self._conclude(self._running())
        self.phase = "delivery_wait" if self.meals else "meal_received"
        app, payment = ("美团", "支付宝") if self.meals else (None, None)
        return self._start(self._item("牛肉面", "购买", app, payment))

    def _delivery_wait(self, functions, hour):
        arrived = any(row["type"] == "delivery" and row["status"] == "delivered"
                      for row in self.layers[5]["events"])
        running = self._running()
        if arrived:
            self.phase = "meal_received"
            if running:
                return self._conclude(running)
            return self._meal_received(functions, hour)
        if running:
            return self._control("report_status", running)
        return self._start(self._item("自己", "等待"))

    def _meal_received(self, functions, hour):
        self.meals += 1
        self.phase = "rest"
        if self.layers[4]["location"]["name"] == "家":
            return self._rest(functions, hour)
        return self._start(self._trip_item("家"))

    def _rest(self, functions, hour):
        if self.meals < 2 and hour >= 18:
            self.phase = "trip"
            return self._trip(functions, hour)
        running = self._running()
        if running:
            return self._control("report_status", running)
        resumable = self._paused(resumable_only=True)
        if resumable:
            return self._control("report_status", resumable)
        until = 18 if self.meals < 2 else 22
        if hour < until:
            if not self.pushed_started:
                target = next(item for item in self.layers[0]["targets"] if item["kind"] == "action"
                              and item["name"] == "刷推荐")
                pushed = next((row for row in target["actions"] if row["available"]
                               and "有推送" in (row["note"] or "")), None)
                if pushed:
                    return self._start(self._action("刷推荐", pushed["app"]))
            return self._start(self._item("自己", "等待"))
        return self._start(self._item("自己", "睡觉"))


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
@pytest.mark.parametrize("seed", [20260105, 20260106])
async def test_complete_two_person_day_uses_real_tools_memory_and_restoration(
        tmp_path, tmp_path_factory, seed, monkeypatch):
    monkeypatch.setattr(runtime_module, "PlanDayClient", _PlanDayClient)
    document = read_scenario("multi-person.json")
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
            "body": "Please send a note when you arrive."})
    for moment in ("09:15", "10:15"):
        document["messages"].append({
            **document["messages"][0], "message_id": f"work-note-{moment}", "thread_id": f"work-note-{moment}",
            "sent_at": f"2026-01-05T{moment}:00+08:00", "subject": "Progress reminder",
            "body": "Please report the actual progress of the work."})
    scenario_path = write_scenario(document, tmp_path)
    scenario_path = scenario_path.rename(scenario_path.with_name("custom.json"))
    environment = LifeEnv()
    environment.engine = engine = LifeEngine.create(
        scenario_path,
        tmp_path / "run",
        run_identity(document, f"day-{seed}"),
        stage_model_identity(),
    )
    catalog = engine.context.semantics.catalog
    farthest = max(catalog.locations, key=lambda name: (catalog.shortest_m("noodle-1", name), name))
    models = {agent_id: DayModel(seed, agent_id, catalog.locations[farthest].name) for agent_id in (1, 2)}
    people_root = tmp_path / "people"
    people = await _people(environment, people_root, models)
    start = engine.context.state.current_time
    restored = False
    rounds = 0
    try:
        # 启动路径（时间与能量规格第四章「执行位置」）：同 AS2 Society.init，初始共同检查点经 LifeEnv.checkpoint
        # 提交之后，零点计划后台任务在首个 run_start 决策之前为每个人物生成当日计划。
        await environment.checkpoint(SocietyCheckpointRequest(
            engine.context.state.run_id, "initial", engine.scheduling_state().current_time,
            tuple((key, str(people_root / f"agent_{key:04d}")) for key in sorted(people))))
        while engine.context.state.current_time < engine.context.state.end_time:
            scheduling = engine.scheduling_state()
            if not scheduling.ready_agent_ids:
                assert scheduling.next_event_at is not None
                await environment.step(int(scheduling.next_event_at.timestamp()) - engine.context.state.current_time,
                                       scheduling.next_event_at)
                continue
            rounds += 1
            assert rounds < 250, (engine.context.state.current_time, {key: model.phase for key, model in models.items()})
            contexts = [context(engine, f"day-{rounds}", agent_id) for agent_id in scheduling.ready_agent_ids]
            await environment.begin_decision_round(tuple(contexts))
            for decision in contexts:
                person = people[decision.agent_id]
                person.bind_decision_context(decision)
                await person.step(0, decision.observed_at)
                await person.to_workspace(people_root / f"agent_{decision.agent_id:04d}")
            results = await environment.commit_decision_round(contexts[0].round_id)
            assert all(result.error_code is None for result in results), results
            if not restored and engine.context.state.current_time >= start + 13*3600:
                snapshots = {f"agent:{key}": snapshot_directory(people_root / f"agent_{key:04d}", engine.context.store.assets)
                             for key in people}
                engine.checkpoint(snapshots)
                engine.close()
                environment.engine = engine = LifeEngine.open(scenario_path, tmp_path / "run")
                people = await _people(environment, people_root, models, restored=True)
                restored = True
        snapshots = {f"agent:{key}": snapshot_directory(people_root / f"agent_{key:04d}", engine.context.store.assets)
                     for key in people}
        engine.checkpoint(snapshots)
        exported = export_run(engine.context.store, engine.context.store.last_event_seq, tmp_path / "export")
        _assert_day(engine, models, start, exported, tmp_path)
        assert restored
        summary = _assert_current_day_artifacts(tmp_path, engine)
        scenario_sha256 = engine.context.store.manifest["scenario_sha256"]
        assert scenario_sha256 == sha256_file(scenario_path)
        candidate = {"scenario_sha256": scenario_sha256, "random_seed": engine.context.scenario.random_seed,
                     "summary": summary}
        destination = tmp_path_factory.getbasetemp() / "dayrun-action-content-candidate.json"
        destination.write_text(json.dumps(candidate, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        frozen = json.loads((Path(__file__).parents[1] / "fixtures/dayrun-action-content-baseline.json").read_text())
        if seed == frozen["random_seed"]:
            assert candidate == {key: frozen[key] for key in ("scenario_sha256", "random_seed", "summary")}
    finally:
        engine.close()


def _assert_current_day_artifacts(case, engine):
    """@brief 验证本次场景、资产和检查点原始哈希，并对真实提交逐字段认证完整导出。"""
    from tests.dayrun_baseline import day_baseline_signature

    project = Path(__file__).resolve().parents[2]
    summary = day_baseline_signature(case, project)
    manifest, events, activities = validate_export(case / "export")
    store = engine.context.store
    assert manifest["frozen_run_manifest"] == store.manifest
    assert manifest["cutoff_event_seq"] == store.last_event_seq
    committed = store.read_events(store.last_event_seq)
    assert events == [event.model_dump(mode="json") for event in committed]
    checkpoint = store.get_checkpoint(manifest["checkpoint"]["checkpoint_id"])
    assert manifest["checkpoint"] == checkpoint.model_dump(mode="json")
    actual = engine.context.state.activities
    assert {activity["activity_id"] for activity in activities} == set(actual)
    for activity in activities:
        state = actual[activity["activity_id"]].model_dump(mode="json")
        projected = {key: activity["activity_type"] if key == "kind" else activity[key] for key in state}
        assert projected == state
    _assert_current_day_timelines(case / "export", store, manifest)
    return summary


def _assert_current_day_timelines(export, store, manifest):
    """@brief 用既有渲染器从真实存储重建逐人逐日时间线，验证全部正文与身份声明。"""
    renderer = timeline_labels._load_render_day()
    declared = {entry["path"]: entry for entry in manifest["timeline"]}
    expected_paths = {f"timeline/{agent_id}-{day}.md" for agent_id in store.manifest["agent_ids"]
                      for day in manifest["daily_coverage"]}
    assert len(declared) == len(manifest["timeline"])
    assert set(declared) == expected_paths
    for agent_id in store.manifest["agent_ids"]:
        for day in manifest["daily_coverage"]:
            path = export / f"timeline/{agent_id}-{day}.md"
            expected = renderer.render_day_markdown(
                store.connection, agent_id, date.fromisoformat(day), store.manifest["window"]["timezone"])
            raw = path.read_bytes()
            assert raw == expected.encode("utf-8")
            entry = declared[path.relative_to(export).as_posix()]
            assert entry["size_bytes"] == len(raw)
            assert entry["sha256"] == sha256_file(path)


def _assert_day(engine, models, start, exported, tmp_path):
    events = engine.context.store.read_events(engine.context.store.last_event_seq)
    assert engine.context.state.current_time - start == 86400
    assert all(model.meals == 2 and model.invalid_parallel_sent and model.offline_started
               and model.pushed_started for model in models.values())
    assert all(agent.body_settled_at == engine.context.state.end_time for agent in engine.context.state.agents.values())
    # 同刻上限（规则 3）：同一人物同一秒的决策轮数不超过上限加三。
    same_second = Counter((event.agent_id, event.occurred_at) for event in events
                          if event.event_type == "decision_started")
    assert max(same_second.values()) <= engine.context.scenario.max_same_moment_decisions + 3
    _assert_start_day_plans(events, start)
    paused = 0
    for agent_id in (1, 2):
        body = [event for event in events if event.agent_id == agent_id
                and event.event_type == "environment_event" and event.data.get("kind") == "body_settlement"]
        assert sum(event.data["interval_end"]-event.data["interval_start"] for event in body) == 86400
        assert all(left.data["interval_end"] == right.data["interval_start"] for left, right in zip(body, body[1:]))
        # 时钟只在人人有事时推进（规则 1）：每个结算区间都有进行中活动，没有空闲区间。
        assert all(event.data["activity_ids"] for event in body)
        facts = project_verified_experiences(events, agent_id=agent_id, cutoff_seq=events[-1].event_seq)
        assert {fact["kind"] for fact in facts} >= {"received", "arrived", "played"}
        assert len([fact for fact in facts if fact["kind"] == "received"]) == 2
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
    quality = json.loads((tmp_path / "export" / "quality.json").read_text(encoding="utf-8"))
    assert quality["schema_version"] == 6
    # 这一天两人都没有记下个人事务：事务指标没有样本，按质量报告的空值约定为 null（docs/VERIFICATION.md#errands-workday-度量与下游-质量报告 ）。
    assert quality["errand_metrics"] is None
    _assert_food_and_delivery(engine, events)
    _assert_app_and_push(engine, events, tmp_path / "export")
    _assert_work_day(engine, events, quality)
    assert [item for item in quality["memory_references"]["problems"]
            if item["code"] == "invalid_observed_result_reference"] == []
    _assert_decision_events(events, _identities(engine))
    retried = {model.seed in _REFLECTION_RETRY_SEEDS for model in models.values()}
    assert len(retried) == 1
    _assert_reflection_requests(events, retried.pop())


def _assert_food_and_delivery(engine, events):
    """@brief 两餐分别由真实现场购买与购物 app 配送收货，无库存和食用活动。"""
    catalog = engine.context.semantics.catalog
    farthest = max(catalog.locations, key=lambda name: (catalog.shortest_m("noodle-1", name), name))
    assert len(engine.context.state.deliveries) == 4
    for agent_id in (1, 2):
        orders = [row for row in engine.context.state.deliveries.values() if row.agent_id == agent_id]
        assert len(orders) == 2 and {row.fulfillment.value for row in orders} == {"pickup", "delivery"}
        assert all(row.goods_id == "beef-noodles" and row.store_id == "noodle-1" and row.quantity == 1
                   and row.unit_price_fen == catalog.goods[row.goods_id].price_fen and row.status == "delivered"
                   for row in orders)
        pickup = next(row for row in orders if row.fulfillment.value == "pickup")
        delivery = next(row for row in orders if row.fulfillment.value == "delivery")
        assert pickup.app_id is None and pickup.destination_id == "noodle-1" and pickup.total_price_fen == 2800
        assert pickup.delivery_fee_fen == 0 and pickup.distance_meters == 0
        assert pickup.expected_at == pickup.created_at == pickup.received_at
        assert delivery.app_id == "meituan" and delivery.payment_app_id == "alipay"
        assert delivery.destination_id == farthest and delivery.delivery_fee_fen == 500
        assert delivery.total_price_fen == 3300
        distance, path = catalog.graph.shortest_paths("noodle-1")[farthest]
        assert delivery.distance_meters == distance and delivery.path == path
        assert delivery.expected_at > delivery.created_at and delivery.received_at == delivery.expected_at
        arrivals = [event for event in events if event.agent_id == agent_id
                    and event.data.get("kind") == "delivery_arrival"
                    and any(row["order_id"] == delivery.order_id for row in event.data["orders"])]
        assert len(arrivals) == 1 and arrivals[0].occurred_at == delivery.expected_at
        assert "inventory_changes" not in arrivals[0].data
        receipts = [event for event in events if event.agent_id == agent_id and event.data.get("kind") == "goods_received"]
        assert len(receipts) == 2 and {event.data["order_id"] for event in receipts} == {row.order_id for row in orders}
        assert all(event.data["goods_id"] == "beef-noodles" and event.data["category"] == "meal" for event in receipts)
        assert all(event.data["provided"] > 0 and event.data["applied"] >= 0 for event in receipts)
        assert "inventory" not in engine.context.state.agents[agent_id].model_dump(mode="json")


def _assert_app_and_push(engine, events, export):
    """@brief 数字足迹只由带 app 的实际动作与导航产生，静默内容接续保留一个活动。"""
    catalog, state = engine.context.semantics.catalog, engine.context.state
    rows = [json.loads(line) for line in (export / "app-usage.jsonl").read_text(encoding="utf-8").splitlines()]
    for agent_id in (1, 2):
        played = [row for row in project_verified_experiences(events, agent_id=agent_id, cutoff_seq=events[-1].event_seq)
                  if row["kind"] == "played"]
        assert played and all(row["position_after_seconds"] > row["position_before_seconds"] for row in played)
        for row in played:
            activity = state.activities[row["activity_id"]]
            assert activity.kind.value == "action" and activity.data["action_id"] in catalog.actions
            assert row["content_id"] in catalog.contents
            if row["app_id"] is not None:
                assert row["app_id"] in catalog.contents[row["content_id"]].app_ids
        uses = [row for row in rows if row["agent_id"] == agent_id and row["fact_kind"] == "app_use"]
        assert uses and all(row["app_id"] in catalog.apps for row in uses)
        assert any(row["agent_id"] == agent_id and row["fact_kind"] == "app_touch" and row["app_id"] == "amap"
                   for row in rows)
        assert any(row["agent_id"] == agent_id and row["fact_kind"] == "push_opened" for row in rows)
        pushes = [event for event in events if event.agent_id == agent_id and event.data.get("kind") == "app_push"
                  and event.data.get("pushed") is not False]
        assert pushes and len({event.data["push_id"] for event in pushes}) == len(pushes)
        for event in pushes:
            assert event.data["app"] == catalog.apps[event.data["app_id"]].name
            assert event.data["action_id"] == catalog.contents[event.data["content_id"]].action_id
            assert event.data["session_id"] in state.sessions and event.data["issued_at"] == event.occurred_at
        sleeps = [row for row in state.activities.values() if row.agent_id == agent_id and row.kind.value == "sleep"]
        assert len(sleeps) == 2 and all(row.elapsed_seconds > 0 for row in sleeps)
        offline = [row for row in state.activities.values() if row.agent_id == agent_id and row.kind.value == "action"
                   and row.data["app_id"] is None]
        assert any(row.data["category"] == "daily_care" and row.elapsed_seconds > 0 for row in offline)
        assert not {row.activity_id for row in offline} & {row["activity_id"] for row in uses}


def _assert_work_day(engine, events, quality):
    """@brief 环境分配内容仅按真实运行秒数计量，人物自述不能触发客观完成。"""
    configs = {row.agent_id: row for row in engine.context.scenario.agents}
    assert configs[1].work_content_ids == ("topic-sales",) and configs[1].work_schedule.workplace_location_id == "office"
    assert configs[2].work_content_ids == () and configs[2].work_schedule is None
    activities = [row for row in engine.context.state.activities.values() if row.kind.value == "action"
                  and row.data["content_id"] == "topic-sales"]
    assert len(activities) == 1
    work = activities[0]
    assert work.agent_id == 1 and work.location_id == "office"
    assert 0 < work.elapsed_seconds < engine.context.semantics.catalog.contents["topic-sales"].duration_seconds
    sources = {event.action_id for event in events if event.event_type == "action_requested"
               and event.data["tool_name"] == "report_status"}
    reports = [event for event in events if event.event_type == "action_result" and event.action_id in sources
               and event.data["status"] == "completed" and work.activity_id in event.data["data"]["activity_ids"]]
    assert len({event.occurred_at for event in reports}) >= 2
    assert len(work.note_history) >= 2 and all(row["note"] == "按当前进展继续。" for row in work.note_history)
    assert not [event for event in events if event.data.get("kind") == "work_content_completed"]
    assert quality["work_topics"] == {"topics": 1, "completed": 0,
        "worked_seconds_by_topic": {"topic-sales": work.elapsed_seconds}, "reports": len(reports)}


def _tool_names(event) -> list[str]:
    """一条模型请求事件实际开放的工具名（没有工具时为空列表）。"""
    return [tool["function"]["name"] for tool in event.data["request"].get("tools") or []]


def _is_decision_request(event) -> bool:
    """人物决策循环发出的请求：不带 purpose 且工具中有 start_activity（含最后一个回合与截断重试）。

    记忆巩固不带工具，就寝反思只带 submit_reflection，都不带 purpose，按工具中没有 start_activity 排除
    （docs/dev/SPEC-as2-integration.md#errands-workday-运行修复入库-就寝反思经严格工具提交 「身份与判定」）。"""
    return (event.event_type == "model_request" and not event.data.get("purpose")
            and "start_activity" in _tool_names(event))


def _assert_reflection_requests(events, retried):
    """规格 1.1：真实日运行里就寝反思经唯一严格工具提交——请求不带 purpose、思考关闭、tool_choice=required，不是
    行动请求、不产生 decision_started；洞见落进带 source=reflection 快照的 memory_updated；含反思的决策，其动作与意图
    窗口的决策提示仍是该决策里工具含 start_activity 的那份请求。retried 时每次反思恰有一次校验重试：两人的附回消息
    逐字相同、分处两条（人物，秒）续接链（_assert_decision_events 已断言观察重复门禁仍为 1）。"""
    reflections = [event for event in events if event.event_type == "model_request"
                   and _tool_names(event) == ["submit_reflection"]]
    assert {event.agent_id for event in reflections} == {1, 2}
    assert set(Counter(event.decision_id for event in reflections).values()) == {2 if retried else 1}
    retries = [event for event in reflections if len(event.data["request"]["messages"]) == 3]
    assert len(retries) == (len(reflections) // 2 if retried else 0)
    if retried:
        [content] = {event.data["request"]["messages"][-1]["content"] for event in retries}
        feedback = json.loads(content)
        assert (feedback["VALIDATION_ERROR"], feedback["PREVIOUS_INVALID_RESPONSE"]) == (_ONE_CALL, [])
        assert len({(event.agent_id, event.occurred_at) for event in retries}) == len(retries) >= 2
    started = Counter(event.decision_id for event in events if event.event_type == "decision_started")
    for event in reflections:
        request = event.data["request"]
        assert request["tool_choice"] == "required" and request["tools"][0]["function"]["strict"] is True
        assert request["extra_body"] == {"thinking": {"type": "disabled"}}
        assert not event.data.get("purpose") and "purpose" not in request
        assert is_action_request(request) is False
        assert started[event.decision_id] == 1
        assert not [item for item in events if item.event_type == "decision_started"
                    and item.decision_id == event.decision_id and item.event_seq > event.event_seq]
    updates = [event for event in events if event.event_type == "memory_updated"
               and any(row.get("source") == "reflection" for row in event.data["episodes"])]
    assert {event.decision_id for event in updates} == {event.decision_id for event in reflections}
    assert all("reflection" in event.data["trigger"] for event in updates)
    for reflection in reflections:
        [action] = [event for event in events if event.event_type == "action_requested"
                    and event.decision_id == reflection.decision_id]
        assert action.event_seq < reflection.event_seq
        prompt = max((event for event in events if _is_decision_request(event)
                      and event.decision_id == reflection.decision_id and event.event_seq < action.event_seq),
                     key=lambda event: event.event_seq)
        assert prompt.event_id != reflection.event_id
        offered = [tool["function"]["name"] for tool in prompt.data["request"]["tools"]]
        assert "start_activity" in offered and "submit_reflection" not in offered

def _assert_decision_events(events, identities):
    """第二章：每次决策的 decision_started 与首个行动请求同批提交且排在它之前，data 为闭集；没有决策自述请求；
    世界请求都带 self_note 键（脚本回复正文为空时为 null）；观察事件不存进度层快照，只存来源引用；第七章：真实请求上
    的观察重复门禁为 1（同一 user 文本只出现在一条（人物，秒）续接链中）；第一章：每个行动请求的系统层、原生工具与六层
    user 见 _assert_action_requests。"""
    by_seq = {event.event_seq: event for event in events}
    for start in (event for event in events if event.event_type == "decision_started"):
        assert set(start.data) == {"round_id", "round_order", "world_state_version", "wake_reasons"}
        request = by_seq[start.event_seq + 1]
        assert (request.event_type, request.decision_id) == ("model_request", start.decision_id)
        assert request.data["request"]["extra_body"] == {"thinking": {"type": "enabled"}}
    requests = [event for event in events if _is_decision_request(event)]
    assert all(json.loads(event.data["request"]["messages"][0]["content"])["layer"] == "系统层" for event in requests)
    assert all(event.data["self_note"] is None for event in events if event.event_type == "action_requested")
    observations = [event for event in events if event.event_type == "observation"]
    assert all("progress_event_ids" in event.data and "progress_layer" not in event.data for event in observations)
    reports = behavior_metrics_report([event.model_dump(mode="json") for event in events])
    gates = [report["gates"]["observation_repetition"] for report in reports.values()]
    assert gates and all(gate["value"] == 1 and gate["pass"] for gate in gates), gates
    _assert_action_requests(requests, identities)


#: 人物配置的每次决策回合上限（_people 创建工作区时写入）；只剩最后一个回合时去掉记忆查询。
_MAX_REACT_TURNS = 16


def _assert_action_requests(requests, identities):
    """第一章「请求组成」与验收台账「七层请求」「系统层与工具」：整次真实日运行（两人、跨检查点恢复）的每个行动请求——
    system 逐字节相同，就是生活决策系统层的紧凑序列化；原生 tools 包含世界工具、任务回看、世界查询与记忆查询（事件按规范
    JSON 存储，键序不保留，按对象比较），本次决策最后一个回合只提供世界工具；链首 user 是按固定
    层序排列的六层 JSON 数组，每一层通过 prompt_schemas 校验。验收台账「唯一出现」：同一批请求（含同秒续接）逐个满足 test_unique_presence_run 的唯一出现
    与人读写法检查（暂停活动进度、任务合同与门店商品目标各恰好一处；没有 _seconds、_fen、身份键、裸数值与内部身份）。"""
    world = {name: model for name, model in REQUEST_MODELS.items() if name != "recall_task_summary"}
    models = {**world, "recall_task_summary": REQUEST_MODELS["recall_task_summary"],
              "query_world": QueryWorldRequest, **_MEMORY_TOOL_MODELS}
    full = [_tool_schema(name, model) for name, model in models.items()]
    assert requests and {event.data["request"]["messages"][0]["content"] for event in requests} == {
        render_layer_json(LIFE_SYSTEM_LAYER)}
    for event in requests:
        request = event.data["request"]
        turn = int(re.search(r":model:(\d{4}):attempt:", event.data["call_id"]).group(1))
        expected = full[:len(world)] if turn == _MAX_REACT_TURNS else full
        assert request["tools"] == expected, turn
        layers = json.loads(request["messages"][1]["content"])
        assert [layer["layer"] for layer in layers] == list(USER_LAYER_NAMES)
        for layer in layers:
            validate_layer(layer)
        _assert_once_each(request)
        _assert_readable(request, identities)


def _assert_start_day_plans(events, start):
    """第四章：启动路径的零点计划在首个决策之前为每人生成一次当日计划（从检查点恢复不重复），首个决策仍带
    run_start；运行终点恰为零点，不再生成。"""
    generated = [event for event in events if event.event_type == "environment_event"
                 and event.data.get("kind") == "day_plan_generated"]
    assert sorted(event.agent_id for event in generated) == [1, 2]
    for event in generated:
        assert event.occurred_at == start
        assert [goal["intent_kind"] for goal in event.data["day_plan"]["goals"]] == ["用餐", "娱乐"]
        first = next(item for item in events
                     if item.event_type == "decision_started" and item.agent_id == event.agent_id)
        assert event.event_seq < first.event_seq and "run_start" in first.data["wake_reasons"]


def _assert_activity_protocol(engine, events, agent_id):
    """第七章：生活决策没有 finish（第四章：也没有 plan_day）；唯一主观记忆经 conclude_activity.memories 写入并由
    收尾写入点对上；刚结束的活动都在结束那一秒收尾，暂停的等待回到开始地点后恢复，结束、暂停与恢复逐项留记录。

    :returns: 本人暂停（随后都已恢复）的次数。
    """
    own = [event for event in events if event.agent_id == agent_id]
    offered = [set(_tool_names(event)) for event in own if _is_decision_request(event)]
    assert offered and all({"finish", "plan_day"}.isdisjoint(names) for names in offered)
    written = [event for event in own if event.event_type == "memory_updated"
               and any(row["text"] == "我以为自己已经吃过一顿饭。" for row in event.data["episodes"])]
    assert written and written[0].data["trigger"][0] == "activity_conclusion"
    kinds = Counter(event.data["kind"] for event in own if event.event_type == "environment_event"
                    and event.data.get("kind") in {"activity_concluded", "activity_paused", "activity_resumed"})
    assert kinds["activity_paused"] == kinds["activity_resumed"] and kinds["activity_concluded"] > 2
    for activity in (item for item in engine.context.state.activities.values() if item.agent_id == agent_id):
        if activity.status == ActivityStatus.TRUNCATED:
            assert activity.concluded_at is None
        elif activity.interruption_reason != "agent_cancelled":
            assert activity.concluded_at == activity.ended_at, activity.name
        else:
            assert activity.concluded_at is not None, activity.name
    return kinds["activity_paused"]

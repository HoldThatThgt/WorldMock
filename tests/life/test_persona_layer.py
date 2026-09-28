"""人设层（docs/dev/SPEC-persona-prompt.md 第一章「人设层」，裁决 #33、#35、#36、#38）。

结构化卡片在新建运行时存进 profile["persona_card_struct"]（不含当下处境）并在恢复时校验；宿主据此渲染身份、作息（刻钟
写对）、习惯、口味与应对，卡片关系与场景联系人按名称精确合并，只来自联系人的项不带亲密度与渠道；看法与偏好只来自成功的
conclude_activity 写下的带 subject 的记忆，同一对象后写的替换先写的，事件里两条都保留；600 汉字上限只约束卡片派生的
字段；只读问答用最近一次生活决策保存的快照；购买本身不写偏好，巩固摘要不进提示。
"""

from __future__ import annotations

import copy
import json
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")

from agentsociety2.agent.base import ReactLoopRequest  # noqa: E402
from agentsociety2.agent.memory_runtime import MemoryRuntimeConfig  # noqa: E402

from custom.agents.life_agent import LifeAgent, _conclusion_memories, _validate_prepared_action  # noqa: E402
from custom.agents.life_memory import WorldMockMemoryRuntime  # noqa: E402
from custom.agents.life_persona import (PROMPT_SNAPSHOT_FILE, card_struct, known_persons, persona_layer,  # noqa: E402
                                        readonly_layers, write_prompt_snapshot)
from custom.agents.life_prompt import empty_memory_layer, readonly_messages  # noqa: E402
from tests.integration.test_life_agent_protocol import _context, _observations  # noqa: E402
from tests.life.conftest import (activity_name, advance_next, advance_until_ready, stage_day_plan,  # noqa: E402
                                 start_activity)
from tests.life.test_memory_layer import (_act, _jump, _memo, _next_morning, _observe,  # noqa: E402
                                          _remote_order, _walk)
from tests.life.test_dayrun_restaurants import _keep_waiting, _stage  # noqa: E402
from tests.profile.conftest import make_card  # noqa: E402
from worldmock.errors import IntegrityError  # noqa: E402
from worldmock.life.activity_control import slot_activities  # noqa: E402
from worldmock.life.models import ConcludeActivityRequest, ConclusionMemory  # noqa: E402
from worldmock.life.narration import RenderBudgetExceededError  # noqa: E402
from worldmock.life.prompt_schemas import validate_layer  # noqa: E402
from worldmock.life.readable import local_date  # noqa: E402
from worldmock.profile.models import Tastes, persona_card_struct  # noqa: E402
from worldmock.profile.textgen import hour_text, render_card  # noqa: E402

_CONTACTS = [{"contact_id": "contact-zhou", "salutation": "老周", "relation": "朋友", "closeness": 20,
              "channels": ["wechat"], "reply_policy": "none"},
             {"contact_id": "contact-shen", "salutation": "沈昊", "relation": "老友", "closeness": 25,
              "channels": ["phone"], "reply_policy": "model"}]
_NO_VIEWS = {"relationships": [], "preferences": []}


def _profile(card=None) -> dict:
    """带两位场景联系人的人物档案；有卡片时另存结构化字段。"""
    profile = {"id": 1, "name": "陈曦", "contacts": copy.deepcopy(_CONTACTS)}
    if card is not None:
        profile["persona_card_struct"] = persona_card_struct(card)
    return profile


def test_card_path_renders_card_fields_and_merges_relationships_by_exact_name() -> None:
    """卡片路径：身份、作息、习惯、口味与应对取自卡片；卡片「老周」与同名联系人合并为一项（带卡片的亲密度与渠道），
    只来自联系人的「沈昊」亲密度为 null、渠道为空；看法写到对应的人；不含当下处境；过人设层 Schema。"""
    card = make_card(chronotype_midsleep=3.25)
    views = {"relationships": [{"person": "老周", "view": "最近很忙"}, {"person": "沈昊", "view": "靠谱"}],
             "preferences": [{"subject": "午饭", "preference": "爱吃面", "noted_on": "2026-01-05"}]}
    layer = persona_layer(_profile(card), "陈曦", views)
    validate_layer(layer)
    assert layer["identity"] == {"name": "陈曦", "age": 32, "occupation": "产品经理",
                                 "living_arrangement": "与伴侣合租两居室"}
    assert layer["rhythm"] == {"wake": "7点到7点半", "leave_home": "8点到8点半", "lunch": "12点到13点",
                               "dinner": "18点半到19点半", "bedtime": "23点到24点", "sleep_midpoint": "3点一刻",
                               "sleep_need": "7.5 小时"}
    assert layer["habits"] == [{"trigger": "下班到家", "response": "公园慢跑半小时", "time_window": "19点到20点"},
                               {"trigger": "睡前", "response": "读几页小说", "time_window": "22点半到23点"}]
    assert layer["tastes"] == {"content_genres": ["悬疑", "纪录片"], "food_preferences": ["川菜", "面食"],
                               "media": ["长视频", "短视频"]}
    assert layer["coping"] == "拼高达模型"
    assert layer["relationships"] == [
        {"person": "母亲", "role": "家人", "closeness": "很亲近", "channels": ["电话", "微信"], "view": None},
        {"person": "老周", "role": "老友", "closeness": "较亲近", "channels": ["微信", "见面"], "view": "最近很忙"},
        {"person": "沈昊", "role": "老友", "closeness": None, "channels": [], "view": "靠谱"}]
    assert layer["preferences"] == views["preferences"]
    text = json.dumps(layer, ensure_ascii=False)
    assert "新版上线收尾" not in text and "父母的体检结果" not in text and "situation" not in text


def test_quarter_hour_times_are_written_as_quarters() -> None:
    """刻钟写对：睡眠中点 3.25 与 3.75 写「3点一刻」「3点三刻」（旧写法只认半点，会写成「3点」）；卡片渲染同一口径。"""
    assert [hour_text(value) for value in (7.0, 7.25, 7.5, 7.75)] == ["7点", "7点一刻", "7点半", "7点三刻"]
    card = make_card(chronotype_midsleep=3.75)
    assert persona_layer(_profile(card), "陈曦", _NO_VIEWS)["rhythm"]["sleep_midpoint"] == "3点三刻"
    assert "睡眠中点约3点三刻" in render_card(card)


def test_no_card_person_keeps_the_name_only_path_and_view_only_people_get_an_item() -> None:
    """没有结构化卡片：只填姓名，作息、口味、应对为 null，习惯为空；关系按场景联系人逐项（亲密度 null、渠道为空）；
    看法对象不在联系人中的已知人物另占一项。"""
    views = {"relationships": [{"person": "李凡", "view": "新同事"}], "preferences": []}
    layer = persona_layer(_profile(), "陈曦", views)
    validate_layer(layer)
    assert layer == {"layer": "人设层",
                     "identity": {"name": "陈曦", "age": None, "occupation": None, "living_arrangement": None},
                     "rhythm": None, "habits": [], "tastes": None, "coping": None,
                     "relationships": [
                         {"person": "老周", "role": "朋友", "closeness": None, "channels": [], "view": None},
                         {"person": "沈昊", "role": "老友", "closeness": None, "channels": [], "view": None},
                         {"person": "李凡", "role": "认识的人", "closeness": None, "channels": [], "view": "新同事"}],
                     "preferences": []}
    assert card_struct(_profile()) is None


def test_six_hundred_hanzi_cap_checks_only_card_derived_fields() -> None:
    """600 汉字上限只约束卡片派生的字段：卡片口味超长时如实报错；人物写下的长看法与联系人不计入。"""
    long_taste = make_card(tastes=Tastes(content_genres=("悬" * 600,), food_preferences=(), media=()))
    with pytest.raises(RenderBudgetExceededError, match="exceed budget"):
        persona_layer(_profile(long_taste), "陈曦", _NO_VIEWS)
    views = {"relationships": [{"person": "沈昊", "view": "靠" * 700}],
             "preferences": [{"subject": "午饭", "preference": "面" * 700, "noted_on": "2026-01-05"}]}
    layer = persona_layer(_profile(make_card()), "陈曦", views)
    assert layer["relationships"][-1]["view"] == "靠" * 700


def test_persona_card_struct_is_saved_on_new_runs_and_validated_on_restore(tmp_path) -> None:
    """新建运行：卡片渲染文本与结构化字段（去掉当下处境）都存进 profile；恢复时结构化字段按 PersonaCardStruct 校验，
    带当下处境或格点不对的字段阻止分支发布；没有卡片的人物不带该键。"""
    from tests.integration.test_host_branch_coverage import (ROOT, SCENARIO, ResumeRequest, _agent_workspace,
                                                             _parent_store, _prepare_resume)
    from worldmock.life.scenario import load_scenario
    from worldmock.runtime import _agent_specs

    scenario, _ = load_scenario(SCENARIO)
    card = make_card()
    profile = _agent_specs(scenario, persona_compiler=lambda agent: card)[0]["profile"]
    assert profile["persona_card"] == render_card(card)
    assert profile["persona_card_struct"] == persona_card_struct(card) and "situation" not in profile[
        "persona_card_struct"]
    assert "persona_card_struct" not in _agent_specs(scenario, persona_compiler=lambda agent: None)[0]["profile"]
    broken = {**persona_card_struct(card), "situation": {"busy_this_week": "赶工", "cares_about": "体检"}}
    agent = {"schema_version": 1, "agent_class": "LifeAgent", "agent_id": 1, "initialized_at": None,
             "profile": {"id": 1, "same_second_continuation": True, "persona_card_struct": broken}}
    parent, checkpoint = _parent_store(tmp_path, workspace_builder=lambda path: _agent_workspace(
        path, agent_document=agent))
    try:
        with pytest.raises(IntegrityError, match="persona card structure is invalid"):
            _prepare_resume(ResumeRequest(ROOT, SCENARIO, tmp_path / "parent", checkpoint.checkpoint_id,
                                          tmp_path / "child"))
    finally:
        parent.close()


def test_subject_is_required_for_relationship_and_preference_and_stripped_before_memory() -> None:
    """subject 必填可空：relationship 与 preference 必须写对象，其他类型为 null；relationship 的对象须是已知人物（卡片关系名、
    联系人称谓或本轮列出的同世界人物）；写入主观记忆前去掉 subject。"""
    with pytest.raises(ValueError, match="subject"):
        ConclusionMemory.model_validate(_memo("preference", "爱吃面"))
    with pytest.raises(ValueError, match="subject"):
        ConclusionMemory.model_validate(_memo("goal", "明天早起", subject="早起"))
    known = known_persons(_profile(make_card()), ["李凡"])
    assert known == {"母亲", "老周", "沈昊", "李凡"}
    names = {"activity_names": ("等待",), "known_persons": known}
    request = ConcludeActivityRequest(task=["等待"], summary=["收尾"], memories=[[
        _memo("relationship", "新认识的", subject="王五"), _memo("preference", "爱吃面", subject="午饭")]])
    assert _validate_prepared_action("conclude_activity", request, names) == (
        "conclude_activity relationship subject is not a known person: 王五")
    fine = ConcludeActivityRequest(task=["等待"], summary=["收尾"], memories=[[
        _memo("relationship", "靠谱", subject="沈昊")]])
    assert _validate_prepared_action("conclude_activity", fine, names) == ""
    written = _conclusion_memories("conclude_activity", fine.model_dump(mode="json"))
    assert written == [{"type": "relationship", "text": "靠谱", "importance": 0.5, "keywords": ["靠谱"],
                        "source": "activity_conclusion", "epistemic_status": "subjective"}]


def test_later_view_replaces_earlier_while_events_keep_both(engine_factory) -> None:
    """同一对象后写的看法替换先写的，事件里两条都保留；preference 按写下的本地日期记 noted_on；被拒请求写下的看法不算；
    这两类记忆不在进度层参数与记忆层中。"""
    engine = engine_factory()
    stage_day_plan(engine)
    friend = engine.scenario.agents[0].contacts[0].salutation
    for index, (view, taste) in enumerate((("靠谱", "爱吃面"), ("最近很忙", None))):
        _, waited = _act(engine, f"v-wait-{index}", "start_activity", start_activity("等待", "自己"))
        assert advance_until_ready(engine)
        memories = [_memo("relationship", view, subject=friend)]
        memories += [_memo("preference", taste, subject="午饭")] if taste else []
        _act(engine, f"v-conclude-{index}", "conclude_activity", ConcludeActivityRequest(
            task=[activity_name(engine, waited.activity_id)], summary=["收尾"], memories=[memories]))
    rejected = _act(engine, "v-rejected", "conclude_activity", ConcludeActivityRequest(
        task=["不存在的活动"], summary=["收尾"], memories=[[_memo("relationship", "骗子", subject=friend)]]))[1]
    assert rejected.status == "rejected"
    observed = _observe(engine, "v-seen")
    day = local_date(engine.state.current_time, engine.scenario.timezone)
    assert observed["persona_views"] == {"relationships": [{"person": friend, "view": "最近很忙"}],
                                         "preferences": [{"subject": "午饭", "preference": "爱吃面", "noted_on": day}]}
    written = [memory["text"] for event in engine.store.read_agent_events("action_requested", 1)
               for group in event.data["request"].get("memories", []) for memory in group]
    assert written == ["靠谱", "爱吃面", "最近很忙", "骗子"]
    _walk(engine, "v-walk")
    road = _observe(engine, "v-road")
    shown = json.dumps([road["memory_layer"], road["progress_layer"]], ensure_ascii=False)
    assert all(text not in shown for text in ("靠谱", "爱吃面", "最近很忙", "骗子"))


def test_first_purchase_writes_no_preference_and_leaves_no_trace_after_delivery_and_midnight(engine_factory) -> None:
    """反例：首次购买后人物只写了「明天还想吃」这类非偏好记忆时 preferences 为空；送达并跨零点后记忆层没有这次购买。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _stage(engine, "market", 1500)
    order = _remote_order(engine, "p-order")
    product = engine.rules.items[order["product_id"]].name
    for step in range(64):
        if engine.state.deliveries[order["order_id"]].status != "pending":
            break
        if engine.scheduling_state().ready_agent_ids:
            _act(engine, f"p-wait-{step}", *_keep_waiting(engine))
        else:
            advance_next(engine)
    names = [activity_name(engine, item.activity_id) for item in slot_activities(engine.state, 1)]
    _act(engine, "p-conclude", "conclude_activity", ConcludeActivityRequest(
        task=names, summary=["吃过了" for _ in names], memories=[[_memo("commitment", "明天还想吃")] for _ in names]))
    walk = _walk(engine, "p-walk")
    assert advance_until_ready(engine)
    _act(engine, "p-arrive", "conclude_activity", ConcludeActivityRequest(
        task=[activity_name(engine, walk.activity_id)], summary=["到了"], memories=[[]]))
    today = _observe(engine, "p-today")
    assert today["persona_views"]["preferences"] == []
    assert product in json.dumps(today["memory_layer"], ensure_ascii=False)
    moment = engine.state.current_time
    _jump(engine, _next_morning(moment))
    stage_day_plan(engine, submitted=True)
    later = _observe(engine, "p-next-day")
    assert later["memory_layer"] == {"layer": "记忆层", "earlier_days": [], "earlier_today": []}
    assert later["persona_views"]["preferences"] == []


def test_consolidation_is_kept_but_its_summary_never_reaches_the_prompt(tmp_path) -> None:
    """阈值巩固保留（摘要照旧写进主观存储），记忆上下文与生活决策、只读问答的请求都不含摘要。"""
    agent = LifeAgent()
    agent._id, agent._name, agent._profile = 7, "Alice", {"id": 7, "name": "Alice", "contacts": []}
    agent._enable_memory = True
    agent._logger = SimpleNamespace(warning=lambda *args: None, error=lambda *args: None)
    agent.trace_span = None
    runtime = WorldMockMemoryRuntime(agent, MemoryRuntimeConfig())
    runtime.ensure_store(tmp_path)
    runtime.store.mark_subjective_consolidated("我最爱吃楼下的牛肉面。", step_count=1, tick=60)
    agent._memory_runtime = runtime
    assert runtime.store.read_subjective_summary() == "我最爱吃楼下的牛肉面。"
    assert "牛肉面" not in json.dumps(runtime.build_context(), ensure_ascii=False)
    life = ReactLoopRequest(0, _context().observed_at, _observations())
    agent.prepare_react_loop(life)
    messages = agent.build_react_messages(life)
    readonly = ReactLoopRequest(0, _context().observed_at, [], "你爱吃什么？", True)
    agent.prepare_react_loop(readonly)
    messages += agent.build_react_messages(readonly)
    assert len(messages) == 5 and "牛肉面" not in json.dumps(messages, ensure_ascii=False)


def test_readonly_questions_use_the_latest_decision_snapshot(tmp_path) -> None:
    """只读问答：尚无生活决策时人设层来自卡片、记忆层两个列表为空；每次生活决策构建请求后把人设层与记忆层存进工作区
    快照，只读问答取最近一份。"""
    agent = LifeAgent()
    agent._id, agent._name = 7, "Alice"
    agent._profile = _profile(make_card())
    agent._enable_memory = False
    agent._workspace_root = tmp_path
    question = "今天过得怎么样？"
    readonly = ReactLoopRequest(0, _context().observed_at, [], question, True)
    agent.prepare_react_loop(readonly)
    persona, memory = agent._life_budget.layers
    assert memory == empty_memory_layer() and persona == persona_layer(agent._profile, "Alice", _NO_VIEWS)
    observations = _observations()
    remembered = {"layer": "记忆层", "earlier_days": [], "earlier_today": [
        {"recorded_at": "2026-01-04T23:00:00+00:00", "settled": ["22:30 给沈昊发消息：到家了"], "self_notes": []}]}
    observations[0]["data"]["memory_layer"] = remembered
    observations[0]["data"]["persona_views"] = {"relationships": [{"person": "沈昊", "view": "靠谱"}],
                                                "preferences": []}
    agent.prepare_react_loop(ReactLoopRequest(0, _context().observed_at, observations))
    snapshot = json.loads((tmp_path / PROMPT_SNAPSHOT_FILE).read_text(encoding="utf-8"))
    assert snapshot == {"persona": agent._life_budget.layers[1], "memory": remembered}
    agent.prepare_react_loop(readonly)
    assert agent.build_react_messages(readonly) == readonly_messages(snapshot["persona"], remembered, question)
    assert readonly_layers(None, agent._profile, "Alice")[1] == empty_memory_layer()
    write_prompt_snapshot(tmp_path, {"layer": "人设层"}, remembered)
    assert readonly_layers(tmp_path, agent._profile, "Alice") == ({"layer": "人设层"}, remembered)
